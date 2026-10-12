"""Development .env credentials must never become test storage credentials."""
import os
from pathlib import Path

import httpx
import pytest

import app as app_module
from app.admin import consignment_controller as controller
from test_shipment_documents import attachment, image_bytes
from test_er_dashboard import migrated_admin, populated, legacy_engine, disposable_engine


@pytest.fixture(autouse=True)
def development_env(tmp_path, monkeypatch):
    root = tmp_path / 'configured-project'; root.mkdir()
    (root / '.env').write_text('SUPABASE_URL=https://real-settings.example.invalid\nSUPABASE_KEY=private-placeholder-test-key\nSUPABASE_MIS_BUCKET=reports-from-dotenv\n')
    monkeypatch.setattr(app_module, 'PROJECT_ROOT', root)


@pytest.mark.parametrize('model', ['legacy', 'er'])
def test_dotenv_does_not_reactivate_remote_uploads(model, app, admin_client, migrated_admin):
    target, client = (app, admin_client) if model == 'legacy' else migrated_admin[:2]
    assert os.environ['SUPABASE_URL'] == os.environ['SUPABASE_KEY'] == os.environ['SUPABASE_MIS_BUCKET'] == ''
    with target.app_context():
        assert controller._get_supabase_client() is None
    result = client.post('/admin/consignments/save', json={'rows': [{'consignment_number': 'ISOLATED-STORAGE',
        **attachment('pod', image_bytes(), 'proof.png')} ]})
    assert result.status_code == 200, result.json
    row = client.get('/admin/consignments/list?search=ISOLATED-STORAGE').json['rows'][0]
    assert not row['pod_image'].startswith('supabase:')
    assert Path(target.instance_path, 'uploads', row['pod_image'].removeprefix('local:')).is_file()


def test_unmocked_http_is_blocked_even_when_a_test_changes_credentials():
    with httpx.Client() as client, pytest.raises(AssertionError, match='must mock HTTP'):
        client.get('https://test.example.invalid/storage/v1/object')
