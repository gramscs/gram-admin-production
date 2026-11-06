#!/usr/bin/env python3
"""Allow authenticated shipment access users to cancel issued LR shipments."""
import argparse
from datetime import UTC, datetime
import hashlib
from pathlib import Path
import sys
from uuid import NAMESPACE_URL, uuid5
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import inspect, select, text
from migrations.shipment_v1 import RUNS, configure_wait_limits
from migrations.storage import MigrationError

VERSION = '007_operator_shipment_cancellation'
SQL_FILE = Path(__file__).with_name('sql') / '007_operator_shipment_cancellation.sql'


def migrate(engine, *, apply=False):
    script = SQL_FILE.read_text()
    checksum = hashlib.sha256(script.encode()).hexdigest()
    with engine.begin() as connection:
        if not apply:
            connection.execute(text('SET TRANSACTION READ ONLY'))
        configure_wait_limits(connection)
        if apply:
            connection.exec_driver_sql('SELECT pg_advisory_xact_lock(723910042007)')
        if not inspect(connection).has_table(RUNS.name):
            raise MigrationError('Existing migration ledger required.')
        if not connection.execute(select(RUNS.c.version).where(RUNS.c.version == '005_centralized_lr')).first():
            raise MigrationError('Apply migration 005 first.')
        previous = connection.execute(select(RUNS).where(RUNS.c.version == VERSION)).mappings().first()
        if previous:
            if previous['target_digest'] != checksum:
                raise MigrationError('Recorded cancellation migration checksum differs.')
            return {'status': 'already_applied'}
        if apply:
            schema = connection.execute(text('SELECT current_schema()')).scalar_one()
            connection.exec_driver_sql(script.replace('__SCHEMA__', connection.dialect.identifier_preparer.quote(schema)), execution_options={'no_parameters': True})
            connection.execute(RUNS.insert().values(
                version=VERSION,
                namespace=uuid5(NAMESPACE_URL, VERSION),
                source_digest=checksum,
                target_digest=checksum,
                source_schema={'upgrade': 'shipment_cancellation_access'},
                counts={},
                warnings=[],
                completed_at=datetime.now(UTC),
            ))
        return {'status': 'applied' if apply else 'ready'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        from app import create_app
        from app.models import db
        with create_app({'AUTO_CREATE_TABLES': False}).app_context():
            print(migrate(db.engine, apply=args.apply))
        return 0
    except Exception as error:
        print(f'Migration stopped: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
