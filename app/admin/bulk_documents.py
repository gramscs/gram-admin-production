"""Review bulk uploads before applying them; download named private documents."""
import json
from pathlib import Path
from tempfile import SpooledTemporaryFile
from urllib.parse import urlsplit
from zipfile import ZipFile, ZIP_DEFLATED

from flask import current_app, jsonify, request, send_file
from werkzeug.utils import secure_filename

from app import limiter, orm as models
from app.admin import admin_bp
from app.admin.auth import require_admin
from app.admin.documents import (DOCUMENTS, MAX_DOCUMENT_BYTES, validate_document,
    store_document_changes, cleanup_documents, open_stored_document)
from app.admin.reporting import filter_shipments, normalize_shipment_filters
from app.models import db
from app.admin.document_lifecycle import commit_document_cleanup, StorageSyncError

MAX_UPLOAD_FILES = 50
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_DOWNLOAD_SHIPMENTS = 200
MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024


def document_name(number, kind, extension):
    stem = secure_filename(str(number)) or 'shipment'
    return f'{stem}_{kind}{extension.lower()}'


def _same_origin():
    origin = request.headers.get('Origin')
    return not origin or urlsplit(origin).netloc == request.host


@admin_bp.post('/admin/consignments/documents/bulk-upload')
@limiter.limit('10 per minute')
@require_admin
def bulk_document_upload():
    request.max_content_length = MAX_UPLOAD_BYTES + 1024 * 1024
    if not _same_origin():
        return jsonify(success=False, message='Upload from this admin app.'), 403
    uploads = request.files.getlist('files')
    if not 1 <= len(uploads) <= MAX_UPLOAD_FILES:
        return jsonify(success=False, message='Choose 1–50 files, up to 50 MB combined.'), 400
    apply = request.form.get('apply', 'false') == 'true'
    # Resolve exact identifiers first, then safe download-name aliases. Never
    # guess when replacing punctuation makes two identifiers indistinguishable.
    rows = models.Consignment.query.with_entities(models.Consignment.id, models.Consignment.consignment_number).all()
    exact, aliases = {}, {}
    for row in rows:
        exact.setdefault(row.consignment_number.casefold(), []).append(row)
        aliases.setdefault((secure_filename(row.consignment_number) or 'shipment').casefold(), []).append(row)
    plans, errors, seen, total = [], [], set(), 0
    for upload in uploads:
        name = (upload.filename or '').replace('\\', '/').rsplit('/', 1)[-1]
        stem = Path(name).stem
        number, separator, kind = stem.rpartition('_')
        kind = kind.lower()
        if not separator or kind not in DOCUMENTS:
            errors.append({'filename': name, 'message': 'Use consignmentNum_pod.ext or consignmentNum_invoice.ext.'})
            continue
        candidates = exact.get(number.casefold(), []) + aliases.get(number.casefold(), [])
        matches = list({row.id: row for row in candidates}.values())
        if len(matches) != 1:
            errors.append({'filename': name, 'message': 'No saved consignment matches this filename.' if not matches else 'This filename matches multiple consignments. Use the exact identifier.'})
            continue
        row = matches[0]
        key = row.id, kind
        if key in seen:
            errors.append({'filename': name, 'message': 'Choose only one file per consignment and document type.'})
            continue
        seen.add(key)
        content = upload.read(MAX_DOCUMENT_BYTES + 1)
        total += len(content)
        if total > MAX_UPLOAD_BYTES:
            return jsonify(success=False, message='Files exceed the combined 50 MB limit.'), 400
        try:
            document = validate_document(content, name)
        except ValueError as error:
            errors.append({'filename': name, 'message': str(error)})
            continue
        plans.append((row, kind, document))
    if errors:
        return jsonify(success=False, message='Fix these files before uploading. No documents were changed.', errors=errors), 400
    matched_ids = {row.id for row, _, _ in plans}
    matched = {row.id: row for row in models.shipment_query().filter(models.Consignment.id.in_(matched_ids)).all()}
    if set(matched) != matched_ids:
        return jsonify(success=False, message='A matched consignment was removed. Check the files again.'), 400
    plans = [(matched[row.id], kind, document) for row, kind, document in plans]
    review = [{'filename': document[3], 'consignment_number': row.consignment_number,
               'kind': kind, 'replaces_existing': bool(getattr(row, DOCUMENTS[kind][0]))}
              for row, kind, document in plans]
    if not apply:
        return jsonify(success=True, review=review, count=len(review), applied=False)
    created, obsolete = [], []
    try:
        for row, kind, document in plans:
            store_document_changes(row, [(kind, document)], created, obsolete)
        commit_document_cleanup(obsolete)
    except StorageSyncError as error:
        db.session.rollback()
        cleanup_documents(created)
        return jsonify(success=False, message=str(error), applied=False), 500
    except Exception:
        db.session.rollback()
        cleanup_documents(created)
        current_app.logger.exception('Bulk documents could not be saved')
        return jsonify(success=False, message='Upload failed. Previous documents were preserved. Try again.', applied=False), 500
    documents = [{'id': str(row.id), 'kind': kind, 'reference': getattr(row, DOCUMENTS[kind][0]),
                  'original_name': getattr(row, DOCUMENTS[kind][1])} for row, kind, _ in plans]
    return jsonify(success=True, count=len(review), review=review, applied=True, documents=documents)


@admin_bp.post('/admin/consignments/documents/bulk-download')
@limiter.limit('6 per minute')
@require_admin(read_only=True)
def bulk_document_download():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not _same_origin():
        return jsonify(success=False, message='Choose documents from this admin app.'), 400
    kind = data.get('kind', 'both')
    if kind not in ('pod', 'invoice', 'both'):
        return jsonify(success=False, message='Choose POD, invoices or both.'), 400
    ids = data.get('ids', [])
    if not isinstance(ids, list) or len(ids) > MAX_DOWNLOAD_SHIPMENTS:
        return jsonify(success=False, message='Select at most 200 shipments per download.'), 400
    query = models.shipment_query()
    if ids:
        try:
            ids = list(dict.fromkeys(models.record_id(value) for value in ids))
        except ValueError:
            return jsonify(success=False, message='A selected shipment ID is invalid.'), 400
        query = query.filter(models.Consignment.id.in_(ids))
    else:
        try:
            query = filter_shipments(query, normalize_shipment_filters(data))
        except ValueError as error:
            return jsonify(success=False, message=str(error)), 400
    rows = query.order_by(models.Consignment.id).limit(MAX_DOWNLOAD_SHIPMENTS + 1).all()
    if len(rows) > MAX_DOWNLOAD_SHIPMENTS:
        return jsonify(success=False, message='More than 200 shipments match. Select shipments or narrow your search.'), 400
    if ids and len(rows) != len(ids):
        return jsonify(success=False, message='A selected shipment no longer exists. Refresh the table.'), 400
    kinds = ('pod', 'invoice') if kind == 'both' else (kind,)
    buffer = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode='w+b')
    manifest, names, total, downloaded = [], set(), 0, 0
    try:
        with ZipFile(buffer, 'w', ZIP_DEFLATED) as archive:
            for row in rows:
                for document_kind in kinds:
                    value = getattr(row, DOCUMENTS[document_kind][0])
                    entry = {'consignment_id': str(row.id), 'consignment_number': row.consignment_number,
                             'identifier_type': row.identifier_type, 'company': row.company.name if row.company else None,
                             'kind': document_kind, 'filename': None, 'status': 'not_uploaded'}
                    if value:
                        try:
                            with open_stored_document(value) as (source, storage_name):
                                content = source.read(MAX_DOWNLOAD_BYTES - total + 1)
                            total += len(content)
                            if total > MAX_DOWNLOAD_BYTES:
                                raise OverflowError('Download exceeds 250 MB. Select fewer shipments.')
                            filename = document_name(row.consignment_number, document_kind, Path(storage_name).suffix)
                            # Folder only needed for rare sanitized-name collisions.
                            if filename.casefold() in names:
                                filename = f'{row.id}/{filename}'
                            names.add(filename.casefold())
                            archive.writestr(filename, content)
                            entry.update(filename=filename, status='downloaded')
                            downloaded += 1
                        except FileNotFoundError:
                            entry['status'] = 'missing'
                        except (ValueError, RuntimeError, OSError):
                            entry['status'] = 'unavailable'
                        except OverflowError:
                            raise
                        except Exception:
                            current_app.logger.exception('Bulk document retrieval failed')
                            entry['status'] = 'unavailable'
                    manifest.append(entry)
            archive.writestr('document-manifest.json', json.dumps({'documents': manifest,
                'downloaded': downloaded, 'shipment_count': len(rows)}, indent=2))
        if not downloaded:
            buffer.close()
            return jsonify(success=False, message='No uploaded documents could be downloaded for these shipments.', documents=manifest), 404
        buffer.seek(0)
        response = send_file(buffer, as_attachment=True, download_name=f'consignment-{kind}-documents.zip', mimetype='application/zip', max_age=0)
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Documents-Downloaded'] = str(downloaded)
        response.headers['X-Documents-Omitted'] = str(len(manifest) - downloaded)
        response.call_on_close(buffer.close)
        return response
    except OverflowError as error:
        buffer.close()
        return jsonify(success=False, message=str(error)), 400
    except Exception:
        buffer.close()
        raise
