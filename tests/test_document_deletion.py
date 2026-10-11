"""Explicit removal must synchronize SQL and storage, including ER history."""
import io
import json
from pathlib import Path

import pytest
from storage3.exceptions import StorageApiError

from app import orm as models
from app.models import db
from app.admin import consignment_controller as controller
from test_shipment_documents import attachment, image_bytes, pdf_bytes
from test_er_dashboard import migrated_admin, populated, legacy_engine, disposable_engine


@pytest.fixture(params=['legacy', 'er'])
def panel(request, app, admin_client, migrated_admin):
    if request.param == 'legacy':
        return app, admin_client
    er_app, client, _, _ = migrated_admin
    return er_app, client


@pytest.fixture
def storage(monkeypatch):
    class Remote:
        objects = None
        failure = None
        failure_path = None
        removed = None
        def __init__(self):
            self.objects, self.removed = {}, []
            self.storage = self
        def from_(self, name):
            remote = self
            class Bucket:
                def upload(self, path, data, options):
                    if remote.failure == 'restore' and options.get('upsert') == 'true':
                        raise RuntimeError('PRIVATE-PROVIDER-KEY')
                    remote.objects[name, path] = bytes(data)
                def download(self, path):
                    try:
                        return remote.objects[name, path]
                    except KeyError:
                        raise StorageApiError('Object not found', 'not_found', 400) from None
                def remove(self, paths):
                    for path in paths:
                        remote.removed.append((name, path))
                        if remote.failure and (remote.failure_path is None or path == remote.failure_path):
                            if remote.failure == 'noop':
                                return []  # Simulate an RLS DELETE denied without an error.
                            if remote.failure == 'raise':
                                raise RuntimeError('PRIVATE-PROVIDER-KEY')
                        remote.objects.pop((name, path), None)
                    return [{'name': path} for path in paths]
            return Bucket()
    remote = Remote()
    monkeypatch.setattr(controller, '_get_supabase_client', lambda: remote)
    monkeypatch.setenv('SUPABASE_BUCKET', 'pod-uploads')
    monkeypatch.setenv('SUPABASE_INVOICE_BUCKET', 'invoice-uploads')
    return remote


def get_row(client, identifier='DEMO0017'):
    rows = client.get('/admin/consignments/list', query_string={'search': identifier}).json['rows']
    return next(row for row in rows if row['consignment_number'] == identifier)


def add(client, identifier='DEMO0017', both=True):
    fields = attachment('pod', image_bytes(), 'proof.png')
    if both:
        fields.update(attachment('invoice', pdf_bytes(), 'invoice.pdf'))
    result = client.post('/admin/consignments/save', json={'rows': [
        {'consignment_number': identifier, 'identifier_type': 'AWB', 'pieces': 3, **fields}]})
    assert result.status_code == 200, result.json
    return get_row(client, identifier)


def replace(client, row, kind='pod'):
    result = client.post(f"/admin/consignments/{row['id']}/{kind}",
                         data={'file': (io.BytesIO(pdf_bytes()), 'replacement.pdf')})
    assert result.status_code == 200, result.json
    return get_row(client, row['consignment_number'])


def assert_er_purged(app, identifier, paths, kind='POD'):
    with app.app_context():
        if not models.using_er():
            return
        from app.er_adapter import File, ShipmentDocument, AuditLog
        shipment = models.Consignment.query.filter_by(consignment_number=identifier).first()
        if shipment:
            assert not db.session.query(ShipmentDocument).filter_by(consignment_id=shipment.id, document_type=kind).all()
        assert not db.session.query(File).filter(File.storage_path.in_(paths)).all()
        assert db.session.query(AuditLog).filter_by(entity_type='shipment_documents', action='delete').count()


@pytest.mark.parametrize('kind', ['pod', 'invoice'])
@pytest.mark.parametrize('via', ['editor', 'save_all'])
def test_explicit_removal_purges_versions_and_only_selected_kind(panel, storage, kind, via):
    app, client = panel
    row = add(client)
    original = dict(row)
    row = replace(client, row, kind)
    field = 'pod_image' if kind == 'pod' else 'invoice_file'
    paths = {original[field], row[field]}
    expected = {key: data for key, data in storage.objects.items() if key[0] != ('pod-uploads' if kind == 'pod' else 'invoice-uploads')}
    if via == 'editor':
        result = client.delete(f"/admin/consignments/{row['id']}/{kind}", json={'expected_reference': row[field]})
    else:
        result = client.post('/admin/consignments/save', json={'rows': [
            {'id': row['id'], 'consignment_number': row['consignment_number'], kind + '_remove': True}]})
    assert result.status_code == 200, result.json
    assert storage.objects == expected
    current = get_row(client)
    assert current[field] is None and current['pieces'] == 3 and current['identifier_type'] == 'AWB'
    other_field = 'invoice_file' if kind == 'pod' else 'pod_image'
    assert current[other_field] == row[other_field]
    assert client.get(f"/admin/consignments/{row['id']}/{kind}").status_code == 404
    assert_er_purged(app, row['consignment_number'], paths, 'POD' if kind == 'pod' else 'invoice')


def test_shipment_delete_purges_all_versions_after_validation(panel, storage):
    app, client = panel
    old = add(client)
    row = replace(client, old)
    paths = [old['pod_image'], old['invoice_file'], row['pod_image']]
    before = dict(storage.objects)
    invalid = client.post('/admin/consignments/save', json={'rows': [{'consignment_number': ''}], 'deleted_ids': [row['id']]})
    assert invalid.status_code == 400 and storage.objects == before
    deleted = client.post('/admin/consignments/save', json={'rows': [], 'deleted_ids': [row['id']]})
    assert deleted.status_code == 200, deleted.json
    assert not storage.objects
    assert_er_purged(app, row['consignment_number'], paths)


@pytest.mark.parametrize('failure', ['raise', 'noop', 'unconfigured'])
@pytest.mark.parametrize('via', ['editor', 'save_all', 'shipment'])
@pytest.mark.parametrize('kind', ['pod', 'invoice'])
def test_failed_storage_delete_never_reports_success_or_drops_database_links(panel, storage, monkeypatch, failure, via, kind):
    app, client = panel
    row = add(client)
    before = dict(storage.objects)
    storage.failure = failure
    if failure == 'unconfigured':
        monkeypatch.setattr(controller, '_get_supabase_client', lambda: None)
    if via == 'editor':
        result = client.delete(f"/admin/consignments/{row['id']}/{kind}", json={})
    elif via == 'shipment':
        result = client.post('/admin/consignments/save', json={'rows': [], 'deleted_ids': [row['id']]})
    else:
        result = client.post('/admin/consignments/save', json={'rows': [{
            'id': row['id'], 'consignment_number': row['consignment_number'], 'pieces': 9, kind + '_remove': True}]})
    assert result.status_code == 500 and result.json['success'] is False
    assert 'PRIVATE-PROVIDER-KEY' not in result.get_data(as_text=True)
    assert storage.objects == before
    current = get_row(client)
    assert current['pod_image'] == row['pod_image'] and current['invoice_file'] == row['invoice_file'] and current['pieces'] == 3


def test_second_file_delete_failure_restores_first_object_and_entire_sql_change(panel, storage):
    _, client = panel
    row = add(client)
    before = dict(storage.objects)
    # Sorted references delete invoice first, then POD; fail only the latter.
    storage.failure, storage.failure_path = 'raise', row['pod_image'].split('/', 1)[1]
    result = client.post('/admin/consignments/save', json={'rows': [], 'deleted_ids': [row['id']]})
    assert result.status_code == 500
    assert len(storage.removed) == 2 and storage.objects == before
    assert get_row(client)['pod_image'] == row['pod_image']
    assert client.get(f"/admin/consignments/{row['id']}/invoice").data == next(value for (bucket, _), value in before.items() if bucket == 'invoice-uploads')


@pytest.mark.parametrize('remote', [True, False])
@pytest.mark.parametrize('kind', ['pod', 'invoice'])
def test_database_commit_failure_restores_deleted_bytes(panel, storage, monkeypatch, remote, kind):
    app, client = panel
    if not remote:
        monkeypatch.setattr(controller, '_get_supabase_client', lambda: None)
    row = add(client)
    field = 'pod_image' if kind == 'pod' else 'invoice_file'
    before = client.get(f"/admin/consignments/{row['id']}/{kind}").data
    def fail():
        raise RuntimeError('Simulated SQL failure')
    monkeypatch.setattr(db.session, 'commit', fail)
    result = client.delete(f"/admin/consignments/{row['id']}/{kind}", json={})
    assert result.status_code == 500
    assert get_row(client)[field] == row[field]
    assert client.get(f"/admin/consignments/{row['id']}/{kind}").data == before
    if not remote:
        assert Path(app.instance_path, 'uploads', row[field].removeprefix('local:')).read_bytes() == before


@pytest.mark.parametrize('bucket', ['invoice-uploads', 'client-invoices'])
def test_invoice_upload_download_replace_remove_uses_supabase_not_instance(panel, storage, monkeypatch, bucket):
    app, client = panel
    monkeypatch.setenv('SUPABASE_INVOICE_BUCKET', bucket)
    local_before = {path for path in Path(app.instance_path, 'uploads').rglob('*') if path.is_file()}
    original = add(client)
    assert original['invoice_file'].startswith(f'supabase:{bucket}/consignments/invoice-')
    assert original['pod_image'].startswith('supabase:pod-uploads/consignments/pod-')
    assert client.get(f"/admin/consignments/{original['id']}/invoice").mimetype == 'application/pdf'
    # A later setting change must not change the location of an existing file.
    monkeypatch.setenv('SUPABASE_INVOICE_BUCKET', 'next-invoices')
    assert client.get(f"/admin/consignments/{original['id']}/invoice").status_code == 200
    current = replace(client, original, 'invoice')
    assert current['invoice_file'].startswith('supabase:next-invoices/consignments/invoice-')
    result = client.delete(f"/admin/consignments/{original['id']}/invoice", json={'expected_reference': current['invoice_file']})
    assert result.status_code == 200, result.json
    assert get_row(client)['invoice_file'] is None
    assert {name for name, _ in storage.objects} == {'pod-uploads'}
    assert_er_purged(app, 'DEMO0017', [original['invoice_file'], current['invoice_file']], 'invoice')
    assert {path for path in Path(app.instance_path, 'uploads').rglob('*') if path.is_file()} == local_before


def test_irrecoverable_restore_is_reported_truthfully(panel, storage, monkeypatch):
    app, client = panel
    row = add(client, both=False)
    original = client.get(f"/admin/consignments/{row['id']}/pod").data
    storage.failure = 'restore'
    def fail(): raise RuntimeError('Simulated SQL failure')
    monkeypatch.setattr(db.session, 'commit', fail)
    result = client.delete(f"/admin/consignments/{row['id']}/pod", json={})
    assert result.status_code == 500 and 'could not be restored' in result.json['message']
    assert get_row(client)['pod_image'] == row['pod_image']
    copies = list(Path(app.instance_path, 'uploads', '.document-recovery').glob('*/reference.json'))
    assert len(copies) == 1
    manifest = json.loads(copies[0].read_text())
    assert manifest['storage_reference'] == row['pod_image']
    assert (copies[0].parent / manifest['recovery_file']).read_bytes() == original
    from test_complete_backup import unpack
    archive, _, report = unpack(client.get('/admin/generate-backup'))
    recovery = next(item for item in report['documents']
                    if '.document-recovery/' in (item.get('source_reference') or '')
                    and item['source_reference'].endswith(manifest['recovery_file']))
    assert recovery['status'] == 'included' and archive.read(recovery['archive_path']) == original


def test_already_missing_object_allows_stale_metadata_removal(panel, storage):
    _, client = panel
    row = add(client, both=False)
    storage.objects.clear()
    result = client.delete(f"/admin/consignments/{row['id']}/pod", json={})
    assert result.status_code == 200, result.json
    assert get_row(client)['pod_image'] is None


def test_shared_file_survives_until_last_attachment_is_removed(panel, storage):
    app, client = panel
    first = add(client, both=False)
    second = add(client, 'DEMO0017-OTHER', both=False)
    assert client.delete(f"/admin/consignments/{second['id']}/pod", json={}).status_code == 200
    with app.app_context():
        row = db.session.get(models.Consignment, models.record_id(second['id']))
        row.pod_image = first['pod_image']
        db.session.commit()
    before = dict(storage.objects)
    result = client.delete(f"/admin/consignments/{first['id']}/pod", json={})
    assert result.status_code == 200 and storage.objects == before
    assert get_row(client, 'DEMO0017-OTHER')['pod_image'] == first['pod_image']
    assert client.get(f"/admin/consignments/{second['id']}/pod").status_code == 200
    assert client.delete(f"/admin/consignments/{second['id']}/pod", json={}).status_code == 200
    assert not storage.objects


@pytest.mark.parametrize('delete_first', ['report', 'document'])
def test_file_shared_with_mis_report_is_kept_until_both_are_removed(panel, storage, delete_first):
    app, client = panel
    row = add(client, both=False)
    generated = client.post('/admin/mis/reports', json={'name': 'Shared PDF', 'format': 'pdf',
        'filters': {'period': 'all'}, 'columns': ['consignment_number']})
    assert generated.status_code == 200, generated.json
    report_id = generated.json['id']
    with app.app_context():
        report = db.session.get(models.MisReport, models.record_id(report_id))
        reference = report.file_ref
        db.session.get(models.Consignment, models.record_id(row['id'])).pod_image = reference
        db.session.commit()
    # Attachments made directly above can leave an unused old file on legacy;
    # the live shared object itself must survive either removal order.
    shared_key = tuple(reference.removeprefix('supabase:').split('/', 1))
    original = storage.objects[shared_key]
    urls = {'report': f'/admin/mis/reports/{report_id}', 'document': f"/admin/consignments/{row['id']}/pod"}
    first = client.delete(urls[delete_first], json={})
    assert first.status_code == 200, first.json
    assert storage.objects[shared_key] == original
    second = client.delete(urls['document' if delete_first == 'report' else 'report'], json={})
    assert second.status_code == 200, second.json
    assert shared_key not in storage.objects


@pytest.mark.parametrize('code,message,status,missing', [
    ('not_found', 'Object not found', 400, True),
    ('NoSuchKey', 'The resource was not found', 404, True),
    ('not_found', 'Bucket not found', 404, False),
    ('NoSuchBucket', 'The specified bucket does not exist', 404, False),
    ('AccessDenied', 'Unauthorized', 403, False),
])
def test_missing_object_detection_does_not_hide_bucket_or_permission_errors(code, message, status, missing):
    from app.admin.document_lifecycle import storage_object_missing
    assert storage_object_missing(StorageApiError(message, code, status)) is missing


def test_targeted_repair_dry_run_apply_and_current_document_guard(migrated_admin, storage):
    app, client, _, _ = migrated_admin
    first = add(client)
    row = replace(client, first)
    add(client, 'DEMO0017-OTHER', both=False)
    runner = app.test_cli_runner()
    args = ['purge-removed-documents', '--identifier', 'DEMO0017', '--kind', 'pod']
    rejected = runner.invoke(args=args + ['--apply'])
    assert rejected.exit_code == 1 and 'current document' in rejected.output
    # Reproduce exactly the old ER soft-remove behavior.
    with app.app_context():
        db.session.get(models.Consignment, models.record_id(row['id'])).pod_image = None
        db.session.commit()
    before = dict(storage.objects)
    preview = runner.invoke(args=args)
    assert preview.exit_code == 0 and '2 already-removed POD' in preview.output
    assert 'Dry run' in preview.output and storage.objects == before
    applied = runner.invoke(args=args + ['--apply'])
    assert applied.exit_code == 0, applied.output
    assert 'Removed 2 POD' in applied.output
    assert get_row(client)['invoice_file'] == row['invoice_file']
    assert get_row(client, 'DEMO0017-OTHER')['pod_image']
    assert len(storage.objects) == 2
    assert_er_purged(app, 'DEMO0017', [first['pod_image'], row['pod_image']])
    repeated = runner.invoke(args=args + ['--apply'])
    assert repeated.exit_code == 0 and 'Removed 0 POD' in repeated.output


def test_repair_failure_preserves_old_versions(migrated_admin, storage):
    app, client, _, _ = migrated_admin
    row = add(client, both=False)
    with app.app_context():
        db.session.get(models.Consignment, models.record_id(row['id'])).pod_image = None
        db.session.commit()
    before = dict(storage.objects)
    storage.failure = 'noop'
    result = app.test_cli_runner().invoke(args=['purge-removed-documents', '--identifier', 'DEMO0017', '--kind', 'pod', '--apply'])
    assert result.exit_code == 1 and storage.objects == before
    assert 'previous documents were preserved' in result.output
    preview = app.test_cli_runner().invoke(args=['purge-removed-documents', '--identifier', 'DEMO0017', '--kind', 'pod'])
    assert preview.exit_code == 0 and '1 already-removed POD' in preview.output
