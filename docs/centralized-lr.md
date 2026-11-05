# Centralized LR issuance — implementation and rollout

The newer [shipment form and archive update](shipment-archives.md) supersedes the
original modal staging and mandatory cancellation-reason UI described here.
Migration 006 adds archive storage; cancellation auditing remains enforced.

## Scope and architecture

This is an extension to the existing Flask/PostgreSQL/Supabase admin panel. No
new service, dependency, production database, or fallback storage is introduced.
The branch is `feature_lr_number_generator`.

The canonical number remains `consignments.identifier_value` (the existing
`consignment_number` adapter property). It is generated as registered uppercase
location code + Indian FY + six-digit sequence, for example `HQ2627000001`.
Existing AWB/Order ID/LRN values remain exactly as stored. Pickup/drop customer
presets remain separate from the new issuing-location master.

Migration 005 supplies PostgreSQL triggers. An issuance locks the location row
until commit, captures `clock_timestamp()` after the lock, computes FY in
`Asia/Kolkata`, and atomically increments `(location_id,financial_year)`. The
shipment, counter and LR audit insert share the same transaction. The unique
identifier and allocation constraints provide collision protection; exhaustion
at 999999 rejects issuance. A failed insert or failed audit rolls everything
back. Successfully issued and cancelled numbers cannot be recycled. Location
locking deliberately serializes issuance at a location even across FY rollover.
No counter is based on an active-shipment count or the shipment primary key.

The existing generic audit log continues to record changes to document metadata
and adapter extras. Both the LR-specific audit log and generic audit log become
append-only. New and historical identifiers are immutable; historical rows are
marked `legacy`, without inventing an issuance timestamp, location or FY. New
shipments receive database-owned `created_at` and `lr_issued_at` timestamps.
Cancellation retains identity, documents, timestamp, user and reason; cancelled
shipments are read-only and cannot generate replacement LRs or labels.

## Application interfaces

- `/admin/issuing-locations`: Location Master; `/options` supplies registered
  locations. Administrators can add, rename and activate/deactivate locations.
  Codes use 2–10 ASCII letters, normalized to uppercase, and are frozen at
  the administrator save (which is stricter than freezing at first issuance).
- `/admin/consignments/save`: accepts 1–100 rows and a persistent UUID
  `idempotency_key`. New rows require `location_id` and `status`, and must not
  supply an identifier. The same actor/key/payload replays the committed response;
  reusing a key with a different payload is rejected. Responses list generated
  numbers. Existing identifiers, issuing locations, FYs and timestamps cannot
  be changed. `deleted_ids` is rejected; no normal hard-delete endpoint remains.
- `/admin/consignments/<uuid>/cancel`: administrator-only, requires
  `{"confirmed":true,"reason":"..."}`. Repeating cancellation retains the first
  reason/user/time. `/lr-history` supplies the immutable audit history.
- `/admin/consignments/import`: Excel/CSV upload creates a persisted preview.
  The import template contains `location_code`, not an identifier or identifier
  type. Supply a code per row, or `location_code` for the whole upload. Headers
  are normalized and reserved LR/CN/GR aliases are rejected. Unknown columns
  are rejected as well, preventing unrecognized identifier mappings.
- `/admin/consignments/import/confirm`: accepts the preview `batch_id`; it never
  trusts replacement client rows. It revalidates locations and shipment fields,
  then calls the same issuance mechanism. The entire upload (maximum 100 rows)
  is one all-or-nothing batch. Allocation is ordered by location then original
  row index; results retain original row order. Preview allocates nothing.
  Reconfirming a batch returns its original result. Identical normalized uploads
  by the same user converge on the same batch, including after completion.
- Shipment register adds location/FY filters, retained numbers with `CANCELLED`
  markers, and an audit-history viewer. Modal and register identifiers are
  read-only. Save buttons prevent overlapping submissions, and request keys are
  retained in session storage for retries of the same payload.
- LR PDF generation validates that the receipt number equals the selected saved
  shipment number. The legal artwork/content is unchanged. Previously stored
  documents remain available through their existing retrieval routes.

New write interfaces and shipment saves require a session CSRF token in
`X-CSRF-Token` (or `csrf_token` for forms), in addition to existing authentication
and same-origin checks. Operators and administrators can issue; only active
administrators can manage locations or cancel. Signup still creates an
`operator` profile; no user metadata can grant administrative rights. Review
and assign initial administrator profiles through the existing trusted database
administration process, not through a public signup field.

Example API payload (send with the existing authenticated session and CSRF header):

```json
{
  "idempotency_key": "d3ae6c9b-e337-4ca5-b693-2db7915ce905",
  "rows": [{"location_id": "<registered-location-uuid>", "status": "In Transit", "pieces": 2}]
}
```

Clients must retain their request key until the outcome is known. Retry the
identical payload/key after a transport timeout; do not create a new key for an
uncertain request. This deliberately changes the old manual-number save/import
contracts. External clients and automation must migrate before enabling writes.

## Automatic location readiness and historical counters

Saving a location as an active administrator is sufficient authorization. There
is no separate review status, approval button or required CLI manifest. New
locations are immediately usable. Locations left pending by an older release
initialize automatically on their next administrator save or first normal use.

Initialization holds the location row lock and derives opening floors from exact
matching historical LR numbers for that code and each FY. Cancelled shipments
are included. Only recognized location + FY + six-digit numbers are used; other
historical formats are left unchanged. An unused location starts at 000001.
Initialization never reserves a new number. Once initialized, existing counters
and metadata are preserved; subsequent saves do not reset or reseed them. Normal
issuance still increments the database counter atomically and unique constraints
still reject collisions. Existing inactive locations cannot be used for issuance.

The migration 005 compatibility fields named `reconciliation_digest`,
`reconciliation_reason` and `opening_counters` are initialized automatically. They
are implementation bookkeeping, not an approval workflow. This application
change requires **no additional database migration** and does not change 005's
checksum, tables or triggers. Do not rerun or edit an applied migration for it.
The older report/approval CLI remains available for compatibility with existing
administrative tooling, but is not required by the UI or normal issuance.

Only history present in the configured database can establish automatic floors.
Records held exclusively outside this system are not imported or guessed. No
historical identifier, cancellation or stored document is modified by readiness
initialization.

## Migrations, security review and staging

Files: `migrations/005_centralized_lr.py`,
`migrations/sql/005_centralized_lr.sql`, and
`migrations/sql/005_centralized_lr_rollback.sql`.

Migration 005 is additive to business records. It records its checksum and
preserved shipment count in the existing migration ledger. It does not edit
historical migrations. The Python command defaults to a read-only preflight;
SQL uses the connection's existing schema and no hard-coded Supabase endpoint.
Do not execute the SQL template directly: the runner safely substitutes its
`__SCHEMA__` token and records migration history in the same transaction.

### Supabase SQL Editor: migration 005 schema error

The raw `005_centralized_lr.sql` is a runner template. Pasting it directly into
SQL Editor leaves `SET search_path = __SCHEMA__, pg_temp` unresolved. Compiling
`gram_lr_identity_guard` then cannot resolve `issuing_locations%ROWTYPE`, even
though the template created the table earlier in the request.

For SQL Editor use **`migrations/sql/005_centralized_lr_supabase.sql`**, selecting
and running its entire contents. It targets `public`, wraps DDL and migration
history in one transaction, pins function schemas, and retains the original
005 checksum. Existing 005 migrations are not rewritten. A successful replay
of an already recorded matching migration is a no-op.

To regenerate the export (no database connection or credentials needed):

```sh
.venv/bin/python migrations/export_lr_sql.py --schema public --output migrations/sql/005_centralized_lr_supabase.sql
```

For another deployment schema, export using that actual schema name. Before
retrying a failed manual migration, this read-only query shows whether the table
survived in the usual Supabase schema:

```sql
SELECT current_schema(), to_regclass('public.issuing_locations');
```

If unrecorded LR tables remain, the exported script stops with an explicit
partial-migration message. Do not drop tables, counters, shipments or ledger
rows to force a retry; inspect the existing schema and review a recovery first.
This export installs the database controls. The current application initializes locations automatically when administrators save them or when an older pending location is first used.

Validation: `tests/test_lr_sql_editor.py` exercises the original failure and
transaction rollback, exported creation and actual issuance, shared Python/SQL
migration history, safe replay, preservation of partial state and deterministic
export generation on disposable PostgreSQL. No hosted migration was applied.


Before applying, inspect the actual target's RLS, policies and inherited grants:

```sql
SELECT tablename, policyname, roles, cmd, qual, with_check
FROM pg_policies WHERE schemaname = current_schema()
AND tablename IN ('consignments','admin_users','audit_logs',
 'issuing_locations','lr_sequences','lr_audit_log','lr_requests','lr_imports');
SELECT table_name, grantee, privilege_type FROM information_schema.role_table_grants
WHERE table_schema = current_schema()
AND table_name IN ('consignments','audit_logs','lr_sequences','lr_audit_log');
```

New control tables have RLS enabled with no browser/Data API write policies.
Privileges are revoked from PUBLIC and, where present, Supabase `anon`,
`authenticated` and `service_role`; those roles also lose shipment mutation
privileges. There are no new security-definer RPCs callable by browser roles.
Trigger functions pin their search path and lose PUBLIC execution privileges.
Counters/audit records reject direct changes; TRUNCATE is explicitly blocked.
Existing audit logs reject UPDATE/DELETE/TRUNCATE.

Use a trusted, least-privileged backend DB login, not an exposed Data API key.
Grant it only required table operations and schema USAGE after reviewing the
existing deployment's role model. SECURITY DEFINER triggers must remain owned
by a trusted migration role. End users must not receive DB credentials, schema
ownership, DDL rights, or the ability to disable triggers. PostgreSQL owners and
superusers can override database safeguards: those are trusted administrators,
not ordinary application identities. The backend sets transaction-local
`gram.actor_id` from verified Supabase identity; never accept it from a request.
Direct database automation must use a trusted actor and the issuance contract.
The existing Storage service key remains server-side.

Stage with a dedicated PostgreSQL/Supabase project and reviewed access:

```sh
.venv/bin/python migrations/005_centralized_lr.py
# After inspecting and approving that preflight in staging:
.venv/bin/python migrations/005_centralized_lr.py --apply
.venv/bin/python -m flask --app wsgi:app check-db
.venv/bin/python -m flask --app wsgi:app check-auth
.venv/bin/python -m flask --app wsgi:app check-storage
```

Confirm administrator assignments and save a location, then test
manual creation, concurrent retries, imports, cancellation, existing documents,
backups and PDF printing with actual staging credentials. Stop old clients from
writing during the database/app rollout; the old manual-number code will be
rejected by the new database triggers. Deploy the migration before activating
new app code, since the new model queries require the new columns.

For production, repeat the backup, role/RLS review, maintenance window,
read-only preflight, migration approval, migration, code release, location creation,
and readiness checks. Do not run remote migrations or enable production writes
solely because local tests passed. No production deployment or Supabase migration
has been performed as part of this implementation.

## Rollback

Before any LR state or approved counter exists, the guarded rollback SQL can
remove migration 005. It refuses to proceed if there is issuance, cancellation,
any audit/request/import state, or reconciliation/counter state. It preserves
historical shipments. Capture the original shipment ACL from
`admin_migration_runs.source_schema.original_consignments_acl` **before** rollback;
the rollback deliberately does not broaden grants automatically. Have the DBA
restore only reviewed original grants, then deploy the compatible previous app.

After issuance or reconciliation, **do not downgrade, reset counters, drop
control tables, restore an older snapshot over newer LRs, or restore hard-delete
code**. Pause new issuance and use a reviewed forward correction, preserving all
identities and audit history. Restoring backups requires reconciliation against
all subsequent/external issued numbers before re-enabling issuance.

## Validation and limitations

See the final validation section below. Tests use the repository's existing
disposable PostgreSQL schemas and mocked Supabase HTTP/Storage boundaries. This
is test isolation, not a replacement for the application's Supabase database.
There are no local application database files.

Known limits: uploads are capped at 100 shipments and one atomic batch; repeated
identical imports by the same actor deliberately replay rather than creating a
second identical set. To create another identical set, use individually keyed
manual issuances. Existing timestamps are not reconstructed. PDF legal content
and legal compliance have not been reviewed. Real Supabase grants/RLS, historical
registers, administrator roles, buckets and live connectivity need staging
verification. Existing database-import/restore utilities are not an approved
way to overwrite an LR-enabled database; retain raw SQL/document backups and
use a reviewed restoration plan. End-to-end production readiness is not claimed
while the legacy regression suite has unresolved failures.

## Validation record — 4 November 2026

| Check | Observed result |
| --- | --- |
| LR issuance, security, migration/rollback and PDF tests | 65 passed |
| Existing JavaScript unit runners | 2 passed |
| Chromium shipment and CSV-import scenario | Passed; no uncaught JavaScript errors |
| Python compilation, JavaScript syntax and whitespace checks | Passed |
| Unchanged HEAD baseline full Python suite | 336 passed, 48 failed, 157 setup errors |
| Last full Python suite with LR changes | 255 passed, 162 failed, 157 setup errors |
| Live Supabase migration, historical reconciliation, production rollout | Not performed |

Commands used: `python -m pytest -q tests/test_lr_issuance.py
tests/test_lr_generator.py --tb=short`, `node --test tests/unit/*.cjs`,
`node tests/browser/lr-issuance.cjs`, and `python -m pytest -q --tb=line`.
All database checks used the dedicated loopback test database and per-test
schemas. Chromium used `tests/browser/serve.py`, with mocked Supabase boundaries.
The unchanged baseline was tested from a temporary archive of HEAD, using the
same interpreter and test database; no worktree or production database was used.

The 157 setup errors also occur in the baseline, chiefly from the existing
`extra_operator_notes` migration fixture. Baseline failures include legacy table
references, storage mocks and historical import issues. Additional failures in
the changed suite predominantly come from old helpers that still POST manually
assigned identifiers without CSRF/idempotency/location fields, and tests that
expect editable identifiers, permanent deletion or pre-005 schema inventories.
Those expectations conflict with this feature's required contract; the old
suites have **not** been silently disabled or changed to pass. The LR-focused
suite verifies the replacement contracts, but migration of the old suites and
resolution of existing fixture failures remain release blockers. Do not report
the full test suite as passing or this branch as production-ready.

The last full run preceded the final constraint NULL-check hardening,
initial-filter loading fix and additional XLSX/malformed-request checks; the affected LR-focused checks were rerun afterward.

## Complete modified/new file inventory

- `README.md`
- `app/__init__.py`
- `app/admin/__init__.py`
- `app/admin/bulk_documents.py`
- `app/admin/consignment_controller.py`
- `app/admin/documents.py`
- `app/admin/labels.py`
- `app/admin/lr.py`
- `app/admin/lr_control.py`
- `app/admin/lr_reconciliation.py`
- `app/admin/reporting.py`
- `app/lr_issuance.py`
- `app/orm.py`
- `app/static/js/admin/api.js`
- `app/static/js/admin/lr-control.js`
- `app/static/js/admin/lr-generator.js`
- `app/static/js/admin/shipment-filters.js`
- `app/static/js/admin/validation.js`
- `app/static/js/consignments.js`
- `app/templates/admin/_shipment_filters.html`
- `app/templates/admin/base.html`
- `app/templates/admin/consignments.html`
- `app/templates/admin/consignments/modals.html`
- `app/templates/admin/issuing_locations.html`
- `app/templates/admin/lr_import.html`
- `database/lr.py`
- `database/models.py`
- `docs/centralized-lr.md`
- `migrations/005_centralized_lr.py`
- `migrations/sql/005_centralized_lr.sql`
- `migrations/sql/005_centralized_lr_rollback.sql`
- `tests/browser/lr-issuance.cjs`
- `tests/browser/serve.py`
- `tests/test_lr_generator.py`
- `tests/test_lr_issuance.py`

## Administrator-created location readiness update

Location Master no longer displays a review/counter approval column, and shipment
location choices no longer display an awaiting-review label. Administrator saves
initialize location bookkeeping automatically. Existing pending locations are
handled on use, without SQL updates or a CLI approval step. Tests cover immediate
issuance, old pending records, preservation across renames, cancelled historical
counter floors and concurrent first use. No remote configuration or database
changes are performed by installing this code update.

Validation for this update: 42 targeted PostgreSQL/Python tests passed, including
new/pending location readiness, historical cancellation floors, concurrency and
existing issuance/SQL-export checks. Both JavaScript unit runners and syntax/
whitespace checks passed. Live Supabase state was not changed during validation.
