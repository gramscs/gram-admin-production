# System testing — 11 October 2026

The final automated run passed **359 tests**, with **10 backend-specific skips**
and **no failures**. Browser workflows also passed after correcting one saving
bug found during this review. These results validate the tested application;
live Supabase storage and the team's running Codespaces app still need an
environment-specific check.

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
validation. The final run with PostgreSQL enabled took approximately 68 seconds.
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
