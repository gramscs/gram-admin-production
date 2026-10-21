#!/usr/bin/env python3
"""Permit versioned LR attachments in the existing PostgreSQL shipment schema.

Run without arguments for a read-only preflight; --apply commits and verifies.
No tables, document versions, storage objects or application data are replaced.
"""
import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import sys
from uuid import NAMESPACE_URL, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, select, text

from migrations.shipment_v1 import LEDGER, RUNS, configure_wait_limits
from migrations.storage import MigrationError

VERSION = '003_lorry_receipts'
CONSTRAINT = 'ck_shipment_documents_valid_type'
SQL = f"ALTER TABLE shipment_documents ADD CONSTRAINT {CONSTRAINT} CHECK (document_type IN ('POD', 'invoice', 'LR'))"
REVISION_DIGEST = hashlib.sha256(SQL.encode()).hexdigest()


def _types(connection, schema):
    constraints = inspect(connection).get_check_constraints('shipment_documents', schema=schema)
    constraint = next((item for item in constraints if item['name'] == CONSTRAINT), None)
    if not constraint or 'document_type' not in constraint['sqltext']:
        raise MigrationError('The shipment document type constraint is missing or incompatible. No records were changed.')
    types = set(re.findall(r"'([^']+)'", constraint['sqltext']))
    if types not in ({'POD', 'invoice'}, {'POD', 'invoice', 'LR'}):
        raise MigrationError('Unexpected document types. Review the existing constraint before migration.')
    return types


def migrate(engine, *, apply=False):
    if engine.dialect.name != 'postgresql':
        raise MigrationError('Use the PostgreSQL database configured by DATABASE_URL.')
    with engine.begin() as connection:
        if not apply:
            connection.execute(text('SET TRANSACTION READ ONLY'))
        configure_wait_limits(connection)
        if apply:
            connection.execute(text('SELECT pg_advisory_xact_lock(723910042004)'))
        schema = connection.execute(text('SELECT current_schema()')).scalar_one()
        inspector = inspect(connection)
        if not all(inspector.has_table(name, schema=schema) for name in ('consignments', 'files', 'shipment_documents')):
            raise MigrationError('Expected an existing shipment ER database. No tables were created.')
        types = _types(connection, schema)
        previous = None
        if inspector.has_table(RUNS.name, schema=schema):
            previous = connection.execute(select(RUNS).where(RUNS.c.version == VERSION)).mappings().first()
        if previous and previous['target_digest'] != REVISION_DIGEST:
            raise MigrationError('Migration 003 differs from its recorded revision. Use the original migration file.')
        if previous and 'LR' not in types:
            raise MigrationError('Migration 003 is recorded but its constraint was changed. Review the database before retrying.')
        count = connection.execute(text('SELECT count(*) FROM shipment_documents')).scalar_one()
        result = dict(version=VERSION, schema=schema, database='postgresql', existing_documents=count,
                      lr_supported='LR' in types, recorded=previous is not None)
        if not apply:
            return {**result, 'status': 'ready'}
        if 'LR' not in types:
            connection.execute(text(f'ALTER TABLE shipment_documents DROP CONSTRAINT {CONSTRAINT}'))
            connection.execute(text(SQL))
        if _types(connection, schema) != {'POD', 'invoice', 'LR'}:
            raise MigrationError('Document type verification failed. The transaction was rolled back.')
        if connection.execute(text('SELECT count(*) FROM shipment_documents')).scalar_one() != count:
            raise MigrationError('Document preservation check failed. The transaction was rolled back.')
        LEDGER.create_all(connection)
        for table in (RUNS.name, 'admin_migration_records'):
            connection.execute(text(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY'))
        if previous is None:
            source = {'allowed_types': sorted(types)}
            connection.execute(RUNS.insert().values(
                version=VERSION, namespace=uuid5(NAMESPACE_URL, 'gram-scs/migrations/' + VERSION),
                source_digest=hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest(),
                target_digest=REVISION_DIGEST, source_schema=source,
                counts={'shipment_documents': {'preserved_rows': count}}, warnings=[], completed_at=datetime.now(UTC)))
        return {**result, 'lr_supported': True, 'status': 'already_applied' if previous else 'applied'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    engine = None
    try:
        from app import create_app
        from app.models import db
        application = create_app({'AUTO_CREATE_TABLES': False})
        with application.app_context():
            engine = db.engine
            result = migrate(engine, apply=args.apply)
        print(json.dumps(result, indent=2))
        print('Committed and verified. Existing shipment documents were preserved.' if args.apply
              else 'Read-only preflight passed. Add --apply to migrate the configured database.')
        return 0
    except MigrationError as error:
        print(f'Migration stopped: {error}', file=sys.stderr)
        return 1
    except Exception:
        print('Migration could not be confirmed. Check DATABASE_URL, connectivity and schema permissions. '
              'No connection details were printed.', file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
