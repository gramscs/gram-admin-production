# System testing — 11 October 2026

The latest full-suite run passed **490 tests**, with **10 backend-specific skips**
and **no failures**. Browser workflows also passed after correcting one saving
bug found during this review. These results validate the tested application;
live Supabase storage and the team's running Codespaces app still need an
environment-specific check. The shipment filters, Documents and dashboard follow-ups below
add coverage to the original 359-test baseline.

## Environment and isolation

- Python 3.12, pytest 9.1.1, SQLite and PostgreSQL 16.
- PostgreSQL used a disposable Docker database, `gram_admin_er_test`, on loopback
  port 55432. Fixtures created and removed separate schemas.
- Chromium 151 ran real browser interactions and downloads.
- Browser tests used a newly migrated ER database with UUID relationships,
  60 synthetic shipments, client data, archived enquiry/subscriber data and an
  unlinked upload. The browser then created its own shipment and documents.
- Test databases and uploads were in temporary directories. No live database
  or business uploads were used. Supabase storage routing used mock clients.

## Coverage and results

| Area | Verified | Result |
| --- | --- | --- |
| Login and access | Anonymous redirects, wrong password, login/logout, inactive identities, viewer write restrictions, cross-origin mutations | Passed |
| Companies | Create/edit, pickup/drop defaults, client selection, validation, archive behavior, preservation of shipment address snapshots | Passed |
| Consignments | Add modal, Save All, inline edits, search, pagination, identifier types, pieces, weight/volume choice and preservation | Passed |
| Documents | Image/PDF uploads and private downloads, previews, replacement, remove/undo, filename/content validation, size limits and PDF safety validation | Passed |
| Bulk documents | Match/review/apply, POD/invoice filenames, ZIP manifest, ambiguous names, all-or-nothing validation and failure cleanup | Passed |
| Storage routing | Separate `pod-uploads` and `invoice-uploads` buckets using mock storage; actual local upload/download round trips | Passed |
| Labels | Selected shipments, one label per piece, barcode switch, 4×6-inch PDF dimensions, reachable print controls | Passed |
| Dashboard | KPI counts, charts, filters, presentation mode and exit, dark/light theme persistence | Passed |
| MIS | Saved views, PDF/Excel/CSV, report history/rename/delete, immutable snapshots and filters | Passed |
| PDFs | Browser downloads, nonempty text on every downloaded page, shipment identifiers, rendered management report and label inspection | Passed |
| Complete backup | ZIP integrity, all 14 tables present in the ER test instance, archived data, documents, MIS files, unlinked upload and complete document manifest | Passed |
| Database/migration | Legacy and ER runtime behavior, fresh imports, all source tables, relationships, constraints, history, rollback, reconciliation, PostgreSQL RLS and locking | Passed |
| Startup/persistence | Real CLI `check-db → upgrade-db → check-db`; a new app process retained 61 shipments and could read both document types | Passed |
| Responsive UI | Dashboard, companies, consignments, MIS and labels at 390, 768 and 1280 px; no page overflow; actual label download at 390 px | Passed |
| Browser errors | No uncaught JavaScript errors or unexpected HTTP 5xx responses in the main browser run | Passed |

The main browser run covered 13 workflow groups plus error monitoring. A second
mobile run covered navigation, document download/preview, removal/undo, invalid
image rejection, measurement preservation, invoice replacement and actual label
download. A dedicated regression additionally exercised two deterministic
refresh-while-editing scenarios.

### Bug found and fixed

When a background table refresh replaced a row while its edit modal was open,
the modal could correctly stage an invoice or other change, but **Save All could
submit the older visible row instead**. The request returned success without
saving the newly selected file.

Save All now gives staged edits priority over older visible rows. The browser
regression forces the original row to become detached while the modal remains
open, then checks the outgoing payload and saved values. It verifies fields,
both document uploads and both removals. This regression failed against the
previous JavaScript and passed with the fix.

### PDF evidence

| Download | Pages | Content check |
| --- | ---: | --- |
| Filtered consignment register | 3 | Shipment appears; every page contains text |
| B2B labels without barcode | 3 | Three pieces, each page 288×432 PDF points (4×6 inches) |
| Saved MIS PDF | 13 | All pages contain text; shipment register included |
| Dashboard management PDF | 13 | All pages contain text; shipment register included |

Downloaded Excel and document ZIP contents were also checked. The document ZIP
contained `BROWSER-SYSTEM_pod.pdf`, `BROWSER-SYSTEM_invoice.png` and its manifest.
PDF page counts depend on the test data and selected columns.

## Repeat the checks

From the repository root, with the project virtual environment active:

```bash
DATABASE_URL=sqlite:////tmp/gram-system-suite.db python -m pytest -q -ra
```

For PostgreSQL coverage, set `ER_TEST_DATABASE_URL` to a **disposable loopback
PostgreSQL database named `gram_admin_er_test`** before running the same command.
The test fixtures reject other hosts/database names. Without it, PostgreSQL
variants are not executed; do not interpret that smaller run as PostgreSQL
validation. The latest run with PostgreSQL enabled took approximately 106 seconds.
The 10 skips concern checks specific to the other database backend, rather than
unconfigured live services. One ReportLab dependency deprecation warning remains.

For the browser regression, start the isolated app in one terminal:

```bash
python tests/browser/serve.py
```

This helper builds a fresh temporary ER database, disables remote storage, binds
only to loopback and removes its temporary data on normal shutdown. Its test
login is `admin` / `system-test-only`; this is a fixture credential.

With Node.js, Playwright and Chromium available, use another terminal:

```bash
SYSTEM_TEST_URL=http://127.0.0.1:10003 \
SYSTEM_TEST_PASSWORD=system-test-only \
node tests/browser/consignment-staging.cjs
```

If Playwright or Chromium is installed outside the checkout, set
`PLAYWRIGHT_MODULE` to the Playwright module path and `CHROMIUM_PATH` to the
Chromium executable. The runner only accepts a loopback app; it creates and
deletes a synthetic shipment. Run it against the disposable helper, not your
operational app. Stop the helper with Ctrl+C when finished.

## Checks still needed in the actual operating environment

`SUPABASE_URL` and `SUPABASE_KEY` were absent in this test environment, so live
bucket existence, permissions and network access were not verified. In the
team's app, upload one test POD and one test invoice, then download both and
confirm their objects are in the intended private buckets. Delete the test
shipment afterward according to your document-retention practice.

The team's running Codespaces instance, Safari/Firefox, physical label printing
and concurrent-user/load testing were not exercised. These results do not
establish those behaviors or a production security assessment.

## Shipment filters and Documents follow-up

The navigation now says **Shipments** and adds **Documents**. Company and
identifier-type filters combine with search on both pages and carry through to
shipment Excel/PDF exports and filtered document ZIPs. Document details are read
from shipment records; there is no separate copy or additional database table.

The new integration file exercises six scenarios across legacy SQLite and the
migrated SQLite/PostgreSQL models, plus page authentication: **20 checks passed**.
The complete final suite passed **379 tests**, with the same 10 backend-specific
skips and no failures. The old dashboard navigation assertion was updated to the
new Shipments URL; the old `/admin/consignments` page remains tested and available.

`tests/browser/shipment-documents.cjs` passed seven browser workflow groups:

1. Shipments navigation, combined filters, pagination and filter persistence.
2. Staged edits survive filtering a row out; Save All keeps the filtered view.
3. Linked document details, missing indicators and review/apply bulk uploads.
4. Filtered document ZIP requests carry company, identifier type and search.
5. Document selections remain selected across pages.
6. Changing a shipment identifier type updates Documents and its filter results.
7. Document layout, bulk buttons and reset at phone/tablet/desktop widths.

Downloaded filtered Excel/PDF files contained only the matching LRN shipment;
the document ZIP contained exactly the matching shipment's POD and invoice, with
the expected identifier type in its manifest. The POD PDF and invoice image
downloaded correctly. No browser JavaScript errors or unexpected server 5xx
responses occurred. The earlier refresh-while-editing regression also passed.

Using the same isolated browser server and Playwright configuration above:

```bash
SYSTEM_TEST_URL=http://127.0.0.1:10003 \
SYSTEM_TEST_PASSWORD=system-test-only \
node tests/browser/shipment-documents.cjs
```

Optional `SYSTEM_TEST_ARTIFACTS_DIR` chooses where this runner saves its test
downloads, screenshot and result JSON. Its default is a temporary directory.


## Dashboard missing-document follow-up

The 382-test run passed against disposable SQLite and PostgreSQL databases
(10 backend-specific skips; no failures). New integration scenarios verify that
missing PODs include shipments in transit, fully documented shipments disappear,
and missing POD/invoice/both counts agree. Company, identifier type, search,
custom pickup dates, status and unassigned-company scope all apply to the list.
Original legacy records and migrated ER records are covered. The separate
Delivered without POD management metric retains its existing meaning.

Four Chromium workflow groups passed on the disposable migrated ER app:

1. Keyboard expansion, missing-category counts and filters, identifier types,
   25-row pagination, and resetting the page when changing category.
2. Opening Documents targets the original shipment, company and identifier type.
3. Custom date/status scope and empty-category messaging.
4. Light/dark theme and usable controls without page overflow at widths 390,
   768 and 1440 pixels. The list scrolls inside the panel to keep it compact.

No browser JavaScript errors or unexpected server 5xx responses occurred.
Screenshots were saved outside the repository. The fixture used synthetic
shipments and local uploads; operational data and Supabase storage were untouched.

Using the same isolated browser server and Playwright configuration above:

```bash
SYSTEM_TEST_URL=http://127.0.0.1:10003 \
SYSTEM_TEST_PASSWORD=system-test-only \
node tests/browser/dashboard-documents.cjs
```


## Individual document management and shipment eye

The final full suite passed **385 tests**, with **10 backend-specific skips**,
no failures and one existing ReportLab deprecation warning. The seven existing
shipment/document browser workflows and both refresh-while-editing regressions
also passed. Downloaded shipment PDFs/Excel, document ZIPs and the editor's current
PDF were checked for their expected contents.

New scenarios exercise the individual POD/invoice endpoint on legacy SQLite,
migrated ER SQLite and migrated ER PostgreSQL. Upload, replacement, invalid
content rejection, protected image preview, stale-reference conflicts and removal
are covered. Shipment fields remain unchanged. Exact shipment filtering prevents
similarly prefixed identifiers from appearing alongside the selected shipment,
and applies to its Excel export. Viewer accounts cannot upload or remove files.

Six new Chromium workflow groups passed on the disposable migrated ER app:

1. Upload a PDF POD and image invoice through the Documents editor.
2. Reject invalid file types; Cancel preserves existing files; replacement changes
   only the selected document and refreshes its displayed filename.
3. Stage removal, undo it, save removal, and upload the missing invoice again.
4. Reject an editor's stale removal after another request replaced the document.
5. Download the current PDF and use light/dark modal controls at widths 390,
   768 and 1440 pixels, with reachable Save buttons and no page overflow.
6. Use the icon-only eye to open exactly one shipment despite overlapping numbers;
   refresh retains that scope and changed/cleared filters release it.

No browser JavaScript errors or unexpected server 5xx responses occurred.
The generated documents, screenshots and test data stayed outside the repository.
No database migration or new dependency is required.

Using the same isolated browser server and Playwright configuration above:

```bash
SYSTEM_TEST_URL=http://127.0.0.1:10003 \
SYSTEM_TEST_PASSWORD=system-test-only \
node tests/browser/document-editor.cjs
```


## Storage destination diagnostics

The full-suite run passed **392 tests** with **10 backend-specific skips**, no
failures and two dependency deprecation warnings. After adding backup failure
handling, all **21 storage/backup tests** passed, including the additional
regression preserving local backup files when remote initialization fails.
The seven existing shipment/document browser workflows also passed.

Eight focused tests cover incomplete credentials, a missing SDK, a failed client
initialization, intentional local development, and read-only checks of both
configured Supabase buckets. Configured-but-broken remote storage now rejects
uploads and preserves existing files instead of silently saving on local disk.
The check-storage command lists at most one entry per consignments folder, never
uploads or removes objects, and suppresses exception details that could contain
secrets. A passing read check does not establish write permissions.

The real CLI was run with explicit blank Storage settings and an isolated SQLite
configuration, reporting local storage without changing files or database data.
The document-library browser regression also checks the Saved locally labels.
Remote bucket permissions and uploads remain dependent on credentials in the
user's running app; no live Supabase Storage check was claimed from mock tests.

```bash
AUTO_CREATE_TABLES=false python -m flask --app wsgi:app check-storage
```

## Explicit document deletion and storage synchronization

The full-suite run passed **490 tests**, with **10 skips**, no failures and two
dependency deprecation warnings, using disposable SQLite/PostgreSQL databases.
After adding checksum-verified restoration and private recovery copies, the
focused document-deletion suite was rerun against the final implementation:
**97 tests passed**, with no skips or failures. All **25 restoration/object-error
checks** also passed during the recovery safeguard review.

The new deletion tests cover removal from Documents and Shipments Save All,
whole-shipment deletion, POD/invoice routing, all selected ER versions and File
metadata, validation rollback, shared shipment/report files, missing objects,
and safe provider error messages. Mock Storage tests simulate a thrown delete
error and a silently denied DELETE that leaves the exact object readable; both
return failure and retain database attachments. A later delete/commit failure
restores previously deleted bytes. Irrecoverable restoration reports failure,
keeps a private local recovery copy and includes that copy in a complete backup.
Object-not-found checks distinguish missing objects from bucket/permission errors.

The repair command was tested with an isolated `DEMO0017` fixture reproducing the
previous ER soft-removal behavior. Dry run changes nothing; apply deletes only
that removed document kind's versions and unshared files. Current attachments
are refused, other shipments/invoices remain, failed cleanup preserves metadata,
and repeating cleanup is safe. These tests did not access the user's live bucket.

All **15 browser workflow groups** passed: six document-editor workflows, two
shipment-staging regressions and seven shipment/document-library workflows.
They covered uploads, replacements, removal/undo, stale edits, filter changes,
downloads, bulk uploads, exact shipment links and responsive light/dark layouts.

```bash
python -m pytest -q tests/test_document_deletion.py
AUTO_CREATE_TABLES=false python -m flask --app wsgi:app purge-removed-documents --identifier DEMO0017 --kind pod
# Run with --apply only when the preview names the intended removed attachment.
AUTO_CREATE_TABLES=false python -m flask --app wsgi:app purge-removed-documents --identifier DEMO0017 --kind pod --apply
```

No schema migration or new dependency is required. Pull the update and restart
the running app. Actual Supabase deletion still requires that app's private
Storage credentials and permissions; no live object cleanup was claimed here.

## Invoice storage follow-up

Invoice uploads already use the same private document service and synchronized
deletion logic as PODs. No separate invoice implementation was introduced.
The follow-up extends failure and rollback checks to invoices and verifies the
default/configurable invoice bucket through upload, private download, replacement
and removal, including a bucket-setting change after upload. With configured
mock Supabase storage, no new invoice file appears in `instance/uploads`.
Both remote invoice versions and their unshared metadata are removed, while the
POD remains attached. Incomplete credentials reject invoice uploads and preserve
previous attachments instead of creating new local files.

The focused run passed **210 tests**, with no skips/failures and two dependency
deprecation warnings. It includes document deletion, storage diagnostics, bulk
routing and validation against disposable SQLite and PostgreSQL databases.
All six document-editor browser workflows also passed, including invoice image
upload, Remove/Undo, saved invoice removal and PDF re-upload.

The actual `check-storage` CLI was run with a disposable database URI and table
creation disabled. Both Supabase credentials were blank in this cloud runtime,
so it correctly reported local storage without changing database records/files.
The cloud environment draft now includes the confirmed project base URL, both
bucket names, a direct `SUPABASE_KEY` requirement and the project's Storage
hostname alongside the existing database hostname. Saving a draft does not apply
credentials or publish it. The server key must be entered securely; actual live
Supabase reads/uploads/deletes remain unverified until it is available. Codespaces
uses its own `.env` and requires a restart after configuration changes.
