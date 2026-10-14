"""Package contents and explicit units shared by editing, exports and labels."""
from decimal import Decimal, ROUND_HALF_UP

CBFT_TO_CBFT = Decimal('35.31466672148859')


def volume_ft3(row):
    value = row.volume
    if value is None:
        return None
    value = Decimal(str(value))
    if getattr(row, 'volume_unit', None) != 'ft3':
        value *= CBFT_TO_CBFT
    return value.quantize(Decimal('.001'), rounding=ROUND_HALF_UP)


def shared_contents(value):
    """Only shipment-wide package settings are used; old box overrides are inert."""
    if value is None:
        return None
    return {'kind': value.get('kind', 'box'), 'default_pieces': value.get('default_pieces')}


def normalize_contents(value):
    """Validate shared contents; discard retired per-box data from older clients."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {'kind', 'default_pieces', 'boxes'}:
        raise ValueError('Box contents must contain a package type and pieces per box/pallet.')
    kind = value.get('kind', 'box')
    if kind not in ('box', 'pallet'):
        raise ValueError('Choose boxes or pallets.')
    raw = value.get('default_pieces')
    if raw is None or raw == '':
        count = None
    elif isinstance(raw, bool) or not str(raw).isascii() or not str(raw).isdigit() or not 1 <= int(raw) <= 1000000:
        raise ValueError('Pieces per box/pallet must be a whole number from 1 to 1000000, or blank if unknown.')
    else:
        count = int(raw)
    return {'kind': kind, 'default_pieces': count}


def upgrade_volume_units():
    """Convert operational rows once, atomically; leave historical reports intact."""
    from app import orm as models
    from app.models import db
    converted = 0
    try:
        rows = models.shipment_query().with_for_update(of=models.Consignment).all()
        for row in rows:
            if row.volume_unit == 'ft3':
                continue
            if row.volume is not None:
                value = volume_ft3(row)
                if not value.is_finite() or value < 0 or value > Decimal('999999999.999'):
                    raise ValueError(f'Volume for shipment {row.consignment_number} is outside the supported range after CBM conversion. No volumes were converted.')
                row.volume = value
                converted += 1
            row.volume_unit = 'ft3'
        db.session.commit()
        return converted
    except Exception:
        db.session.rollback()
        raise
