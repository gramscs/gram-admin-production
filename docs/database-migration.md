# PostgreSQL database upgrades and imports

The application supports PostgreSQL for records and Supabase Storage for files.
It does not create a database, run schema migrations at startup, or fall back to
local persistence.

## Upgrade an existing application database

### Version 002: PostgreSQL label numbering

[`002_label_runs.py`](../migrations/002_label_runs.py) adds the missing
`label_runs` table to the **existing PostgreSQL database in `DATABASE_URL`**.
It uses the application's `.env` loading and connection settings. Running a
migration from Codespaces changes that configured database, not a database on
the Codespaces filesystem.

From the repository, with the app's virtual environment activated:

```bash
git pull origin standalone-admin
python migrations/002_label_runs.py --apply
AUTO_CREATE_TABLES=false python -m flask --app wsgi:app check-db
```

The migration creates the table, constraints and indexes, enables row-level
security, and records `002_label_runs` in `admin_migration_runs` in the same
database. It preserves existing shipments and label-numbering assignments.
DDL and the history entry commit together; failure rolls back the transaction.
Repeated application verifies the existing table and keeps the same history
entry and numbering. Omit `--apply` for a read-only preflight.

After the command succeeds, reopen label review and retry preview/download.
Do not rerun migration 001 or move data to another database for this error.
Pushing/pulling this file alone does not execute it on Supabase. No local
database or persistence fallback is created. The migration does not import old
local numbering files or regenerate historical PDFs.

If migration 002 is already recorded but its table has subsequently disappeared,
the command stops instead of silently restarting numbering. Restore the
missing numbering history before retrying. Application rollback can leave this
additive table in place; do not drop it after labels have been generated.

### General support-table maintenance

Set `DATABASE_URL` to the intended PostgreSQL database. Run:

```bash
AUTO_CREATE_TABLES=false python -m flask --app wsgi:app upgrade-db
AUTO_CREATE_TABLES=false python -m flask --app wsgi:app check-db
AUTO_CREATE_TABLES=false python -m flask --app wsgi:app check-storage
```

`upgrade-db` is idempotent and additive: it creates missing application support
tables (`admin_record_extras` and `label_runs`) and preserves existing business
tables and records. It does not rebuild the schema, rename or drop business
columns, or import data. Database columns and public API fields for shipment
measurements are `weight` and `volume`.

For future business-schema changes, add a dedicated versioned PostgreSQL
migration that alters the existing tables transactionally and preserves their
data. Do not use the one-time importer below for routine schema changes.

## One-time import into a fresh database

[`migrations/001_shipment_database.py`](../migrations/001_shipment_database.py)
imports an existing PostgreSQL source into a **fresh PostgreSQL destination**.
It creates the ER business tables and migration archive transactionally. It is
not used for regular application upgrades.

Configure both connection strings securely:

- `MIGRATION_SOURCE_DATABASE_URL`: the existing PostgreSQL source.
- `DATABASE_URL`: a different, empty PostgreSQL destination.

Then run a read-only preflight:

```bash
python migrations/001_shipment_database.py
```

Apply only after reviewing the reported counts and warnings:

```bash
python migrations/001_shipment_database.py --apply
```

The source and destination must be separate. Source files must already be
available in the configured private Supabase buckets; local file references are
not imported. The importer verifies referenced objects and inventories the
configured Supabase namespaces. Existing source records and object bytes are
not modified. For a full-model source, identities, document history, and
relationships are preserved. Every source table and row is retained in the
migration archive.

The database role must be able to read and lock all source tables, create target
tables, indexes, and constraints, and enable row-level security. Supabase
connections require SSL. Keep connection strings outside command arguments and
source control.

## Historical files

Historical database rows that point to local file paths are intentionally left
unchanged. The application no longer reads, creates, or deletes local upload
files, so those references are not retrievable through the application. Move
such files to Supabase and update their records only as a separately authorized
data migration.
