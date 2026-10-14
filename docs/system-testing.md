# System testing

## Versioned label-numbering migration — 14 October 2026

`tests/test_label_runs_migration.py`: **8 passed**, with two upstream
deprecation warnings. These isolated PostgreSQL checks cover read-only
preflight, table creation and PDF recovery, indexes/constraints/RLS, retained
shipment data and prior migration history, repeat application without resetting
numbering, atomic rollback, incompatible/missing-recorded-table protection,
rejection of non-PostgreSQL targets, use of the app connection, and redacted CLI
errors. Migration 002 is committed for execution against the configured
`DATABASE_URL`; it has not been applied to the user's Supabase database from
this environment.

## Missing label-numbering table — 14 October 2026

`tests/test_label_review.py`: **29 passed**, with two upstream deprecation
warnings. The regression test removes `label_runs` only in an isolated
PostgreSQL test schema, reproduces the failed PDF request, checks the actionable
upgrade message, runs the existing additive `upgrade-db` command, and verifies
preview/download recovery. Repeating the upgrade preserves assignments and
shipment records. No automatic schema creation was added to label requests.
The live database connection was refused from this environment, so the upgrade
must be run in the app's configured Codespaces terminal as described in the
[upgrade guide](database-migration.md).

## Label exception removal — 14 October 2026

Against the current PostgreSQL-only application, the five targeted modules
(`test_package_contents`, `test_label_review`, `test_label_layout`,
`test_label_names_metrics`, `test_shipment_fields_labels`) finished with
**88 passed and 1 failed**, with two upstream deprecation warnings.
The failure is in the existing complete database import path: its temporary
validation schema reports `relation "consignments" does not exist`. This is
separate from shipment saves and label generation; no migration code or live
database was changed for this release. That import check remains unresolved.

The passing checks cover shared package counts, ignored historical quantity,
weight and invoice overrides, normal Excel import/export, complete backups,
PDF text and geometry, unchanged shipment records during printing, validation,
and PostgreSQL label numbering. A numbering boundary error was corrected so
new order/invoice batches no longer skip one number; existing assignments are
preserved.

Two Chromium runners passed all seven workflow groups: `box-contents.cjs` and
`label-review.cjs`. They exercised a 100-box shipment and 100-page PDF, quantity
validation inside the modal, package type/count edits, 50 shipments selected
across pages, preview/download/reprint, and desktop/mobile/tablet light/dark
layouts. There were no uncaught JavaScript errors. Both used disposable
PostgreSQL schemas and mocked storage, not live Supabase data.

Updated examples: [PDF](b2b-label-example.pdf),
[box contents](screenshots/box-contents.png),
[mobile form](screenshots/box-contents-mobile.png), and
[label review](screenshots/label-review.png).

## Running tests

The application test suite uses an isolated PostgreSQL database and mocked
Supabase Storage. It must not point at the configured application database or
real Supabase project.

Set `ER_TEST_DATABASE_URL` to a dedicated database whose name includes `test`
(the database-model integrity tests require the loopback database
`gram_admin_er_test`). The application fixtures create unique PostgreSQL
schemas and drop only those schemas after each test. Do not use production
credentials or share the test database with a running application.

```bash
ER_TEST_DATABASE_URL=postgresql://user:password@127.0.0.1:5432/gram_admin_er_test \
  .venv/bin/python -m pytest -q
```

The test harness disables external HTTP requests and replaces the Supabase
client with an in-memory object store. Database and storage behavior can
therefore be exercised without creating persistent local business files or
contacting external services.
