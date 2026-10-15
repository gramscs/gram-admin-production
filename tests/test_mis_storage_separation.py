"""New saved reports do not populate the POD or invoice bucket."""
import pytest

from app import orm as models
from app.models import db
from test_document_deletion import panel, storage, add
from test_document_names import enable_listing
from test_er_dashboard import migrated_admin, populated, legacy_engine, disposable_engine
from test_complete_backup import unpack


def test_reports_use_private_dedicated_storage_and_backup(panel, storage, monkeypatch):
    app, client = panel
    enable_listing(storage, monkeypatch)
    row = add(client, 'REPORT-CLIENT')
    before = dict(storage.objects)
    monkeypatch.setenv('SUPABASE_MIS_BUCKET', 'mis-reports')
    generated = client.post('/admin/mis/reports', json={'name':'Management report', 'format':'pdf',
        'filters':{'period':'all'}, 'columns':['consignment_number']})
    assert generated.status_code == 200, generated.json
    with app.app_context():
        report = db.session.get(models.MisReport, models.record_id(generated.json['id']))
        reference = report.file_ref
    assert reference.startswith('supabase:mis-reports/mis_reports/')
    assert {key:value for key,value in storage.objects.items() if key[0] in ('pod-uploads','invoice-uploads')} == before
    download = client.get(generated.json['download_url'])
    assert download.status_code == 200 and download.data.startswith(b'%PDF')
    archive, _, inventory = unpack(client.get('/admin/generate-backup'))
    item = next(item for item in inventory['documents'] if item.get('source_reference') == reference)
    assert archive.read(item['archive_path']) == download.data
    deleted = client.delete('/admin/mis/reports/'+str(generated.json['id']), json={})
    assert deleted.status_code == 200, deleted.json
    assert storage.objects == before


@pytest.mark.parametrize('bucket', ['pod-uploads','invoice-uploads'])
def test_report_configuration_rejects_reusing_document_buckets(panel, storage, monkeypatch, bucket):
    _, client = panel
    monkeypatch.setenv('SUPABASE_MIS_BUCKET', bucket)
    before = dict(storage.objects)
    response = client.post('/admin/mis/reports', json={'name':'Wrong report bucket','format':'pdf'})
    assert response.status_code == 400 and 'separate MIS bucket' in response.json['message']
    assert storage.objects == before


def test_backup_includes_unlinked_uploads_in_dedicated_report_namespace(panel, storage, monkeypatch):
    _, client = panel
    enable_listing(storage, monkeypatch)
    monkeypatch.setenv('SUPABASE_URL', 'https://test.supabase.co')
    monkeypatch.setenv('SUPABASE_KEY', 'private-test-placeholder')
    monkeypatch.setenv('SUPABASE_MIS_BUCKET', 'mis-reports')
    reference = 'supabase:mis-reports/mis_reports/older-report.pdf'
    content = b'%PDF-retained-unlinked-report'
    storage.objects['mis-reports', 'mis_reports/older-report.pdf'] = content
    before = dict(storage.objects)
    archive, _, inventory = unpack(client.get('/admin/generate-backup'))
    item = next(item for item in inventory['documents'] if item.get('source_reference') == reference)
    assert item['status'] == 'included' and item['archive_path'].startswith('other_uploads/')
    assert archive.read(item['archive_path']) == content
    assert not inventory['inventory_errors'] and storage.objects == before


def test_remote_report_setting_without_credentials_does_not_save_locally(app, admin_client, monkeypatch):
    monkeypatch.setenv('SUPABASE_MIS_BUCKET', 'mis-reports')
    response = admin_client.post('/admin/mis/reports', json={'name': 'Not configured', 'format': 'pdf'})
    assert response.status_code == 500
    with app.app_context():
        assert models.MisReport.query.count() == 0
    checked = app.test_cli_runner().invoke(args=['check-storage'])
    assert checked.exit_code != 0 and 'configured report bucket' in checked.output
