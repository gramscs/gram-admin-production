`lr-template.pdf` is derived from the user-supplied two-page `LAKONS0107.pdf`.
Its fixed artwork, company details, logo, authorised signatory stamp, declaration,
receipt acknowledgement layout and complete terms page are retained. Dummy
shipment text, the old barcode, old timestamp and page counters were removed
from the PDF content streams, rather than covered with opaque shapes. Invoice
column headings added in the sample were retained. No sample shipment values
are used as application defaults.

The generator overlays reviewed values in the original cells, creates a barcode
for the reviewed LR number, and prints one generation timestamp in IST and
`1 Of 2` / `2 Of 2` for each original/terms pair. It does not alter the terms.
