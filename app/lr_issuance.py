"""Shared transaction helpers; PostgreSQL migration 005 owns LR allocation."""
import hashlib
import json
import re
from uuid import UUID
from zoneinfo import ZoneInfo
from sqlalchemy import select, text
from app.models import db
from database.models import Consignment
from database.lr import IssuingLocation, LRRequest

PROTECTED = {'lr_number', 'lr_sequence', 'financial_year', 'lr_issued_at', 'created_at',
             'lr_status', 'cancelled_at', 'cancelled_by', 'cancellation_reason'}

def financial_year(moment):
    if moment.tzinfo is None:
        raise ValueError('Issuance time must be timezone-aware.')
    local = moment.astimezone(ZoneInfo('Asia/Kolkata'))
    year = local.year - (local.month < 4)
    return f'{year % 100:02}{(year + 1) % 100:02}'

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()

def actor(user):
    db.session.execute(text("SELECT set_config('gram.actor_id', :actor, true)"), {'actor': str(user.id)})

def begin_request(user, key, payload):
    try:
        key = UUID(str(key))
    except (ValueError, TypeError):
        raise ValueError('A persistent UUID idempotency_key is required. Reuse it only when retrying the same request.') from None
    actor(user)
    db.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                       {'key': f'lr-request:{user.id}:{key}'})
    previous = db.session.get(LRRequest, (user.id, key))
    fingerprint = digest(payload)
    if previous and previous.digest != fingerprint:
        raise ValueError('This idempotency key was already used with different shipment data.')
    return key, fingerprint, previous.response if previous else None

def finish_request(user, key, fingerprint, response):
    db.session.add(LRRequest(actor_id=user.id, request_key=key, digest=fingerprint, response=response))

def initialize_location(location):
    """Initialize an administrator-managed location without an approval workflow.

    Caller holds the location row lock (or just inserted it). Historical numbers
    only establish the opening floor; normal issuance still uses the DB counter.
    Never reset an existing initialization or reserve a new number here.
    """
    if location.reconciliation_digest:
        return
    numbers = db.session.scalars(select(Consignment.identifier_value).where(
        Consignment.identifier_value.ilike(location.location_code + '%'))).all()
    counters, recognized = {}, []
    pattern = re.compile(re.escape(location.location_code) + r'(\d{4})(\d{6})', re.ASCII)
    for number in numbers:
        match = pattern.fullmatch(number.upper())
        if match and int(match[2]) > 0:
            counters[match[1]] = max(counters.get(match[1], 0), int(match[2]))
            recognized.append(number)
    db.session.execute(text("""UPDATE issuing_locations
        SET reconciliation_digest=:digest, reconciliation_reason=:reason,
            opening_counters=CAST(:counters AS jsonb)
        WHERE id=:id AND reconciliation_digest IS NULL"""),
        dict(id=location.id, digest=digest(sorted(recognized)), counters=json.dumps(counters),
             reason='Automatically initialized for an administrator-managed location from existing LR history.'))
    db.session.refresh(location)


def location_id(value):
    try:
        identifier = UUID(str(value))
    except (ValueError, TypeError):
        raise ValueError('Choose a registered issuing location.') from None
    location = db.session.scalar(select(IssuingLocation).where(IssuingLocation.id == identifier)
        .with_for_update().execution_options(populate_existing=True))
    if not location or not location.is_active:
        raise ValueError('Choose an active issuing location.')
    # Also handles locations left pending by an earlier application version.
    initialize_location(location)
    return identifier

def validate_identity(data, existing=None):
    for name in PROTECTED.intersection(data):
        expected = getattr(existing, name, None) if existing else None
        if hasattr(expected, 'isoformat'):
            expected = expected.isoformat()
        if existing is None or str(data[name] or '') != str(expected or ''):
            raise ValueError('LR identity, timestamps and cancellation fields cannot be supplied or changed.')
    if 'identifier_value' in data:
        raise ValueError('Do not supply an internal identifier value.')
    supplied = data.get('consignment_number')
    if existing is None:
        if supplied not in (None, '') or data.get('identifier_type', 'LRN') != 'LRN':
            raise ValueError('GRAM generates LR numbers automatically. Do not supply an identifier.')
    elif supplied is not None and supplied != existing.identifier_value:
        raise ValueError('Issued and historical shipment identifiers cannot be changed.')
    if existing and 'location_id' in data and str(data['location_id'] or '') != str(existing.location_id or ''):
        raise ValueError('Issuing location cannot change after issuance.')
    if existing and 'identifier_type' in data and data['identifier_type'] != existing.identifier_type:
        raise ValueError('Identifier type cannot change after issuance.')
    if existing and existing.lr_status == 'cancelled':
        raise ValueError('Cancelled shipments are read-only.')

def create_shipment(data, fields):
    validate_identity(data)
    row = Consignment(location_id=location_id(data.get('location_id')))
    for key, value in fields.items():
        setattr(row, key, value)
    db.session.add(row)
    db.session.flush()
    db.session.refresh(row)
    return row

def reserved_header(value):
    token = re.sub(r'[^a-z0-9]', '', str(value or '').casefold())
    return token in {'lr', 'lrn', 'cn', 'gr', 'lrnumber', 'lrno', 'lrnum', 'lridentifier',
                     'consignmentnumber', 'consignmentno', 'consignmentnum', 'consignmentid',
                     'cnnumber', 'cnno', 'grnumber', 'grno', 'gramlr', 'gramlrnumber',
                     'lorryreceiptnumber', 'lorryreceiptno', 'identifiervalue', 'identifiertype',
                     'lrsequence', 'financialyear', 'lrissuedat', 'lrstatus', 'createdat',
                     'cancelledat', 'cancelledby', 'cancellationreason'}


def public_error(error):
    """Translate known database guard failures without disclosing SQL/parameters."""
    primary = getattr(getattr(getattr(error, 'orig', None), 'diag', None), 'message_primary', '') or ''
    if 'capacity exhausted' in primary:
        return 'LR sequence capacity exhausted. Contact an administrator. No shipments were issued.'
    if 'reconciled issuing location' in primary:
        return 'Issuing location is unavailable. Refresh and choose an active location. Nothing was issued.'
    if getattr(getattr(error, 'orig', None), 'pgcode', '') == '23505':
        return 'LR or location collision. Ask an administrator to check registered codes and historical LR numbers. Nothing was issued.'
    return 'LR transaction failed. Check migration 005, permissions and location configuration. Retry the same request key after correction.'
