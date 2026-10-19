"""LR review and PDF generation. Reads shipments; never persists LR form data."""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from flask import jsonify, render_template, request, send_file

from app import orm as models
from app.admin import admin_bp
from app.admin.auth import require_admin
from app.admin.labels import _label_rows
from app.admin.lr_fields import FIELDS, FIELD_NAMES
from app.admin.lr_pdf import generate_lr_pdf
from app.admin.reporting import parse_date
from app.shipment_packages import volume_ft3

MAX_RECEIPTS = 100


def _selected_rows(values):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_RECEIPTS:
        raise ValueError(f'Select between 1 and {MAX_RECEIPTS} saved shipments.')
    selected = list(dict.fromkeys(models.record_id(value) for value in values))
    found = {row.id: row for row in models.shipment_query().filter(models.Consignment.id.in_(selected)).all()}
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
    return dict(id=str(row.id), consignment_number=row.consignment_number,
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
        output = generate_lr_pdf(receipts)
    except (ValueError, TypeError) as error:
        return jsonify(success=False, message=str(error)), 400
    response = send_file(output, as_attachment=data.get('delivery') != 'preview',
                         download_name='lorry-receipts.pdf', mimetype='application/pdf')
    response.headers['Cache-Control'] = 'no-store'
    return response
