"""LR attachments share the real PostgreSQL document lifecycle and private storage."""
import base64
import importlib
from io import BytesIO
import json
from zipfile import ZipFile

import pytest
from pypdf import PdfReader
from sqlalchemy import event, select, text

from app.admin.reporting import analyze, normalize_filters
from app.models import db
from database.models import ShipmentDocument, File
from migrations.shipment_v1 import RUNS
from test_document_deletion import storage
from test_document_names import enable_listing
from test_shipment_documents import attachment, pdf_bytes

migration = importlib.import_module('migrations.003_lorry_receipts')


def save(client, identifier='LR-DOC-001', **fields):
    result = client.post('/admin/consignments/save', json={'rows': [{
        'consignment_number': identifier, 'pieces': 2, 'weight': '25',
        'pickup_date': '2026-10-19', 'pickup_address': 'Dispatch warehouse',
        'drop_address': 'Receiving warehouse', **fields,
    }]})
    assert result.status_code == 200, result.json
    return client.get('/admin/consignments/list', query_string={'search': identifier}).json['rows'][0]


def preview(client, rows):
    reviewed = client.post('/admin/lr-generator/review', json={'consignment_ids': [row['id'] for row in rows]})
    assert reviewed.status_code == 200, reviewed.json
    details = []
    for row in reviewed.json['shipments']:
        details.append(dict(row['values'], id=row['id'], consignor_name='Sender', consignee_name='Receiver',
                            vehicle_number='MH12AB1234', vehicle_type='32 Feet', goods_description='Packaged goods',
                            chargeable_weight='25', cargo_value='1000', invoice_value='1000',
                            invoice_date='2026-10-19', invoice_number='INV-' + row['consignment_number']))
    payload = dict(consignment_ids=[row['id'] for row in rows], lr_details=details, delivery='preview',
                   include_documents=True)
    result = client.post('/admin/lr-generator/generate', json=payload)
    assert result.status_code == 200, result.data[:100]
    return result, payload


def upload(client, document, *, expected_reference=None, content=None):
    return client.post(f"/admin/consignments/{document['id']}/lr", data={
        'file': (BytesIO(content if content is not None else base64.b64decode(document['pdf'])), document['filename']),
        'expected_reference': expected_reference or '',
    })


def test_lr_manual_upload_versioning_and_removal_preserve_other_documents(app, admin_client, storage):
    row = save(admin_client, **attachment('pod', pdf_bytes(), 'pod.pdf'), **attachment('invoice', pdf_bytes(), 'invoice.pdf'),
               **attachment('lr', pdf_bytes(), 'lorry.pdf'))
    assert row['lr_file'] and row['lr_original_name'] == 'lorry.pdf'
    assert row['lr_file'].startswith('supabase:lr-uploads/consignments/')
    result = admin_client.post(f"/admin/consignments/{row['id']}/lr", data={'file': (BytesIO(pdf_bytes()), 'replacement.pdf')})
    assert result.status_code == 200, result.json
    assert result.json['lr_file'] != row['lr_file']
    with app.app_context():
        docs = db.session.query(ShipmentDocument).filter_by(document_type='LR').order_by(ShipmentDocument.version).all()
        assert [(doc.version, doc.is_current) for doc in docs] == [(1, False), (2, True)]
    result = admin_client.delete(f"/admin/consignments/{row['id']}/lr", json={'expected_reference': result.json['lr_file']})
    assert result.status_code == 200
    current = admin_client.get('/admin/consignments/list').json['rows'][0]
    assert current['lr_file'] is None and current['pod_image'] == row['pod_image'] and current['invoice_file'] == row['invoice_file']
    assert len(storage.objects) == 2
    with app.app_context():
        assert db.session.query(ShipmentDocument).filter_by(document_type='LR').count() == 0


def test_generated_files_use_standard_upload_and_match_preview(app, admin_client, storage):
    rows = [save(admin_client, f'LR-UP-{n}') for n in range(2)]
    response, _ = preview(admin_client, rows)
    assert storage.objects == {}  # Preview has no storage or database document side effects.
    with app.app_context():
        assert db.session.query(ShipmentDocument).count() == 0
    assert response.headers['Cache-Control'] == 'no-store'
    generated = response.json
    original = PdfReader(BytesIO(base64.b64decode(generated['preview_pdf'])))
    assert len(original.pages) == 4
    for index, (row, document) in enumerate(zip(rows, generated['documents'], strict=True)):
        assert document['id'] == row['id']
        assert document['filename'] == row['consignment_number'] + '_lr.pdf'
        result = upload(admin_client, document)
        assert result.status_code == 200, result.json
        assert result.json['lr_file'].startswith('supabase:lr-uploads/')
        response = admin_client.get(f"/admin/consignments/{row['id']}/lr")
        reader = PdfReader(BytesIO(response.data))
        assert response.status_code == 200 and len(reader.pages) == 2
        assert row['consignment_number'] in reader.pages[0].extract_text()
        assert rows[1-index]['consignment_number'] not in reader.pages[0].extract_text()
        assert 'TERMS & CONDITIONS' in reader.pages[1].extract_text()
        assert reader.pages[0].extract_text() == original.pages[index * 2].extract_text()
        assert reader.pages[1].extract_text() == original.pages[index * 2 + 1].extract_text()
        # The ordinary endpoint prevents overwriting a document with a stale reference.
        assert upload(admin_client, document).status_code == 409
        replacement = upload(admin_client, document, expected_reference=result.json['lr_file'])
        assert replacement.status_code == 200
    with app.app_context():
        assert {file.mime_type for file in db.session.query(File).all()} == {'application/pdf'}
        assert {file.storage_bucket for file in db.session.query(File).all()} == {'lr-uploads'}
        docs = db.session.query(ShipmentDocument).filter_by(document_type='LR').all()
        assert sorted((doc.version, doc.is_current) for doc in docs) == [(1, False), (1, False), (2, True), (2, True)]


@pytest.mark.parametrize('invalid', [b'not a PDF', pdf_bytes(script=True), pdf_bytes(encrypted=True)])
def test_generated_upload_uses_standard_content_validation(app, admin_client, storage, invalid):
    row = save(admin_client)
    response, _ = preview(admin_client, [row])
    result = upload(admin_client, response.json['documents'][0], content=invalid)
    assert result.status_code == 400, result.json
    assert storage.objects == {}
    with app.app_context():
        assert db.session.query(ShipmentDocument).count() == 0


def test_generated_upload_failure_preserves_previous_and_other_completed_documents(app, admin_client, storage, monkeypatch):
    rows = [save(admin_client, f'LR-RETRY-{n}', **attachment('lr', pdf_bytes(), 'existing.pdf')) for n in range(2)]
    response, _ = preview(admin_client, rows)
    documents = response.json['documents']
    first = upload(admin_client, documents[0], expected_reference=rows[0]['lr_file'])
    assert first.status_code == 200
    baseline = dict(storage.objects)
    from app.admin import documents as document_routes
    original = document_routes.commit_document_cleanup
    def fail_commit(*args):
        raise RuntimeError('Simulated commit failure after storage upload')
    monkeypatch.setattr(document_routes, 'commit_document_cleanup', fail_commit)
    failed = upload(admin_client, documents[1], expected_reference=rows[1]['lr_file'])
    assert failed.status_code == 500
    assert storage.objects == baseline  # Failed upload is cleaned up; previous versions remain.
    current = {row['id']: row for row in admin_client.get('/admin/consignments/list').json['rows']}
    assert current[rows[0]['id']]['lr_file'] == first.json['lr_file']
    assert current[rows[1]['id']]['lr_file'] == rows[1]['lr_file']
    monkeypatch.setattr(document_routes, 'commit_document_cleanup', original)
    retried = upload(admin_client, documents[1], expected_reference=rows[1]['lr_file'])
    assert retried.status_code == 200
    with app.app_context():
        docs = db.session.query(ShipmentDocument).filter_by(document_type='LR').all()
        assert sorted((doc.version, doc.is_current) for doc in docs) == [(1, False), (1, False), (2, True), (2, True)]


def test_lr_bulk_documents_dashboard_mis_and_backup(app, admin_client, storage, monkeypatch):
    enable_listing(storage, monkeypatch)
    row = save(admin_client)
    with app.app_context():
        report = analyze(normalize_filters({'period': 'all'}))
        assert report['metrics']['missing_lr'] == 1 and report['document_counts']['lr'] == 1
        assert report['details'][0]['lr'] == 'No'
    response = admin_client.post('/admin/consignments/documents/bulk-upload', data={
        'apply': 'true', 'files': [(BytesIO(pdf_bytes()), row['consignment_number'] + '_lr.pdf')],
    })
    assert response.status_code == 200 and response.json['documents'][0]['kind'] == 'lr', response.json
    with app.app_context():
        report = analyze(normalize_filters({'period': 'all'}))
        assert report['metrics']['missing_lr'] == 0 and report['details'][0]['lr'] == 'Yes'
    for kind in ['lr', 'all']:
        response = admin_client.post('/admin/consignments/documents/bulk-download', json={'ids': [row['id']], 'kind': kind})
        assert response.status_code == 200
        assert row['consignment_number'] + '_lr.pdf' in ZipFile(BytesIO(response.data)).namelist()
    response = admin_client.get('/admin/generate-backup')
    assert response.status_code == 200
    archive = ZipFile(BytesIO(response.data))
    report = json.loads(archive.read('document_report.json'))
    assert any(item['kind'] == 'lr' and item['status'] == 'included' for item in report['documents'])
    for fmt in ('csv', 'xlsx', 'pdf'):
        response = admin_client.get('/admin/mis/export', query_string={'format': fmt, 'period': 'all'})
        assert response.status_code == 200
        if fmt == 'csv':
            assert b'Lorry receipt uploaded' in response.data
        elif fmt == 'xlsx':
            from openpyxl import load_workbook
            workbook = load_workbook(BytesIO(response.data))
            assert any('Lorry receipt uploaded' in tuple(cell.value for cell in row)
                       for sheet in workbook for row in sheet)
        else:
            assert 'LORRY RECEIPTS MISSING' in '\n'.join(page.extract_text() for page in PdfReader(BytesIO(response.data)).pages)
    assert b'Lorry Receipt' in admin_client.get('/admin/documents').data
    assert b'modal-lr-file' in admin_client.get('/admin/consignments').data


def test_migration_preserves_documents_is_repeatable_and_rolls_back_on_error(app, admin_client, storage):
    row = save(admin_client, **attachment('pod', pdf_bytes(), 'pod.pdf'))
    with app.app_context():
        db.session.remove()
        engine = db.engine
        with engine.begin() as c:
            c.execute(text(f'ALTER TABLE shipment_documents DROP CONSTRAINT {migration.CONSTRAINT}'))
            c.execute(text(f"ALTER TABLE shipment_documents ADD CONSTRAINT {migration.CONSTRAINT} CHECK (document_type IN ('POD', 'invoice'))"))
        before = dict(storage.objects)
        checked = app.test_cli_runner().invoke(args=['check-db'])
        assert checked.exit_code != 0 and 'migration 003' in checked.output
        assert migration.migrate(engine)['lr_supported'] is False
        assert migration.migrate(engine)['recorded'] is False
        def fail_history(connection, cursor, statement, parameters, context, many):
            if statement.startswith('INSERT INTO admin_migration_runs'):
                raise RuntimeError('History insert failure')
        event.listen(engine, 'before_cursor_execute', fail_history)
        try:
            with pytest.raises(RuntimeError, match='History insert failure'):
                migration.migrate(engine, apply=True)
        finally:
            event.remove(engine, 'before_cursor_execute', fail_history)
        assert migration.migrate(engine)['lr_supported'] is False
        assert migration.migrate(engine, apply=True)['status'] == 'applied'
        assert migration.migrate(engine, apply=True)['status'] == 'already_applied'
        assert migration.migrate(engine)['lr_supported'] is True
        assert app.test_cli_runner().invoke(args=['check-db']).exit_code == 0
        assert db.session.query(ShipmentDocument).count() == 1
        assert db.session.execute(select(RUNS.c.version).where(RUNS.c.version == migration.VERSION)).scalar_one() == migration.VERSION
        assert storage.objects == before
    assert admin_client.post(f"/admin/consignments/{row['id']}/lr", data={'file': (BytesIO(pdf_bytes()), 'lr.pdf')}).status_code == 200


def test_outdated_preview_and_viewer_cannot_replace_an_lr(app, admin_client, storage):
    row = save(admin_client)
    response, _ = preview(admin_client, [row])
    document = response.json['documents'][0]
    changed = admin_client.post(f"/admin/consignments/{row['id']}/lr", data={'file': (BytesIO(pdf_bytes()), 'manual.pdf')})
    assert changed.status_code == 200
    baseline = dict(storage.objects)
    assert upload(admin_client, document).status_code == 409
    from database.models import AdminUser
    with app.app_context():
        db.session.query(AdminUser).filter_by(username='admin').one().role = 'viewer'
        db.session.commit()
    assert upload(admin_client, document).status_code == 403
    assert storage.objects == baseline


def test_large_lr_upload_download_and_backup_never_spool_to_disk(app, admin_client, storage, monkeypatch):
    import tempfile
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject
    from openpyxl.worksheet import _writer
    enable_listing(storage, monkeypatch)
    row = save(admin_client)
    writer, output = PdfWriter(), BytesIO()
    page = writer.add_blank_page(width=595, height=842)
    stream = DecodedStreamObject()
    stream.set_data(b'% Document buffer remains in memory\n' * 40000)
    page[NameObject('/Contents')] = stream
    writer.write(output)
    content = output.getvalue()
    assert len(content) > 1024 * 1024

    def forbid_disk(*args, **kwargs):
        raise AssertionError('Document processing must not create local temporary files.')
    monkeypatch.setattr(tempfile, 'TemporaryFile', forbid_disk)
    monkeypatch.setattr(tempfile, 'NamedTemporaryFile', forbid_disk)
    monkeypatch.setattr(_writer, 'NamedTemporaryFile', forbid_disk)
    # Raw multipart avoids the test client's own disk-spooling transport.
    boundary = b'lr-memory-boundary'
    body = (b'--' + boundary + b'\r\nContent-Disposition: form-data; name="file"; filename="lr.pdf"\r\n'
            b'Content-Type: application/pdf\r\n\r\n' + content + b'\r\n--' + boundary + b'--\r\n')
    result = admin_client.post(f"/admin/consignments/{row['id']}/lr", data=body,
                              content_type='multipart/form-data; boundary=lr-memory-boundary')
    assert result.status_code == 200, result.json
    assert result.json['lr_file'].startswith('supabase:lr-uploads/')
    assert len(admin_client.get(f"/admin/consignments/{row['id']}/lr").data) > 1024 * 1024
    result = admin_client.post('/admin/consignments/documents/bulk-download', json={'ids': [row['id']], 'kind': 'lr'})
    assert result.status_code == 200
    result = admin_client.get('/admin/generate-backup')
    assert result.status_code == 200
    report = json.loads(ZipFile(BytesIO(result.data)).read('document_report.json'))
    assert any(item['kind'] == 'lr' and item['status'] == 'included' for item in report['documents'])
