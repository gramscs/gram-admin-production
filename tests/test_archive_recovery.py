"""Adopt a verified existing archive table without dropping or rewriting data."""
import importlib
from pathlib import Path
import pytest
from sqlalchemy import text
from app.models import db
from test_lr_issuance import lr_app, panel, save
from migrations.recover_006_shipment_archives import render_sql, migration


def recover(engine):
    with engine.begin() as c:
        schema=c.execute(text('SELECT current_schema()')).scalar_one()
        c.exec_driver_sql(render_sql(schema).replace('\nBEGIN;','',1).replace('\nCOMMIT;',''),execution_options={'no_parameters':True})


def test_recover_existing_rows_and_record_original_checksum(lr_app,panel):
    client,headers,_=panel
    row=save(panel).json['shipments'][0]
    assert client.post('/admin/consignments/'+row['id']+'/archive',headers=headers,json={'confirmed':True}).status_code==200
    with lr_app.app_context():
        with db.engine.begin() as c:
            before=c.execute(text('SELECT to_jsonb(a) FROM shipment_archives a')).scalar_one()
            c.execute(text("DELETE FROM admin_migration_runs WHERE version='006_shipment_archives'"))
            c.execute(text('DROP TRIGGER archived_shipment_readonly ON consignments'))
            c.execute(text('ALTER FUNCTION gram_archive_guard() SET search_path = __SCHEMA__, pg_temp'))
        recover(db.engine)
        recover(db.engine)
        assert migration.migrate(db.engine,apply=True)['status']=='already_applied'
        with db.engine.connect() as c:
            assert c.execute(text('SELECT to_jsonb(a) FROM shipment_archives a')).scalar_one()==before
        assert client.get('/admin/consignments/list').json['total']==0
        assert client.get('/admin/consignments/list?archive=archived').json['total']==1
        assert client.post('/admin/consignments/'+row['id']+'/restore',headers=headers,json={'confirmed':True}).status_code==200


def test_table_only_partial_install_is_completed(lr_app):
    with lr_app.app_context():
        with db.engine.begin() as c:
            c.execute(text("DELETE FROM admin_migration_runs WHERE version='006_shipment_archives'"))
            c.execute(text('DROP TRIGGER archived_shipment_readonly ON consignments'))
            c.execute(text('DROP TRIGGER archive_guard ON shipment_archives'))
            c.execute(text('DROP TRIGGER archive_truncate_guard ON shipment_archives'))
            c.execute(text('DROP FUNCTION gram_archive_guard(),gram_archived_shipment_readonly()'))
            c.execute(text('DROP INDEX ix_shipment_archives_archived_at'))
            c.execute(text('ALTER TABLE shipment_archives DISABLE ROW LEVEL SECURITY'))
        recover(db.engine)
        assert migration.migrate(db.engine)['status']=='already_applied'


@pytest.mark.parametrize('alteration',[
    'ALTER TABLE shipment_archives ADD COLUMN unknown text',
    "CREATE OR REPLACE FUNCTION gram_archive_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$ BEGIN RETURN NEW; END $$",
    'ALTER TABLE shipment_archives DROP CONSTRAINT shipment_archives_archived_by_fkey',
    'CREATE POLICY unexpected_policy ON shipment_archives USING (true)',
])
def test_incompatible_objects_stop_without_recording_history(lr_app,alteration):
    with lr_app.app_context():
        with db.engine.begin() as c:
            c.execute(text("DELETE FROM admin_migration_runs WHERE version='006_shipment_archives'"))
            c.execute(text(alteration))
        with pytest.raises(Exception):
            recover(db.engine)
        with db.engine.connect() as c:
            assert c.execute(text("SELECT count(*) FROM admin_migration_runs WHERE version='006_shipment_archives'")).scalar_one()==0


def test_recovery_export_matches():
    assert (Path(__file__).resolve().parents[1]/'migrations/sql/006_shipment_archives_recover_supabase.sql').read_text()==render_sql()
