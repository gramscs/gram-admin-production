# PostgreSQL database upgrades and imports

The application supports PostgreSQL for records and Supabase Storage for files.
It does not create a database, run schema migrations at startup, or fall back to
local persistence.

## Upgrade an existing application database

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
