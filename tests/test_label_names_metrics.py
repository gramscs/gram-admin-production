"""Optional label names and public measurement names without schema changes."""
import io
import json

import pytest
from openpyxl import Workbook, load_workbook
from pypdf import PdfReader

from test_label_review import panel, create, snapshot, pages, migrated_admin, populated, legacy_engine, disposable_engine
from test_complete_backup import unpack
from label_helpers import label_form


@pytest.mark.parametrize('named', [False, True])
def test_optional_names_address_pin_order_and_measurement_caption(panel, named):
    app, client = panel
    rows = create(client, 2)
    for row in rows:
        updated = client.post('/admin/consignments/save', json={'rows': [{**row,
            'pickup_tag': 'Warehouse preset (not a sender)', 'drop_tag': 'Depot preset (not a receiver)',
            'pickup_address': 'Warehouse & dispatch <A>, Delhi 110001',
            'drop_address': '24 Distribution Road, Delhi 110037'}]})
        assert updated.status_code == 200, updated.json
    before = snapshot(app, include_label_runs=False)
    data = label_form([row['id'] for row in rows])
    details = json.loads(data['label_details'])
    for index, detail in enumerate(details):
        detail.update(sender_name='  Acme & Sons <HQ>  ' if named else '',
                      receiver_name=f'Receiver {index + 1}' if named else '   ')
    data['label_details'] = json.dumps(details)
    for index, page in enumerate(pages(client.post('/admin/labels/generate', data=data))):
        text = page.extract_text()
        assert 'DESTINATION PIN' not in text and 'PIN NOT SET' not in text and 'PIN:' not in text
        assert 'chargeable' not in text.lower()
        assert 'Warehouse preset' not in text and 'Depot preset' not in text
        assert 'Sender' not in text and 'Recipient' not in text
        assert text.count('110001') == text.count('110037') == 1
        assert ('VOLUME' if index >= 2 else 'WEIGHT') in text
        assert ('WEIGHT' if index >= 2 else 'VOLUME') not in text
        if named:
            assert text.index('Acme & Sons <HQ>') < text.index('Warehouse & dispatch <A>') < text.index('110001')
            assert text.index(f'Receiver {index // 2 + 1}') < text.index('24 Distribution Road') < text.index('110037')
        else:
            assert 'Acme' not in text and 'Receiver' not in text
    assert snapshot(app, include_label_runs=False) == before


def test_pin_missing_from_address_is_appended_once(panel):
    _, client = panel
    row = create(client)[0]
    page = pages(client.post('/admin/labels/generate', data=label_form([row['id']])))[0]
    text = page.extract_text()
    assert text.count('110001') == text.count('110037') == 1
    assert text.index('429 Jagraj Gram') < text.index('110001')
    assert text.index('Ms Ashiya') < text.index('110037')
    pin_sizes = []
    page.extract_text(visitor_text=lambda text, cm, tm, font, size: pin_sizes.append(size) if '110037' in text else None)
    assert pin_sizes and max(pin_sizes) <= 11


def test_blank_names_preserve_existing_label_run(panel):
    _, client = panel
    row = create(client)[0]
    data = label_form([row['id']])
    old_text = pages(client.post('/admin/labels/generate', data=data))[0].extract_text()
    details = json.loads(data['label_details']); details[0].update(sender_name='', receiver_name=' ')
    data['label_details'] = json.dumps(details)
    assert pages(client.post('/admin/labels/generate', data=data))[0].extract_text() == old_text


@pytest.mark.parametrize('headers', [('weight', 'volume'), ('weight', 'volume')])
def test_new_and_legacy_excel_imports_export_with_plain_measurement_names(admin_client, headers):
    book = Workbook(); sheet = book.active
    sheet.append(['consignment_number', 'measurement_mode', *headers])
    sheet.append(['IMPORT-METRICS', 'volume', 12.345, .678])
    buffer = io.BytesIO(); book.save(buffer)
    imported = admin_client.post('/admin/consignments/import', data={'file': (io.BytesIO(buffer.getvalue()), 'shipments.xlsx')}, follow_redirects=True)
    assert imported.status_code == 200 and b'Added: 1' in imported.data
    for endpoint in ('export.xlsx', 'import-template.xlsx'):
        response = admin_client.get('/admin/consignments/' + endpoint)
        sheet = load_workbook(io.BytesIO(response.data)).active
        names = [cell.value for cell in sheet[1]]
        assert 'weight' in names and 'volume' in names
        assert not any('chargeable' in str(name).lower() for name in names)
        if endpoint == 'export.xlsx':
            row = dict(zip(names, [cell.value for cell in sheet[2]]))
            assert row['weight'] == 12.345 and row['volume'] == .678
    archive, payload, _ = unpack(admin_client.get('/admin/generate-backup'))
    workbook = load_workbook(io.BytesIO(archive.read('admin-data.xlsx')))
    assert not any('chargeable' in str(cell.value).lower() for sheet in workbook for cell in sheet[1])
    # The machine-readable backup retains database keys for restoration.
    assert float(payload['consignments'][0]['weight']) == 12.345


@pytest.mark.parametrize('field,title', [('weight', 'Weight'), ('volume', 'Volume')])
def test_invalid_measurement_error_uses_public_name(admin_client, field, title):
    response = admin_client.post('/admin/consignments/save', json={'rows': [{'consignment_number': 'BAD-METRIC', field: '-1'}]})
    assert response.status_code == 400
    message = response.json['errors'][0]['message']
    assert title in message and 'chargeable' not in message.lower()
