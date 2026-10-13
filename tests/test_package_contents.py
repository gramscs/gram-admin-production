"""B2B boxes, per-box pieces and unit-safe historical volume upgrades."""
from decimal import Decimal
from pathlib import Path
import io
import json

import pytest
from openpyxl import load_workbook

from app import orm as models
from app.models import db
from label_helpers import label_form
from test_label_review import panel, create, pages, snapshot, migrated_admin, populated, legacy_engine, disposable_engine
from test_complete_backup import unpack


CONTENTS = {'kind': 'box', 'default_pieces': 10,
            'boxes': {'2': {'pieces': 8, 'note': 'Fragile & upright <glass>'}}}


def saved(client, **values):
    data = {'consignment_number': 'BOX-CONTENTS', 'pieces': 3, 'package_contents': CONTENTS,
            'measurement_mode': 'volume', 'chargeable_volume': '25.250', **values}
    response = client.post('/admin/consignments/save', json={'rows': [data]})
    assert response.status_code == 200, response.json
    return client.get('/admin/consignments/list?search=BOX-CONTENTS').json['rows'][0]


def test_per_box_counts_exceptions_persist_and_print_on_the_right_page(panel):
    app, client = panel
    row = saved(client)
    assert row['package_contents'] == CONTENTS
    assert row['pieces'] == 3 and row['volume_unit'] == 'ft3'
    before = snapshot(app)
    review = client.post('/admin/labels/review', json={'consignment_ids': [row['id']]}).json
    assert review['shipments'][0]['package_contents'] == CONTENTS
    output = pages(client.post('/admin/labels/generate', data=label_form([row['id']])))
    assert len(output) == 3
    for index, page in enumerate(output, 1):
        text = page.extract_text()
        assert 'PIECES\n' in text and 'BOX\n' in text and 'BOXES\n' not in text
        assert f'{index} of 3' in text and '25.250 ft³' in text and 'm³' not in text
        assert ('\n8\n' if index == 2 else '\n10\n') in text
        assert ('Fragile & upright <glass>' in text) == (index == 2)
    assert snapshot(app) == before
    # Older callers that omit contents must not erase them.
    result = client.post('/admin/consignments/save', json={'rows': [{'id': row['id'], 'consignment_number': row['consignment_number'], 'status': 'Delivered'}]})
    assert result.status_code == 200
    assert client.get('/admin/consignments/list?search=BOX-CONTENTS').json['rows'][0]['package_contents'] == CONTENTS


def test_box_weight_exceptions_persist_and_invoice_values_can_vary_per_box(panel):
    app, client = panel
    contents = {'kind': 'box', 'default_pieces': 10,
                'boxes': {'2': {'pieces': 8, 'note': 'Fragile', 'weight': '3.250'}}}
    row = saved(client, measurement_mode='weight', chargeable_weight='12.345', package_contents=contents)
    assert row['package_contents'] == contents
    reviewed = client.post('/admin/labels/review', json={'consignment_ids': [row['id']]}).json['shipments'][0]
    assert [box['weight'] for box in reviewed['boxes']] == ['12.345', '3.250', '12.345']
    data = label_form([row['id']])
    details = json.loads(data['label_details'])
    details[0]['box_invoice_amounts'] = ['6000.00', '50.25', '7000']
    data['label_details'] = json.dumps(details)
    output = pages(client.post('/admin/labels/generate', data=data))
    assert len(output) == 3
    assert '12.345 kg' in output[0].extract_text() and 'Rs 6,000.00' in output[0].extract_text()
    assert '3.250 kg' in output[1].extract_text() and 'Rs 50.25' in output[1].extract_text()
    assert '12.345 kg' in output[2].extract_text() and 'Rs 7,000.00' in output[2].extract_text()


@pytest.mark.parametrize('contents', [
    {'kind': 'bag'}, {'default_pieces': -1}, {'default_pieces': True}, {'default_pieces': '1.5'},
    {'boxes': {'4': {'pieces': 2}}}, {'boxes': {'02': {'pieces': 2}}},
    {'boxes': {'1': {'note': 'x' * 121}}}, {'boxes': {'1': {'note': 'two\nlines'}}},
    {'boxes': {'1': {'weight': -1}}}, {'boxes': {'1': {'weight': 'NaN'}}},
    {'boxes': {'1': {'weight': 'Infinity'}}}, {'boxes': {'1': {'weight': '1.0001'}}},
    {'boxes': {'1': {'weight': '1000000000'}}}, {'boxes': {'1': {'weight': True}}},
])
def test_bad_contents_rejected_without_partial_save(panel, contents):
    app, client = panel
    before = snapshot(app)
    response = client.post('/admin/consignments/save', json={'rows': [{'consignment_number': 'INVALID-CONTENTS', 'pieces': 3, 'package_contents': contents}]})
    assert response.status_code == 400
    assert snapshot(app) == before


def test_reducing_boxes_requires_explicit_removal_of_out_of_range_entries(panel):
    _, client = panel
    row = saved(client)
    response = client.post('/admin/consignments/save', json={'rows': [{**row, 'pieces': 1}]})
    assert response.status_code == 400
    assert client.get('/admin/consignments/list?search=BOX-CONTENTS').json['rows'][0]['pieces'] == 3
    response = client.post('/admin/consignments/save', json={'rows': [{**row, 'pieces': 1, 'package_contents': {'kind': 'box', 'default_pieces': 10, 'boxes': {}}}]})
    assert response.status_code == 200


def test_unknown_piece_counts_and_pallets_are_not_invented(panel):
    _, client = panel
    row = saved(client, package_contents={'kind': 'pallet', 'default_pieces': None, 'boxes': {}})
    for page in pages(client.post('/admin/labels/generate', data=label_form([row['id']]))):
        text = page.extract_text()
        assert 'PALLET\n' in text and 'PIECES\n' in text and '—' in text


def test_old_cbm_is_displayed_correctly_and_upgrade_converts_exactly_once(panel):
    app, client = panel
    row = saved(client)
    with app.app_context():
        stored = db.session.get(models.Consignment, models.record_id(row['id']))
        stored.chargeable_volume = Decimal('1.000')
        stored.volume_unit = None
        db.session.commit()
    before = snapshot(app)
    converted = client.get('/admin/consignments/list?search=BOX-CONTENTS').json['rows'][0]
    assert converted['chargeable_volume'] == '35.315' and converted['volume_unit'] == 'ft3'
    assert snapshot(app) == before, 'Read-only views must not mutate the database'
    for attempt in range(2):
        result = app.test_cli_runner().invoke(args=['upgrade-db'])
        assert result.exit_code == 0, result.output
        assert f'Converted {1 if attempt == 0 else 0} shipment volumes' in result.output
        with app.app_context():
            stored = db.session.get(models.Consignment, models.record_id(row['id']))
            assert stored.chargeable_volume == Decimal('35.315') and stored.volume_unit == 'ft3'
    result = client.post('/admin/consignments/save', json={'rows': [converted]})
    assert result.status_code == 200
    assert client.get('/admin/consignments/list?search=BOX-CONTENTS').json['rows'][0]['chargeable_volume'] == '35.315'


def test_box_contents_and_ft3_survive_excel_and_complete_backup(panel):
    app, client = panel
    saved(client)
    sheet = load_workbook(io.BytesIO(client.get('/admin/consignments/export.xlsx').data)).active
    rows = list(sheet.values)
    headers = rows[0]
    record = next(dict(zip(headers, values)) for values in rows[1:] if values[0] == 'BOX-CONTENTS')
    assert record['boxes_pallets'] == 3 and record['volume_unit'] == 'ft3' and record['volume'] == 25.25
    assert json.loads(record['package_contents']) == CONTENTS
    from openpyxl import Workbook
    book = Workbook(); book.active.append(headers)
    record['consignment_number'] = 'BOX-CONTENTS-IMPORTED'
    book.active.append([record.get(key) for key in headers])
    content = io.BytesIO(); book.save(content)
    response = client.post('/admin/consignments/import', data={'file': (io.BytesIO(content.getvalue()), 'contents.xlsx')}, follow_redirects=True)
    assert response.status_code == 200 and b'Added: 1' in response.data
    imported = client.get('/admin/consignments/list?search=BOX-CONTENTS-IMPORTED').json['rows'][0]
    assert imported['package_contents'] == CONTENTS and imported['chargeable_volume'] == '25.250'
    archive, payload, _ = unpack(client.get('/admin/generate-backup'))
    workbook = load_workbook(io.BytesIO(archive.read('admin-data.xlsx')))
    values = list(workbook['consignments'].values)
    records = [dict(zip(values[0], row)) for row in values[1:]]
    backed_up = next(row for row in records if row.get('consignment_number', row.get('identifier_value')) == 'BOX-CONTENTS')
    assert json.loads(backed_up['package_contents']) == CONTENTS and backed_up['volume_unit'] == 'ft3'


def test_volume_upgrade_rolls_back_all_rows_if_one_value_overflows(panel):
    app, client = panel
    row = saved(client)
    with app.app_context():
        stored = db.session.get(models.Consignment, models.record_id(row['id']))
        stored.chargeable_volume = Decimal('999999999.999')
        stored.volume_unit = None
        db.session.commit()
    before = snapshot(app)
    result = app.test_cli_runner().invoke(args=['upgrade-db'])
    assert result.exit_code != 0
    assert snapshot(app) == before


def test_complete_database_migration_keeps_contents_and_volume_unit(panel, tmp_path):
    app, client = panel
    saved(client)
    from app.er_adapter import prepare_backend, RecordExtras
    from database.models import Consignment
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from migrations.shipment_v1 import migrate
    from migrations.storage import Storage
    with app.app_context():
        source = db.engine
        with disposable_engine(source.dialect.name, tmp_path / 'copied-boxes.db') as target:
            migrate(target, Storage(Path(app.instance_path) / 'uploads'), source_engine=source, fresh_target=True)
            prepare_backend(target)
            with Session(target) as session:
                record = session.scalar(select(Consignment).where(Consignment.identifier_value == 'BOX-CONTENTS'))
                extras = session.get(RecordExtras, ('consignments', record.id)).data
                assert extras['volume_unit'] == 'ft3' and extras['package_contents'] == CONTENTS
                assert record.pieces == 3 and record.chargeable_volume == Decimal('25.250')


def test_legacy_database_upgrade_with_unassigned_company(legacy_engine, tmp_path, monkeypatch):
    from app import create_app
    from app.models import Consignment
    from sqlalchemy import insert, select
    with legacy_engine.begin() as connection:
        connection.execute(insert(Consignment.__table__).values(consignment_number='OLD-CBM', pieces=2, chargeable_volume='2.000'))
    monkeypatch.setenv('DATABASE_URL', legacy_engine.url.render_as_string(hide_password=False))
    application = create_app({'TESTING': True, 'INSTANCE_PATH': str(tmp_path / 'cli-instance'), 'AUTO_CREATE_TABLES': False, 'ADMIN_DATABASE_MODEL': 'legacy', 'RATELIMIT_ENABLED': False})
    try:
        for expected in (1, 0):
            result = application.test_cli_runner().invoke(args=['upgrade-db'])
            assert result.exit_code == 0, result.output
            assert f'Converted {expected} shipment volumes' in result.output
        with legacy_engine.connect() as connection:
            row = connection.execute(select(Consignment.__table__)).mappings().one()
            assert row['chargeable_volume'] == Decimal('70.629') and row['volume_unit'] == 'ft3'
    finally:
        with application.app_context():
            db.session.remove(); db.engine.dispose()
