"""Archive visibility at the shared query and public endpoint boundaries."""
from uuid import uuid4

import pytest
from flask import Flask
from sqlalchemy import create_engine, text
from sqlalchemy.orm import scoped_session, sessionmaker

from app import orm as models
from app.models import db
from app.admin.reporting import filter_shipments
from test_lr_generator import lr_app, lr_client, shipment


def test_shared_query_excludes_archives_even_for_direct_ids(monkeypatch):
    # Only identity columns are needed to exercise the real archive predicate.
    engine = create_engine('sqlite://')
    active, archived = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE consignments (id CHAR(32) PRIMARY KEY)'))
        connection.execute(text('CREATE TABLE shipment_archives (shipment_id CHAR(32) PRIMARY KEY)'))
        for identifier in (active, archived):
            connection.execute(text('INSERT INTO consignments VALUES (:id)'), {'id': identifier.hex})
        connection.execute(text('INSERT INTO shipment_archives VALUES (:id)'), {'id': archived.hex})
    session = scoped_session(sessionmaker(bind=engine))
    monkeypatch.setattr(db, 'session', session)
    app = Flask(__name__)
    app.config['ADMIN_DATABASE_MODEL'] = 'er'
    try:
        with app.app_context():
            def ids(query):
                return {row.id for row in query.with_entities(models.Consignment.id).all()}
            assert ids(models.shipment_query()) == {active}
            assert ids(models.shipment_query(archived=True)) == {archived}
            assert ids(filter_shipments(models.shipment_query(), {'archive': 'archived'})) == {active}
            assert ids(models.shipment_query().filter_by(id=archived)) == set()
            from app.admin.consignment_controller import active_shipment_selection
            with app.test_request_context(json={'consignment_ids': [str(active), str(archived)]}):
                response = active_shipment_selection.__wrapped__()
                assert response.json == {'success': True, 'consignment_ids': [str(active)]}
            assert ids(models.shipment_query().filter(models.Consignment.id.in_([active, archived]))) == {active}
            with engine.begin() as connection:
                connection.execute(text('DELETE FROM shipment_archives'))
            assert ids(models.shipment_query()) == {active, archived}
    finally:
        session.remove()
        engine.dispose()


class EmptyQuery:
    def filter(self, *args): return self
    def filter_by(self, **kwargs): return self
    def order_by(self, *args): return self
    def offset(self, *args): return self
    def limit(self, *args): return self
    def with_for_update(self, **kwargs): return self
    def count(self): return 0
    def all(self): return []
    def first(self): return None


@pytest.mark.parametrize('path,archived', [
    ('/admin/consignments/list', False),
    ('/admin/consignments/list?archive=archived', True),
    ('/admin/documents/list', False),
    ('/admin/documents/list?archive=archived', False),
])
def test_only_consignments_list_can_request_archived_rows(lr_client, monkeypatch, path, archived):
    calls = []
    def query(*, archived=False):
        calls.append(archived)
        return EmptyQuery()
    monkeypatch.setattr(models, 'shipment_query', query)
    response = lr_client.get(path)
    assert response.status_code == 200, response.json
    assert calls == [archived]


@pytest.mark.parametrize('method', ['GET', 'POST', 'DELETE'])
def test_archived_or_missing_shipment_documents_are_unavailable(lr_client, monkeypatch, method):
    monkeypatch.setattr(models, 'shipment_query', lambda: EmptyQuery())
    response = lr_client.open('/admin/consignments/1/pod', method=method)
    assert response.status_code == 404


@pytest.mark.parametrize('module', ['labels', 'lr'])
def test_generators_reject_unavailable_selected_shipments(lr_app, monkeypatch, module):
    from app.admin import labels, lr
    monkeypatch.setattr(models, 'shipment_query', lambda: EmptyQuery())
    with lr_app.app_context(), pytest.raises(ValueError, match='no longer exists'):
        {'labels': labels, 'lr': lr}[module]._selected_rows(['1'])


@pytest.mark.parametrize('payload', [None, {}, {'consignment_ids': '1'}, {'consignment_ids': ['bad']}, {'consignment_ids': ['1'] * 501}])
def test_selection_revalidation_rejects_invalid_requests(lr_client, payload):
    assert lr_client.post('/admin/consignments/selection', json=payload).status_code == 400


def test_selection_revalidation_requires_authentication(lr_app):
    assert lr_app.test_client().post('/admin/consignments/selection', json={'consignment_ids': []}).status_code == 401
