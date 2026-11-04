#!/usr/bin/env python3
"""Read-only preflight by default. Apply only after staging and review."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid5, NAMESPACE_URL
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import inspect, select, text
from migrations.shipment_v1 import RUNS, configure_wait_limits
from migrations.storage import MigrationError

VERSION = '005_centralized_lr'
SQL_FILE = Path(__file__).with_name('sql') / '005_centralized_lr.sql'

def migrate(engine, *, apply=False):
    script = SQL_FILE.read_text()
    checksum = hashlib.sha256(script.encode()).hexdigest()
    with engine.begin() as connection:
        if not apply:
            connection.execute(text('SET TRANSACTION READ ONLY'))
        configure_wait_limits(connection)
        if apply:
            connection.execute(text('SELECT pg_advisory_xact_lock(723910042005)'))
        if not inspect(connection).has_table(RUNS.name):
            raise MigrationError('Existing migration ledger required.')
        previous = connection.execute(select(RUNS).where(RUNS.c.version == VERSION)).mappings().first()
        if previous:
            if previous['target_digest'] != checksum:
                raise MigrationError('Migration checksum differs from recorded revision.')
            if not inspect(connection).has_table('lr_sequences'):
                raise MigrationError('Recorded LR structure is missing.')
            return {'status': 'already_applied'}
        count = connection.execute(text('SELECT count(*) FROM consignments')).scalar_one()
        original_acl = connection.execute(text("SELECT relacl::text FROM pg_class WHERE oid='consignments'::regclass")).scalar_one()
        if apply:
            schema = connection.execute(text('SELECT current_schema()')).scalar_one()
            script = script.replace('__SCHEMA__', connection.dialect.identifier_preparer.quote(schema))
            connection.exec_driver_sql(script, execution_options={'no_parameters': True})
            connection.execute(RUNS.insert().values(version=VERSION, namespace=uuid5(NAMESPACE_URL, VERSION),
                source_digest=checksum, target_digest=checksum, source_schema={'upgrade':'centralized_lr', 'original_consignments_acl':original_acl},
                counts={'consignments': {'preserved_rows':count}}, warnings=[], completed_at=__import__('datetime').datetime.now(__import__('datetime').UTC)))
            if connection.execute(text('SELECT count(*) FROM consignments')).scalar_one() != count:
                raise MigrationError('Shipment preservation check failed.')
        return {'status': 'applied' if apply else 'ready', 'historical_shipments': count,
                'issuance': 'blocked until opening counters are reviewed'}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        from app import create_app
        from app.models import db
        with create_app({'AUTO_CREATE_TABLES': False}).app_context():
            print(json.dumps(migrate(db.engine, apply=args.apply), indent=2))
    except Exception:
        print('Migration stopped. Check connectivity, existing schema, permissions and migration history. No credentials printed.', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
