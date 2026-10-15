"""Archive copies use current shipment IDs, never opaque storage keys."""
import io
from pathlib import Path

import pytest

from app import orm as models
from app.models import db
from test_document_deletion import panel, storage, add, get_row
from test_document_names import enable_listing
from test_er_dashboard import migrated_admin, populated, legacy_engine, disposable_engine
from test_shipment_documents import image_bytes
from test_complete_backup import unpack


@pytest.mark.parametrize('identifier_type', ['LRN', 'Order ID', 'AWB'])
def test_old_keys_and_identifier_changes_use_shipment_filenames(panel, storage, monkeypatch, identifier_type):
    app, client = panel
    enable_listing(storage, monkeypatch)
    row = add(client, 'BEFORE-RENAME')
    with app.app_context():
        record = db.session.get(models.Consignment, models.record_id(row['id']))
        for kind, bucket, extension in [('pod', 'pod-uploads', '.png'), ('invoice', 'invoice-uploads', '.pdf')]:
            field = 'pod_image' if kind == 'pod' else 'invoice_file'
            old = getattr(record, field)
            old_bucket, old_path = old.removeprefix('supabase:').split('/', 1)
            content = storage.objects.pop((old_bucket, old_path))
            opaque_path = 'consignments/opaque-key-' + kind + extension
            storage.objects[bucket, opaque_path] = content
            if models.using_er():
                from app.er_adapter import File
                file = db.session.query(File).filter_by(storage_path=old).one()
                file.storage_path = 'supabase:' + bucket + '/' + opaque_path
            else:
                setattr(record, field, 'supabase:' + bucket + '/' + opaque_path)
        db.session.commit()
    updated = client.post('/admin/consignments/save', json={'rows': [{'id': row['id'], 'consignment_number': 'FINAL-123', 'identifier_type': identifier_type}]})
    assert updated.status_code == 200, updated.json
    before = dict(storage.objects)
    archive, payload, report = unpack(client.get('/admin/generate-backup'))
    entries = [item for item in report['documents'] if item.get('consignment_number') == 'FINAL-123']
    assert {Path(item['archive_path']).name for item in entries} == {'FINAL-123_pod.png', 'FINAL-123_invoice.pdf'}
    for item in entries:
        bucket, path = item['source_reference'].removeprefix('supabase:').split('/', 1)
        assert archive.read(item['archive_path']) == before[bucket, path]
    assert storage.objects == before
    assert get_row(client, 'FINAL-123')['identifier_type'] == identifier_type


def test_extensionless_keys_and_retained_versions_use_metadata(migrated_admin, storage, monkeypatch):
    app, client, _, _ = migrated_admin
    enable_listing(storage, monkeypatch)
    row = add(client, 'AWB-OPAQUE')
    assert client.post(f"/admin/consignments/{row['id']}/pod", data={'file': (io.BytesIO(image_bytes()), 'another.png')}).status_code == 200
    with app.app_context():
        from app.er_adapter import File
        files = db.session.query(File).filter(File.storage_path.like('supabase:%')).all()
        for index, file in enumerate(files):
            bucket, old_path = file.storage_path.removeprefix('supabase:').split('/', 1)
            path = f'consignments/opaque-{index}'
            storage.objects[bucket, path] = storage.objects.pop((bucket, old_path))
            file.storage_path = f'supabase:{bucket}/{path}'
            file.original_filename = 'original-without-extension'
        db.session.commit()
    before = dict(storage.objects)
    archive, _, report = unpack(client.get('/admin/generate-backup'))
    current = [entry for entry in report['documents'] if entry.get('consignment_number') == 'AWB-OPAQUE']
    assert {Path(entry['archive_path']).name for entry in current} == {'AWB-OPAQUE_pod.png', 'AWB-OPAQUE_invoice.pdf'}
    history = [entry for entry in report['documents'] if entry.get('source') == 'file_record' and entry.get('source_reference') in {f'supabase:{bucket}/{path}' for bucket, path in before}]
    assert len(history) == 1 and Path(history[0]['archive_path']).name == 'AWB-OPAQUE_pod.png'
    assert storage.objects == before


@pytest.mark.parametrize('kind', ['pod', 'invoice'])
def test_manual_supabase_cleanup_reports_missing_and_remove_clears_metadata(panel, storage, monkeypatch, kind):
    _, client = panel
    enable_listing(storage, monkeypatch)
    row = add(client, 'MANUAL-CLEANUP')
    field = 'pod_image' if kind == 'pod' else 'invoice_file'
    key = tuple(row[field].removeprefix('supabase:').split('/', 1))
    storage.objects.pop(key)
    before = dict(storage.objects)
    archive, _, report = unpack(client.get('/admin/generate-backup'))
    entry = next(item for item in report['documents'] if item.get('consignment_number') == 'MANUAL-CLEANUP' and item['kind'] == kind)
    assert entry['status'] == 'missing' and entry['archive_path'] is None
    assert client.get(f"/admin/consignments/{row['id']}/{kind}").status_code == 404
    assert client.delete(f"/admin/consignments/{row['id']}/{kind}", json={'expected_reference': row[field]}).status_code == 200
    assert get_row(client, 'MANUAL-CLEANUP')[field] is None
    assert storage.objects == before
