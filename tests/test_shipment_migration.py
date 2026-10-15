"""PostgreSQL-only tests and fixtures for one-time imports."""
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
import os
from uuid import uuid4

from PIL import Image
import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.engine import make_url

from app.models import db
from database.models import Base
from migrations import shipment_v1
from migrations.shipment_v1 import LEDGER, RECORDS, migrate, migration_engine
from migrations.storage import MigrationError, Storage


class RemoteBucket:
    def __init__(self, objects, bucket):
        self.objects, self.bucket = objects, bucket

    def download(self, path):
        try:
            return self.objects[self.bucket, path]
        except KeyError:
            raise FileNotFoundError(path) from None

    def upload(self, path, content, options=None):
        if hasattr(content, "read"):
            content = content.read()
        self.objects[self.bucket, path] = bytes(content)
        return {"Key": path}

    def remove(self, paths):
        removed = []
        for path in paths:
            if self.objects.pop((self.bucket, path), None) is not None:
                removed.append({"name": path})
        return removed

    def info(self, path):
        if (self.bucket, path) not in self.objects:
            raise FileNotFoundError(path)
        return {"name": path}

    def list(self, folder="", options=None):
        prefix = folder.rstrip("/") + "/" if folder else ""
        entries = []
        for (bucket, path), content in self.objects.items():
            if bucket != self.bucket or not path.startswith(prefix):
                continue
            name = path[len(prefix):]
            if name and "/" not in name:
                entries.append({"name": name, "id": "object", "metadata": {"size": len(content)}})
        options = options or {}
        offset, limit = options.get("offset", 0), options.get("limit", len(entries))
        return sorted(entries, key=lambda item: item["name"])[offset:offset + limit]


class RemoteStorage:
    def __init__(self, objects):
        self.objects = objects

    def from_(self, bucket):
        return RemoteBucket(self.objects, bucket)


class RemoteClient:
    def __init__(self, objects):
        self.storage = RemoteStorage(objects)


@contextmanager
def disposable_engine(backend, name):
    if backend != "postgresql":
        raise ValueError("Migration tests require PostgreSQL.")
    setting = os.getenv("ER_TEST_DATABASE_URL", "").strip()
    if not setting:
        pytest.fail("Set ER_TEST_DATABASE_URL to a dedicated PostgreSQL test database.")
    url = make_url(setting)
    if url.get_backend_name() != "postgresql" or "test" not in (url.database or "").lower():
        pytest.fail("ER_TEST_DATABASE_URL must name a dedicated PostgreSQL test database.")
    schema = "migration_" + uuid4().hex
    admin = create_engine(url, pool_pre_ping=True)
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    scoped_url = url.update_query_dict({"options": f"-csearch_path={schema},public"})
    engine = migration_engine(scoped_url)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


@pytest.fixture
def legacy_engine():
    setting = os.getenv("ER_TEST_DATABASE_URL", "").strip()
    if not setting:
        pytest.fail("Set ER_TEST_DATABASE_URL to a dedicated PostgreSQL test database.")
    url = make_url(setting)
    if (url.get_backend_name() != "postgresql" or url.host not in {"127.0.0.1", "localhost"}
            or url.database != "gram_admin_er_test"):
        pytest.fail("Migration tests require the disposable loopback database gram_admin_er_test.")
    schema = "legacy_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = migration_engine(url.update_query_dict({"options": f"-csearch_path={schema},public"}))
    with engine.begin() as connection:
        db.metadata.create_all(connection)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


@pytest.fixture
def populated(legacy_engine):
    objects = {
        ("pod-uploads", "consignments/pod.png"): b"\x89PNG\r\n\x1a\nproof",
        ("invoice-uploads", "consignments/invoice.pdf"): b"%PDF-1.4\ninvoice bytes",
        ("mis-reports", "consignments/report.csv"): b"identifier,pieces\nLRN-001,2\n",
        ("pod-uploads", "consignments/orphan.pdf"): b"%PDF-1.4\nunlinked upload",
    }
    storage = Storage(RemoteClient(objects), additional_buckets=("invoice-uploads", "mis-reports"))
    with legacy_engine.begin() as connection:
        connection.execute(insert(db.metadata.tables["company"]).values(
            id=7, name="Acme", address="Registered address", active=True))
        for id, kind in ((8, "pickup"), (9, "drop")):
            connection.execute(insert(db.metadata.tables["company_location"]).values(
                id=id, company_id=7, kind=kind, label=kind + " warehouse",
                address=kind + " address", pincode="110001", is_default=True))
        connection.exec_driver_sql("ALTER TABLE consignment ADD COLUMN extra_operator_notes TEXT")
        connection.execute(insert(db.metadata.tables["consignment"]).values(
            id=11, consignment_number="LRN-001", identifier_type="LRN", company_id=7,
            status="In Transit", pieces=2, weight="12.345", volume="0.125",
            pickup_address="Edited pickup address", drop_address="Drop snapshot",
            pickup_pincode="110001", drop_pincode="400001", pickup_tag="Warehouse A",
            drop_tag="Store B", pickup_date="2026-10-01", drop_date="02/10/2026",
            eta="ETA text", eta_debug_json='{"source":"manual"}',
            pod_image="supabase:pod-uploads/consignments/pod.png",
            pod_original_name="Customer proof.png",
            invoice_file="supabase:invoice-uploads/consignments/invoice.pdf",
            invoice_original_name="Invoice 123.pdf"))
        connection.execute(insert(db.metadata.tables["consignment"]).values(
            id=12, consignment_number="AWB-002", identifier_type="AWB", status=None,
            pieces=1, pickup_date="next Tuesday",
            pod_image="supabase:pod-uploads/consignments/pod.png"))
        connection.execute(insert(db.metadata.tables["mis_view"]).values(
            id=13, name="Client MIS", notes="Saved view notes", created_by="admin",
            updated_at=datetime(2026, 10, 1, tzinfo=UTC), output_format="csv",
            filters_json=json.dumps({"company": "7", "period": "month"}),
            columns_json=json.dumps(["consignment_number", "status", "pickup_date"])))
        connection.execute(insert(db.metadata.tables["mis_report"]).values(
            id=14, name="October report", notes="Management notes", generated_by="manager",
            generated_at=datetime(2026, 10, 2, 12, tzinfo=UTC), output_format="csv",
            row_count=2, file_ref="supabase:mis-reports/consignments/report.csv",
            file_name="October MIS.csv", filters_json=json.dumps({"company": "7"}),
            columns_json=json.dumps(["consignment_number", "status"]),
            summary_json=json.dumps({"total": 2})))
        connection.execute(insert(db.metadata.tables["lead"]).values(
            id=15, name="Archived lead", email="lead@example.test", message="Preserve enquiry"))
        connection.execute(insert(db.metadata.tables["newsletter_subscriber"]).values(
            id=16, email="subscriber@example.test"))
        connection.execute(
            db.metadata.tables["consignment"].update().where(
                db.metadata.tables["consignment"].c.id == 11
            ).values(extra_operator_notes="Keep this extra field")
        )
    return legacy_engine, storage, objects[("pod-uploads", "consignments/pod.png")]


def legacy_snapshot(engine):
    with engine.begin() as connection:
        _, rows = shipment_v1.read_legacy(connection)
        return shipment_v1.digest(rows)


def test_import_preserves_source_and_copies_references(populated):
    source, storage, pod_content = populated
    before = legacy_snapshot(source)
    with disposable_engine("postgresql", "import-target") as target:
        result = migrate(target, storage, source_engine=source, fresh_target=True)
        assert result["status"] == "applied"
        assert result["counts"]["archived_rows"] == 9
        assert before == legacy_snapshot(source)
        with target.begin() as connection:
            rows = shipment_v1.target_snapshot(connection)
            shipment = next(row for row in rows["consignments"] if row["identifier_value"] == "LRN-001")
            assert shipment["weight"] == 12.345 and shipment["volume"] == 0.125
            assert len(rows["files"]) == 4 and len(rows["shipment_documents"]) == 3
            pod = next(file for file in rows["files"] if file["storage_bucket"] == "pod-uploads")
            assert pod["checksum"] == hashlib.sha256(pod_content).hexdigest()
            assert pod["storage_path"] == "supabase:pod-uploads/consignments/pod.png"
            archive = connection.execute(select(RECORDS).where(
                RECORDS.c.source_table == "consignment",
                RECORDS.c.source_id == "11")).mappings().one()
            assert archive["source_values"]["extra_operator_notes"] == "Keep this extra field"


def test_storage_rejects_local_references(populated):
    _, storage, _ = populated
    with pytest.raises(MigrationError, match="Supabase"):
        storage.describe("local:shipment.pdf")


def test_migration_engine_refuses_non_postgresql():
    with pytest.raises(MigrationError, match="PostgreSQL"):
        migration_engine("mysql://user:password@localhost/database")
