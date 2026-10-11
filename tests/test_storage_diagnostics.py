"""Storage checks are read-only; broken remote settings never redirect uploads."""
import io
import sys
from pathlib import Path

import pytest

from app.admin import consignment_controller as controller
from test_shipment_documents import create_shipment, attachment, image_bytes
from test_complete_backup import unpack


@pytest.mark.parametrize('url,key', [('https://test.supabase.co', ''), ('', 'private-test-key')])
@pytest.mark.parametrize('kind', ['pod', 'invoice'])
def test_incomplete_settings_reject_upload_and_preserve_existing_file(app, admin_client, monkeypatch, url, key, kind):
    field = 'pod_image' if kind == 'pod' else 'invoice_file'
    row = create_shipment(admin_client, **attachment(kind, image_bytes(), 'old.png'))
    old = Path(app.instance_path, 'uploads', row[field])
    before = old.read_bytes()
    monkeypatch.setenv('SUPABASE_URL', url)
    monkeypatch.setenv('SUPABASE_KEY', key)
    with pytest.raises(RuntimeError, match='Set both SUPABASE_URL and SUPABASE_KEY'):
        controller._get_supabase_client()
    result = admin_client.post('/admin/consignments/' + str(row['id']) + '/' + kind,
                               data={'file': (io.BytesIO(image_bytes()), 'new.png')})
    assert result.status_code == 500
    assert old.read_bytes() == before and list(old.parent.iterdir()) == [old]
    assert admin_client.get('/admin/consignments/list').json['rows'][0][field] == row[field]
    checked = app.test_cli_runner().invoke(args=['check-storage'])
    assert checked.exit_code != 0 and 'Set both' in checked.output and 'private-test-key' not in checked.output


@pytest.mark.parametrize('failure', ['missing_sdk', 'invalid_client'])
def test_remote_initialization_failure_does_not_fall_back_or_expose_keys(app, monkeypatch, failure):
    secret = 'PRIVATE-TEST-KEY-DO-NOT-PRINT'
    monkeypatch.setenv('SUPABASE_URL', 'https://test.supabase.co')
    monkeypatch.setenv('SUPABASE_KEY', secret)
    if failure == 'missing_sdk':
        monkeypatch.setitem(sys.modules, 'supabase', None)
    else:
        import supabase
        def invalid(*args):
            raise ValueError(secret)
        monkeypatch.setattr(supabase, 'create_client', invalid)
    with app.app_context(), pytest.raises(RuntimeError):
        controller._store_pod_bytes('never-created.png', image_bytes(), 'image/png')
    assert not list(Path(app.instance_path, 'uploads').glob('*'))
    result = app.test_cli_runner().invoke(args=['check-storage'])
    assert result.exit_code != 0 and secret not in result.output


def test_local_check_explains_database_and_storage_are_separate(app, monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', '')
    monkeypatch.setenv('SUPABASE_KEY', '')
    root = Path(app.instance_path, 'uploads')
    root.mkdir(exist_ok=True)
    original = root / 'existing.png'; original.write_bytes(image_bytes())
    before = original.read_bytes()
    result = app.test_cli_runner().invoke(args=['check-storage'])
    assert result.exit_code == 0 and 'Storage: local.' in result.output
    assert 'does not enable Supabase Storage' in result.output
    assert 'No files or database records were changed' in result.output
    assert original.read_bytes() == before and list(root.iterdir()) == [original]


def test_broken_remote_settings_still_allow_backup_of_local_files(app, admin_client, monkeypatch):
    row = create_shipment(admin_client, **attachment('pod', image_bytes(), 'existing.png'))
    original = Path(app.instance_path, 'uploads', row['pod_image']).read_bytes()
    monkeypatch.setenv('SUPABASE_URL', 'https://test.supabase.co')
    monkeypatch.setenv('SUPABASE_KEY', 'PRIVATE-TEST-KEY')
    def broken():
        raise RuntimeError('Invalid storage configuration')
    monkeypatch.setattr(controller, '_get_supabase_client', broken)
    response = admin_client.get('/admin/generate-backup')
    archive, data, report = unpack(response)
    assert response.headers['X-Backup-Status'] == 'incomplete'
    assert archive.read(data['consignments'][0]['pod_backup_path']) == original
    assert {item['bucket'] for item in report['inventory_errors']} == {'pod-uploads', 'invoice-uploads'}
    assert 'PRIVATE-TEST-KEY' not in str(report)


@pytest.mark.parametrize('fail_bucket', [None, 'invoice-uploads'])
def test_remote_checks_both_buckets_without_writing(app, monkeypatch, fail_bucket):
    reads = []
    class Bucket:
        def __init__(self, name): self.name = name
        def list(self, path, options):
            reads.append(('list', self.name, path, options))
            if self.name == fail_bucket: raise RuntimeError('PRIVATE-KEY-IN-REMOTE-ERROR')
            return []
        def upload(self, *args): raise AssertionError('Read-only command uploaded a file')
        def remove(self, *args): raise AssertionError('Read-only command removed a file')
    class Storage:
        def get_bucket(self, name): reads.append(('bucket', name))
        def from_(self, name): return Bucket(name)
    class Client:
        storage = Storage()
    monkeypatch.setattr(controller, '_get_supabase_client', lambda: Client())
    monkeypatch.setenv('SUPABASE_BUCKET', 'pod-uploads')
    monkeypatch.setenv('SUPABASE_INVOICE_BUCKET', 'invoice-uploads')
    result = app.test_cli_runner().invoke(args=['check-storage'])
    assert result.exit_code == (1 if fail_bucket else 0)
    assert ('list', 'pod-uploads', 'consignments', {'limit': 1}) in reads
    assert ('list', 'invoice-uploads', 'consignments', {'limit': 1}) in reads
    assert 'Upload permissions were not tested' in result.output
    assert 'PRIVATE-KEY-IN-REMOTE-ERROR' not in result.output
    assert 'POD: readable at pod-uploads/consignments' in result.output
