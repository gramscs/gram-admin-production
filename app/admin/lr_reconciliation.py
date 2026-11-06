"""Explicit, offline-reviewed opening counters; never renumber historical rows."""
import json
import re
from pathlib import Path
import click
from sqlalchemy import select, text
from app.models import db
from app.lr_issuance import digest
from database.lr import IssuingLocation
from database.models import Consignment


def history():
    return [dict(id=str(r.id), number=r.identifier_value, status=r.lr_status)
            for r in db.session.scalars(select(Consignment).order_by(Consignment.id)).all()]


def register(app):
    @app.cli.command('lr-reconciliation-report')
    @click.option('--location-code', required=True)
    def report(location_code):
        records = history()
        code = location_code.strip().upper()
        recognized, unmapped, minimum = [], [], {}
        for record in records:
            match = re.fullmatch(re.escape(code) + r'(\d{4})(\d{6})', record['number'].upper())
            if match and int(match[2]) > 0:
                minimum[match[1]] = max(minimum.get(match[1],0), int(match[2]))
                recognized.append(record)
            else:
                unmapped.append(record)
        click.echo(json.dumps(dict(location_code=code, history_digest=digest(records),
            minimum_opening_counters=minimum, recognized=recognized, unmapped=unmapped,
            note='Review ALL history, including external/cancelled LRs; minimums are not approval.'), indent=2))

    @app.cli.command('lr-approve-opening-counters')
    @click.argument('manifest', type=click.Path(exists=True, dir_okay=False))
    def approve(manifest):
        data = json.loads(Path(manifest).read_text())
        # Block both issuance and concurrent historical changes while verifying the review snapshot.
        db.session.execute(text('LOCK TABLE consignments IN SHARE ROW EXCLUSIVE MODE'))
        location = db.session.scalar(select(IssuingLocation).where(
            IssuingLocation.location_code == data.get('location_code')).with_for_update())
        if not location or location.reconciliation_digest:
            raise click.ClickException('Use an existing, unreconciled location. Approved counters cannot be changed.')
        records = history()
        if data.get('history_digest') != digest(records) or data.get('history_reviewed') is not True or data.get('external_history_reviewed') is not True:
            raise click.ClickException('Review the current history digest and external/cancelled history first.')
        reason, counters = str(data.get('reason','')).strip(), data.get('opening_counters')
        if not reason or not isinstance(counters,dict):
            raise click.ClickException('Provide a review reason and explicit opening_counters object.')
        for fy, value in counters.items():
            if not re.fullmatch(r'\d{4}',fy) or (int(fy[2:]) - int(fy[:2])) % 100 != 1 or type(value) is not int or not 0 <= value <= 999999:
                raise click.ClickException('Counter values must be integers 0–999999 keyed by consecutive YY pairs.')
        for record in records:
            match = re.fullmatch(re.escape(location.location_code) + r'(\d{4})(\d{6})',record['number'].upper())
            if match and counters.get(match[1],0) < int(match[2]):
                raise click.ClickException('An opening counter is below a historical LR, including cancelled LRs.')
        db.session.execute(text('UPDATE issuing_locations SET reconciliation_digest=:digest, reconciliation_reason=:reason, opening_counters=CAST(:counters AS jsonb) WHERE id=:id'),
            dict(digest=data['history_digest'], reason=reason, counters=json.dumps(counters), id=location.id))
        db.session.commit()
        click.echo('Reviewed opening counters saved. No historical identifiers were changed.')
