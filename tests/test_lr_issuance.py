"""Real PostgreSQL transactions for migration 005; Supabase boundaries use existing fixtures."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import importlib
import io
import json
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from app.models import db
from app.lr_issuance import financial_year, reserved_header
from database.models import Consignment
from database.lr import IssuingLocation, LRSequence, LRAudit
from auth_support import sign_in

migration = importlib.import_module('migrations.005_centralized_lr')
NEW_COLUMNS = ('location_id','lr_sequence','financial_year','created_at','lr_issued_at','lr_status','cancelled_at','cancelled_by','cancellation_reason')

@pytest.fixture
def lr_app(app):
    with app.app_context():
        # Model-created fixture represents old schema before the versioned migration.
        with db.engine.begin() as connection:
            for name in NEW_COLUMNS:
                connection.exec_driver_sql(f'ALTER TABLE consignments DROP COLUMN {name}')
        assert migration.migrate(db.engine)['status'] == 'ready'
        assert migration.migrate(db.engine, apply=True)['status'] == 'applied'
        assert migration.migrate(db.engine, apply=True)['status'] == 'already_applied'
    yield app

@pytest.fixture
def panel(lr_app):
    client = lr_app.test_client()
    assert sign_in(client).status_code == 302
    with client.session_transaction() as state:
        token = state['auth_csrf_token']
    headers = {'X-CSRF-Token': token}
    response = client.post('/admin/issuing-locations', json={'location_name':'Head Office','location_code':'hq'}, headers=headers)
    assert response.status_code == 200, response.json
    identifier = response.json['id']
    report = lr_app.test_cli_runner().invoke(args=['lr-reconciliation-report','--location-code','HQ'])
    assert report.exit_code == 0, report.output
    from tempfile import NamedTemporaryFile
    with NamedTemporaryFile(mode='w',suffix='.json') as output:
        json.dump(dict(location_code='HQ',history_digest=json.loads(report.output)['history_digest'],
            history_reviewed=True,external_history_reviewed=True,reason='Reviewed empty fixture history',opening_counters={}),output)
        output.flush()
        approved = lr_app.test_cli_runner().invoke(args=['lr-approve-opening-counters',output.name])
        assert approved.exit_code == 0, approved.output
    return client, headers, identifier

def save(panel, **changes):
    client, headers, location = panel
    data = dict(location_id=location,status='In Transit',pieces=2,consignment_number='')
    data.update(changes)
    return client.post('/admin/consignments/save',json=dict(rows=[data],idempotency_key=str(uuid4())),headers=headers)

@pytest.mark.parametrize('moment,expected', [('2027-03-31T18:29:59+00:00','2627'),('2027-03-31T18:30:00+00:00','2728'),('2026-11-04T00:00:00+00:00','2627')])
def test_ist_boundary(moment,expected):
    assert financial_year(datetime.fromisoformat(moment)) == expected

@pytest.mark.parametrize('header',['LR Number','lR_no','Consignment.Number','CN-No','GR_number','LRN','lorry receipt no'])
def test_reserved_aliases(header):
    assert reserved_header(header)
    assert not reserved_header('invoice_number')

def test_issue_retry_cancel_and_immutability(lr_app,panel):
    client, headers, location = panel
    key = str(uuid4()); payload=dict(rows=[dict(location_id=location,status='In Transit')],idempotency_key=key)
    first = client.post('/admin/consignments/save',json=payload,headers=headers)
    assert first.status_code == 200,first.json
    assert client.post('/admin/consignments/save',json=payload,headers=headers).json == first.json
    row = first.json['shipments'][0]
    assert row['lr_number'].startswith('HQ') and row['lr_number'].endswith('000001')
    assert save(panel,consignment_number='MANUAL').status_code == 400
    assert client.post('/admin/consignments/save',json={'rows':[dict(id=row['id'],status='Delivered',consignment_number='CHANGED')], 'idempotency_key':str(uuid4())},headers=headers).status_code == 400
    assert client.post('/admin/consignments/save',json={'rows':[], 'deleted_ids':[row['id']]},headers=headers).status_code == 400
    cancelled=client.post('/admin/consignments/'+row['id']+'/cancel',json={'confirmed':True,'reason':'Duplicate booking'},headers=headers)
    assert cancelled.status_code == 200,cancelled.json
    second=save(panel)
    assert second.status_code == 200,second.json
    assert second.json['shipments'][0]['lr_number'].endswith('000002')
    events=client.get('/admin/consignments/'+row['id']+'/lr-history').json['events']
    assert [e['action'] for e in events]==['issued','cancelled']
    assert events[-1]['reason']=='Duplicate booking'
    with lr_app.app_context():
        assert db.session.get(Consignment,row['id']).lr_status=='cancelled'
        with pytest.raises(Exception):
            db.session.execute(text('UPDATE lr_sequences SET last_sequence=0'))
        db.session.rollback()
        with pytest.raises(Exception):
            db.session.execute(text('DELETE FROM lr_audit_log'))
        db.session.rollback()

def test_rollback_and_inactive_location(lr_app,panel):
    client,headers,location=panel
    payload=dict(idempotency_key=str(uuid4()),rows=[dict(location_id=location,status='In Transit'),dict(location_id=location,status='')])
    assert client.post('/admin/consignments/save',json=payload,headers=headers).status_code==400
    assert save(panel).json['shipments'][0]['lr_number'].endswith('000001')
    response=client.post('/admin/issuing-locations',headers=headers,json=dict(id=location,location_name='HQ',location_code='HQ',is_active=False))
    assert response.status_code==200,response.json
    assert save(panel).status_code==400
    assert client.post('/admin/issuing-locations',headers=headers,json=dict(id=location,location_name='HQ',location_code='NEW')).status_code==409

def test_preview_confirm_and_reserved_columns(lr_app,panel):
    client,headers,location=panel
    def upload(data):
        return client.post('/admin/consignments/import',data={'file':(io.BytesIO(data.encode()),'shipments.csv'),'location_code':'HQ'},headers=headers)
    assert upload('LR No,status\nMANUAL,In Transit').status_code==400
    preview=upload('status,boxes_pallets\nIn Transit,2\nDelivered,1')
    assert preview.status_code==200,preview.get_data(as_text=True)
    import re
    batch=re.search('name="batch_id" value="([^"]+)"',preview.get_data(as_text=True))[1]
    with lr_app.app_context():
        assert db.session.query(Consignment).count()==0
        assert db.session.query(LRSequence).count()==0
    result=client.post('/admin/consignments/import/confirm',json={'batch_id':batch},headers=headers)
    assert result.status_code==200,result.json
    assert [r['lr_number'][-6:] for r in result.json['shipments']]==['000001','000002']
    assert client.post('/admin/consignments/import/confirm',json={'batch_id':batch},headers=headers).json==result.json
    assert b'Import results' in upload('status,boxes_pallets\nIn Transit,2\nDelivered,1').data

def test_parallel_database_issuance_and_overflow(lr_app,panel):
    _,_,location=panel
    with lr_app.app_context():
        engine=db.engine
        user_id=db.session.execute(text('SELECT id FROM admin_users LIMIT 1')).scalar_one()
    def issue(index):
        with engine.begin() as connection:
            connection.execute(text("SELECT set_config('gram.actor_id',:actor,true)"),{'actor':str(user_id)})
            row=connection.execute(text("INSERT INTO consignments(location_id) VALUES (:location) RETURNING identifier_value,lr_issued_at"),{'location':location}).one()
            return row
    with ThreadPoolExecutor(max_workers=5) as pool:
        results=list(pool.map(issue,range(12)))
    ordered=sorted(results,key=lambda r:r[0])
    assert [r[0][-6:] for r in ordered]==[f'{i:06}' for i in range(1,13)]
    assert [r[1] for r in ordered]==sorted(r[1] for r in ordered)
    # Overflow is tested with a separately reconciled location, not by editing counters.
    with engine.begin() as connection:
        identifier=connection.execute(text("INSERT INTO issuing_locations(location_name,location_code) VALUES('Overflow','OF') RETURNING id")).scalar_one()
        fy=financial_year(datetime.now(UTC))
        connection.execute(text("UPDATE issuing_locations SET reconciliation_digest=:digest,reconciliation_reason='reviewed test',opening_counters=CAST(:counters AS jsonb) WHERE id=:id"),{'digest':'0'*64,'counters':json.dumps({fy:999999}),'id':identifier})
    with pytest.raises(Exception,match='capacity exhausted'):
        with engine.begin() as connection:
            connection.execute(text("SELECT set_config('gram.actor_id',:actor,true)"),{'actor':str(user_id)})
            connection.execute(text('INSERT INTO consignments(location_id) VALUES(:id)'),{'id':identifier})

@pytest.mark.parametrize('fields',[{'location_id':None},{'location_id':'unregistered'},{'lr_sequence':1},{'financial_year':'2728'}, {'lr_issued_at':'2020-01-01'}, {'lr_status':'legacy'},{'identifier_value':'BY-PASS'}, {'identifier_type':'AWB'}])
def test_reject_identity_overrides(panel,fields):
    client,headers,location=panel
    row=dict(location_id=location,status='In Transit'); row.update(fields)
    result=client.post('/admin/consignments/save',json={'rows':[row],'idempotency_key':str(uuid4())},headers=headers)
    assert result.status_code==400,result.json


def test_role_and_csrf_controls(lr_app,panel):
    client,headers,location=panel
    assert client.post('/admin/issuing-locations',json={'location_name':'Bad','location_code':'BAD'}).status_code==403
    first=save(panel).json['shipments'][0]
    with lr_app.app_context():
        db.session.execute(text("UPDATE admin_users SET role='operator'"));db.session.commit()
    assert client.post('/admin/issuing-locations',json={'location_name':'Other','location_code':'OT'},headers=headers).status_code==403
    assert client.post('/admin/consignments/'+first['id']+'/cancel',json={'confirmed':True,'reason':'test'},headers=headers).status_code==403
    assert save(panel).status_code==200
    with lr_app.app_context():
        db.session.execute(text("UPDATE admin_users SET role='viewer'"));db.session.commit()
    assert save(panel).status_code==403


def test_database_identity_delete_and_audit_failure(lr_app,panel):
    client,headers,location=panel
    first=save(panel).json['shipments'][0]
    with lr_app.app_context():
        engine=db.engine
        user=db.session.execute(text('SELECT id FROM admin_users LIMIT 1')).scalar_one()
    for sql in ["UPDATE consignments SET identifier_value='HACK'", 'DELETE FROM consignments', 'TRUNCATE consignments CASCADE', 'TRUNCATE lr_sequences', 'TRUNCATE lr_audit_log', "UPDATE lr_audit_log SET action='forged'", 'DELETE FROM audit_logs']:
        with pytest.raises(Exception):
            with engine.begin() as connection:
                connection.execute(text("SELECT set_config('gram.actor_id',:id,true)"),{'id':str(user)})
                connection.execute(text(sql))
    with engine.begin() as connection:
        connection.execute(text("CREATE FUNCTION fail_lr_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'simulated audit failure'; END $$"))
        connection.execute(text('CREATE TRIGGER fail_lr_audit BEFORE INSERT ON lr_audit_log FOR EACH ROW EXECUTE FUNCTION fail_lr_audit()'))
    failed=save(panel)
    assert failed.status_code!=200
    with engine.begin() as connection:
        connection.execute(text('DROP TRIGGER fail_lr_audit ON lr_audit_log'))
    assert save(panel).json['shipments'][0]['lr_number'].endswith('000002')


def test_independent_locations_fy_and_sql_boundary(lr_app,panel):
    client,headers,location=panel
    with lr_app.app_context():
        engine=db.engine
        with engine.begin() as connection:
            for moment,expected in [('2027-03-31 18:29:59+00','2627'),('2027-03-31 18:30:00+00','2728')]:
                assert connection.execute(text('SELECT gram_lr_financial_year(CAST(:moment AS timestamptz))'),{'moment':moment}).scalar_one()==expected
            assert connection.execute(text("SELECT count(*) FROM pg_class WHERE oid IN ('issuing_locations'::regclass,'lr_audit_log'::regclass,'lr_sequences'::regclass) AND relrowsecurity")).scalar_one()==3
            other=connection.execute(text("INSERT INTO issuing_locations(location_name,location_code) VALUES('Pune','PUN') RETURNING id")).scalar_one()
            connection.execute(text("UPDATE issuing_locations SET reconciliation_digest=:digest,reconciliation_reason='test reviewed',opening_counters=CAST(:counters AS jsonb) WHERE id=:id"),{'id':other,'digest':'0'*64,'counters':json.dumps({'2526':12})})
    assert save(panel).json['shipments'][0]['lr_number'].endswith('000001')
    result=client.post('/admin/consignments/save',headers=headers,json={'idempotency_key':str(uuid4()),'rows':[{'location_id':str(other),'status':'In Transit'}]})
    assert result.status_code==200,result.json
    assert result.json['shipments'][0]['lr_number'].startswith('PUN')
    assert result.json['shipments'][0]['lr_number'].endswith('000001')


def test_import_preview_validation_and_rollback(lr_app,panel):
    client,headers,location=panel
    for content in ['location_code,pieces\nHQ,2','location_code,status\nUNKNOWN,In Transit','status,pieces\nIn Transit,-1']:
        response=client.post('/admin/consignments/import',data={'file':(io.BytesIO(content.encode()),'bad.csv'),'location_code':'HQ'},headers=headers)
        assert response.status_code==400,response.data
    preview=client.post('/admin/consignments/import',data={'file':(io.BytesIO(b'status\nIn Transit\nDelivered'),'ok.csv'),'location_code':'HQ'},headers=headers)
    import re
    batch=re.search('name="batch_id" value="([^"]+)"',preview.get_data(as_text=True))[1]
    # Deactivation after preview must invalidate confirmation without issuing anything.
    client.post('/admin/issuing-locations',json=dict(id=location,location_code='HQ',location_name='HQ',is_active=False),headers=headers)
    assert client.post('/admin/consignments/import/confirm',json={'batch_id':batch},headers=headers).status_code==400
    with lr_app.app_context():
        assert db.session.query(Consignment).count()==0
        assert db.session.query(LRSequence).count()==0


def test_history_preserved_and_unapproved_location(app):
    with app.app_context():
        with db.engine.begin() as connection:
            identifier=connection.execute(text("INSERT INTO consignments(identifier_value,identifier_type) VALUES('LEGACY-KEEP','AWB') RETURNING id")).scalar_one()
            for column in NEW_COLUMNS:
                connection.exec_driver_sql(f'ALTER TABLE consignments DROP COLUMN {column}')
        migration.migrate(db.engine,apply=True)
        row=db.session.get(Consignment,identifier)
        assert row.identifier_value=='LEGACY-KEEP' and row.identifier_type=='AWB' and row.lr_issued_at is None
        user=db.session.execute(text('SELECT id FROM admin_users LIMIT 1')).scalar_one()
        db.session.rollback()
        with db.engine.begin() as connection:
            location=connection.execute(text("INSERT INTO issuing_locations(location_name,location_code) VALUES('Unreviewed','UR') RETURNING id")).scalar_one()
        with pytest.raises(Exception,match='reconciled'):
            with db.engine.begin() as connection:
                connection.execute(text("SELECT set_config('gram.actor_id',:actor,true)"),{'actor':str(user)})
                connection.execute(text('INSERT INTO consignments(location_id) VALUES(:location)'),{'location':location})


def test_concurrent_idempotent_save_and_import(lr_app,panel):
    client,headers,location=panel
    cookie=client.get_cookie('session').value
    payload=dict(idempotency_key=str(uuid4()),rows=[dict(location_id=location,status='In Transit')])
    def submit(_):
        with lr_app.test_client() as concurrent:
            concurrent.set_cookie('session',cookie)
            result=concurrent.post('/admin/consignments/save',json=payload,headers=headers)
            assert result.status_code==200,result.json
            return result.json
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(submit,range(8)))
    assert all(result==results[0] for result in results)
    preview=client.post('/admin/consignments/import',data={'location_code':'HQ','file':(io.BytesIO(b'status\nIn Transit\nDelivered'),'ok.csv')},headers=headers)
    import re
    batch=re.search('name="batch_id" value="([^"]+)"',preview.get_data(as_text=True))[1]
    def confirm(_):
        with lr_app.test_client() as concurrent:
            concurrent.set_cookie('session',cookie)
            result=concurrent.post('/admin/consignments/import/confirm',json={'batch_id':batch},headers=headers)
            assert result.status_code==200,result.json
            return result.json
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(confirm,range(8)))
    assert all(result==results[0] for result in results)
    with lr_app.app_context():
        assert db.session.query(Consignment).count()==3
        assert db.session.query(LRAudit).count()==3


def test_reviewed_rollback_refuses_issued_state(lr_app,panel):
    save(panel)
    with lr_app.app_context():
        from pathlib import Path
        script=(Path(__file__).resolve().parents[1]/'migrations/sql/005_centralized_lr_rollback.sql').read_text()
        # Outer test transaction owns commit/rollback; production SQL owns its transaction.
        script=script.replace('BEGIN;','',1).replace('\nCOMMIT;','')
        with pytest.raises(Exception,match='Downgrade refused'):
            with db.engine.begin() as connection:
                connection.exec_driver_sql(script,execution_options={'no_parameters':True})
        assert db.session.query(Consignment).count()==1


def test_empty_migration_can_be_rolled_back(lr_app):
    from pathlib import Path
    script=(Path(__file__).resolve().parents[1]/'migrations/sql/005_centralized_lr_rollback.sql').read_text()
    script=script.replace('BEGIN;','',1).replace('\nCOMMIT;','')
    with lr_app.app_context():
        from sqlalchemy import inspect
        with db.engine.begin() as connection:
            connection.exec_driver_sql(script,execution_options={'no_parameters':True})
        assert 'lr_sequences' not in inspect(db.engine).get_table_names()


def test_fresh_model_schema_supports_versioned_upgrade(app):
    with app.app_context():
        # Historical import utilities create current model columns without LR triggers.
        # Upgrade must add constraints/controls without recreating those columns.
        assert migration.migrate(db.engine, apply=True)['status'] == 'applied'
        from sqlalchemy import inspect
        foreign = inspect(db.engine).get_foreign_keys('consignments')
        assert any(f['constrained_columns'] == ['location_id'] for f in foreign)


def test_xlsx_import_limit_and_idempotency_conflict(panel):
    from openpyxl import Workbook
    from app.spreadsheets import save_workbook_in_memory
    client,headers,location=panel
    workbook=Workbook();sheet=workbook.active
    sheet.append(['location_code','status','boxes_pallets']);sheet.append(['HQ','In Transit',2])
    output=io.BytesIO();save_workbook_in_memory(workbook,output);output.seek(0)
    preview=client.post('/admin/consignments/import',data={'file':(output,'shipments.xlsx')},headers=headers)
    assert preview.status_code==200 and b'Review shipment import' in preview.data
    too_many=('status\n'+'In Transit\n'*101).encode()
    response=client.post('/admin/consignments/import',data={'file':(io.BytesIO(too_many),'many.csv'),'location_code':'HQ'},headers=headers)
    assert response.status_code==400 and '100' in response.json['message']
    key=str(uuid4())
    payload={'idempotency_key':key,'rows':[{'location_id':location,'status':'In Transit'}]}
    assert client.post('/admin/consignments/save',json=payload,headers=headers).status_code==200
    payload['rows'][0]['status']='Delivered'
    assert client.post('/admin/consignments/save',json=payload,headers=headers).status_code==400
    assert client.post('/admin/consignments/save',json=['invalid'],headers=headers).status_code==400
    assert client.post('/admin/issuing-locations',json=['id'],headers=headers).status_code==400
