"""The application never falls back to persistent local file storage."""
import httpx
import pytest

import app as app_module
from app.admin import consignment_controller as controller
from test_shipment_documents import attachment, image_bytes


@pytest.fixture(autouse=True)
def development_env(tmp_path, monkeypatch):
    root = tmp_path / "configured-project"
    root.mkdir()
    (root / ".env").write_text(
        "SUPABASE_URL=https://unexpected.example.invalid\n"
        "SUPABASE_KEY=dotenv-test-key\n"
    )
    monkeypatch.setattr(app_module, "PROJECT_ROOT", root)


def test_documents_are_saved_only_to_supabase(app, admin_client, isolated_storage):
    response = admin_client.post("/admin/consignments/save", json={"rows": [{
        "consignment_number": "REMOTE-STORAGE",
        **attachment("pod", image_bytes(), "proof.png"),
    }]})
    assert response.status_code == 200, response.json
    row = admin_client.get("/admin/consignments/list?search=REMOTE-STORAGE").json["rows"][0]
    assert row["pod_image"].startswith("supabase:pod-uploads/")
    bucket, path = row["pod_image"].removeprefix("supabase:").split("/", 1)
    assert isolated_storage.storage.from_(bucket).download(path)
    assert controller._get_supabase_client() is not None


def test_unmocked_http_is_blocked_even_when_a_test_changes_credentials():
    with httpx.Client() as client, pytest.raises(AssertionError, match="must mock HTTP"):
        client.get("https://test.example.invalid/storage/v1/object")
