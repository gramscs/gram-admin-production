"""Helpers and focused checks for PostgreSQL-only imports."""

from migrations.storage import MigrationError, Storage
from test_shipment_migration import disposable_engine


def test_import_storage_requires_supabase_objects():
    try:
        Storage().describe("supabase:pod-uploads/consignments/missing.pdf")
    except MigrationError as error:
        assert "SUPABASE_URL" in str(error)
    else:
        raise AssertionError("Supabase credentials must be required for imported file references.")
