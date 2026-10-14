import base64
import io
from pathlib import PurePosixPath

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from app.models import db
from app.admin import consignment_controller


def object_bytes(reference):
    bucket, path = consignment_controller._parse_supabase_pod_value(reference)
    return consignment_controller._get_supabase_client().storage.from_(bucket).download(path)


def object_key(reference):
    return consignment_controller._parse_supabase_pod_value(reference)


def pdf_bytes(script=False, encrypted=False):
    writer = PdfWriter()
    writer.add_blank_page(width=288, height=432)
    if script:
        writer.add_js("app.alert('script');")
    if encrypted:
        writer.encrypt('secret')
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def image_bytes(kind='PNG'):
    buffer = io.BytesIO()
    Image.new('RGB', (8, 8), 'green').save(buffer, format=kind)
    return buffer.getvalue()


def create_shipment(client, **fields):
    response = client.post('/admin/consignments/save', json={'rows': [{'consignment_number': 'DOC001', **fields}]})
    assert response.status_code == 200, response.get_json()
    return client.get('/admin/consignments/list').get_json()['rows'][0]


def attachment(kind, content, name, mime='application/octet-stream'):
    return {kind + '_file_data': 'data:' + mime + ';base64,' + base64.b64encode(content).decode(), kind + '_file_name': name, kind + '_file_type': mime}


@pytest.mark.parametrize('kind', ['pod', 'invoice'])
def test_pdf_document_save_private_download_replace_and_staged_remove(admin_client, app, isolated_storage, kind):
    row = create_shipment(admin_client, **attachment(kind, pdf_bytes(), 'delivery.pdf', 'text/html'))
    field = 'pod_image' if kind == 'pod' else 'invoice_file'
    old = row[field]
    assert old.startswith('supabase:')
    response = admin_client.get(f"/admin/consignments/{row['id']}/{kind}?preview=1")
    assert response.status_code == 200 and response.mimetype == 'application/pdf'
    assert response.headers['Content-Disposition'].startswith('attachment')
    assert f"{row['consignment_number']}_{kind}.pdf" in response.headers['Content-Disposition']
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'sandbox' in response.headers['Content-Security-Policy']
    assert len(PdfReader(io.BytesIO(response.data)).pages) == 1
    uploaded = admin_client.post(f"/admin/consignments/{row['id']}/{kind}", data={'file': (io.BytesIO(image_bytes()), 'new.png')})
    assert uploaded.status_code == 200 and object_key(old) not in isolated_storage.objects
    preview = admin_client.get(f"/admin/consignments/{row['id']}/{kind}?preview=1")
    assert preview.mimetype == 'image/png' and preview.headers['Content-Disposition'].startswith('inline')
    row = admin_client.get('/admin/consignments/list').get_json()['rows'][0]
    current = row[field]
    response = admin_client.post('/admin/consignments/save', json={'rows': [{'id': row['id'], 'consignment_number': row['consignment_number'], kind + '_remove': True}]})
    assert response.status_code == 200 and object_key(current) not in isolated_storage.objects
    assert admin_client.get(f"/admin/consignments/{row['id']}/{kind}").status_code == 404


@pytest.mark.parametrize('kind,ext,mime', [('JPEG', 'jpg', 'image/jpeg'), ('PNG', 'png', 'image/png'), ('WEBP', 'webp', 'image/webp')])
def test_image_formats_are_detected_and_reencoded(admin_client, kind, ext, mime):
    row = create_shipment(admin_client, **attachment('pod', image_bytes(kind), 'proof.' + ext))
    response = admin_client.get(f"/admin/consignments/{row['id']}/pod")
    assert response.mimetype == mime
    assert Image.open(io.BytesIO(response.data)).format == kind


@pytest.mark.parametrize('content,name', [(b'<svg onload="alert(1)"/>', 'proof.svg'), (b'<html>bad</html>', 'proof.png'), (image_bytes(), 'wrong.pdf'), (image_bytes(), 'wrong.jpg'), (b'%PDF-broken', 'broken.pdf'), (pdf_bytes(script=True), 'active.pdf'), (pdf_bytes(encrypted=True), 'locked.pdf'), (b'', 'empty.pdf'), (b'x' * (5 * 1024 * 1024+1), 'large.png')])
def test_invalid_document_never_replaces_saved_file(admin_client, app, isolated_storage, content, name):
    row = create_shipment(admin_client, **attachment('pod', image_bytes(), 'good.png'))
    before = dict(isolated_storage.objects)
    response = admin_client.post(f"/admin/consignments/{row['id']}/pod", data={'file': (io.BytesIO(content), name)})
    assert response.status_code == 400, response.get_json()
    assert isolated_storage.objects == before


def test_bad_later_row_does_not_delete_or_replace_earlier_documents(admin_client, app, isolated_storage):
    row = create_shipment(admin_client, **attachment('pod', image_bytes(), 'good.png'))
    before = dict(isolated_storage.objects)
    response = admin_client.post('/admin/consignments/save', json={'rows': [
        {'id': row['id'], 'consignment_number': row['consignment_number'], **attachment('pod', pdf_bytes(), 'replacement.pdf')},
        {'consignment_number': 'BAD002', **attachment('invoice', b'not a PDF', 'bad.pdf')},
    ]})
    assert response.status_code == 400
    assert isolated_storage.objects == before
    assert admin_client.get('/admin/consignments/list').get_json()['total'] == 1


def test_storage_failure_cleans_new_files_and_preserves_previous_documents(admin_client, app, isolated_storage, monkeypatch):
    row = create_shipment(admin_client, **attachment('pod', image_bytes(), 'old.png'))
    before = dict(isolated_storage.objects)
    from app.admin import consignment_controller
    original = consignment_controller._store_pod_bytes
    def failing_store(filename, *args):
        if PurePosixPath(filename).stem.endswith('_invoice'):
            raise RuntimeError('simulated storage failure')
        return original(filename, *args)
    monkeypatch.setattr(consignment_controller, '_store_pod_bytes', failing_store)
    response = admin_client.post('/admin/consignments/save', json={'rows': [{'id': row['id'], 'consignment_number': row['consignment_number'], **attachment('pod', pdf_bytes(), 'new.pdf'), **attachment('invoice', pdf_bytes(), 'invoice.pdf')}]})
    assert response.status_code == 500
    assert isolated_storage.objects == before
    assert admin_client.get('/admin/consignments/list').get_json()['rows'][0]['pod_image'] == row['pod_image']


def test_document_reference_injection_and_non_supabase_paths_are_rejected(admin_client, app):
    row = create_shipment(admin_client)
    for field in ('pod_image', 'invoice_file'):
        response = admin_client.post('/admin/consignments/save', json={'rows': [{'id': row['id'], 'consignment_number': row['consignment_number'], field: 'https://example.test/file.pdf'}]})
        assert response.status_code == 400
    with pytest.raises(ValueError, match='not stored in Supabase'):
        consignment_controller._parse_supabase_pod_value('../outside/private.pdf')


@pytest.mark.parametrize('kind', ['pod', 'invoice'])
@pytest.mark.parametrize('method', ['get', 'post', 'delete'])
def test_documents_require_login(client, kind, method):
    response = getattr(client, method)('/admin/consignments/1/' + kind, headers={'Accept': 'application/json'})
    assert response.status_code == 401


def test_cross_origin_document_change_is_rejected(admin_client):
    row = create_shipment(admin_client)
    response = admin_client.post(f"/admin/consignments/{row['id']}/invoice", data={'file': (io.BytesIO(pdf_bytes()), 'invoice.pdf')}, headers={'Origin': 'https://unrelated.example'})
    assert response.status_code == 403


def test_documents_are_only_in_form_not_table(admin_client):
    html = admin_client.get('/admin/consignments').get_data(as_text=True)
    head = html.split('<thead')[1].split('</thead>')[0]
    assert 'POD' not in head and 'Invoice' not in head
    assert 'modal-pod-file' in html and 'modal-invoice-file' in html
    assert 'image/*' not in html


def test_database_commit_failure_preserves_old_file(admin_client, app, isolated_storage, monkeypatch):
    row = create_shipment(admin_client, **attachment('pod', image_bytes(), 'old.png'))
    before = dict(isolated_storage.objects)
    def fail():
        raise RuntimeError('simulated database commit failure')
    monkeypatch.setattr(db.session, 'commit', fail)
    response = admin_client.post(f"/admin/consignments/{row['id']}/pod", data={'file': (io.BytesIO(pdf_bytes()), 'new.pdf')})
    assert response.status_code == 500
    assert isolated_storage.objects == before
    assert admin_client.get('/admin/consignments/list').get_json()['rows'][0]['pod_image'] == row['pod_image']


def test_image_metadata_is_removed(admin_client):
    from PIL.PngImagePlugin import PngInfo
    buffer = io.BytesIO()
    info = PngInfo()
    info.add_text('private note', 'remove this')
    Image.new('RGB', (8, 8)).save(buffer, format='PNG', pnginfo=info)
    row = create_shipment(admin_client, **attachment('pod', buffer.getvalue(), 'proof.png'))
    saved = admin_client.get(f"/admin/consignments/{row['id']}/pod")
    assert 'private note' not in Image.open(io.BytesIO(saved.data)).info


def test_private_supabase_document_roundtrip(admin_client, monkeypatch):
    from app.admin import consignment_controller
    objects = {}
    class Bucket:
        def upload(self, path, data, options):
            assert options['content-type'] == 'application/pdf'
            objects[path] = data
        def download(self, path):
            if path not in objects:
                raise FileNotFoundError('Object not found')
            return objects[path]
        def info(self, path):
            if path not in objects:
                raise FileNotFoundError('Object not found')
            return {'name': path}
        def remove(self, paths):
            for path in paths:
                objects.pop(path, None)
    class Storage:
        def from_(self, bucket):
            assert bucket == 'private-documents'
            return Bucket()
    class Client:
        storage = Storage()
    monkeypatch.setenv('SUPABASE_BUCKET', 'private-documents')
    monkeypatch.setenv('SUPABASE_INVOICE_BUCKET', 'private-documents')
    monkeypatch.setattr(consignment_controller, '_get_supabase_client', lambda: Client())
    row = create_shipment(admin_client, **attachment('invoice', pdf_bytes(), 'invoice.pdf'))
    assert row['invoice_file'].startswith('supabase:private-documents/consignments/')
    assert PurePosixPath(row['invoice_file']).name == f"{row['consignment_number']}_invoice.pdf"
    response = admin_client.get(f"/admin/consignments/{row['id']}/invoice")
    assert response.status_code == 200 and len(PdfReader(io.BytesIO(response.data)).pages) == 1
    assert admin_client.delete(f"/admin/consignments/{row['id']}/invoice").status_code == 200
    assert not objects


def test_shipment_deletion_only_removes_documents_after_successful_save(admin_client, app, isolated_storage):
    row = create_shipment(admin_client, **attachment('pod', image_bytes(), 'proof.png'), **attachment('invoice', pdf_bytes(), 'invoice.pdf'))
    assert len(isolated_storage.objects) == 2
    invalid = admin_client.post('/admin/consignments/save', json={'deleted_ids': [row['id']], 'rows': [{'consignment_number': ''}]})
    assert invalid.status_code == 400
    assert len(isolated_storage.objects) == 2
    assert admin_client.get('/admin/consignments/list').get_json()['total'] == 1
    deleted = admin_client.post('/admin/consignments/save', json={'deleted_ids': [row['id']], 'rows': []})
    assert deleted.status_code == 200 and deleted.get_json()['deleted_count'] == 1
    assert not isolated_storage.objects
