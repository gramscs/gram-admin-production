# Gram SCS — Standalone Admin Panel

This is a portable copy of the admin panel from `gramscs/gram-scs`. It runs as
its own Flask application. You can copy this folder's contents into an empty
repository and run it without the original website repository.

## Start locally

Requires Python 3.12. Node.js, Docker, and PostgreSQL are not needed for the
default local setup.

From this folder:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python run.py
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell and copy
the settings with `Copy-Item .env.example .env` instead.

Open `http://127.0.0.1:5000`. It takes you to the admin login.

Local development login:

- Username: `admin`
- Password: `admin-pass`

The application automatically reads `.env`. These development defaults apply
when `FLASK_ENV=development` and credentials are left blank. To change the
password, generate a hash and put it in `ADMIN_PASSWORD_HASH` in `.env`:

```bash
python -c "from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass('New admin password: ')))"
```

## Update an existing installation

Stop the running Flask server, activate your virtual environment, then run:

```bash
git pull origin standalone-admin
python -m pip install -r requirements.txt
python -m flask --app wsgi:app upgrade-db
python -m flask --app wsgi:app check-db
python run.py
```

The document validator requires the updated dependencies. The schema upgrade
adds document fields and preserves existing records; restart before opening
the new pages. Use your normal production restart command on a hosted app.

## Add dummy data locally

With your virtual environment activated, run from this repository:

```bash
DATABASE_URL=sqlite:///instance/admin.db python -m flask --app wsgi:app seed-demo
```

This adds **25 sample shipments**, including all four delivery statuses,
addresses, PIN codes, and dates. It only seeds shipment data.
Refresh the admin pages after running it. Existing records are preserved,
and running the command again does not duplicate the demo records. The command
only works in local SQLite development and does not seed Supabase.

The command explicitly targets `instance/admin.db`. Your running app must use
the same SQLite database to display these records. On Windows PowerShell, set
`$env:DATABASE_URL = 'sqlite:///instance/admin.db'` first, then run
`python -m flask --app wsgi:app seed-demo`.

## What is included

- A shared green/charcoal design, Space Grotesk typography, and responsive navigation.
- Management dashboard with pickup trends, status mix, client/route rankings,
  document watchlists, date/client/status filters and presentation mode.
- MIS reports in Excel, PDF and CSV, with configurable columns, saved reporting
  views, immutable report history, private downloads, names/notes and deletion.
- Light and dark themes across every admin screen, including login.
- Shipment creation, editing, deletion, searching, sorting, and pagination.
- PDF/image POD and invoice uploads inside the shipment form, previews, downloads,
  staged removal, and file-content validation.
- Excel import/export, import-template download, and a complete shipment MIS PDF.
- Client company masters with multiple pickup/drop presets and editable shipment snapshots.
- B2B piece labels with optional Code 128 barcodes and PDF preview.
- Complete ZIP backup of all admin tables and uploaded files, with ordered
  consignment folders, Excel/JSON data, checksums, and a missing-file report.
- Local Bootstrap, icon fonts, interface fonts, scripts, and logo. The interface
  does not need a CDN or the public website to load.

## Where the code is

```text
app/
  __init__.py                  Standalone application setup and health checks
  admin/                      Original admin routes and business logic
  models.py                   Shipment/client/MIS tables; retained legacy records
  db_maintenance.py           Optional PostgreSQL schema repair helper
  templates/admin/            Admin screen HTML and shared theme components
  static/js/consignments.js    Shipment screen behavior
  static/js/admin/             Requests, pending edits, input checks, theme preference
  static/vendor/              Bundled Bootstrap and Space Grotesk font
  static/css/                 Shared themes, consignment layout, and icons
  static/fonts/               Icon fonts
  static/images/logo.png      Original branding
tests/                        Standalone application tests
database/                     UUID shipment/client/document/MIS business models
docs/database-model.md        Database design, integrity rules and migration mapping
docs/database-schema.sql      Generated PostgreSQL schema for empty staging databases
migrations/001_shipment_database.py  One-command schema and existing-data migration
run.py                        Local server entry point
wsgi.py                       Production server entry point
.env.example                  Configuration template
requirements.txt              Python dependencies
```

## Appearance

Dashboard previews with local demo data: [Light mode](docs/screenshots/dashboard-light.png)
· [Dark mode](docs/screenshots/dashboard-dark.png).

Use the theme toggle in the top bar (sun/moon icon on phones) to switch between
light and dark mode. Your choice is saved in this browser and applies to every
admin screen, including login; it also syncs between open tabs. Before you make
a choice, the app follows your device theme. The toggle still works if browser
storage is blocked, although the preference cannot be remembered after reload.

The dashboard shows saved consignment totals, exact **In Transit** and
**Delivered** and **Out for Delivery** status counts, and the five most recently
added shipments. These are database values, not sample analytics. On small
screens, use the menu button beside the logo to open navigation.

## Database and delivery-proof files

The supplied ten-table ER diagram is implemented in the **next-version**
SQLAlchemy [database model](docs/database-model.md), with UUID keys, shipment
history, versioned documents, file metadata, MIS snapshots and audit records.
Review the [PostgreSQL schema](docs/database-schema.sql) and migration mapping
there. This design is not applied to the running app automatically; the
current database and uploads remain unchanged.

To create the new schema and import existing records in one run, follow the
[migration instructions](docs/database-migration.md):

```bash
python migrations/001_shipment_database.py --apply
```

Set `DATABASE_URL` securely to a fresh, empty target. The source defaults to
local `instance/admin.db`; use `MIGRATION_SOURCE_DATABASE_URL` for a remote
source or `--source-local` for another local file. The command creates all ten
model tables, imports legacy or full-model data and archives every source row,
including extra tables/columns. It preserves existing history and relationships.
Omitting `--apply` performs a preflight. The dashboard detects the migrated ER
tables automatically; run `upgrade-db` once to restore archived screen fields,
then restart the app. File storage stays in its original location.

If the migration already printed **Migration committed**, do not import again:

```bash
AUTO_CREATE_TABLES=false python -m flask --app wsgi:app upgrade-db
AUTO_CREATE_TABLES=false python -m flask --app wsgi:app check-db
```

`upgrade-db` creates ER screen bookkeeping and preserves imported data. It does
not recreate the old `consignment` or `company` tables. Existing local databases
continue using their original schema. See [dashboard cutover](docs/database-migration.md#dashboard-cutover).

By default, this app creates a **new, empty** SQLite database at
`instance/admin.db` and stores uploaded PODs and invoices in `instance/uploads/`.
It does not include your original database, enquiries, uploads, passwords, or
test records.

To work with the same records as your website, set `DATABASE_URL` to the same
database connection in `.env`. PostgreSQL connections use
`postgresql://user:password@host:5432/database`.

This admin app is dedicated to shipment tracking. Leads pages, editing routes,
and email tools have been removed. Existing enquiry/subscriber records remain
in the database and complete backup so the change does not discard data.
For shared Supabase shipment-document storage, configure `SUPABASE_URL`,
`SUPABASE_KEY`, and `SUPABASE_BUCKET`. If the website stores proofs on its
local disk, a shared database alone does not share those files: copy/mount
the uploads into this app's `instance/uploads/`, or configure the common
Supabase storage. Back up the `instance/` directory when using local storage.

Table creation runs automatically for local SQLite development. Remote PostgreSQL
tables are left unchanged by default. You can create missing tables explicitly:

```bash
python -m flask --app wsgi:app init-db
```

This creates missing tables and preserves existing records. It is not a schema
migration tool. For a legacy PostgreSQL shipment table missing fields, the
original repair helper is available as an explicit command:

```bash
python -m flask --app wsgi:app repair-consignment-schema
```

## Connect to Supabase PostgreSQL

1. Open your Supabase project and click **Connect**.
2. Select **Session pooler** and copy the PostgreSQL connection URI. The
   session pooler on port 5432 works with IPv4 hosting environments.
3. Replace the password placeholder with the database password, URL-encoding
   special characters. Store the URI securely as `DATABASE_URL` in your
   hosting/environment settings, or in your ignored local `.env` file.
4. Restart the app, then run the read-only connection and table check:

   ```bash
   python -m flask --app wsgi:app check-db
   ```

`SUPABASE_URL` and `SUPABASE_KEY` are for shipment-document file storage; they
do not replace the PostgreSQL database connection string. No Supabase API key
is required for the database connection.

The app requests encrypted connections for Supabase hosts unless the connection
URI already specifies an SSL mode. Supplied SSL settings, including `verify-full`
and a CA certificate path, are preserved. PostgreSQL connections have a 10-second
connection timeout and check pooled connections before reuse.

`check-db` does not create tables, copy records, or change data. If it reports
missing tables on a new database, use `init-db`. If you are sharing an existing
website database, inspect missing fields before using the PostgreSQL repair
command. Existing data is not automatically migrated from SQLite to Supabase.

Allow outbound access to the exact hostname from your connection URI and port
5432 in your hosting environment. The credentials must be available to the
Python process for PostgreSQL authentication.

## Shipment fields and B2B labels

Click **Add Row** to open the Add Consignment modal. Fill in the shipment details
and click **Save** to stage the completed row in the table. Click **Save All**
to write staged changes to the database. Cancelling the modal adds no row.
The row's Edit button opens the same form for existing shipment details.

Validation errors appear in the open shipment modal, with the affected field
highlighted and focused. The error summary stays visible as the form scrolls;
it does not disappear after ten seconds. Correct the fields and click **Save**
again. Entered values and document selections remain available. **Save All**
continues to write staged changes to the database. Company, bulk-upload and
document-editor errors also come into view within their own dialogs.

The sheet groups identifier type/number, weight/volume, pickup
tag/date, and drop pincode/date together. Every displayed field can still be
edited directly, and each sortable field has its own header button. Use
**Import / Export** for the Excel import dialog, import template, and Excel/PDF
exports. The add/edit form groups shipment, pickup, drop, and document details;
its footer stays visible while you scroll.

Each shipment has an identifier type (LRN, Order ID, or AWB), an identifier of
up to 64 characters, a number of pieces, weight in kg, and volume in m³.
Weight/volume accept non-negative values with up to three decimal places. Pieces must be a whole number from 1 to 10000. Identifiers remain unique
across all types. Existing records default to LRN and one piece.

Local SQLite development upgrades existing shipment tables automatically on
startup. To upgrade an existing PostgreSQL/Supabase database, run explicitly:

```bash
python -m flask --app wsgi:app upgrade-db
python -m flask --app wsgi:app check-db
```

The upgrade adds shipment/document columns, creates company/location master
tables, retains legacy enquiry columns, and allows longer identifiers without deleting records. It does not require copying or replacing the database.

Open **B2B Labels**, select saved shipments, then click **Generate labels** to
open **Review label details**. Selections and entered details survive page
changes, searches and refreshes in the same browser tab. Each batch is limited
to 500 piece labels.

For each selected shipment, enter its invoice amount in rupees, choose
**Pre-paid**, **To pay**, or **Cash on delivery**, and enter a goods description.
Check **EWBN exists** only when applicable and enter its 12-digit number;
unchecked e-way bills are omitted entirely. **Apply common details** fills the
payment mode and description across the batch, after which individual fields
remain editable. Invoice amounts and e-way bills are entered per shipment.
Optionally enter **Sender name** and **Receiver name** for each shipment. Names
are printed above the address; a blank name prints the address directly without
a placeholder or preset location tag. PIN codes already in an address are not
repeated. These reviewed names stay with the label run, without changing the
company or shipment database.
Use **Preview PDF** or **Download PDF** when all shipments are ready.

The 4×6-inch PDF uses the supplied GRAM logo alone in the header,
saved pickup date, shipment identifier, boxes, weight/volume totals,
pickup/sender and drop/recipient addresses and PIN codes. It produces one label
per piece. All pieces of a shipment share the reviewed commercial details and
assigned order/invoice numbers. Print at 100% scale; Code 128 barcodes remain
optional. The Archive Delivered button and deletion endpoint have been removed.

Printed labels show the tracking number without an LRN, AWB or Order ID type
heading. The reference-inspired layout places the logo and payment badge first,
then sender and recipient addresses with postal codes in the address block.
Measurement totals, boxes, pickup date and piece count share one row, followed by tracking/barcode and the
invoice amount, order/invoice numbers, goods description and optional e-way bill.
The footer uses a two-column reference grid for invoice amount/EWBN and
order ID/invoice number, followed by a full-width goods description.
An inset double border with rounded corners and muted green strokes frames the
label, with clear space around its addresses, tracking barcode and footer.
Only the shipment's selected measurement is printed: **Weight** uses kg and
**Volume** uses m³. Any saved value in the inactive measurement field stays in
the database but is omitted from the label, selection list and review summary.
Addresses and references use embedded **Liberation Mono** (Courier-compatible)
for a mechanical, fixed-width style; small headings use Liberation Sans.
Address blocks follow their text height instead of reserving empty rows. Blank
lines and repeated spaces are removed from printed addresses, while meaningful
line breaks remain. Long addresses fit a shared height budget, and the barcode
uses the available space underneath. The supplied fonts and their license are
bundled in the app so printing does not depend on fonts installed on the server.

Public metrics use **Weight** and **Volume**, including dashboard, forms, MIS,
PDFs and Excel downloads. Shipment import/export and backup workbooks use
`weight` and `volume` headers; older imports using `chargeable_weight` and
`chargeable_volume` are still accepted. Existing database/API field names and
machine-readable backup JSON remain unchanged for compatibility. No migration
is required.

Order IDs use `B2B-YYYYMMDD-0001`, with the generation date in India and a daily
sequence. Invoice numbers use `B2B-2026-27-0001`, with the April–March financial
year and a sequence across that year. Preview, download and reprint of the same
unchanged batch reuse its numbers. Changing its selection/details creates a new
batch. Preview reserves numbers, so abandoned previews can leave sequence gaps.

After switching between the older numeric-ID database and the migrated UUID
database, Labels removes incompatible saved browser selections automatically
and keeps current selections and entered details. Preview/download binds each
review card to the actual saved shipment ID. If the app reports outdated label
details, reopen **Generate labels**; if a selected shipment was deleted, use
**Clear selection** and select the current shipments again.

**No database schema or records are changed by label generation.** Reviewed
snapshots and locked numbering files are stored privately under
`instance/uploads/.label-state/` and included in the complete backup. Preserve
this folder on a persistent volume across app restarts and deployments. Multiple
server instances must share the same volume with file-lock support to maintain
one sequence; independent copies cannot coordinate numbering. Restore numbering
along with the corresponding backup. These are label references; the app does
not create accounting invoices from these inputs.

## Deploy as an individual app

Set `FLASK_ENV=production`, a strong `SECRET_KEY`, your `ADMIN_PASSWORD_HASH`,
and `DATABASE_URL` in your hosting service's environment settings. The app
requires these settings in production and uses secure login cookies, so serve
it over HTTPS. Initialize missing tables with `init-db` before serving traffic.

On a Linux host, start with:

```bash
gunicorn --bind 0.0.0.0:8000 --workers 2 wsgi:app
```

A `Procfile` is included for hosts that supply `PORT`. For shared rate limits
across workers, install `redis` and set `RATELIMIT_STORAGE_URI` to your Redis URL;
the default in-memory limits operate within each worker.

Readiness checks:

- `GET /health`: application responds with `status: ok`.
- `GET /health/db`: database query succeeds with `status: ok`.

## Run tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests use temporary databases and uploads. They cover login protection, admin
screens, shipment editing, PDF/image PODs and invoices, content validation and
failed-save recovery, Excel import/export, labels, backups, startup, and persistence.

## Behavior retained from the original

- Shipments **Export PDF** downloads a readable shipment register matching the
  current search, company and identifier-type filters. Management charts and
  summaries are available separately in Dashboard and MIS Reports. Unsaved form
  edits require **Save All** first.
- Complete backups include database records and uploaded files; the optional
  data-only JSON export contains records and file references. No backup-restore
  interface is included.

The public website link on the login screen now points to this application's
home route. The admin business logic, models, and JavaScript were copied from
the original. The standalone application setup and bundled browser assets are
the additions that let this folder run independently.

## Client companies and shipment presets

Open **Companies → Add Company** to save a name, registered address, contact
information, and any number of regular pickup/drop locations (up to 50 per
company). Choose one default for each group. If no default is marked, the
first location in that group becomes the default.

In **Shipments → Add Row**, select your client company. Its default pickup
and drop locations fill automatically. Choose another saved location or edit
the address, pincode, or tag manually. **Save**, then **Save All**, as before.
Shipment addresses are copies: editing a master later does not change existing
shipments. No company is required for one-off shipments.

Company **Archive** keeps master data and existing shipment links, while
preventing new selections. Use **Include archived → Restore** to reactivate a
client. Editing an archived company also lets you mark it active again. Excel
exports include `company_id` and `client_company`; the import template accepts
an optional `company_id` from a master in this database.

## Shipment filters and Documents

**Shipments** has Company and Identifier type dropdowns alongside search.
Choose LRN, Order ID or AWB; combine the filters to narrow the table. Company
options include archived clients and an **Unassigned company** option. Pagination,
Excel/PDF exports and **Bulk documents → All matching the current filters** use
the same filters. **Clear Filters** resets all three fields. Filters stay in the
page URL so a refresh or a shared link restores the view. Staged shipment edits
remain available when a filter hides the edited row.

**Documents** shows current PODs and invoices beside the original shipment
identifier, identifier type and company. It includes image thumbnails, PDF/image
downloads and missing-document indicators. **Upload** adds a missing document;
the **pencil** opens a small editor for replacing or removing an attached file.
Preview or download the current document, choose a replacement, or stage a removal.
**Undo changes** resets the editor; **Cancel** preserves the saved file. **Save
changes** commits that one document immediately and refreshes the list. If another
user replaced the document while the editor was open, the app asks you to reload
rather than overwrite the newer attachment. Shipment fields and the other document
are left intact.

The compact **eye** opens the exact shipment, including when identifiers overlap.
Refreshing keeps this specific-shipment view. Changing a search/company/type
filter or choosing **Clear Filters** returns to normal filtering. The same
company/type/search filters and the same **Bulk documents** review/upload/ZIP
flow are available here. Selections remain available across document pages.
Uploading from either tab updates the same shipment document; identifier changes
in Shipments appear in Documents automatically. Files remain private and retain
their existing validation and storage rules.

The shipment page is now `/admin/shipments`; existing `/admin/consignments` links
continue to work. API paths, saved records and database tables keep their existing
names. This update requires no database migration.

Additional browser coverage, using the isolated helper documented in
[system-testing.md](docs/system-testing.md):

```bash
SYSTEM_TEST_URL=http://127.0.0.1:10003 \
SYSTEM_TEST_PASSWORD=system-test-only \
node tests/browser/shipment-documents.cjs
```

## Dashboard document follow-up

Expand **Missing PODs & invoices** on the dashboard to see the shipments still
waiting for documents, with their identifier, identifier type, company and status.
Choose **Either missing**, **POD missing**, **Invoice missing** or **Both missing**.
Counts and the paginated list follow the dashboard's company, identifier, search,
pickup-date and status filters. The list includes all shipment statuses by default;
the existing **Delivered without POD** watchlist metric still counts delivered
shipments only. **Open Documents** opens the original shipment's document records
so you can upload the missing files. The list refreshes when you reload the dashboard.
No database migration is required.

## Complete admin backup

Choose **Complete backup** in the sidebar or **Download Backup ZIP** on the
dashboard. The ZIP includes every row and column from every application-owned
table: consignments, client companies (including archived clients), saved
pickup/drop locations, and preserved legacy enquiries/subscribers. The backup
is read-only; it does not change or delete records or uploads.

Inside the ZIP:

- `data.json`: all admin database records, with each shipment's backup order,
  document path, and retrieval status.
- `admin-data.xlsx`: a sheet for each table, plus document and inventory reports.
  JSON is the authoritative full-data copy; Excel is for browsing the records.
- `documents/000001_<consignment-number>_id-<id>/`: that shipment's POD and invoice
  files. Numeric prefixes follow the consignment data/Excel order (database ID
  ascending). Files keep the standard names, such as `DEMO0017_pod.png` and
  `DEMO0017_invoice.pdf`, including attachments saved under older storage names.
- `file_history/`: retained ER document versions, in separate numbered folders
  so repeated standard filenames remain distinct.
- `other_uploads/`: all discovered uploaded files that no longer have a record
  link, including local legacy uploads and unlinked admin Supabase uploads.
- `document_report.json` and `README.txt`: included/missing/unavailable files,
  upload-listing errors, original references, sizes, and SHA-256 checksums.

Local inventory covers `instance/uploads/`. Supabase inventory covers the
`consignments/` namespace used by the admin uploader in configured/referenced
buckets, plus every file directly referenced by shipments or saved MIS reports. It does not
export unrelated website buckets/tables or environment credentials.

Missing files or failed storage listings make the filename end in
`_incomplete.zip`. All readable data/files are still included; check the report
before relying on that backup. Live edits to uploads during export can cause
retrieval issues. Files are copied exactly rather than re-encoded. Large
archives spill to temporary disk instead of holding all file bytes in memory.

**Download data only (JSON)** on the dashboard retains a faster records-only
option (`/admin/generate-backup?format=json`). The default backup URL downloads
the complete ZIP. Neither format includes an automatic restore interface.

## POD and invoice documents

Open a shipment with **Add Row** or **Edit**. The **Shipment documents** section
contains separate POD and invoice cards. Both accept **PDF, JPG, PNG and WebP**,
up to **5 MB per file**. Neither document appears as a table column. Image
previews appear in the form; PDFs use **Download** instead of an embedded viewer.

Choose, replace, remove or undo a document change in the form, then click
**Save → Save All**. **Cancel** discards the form's changes. Files already saved
are untouched until you submit the save. Staged changes survive shipment
filtering/pagination. Download buttons work for saved and selected documents.

Supabase uploads use two private buckets, with the same server credentials:

```env
SUPABASE_BUCKET=pod-uploads
SUPABASE_INVOICE_BUCKET=invoice-uploads
```

Set `SUPABASE_URL` and `SUPABASE_KEY` securely in the running app's `.env` too.
The database connection alone does not enable Storage. Use the project's HTTPS
API URL for `SUPABASE_URL` (not the PostgreSQL connection string), and a server-side
legacy **service_role** key for `SUPABASE_KEY` (under Supabase API Keys → Legacy). Keep that key out of the browser and
Git. Restart the app after editing `.env`. The buckets must already
exist, and the server key must allow uploads/downloads in both. PODs go to
`pod-uploads/consignments`; invoices go to `invoice-uploads/consignments`. Saved MIS files continue using
the POD/default bucket. Existing references still download from their original
bucket; this update does not move earlier files. Complete backups inventory
both configured buckets, including unlinked files under `consignments/`.

Invoice upload, download, replacement and removal use the same document service
as PODs. With both Supabase credentials set, new invoices are stored under
`invoice-uploads/consignments/`, not `instance/uploads/`. This applies to the
shipment form, Documents editor and bulk uploader. Their database references
include the bucket, so downloads/removals still find earlier files if you change
the bucket setting. A failed remote operation reports an error instead of
saving a new invoice locally. When both credentials are blank, both document
types intentionally use local development storage.

To activate remote invoices in the running Codespaces app, set both credentials
securely in its `.env`, keep `SUPABASE_INVOICE_BUCKET=invoice-uploads`, restart,
and run `AUTO_CREATE_TABLES=false python -m flask --app wsgi:app check-storage`.
After uploading a new invoice, Documents should show **Supabase · invoice-uploads**.
Existing local invoices keep their original references; turning on credentials
does not transfer files already uploaded locally. Re-upload those files through
the invoice editor to move their current attachment to Supabase.

Choose **Bulk documents** on the consignment page:

- Upload multiple files named `consignmentNum_pod.ext` or
  `consignmentNum_invoice.ext`, for example `LRN123_pod.pdf` and
  `LRN123_invoice.jpg`. Choose **Check files**, review matches and replacements,
  then **Upload checked files**. All files are validated first; any mismatch,
  duplicate or unsafe content stops the batch before changing documents.
- Select saved shipments with the table checkboxes, across pages if needed,
  or download all shipments matching the current search. Choose PODs, invoices
  or both. The ZIP uses the requested consignment filenames and includes
  `document-manifest.json` to identify documents that were not uploaded, are
  missing, or could not be retrieved.

Uploads support 50 files per batch, 5 MB each and 50 MB combined; document ZIPs
support 200 shipments and 250 MB per download. Special characters in download
filenames are sanitized. Ambiguous upload names are rejected rather than linked
to the wrong shipment. Rare duplicate ZIP names use a shipment-ID subfolder.
Bulk uploads save immediately; the form's individual uploads still use Save All.
New files use the same standard basename in private storage, individual
downloads, bulk ZIPs and complete backups. Supabase objects use
`consignments/<version-folder>/DEMO0017_pod.png` or
`consignments/<version-folder>/DEMO0017_invoice.pdf` in their respective buckets;
local development uses the same version-folder/name under `instance/uploads`.
Unique version folders prevent overwrites and preserve rollback/history. Original
upload names remain in metadata. Validated JPEG files use `.jpg`; unsafe characters
in shipment numbers are sanitized. Existing storage objects keep their references;
their downloads/backups receive the standard names without moving the originals.
If a shipment number is edited, downloads use its current number. Complete
backups retain the shipment-order folders, and unknown/unlinked files keep their
stored basename because no shipment/document mapping is available for them.
ER databases retain previous document versions when you **replace** a file;
legacy local databases delete the previous file after a successful replacement.
Explicit **Remove** deletes every saved version of that shipment's selected
document type, its attachment records and its unshared file metadata/bytes.
Deleting a shipment does the same for both PODs and invoices. Files still used
by another shipment (including its history) or an MIS report are kept. Audit logs
and original migration archives remain for traceability.

Removal checks the saved reference's actual bucket, even if bucket settings have
since changed. A failed or silently denied Storage delete returns an error and
rolls back the database changes. Temporary copies restore files already deleted
if a later delete or database commit fails. Restored bytes are verified. If
restoration fails, the error says manual recovery is needed and the app attempts
to save a private copy under `instance/uploads/.document-recovery/`. The server
log identifies that folder; `reference.json` maps each copy to its original
storage reference. Complete backups include these recovery copies too.
Storage and SQL are separate services: this is
compensating rollback, not a distributed transaction; an interrupted process or
an uncertain database commit can still need reconciliation.

For documents removed by the old ER behavior, use the targeted repair below.
It refuses to touch a current attachment and preserves shared files. The first
command previews the already-removed POD versions of exactly `DEMO0017`; the
second applies that cleanup. It does not scan or wipe a bucket.

```bash
AUTO_CREATE_TABLES=false python -m flask --app wsgi:app purge-removed-documents --identifier DEMO0017 --kind pod
AUTO_CREATE_TABLES=false python -m flask --app wsgi:app purge-removed-documents --identifier DEMO0017 --kind pod --apply
```

Use the running app's environment and database. Replace the identifier and
`--kind invoice` to repair another previously removed attachment. Repeating the
cleanup is safe. Restart the app after updating the code; no migration is needed.

**Measure shipment by** selects **Weight (kg)** or **Volume (m³ / CBM)** in both
the form and table. Only the selected input appears. Changing the basis preserves
any measurement already recorded in the other field. Volume is stored in cubic
metres; no carrier-specific volumetric-weight conversion is assumed. Excel and
MIS exports include the measurement basis. ER databases store the choice in
`admin_record_extras`, preserving the ten-table business model. Run `upgrade-db`
after pulling the update to add the field to older legacy databases.

**Export PDF** now downloads a consignment register with actual rows on the first
page, black text, embedded fonts and repeated table headings. The current search
is applied; an empty result shows an explanation. Wide registers are divided
into column groups with repeated identifiers. Management MIS exports remain
available from the reporting pages.

The server detects the file contents independently of the browser MIME type.
Images are decoded/re-encoded without metadata, limited to one image and
16 megapixels. PDFs must be readable, unencrypted and 1–200 pages; files with
scripts, automatic actions or embedded attachments are rejected. SVG and other
file formats are rejected. Stored names follow the shipment/document convention
inside unique version folders. Document requests require
admin login, and downloads disable content sniffing and caching. This is file
validation, not antivirus scanning. For Supabase storage, use a **private bucket**
and keep the storage key on the server; a public bucket has its own access rules.

The sidebar groups **Reporting**, **Shipment operations**, **Master data**, and
**Data & account**. Labels use a compact print-settings panel with normal page
scrolling; on tablets and phones, settings appear before the shipment list.
[View the batch label review](docs/screenshots/label-review.png).

### Check where uploaded documents are stored

Run this read-only command in the running app's terminal:

```bash
AUTO_CREATE_TABLES=false python -m flask --app wsgi:app check-storage
```

It reports **local** when both Storage credentials are blank, or checks that both
Supabase buckets can be inspected and their `consignments` folder listed. A passing
read check does not prove upload permissions. No files or database records are
changed. API keys and underlying remote exception details are not printed.

Documents shows **Saved locally** or **Supabase · bucket-name** beside each
attached file. When remote Storage settings are incomplete, or the Supabase client
cannot initialize, uploads now fail rather than silently storing on local disk.
Both settings blank still support intentional local development.

The pinned Supabase SDK accepts legacy JWT API keys; use the legacy service_role
key rather than an sb_secret_ key. Existing local files do not move when you add
Supabase credentials. After configuring
Storage and restarting, download a local file from Documents, then use its pencil
editor to replace it with that downloaded file and save. The new attachment will
use Supabase. For several files, bulk-download the POD/invoice ZIP, extract it,
and bulk-upload its correctly named files in batches of up to 50. Keep your backup
until the new files can be downloaded successfully.

## Label barcode choice

EAN/UPC codes generally identify retail products. These shipment labels use
ordinary **Code 128**, which can encode an LRN, Order ID, or AWB without an EAN.
Uncheck **Include Code 128 barcode** to print address labels without it. The
format does not claim GS1-128/SSCC compliance; carrier-specific GS1 requirements
need a separate identifier/format. See the [GS1 barcode overview](https://www.gs1.org/standards/barcodes/1d-barcodes).

Labels print on white paper with black text and a high-contrast black payment
badge with white text, irrespective of the admin theme. PIN codes stay in the
address block, without a separate banner. The supplied green logo
remains unchanged. The destination address and PIN receive priority; shipment totals
are clearly marked so they are not mistaken for per-piece weights/volumes.
Fonts are embedded for consistent printing. [View a sample three-piece label PDF](docs/b2b-label-example.pdf).

Companies includes an **eye icon** to view the company, contacts and all saved
locations without changing them. The view hides Save/Add/Remove actions; choose
**Edit** separately to change the master. Login retains its theme toggle and no
longer includes the Admin Home link.


## Management dashboard and MIS

The dashboard uses saved shipment data, with locally bundled interactive SVG
charts and the existing light/dark themes. **Present** opens a clean presentation
layout; click **Exit presentation** or press Escape to return. Trend points and
status segments show exact values on hover/focus, and the pickup chart includes
an expandable data table.

Use **Pickup-date period**, **Client** and **Current status** to select a view.
Periods include all time, the last 7/30 days, this month/quarter and custom dates.
Archived clients remain available for historical reporting. Click a client in
**Volume by client** to filter the dashboard to that client. Dashboard downloads
use the same applied filters. Shipment search can also narrow reports by
identifier, client, status, route, address or pincode.

Reporting definitions:

- Delivery metrics show **current status for the selected pickup-date cohort**.
  The app does not record actual delivery timestamps, so reports do not calculate
  on-time delivery or actual delivery trends.
- **Past expected date** counts open shipments whose estimated drop date (or ETA
  fallback) is before today in IST. This is an estimate-based attention flag.
- Date filters use pickup dates. All time includes undated records; date ranges
  exclude them and show the number excluded. Undated records never appear in the
  pickup trend. Supported dates include ISO dates/timestamps and DD/MM/YYYY or
  DD-MM-YYYY. Charts aggregate daily, monthly or yearly to fit the date span.
- Missing POD counts delivered shipments without a POD reference; missing
  invoices counts all selected shipments without an invoice reference. These
  indicators do not verify that an attached file can still be retrieved.
- Measurement totals include recorded, valid, non-negative measurements only.
  Weight/volume coverage shows how many shipments have a value. Invalid legacy
  measurements are flagged and excluded; blank measurements are not estimates.

Open **MIS Reports** to manage reports:

1. Choose filters, optional shipment search and the columns for your register.
2. Enter a report/view name, optional notes and a download format.
3. **Generate & save report** stores a snapshot and starts its download. The
   success message provides a download link and **Refresh report history**.
4. **Save as new view** keeps the filters, columns and format for reuse. Load a
   saved view to change it, then **Update loaded view**. Relative periods refresh
   when loaded/generated. Deleting a view leaves generated reports intact.
5. History keeps the original generated files, even if shipment data changes.
   Download again, edit the display name/notes, or delete a report and its file.
   Deleting reports does not change shipment records. History is paginated.

**Excel** includes editable charts, summary/coverage metrics, current status,
pickup trend, client and route breakdowns, attention flags and the full filtered
shipment register with your chosen columns. **CSV** contains the full selected
register. Spreadsheet text is protected against formula execution.

**PDF** includes a landscape management summary with vector charts, metrics and
coverage, the top 15 clients/routes, and the first 50 attention records. It also
includes **every filtered shipment**, even those with no attention flags, using
your selected register columns. Wide tables are divided into readable column
groups with row numbers and the selected shipment identifier repeated; long
tables/addresses continue across pages. Reports show pickup-date filters, search
and generation time in IST.

When no shipments match, the PDF states this clearly, shows the number of saved
shipments, and explains which dates/filters to check. An empty database explains
that shipments must be added/imported and saved first. Downloads read and check
the response before saving the file: login pages, server errors, empty files and
invalid PDF responses are shown as errors instead of becoming misleading PDFs.
Generated reports are checked for an overview and register before being offered.

After updating, restart the app, refresh the browser, and **generate a new PDF**.
Previously saved reports remain their original snapshots and are not rewritten.

Complete backups include the `mis_view` and `mis_report` tables. Generated MIS
files are linked to their history rows in `mis_reports/`, with checksums and
missing-file reporting, alongside the other uploaded documents.

After updating an existing installation, run the following against your intended
database (keep the configured DATABASE_URL), then restart the app:

```bash
python -m flask --app wsgi:app upgrade-db
python -m flask --app wsgi:app check-db
```

This additive upgrade creates `mis_view` and `mis_report`; it preserves existing
shipments, companies, documents and retained legacy records. No new dependency or
external chart service is required.

Preview using sample data: [Management dashboard](docs/screenshots/dashboard-light.png)
· [Dark dashboard](docs/screenshots/dashboard-dark.png)
· [MIS reports](docs/screenshots/mis-reports-dark.png)
· [Example management PDF](docs/management-mis-example.pdf).
