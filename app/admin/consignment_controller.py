"""Admin consignment management routes and helpers."""

from decimal import Decimal, InvalidOperation
import io
import json
import logging
import os
import re
from types import SimpleNamespace

from flask import current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from openpyxl import Workbook, load_workbook
from sqlalchemy.exc import SQLAlchemyError, DatabaseError, OperationalError, ProgrammingError

from app import limiter
from app.admin import admin_bp
from app.admin.auth import require_admin
from app.models import db
from app import orm as models
from app.shipment_packages import normalize_contents, volume_ft3, shared_contents
from app.spreadsheets import save_workbook_in_memory
from app.admin.documents import prepare_row_documents, store_document_changes, cleanup_documents
from app.admin.document_lifecycle import commit_document_cleanup, purge_shipment_documents, StorageSyncError, document_save_error

logger = logging.getLogger(__name__)



def _get_supabase_client():
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", "").strip()
    if not url and not key:
        raise RuntimeError('Set SUPABASE_URL and SUPABASE_KEY. Local file storage is disabled.')
    if not url or not key:
        raise RuntimeError('Set both SUPABASE_URL and SUPABASE_KEY to use Supabase Storage.')
    try:
        from supabase import create_client
        from supabase._sync.client import SupabaseException
    except Exception:
        raise RuntimeError('Supabase Storage requires the Supabase package. Install the app requirements.') from None
    try:
        try:
            return create_client(url, key)
        except SupabaseException as error:
            if str(error) != 'Invalid API key':
                raise
            # The older general SDK validates JWT syntax locally. Proxy-backed
            # keys can be opaque until HTTPS egress substitutes the real key.
            # The official Storage client sends that binding to the same project;
            # Supabase still validates credentials and permissions on every call.
            from storage3 import create_client as create_storage_client
            storage = create_storage_client(url.rstrip('/') + '/storage/v1',
                {'apikey': key, 'Authorization': 'Bearer ' + key}, is_async=False)
            return SimpleNamespace(storage=storage)
    except Exception:
        raise RuntimeError('Supabase Storage could not initialize. Check SUPABASE_URL and use the legacy service_role key for SUPABASE_KEY.') from None


def _store_pod_bytes(filename, file_bytes, content_type=None, bucket_name=None, *, remote_prefix='consignments'):
    supa = _get_supabase_client()
    payload = file_bytes
    if hasattr(payload, "read"):
        payload = payload.read()
    if isinstance(payload, bytearray):
        payload = bytes(payload)
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("POD upload payload must be bytes-like.")

    bucket = bucket_name or os.getenv("SUPABASE_BUCKET", '').strip() or 'pod-uploads'
    object_path = f"{remote_prefix}/{filename}"
    try:
        supa.storage.from_(bucket).upload(object_path, payload, {"content-type": content_type or "application/octet-stream"})
    except Exception:
        try:
            supa.storage.from_(bucket).remove([object_path])
        except Exception:
            logger.exception("Failed to clean incomplete document upload")
        raise
    return f"supabase:{bucket}/{object_path}"


def _store_mis_bytes(filename, content, mime):
    bucket = os.getenv('SUPABASE_MIS_BUCKET', '').strip() or 'mis-reports'
    document_buckets = {os.getenv('SUPABASE_BUCKET', '').strip() or 'pod-uploads',
                        os.getenv('SUPABASE_INVOICE_BUCKET', '').strip() or 'invoice-uploads',
                        os.getenv('SUPABASE_LR_BUCKET', '').strip() or 'lr-uploads'}
    if bucket in document_buckets:
        raise ValueError('Use a separate MIS bucket, rather than a POD, invoice or LR bucket.')
    return _store_pod_bytes(filename, content, mime, bucket_name=bucket,
                            remote_prefix='mis_reports')


def _parse_supabase_pod_value(pod_value):
    if not isinstance(pod_value, str) or not pod_value.startswith('supabase:'):
        raise ValueError('POD is not stored in Supabase.')

    _, rest = pod_value.split(':', 1)
    bucket, object_path = rest.split('/', 1)
    if not bucket or not object_path:
        raise ValueError('Invalid Supabase POD path.')

    return bucket, object_path


def _download_supabase_pod_file(pod_value):
    client = _get_supabase_client()
    if not client:
        raise RuntimeError('Supabase not configured.')

    bucket, object_path = _parse_supabase_pod_value(pod_value)
    storage = client.storage.from_(bucket)
    # Downloads may remain cached briefly after removal. Check live metadata so
    # missing objects are not served from that cache or mistaken for restored files.
    storage.info(object_path)
    content = storage.download(object_path)
    if hasattr(content, 'read'):
        content = content.read()
    if isinstance(content, bytearray):
        content = bytes(content)
    if not isinstance(content, bytes):
        raise RuntimeError('Unexpected Supabase download response.')

    return content, object_path


def _delete_pod_file(pod_value):
    if not pod_value:
        return

    if isinstance(pod_value, str) and pod_value.startswith("supabase:"):
        client = _get_supabase_client()
        if not client:
            raise RuntimeError('Supabase is not configured; the stored document could not be removed.')
        bucket, object_path = _parse_supabase_pod_value(pod_value)
        storage = client.storage.from_(bucket)
        storage.remove([object_path])
        # A denied DELETE can return an empty list instead of an error. Verify
        # live metadata; a cached download can still return deleted bytes.
        from app.admin.document_lifecycle import storage_object_missing
        try:
            storage.info(object_path)
        except Exception as error:
            if not storage_object_missing(error):
                raise
        else:
            raise RuntimeError('Storage did not remove the requested document.')
        return

    raise ValueError('This file is not stored in Supabase. Local file storage is disabled.')


def _is_external_pod_url(value):
    if not isinstance(value, str):
        return False
    lowered = value.strip().lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _normalize_header(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _serialize_consignment(consignment):
    return {
        "id": getattr(consignment, "id", None),
        "consignment_number": getattr(consignment, "consignment_number", None),
        "location_id": str(consignment.location_id) if consignment.location_id else None,
        "financial_year": consignment.financial_year,
        "lr_status": consignment.lr_status,
        "is_archived": consignment.archive_record is not None,
        "lr_issued_at": consignment.lr_issued_at.isoformat() if consignment.lr_issued_at else None,
        "cancellation_reason": consignment.cancellation_reason,
        "identifier_type": consignment.identifier_type or "LRN",
        "pieces": consignment.pieces or 1,
        "weight": str(consignment.weight) if consignment.weight is not None else None,
        "volume": str(volume_ft3(consignment)) if consignment.volume is not None else None,
        "volume_unit": "ft3",
        "package_contents": shared_contents(consignment.package_contents),
        "measurement_mode": consignment.measurement_mode or ('volume' if consignment.volume is not None and consignment.weight is None else 'weight'),
        "company_id": consignment.company_id,
        "company_name": consignment.company.name if consignment.company else None,
        "status": getattr(consignment, "status", None),
        "pickup_pincode": getattr(consignment, "pickup_pincode", None),
        "pickup_address": getattr(consignment, "pickup_address", None),
        "pickup_tag": getattr(consignment, "pickup_tag", None),
        "pickup_date": getattr(consignment, "pickup_date", None),
        "drop_pincode": getattr(consignment, "drop_pincode", None),
        "drop_address": getattr(consignment, "drop_address", None),
        "drop_tag": getattr(consignment, "drop_tag", None),
        "drop_date": getattr(consignment, "drop_date", None),
        "eta": getattr(consignment, "eta", None),
        "pod_image": getattr(consignment, "pod_image", None),
        "pod_original_name": consignment.pod_original_name,
        "invoice_file": consignment.invoice_file,
        "invoice_original_name": consignment.invoice_original_name,
        "lr_file": consignment.lr_file,
        "lr_original_name": consignment.lr_original_name,
        "pod_file_name": getattr(consignment, "pod_file_name", None),
        "pod_file_type": getattr(consignment, "pod_file_type", None),
        "pod_file_data": getattr(consignment, "pod_file_data", None),
    }


@admin_bp.route("/admin/consignments", methods=["GET"], endpoint="consignments_panel")
@admin_bp.route("/admin/shipments", methods=["GET"], endpoint="consignments_panel")
@require_admin
def consignments_panel():
    try:
        from app.admin.reporting import normalize_shipment_filters
        filters = normalize_shipment_filters(request.args)
        companies = models.Company.query.order_by(models.Company.name).all()
        return render_template("admin/consignments.html", consignments=[], companies=companies, filters=filters)
    except ValueError as error:
        return render_template('admin/consignments.html', consignments=[], companies=[], filters={}, error=str(error)), 400
    except (OperationalError, DatabaseError, ProgrammingError):
        logger.exception("Database error loading admin consignments panel")
        return render_template(
            "admin/consignments.html",
            consignments=[],
            error="Unable to load data. Please try again.",
        )
    except Exception:
        logger.exception("Unexpected error in admin consignments panel")
        return render_template(
            "admin/consignments.html",
            consignments=[],
            error="An unexpected error occurred.",
        )


@admin_bp.route("/admin/documents/list", methods=["GET"], endpoint="documents_list_api")
@admin_bp.route("/admin/consignments/list", methods=["GET"], endpoint="consignments_list_api")
@require_admin
def consignments_list_api():
    try:
        page = max(1, request.args.get("page", 1, type=int))
        per_page = max(1, min(100, request.args.get("per_page", 10, type=int)))
        sort_by = request.args.get("sort_by", "id", type=str)
        sort_order = request.args.get("sort_order", "asc", type=str)

        allowed_sort_columns = {
            "id", "consignment_number", "identifier_type", "pieces", "weight", "volume", "status", "pickup_pincode", "drop_pincode",
            "pickup_tag", "drop_tag", "pickup_date", "drop_date",
        }
        if sort_by not in allowed_sort_columns:
            sort_by = "id"
        sort_order = "asc" if sort_order.lower() == "asc" else "desc"

        from app.admin.reporting import filter_shipments, normalize_shipment_filters
        filters = normalize_shipment_filters(request.args)
        archived = request.endpoint == 'admin.consignments_list_api' and filters['archive'] == 'archived'
        query = filter_shipments(models.shipment_query(archived=archived), filters)

        total = query.count()
        sort_column = getattr(models.Consignment, sort_by)
        query = query.order_by(sort_column.desc() if sort_order == "desc" else sort_column.asc())
        rows = query.offset((page - 1) * per_page).limit(per_page).all()

        pages = (total + per_page - 1) // per_page if total else 0
        return jsonify({
            "success": True,
            "rows": [_serialize_consignment(row) for row in rows],
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": pages,
            "has_prev": page > 1,
            "has_next": page < pages,
        })
    except ValueError as exc:
        return jsonify(success=False, rows=[], error=str(exc)), 400
    except Exception as exc:
        logger.exception("Consignment list API failed: %s", exc)
        return jsonify({
            "success": False,
            "rows": [],
            "page": 1,
            "per_page": 10,
            "total": 0,
            "pages": 0,
            "has_prev": False,
            "has_next": False,
            "error": "Unable to load consignments right now.",
        }), 500


@admin_bp.route("/admin/consignments/import-template.xlsx", methods=["GET"], endpoint="consignments_import_template_excel")
@require_admin
def consignments_import_template_excel():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Consignments"
    sheet.append([
        "location_code",
        "status",
        "pickup_address",
        "pickup_pincode",
        "pickup_tag",
        "pickup_date",
        "drop_address",
        "drop_pincode",
        "drop_tag",
        "drop_date",
        "boxes_pallets", "weight", "volume", "company_id", "measurement_mode", "volume_unit", "package_contents",
    ])

    buffer = io.BytesIO()
    save_workbook_in_memory(workbook, buffer)
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name="consignment_import_template.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@admin_bp.route("/admin/consignments/import", methods=["POST"], endpoint="consignments_import_excel")
@limiter.limit("10 per minute")
@require_admin
def consignments_import_excel():
    from app.admin.lr_control import preview_import
    return preview_import()


@admin_bp.route("/admin/consignments/export.xlsx", methods=["GET"], endpoint="consignments_export_excel")
@require_admin
def consignments_export_excel():
    from app.admin.reporting import filter_shipments, normalize_shipment_filters
    try:
        filters = normalize_shipment_filters(request.args)
    except ValueError as error:
        return jsonify(success=False, message=str(error)), 400
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Consignments"
    headers = [
        "consignment_number",
        "status",
        "pickup_tag",
        "drop_pincode",
        "pickup_date",
        "drop_date",
        "pickup_address",
        "drop_address",
        "identifier_type", "boxes_pallets", "weight", "volume", "company_id", "client_company", "measurement_mode", "volume_unit", "package_contents",
    ]
    sheet.append(headers)

    rows = filter_shipments(models.shipment_query(), filters).order_by(models.Consignment.id.asc()).all()
    for consignment in rows:
        sheet.append([
            getattr(consignment, "consignment_number", None),
            getattr(consignment, "status", None),
            getattr(consignment, "pickup_tag", None),
            getattr(consignment, "drop_pincode", None),
            getattr(consignment, "pickup_date", None),
            getattr(consignment, "drop_date", None),
            getattr(consignment, "pickup_address", None),
            getattr(consignment, "drop_address", None),
            consignment.identifier_type, consignment.pieces,
            consignment.weight, volume_ft3(consignment),
            models.export_id(consignment.company_id), consignment.company.name if consignment.company else None,
            consignment.measurement_mode or ('volume' if consignment.volume is not None and consignment.weight is None else 'weight'),
            'ft3', json.dumps(shared_contents(consignment.package_contents)) if consignment.package_contents else None,
        ])

    buffer = io.BytesIO()
    save_workbook_in_memory(workbook, buffer)
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name="consignments.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@admin_bp.route("/admin/consignments/export.pdf", methods=["GET"], endpoint="consignments_export_pdf")
@limiter.limit("6 per minute")
@require_admin
def consignments_export_pdf():
    from datetime import datetime
    from app.admin.mis import protect_errors, report_response
    from app.admin.reporting import normalize_filters, validate_columns, analyze, DEFAULT_COLUMNS, IST
    from app.admin.mis_exports import consignment_pdf

    @protect_errors
    def download_register():
        filters = normalize_filters(request.args.to_dict())
        columns = validate_columns(request.args.getlist('columns') or DEFAULT_COLUMNS)
        report = analyze(filters)
        content = consignment_pdf(report, columns)
        return report_response(content, f'consignments_{datetime.now(IST):%Y%m%d_%H%M%S_IST}.pdf', 'pdf')

    return download_register()


CONSIGNMENT_SAVE_FIELDS = (
    "status",
    "pickup_pincode",
    "pickup_address",
    "pickup_tag",
    "pickup_date",
    "drop_pincode",
    "drop_address",
    "drop_tag",
    "drop_date",
    "eta",
)


def _normalize_shipment_fields(row, existing=None):
    if "consignment_number" in row and (row["consignment_number"] or existing is not None):
        number = str(row["consignment_number"] or "").strip()
        if not number or len(number) > 64 or not number.isascii() or not all(32 <= ord(char) <= 126 for char in number):
            raise ValueError("Identifier must contain 1–64 printable ASCII characters.")
    for name in ('pickup_pincode', 'drop_pincode'):
        raw = str(row.get(name) or '').strip()
        if raw and not re.fullmatch('[1-9][0-9]{5}', raw):
            raise ValueError(f'{name.replace("_", " ").title()} must be a valid six-digit Indian pincode.')
    for name in CONSIGNMENT_SAVE_FIELDS:
        if name in row and row[name] is not None and not isinstance(row[name], (str, int)):
            raise ValueError(f'{name} must be text.')
    if len(str(row.get('status') or '')) > 200:
        raise ValueError('Status must contain at most 200 characters.')
    def value(name, default):
        return row[name] if name in row else getattr(existing, name, default)

    identifier_type = str(value("identifier_type", "LRN") or "LRN").strip()
    if identifier_type not in {"LRN", "Order ID", "AWB"}:
        raise ValueError("Choose LRN, Order ID, or AWB.")
    raw_pieces = value("pieces", 1)
    try:
        pieces = Decimal(str(1 if raw_pieces in (None, "") else raw_pieces))
        if not pieces.is_finite() or pieces != pieces.to_integral_value() or not 1 <= pieces <= 10000:
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise ValueError("Boxes/pallets must be a whole number from 1 to 10000.") from None
    fields = {"identifier_type": identifier_type, "pieces": int(pieces)}
    contents = value('package_contents', None)
    if isinstance(contents, str):
        try:
            contents = json.loads(contents) if contents.strip() else None
        except (ValueError, TypeError):
            raise ValueError('Box contents must be valid JSON in the import file.') from None
    fields['package_contents'] = normalize_contents(contents)
    if 'volume' in row:
        unit = row.get('volume_unit') or 'ft3'
        if unit not in ('ft3', 'm3'):
            raise ValueError('Volume unit must be ft3 (CBFT) or m3 (legacy CBM import).')
        fields['volume_unit'] = unit
    mode = value('measurement_mode', None) or ('volume' if value('volume', None) not in (None, '') and value('weight', None) in (None, '') else 'weight')
    if mode not in ('weight', 'volume'):
        raise ValueError('Choose Weight (kg) or Volume (ft³).')
    fields['measurement_mode'] = mode
    company_id = value("company_id", None)
    if company_id in (None, ""):
        fields["company_id"] = None
    else:
        from app.admin.input_validation import positive_id
        company_id = positive_id(company_id)
        company = db.session.get(models.Company, company_id)
        if not company or (not company.active and company_id != getattr(existing, "company_id", None)):
            raise ValueError("Choose an active client company. Existing archived links can be retained.")
        fields["company_id"] = company_id
    if models.using_er() and fields['company_id'] != getattr(existing, 'company_id', None):
        fields['pickup_location_id'] = None
        fields['drop_location_id'] = None
    for name in ("weight", "volume"):
        raw = value(name, None)
        if raw in (None, ""):
            fields[name] = None
            continue
        try:
            number = Decimal(str(raw))
            if not number.is_finite() or number < 0 or number > Decimal("999999999.999") or number != number.quantize(Decimal("0.001")):
                raise ValueError()
        except (InvalidOperation, ValueError):
            title = "Weight" if name == "weight" else "Volume"
            raise ValueError(f"{title} must be non-negative with up to 3 decimal places.") from None
        fields[name] = number
    return fields


def _apply_consignment_payload(consignment, row):
    for field in CONSIGNMENT_SAVE_FIELDS:
        if field not in row:
            continue
        value = row.get(field)
        if value is None:
            value = ""
        if isinstance(value, str):
            value = value.strip()
        setattr(consignment, field, value)


def _normalize_save_payload():
    if request.is_json:
        payload = request.get_json(silent=True) or {}
    else:
        payload = request.form.to_dict(flat=True)

    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get("rows"), list):
        return payload

    # Backwards-compatible single-row payload support.
    if payload.get("location_id") or payload.get("consignment_number") or payload.get("id"):
        return {"rows": [payload], "deleted_ids": [], "idempotency_key": payload.get("idempotency_key")}

    return payload


@admin_bp.route("/admin/consignments/save", methods=["POST"], endpoint="consignments_save")
@require_admin
def consignments_save():
    payload = _normalize_save_payload()
    rows = payload.get("rows") if isinstance(payload, dict) else None
    deleted_ids = payload.get("deleted_ids", []) if isinstance(payload, dict) else []

    if not isinstance(rows, list):
        return jsonify({"success": False, "message": "Rows payload is required."}), 400

    from app import lr_issuance as issuance
    from app.admin.auth import current_admin_user
    from app.admin.lr_control import check_csrf
    user = current_admin_user()
    if user.role not in ('administrator', 'operator'):
        return jsonify(success=False, message='Shipment operator access required.'), 403
    if not check_csrf():
        return jsonify(success=False, message='Refresh the page and retry (CSRF token required).'), 403
    if deleted_ids:
        return jsonify(success=False, message='Permanent deletion is disabled. Use cancellation or archiving.'), 400
    if not 1 <= len(rows) <= 100:
        return jsonify(success=False, message='Save between 1 and 100 shipments per request.'), 400
    created_files, obsolete_files, document_plans, saved = [], [], [], []
    try:
        key, fingerprint, previous = issuance.begin_request(user, payload.get('idempotency_key'), rows)
        if previous is not None:
            db.session.rollback()
            return jsonify(previous)
        # Stable lock order across mixed-location saves avoids AB/BA lock cycles.
        ordered = sorted(enumerate(rows), key=lambda pair: (str(pair[1].get('location_id', '')), str(pair[1].get('id', '')))
                  if isinstance(pair[1], dict) else ('', ''))
        for index, row in ordered:
            if not isinstance(row, dict):
                raise ValueError(f'Row {index + 1}: shipment must be an object.')
            row_id = row.get('id')
            if str(row_id).startswith('-'):
                row_id = None
            existing = db.session.get(models.Consignment, models.record_id(row_id), with_for_update=True) if row_id else None
            if row_id and existing is None:
                raise ValueError(f'Row {index + 1}: shipment no longer exists.')
            try:
                issuance.validate_identity(row, existing)
                fields = _normalize_shipment_fields(row, existing)
                if not str(row.get('status', getattr(existing, 'status', '')) or '').strip():
                    raise ValueError('Status is required.')
                # Keep all existing non-LR field validation and document staging.
                candidate = existing or models.Consignment()
                _apply_consignment_payload(candidate, row)
                for field, value in fields.items():
                    setattr(candidate, field, value)
                changes = prepare_row_documents(row, candidate)
                if existing is None:
                    fields.update({name: getattr(candidate, name) for name in CONSIGNMENT_SAVE_FIELDS})
                    candidate = issuance.create_shipment(row, fields)
                saved.append((index, candidate))
                document_plans.append((candidate, changes))
            except ValueError as error:
                raise ValueError(f'Row {index + 1}: {error}') from None
        for consignment, changes in document_plans:
            store_document_changes(consignment, changes, created_files, obsolete_files)
        db.session.flush()
        from app.admin.reporting import filter_shipments
        result = dict(success=True, message='Saved. LR numbers: ' + ', '.join(row.consignment_number for _, row in saved),
                      saved_count=len(saved), deleted_count=0, total=filter_shipments(models.shipment_query(), {}).count(),
                      shipments=[dict(id=str(row.id), lr_number=row.consignment_number) for _, row in sorted(saved)])
        issuance.finish_request(user, key, fingerprint, result)
        commit_document_cleanup(obsolete_files)
        return jsonify(result)
    except ValueError as error:
        db.session.rollback()
        cleanup_documents(created_files)
        logger.warning('Rejected LR shipment mutation: %s', error)
        return jsonify(success=False, message=str(error)), 400
    except SQLAlchemyError as error:
        db.session.rollback()
        cleanup_documents(created_files)
        logger.error('LR transaction rejected (%s)', type(error).__name__)
        return jsonify(success=False, message=issuance.public_error(error)), 409
    except StorageSyncError as error:
        db.session.rollback()
        cleanup_documents(created_files)
        return jsonify(success=False, message=str(error)), 500
    except Exception as error:
        db.session.rollback()
        cleanup_documents(created_files)
        logger.exception("Failed to save consignments")
        return jsonify({"success": False, "message": document_save_error(error, "Failed to save consignments.")}), 500
