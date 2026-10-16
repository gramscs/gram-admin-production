# Gram SCS — Standalone Admin Panel

This is a standalone Flask admin panel. It uses PostgreSQL for application data
and private Supabase Storage buckets for uploaded documents and generated reports.
It does not keep application data in local database or file storage.

## Configure and run

Requires Python 3.12. Copy [.env.example](.env.example) to `.env` and configure
`DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_KEY`, `SECRET_KEY`, and
`ADMIN_PASSWORD_HASH`. The database URL must point to PostgreSQL. Supabase
Storage must contain private `pod-uploads`, `invoice-uploads`, and
`mis-reports` buckets (or the bucket names configured in `.env`).

Install dependencies and start the app:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py
```

Render uses Python 3.12 as pinned in `.python-version` and the `Procfile` WSGI
entry point (`wsgi:app`). The `app:app` target also works for Render services
with that start command configured.

Schema creation is not automatic on startup. For an existing compatible ER
database, run the additive support-table upgrade and verify the schema:

```bash
python -m flask --app wsgi:app upgrade-db
python -m flask --app wsgi:app check-db
python -m flask --app wsgi:app check-storage
```

`upgrade-db` preserves existing records and adds only the application support
tables when missing; it does not recreate the business schema. See
[database migration and upgrade instructions](docs/database-migration.md).

For the missing label-numbering table, apply the versioned migration directly
to the PostgreSQL database already configured by `DATABASE_URL`:

```bash
python migrations/002_label_runs.py --apply
```

This creates `label_runs` and records the revision in PostgreSQL, preserving
existing shipments and numbering. It is safe to rerun and creates no local
database. Pulling the project does not execute the migration automatically.

## Features

- Shipment, client, pickup/drop preset, document, reporting, and label management.
- Private Supabase uploads for PODs, invoices, and MIS reports.
- Database-backed label numbering and shipment audit history.
- Excel import/export, PDF reports and labels, bulk document tools, and complete
  downloadable backups.
- Locally bundled interface assets; no CDN is required.

## Project structure

- [app/](app): Flask application, routes, services, templates, and static assets.
- [database/](database): PostgreSQL ER models, integrity rules, and schema tools.
- [migrations/](migrations): versioned PostgreSQL upgrades and one-time import utilities.
- [tests/](tests): automated tests; database tests require a dedicated
  PostgreSQL test database configured with `ER_TEST_DATABASE_URL`.
- [docs/database-model.md](docs/database-model.md): schema and integrity model.
- [docs/database-migration.md](docs/database-migration.md): upgrade and import guidance.

The measurement fields are named `weight` and `volume` in the application and
database. No compatibility aliases for the previous column names are supported.

## Shared label details

In shipment Add/Edit, **Box contents →** lets you choose boxes or pallets and
enter one pieces-per-package count. Label review asks for one invoice amount per
shipment. Every label uses that count, the selected shipment weight or volume,
and the same invoice amount. Box/pallet numbering still runs from **1 of N**.

Per-package quantity, weight, invoice and handling-note exceptions have been
removed. Old overrides cannot affect new labels or normal Excel exports and
are discarded when a shipment is saved or imported. Historical backup records
and already generated PDFs are preserved. No schema migration is needed for
this removal. Pull the update and restart the app.
