# Shipment modal, cancellation and archives

The shipment modal's **Save** now writes that shipment immediately and displays
its LRN. Inline register edits now autosave after an 800 ms pause. **Save All**
is an optional immediate save or retry for pending edits. Failed saves keep the
modal open, and retries of an unchanged request reuse its persistent idempotency
key. The modal cannot close while the save request is in flight. A transport
failure after commit can be retried to recover the same number.

Cancelled shipments retain their number and history. They appear only in the
Archived shipments register, using grey rows rather than a large CANCELLED label.
The row has an accessible cancellation label and
its editable controls are disabled. Cancellation still asks for confirmation,
but no longer asks for a reason: the server records `Cancelled by administrator.`,
the verified administrator and database timestamp. Existing clients may still
send a custom reason. The existing database cancellation safeguards are retained.

Administrators can **Archive** a saved shipment and **Restore** it later. The
register defaults to **Active shipments**; choose **Archived shipments** in the
Register dropdown to see archived records. Search and paging respect this
selection. Archived shipments appear only in this register view; exports, dashboard/MIS totals, labels, LR generation and the
document library always exclude both archived and cancelled shipments. Unsaved
inline edits must be saved
before archiving.

## Storage and integrity

Archives use a separate `shipment_archives` table in the existing PostgreSQL
(Supabase) database. It stores a server-generated snapshot, shipment ID, actor and
archive timestamp. Canonical shipment identity rows are retained to preserve
foreign keys, LR counters, audit history and documents for restoration. The shared
shipment query excludes them by default; only the consignments archive list explicitly requests archived rows. Direct document viewing and
downloads are unavailable until restoration; archiving does not reset,
reassign or release an LRN. Snapshot data includes canonical fields, dashboard
extras and document references; stored document bytes remain in their existing
private Supabase buckets.

Archived shipments are read-only. Non-cancelled archived shipments can be
restored before editing or changing documents. Cancellation is permanent: a
cancelled shipment stays in the Archived register and cannot be restored to
Active. This visibility rule also covers cancellations without an archive
snapshot; no existing records need to be rewritten. Restoring removes the current
archive marker/snapshot, while
an immutable generic audit event retains the previous snapshot. Archive and
restore are atomic, administrator-only, session/CSRF-protected and safe to retry.
Database triggers independently enforce actor permissions, snapshot immutability
and protection against canonical shipment updates while archived. Normal browser
and Supabase Data API roles receive no archive write permissions.

## Migration and release

Migration **006** is required before starting this version. Migration 005 and all
previous migrations remain unchanged. No hosted database was changed by preparing
these files.

For a configured backend connection, run the read-only preflight first:

```sh
.venv/bin/python migrations/006_shipment_archives.py
```

After reviewing the target database and the migration:

```sh
.venv/bin/python migrations/006_shipment_archives.py --apply
.venv/bin/python -m flask --app wsgi:app check-db
```

For Supabase SQL Editor, run the **entire** contents of
`migrations/sql/006_shipment_archives_supabase.sql`. It targets `public`, pins
function schemas, runs transactionally, records the same checksum/history as the
Python runner, and refuses unrecorded partial archive tables rather than
recreating or deleting them. The raw `006_shipment_archives.sql` remains a Python
runner template and must not be pasted directly into SQL Editor.

### Existing archive table without a migration-history entry

If the normal SQL Editor file reports `Unrecorded archive table exists`, do not
remove the table or manually insert a ledger row. Inspect its schema first. For
a table with exactly migration 006's four columns and PK/FK/check constraints,
use `migrations/sql/006_shipment_archives_recover_supabase.sql` after review.
Run the entire recovery file in SQL Editor; it targets `public`.

Recovery validates column types/nullability/defaults, constraints, existing
function bodies, triggers, index and RLS policies. It refuses conflicting
objects. It installs missing known controls, fixes the pinned schema on matching
functions, enables protection triggers/RLS, revokes browser-role write access,
and records the original migration checksum only after validation. It never
drops/recreates the table or changes shipment/archive rows, IDs or timestamps.
Existing snapshots are retained. A conflicting recorded checksum also stops
recovery. A successful recovery makes the normal 006 runner a safe no-op.

Regenerate this reviewed recovery export without database access:

```sh
.venv/bin/python migrations/recover_006_shipment_archives.py --output migrations/sql/006_shipment_archives_recover_supabase.sql
```

Seven PostgreSQL regression tests passed: existing-record preservation,
restoration after recovery, table-only completion, wrong-schema repair, safe
replay, deterministic export, and rejection of incompatible columns, functions,
constraints or policies. These tests used disposable schemas; no recovery was
executed against the hosted Supabase database.

The archive migration can be exported again without database access:

```sh
.venv/bin/python migrations/006_shipment_archives.py --export-sql migrations/sql/006_shipment_archives_supabase.sql
```

Restart the app after the migration and code update. This migration adds archive
storage and triggers; it does not archive, delete, renumber or copy existing
shipments automatically. Normal archiving occurs only when an administrator
chooses Archive and confirms.

Rollback: first restore all archived shipments, then review and execute
`migrations/sql/006_shipment_archives_rollback.sql`. It refuses to drop a nonempty
archive table. It removes only 006's table, triggers, functions and ledger entry;
005's identity/cancellation controls and generic archive/restore audit events are
preserved. Roll back 006 before considering a downgrade of 005.

## Validation

Targeted PostgreSQL tests exercise archive defaults/filters, snapshot contents,
restore/counter preservation, read-only enforcement, administrator/CSRF checks,
non-prompted cancellation, SQL Editor/Python migration compatibility and guarded
rollback. The Chromium scenario checks immediate modal issuance, recovery from a
lost save response, grey cancellation with no reason prompt, archive/restore
filtering and the existing confirmed CSV import workflow. All database/browser
checks use disposable local PostgreSQL test schemas and mocked Supabase HTTP
boundaries, not hosted application data.

Validation result: 48 focused Python/PostgreSQL tests passed; both JavaScript unit
runners passed. The Chromium scenario passed, including retry after a committed
save response was lost, with no uncaught JavaScript errors. Python compilation,
JavaScript syntax and whitespace checks also passed. The full legacy suite was
not rerun for these targeted changes; its previously documented failures remain.

## Inline autosave

Valid edits to existing active shipments are saved automatically after a short
pause. A save indicator reports unsaved changes, saving, saved, validation errors
or transport failures. There is no table reload after inline saves, so focus and
new edits are retained. Requests are serialized and capped at 100 rows per batch;
only the exact acknowledged row version is cleared from pending state. Changes
made during a request are saved in a subsequent batch. Invalid fields stay
pending until corrected. Failed requests retain edits without an endless retry
loop; Save All retries an unchanged payload with its existing idempotency key.

Autosave pauses while the shipment modal is open. Modal Save waits for any
already-running inline request, then persists its own shipment directly. Closing
the modal resumes pending inline saves. The browser warns before leaving while
there are pending edits or an in-flight save. No backend endpoint, database schema
or migration changes are needed for this autosave update.

Validation: seven queue unit tests cover coalescing/manual flush, in-flight edits,
version acknowledgements, failed retries, invalid values, modal pause and bounded
batches. The Chromium scenario verifies immediate modal saving, actual database
updates from inline edits, focus retention, newer edits during a delayed response,
invalid input protection and manual retry with the same request key.
