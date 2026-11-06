"""Location master, cancellation and two-phase import using centralized issuance."""
import csv
import hmac
import io
import json
import re
from functools import wraps
from uuid import UUID
from flask import current_app, jsonify, render_template, request, session
from openpyxl import load_workbook
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from app.admin import admin_bp
from app.admin.auth import current_admin_user, require_admin
from app.models import db
from app import lr_issuance as issuance
from database.lr import IssuingLocation, LRImport, LRAudit
from database.models import Consignment, AdminUser, AuditLog


def json_object():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError('Send a JSON object.')
    return data


def check_csrf():
    token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
    return isinstance(token, str) and isinstance(session.get('auth_csrf_token'), str) and hmac.compare_digest(token, session['auth_csrf_token'])


def mutation(role='operator'):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_admin_user()
            allowed = ('administrator',) if role == 'administrator' else ('administrator', 'operator')
            profile = db.session.get(AdminUser, user.id)
            if user.role not in allowed or not profile or not profile.active:
                return jsonify(success=False, message='Authorized administrator required.'), 403
            if not check_csrf():
                return jsonify(success=False, message='Refresh the page and retry (CSRF token required).'), 403
            try:
                issuance.actor(user)
                return view(*args, **kwargs)
            except (ValueError, TypeError) as error:
                db.session.rollback()
                return jsonify(success=False, message=str(error)), 400
            except SQLAlchemyError as error:
                db.session.rollback()
                current_app.logger.exception('LR transaction failed')
                return jsonify(success=False, message=issuance.public_error(error)), 409
        return wrapped
    return decorate


@admin_bp.get('/admin/issuing-locations')
@require_admin
def issuing_locations():
    return render_template('admin/issuing_locations.html')


@admin_bp.get('/admin/issuing-locations/options')
@require_admin
def issuing_location_options():
    rows = db.session.scalars(select(IssuingLocation).order_by(IssuingLocation.location_code)).all()
    return jsonify(locations=[dict(id=str(row.id), location_name=row.location_name, location_code=row.location_code,
                                   is_active=row.is_active, reconciled=bool(row.reconciliation_digest),
                                   code_locked=bool(row.reconciliation_digest)) for row in rows])


@admin_bp.post('/admin/issuing-locations')
@require_admin
@mutation('administrator')
def save_issuing_location():
    data = json_object()
    if set(data) - {'id','location_name','location_code','is_active'}:
        raise ValueError('Only location name, registered code and active status may be edited.')
    row = db.session.get(IssuingLocation, UUID(str(data['id'])), with_for_update=True) if data.get('id') else IssuingLocation()
    if row is None:
        raise ValueError('Location not found.')
    name, code = str(data.get('location_name','')).strip(), str(data.get('location_code','')).strip().upper()
    if not 1 <= len(name) <= 120 or not re.fullmatch('[A-Z]{2,10}', code):
        raise ValueError('Enter a name (1–120 characters) and a code (2–10 English letters).')
    if not isinstance(data.get('is_active', True), bool):
        raise ValueError('Active status must be true or false.')
    before = dict(location_name=row.location_name, location_code=row.location_code, is_active=row.is_active) if row.id else None
    row.location_name, row.location_code, row.is_active = name, code, data.get('is_active', True)
    db.session.add(row)
    db.session.flush()
    issuance.initialize_location(row)
    db.session.add(AuditLog(actor_id=current_admin_user().id, entity_type='issuing_locations', entity_id=row.id,
        action='update' if before else 'create', before_values=before,
        after_values=dict(location_name=name, location_code=code, is_active=row.is_active)))
    db.session.commit()
    return jsonify(success=True, id=str(row.id))


@admin_bp.post('/admin/consignments/<uuid:identifier>/cancel')
@require_admin
@mutation('administrator')
def cancel_lr(identifier):
    data = json_object()
    reason = str(data.get('reason') or 'Cancelled by administrator.').strip()
    if data.get('confirmed') is not True or not 1 <= len(reason) <= 1000:
        raise ValueError('Confirm cancellation. An optional reason must be 1–1000 characters.')
    from app import orm as models
    row = db.session.get(models.Consignment, identifier, with_for_update=True)
    if row is None:
        raise ValueError('Shipment not found.')
    if row.archive_record is not None:
        raise ValueError('Restore the archived shipment before cancelling it.')
    if row.lr_status != 'cancelled':
        row.lr_status, row.cancellation_reason = 'cancelled', reason
        db.session.flush()
    db.session.commit()
    return jsonify(success=True, lr_number=row.identifier_value, lr_status='cancelled')


@admin_bp.get('/admin/consignments/<uuid:identifier>/lr-history')
@require_admin
def lr_history(identifier):
    rows = db.session.scalars(select(LRAudit).where(LRAudit.shipment_id == identifier).order_by(LRAudit.recorded_at)).all()
    return jsonify(events=[dict(action=r.action, lr_number=r.lr_number, actor_id=str(r.actor_id),
                                recorded_at=r.recorded_at.isoformat(), reason=r.reason,
                                before=r.before_values, after=r.after_values) for r in rows])


@mutation()
def preview_import():
    from app.admin.consignment_controller import _normalize_header, _normalize_shipment_fields
    uploaded = request.files.get('file')
    if not uploaded or request.form.get('mapping'):
        raise ValueError('Upload Excel/CSV without an identifier mapping.')
    if (uploaded.filename or '').lower().endswith('.csv'):
        source = csv.reader(io.StringIO(uploaded.read().decode('utf-8-sig')))
    elif (uploaded.filename or '').lower().endswith('.xlsx'):
        workbook = load_workbook(uploaded, read_only=True, data_only=True)
        source = workbook.active.iter_rows(values_only=True)
    else:
        raise ValueError('Choose an .xlsx or .csv file.')
    raw_headers = next(source, [])
    if not raw_headers or any(issuance.reserved_header(h) for h in raw_headers):
        raise ValueError('GRAM generates LR numbers automatically. Remove all LR/Consignment Number columns and aliases.')
    headers = [_normalize_header(h) for h in raw_headers]
    if len(set(headers)) != len(headers) or '' in headers:
        raise ValueError('Column names must be unique and non-empty.')
    allowed = {'location_code','status','pieces','boxes_pallets','weight','volume','company_id','measurement_mode',
               'volume_unit','package_contents','pickup_address','pickup_pincode','pickup_tag','pickup_date',
               'drop_address','drop_pincode','drop_tag','drop_date','eta'}
    if set(headers) - allowed:
        raise ValueError('Unrecognized columns: ' + ', '.join(sorted(set(headers) - allowed)))
    if 'status' not in headers:
        raise ValueError('The status column is required.')
    rows, errors = [], []
    for index, values in enumerate(source, 2):
        if all(v in (None,'') for v in values):
            continue
        if len(rows) + len(errors) >= 100:
            raise ValueError('Import at most 100 shipments at a time. Each confirmed upload is atomic.')
        data = dict(zip(headers, values))
        # Normalize Excel date/number cells for durable JSON preview storage.
        data = {k: (v.isoformat() if hasattr(v,'isoformat') else v) for k,v in data.items()}
        try:
            code = str(data.pop('location_code', None) or request.form.get('location_code','')).strip().upper()
            location = db.session.scalar(select(IssuingLocation).where(IssuingLocation.location_code == code))
            data['location_id'] = str(issuance.location_id(location.id if location else None))
            data['location_code'] = location.location_code
            data['pieces'] = data.pop('boxes_pallets', data.get('pieces',1))
            if not str(data.get('status') or '').strip():
                raise ValueError('Status is required.')
            _normalize_shipment_fields(data)
            rows.append(data)
        except ValueError as error:
            errors.append(f'Row {index}: {error}')
    if errors or not rows:
        raise ValueError('\n'.join(errors) or 'No shipment rows found.')
    user = current_admin_user()
    fingerprint = issuance.digest(rows)
    db.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'), {'key':f'lr-import:{user.id}:{fingerprint}'})
    batch = db.session.scalar(select(LRImport).where(LRImport.actor_id == user.id, LRImport.digest == fingerprint))
    if batch is None:
        batch = LRImport(actor_id=user.id, digest=fingerprint, rows=rows)
        db.session.add(batch)
    db.session.commit()
    return render_template('admin/lr_import.html', batch=batch)


@admin_bp.post('/admin/consignments/import/confirm')
@require_admin
@mutation()
def confirm_lr_import():
    from app.admin.consignment_controller import _normalize_shipment_fields, CONSIGNMENT_SAVE_FIELDS
    data = json_object() if request.is_json else request.form
    batch = db.session.get(LRImport, UUID(str(data.get('batch_id'))), with_for_update=True)
    if not batch or batch.actor_id != current_admin_user().id:
        raise ValueError('Import preview not found for this user.')
    if batch.response is None:
        generated = []
        for index, row in sorted(enumerate(batch.rows), key=lambda pair: (pair[1]['location_id'], pair[0])):
            fields = _normalize_shipment_fields(row)
            fields.update({k: row[k] for k in CONSIGNMENT_SAVE_FIELDS if k in row})
            shipment = issuance.create_shipment(row, fields)
            generated.append((index, dict(row=index+2, id=str(shipment.id), lr_number=shipment.identifier_value)))
        batch.response = dict(success=True, shipments=[r for _,r in sorted(generated)])
    db.session.commit()
    if request.is_json:
        return jsonify(batch.response)
    return render_template('admin/lr_import.html', batch=batch)
