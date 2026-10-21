"""Storage diagnostics fail explicitly instead of falling back to local files."""
import sys

import pytest

from app.admin import consignment_controller as controller
from test_shipment_documents import attachment, image_bytes


def test_opaque_proxy_binding_uses_official_storage_client(monkeypatch):
    from types import SimpleNamespace
    import storage3
    import supabase
    from supabase._sync.client import SupabaseException
    monkeypatch.setenv('SUPABASE_URL', 'https://storage-test.supabase.co')
    monkeypatch.setenv('SUPABASE_KEY', 'opaque-test-binding')
    def reject_format(*args):
        raise SupabaseException('Invalid API key')
    monkeypatch.setattr(supabase, 'create_client', reject_format)
    seen = {}
    storage = SimpleNamespace()
    def create(url, headers, **options):
        seen.update(url=url, headers=headers, options=options)
        return storage
    monkeypatch.setattr(storage3, 'create_client', create)
    assert controller._get_supabase_client().storage is storage
    assert seen == {'url': 'https://storage-test.supabase.co/storage/v1',
                    'headers': {'apikey': 'opaque-test-binding', 'Authorization': 'Bearer opaque-test-binding'},
                    'options': {'is_async': False}}


@pytest.mark.parametrize("url,key", [("https://test.supabase.co", ""), ("", "test-key")])
@pytest.mark.parametrize("kind", ["pod", "invoice"])
def test_incomplete_settings_reject_upload_without_local_fallback(
        app, admin_client, monkeypatch, url, key, kind):
    monkeypatch.setenv("SUPABASE_URL", url)
    monkeypatch.setenv("SUPABASE_KEY", key)
    with pytest.raises(RuntimeError, match="SUPABASE_URL and SUPABASE_KEY"):
        controller._get_supabase_client()
    result = admin_client.post(
        "/admin/consignments/save",
        json={"rows": [{"consignment_number": "NO-LOCAL-" + kind,
                        **attachment(kind, image_bytes(), "proof.png")}]},
    )
    assert result.status_code == 500
    from app import orm as models
    with app.app_context():
        assert models.Consignment.query.filter_by(consignment_number="NO-LOCAL-" + kind).first() is None


@pytest.mark.parametrize("failure", ["missing_sdk", "invalid_client"])
def test_remote_initialization_failure_does_not_expose_keys_or_write_files(
        app, monkeypatch, failure):
    secret = "PRIVATE-TEST-KEY-DO-NOT-PRINT"
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", secret)
    if failure == "missing_sdk":
        monkeypatch.setitem(sys.modules, "supabase", None)
    else:
        import supabase
        monkeypatch.setattr(supabase, "create_client", lambda *_: (_ for _ in ()).throw(ValueError(secret)))
    with app.app_context(), pytest.raises(RuntimeError):
        controller._store_pod_bytes("never-created.png", image_bytes(), "image/png")
    checked = app.test_cli_runner().invoke(args=["check-storage"])
    assert checked.exit_code != 0 and secret not in checked.output


def test_check_storage_requires_supabase_credentials(app, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_KEY", "")
    result = app.test_cli_runner().invoke(args=["check-storage"])
    assert result.exit_code != 0
    assert "Local file storage is disabled" in result.output


@pytest.mark.parametrize("fail_bucket", [None, "invoice-uploads"])
def test_storage_check_reads_buckets_without_mutating_them(app, monkeypatch, fail_bucket):
    reads = []

    class Bucket:
        def __init__(self, name):
            self.name = name

        def list(self, path, options):
            reads.append(("list", self.name, path, options))
            if self.name == fail_bucket:
                raise RuntimeError("PRIVATE-KEY-IN-REMOTE-ERROR")
            return []

        def upload(self, *args):
            raise AssertionError("Read-only check uploaded a file")

        def remove(self, *args):
            raise AssertionError("Read-only check removed a file")

    class Storage:
        def get_bucket(self, name):
            reads.append(("bucket", name))

        def from_(self, name):
            return Bucket(name)

    class Client:
        storage = Storage()

    import supabase
    monkeypatch.setattr(supabase, "create_client", lambda *_: Client())
    monkeypatch.setenv("SUPABASE_BUCKET", "pod-uploads")
    monkeypatch.setenv("SUPABASE_INVOICE_BUCKET", "invoice-uploads")
    monkeypatch.setenv("SUPABASE_MIS_BUCKET", "mis-reports")
    result = app.test_cli_runner().invoke(args=["check-storage"])
    assert result.exit_code == (1 if fail_bucket else 0)
    assert ("list", "pod-uploads", "consignments", {"limit": 1}) in reads
    assert ("list", "invoice-uploads", "consignments", {"limit": 1}) in reads
    assert ("list", "mis-reports", "mis_reports", {"limit": 1}) in reads
    assert "Upload permissions were not tested" in result.output
    assert "PRIVATE-KEY-IN-REMOTE-ERROR" not in result.output


@pytest.mark.parametrize("bucket", ["pod-uploads", "invoice-uploads"])
def test_storage_check_rejects_shared_report_bucket(app, monkeypatch, bucket):
    import supabase
    monkeypatch.setattr(supabase, "create_client", lambda *_: object())
    monkeypatch.setenv("SUPABASE_BUCKET", "pod-uploads")
    monkeypatch.setenv("SUPABASE_INVOICE_BUCKET", "invoice-uploads")
    monkeypatch.setenv("SUPABASE_MIS_BUCKET", bucket)
    result = app.test_cli_runner().invoke(args=["check-storage"])
    assert result.exit_code != 0 and "must be separate" in result.output
