"""Persistent, locked label numbering without changing application databases."""
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from uuid import UUID
from zoneinfo import ZoneInfo

from flask import current_app


class LabelRunConflict(ValueError):
    pass


def order_date():
    return datetime.now(ZoneInfo('Asia/Kolkata')).date()


def financial_year(day):
    start = day.year if day.month >= 4 else day.year - 1
    return f'{start}-{str(start + 1)[-2:]}'


def run_id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Reopen the label review before generating labels.') from None


def _read(path, default=None):
    if not path.exists():
        return default
    with path.open() as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise RuntimeError('Invalid label numbering state; restore its backup before generating labels.')
    return value


def _write(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False, encoding='utf-8') as output:
            temporary = Path(output.name)
            json.dump(value, output, ensure_ascii=False, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _locked_state():
    root = Path(current_app.instance_path, 'uploads', '.label-state')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / 'numbering.lock').open('a') as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield root
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def render_label_run(identifier, owner, shipments, render):
    """One assignment per shipment, reused across preview/download and retries.

    Numbering is durable on the instance volume, process/thread safe on Linux,
    and included by the existing complete upload backup. Render errors reserve
    nothing. Counters are persisted before the run, so a disk failure may leave
    a gap but can never cause another run to reuse an assigned sequence.
    """
    identifier = run_id(identifier)
    encoded = json.dumps(shipments, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    fingerprint = hashlib.sha256(encoded).hexdigest()
    with _locked_state() as root:
        path = root / (identifier + '.json')
        existing = _read(path)
        if existing:
            if existing.get('owner') != owner or existing.get('fingerprint') != fingerprint:
                raise LabelRunConflict('This label batch changed. Reopen Review label details to generate a new batch.')
            return render(existing['assignments'])
        day = order_date()
        date_key, year = day.strftime('%Y%m%d'), financial_year(day)
        counters = _read(root / 'counters.json', {'orders': {}, 'invoices': {}})
        order_count = counters['orders'].get(date_key, 0)
        invoice_count = counters['invoices'].get(year, 0)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (order_count, invoice_count)):
            raise RuntimeError('Invalid label sequences; restore the numbering backup.')
        assignments = []
        for offset, shipment in enumerate(shipments, 1):
            assignments.append({'id': shipment['id'],
                'order_id': f'B2B-{date_key}-{order_count + offset:04d}',
                'invoice_number': f'B2B-{year}-{invoice_count + offset:04d}'})
        output = render(assignments)
        counters['orders'][date_key] = order_count + len(shipments)
        counters['invoices'][year] = invoice_count + len(shipments)
        _write(root / 'counters.json', counters)
        _write(path, {'owner': owner, 'fingerprint': fingerprint, 'order_date': day.isoformat(),
                      'shipments': shipments, 'assignments': assignments})
        return output
