"""Fields printed in the supplied LR; values are transient, never model fields."""


def field(name, label, box, *, kind='text', required=False, limit=80, group='Dispatch'):
    return dict(name=name, label=label, box=box, kind=kind, required=required,
                limit=limit, group=group)


# Rectangles are (left, top, right, bottom) in A4 PDF points, measured from the top.
FIELDS = [
    field('lr_number', 'LR number', (453, 61, 579, 75), required=True, limit=64),
    field('lr_date', 'LR date', (453, 80, 579, 94), kind='date', required=True),
    field('vehicle_number', 'Vehicle number', (100, 96, 289, 111), required=True, limit=24),
    field('vehicle_type', 'Vehicle type', (396, 96, 579, 111), required=True, limit=40),
    field('driver_name', 'Driver name', (100, 113, 289, 125), limit=80),
    field('driver_contact', 'Driver contact', (396, 113, 579, 125), limit=30),
    field('consignor_name', 'Consignor name', (105, 128, 289, 141), required=True, limit=120, group='Consignor'),
    field('consignor_address', 'Consignor address', (105, 145, 289, 173), kind='textarea', required=True, limit=400, group='Consignor'),
    field('consignor_gstin', 'Consignor GSTIN', (105, 175, 289, 189), limit=15, group='Consignor'),
    field('consignor_contact', 'Consignor contact / mobile', (105, 192, 289, 204), limit=40, group='Consignor'),
    field('consignee_name', 'Consignee name', (380, 128, 579, 141), required=True, limit=120, group='Consignee'),
    field('consignee_address', 'Consignee address', (380, 145, 579, 173), kind='textarea', required=True, limit=400, group='Consignee'),
    field('consignee_gstin', 'Consignee GSTIN', (380, 175, 579, 189), limit=15, group='Consignee'),
    field('consignee_contact', 'Consignee contact / mobile', (380, 192, 579, 204), limit=40, group='Consignee'),
    field('packages', 'Number of packages', (38, 237, 96, 252), kind='integer', required=True, group='Cargo'),
    field('package_type', 'Package type', None, kind='package', required=True, group='Cargo'),
    field('goods_description', 'Description of cargo / goods', (101, 237, 319, 252), kind='textarea', required=True, limit=240, group='Cargo'),
    field('gross_weight', 'Gross weight (kg)', (322, 237, 378, 252), kind='weight', group='Cargo'),
    field('volume_weight', 'Volume weight (kg)', (382, 237, 444, 252), kind='weight', group='Cargo'),
    field('chargeable_weight', 'Chargeable weight (kg)', (448, 237, 520, 252), kind='weight', required=True, group='Cargo'),
    field('cargo_value', 'Cargo value (Rs)', (524, 237, 579, 252), kind='money', required=True, group='Cargo'),
    field('invoice_number', 'Invoice number', (17, 284, 121, 299), required=True, limit=64, group='Invoice & e-way bill'),
    field('invoice_date', 'Invoice date', (125, 284, 201, 299), kind='date', required=True, group='Invoice & e-way bill'),
    field('invoice_value', 'Invoice value (Rs)', (205, 284, 293, 299), kind='money', required=True, group='Invoice & e-way bill'),
    field('eway_bill_number', 'E-way bill number / reference', (297, 284, 402, 299), limit=64, group='Invoice & e-way bill'),
    field('eway_bill_date', 'E-way bill date', (406, 284, 494, 299), kind='date', group='Invoice & e-way bill'),
    field('eway_valid_until', 'E-way bill valid upto', (498, 284, 579, 299), kind='date', group='Invoice & e-way bill'),
    field('reporting_date', 'Reporting date', (108, 318, 293, 332), kind='date', group='Movement & remarks'),
    field('departure_date', 'Departure date', (368, 318, 579, 332), kind='date', group='Movement & remarks'),
    field('appointment_date', 'Appointment date', (108, 334, 293, 346), kind='date', group='Movement & remarks'),
    field('container_number', 'Container number', (368, 334, 579, 346), limit=40, group='Movement & remarks'),
    field('seal_1', 'Seal 1', (108, 348, 293, 360), limit=40, group='Movement & remarks'),
    field('seal_2', 'Seal 2', (368, 348, 579, 360), limit=40, group='Movement & remarks'),
    field('trip_number', 'Trip number', (108, 363, 293, 374), limit=40, group='Movement & remarks'),
    field('cargo_status', 'Cargo status', (368, 363, 579, 374), limit=80, group='Movement & remarks'),
    field('loading_remarks', 'Loading remarks', (108, 377, 579, 388), limit=180, group='Movement & remarks'),
    field('general_remarks', 'General remarks', (108, 391, 579, 402), limit=180, group='Movement & remarks'),
]

FIELD_NAMES = {item['name'] for item in FIELDS}
