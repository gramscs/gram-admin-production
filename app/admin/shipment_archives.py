"""Private archive snapshots in the existing PostgreSQL database."""
from flask import jsonify
from sqlalchemy import select
from app.admin import admin_bp
from app.admin.auth import require_admin, current_admin_user
from app.admin.lr_control import mutation, json_object
from app.models import db
from app import orm as models
from database.lr import ShipmentArchive
from database.models import Consignment, AuditLog
from migrations.shipment_v1 import json_value


def archived(row):
    return db.session.get(ShipmentArchive,row.id) is not None


@admin_bp.post('/admin/consignments/<uuid:identifier>/archive')
@require_admin
@mutation('administrator')
def archive_shipment(identifier):
    from app.admin.consignment_controller import _serialize_consignment
    row=db.session.get(models.Consignment,identifier,with_for_update=True)
    if not row:
        raise ValueError('Shipment not found.')
    data=json_object()
    if data.get('confirmed') is not True:
        raise ValueError('Confirm archiving this shipment.')
    if not archived(row):
        snapshot=json_value(dict(shipment=_serialize_consignment(row),
            canonical={c.key:getattr(row,c.key) for c in row.__table__.columns},
            extras=row.extra_record.data if row.extra_record else {}))
        db.session.add(ShipmentArchive(shipment_id=row.id,archived_by=current_admin_user().id,snapshot=snapshot))
        db.session.add(AuditLog(actor_id=current_admin_user().id,entity_type='consignments',entity_id=row.id,
            action='archive',before_values=None,after_values=snapshot))
    db.session.commit()
    return jsonify(success=True,message='Shipment archived.')


@admin_bp.post('/admin/consignments/<uuid:identifier>/restore')
@require_admin
@mutation('administrator')
def restore_shipment(identifier):
    row=db.session.get(models.Consignment,identifier,with_for_update=True)
    if not row:
        raise ValueError('Shipment not found.')
    if json_object().get('confirmed') is not True:
        raise ValueError('Confirm restoring this shipment.')
    if row.lr_status == 'cancelled':
        raise ValueError('Cancelled shipments cannot return to the active register.')
    archive=db.session.get(ShipmentArchive,identifier)
    if archive:
        db.session.add(AuditLog(actor_id=current_admin_user().id,entity_type='consignments',entity_id=row.id,
            action='restore',before_values=archive.snapshot,after_values={'archived':False}))
        db.session.delete(archive)
    db.session.commit()
    return jsonify(success=True,message='Shipment restored to the active register.')
