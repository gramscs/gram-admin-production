#!/usr/bin/env python3
"""Add private shipment archive snapshots. Read-only preflight unless --apply."""
import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
from uuid import NAMESPACE_URL, uuid5
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import inspect, select, text
from migrations.shipment_v1 import RUNS, configure_wait_limits
from migrations.storage import MigrationError

VERSION = '006_shipment_archives'
SQL_FILE = Path(__file__).with_name('sql') / '006_shipment_archives.sql'


def migrate(engine, *, apply=False):
    script = SQL_FILE.read_text()
    checksum = hashlib.sha256(script.encode()).hexdigest()
    with engine.begin() as connection:
        if not apply:
            connection.execute(text('SET TRANSACTION READ ONLY'))
        configure_wait_limits(connection)
        if apply:
            connection.execute(text('SELECT pg_advisory_xact_lock(723910042006)'))
        if not inspect(connection).has_table(RUNS.name):
            raise MigrationError('Existing migration ledger required.')
        if not connection.execute(select(RUNS.c.version).where(RUNS.c.version == '005_centralized_lr')).first():
            raise MigrationError('Apply migration 005 first.')
        previous = connection.execute(select(RUNS).where(RUNS.c.version == VERSION)).mappings().first()
        if previous:
            if previous['target_digest'] != checksum or not inspect(connection).has_table('shipment_archives'):
                raise MigrationError('Recorded archive migration differs from its schema or checksum.')
            return {'status':'already_applied'}
        if inspect(connection).has_table('shipment_archives'):
            raise MigrationError('Unrecorded archive table exists. Inspect partial state; do not drop it.')
        if apply:
            schema = connection.execute(text('SELECT current_schema()')).scalar_one()
            connection.exec_driver_sql(script.replace('__SCHEMA__',connection.dialect.identifier_preparer.quote(schema)), execution_options={'no_parameters':True})
            connection.execute(RUNS.insert().values(version=VERSION,namespace=uuid5(NAMESPACE_URL,VERSION),source_digest=checksum,target_digest=checksum,
                source_schema={'upgrade':'shipment_archives'},counts={},warnings=[],completed_at=datetime.now(UTC)))
        return {'status':'applied' if apply else 'ready'}


def sql_editor(schema='public'):
    quoted = '"' + schema.replace('"','""') + '"'
    script = SQL_FILE.read_text()
    checksum = hashlib.sha256(script.encode()).hexdigest()
    return f'''-- Run this entire file in Supabase SQL Editor. Targets {quoted}.
BEGIN;
SET LOCAL search_path = {quoted};
SET LOCAL lock_timeout = '15s';
SET LOCAL statement_timeout = '120s';
SELECT pg_advisory_xact_lock(723910042006);
DO $archive_migration$
DECLARE previous text;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM admin_migration_runs WHERE version='005_centralized_lr') THEN RAISE EXCEPTION 'Apply migration 005 first'; END IF;
 SELECT target_digest INTO previous FROM admin_migration_runs WHERE version='{VERSION}';
 IF FOUND THEN
  IF previous <> '{checksum}' OR to_regclass('shipment_archives') IS NULL THEN RAISE EXCEPTION 'Archive migration checksum or schema differs'; END IF;
  RETURN;
 END IF;
 IF to_regclass('shipment_archives') IS NOT NULL THEN RAISE EXCEPTION 'Unrecorded archive table exists; inspect partial migration without dropping records'; END IF;
 EXECUTE $archive_ddl$
{script.replace('__SCHEMA__',quoted)}
$archive_ddl$;
 INSERT INTO admin_migration_runs(version,namespace,source_digest,target_digest,source_schema,counts,warnings,completed_at)
 VALUES('{VERSION}','{uuid5(NAMESPACE_URL,VERSION)}'::uuid,'{checksum}','{checksum}','{{"upgrade":"shipment_archives"}}'::jsonb,'{{}}'::jsonb,'[]'::jsonb,clock_timestamp());
END $archive_migration$;
COMMIT;
SELECT version,completed_at FROM {quoted}.admin_migration_runs WHERE version='{VERSION}';
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--export-sql',type=Path)
    args=parser.parse_args()
    if args.export_sql:
        args.export_sql.write_text(sql_editor())
        print('SQL Editor file exported; no database changes made.')
        return 0
    try:
        from app import create_app
        from app.models import db
        with create_app({'AUTO_CREATE_TABLES':False}).app_context():
            print(json.dumps(migrate(db.engine,apply=args.apply)))
        return 0
    except Exception:
        print('Archive migration stopped. Check migration history, schema, connectivity and permissions. No credentials printed.',file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
