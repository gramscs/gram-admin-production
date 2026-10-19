"""Private, content-validated POD, invoice and lorry receipt documents."""

import base64
import binascii
import io
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
import warnings
from contextlib import contextmanager

from flask import current_app, jsonify, request, send_file
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, ArrayObject, IndirectObject
from werkzeug.utils import secure_filename

from app import limiter
from app.admin import admin_bp
from app.admin.auth import require_admin
from app.models import db
from app import orm as models
from app.admin.document_lifecycle import (commit_document_cleanup, document_is_retained,
    purge_shipment_documents, storage_object_missing, StorageSyncError)

MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
DOCUMENTS = {'pod': ('pod_image', 'pod_original_name'), 'invoice': ('invoice_file', 'invoice_original_name'), 'lr': ('lr_file', 'lr_original_name')}
DOCUMENT_TYPES = {'pod': 'POD', 'invoice': 'invoice', 'lr': 'LR'}
IMAGE_TYPES = {'JPEG': ('.jpg', 'image/jpeg'), 'PNG': ('.png', 'image/png'), 'WEBP': ('.webp', 'image/webp')}
EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.pdf'}


def storage_reference(bucket, path):
    """ER metadata stores bucket/path separately; storage APIs use a full reference."""
    return path if path.startswith('supabase:') else f'supabase:{bucket}/{path}'


def document_name(number, kind, extension):
    """One safe shipment/document filename for storage and every download."""
    stem = secure_filename(str(number)) or 'shipment'
    return f'{stem}_{kind}{extension.lower()}'


def document_extension(storage_name, original_name=None, mime_type=None):
    """Preserve a usable extension when an older object key is opaque."""
    for name in (storage_name, original_name):
        extension = Path(name or '').suffix.lower()
        if extension in EXTENSIONS:
            return extension
    return {'application/pdf': '.pdf', 'image/jpeg': '.jpg', 'image/png': '.png',
            'image/webp': '.webp'}.get(mime_type, '')


@contextmanager
def open_stored_document(value):
    """Read private storage without fetching external URLs or escaping uploads."""
    if not isinstance(value, str) or not value:
        raise ValueError('Invalid document reference.')
    if value.lower().startswith(('http://', 'https://', 'data:')):
        raise ValueError('External document references cannot be fetched.')
    if value.startswith('supabase:'):
        from app.admin.consignment_controller import _download_supabase_pod_file
        try:
            content, name = _download_supabase_pod_file(value)
        except Exception as error:
            if storage_object_missing(error):
                raise FileNotFoundError('Document file missing from storage.') from None
            raise
        with io.BytesIO(content) as source:
            yield source, name
    else:
        raise ValueError('This file is not stored in Supabase. Local file storage is disabled.')


def _check_pdf_tree(root):
    seen, count = set(), 0
    def visit(value, depth=0):
        nonlocal count
        count += 1
        if count > 20000 or depth > 80:
            raise ValueError('PDF is too complex. Upload a flattened copy.')
        if isinstance(value, IndirectObject):
            key = (value.idnum, value.generation)
            if key in seen:
                return
            seen.add(key)
            value = value.get_object()
        if isinstance(value, DictionaryObject):
            if any(key in value for key in ('/JS', '/OpenAction', '/AA', '/EmbeddedFiles', '/RichMedia', '/XFA', '/AF')) or value.get('/S') in ('/JavaScript', '/Launch', '/SubmitForm', '/ImportData', '/GoToR') or value.get('/Type') == '/Filespec' or value.get('/Subtype') == '/FileAttachment':
                raise ValueError('PDF contains scripts, actions or attachments. Upload a flattened copy.')
            for item in value.values():
                visit(item, depth+1)
        elif isinstance(value, ArrayObject):
            for item in value:
                visit(item, depth+1)
    visit(root)


def validate_document(content, filename):
    """Detect contents independently of the browser MIME type; strip metadata."""
    if not content or len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError('Choose a non-empty document of at most 5 MB.')
    name = (secure_filename(filename or '') or 'document')[:255]
    ext = Path(name).suffix.lower()
    if ext not in EXTENSIONS:
        raise ValueError('Use a PDF, JPG, PNG or WebP file. SVG and other formats are not accepted.')
    try:
        if ext == '.pdf':
            if not content.startswith(b'%PDF-'):
                raise ValueError('The file contents do not match a PDF.')
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                raise ValueError('Use a PDF without password protection.')
            if not 1 <= len(reader.pages) <= 200:
                raise ValueError('PDF must contain 1–200 pages.')
            _check_pdf_tree(reader.trailer['/Root'])
            writer = PdfWriter()
            for page in reader.pages:
                writer.add_page(page)
            output = io.BytesIO()
            writer.write(output)
            clean, detected_ext, mime = output.getvalue(), '.pdf', 'application/pdf'
        else:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as image:
                    detected = image.format
                    if detected not in IMAGE_TYPES or image.width * image.height > 16000000 or getattr(image, 'n_frames', 1) != 1:
                        raise ValueError('Use a single JPG, PNG or WebP image, at most 16 megapixels.')
                    detected_ext, mime = IMAGE_TYPES[detected]
                    if ext != detected_ext and not (ext == '.jpeg' and detected == 'JPEG'):
                        raise ValueError('The filename does not match the image contents.')
                    image.load()
                    pixels = image.convert('RGB' if detected == 'JPEG' else 'RGBA')
                    pixels.info.clear()
                    output = io.BytesIO()
                    pixels.save(output, format=detected)
                    clean = output.getvalue()
    except ValueError:
        raise
    except Exception as error:
        raise ValueError('The document is damaged or unsupported. Export it again as PDF, JPG, PNG or WebP.') from error
    if len(clean) > MAX_DOCUMENT_BYTES:
        raise ValueError('The processed document exceeds 5 MB. Use a smaller file.')
    return clean, detected_ext, mime, name


def prepare_row_documents(row, consignment):
    changes = []
    for kind, (field, name_field) in DOCUMENTS.items():
        remove = row.get(kind + '_remove', False)
        if not isinstance(remove, bool):
            raise ValueError(f'{kind.upper()} removal must be true or false.')
        data = row.get(kind + '_file_data')
        reference = row.get(field)
        if reference and reference != getattr(consignment, field):
            raise ValueError(f'Upload the {kind.upper()} file; arbitrary document paths or URLs cannot be saved.')
        if data and remove:
            raise ValueError(f'Choose either replace or remove for {kind.upper()}.')
        if data:
            if not isinstance(data, str) or len(data) > (MAX_DOCUMENT_BYTES * 4 // 3 + 1024):
                raise ValueError(f'{kind.upper()} upload exceeds 5 MB.')
            header, separator, encoded = data.partition(',')
            if not separator or not header.startswith('data:') or not header.endswith(';base64'):
                raise ValueError('Document upload must be a base64 data URL.')
            try:
                content = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise ValueError('Document upload data is invalid.') from None
            document = validate_document(content, row.get(kind + '_file_name'))
            changes.append((kind, document))
        elif remove:
            changes.append((kind, None))
    return changes


def store_document_changes(consignment, changes, created, obsolete):
    from app.admin.consignment_controller import _store_pod_bytes
    for kind, document in changes:
        field, name_field = DOCUMENTS[kind]
        old = getattr(consignment, field)
        if document:
            content, ext, mime, original = document
            # A unique folder preserves replacement rollback and ER history;
            # the visible leaf always follows the bulk-upload naming convention.
            filename = f'{uuid4().hex}/{document_name(consignment.consignment_number, kind, ext)}'
            bucket = ((os.getenv('SUPABASE_INVOICE_BUCKET', '').strip() or 'invoice-uploads') if kind == 'invoice'
                      else (os.getenv('SUPABASE_LR_BUCKET', '').strip() or os.getenv('SUPABASE_BUCKET', '').strip() or 'pod-uploads') if kind == 'lr' else None)
            value = _store_pod_bytes(filename, content, mime, bucket_name=bucket)
            created.append(value)
            setattr(consignment, field, value)
            setattr(consignment, name_field, original)
        else:
            purge_shipment_documents(consignment, obsolete, kind)
        if old:
            obsolete.append(old)


def cleanup_documents(values):
    from app.admin.consignment_controller import _delete_pod_file
    for value in set(values):
        try:
            if document_is_retained(value):
                continue
            _delete_pod_file(value)
        except Exception:
            current_app.logger.exception('Unable to remove obsolete shipment document')


def _response_error(message, status=400):
    return jsonify(success=False, message=message), status


@admin_bp.route('/admin/consignments/<consignment_id>/<any(pod,invoice,lr):kind>', methods=['GET', 'POST', 'DELETE'])
@limiter.limit('30 per minute', methods=['POST', 'DELETE'])
@require_admin
def consignment_document(consignment_id, kind):
    try:
        consignment_id = models.record_id(consignment_id)
    except ValueError:
        return _response_error('Consignment not found.', 404)
    row = db.session.get(models.Consignment, consignment_id, with_for_update=request.method != 'GET')
    if not row:
        return _response_error('Consignment not found.', 404)
    field, name_field = DOCUMENTS[kind]
    if request.method != 'GET':
        origin = request.headers.get('Origin')
        if origin and urlsplit(origin).netloc != request.host:
            return _response_error('Document changes must come from this admin app.', 403)
        values = request.form if request.method == 'POST' else request.get_json(silent=True)
        if isinstance(values, dict) or request.method == 'POST':
            if 'expected_reference' in values:
                expected = values.get('expected_reference')
                if expected is not None and not isinstance(expected, str):
                    db.session.rollback()
                    return _response_error('Invalid document reference.')
                if (expected or None) != (getattr(row, field) or None):
                    db.session.rollback()
                    return _response_error('This document changed since you opened it. Close this editor and reload Documents before trying again.', 409)
        created, obsolete = [], []
        try:
            document = None
            if request.method == 'POST':
                uploaded = request.files.get('file')
                if not uploaded:
                    return _response_error('Choose a document to upload.')
                document = validate_document(uploaded.read(MAX_DOCUMENT_BYTES+1), uploaded.filename)
            store_document_changes(row, [(kind, document)], created, obsolete)
            commit_document_cleanup(obsolete)
        except ValueError as error:
            db.session.rollback()
            cleanup_documents(created)
            return _response_error(str(error))
        except StorageSyncError as error:
            db.session.rollback()
            cleanup_documents(created)
            return _response_error(str(error), 500)
        except Exception:
            db.session.rollback()
            cleanup_documents(created)
            current_app.logger.exception('Unable to update shipment document')
            return _response_error('Document could not be saved. Your previous document was preserved.', 500)
        return jsonify(success=True, **{field: getattr(row, field)})
    value = getattr(row, field)
    if not value:
        return _response_error('No document found.', 404)
    try:
        with open_stored_document(value) as (source, storage_name):
            content = source.read()
        extension = Path(storage_name).suffix.lower()
        mime = {'.pdf': 'application/pdf', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp'}.get(extension, 'application/octet-stream')
        preview = request.args.get('preview') == '1' and mime.startswith('image/')
        if preview:
            # Verify old files too before allowing image previews.
            validate_document(content, storage_name)
        response = send_file(io.BytesIO(content), as_attachment=not preview,
                             download_name=document_name(row.consignment_number, kind, extension), mimetype=mime, max_age=0)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
        return response
    except FileNotFoundError:
        return _response_error('Document file missing.', 404)
    except ValueError:
        return _response_error('This document cannot be previewed. Download it instead.')
    except Exception:
        current_app.logger.exception('Unable to retrieve shipment document')
        return _response_error('Document could not be retrieved.', 500)
