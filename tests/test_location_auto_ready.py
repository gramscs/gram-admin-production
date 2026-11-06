"""Administrator location saves need no separate counter approval."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from sqlalchemy import text
from app.models import db
from app.lr_issuance import financial_year
from database.lr import IssuingLocation, LRSequence
from datetime import UTC, datetime
from test_lr_issuance import lr_app, panel, save, migration
from auth_support import sign_in


def test_admin_location_immediately_usable(lr_app, panel):
    client, headers, identifier = panel
    options = client.get('/admin/issuing-locations/options').json['locations']
    assert options[0]['code_locked'] is True
    with lr_app.app_context():
        location = db.session.get(IssuingLocation, identifier)
        assert location.reconciliation_digest and location.opening_counters == {}
        assert db.session.query(LRSequence).count() == 0
    result = save(panel)
    assert result.status_code == 200, result.json
    assert result.json['shipments'][0]['lr_number'].endswith('000001')
    page = client.get('/admin/issuing-locations').get_data(as_text=True)
    assert 'reviewed before issuance' not in page and 'Opening counters' not in page


def test_existing_pending_location_initializes_on_first_use(lr_app, panel):
    client, headers, _ = panel
    with lr_app.app_context():
        with db.engine.begin() as connection:
            identifier = connection.execute(text("INSERT INTO issuing_locations(location_name,location_code) VALUES('Old pending','OLD') RETURNING id")).scalar_one()
    result = save((client, headers, str(identifier)))
    assert result.status_code == 200, result.json
    assert result.json['shipments'][0]['lr_number'].startswith('OLD')
    assert result.json['shipments'][0]['lr_number'].endswith('000001')


def test_admin_save_preserves_initialized_counter(lr_app, panel):
    client, headers, identifier = panel
    assert save(panel).status_code == 200
    response = client.post('/admin/issuing-locations', headers=headers, json=dict(id=identifier,location_name='New HQ name',location_code='HQ',is_active=True))
    assert response.status_code == 200, response.json
    assert save(panel).json['shipments'][0]['lr_number'].endswith('000002')


@pytest.mark.parametrize('create_via_admin', [True, False])
def test_cancelled_historical_number_sets_floor(app, create_via_admin):
    fy = financial_year(datetime.now(UTC))
    with app.app_context():
        with db.engine.begin() as connection:
            user = connection.execute(text('SELECT id FROM admin_users LIMIT 1')).scalar_one()
            # Historical fixture predates migration 005. No triggers are disabled.
            connection.execute(text('INSERT INTO consignments(identifier_value) VALUES(:number)'),
                               {'number': 'PUN'+fy+'000042'})
        migration.migrate(db.engine, apply=True)
        __import__('importlib').import_module('migrations.006_shipment_archives').migrate(db.engine, apply=True)
        with db.engine.begin() as connection:
            connection.execute(text("SELECT set_config('gram.actor_id', :actor, true)"), {'actor': str(user)})
            connection.execute(text("UPDATE consignments SET lr_status='cancelled', cancellation_reason='Historical cancellation' WHERE identifier_value=:number"),
                               {'number': 'PUN'+fy+'000042'})
    client = app.test_client()
    assert sign_in(client).status_code == 302
    with client.session_transaction() as state:
        headers = {'X-CSRF-Token': state['auth_csrf_token']}
    if create_via_admin:
        response = client.post('/admin/issuing-locations', headers=headers,
                               json=dict(location_name='Pune', location_code='PUN', is_active=True))
        assert response.status_code == 200, response.json
        pending_id = response.json['id']
    else:
        with app.app_context(), db.engine.begin() as connection:
            pending_id = str(connection.execute(text("INSERT INTO issuing_locations(location_name,location_code) VALUES('Old pending Pune','PUN') RETURNING id")).scalar_one())
    result = save((client, headers, pending_id))
    assert result.status_code == 200, result.json
    assert result.json['shipments'][0]['lr_number'].endswith('000043')
    result = save((client, headers, pending_id))
    assert result.json['shipments'][0]['lr_number'].endswith('000044')


def test_pending_location_concurrent_first_use(lr_app, panel):
    client, headers, _ = panel
    cookie = client.get_cookie('session').value
    with lr_app.app_context():
        with db.engine.begin() as connection:
            identifier = connection.execute(text("INSERT INTO issuing_locations(location_name,location_code) VALUES('Concurrent','CC') RETURNING id")).scalar_one()
    def issue(_):
        with lr_app.test_client() as other:
            other.set_cookie('session', cookie)
            response = other.post('/admin/consignments/save',headers=headers,json={'idempotency_key':str(uuid4()),'rows':[{'location_id':str(identifier),'status':'In Transit'}]})
            assert response.status_code == 200, response.json
            return response.json['shipments'][0]['lr_number'][-6:]
    with ThreadPoolExecutor(max_workers=4) as pool:
        numbers = list(pool.map(issue, range(8)))
    assert sorted(numbers) == [f'{n:06}' for n in range(1,9)]
