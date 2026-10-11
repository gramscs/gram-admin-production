"""Reviewed commercial labels, durable sequences and unchanged databases."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import io
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from pypdf import PdfReader
from sqlalchemy import inspect, text

from app import orm as models
from app.models import db
from app.admin import label_runs, labels
from test_er_dashboard import migrated_admin, populated, legacy_engine, disposable_engine
from test_complete_backup import unpack
from label_helpers import label_form


@pytest.fixture(params=['legacy', 'er'])
def panel(request, app, admin_client, migrated_admin):
    if request.param == 'legacy':
        return app, admin_client
    er_app, client, _, _ = migrated_admin
    return er_app, client


def create(client, count=1):
    rows = [{'consignment_number': f'LABEL-{index:03d}', 'identifier_type': 'AWB', 'pieces': 2,
        'pickup_date': '2026-10-09', 'pickup_address': '429 Jagraj Gram, Delhi',
        'drop_address': 'Ms Ashiya, Delhi', 'pickup_pincode': '110001', 'drop_pincode': '110037',
        'chargeable_weight': '100', 'chargeable_volume': '1.5'} for index in range(count)]
    saved = client.post('/admin/consignments/save', json={'rows': rows})
    assert saved.status_code == 200, saved.json
    found = client.get('/admin/consignments/list', query_string={'search': 'LABEL-', 'per_page': 100}).json['rows']
    return sorted(found, key=lambda row: row['consignment_number'])


def snapshot(app):
    with app.app_context():
        with db.engine.connect() as connection:
            return {name: [tuple(row) for row in connection.execute(text(f'SELECT * FROM "{name}"')).all()]
                    for name in sorted(inspect(db.engine).get_table_names())}


def pages(response):
    assert response.status_code == 200, response.json
    return PdfReader(io.BytesIO(response.data)).pages


def test_review_and_pdf_use_db_fields_manual_details_logo_and_no_db_writes(panel, monkeypatch):
    app, client = panel
    rows = create(client, 2)
    before = snapshot(app)
    review = client.post('/admin/labels/review', json={'consignment_ids': [row['id'] for row in rows]})
    assert review.status_code == 200 and review.json['shipments'][0]['pickup_date'] == '09/10/2026'
    data = label_form([row['id'] for row in rows])
    details = json.loads(data['label_details'])
    details[0].update(invoice_amount='6000.50', payment_mode='to-pay', eway_bill_exists=True, eway_bill_number='123456789012')
    details[1].update(invoice_amount='8000', payment_mode='cash-on-delivery', goods_description='Tyres & wheels <boxed>', eway_bill_number='ignore unchecked')
    data['label_details'] = json.dumps(details)
    monkeypatch.setattr(label_runs, 'order_date', lambda: date(2026, 10, 11))
    output = pages(client.post('/admin/labels/generate', data=data))
    assert len(output) == 4
    for index, page in enumerate(output):
        extracted = page.extract_text()
        assert 'GRAM SCS' not in extracted and 'GRAM B2B' not in extracted
        assert 'SHIPMENT /' not in extracted and 'AWB' not in extracted
        assert '09/10/2026' in extracted
        assert rows[index // 2]['consignment_number'] in extracted
        assert '100.000 kg' in extracted and '1.500 m3' in extracted
        assert 'PIECE' in extracted and f'{index % 2 + 1} of 2' in extracted
        assert f'B2B-20261011-{index // 2 + 1:04d}' in extracted
        assert f'B2B-2026-27-{index // 2 + 1:04d}' in extracted
        assert '429 Jagraj Gram' in extracted and 'Ms Ashiya' in extracted
        assert list(page.images), 'GRAM logo must be embedded'
        assert (float(page.mediabox.width), float(page.mediabox.height)) == (288, 432)
        if index < 2:
            assert 'Rs 6,000.50' in extracted and 'TO PAY' in extracted and 'E-WAY BILL: 123456789012' in extracted
        else:
            assert 'CASH ON DELIVERY' in extracted and 'Tyres & wheels <boxed>' in extracted
            assert 'E-WAY BILL' not in extracted and 'ignore unchecked' not in extracted
    assert snapshot(app) == before
    archive, _, report = unpack(client.get('/admin/generate-backup'))
    counter = next(item for item in report['documents'] if (item.get('source_reference') or '').endswith('.label-state/counters.json'))
    assert json.loads(archive.read(counter['archive_path']))['invoices']['2026-27'] == 2


def test_fifty_shipments_and_reprints_keep_one_number_per_shipment(panel, monkeypatch):
    app, client = panel
    rows = create(client, 50)
    ids = [row['id'] for row in rows]
    reviewed = client.post('/admin/labels/review', json={'consignment_ids': ids})
    assert reviewed.status_code == 200 and len(reviewed.json['shipments']) == 50
    monkeypatch.setattr(label_runs, 'order_date', lambda: date(2026, 10, 11))
    data = label_form(ids, delivery='preview')
    first = pages(client.post('/admin/labels/generate', data=data))
    second = pages(client.post('/admin/labels/generate', data={**data, 'delivery': 'download', 'barcode': '0'}))
    assert len(first) == len(second) == 100
    for index in range(50):
        code = f'B2B-20261011-{index + 1:04d}'
        assert code in first[index * 2].extract_text() and code in first[index * 2 + 1].extract_text()
        assert code in second[index * 2].extract_text()
    state = Path(app.instance_path, 'uploads', '.label-state', 'counters.json')
    assert json.loads(state.read_text()) == {'orders': {'20261011': 50}, 'invoices': {'2026-27': 50}}
    third = pages(client.post('/admin/labels/generate', data=label_form(ids[:1])))
    assert 'B2B-20261011-0051' in third[0].extract_text()
    assert 'B2B-2026-27-0051' in third[0].extract_text()


@pytest.mark.parametrize('change', [
    {'invoice_amount': ''}, {'invoice_amount': '-1'}, {'invoice_amount': 'NaN'},
    {'invoice_amount': 'Infinity'}, {'invoice_amount': '1.001'}, {'invoice_amount': True},
    {'invoice_amount': '1000000000'}, {'payment_mode': 'cash'}, {'goods_description': ''},
    {'goods_description': 'x' * 161}, {'eway_bill_exists': 'yes'},
    {'eway_bill_exists': True, 'eway_bill_number': '123'},
])
def test_invalid_label_details_do_not_allocate_or_touch_shipments(app, admin_client, change):
    row = create(admin_client)[0]
    before = snapshot(app)
    data = label_form([row['id']])
    details = json.loads(data['label_details']); details[0].update(change)
    data['label_details'] = json.dumps(details)
    rejected = admin_client.post('/admin/labels/generate', data=data)
    assert rejected.status_code == 400 and rejected.json['success'] is False
    assert not Path(app.instance_path, 'uploads', '.label-state').exists()
    assert snapshot(app) == before


def test_run_cannot_be_reused_for_different_details_or_owner(app, admin_client):
    row = create(admin_client)[0]
    data = label_form([row['id']])
    assert admin_client.post('/admin/labels/generate', data=data).status_code == 200
    details = json.loads(data['label_details']); details[0]['invoice_amount'] = '9'
    changed = admin_client.post('/admin/labels/generate', data={**data, 'label_details': json.dumps(details)})
    assert changed.status_code == 409
    with admin_client.session_transaction() as state:
        state['admin_username'] = 'another-admin'
    assert admin_client.post('/admin/labels/generate', data=data).status_code == 409


def test_render_failure_allocates_nothing(app, admin_client, monkeypatch):
    row = create(admin_client)[0]
    def fail(*args): raise RuntimeError('Simulated PDF error')
    monkeypatch.setattr(labels, 'generate_labels_pdf', fail)
    assert admin_client.post('/admin/labels/generate', data=label_form([row['id']])).status_code == 500
    assert not Path(app.instance_path, 'uploads', '.label-state', 'counters.json').exists()


def test_locked_sequences_parallel_requests_date_and_financial_year_rollovers(app, monkeypatch):
    monkeypatch.setattr(label_runs, 'order_date', lambda: date(2026, 3, 31))
    def allocate(index):
        with app.app_context():
            return label_runs.render_label_run(str(uuid4()), 'admin', [{'id': str(index)}], lambda assignments: assignments)[0]
    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = list(pool.map(allocate, range(20)))
    assert {run['order_id'] for run in runs} == {f'B2B-20260331-{number:04d}' for number in range(1, 21)}
    assert {run['invoice_number'] for run in runs} == {f'B2B-2025-26-{number:04d}' for number in range(1, 21)}
    monkeypatch.setattr(label_runs, 'order_date', lambda: date(2026, 4, 1))
    april = allocate(21)
    assert april['order_id'] == 'B2B-20260401-0001' and april['invoice_number'] == 'B2B-2026-27-0001'
    monkeypatch.setattr(label_runs, 'order_date', lambda: date(2026, 4, 2))
    next_day = allocate(22)
    assert next_day['order_id'] == 'B2B-20260402-0001' and next_day['invoice_number'] == 'B2B-2026-27-0002'


def test_numbering_survives_process_restart_and_rejects_corrupt_state(app):
    program = '''
import json, sys
from datetime import date
from flask import Flask
from app.admin import label_runs
app = Flask('label-restart-test', instance_path=sys.argv[1])
label_runs.order_date = lambda: date(2026, 10, 11)
with app.app_context():
    result = label_runs.render_label_run(sys.argv[2], 'admin', [{'id': 'shipment'}], lambda assignments: assignments)
print(json.dumps(result))
'''
    def restarted(identifier):
        result = subprocess.run([sys.executable, '-c', program, app.instance_path, identifier],
                                capture_output=True, text=True, check=True)
        return json.loads(result.stdout)[0]
    first_job = str(uuid4())
    first = restarted(first_job)
    assert restarted(first_job) == first
    second = restarted(str(uuid4()))
    assert second['order_id'] == 'B2B-20261011-0002'
    assert second['invoice_number'] == 'B2B-2026-27-0002'
    counters = Path(app.instance_path, 'uploads', '.label-state', 'counters.json')
    counters.write_text('{broken state')
    with app.app_context(), pytest.raises(json.JSONDecodeError):
        label_runs.render_label_run(str(uuid4()), 'admin', [{'id': 'shipment'}], lambda assignments: assignments)
    assert counters.read_text() == '{broken state', 'Corrupt numbering must never silently reset'


def test_review_limits_missing_ids_validation_origin_and_login(app, admin_client, client):
    row = create(admin_client)[0]
    for ids in [[], [999999], [row['id']] * 501]:
        assert admin_client.post('/admin/labels/review', json={'consignment_ids': ids}).status_code == 400
    assert admin_client.post('/admin/labels/review', json={'consignment_ids': [row['id']]}, headers={'Origin': 'https://other.test'}).status_code == 400
    assert admin_client.post('/admin/labels/generate', data={'consignment_ids': [row['id']]}).status_code == 400
    admin_client.get('/admin/logout')
    assert client.post('/admin/labels/review', json={'consignment_ids': [row['id']]}).status_code == 401
