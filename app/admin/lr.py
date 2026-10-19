"""Review LRs, preview PDFs and explicitly attach previewed receipts to shipments."""
import hashlib
import hmac
from io import BytesIO
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from flask import current_app, jsonify, render_template, request, send_file, session
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pypdf import PdfReader, PdfWriter

from app import limiter, orm as models
from app.admin import admin_bp
from app.admin.auth import require_admin
from app.admin.labels import _label_rows
from app.admin.lr_fields import FIELDS, FIELD_NAMES
from app.admin.lr_pdf import generate_lr_pdf
from app.admin.reporting import parse_date
from app.shipment_packages import volume_ft3
from app.models import db
from app.admin.documents import validate_document, store_document_changes, cleanup_documents, document_name
from app.admin.document_lifecycle import commit_document_cleanup, StorageSyncError

MAX_RECEIPTS = 100


def _selected_rows(values, *, lock=False):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_RECEIPTS:
        raise ValueError(f'Select between 1 and {MAX_RECEIPTS} saved shipments.')
    selected = list(dict.fromkeys(models.record_id(value) for value in values))
    query = models.shipment_query().filter(models.Consignment.id.in_(selected))
    if lock:
        query = query.order_by(models.Consignment.id).with_for_update(of=models.Consignment)
    found = {row.id: row for row in query.all()}
    if len(found) != len(selected):
        raise ValueError('A selected shipment no longer exists. Refresh the list and select again.')
    return [found[identifier] for identifier in selected]


def _address(address, pincode):
    text = (address or '').strip()
    return text + ('\n' + pincode if pincode and pincode not in text else '')


def lr_shipment(row):
    values = {name: '' for name in FIELD_NAMES}
    pickup_day = parse_date(row.pickup_date)
    weight = str(row.weight) if row.weight is not None else ''
    values.update(lr_number=row.consignment_number, lr_date=pickup_day.isoformat() if pickup_day else '',
                  consignor_name=row.company.name if row.company else '',
                  consignor_address=_address(row.pickup_address, row.pickup_pincode),
                  consignee_address=_address(row.drop_address, row.drop_pincode),
                  packages=str(row.pieces or 1),
                  package_type='PALLET' if (row.package_contents or {}).get('kind') == 'pallet' else 'BOX',
                  gross_weight=weight, cargo_status=row.status or '')
    return dict(id=str(row.id), consignment_number=row.consignment_number, lr_file=getattr(row, 'lr_file', None),
                company=row.company.name if row.company else '', values=values,
                sources=[name for name, value in values.items() if value],
                volume_ft3=str(volume_ft3(row)) if row.volume is not None else '')


def _payload():
    origin = request.headers.get('Origin')
    if origin and urlsplit(origin).netloc != request.host:
        raise ValueError('LR requests must come from this admin app.')
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError('Send the selected shipments and LR details.')
    return data


def _review_details(items, rows):
    if not isinstance(items, list) or len(items) != len(rows):
        raise ValueError('Complete LR details for every selected shipment.')
    expected = {str(row.id) for row in rows}
    result = {}
    numbers = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Invalid LR details.')
        identifier = str(models.record_id(item.get('id')))
        if identifier not in expected or identifier in result:
            raise ValueError('LR details must match exactly the selected shipments.')
        values = {}
        for field in FIELDS:
            name, title, kind = field['name'], field['label'], field['kind']
            raw = item.get(name, '')
            if not isinstance(raw, str) or len(raw) > field['limit'] or any(
                    ord(c) < 32 and not (kind == 'textarea' and c in '\n\r') or ord(c) == 127 for c in raw):
                raise ValueError(f'{title} must be text of up to {field["limit"]} characters.')
            value = raw.strip()
            if field['required'] and not value:
                raise ValueError(f'Enter {title.lower()} for every LR.')
            if value:
                if kind in ('money', 'weight'):
                    precision = Decimal('.01' if kind == 'money' else '.001')
                    try:
                        amount = Decimal(value)
                        if not amount.is_finite() or not 0 <= amount <= Decimal('999999999.99') or amount != amount.quantize(precision):
                            raise ValueError()
                        value = format(amount.quantize(precision), 'f')
                    except (InvalidOperation, ValueError):
                        raise ValueError(f'{title} must be a nonnegative number with at most {2 if kind == "money" else 3} decimal places, up to 999999999.99.') from None
                elif kind == 'integer':
                    if not re.fullmatch(r'[0-9]{1,7}', value) or not 1 <= int(value) <= 1000000:
                        raise ValueError('Package count must be between 1 and 1000000.')
                    value = str(int(value))
                elif kind == 'package' and value not in ('BOX', 'PALLET', 'BAG', 'BUNDLE', 'DRUM', 'CARTON', 'PACKAGE'):
                    raise ValueError('Choose a package type.')
                elif kind == 'date':
                    try:
                        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                            raise ValueError()
                        date.fromisoformat(value)
                    except ValueError:
                        raise ValueError(f'Enter a valid {title.lower()}.') from None
                elif kind == 'time':
                    try:
                        if not re.fullmatch(r'\d{2}:\d{2}', value):
                            raise ValueError()
                        datetime.strptime(value, '%H:%M')
                    except ValueError:
                        raise ValueError(f'Enter a valid {title.lower()}.') from None
            values[name] = value
        if not re.fullmatch(r'[ -~]{1,64}', values['lr_number']):
            raise ValueError('LR number must use printable English letters, numbers or symbols for its barcode.')
        if values['lr_number'].casefold() in numbers:
            raise ValueError('Each shipment in this batch needs a different LR number.')
        numbers.add(values['lr_number'].casefold())
        if values['eway_bill_date'] and values['eway_valid_until'] and values['eway_valid_until'] < values['eway_bill_date']:
            raise ValueError('E-way bill validity must not end before its issue date.')
        result[identifier] = values
    return [result[str(row.id)] for row in rows]


@admin_bp.get('/admin/lr-generator')
@require_admin
def lr_generator_panel():
    return render_template('admin/lr_generator.html', **_label_rows(), lr_fields=FIELDS)


@admin_bp.post('/admin/lr-generator/review')
@require_admin
def lr_review():
    try:
        data = _payload()
        return jsonify(success=True, shipments=[lr_shipment(row) for row in _selected_rows(data.get('consignment_ids'))])
    except (ValueError, TypeError) as error:
        return jsonify(success=False, message=str(error)), 400


@admin_bp.post('/admin/lr-generator/generate')
@require_admin
def lr_generate():
    try:
        data = _payload()
        rows = _selected_rows(data.get('consignment_ids'))
        receipts = _review_details(data.get('lr_details'), rows)
        if data.get('prepare_upload') is True and data.get('expected_references') != _references(rows):
            return jsonify(success=False, message='An LR changed since review. Reopen the details before previewing.'), 409
        output = generate_lr_pdf(receipts)
    except (ValueError, TypeError) as error:
        return jsonify(success=False, message=str(error)), 400
    response = send_file(output, as_attachment=data.get('delivery') != 'preview',
                         download_name='lorry-receipts.pdf', mimetype='application/pdf')
    response.headers['Cache-Control'] = 'no-store'
    if data.get('prepare_upload') is True:
        response.headers['X-LR-Preview-Token'] = _signer().dumps({
            'owner': session.get('admin_username'), 'pdf': hashlib.sha256(output.getvalue()).hexdigest(),
            'references': _digest(_references(rows)),
        })
    return response


def _references(rows):
    return [{'id': str(row.id), 'reference': getattr(row, 'lr_file', None)} for row in rows]


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='lr-preview-upload-v1')


@admin_bp.post('/admin/lr-generator/upload')
@limiter.limit('10 per minute')
@require_admin
def lr_upload():
    """Bind the exact preview bytes to their selected shipments; commit as a batch."""
    request.max_content_length = 51 * 1024 * 1024
    created, obsolete = [], []
    try:
        origin = request.headers.get('Origin')
        if origin and urlsplit(origin).netloc != request.host:
            return jsonify(success=False, message='Upload LRs from this admin app.'), 403
        try:
            signed = _signer().loads(request.form.get('preview_token', ''), max_age=1800)
        except (BadSignature, SignatureExpired):
            return jsonify(success=False, message='Preview the LRs again; the upload authorisation is missing or expired.'), 400
        if signed.get('owner') != session.get('admin_username'):
            return jsonify(success=False, message='Preview these LRs in your current admin session.'), 403
        ids = json.loads(request.form.get('consignment_ids', 'null'))
        references = json.loads(request.form.get('expected_references', 'null'))
        if not hmac.compare_digest(signed['references'], _digest(references)):
            raise ValueError('The shipment selection changed. Preview the LRs again.')
        uploaded = request.files.get('file')
        if not uploaded:
            raise ValueError('Preview an LR before uploading.')
        content = uploaded.read(50 * 1024 * 1024 + 1)
        if len(content) > 50 * 1024 * 1024 or not hmac.compare_digest(signed['pdf'], hashlib.sha256(content).hexdigest()):
            raise ValueError('The PDF differs from its preview. Preview the LRs again.')
        rows = _selected_rows(ids, lock=True)
        current = _references(rows)
        if current != references:
            db.session.rollback()
            return jsonify(success=False, message='An LR was already uploaded or changed. Reopen review to check its current document.'), 409
        pdf = PdfReader(BytesIO(content))
        if len(pdf.pages) != len(rows) * 2:
            raise ValueError('Each selected shipment must have exactly two previewed pages.')
        # Prepare every split PDF before writing any object to private storage.
        documents = []
        for index, row in enumerate(rows):
            writer, output = PdfWriter(), BytesIO()
            writer.add_page(pdf.pages[index * 2])
            writer.add_page(pdf.pages[index * 2 + 1])
            writer.write(output)
            documents.append(validate_document(output.getvalue(), document_name(row.consignment_number, 'lr', '.pdf')))
        for row, document in zip(rows, documents):
            store_document_changes(row, [('lr', document)], created, obsolete)
        commit_document_cleanup(obsolete)
        return jsonify(success=True, count=len(rows), documents=[{
            'id': str(row.id), 'kind': 'lr', 'reference': row.lr_file, 'original_name': row.lr_original_name,
        } for row in rows])
    except (ValueError, TypeError, KeyError) as error:
        db.session.rollback()
        cleanup_documents(created)
        return jsonify(success=False, message=str(error)), 400
    except StorageSyncError as error:
        db.session.rollback()
        cleanup_documents(created)
        return jsonify(success=False, message=str(error)), 500
    except Exception:
        db.session.rollback()
        cleanup_documents(created)
        current_app.logger.exception('Unable to upload previewed LRs')
        return jsonify(success=False, message='LRs could not be saved. Previous documents were preserved. Check storage and migration 003.'), 500
