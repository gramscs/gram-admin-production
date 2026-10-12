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

## Reviewed GRAM B2B labels

The full suite passed **568 tests**, with **10 expected skips**, no failures and
two dependency deprecation warnings against disposable SQLite/PostgreSQL
databases. A subsequently added process-restart/corrupt-state test also passed
separately. No live database or Supabase bucket was used for these checks.

Label checks cover authoritative pickup dates, saved addresses/measurement
totals, the embedded GRAM logo, required invoice amounts, all three payment
modes, goods descriptions, conditional 12-digit e-way bills, and 4×6-inch PDF
pages. Fifty shipments with two pieces each produce 100 readable pages, with
one order/invoice assignment per shipment. Database table/row snapshots remain
identical before and after review and generation on legacy and ER backends.

Numbering checks include 20 concurrent requests, daily order rollover,
April–March financial-year invoice rollover, changed-batch/owner rejection,
render failure without reserved numbers, and preview/download retries without
new assignments. Separate Python processes retain previous assignments and
continue the sequence; corrupt state rejects generation instead of resetting.
The complete backup includes the private numbering files and reviewed snapshots.

All **four browser workflow groups** in `tests/browser/label-review.cjs` passed:

- Fifty shipments selected across two pages retain their selection on refresh
  and receive one review card each.
- Common payment/description values and individual overrides work; missing
  invoice amounts prevent generation and optional e-way bills are validated.
- Actual PDF download, preview and refresh/reprint reuse references while
  shipment data stays unchanged.
- Light/dark layouts at desktop, tablet and phone sizes keep the modal footer
  reachable, without horizontal overflow, JavaScript errors or server errors.

The browser downloaded 51 piece labels for 50 shipments. Every page contains
text and the embedded logo; reprint text matches the original page for page.
Only the shipment with an enabled e-way bill prints that field. The first PDF
page and desktop/mobile review screens were rendered and inspected visually.
The updated sample PDF is `docs/b2b-label-example.pdf`, and the batch-review
screenshot is `docs/screenshots/label-review.png`.

```bash
python -m pytest -q -ra
python -m pytest -q tests/test_label_review.py -k process_restart
SYSTEM_TEST_PORT=10003 python tests/browser/serve.py
# In another terminal, with Playwright and Chromium installed:
SYSTEM_TEST_URL=http://127.0.0.1:10003 SYSTEM_TEST_PASSWORD=system-test-only \
  node tests/browser/label-review.cjs
```

No migration or new application dependency is required. Preserve
`instance/uploads/.label-state/` on a persistent volume; multiple app servers
must share that volume and support file locking. Preview reserves a sequence,
so abandoned previews can leave gaps. Physical printer output and other browser
engines were not checked; print the generated PDFs at actual size (100%).

## Label design refinement

The current label follows the user's supplied reference: a compact logo/payment
header, sender block, larger recipient block, bold destination-PIN band, one row
for chargeable measurements/boxes/pickup date/piece count, tracking/barcode,
then commercial details. The footer now uses aligned caption/value columns for
invoice amount, order ID, invoice number, goods description and optional e-way bill.
The payment badge uses the selected Pre-paid/To pay/Cash on delivery value;
the routing band uses the actual drop PIN. No priority, carrier routing codes,
telephone numbers, country or package dimensions are invented. All existing
label parameters remain, and internal identifier types (LRN/AWB/Order ID) remain
omitted. Black bands use white text; other text is black on white paper, with
the original green logo. Fonts remain embedded and PDF dimensions remain
4×6 inches. Barcode-free labels retain an enlarged tracking number in the same
tracking section.

The focused label/company tests passed **48 tests** without failures, covering
legacy and ER SQLite backends. All **four browser workflow groups** passed again
with 50 selected shipments and real preview/download/reprint PDFs. Representative
short/long destination addresses, weight plus volume, optional e-way bills,
longer goods descriptions and barcode-free pages were rendered and visually
inspected. This is a PDF layout change; no database migration or workflow change
is required. The current sample is `docs/b2b-label-example.pdf`, with a rendered
preview at `docs/screenshots/b2b-label.png`. Physical printer output remains
unverified; print at actual size.

The border follow-up adds a muted green double frame with rounded corners,
drawn inside the 4×6 page without moving any content. The existing PDF checks
were rerun for piece counts, embedded logo/fonts, barcode on/off, reviewed
fields and unchanged database snapshots. The sample PDF and rendered preview
were updated and visually checked for clearance around the frame.

## Selected label measures, company viewing and document names

The final full suite passed **578 tests**, with **10 expected skips**, no
failures and two dependency deprecation warnings, using disposable SQLite and
PostgreSQL databases.

Login no longer has the Admin Home link. The company eye icon opens the complete
master and locations in view-only mode: text is selectable but read-only, status
checkboxes are disabled, Save/Add/Remove actions are hidden, and form submission
is blocked. Closing View and choosing Edit restores the normal editor. Browser
checks verified no company write requests from View, including Enter on a field,
and reachable Close controls on desktop and dark-mode phones.

The label list, review summary and PDF use only the shipment's selected
measurement mode, even when both numeric values are saved. Weight uses kg;
volume uses m³. Separate weight/volume PDF pages were downloaded and verified.
Label fonts are bundled Liberation Sans regular/bold, with Arial-compatible
metrics and their OFL license, embedded in PDFs. The revised footer keeps all
existing commercial values in a caption/value grid. Samples for weight, volume,
longer addresses and descriptions were rendered and visually inspected.

One shared filename helper now names stored PODs/invoices and individual/bulk/
complete-backup downloads: `consignmentNum_pod.ext` or
`consignmentNum_invoice.ext`. Unique storage version folders retain rollback and
ER history without changing the visible basename. Current and retained document
copies in complete backups use the same convention. Numbered backup folders
still follow the original shipment order; unknown uploads retain their stored
basename in separate numbered folders. Existing storage references are not moved.

The new naming tests passed **eight cases** across disposable SQLite/PostgreSQL
fixtures. They upload **150 files for 75 shipments** in three 50-file batches,
check both bucket names and exact storage basenames, verify every complete-backup
filename, and submit the extracted files for bulk review without renaming.
Same-extension replacement retains distinct paths with identical basenames;
historical backups use the same name, and a commit failure preserves the current
bytes/reference and removes the unsuccessful upload.

All **13 browser workflow groups** passed: three new admin follow-up groups,
six document-editor groups and four 50-shipment label-review groups. These include
POD/invoice upload, replacement, Remove/Undo, stale-edit rejection, actual file
downloads and complete backups, company viewing/editing transitions, selected
measurements, preview/reprint numbering, and responsive light/dark screens.

Storage tests use a simulated Supabase client. No live bucket credentials were
used and no existing live objects were renamed. Pull and restart the app; no
database migration or new Python dependency is required. The source ZIP includes
both label fonts and their license.

## Label record-ID handling

The reported after-menu error was reproduced by submitting a UI/widget ID in
the manual details while the selected shipment ID remained a valid UUID. Review
cards now bind their IDs and input references directly to the server-returned
shipment, rather than collecting arbitrary `data-field` values or trusting the
card's mutable DOM ID. Changes to unrelated fields or UI identifiers cannot
overwrite the shipment ID or the key used to save invoice/description drafts.

The label page declares its numeric/UUID ID format and removes incompatible
cached selections from older databases, preserving current selections and
manual details. Its script URL is versioned so updated pages load the fix.
Malformed selected/detail IDs return specific recovery instructions; they do
not allocate label numbers or change database rows. Valid IDs still go through
the same strict record lookup and selected/detail matching checks.

The focused label/company/ER suite passed **94 tests**, with **one expected skip**
and no failures, against disposable SQLite/PostgreSQL fixtures. The new browser
regression passed three workflow groups: stale numeric/malformed selections
on the UUID app, actual PDF download despite overwritten DOM IDs/injected fields,
and Preview/refresh preserving both numbering and edited invoice details.
The recovered PDF was inspected for its real shipment reference, invoice amount
and goods description. No live database or Storage object was changed.
The existing four 50-shipment browser workflow groups also passed again,
including cross-page selection, actual download/preview/reprint, refresh and
responsive light/dark controls.

## Optional label names and measurement wording (12 October 2026)

Labels now put postal codes in the address block, with no large destination-PIN
banner. A PIN already present in the saved address is printed once. Each review
card has optional sender and receiver names: entered names precede the address,
and blank names print the address directly without a placeholder or location tag.
Names are escaped, limited to 120 characters, and stored in the reviewed label
run rather than the shipment/company database. Blank names preserve compatibility
with previously saved runs. The optional e-way bill is captioned **EWBN**.

Public metrics use **Weight** or **Volume**, with the selected shipment measurement
on printed labels. Dashboard, shipment forms, accessible field labels, MIS
columns/summaries and generated PDFs use the same wording. Shipment Excel
exports/templates and backup workbooks use `weight`/`volume` headers; both new
and legacy import headers are accepted. Database/API keys and authoritative
backup JSON retain their original names for restoration compatibility.

Validation completed:

- Full pytest suite: **607 passed, 10 skipped**, no failures, with disposable
  SQLite and PostgreSQL fixtures. Two upstream dependency deprecation warnings.
- Four 50-shipment browser groups passed, including optional names in the
  submitted request and after refresh, real preview/download/reprint, unchanged
  shipment records, and accessible controls on desktop/mobile/tablet in both themes.
- All three record-ID browser regression groups passed with the expanded form.
- Both downloaded batch PDFs contained 51 pages, matching references and manual
  names on reprint; other shipments omitted names. Conditional EWBN and the absence
  of the PIN banner/old metric caption were verified from extracted text.
- Named weight and address-only volume label PDFs were rendered and inspected.
  The updated management sample has 13 nonblank pages and current metric wording.

Current samples are `docs/b2b-label-example.pdf`, `docs/management-mis-example.pdf`
and the updated dashboard/MIS/review/label screenshots. All data used for these
checks was disposable sample data. No live database or Supabase object was changed.
No database migration or new dependency is required; pull and restart the app.

## Compact industrial label layout (12 October 2026)

Printed address blocks now follow their actual text height. Blank lines,
indentation and repeated whitespace are removed, with meaningful address line
breaks preserved. Small, consistent gaps separate headings, names, addresses
and rules. Sender/receiver text shares a bounded height budget so longer
addresses cannot push the metrics, tracking block or footer off a 4×6-inch page.
The barcode uses the space available below the metrics, maintaining its existing
Code 128 content, width scaling and quiet zones. The footer uses two columns for
invoice amount/EWBN and order/invoice references, plus a full-width goods section.

Addresses, metrics and references use embedded Liberation Mono regular/bold for
a mechanical Courier-compatible style. Small headings retain Liberation Sans.
Both families are shipped with the app and covered by the included OFL license.
No host font installation or database change is required.

The focused label/field/review suite passed **70 tests**, with no failures or
skips. The new nine PDF geometry tests use optional Poppler `pdftotext` bounding
boxes to check compact spacing, equivalent layouts for padded/clean addresses,
text within the print frame, separate document/tracking blocks, short/long
addresses, with/without barcode and EWBN, plus embedded fonts and retained text.
Existing tests verify optional names, measurement selection, input validation,
legacy/ER backends and unchanged database records during generation.

The four 50-shipment browser workflow groups and three record-ID regression
groups passed. Actual batch download and reprint PDFs each contain 51 pages,
matching numbers/details and embedded mechanical fonts. Weight, volume, long
address and no-barcode samples were rendered and inspected. The sample PDF and
two label screenshots now show the new layout. All checks used disposable sample
data; no live database or Supabase object was changed.

## Validation inside open forms (12 October 2026)

Shipment add/edit errors now use a persistent summary below the modal header,
outside the scrolling body. Invalid fields receive a visible marker, an
accessible error description and focus. Draft field values and selected
documents are retained; no invalid row is staged. Existing identifier/PIN and
numeric rules are checked before staging. Retained inactive measurements are
also checked, with instructions to correct them without changing the selected
measurement basis or discarding their value.

Delayed Save All failures route into an open shipment modal. Structured HTTP
validation errors are read from the API wrapper's response body, display their
actual messages, and mark the matching table record by ID. The underlying table
is not scrolled while that modal is open. Page feedback remains current when
the dialog closes. The Save/Save All staging workflow is retained. New/reopened forms clear old markers
and summaries. The page's auto-dismiss timer leaves modal alerts visible.
Company/MIS, bulk-upload and document-editor errors scroll into view within their
own dialogs. Updated scripts/styles have versioned URLs.

Validation completed with disposable local data:

- **101 focused tests passed**, no failures or skips. One upstream ReportLab
  deprecation warning. An older label-font check was updated to verify all four
  embedded fonts (Liberation Sans and Mono), matching the existing label design.
- Five browser validation groups passed: required identifier, PINs, pieces,
  active/retained measurements and document selection; delayed structured server
  rejection with an open editor; company server validation; bulk upload; and
  document editor on a short mobile viewport. Errors persist beyond ten seconds,
  input values survive errors, and invalid entries cannot be staged/saved.
- Existing consignment staging and document-editor browser regressions passed,
  covering delayed table refresh, both document uploads/removals, replacement,
  Cancel/Undo, stale-editor protection and reachable controls across screen sizes.
- Desktop/mobile screenshots were inspected. Current examples are
  `docs/screenshots/shipment-validation-desktop.png` and
  `docs/screenshots/shipment-validation-mobile.png`.

No live database or Supabase data was changed. Pull and restart the app; no
migration or new dependency is needed.
