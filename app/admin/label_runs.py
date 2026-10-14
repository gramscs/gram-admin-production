"""Database-backed label numbering and idempotent label batch rendering."""
from datetime import date, datetime
import hashlib
import json
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, text
from sqlalchemy.exc import ProgrammingError

from app.models import db
from database.models import LabelRun


class LabelRunConflict(ValueError):
    pass


class LabelSchemaMissing(RuntimeError):
    pass


def order_date():
    return datetime.now(ZoneInfo('Asia/Kolkata')).date()


def financial_year(day):
    start = day.year if day.month >= 4 else day.year - 1
    return f'{start}-{str(start + 1)[-2:]}'


def run_id(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Reopen the label review before generating labels.') from None


def render_label_run(identifier, owner, shipments, render):
    """Persist label assignments transactionally in PostgreSQL."""
    if db.engine.dialect.name != 'postgresql':
        raise RuntimeError('Label numbering requires PostgreSQL.')
    identifier = run_id(identifier)
    encoded = json.dumps(shipments, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    fingerprint = hashlib.sha256(encoded).hexdigest()
    session = db.session
    try:
        session.execute(text('SELECT pg_advisory_xact_lock(723910042003)'))
        try:
            existing = session.get(LabelRun, identifier)
        except ProgrammingError as error:
            if getattr(error.orig, 'pgcode', None) == '42P01':
                raise LabelSchemaMissing(
                    'Label numbering is not set up in the database. In the app terminal, run '
                    'python -m flask --app wsgi:app upgrade-db, then retry generating labels.'
                ) from error
            raise
        if existing is not None:
            if existing.owner != owner or existing.fingerprint != fingerprint:
                raise LabelRunConflict('This label batch changed. Reopen Review label details to generate a new batch.')
            output = render(existing.assignments)
            session.rollback()
            return output

        day = order_date()
        year = financial_year(day)
        order_end = session.query(func.max(LabelRun.order_sequence_start + LabelRun.shipment_count - 1)).filter_by(
            order_date=day).scalar() or 0
        invoice_end = session.query(func.max(LabelRun.invoice_sequence_start + LabelRun.shipment_count - 1)).filter_by(
            financial_year=year).scalar() or 0
        order_start, invoice_start = order_end + 1, invoice_end + 1
        assignments = [
            {
                'id': shipment['id'],
                'order_id': f'B2B-{day:%Y%m%d}-{order_start + offset:04d}',
                'invoice_number': f'B2B-{year}-{invoice_start + offset:04d}',
            }
            for offset, shipment in enumerate(shipments)
        ]
        output = render(assignments)
        session.add(LabelRun(
            id=identifier,
            owner=owner,
            fingerprint=fingerprint,
            order_date=day,
            financial_year=year,
            shipment_count=len(shipments),
            order_sequence_start=order_start,
            invoice_sequence_start=invoice_start,
            assignments=assignments,
        ))
        session.commit()
        return output
    except Exception:
        session.rollback()
        raise
