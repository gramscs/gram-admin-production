"""Downloadable database and Supabase-object backups."""
import io
import json
from zipfile import ZipFile

from app import orm as models
from app.models import db
from test_shipment_documents import attachment, image_bytes


def unpack(response):
    assert response.status_code == 200
    assert response.mimetype == "application/zip"
    archive = ZipFile(io.BytesIO(response.data))
    assert archive.testzip() is None
    return archive, json.loads(archive.read("data.json")), json.loads(archive.read("document_report.json"))


def test_backup_contains_current_erd_tables_and_supabase_document(admin_client, app, isolated_storage):
    response = admin_client.post("/admin/consignments/save", json={"rows": [{
        "consignment_number": "BACKUP-001",
        **attachment("pod", image_bytes(), "proof.png"),
    }]})
    assert response.status_code == 200, response.json
    before = dict(isolated_storage.storage.objects)
    backup_response = admin_client.get("/admin/generate-backup")
    archive, payload, report = unpack(backup_response)
    assert backup_response.headers["Cache-Control"] == "private, no-store"
    assert "consignments" in payload and "files" in payload and "shipment_documents" in payload
    assert len(payload["consignments"]) == 1
    document = next(item for item in report["documents"] if item["status"] == "included")
    assert archive.read(document["archive_path"]) == image_bytes()
    assert not report["inventory_errors"]
    assert isolated_storage.storage.objects == before
    with app.app_context():
        assert models.Consignment.query.count() == 1


def test_json_backup_does_not_download_supabase_objects(admin_client, app, isolated_storage, monkeypatch):
    response = admin_client.post("/admin/consignments/save", json={"rows": [{
        "consignment_number": "BACKUP-JSON",
        **attachment("pod", image_bytes(), "proof.png"),
    }]})
    assert response.status_code == 200
    payload = admin_client.get("/admin/generate-backup?format=json")
    assert payload.status_code == 200 and "consignments" in payload.json


def test_backup_status_reports_missing_supabase_objects(admin_client, app):
    from database.models import File, ShipmentDocument
    with app.app_context():
        row = models.Consignment(identifier_value="BACKUP-MISSING")
        missing = File(storage_bucket="pod-uploads", storage_path="consignments/missing.png",
                       original_filename="missing.png", mime_type="image/png", size_bytes=12)
        row.documents.append(ShipmentDocument(file=missing, document_type="POD"))
        db.session.add(row)
        db.session.commit()
    archive, payload, report = unpack(admin_client.get("/admin/generate-backup"))
    assert payload["metadata"]["backup_status"] == "incomplete"
    assert any(item["status"] == "missing" for item in report["documents"])
    assert b"INCOMPLETE" in archive.read("README.txt")
