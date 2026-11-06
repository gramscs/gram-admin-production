"""Archive storage, normal visibility, actor protection and non-prompted cancellation."""
import importlib
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import text
from app.models import db
from database.lr import ShipmentArchive, LRAudit, LRSequence
from database.models import Consignment, AuditLog
from test_lr_issuance import lr_app, panel, save

archive_migration=importlib.import_module('migrations.006_shipment_archives')


def test_cancel_without_reason_retains_identity_and_actor(lr_app,panel):
    client,headers,_=panel
    issued=save(panel).json['shipments'][0]
    result=client.post('/admin/consignments/'+issued['id']+'/cancel',json={'confirmed':True},headers=headers)
    assert result.status_code==200,result.json
    with lr_app.app_context():
        row=db.session.get(Consignment,issued['id'])
        assert row.identifier_value==issued['lr_number']
        assert row.cancellation_reason=='Cancelled by administrator.'
        assert row.cancelled_by is not None and row.cancelled_at is not None
        assert db.session.query(LRAudit).filter_by(shipment_id=row.id,action='cancelled').count()==1


def test_archive_filter_restore_and_counter_preservation(lr_app,panel):
    client,headers,_=panel
    issued=save(panel).json['shipments'][0]
    url='/admin/consignments/'+issued['id']
    response=client.post(url+'/archive',json={'confirmed':True},headers=headers)
    assert response.status_code==200,response.json
    assert client.post(url+'/archive',json={'confirmed':True},headers=headers).status_code==200
    assert client.get('/admin/consignments/list').json['total']==0
    listing=client.get('/admin/consignments/list?archive=archived').json
    assert listing['total']==1 and listing['rows'][0]['consignment_number']==issued['lr_number']
    assert listing['rows'][0]['is_archived'] is True
    assert client.get('/admin/consignments/list?archive=invalid').status_code==400
    with lr_app.app_context():
        snapshot=db.session.get(ShipmentArchive,issued['id'])
        assert snapshot.snapshot['canonical']['identifier_value']==issued['lr_number']
        assert snapshot.archived_by is not None
        assert db.session.query(Consignment).count()==1
        assert db.session.query(AuditLog).filter_by(action='archive').count()==1
        assert db.session.query(LRSequence).one().last_sequence==1
    edit=client.post('/admin/consignments/save',headers=headers,json={'idempotency_key':str(uuid4()),'rows':[{'id':issued['id'],'status':'Delivered'}]})
    assert edit.status_code==400 and 'Restore' in edit.json['message']
    assert client.post(url+'/cancel',headers=headers,json={'confirmed':True}).status_code==400
    assert client.post(url+'/restore',headers=headers,json={'confirmed':True}).status_code==200
    assert client.get('/admin/consignments/list').json['total']==1
    assert client.get('/admin/consignments/list?archive=archived').json['total']==0
    assert save(panel).json['shipments'][0]['lr_number'].endswith('000002')
    with lr_app.app_context():
        assert db.session.query(AuditLog).filter_by(action='restore').count()==1


def test_archive_permissions_and_snapshot_immutability(lr_app,panel):
    client,headers,_=panel
    issued=save(panel).json['shipments'][0]
    url='/admin/consignments/'+issued['id']+'/archive'
    assert client.post(url,json={'confirmed':True}).status_code==403
    assert client.post(url,headers=headers,json={}).status_code==400
    with lr_app.app_context():
        db.session.execute(text("UPDATE admin_users SET role='operator'"));db.session.commit()
    assert client.post(url,headers=headers,json={'confirmed':True}).status_code==403
    with lr_app.app_context():
        db.session.execute(text("UPDATE admin_users SET role='administrator'"));db.session.commit()
    assert client.post(url,headers=headers,json={'confirmed':True}).status_code==200
    with lr_app.app_context():
        engine=db.engine
        who=db.session.execute(text('SELECT id FROM admin_users LIMIT 1')).scalar_one()
        db.session.rollback()
        for sql in ["UPDATE shipment_archives SET snapshot='{}'",'TRUNCATE shipment_archives',"UPDATE consignments SET current_status='Modified'",'DELETE FROM shipment_archives']:
            with pytest.raises(Exception):
                with engine.begin() as connection:
                    # No actor: direct attempts cannot claim admin privileges.
                    connection.execute(text(sql))
        with pytest.raises(Exception,match='Restore archived'):
            with engine.begin() as connection:
                connection.execute(text("SELECT set_config('gram.actor_id',:actor,true)"),{'actor':str(who)})
                connection.execute(text("UPDATE consignments SET current_status='Modified'"))


def test_exported_migration_matches_and_replays(lr_app):
    assert (Path(__file__).resolve().parents[1]/'migrations/sql/006_shipment_archives_supabase.sql').read_text()==archive_migration.sql_editor()
    with lr_app.app_context():
        assert archive_migration.migrate(db.engine,apply=True)['status']=='already_applied'


def test_sql_editor_archive_migration(app):
    from test_lr_issuance import migration
    with app.app_context():
        migration.migrate(db.engine,apply=True)
        with db.engine.begin() as connection:
            schema=connection.execute(text('SELECT current_schema()')).scalar_one()
            sql=archive_migration.sql_editor(schema).replace('\nBEGIN;','',1).replace('\nCOMMIT;','')
            connection.exec_driver_sql(sql,execution_options={'no_parameters':True})
        assert archive_migration.migrate(db.engine,apply=True)['status']=='already_applied'


def test_archive_rollback_refuses_existing_snapshots(lr_app,panel):
    client,headers,_=panel
    identifier=save(panel).json['shipments'][0]['id']
    assert client.post('/admin/consignments/'+identifier+'/archive',headers=headers,json={'confirmed':True}).status_code==200
    script=(Path(__file__).resolve().parents[1]/'migrations/sql/006_shipment_archives_rollback.sql').read_text().replace('BEGIN;','',1).replace('\nCOMMIT;','')
    with lr_app.app_context():
        with pytest.raises(Exception,match='Restore archived shipments'):
            with db.engine.begin() as connection:
                connection.exec_driver_sql(script,execution_options={'no_parameters':True})
        assert db.session.query(ShipmentArchive).count()==1
