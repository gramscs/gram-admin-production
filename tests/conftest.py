import sys
from pathlib import Path

import pytest
import httpx
from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from app.models import db


@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch):
    # Explicit blanks survive load_dotenv(override=False); deleting the variables
    # allows a developer's .env to restore real bucket credentials during tests.
    monkeypatch.setenv('SUPABASE_URL', '')
    monkeypatch.setenv('SUPABASE_KEY', '')
    monkeypatch.setenv('SUPABASE_MIS_BUCKET', '')
    def blocked_http(*args, **kwargs):
        raise AssertionError('Tests must mock HTTP clients; external services are not test storage.')
    async def blocked_async_http(*args, **kwargs):
        blocked_http()
    monkeypatch.setattr(httpx.Client, 'send', blocked_http)
    monkeypatch.setattr(httpx.AsyncClient, 'send', blocked_async_http)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", generate_password_hash("test-admin-password"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'admin.db'}")
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_KEY", "")
    application = create_app({
        "TESTING": True,
        "SECRET_KEY": "standalone-test-key",
        "INSTANCE_PATH": str(tmp_path / "instance"),
        "AUTO_CREATE_TABLES": True,
        "RATELIMIT_ENABLED": False,
    })
    # Authentication settings are loaded once per process in the copied module.
    from app.admin import auth
    monkeypatch.setattr(auth, "ADMIN_PASSWORD_HASH", generate_password_hash("test-admin-password"))
    yield application
    with application.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin_client(client):
    response = client.post("/admin/login", data={"username": "admin", "password": "test-admin-password"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/dashboard")
    return client
