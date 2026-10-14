"""Package contents and explicit units shared by editing, exports and labels."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CBFT_TO_CBFT = Decimal('35.31466672148859')


def volume_ft3(row):
    value = row.volume
    if value is None:
        return None
    value = Decimal(str(value))
    if getattr(row, 'volume_unit', None) != 'ft3':
        value *= CBFT_TO_CBFT
    return value.quantize(Decimal('.001'), rounding=ROUND_HALF_UP)


def normalize_contents(value, boxes):
    """Sparse per-package overrides keep large, mostly identical loads small."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {'kind', 'default_pieces', 'boxes'}:
        raise ValueError('Box contents must contain a package type, default pieces and box exceptions.')
    kind = value.get('kind', 'box')
    if kind not in ('box', 'pallet'):
        raise ValueError('Choose boxes or pallets.')

    def count(raw):
        if raw is None or raw == '':
            return None
        if isinstance(raw, bool) or not str(raw).isascii() or not str(raw).isdigit() or not 1 <= int(raw) <= 1000000:
            raise ValueError('Pieces per box/pallet must be a whole number from 1 to 1000000, or blank if unknown.')
        return int(raw)

    def weight(raw):
        if raw is None or raw == '':
            return None
        if isinstance(raw, bool):
            raise ValueError('Box weight must be non-negative with up to 3 decimal places, or blank to use shipment weight.')
        try:
            number = Decimal(str(raw))
            if not number.is_finite() or number < 0 or number > Decimal('999999999.999') or number != number.quantize(Decimal('.001')):
                raise ValueError()
        except (InvalidOperation, ValueError):
            raise ValueError('Box weight must be non-negative with up to 3 decimal places, or blank to use shipment weight.') from None
        return f'{number:.3f}'

    default = count(value.get('default_pieces'))
    overrides = value.get('boxes', {})
    if not isinstance(overrides, dict) or len(overrides) > boxes:
        raise ValueError('Box exceptions must match the number of boxes/pallets in this shipment.')
    cleaned = {}
    for key, entry in overrides.items():
        if not isinstance(key, str) or not key.isascii() or not key.isdigit() or str(int(key)) != key or not 1 <= int(key) <= boxes:
            raise ValueError('A box exception is outside this shipment. Review Box contents before reducing its box count.')
        if not isinstance(entry, dict) or set(entry) - {'pieces', 'note', 'weight'}:
            raise ValueError('Each box exception needs pieces, weight and an optional note.')
        pieces = count(entry.get('pieces', default))
        box_weight = weight(entry.get('weight'))
        note = entry.get('note', '')
        if not isinstance(note, str) or len(note.strip()) > 120 or any(ord(c) < 32 or ord(c) == 127 for c in note):
            raise ValueError('Box exception notes must be a single line of up to 120 characters.')
        if pieces != default or note.strip() or box_weight is not None:
            cleaned[key] = {'pieces': pieces, 'note': note.strip()}
            if box_weight is not None:
                cleaned[key]['weight'] = box_weight
    return {'kind': kind, 'default_pieces': default, 'boxes': cleaned}


def package_at(contents, number):
    contents = contents or {}
    return {'pieces': contents.get('default_pieces'), 'note': '', **contents.get('boxes', {}).get(str(number), {})}


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
