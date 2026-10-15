"""Canonical POD/invoice names survive storage, history, backup and re-upload."""
import io
from pathlib import Path

import pytest
from PIL import Image

from app import orm as models
from app.models import db
from test_document_deletion import panel, storage, add, get_row
from test_er_dashboard import migrated_admin, populated, legacy_engine, disposable_engine
from test_shipment_documents import image_bytes, pdf_bytes
from test_complete_backup import unpack


def enable_listing(remote, monkeypatch):
    original = remote.from_
    def bucket(name):
        result = original(name)
        def listing(folder, options):
            found = {}
            for (stored_bucket, path) in remote.objects:
                if stored_bucket != name or not path.startswith(folder + '/'):
                    continue
                tail = path[len(folder) + 1:]
                child = tail.split('/', 1)[0]
                directory = '/' in tail
                found[child] = {'name': child, 'id': None if directory else child,
                                'metadata': None if directory else {}}
            ordered = [found[key] for key in sorted(found)]
            offset = options.get('offset', 0)
            return ordered[offset:offset + options.get('limit', 100)]
        result.list = listing
        return result
    monkeypatch.setattr(remote, 'from_', bucket)


def test_150_named_documents_roundtrip_through_storage_backup_and_bulk_review(panel, storage, monkeypatch):
    app, client = panel
    enable_listing(storage, monkeypatch)
    numbers = [f'NAMING-{index:03d}' for index in range(75)]
    assert client.post('/admin/consignments/save', json={'rows': [
        {'consignment_number': number} for number in numbers]}).status_code == 200
    uploads = [(number + '_pod.png', image_bytes()) for number in numbers]
    uploads += [(number + '_invoice.pdf', pdf_bytes()) for number in numbers]
    for offset in range(0, 150, 50):
        result = client.post('/admin/consignments/documents/bulk-upload', data={
            'apply': 'true', 'files': [(io.BytesIO(content), name) for name, content in uploads[offset:offset + 50]]})
        assert result.status_code == 200, result.json
        for document in result.json['documents']:
            assert Path(document['reference']).name == document['original_name']
    assert len(storage.objects) == 150
    assert {Path(path).name for _, path in storage.objects} == {name for name, _ in uploads}
    assert all(path.startswith('consignments/') and len(Path(path).parts) == 3 for _, path in storage.objects)
    archive, _, report = unpack(client.get('/admin/generate-backup'))
    documents = [entry for entry in report['documents'] if entry.get('consignment_number') in numbers]
    assert len(documents) == 150 and all(entry['status'] == 'included' for entry in documents)
    assert {Path(entry['archive_path']).name for entry in documents} == {name for name, _ in uploads}
    # Extracted backup files require no renaming to match the bulk uploader.
    before = dict(storage.objects)
    for offset in range(0, 150, 50):
        batch = documents[offset:offset + 50]
        result = client.post('/admin/consignments/documents/bulk-upload', data={
            'files': [(io.BytesIO(archive.read(entry['archive_path'])), Path(entry['archive_path']).name) for entry in batch]})
        assert result.status_code == 200 and result.json['count'] == 50
        assert all(item['replaces_existing'] for item in result.json['review'])
    assert storage.objects == before


def test_same_name_replacements_keep_distinct_versions_and_rollback(panel, storage, monkeypatch):
    app, client = panel
    enable_listing(storage, monkeypatch)
    row = add(client, 'NAMED-REPLACEMENT')
    original = row['pod_image']
    content = io.BytesIO(); Image.new('RGB', (8, 8), 'red').save(content, 'PNG')
    result = client.post(f"/admin/consignments/{row['id']}/pod", data={
        'file': (io.BytesIO(content.getvalue()), 'NAMED-REPLACEMENT_pod.png')})
    assert result.status_code == 200
    current = get_row(client, 'NAMED-REPLACEMENT')
    assert original != current['pod_image']
    assert Path(original).name == Path(current['pod_image']).name == 'NAMED-REPLACEMENT_pod.png'
    download = client.get(f"/admin/consignments/{row['id']}/pod")
    assert 'NAMED-REPLACEMENT_pod.png' in download.headers['Content-Disposition']
    archive, _, report = unpack(client.get('/admin/generate-backup'))
    with app.app_context():
        er = models.using_er()
    if er:
        history = [entry for entry in report['documents'] if entry.get('source_reference') == original]
        assert len(history) == 1 and Path(history[0]['archive_path']).name == 'NAMED-REPLACEMENT_pod.png'
        assert archive.read(history[0]['archive_path']) != download.data
    before = dict(storage.objects)
    def fail(): raise RuntimeError('Simulated commit failure')
    monkeypatch.setattr(db.session, 'commit', fail)
    failed = client.post(f"/admin/consignments/{row['id']}/pod", data={
        'file': (io.BytesIO(image_bytes()), 'NAMED-REPLACEMENT_pod.png')})
    assert failed.status_code == 500
    assert storage.objects == before
    assert get_row(client, 'NAMED-REPLACEMENT')['pod_image'] == current['pod_image']
