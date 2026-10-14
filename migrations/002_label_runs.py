#!/usr/bin/env python3
"""Add PostgreSQL label numbering to the existing application database.

python migrations/002_label_runs.py --apply
Omit --apply for a read-only preflight. Uses the app's DATABASE_URL and .env.
"""
import argparse
from datetime import datetime, UTC
import hashlib
import json
from pathlib import Path
import sys
from uuid import NAMESPACE_URL, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, select, text

from migrations.shipment_v1 import LEDGER, RUNS, configure_wait_limits
from migrations.storage import MigrationError

VERSION = '002_label_runs'
# Keep this revision fixed rather than importing a model that may change later.
TABLE_SQL = """CREATE TABLE IF NOT EXISTS label_runs (
    id UUID CONSTRAINT pk_label_runs PRIMARY KEY DEFAULT gen_random_uuid(),
    owner VARCHAR(120) NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    order_date DATE NOT NULL,
    financial_year VARCHAR(7) NOT NULL,
    shipment_count INTEGER NOT NULL,
    order_sequence_start INTEGER NOT NULL,
    invoice_sequence_start INTEGER NOT NULL,
    assignments JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_label_runs_positive_shipment_count CHECK (shipment_count > 0),
    CONSTRAINT ck_label_runs_positive_order_sequence CHECK (order_sequence_start > 0),
    CONSTRAINT ck_label_runs_positive_invoice_sequence CHECK (invoice_sequence_start > 0)
)"""
INDEX_SQL = (
    'CREATE INDEX IF NOT EXISTS ix_label_runs_order_date ON label_runs (order_date)',
    'CREATE INDEX IF NOT EXISTS ix_label_runs_financial_year ON label_runs (financial_year)',
)
COLUMN_TYPES = {
    'id': 'UUID', 'owner': 'VARCHAR(120)', 'fingerprint': 'VARCHAR(64)',
    'order_date': 'DATE', 'financial_year': 'VARCHAR(7)', 'shipment_count': 'INTEGER',
    'order_sequence_start': 'INTEGER', 'invoice_sequence_start': 'INTEGER',
    'assignments': 'JSONB', 'created_at': 'TIMESTAMP WITH TIME ZONE',
}
REVISION_DIGEST = hashlib.sha256((TABLE_SQL + '\n'.join(INDEX_SQL)).encode()).hexdigest()


def check_label_table(connection, schema):
    inspector = inspect(connection)
    if not inspector.has_table('label_runs', schema=schema):
        return None
    columns = {column['name']: column for column in inspector.get_columns('label_runs', schema=schema)}
    invalid = [name for name, kind in COLUMN_TYPES.items() if name not in columns
               or str(columns[name]['type'].compile(dialect=connection.dialect)) != kind
               or columns[name]['nullable']]
    if invalid or inspector.get_pk_constraint('label_runs', schema=schema)['constrained_columns'] != ['id']:
        raise MigrationError('The existing label_runs structure is incompatible. No existing table or records were replaced.')
    return sorted(columns)


def migrate(engine, *, apply=False):
    if engine.dialect.name != 'postgresql':
        raise MigrationError('This migration requires the PostgreSQL database configured by DATABASE_URL.')
    with engine.begin() as connection:
        if not apply:
            connection.execute(text('SET TRANSACTION READ ONLY'))
        configure_wait_limits(connection)
        if apply:
            # Match the support-table upgrade and label generator locks.
            connection.execute(text('SELECT pg_advisory_xact_lock(723910042002)'))
            connection.execute(text('SELECT pg_advisory_xact_lock(723910042003)'))
        schema = connection.execute(text('SELECT current_schema()')).scalar_one()
        inspector = inspect(connection)
        if not inspector.has_table('consignments', schema=schema):
            raise MigrationError('Expected an existing shipment database. Check DATABASE_URL; do not use a fresh database for migration 002.')
        shipment_columns = {column['name'] for column in inspector.get_columns('consignments', schema=schema)}
        if not {'id', 'identifier_value'} <= shipment_columns:
            raise MigrationError('The configured database does not have the shipment ER schema.')
        original_columns = check_label_table(connection, schema)
        previous = None
        if inspector.has_table(RUNS.name, schema=schema):
            previous = connection.execute(select(RUNS).where(RUNS.c.version == VERSION)).mappings().first()
        if previous:
            if original_columns is None:
                raise MigrationError('Migration 002 was recorded but label_runs is missing. Restore its numbering history before retrying; it will not be reset.')
            if previous['target_digest'] != REVISION_DIGEST:
                raise MigrationError('Migration 002 differs from the recorded revision. Use the original migration file.')
        preserved = connection.execute(text('SELECT count(*) FROM label_runs')).scalar_one() if original_columns else 0
        result = {'version': VERSION, 'database': 'postgresql', 'schema': schema,
                  'existing_label_runs': preserved, 'create_label_runs': original_columns is None}
        if not apply:
            return {**result, 'status': 'ready', 'recorded': previous is not None}

        connection.execute(text(TABLE_SQL))
        for statement in INDEX_SQL:
            connection.execute(text(statement))
        LEDGER.create_all(connection)
        for table in ('label_runs', RUNS.name, 'admin_migration_records'):
            connection.execute(text(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY'))
        check_label_table(connection, schema)
        indexes = {index['name']: index['column_names'] for index in inspect(connection).get_indexes('label_runs', schema=schema)}
        if indexes.get('ix_label_runs_order_date') != ['order_date'] or indexes.get('ix_label_runs_financial_year') != ['financial_year']:
            raise MigrationError('Label numbering indexes could not be verified. The migration was rolled back.')
        secured = connection.execute(text("SELECT relrowsecurity FROM pg_class WHERE oid = 'label_runs'::regclass")).scalar_one()
        if not secured or connection.execute(text('SELECT count(*) FROM label_runs')).scalar_one() != preserved:
            raise MigrationError('Label numbering preservation/security checks failed. The migration was rolled back.')
        if previous is None:
            source = {'schema': schema, 'label_runs': original_columns}
            connection.execute(RUNS.insert().values(
                version=VERSION, namespace=uuid5(NAMESPACE_URL, 'gram-scs/migrations/' + VERSION),
                source_digest=hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest(),
                target_digest=REVISION_DIGEST, source_schema=source,
                counts={'label_runs': {'created': original_columns is None, 'preserved_rows': preserved}},
                warnings=[], completed_at=datetime.now(UTC),
            ))
        return {**result, 'status': 'already_applied' if previous else 'applied'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Apply the additive migration to DATABASE_URL in one transaction.')
    args = parser.parse_args(argv)
    engine = None
    try:
        # Reuse the app's URL validation, SSL settings and .env loading. Disable
        # startup DDL so even AUTO_CREATE_TABLES=true cannot alter a preflight.
        from app import create_app
        from app.models import db
        application = create_app({'AUTO_CREATE_TABLES': False})
        with application.app_context():
            engine = db.engine
            result = migrate(engine, apply=args.apply)
        print(json.dumps(result, indent=2))
        print('Migration committed and verified in PostgreSQL. Existing shipment and label records were preserved.'
              if args.apply else 'Preflight passed. No database changes were made. Add --apply to run migration 002.')
        return 0
    except MigrationError as error:
        print(f'Migration stopped: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Migration interrupted. Retry the same command to verify its state.', file=sys.stderr)
        return 130
    except Exception:
        print('Migration could not be confirmed. Check DATABASE_URL, PostgreSQL connectivity and schema permissions. '
              'Retry this same command; existing numbering is preserved. No connection details were printed.', file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
