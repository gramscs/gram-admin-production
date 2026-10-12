"""Storage metadata, not a cached GET, determines whether a file still exists."""
import pytest

from app.models import db
from test_document_deletion import panel, storage, add, get_row
from test_document_names import enable_listing
from test_complete_backup import unpack
from test_er_dashboard import migrated_admin, populated, legacy_engine, disposable_engine


@pytest.mark.parametrize('kind', ['pod', 'invoice'])
@pytest.mark.parametrize('via', ['editor', 'save_all'])
def test_cached_download_does_not_block_removal(panel, storage, kind, via):
    _, client = panel
    row = add(client, 'CACHED-DELETE')
    field = 'pod_image' if kind == 'pod' else 'invoice_file'
    bucket, path = row[field].removeprefix('supabase:').split('/', 1)
    original = client.get(f"/admin/consignments/{row['id']}/{kind}").data
    storage.cache_deleted_downloads = True
    if via == 'editor':
        response = client.delete(f"/admin/consignments/{row['id']}/{kind}", json={'expected_reference':row[field]})
    else:
        response = client.post('/admin/consignments/save', json={'rows':[{'id':row['id'],'consignment_number':row['consignment_number'],kind+'_remove':True}]})
    assert response.status_code == 200, response.json
    assert (bucket, path) not in storage.objects
    assert storage.from_(bucket).download(path) == original  # Provider's stale cache still responds.
    assert get_row(client, 'CACHED-DELETE')[field] is None
    assert client.get(f"/admin/consignments/{row['id']}/{kind}").status_code == 404


@pytest.mark.parametrize('kind', ['pod', 'invoice'])
def test_cached_missing_file_is_not_downloaded_or_backed_up(panel, storage, monkeypatch, kind):
    _, client = panel
    enable_listing(storage, monkeypatch)
    row = add(client, 'CACHED-MISSING')
    field = 'pod_image' if kind == 'pod' else 'invoice_file'
    bucket, path = row[field].removeprefix('supabase:').split('/', 1)
    original = client.get(f"/admin/consignments/{row['id']}/{kind}").data
    storage.cache_deleted_downloads = True
    storage.objects.pop((bucket, path))
    assert storage.from_(bucket).download(path) == original
    assert client.get(f"/admin/consignments/{row['id']}/{kind}").status_code == 404
    _, _, report = unpack(client.get('/admin/generate-backup'))
    item = next(item for item in report['documents'] if item.get('consignment_number')=='CACHED-MISSING' and item['kind']==kind)
    assert item['status']=='missing' and item['archive_path'] is None


@pytest.mark.parametrize('kind', ['pod', 'invoice'])
def test_sql_failure_restores_real_object_even_when_download_is_cached(panel, storage, monkeypatch, kind):
    _, client = panel
    row = add(client, 'CACHED-ROLLBACK')
    field = 'pod_image' if kind == 'pod' else 'invoice_file'
    bucket, path = row[field].removeprefix('supabase:').split('/', 1)
    original = client.get(f"/admin/consignments/{row['id']}/{kind}").data
    storage.cache_deleted_downloads = True
    def fail(): raise RuntimeError('Simulated SQL failure')
    monkeypatch.setattr(db.session, 'commit', fail)
    response = client.delete(f"/admin/consignments/{row['id']}/{kind}", json={})
    assert response.status_code==500 and 'could not be restored' not in response.json['message']
    assert storage.objects[bucket, path]==original
    assert get_row(client, 'CACHED-ROLLBACK')[field]==row[field]
