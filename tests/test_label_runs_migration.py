"""Versioned label-table upgrades use only the configured PostgreSQL database."""
from datetime import datetime, UTC
import importlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, inspect, select, text

from app.models import db
from database.models import LabelRun
from label_helpers import label_form
from migrations.shipment_v1 import RUNS
from migrations.storage import MigrationError
from test_label_review import create, pages, snapshot

migration = importlib.import_module('migrations.002_label_runs')


def business_data(app):
    return {name: rows for name, rows in snapshot(app).items()
            if name not in ('label_runs', 'admin_migration_runs')}


def remove_test_label_table(app):
    with app.app_context():
        db.session.remove()
        LabelRun.__table__.drop(db.engine)


def test_missing_table_preflight_apply_and_pdf_recovery_preserve_business_records(app, admin_client):
    row = create(admin_client)[0]
    remove_test_label_table(app)
    before = business_data(app)
    with app.app_context():
        result = migration.migrate(db.engine)
        assert result['status'] == 'ready' and result['create_label_runs']
        assert not inspect(db.engine).has_table('label_runs')
        assert db.session.execute(select(RUNS)).first() is None
        db.session.remove()
        result = migration.migrate(db.engine, apply=True)
        assert result['status'] == 'applied' and result['existing_label_runs'] == 0
        inspector = inspect(db.engine)
        assert {column['name'] for column in inspector.get_columns('label_runs')} == set(migration.COLUMN_TYPES)
        assert len(inspector.get_check_constraints('label_runs')) == 3
        assert db.session.execute(text("SELECT relrowsecurity FROM pg_class WHERE oid='label_runs'::regclass")).scalar_one()
        assert db.session.execute(select(RUNS.c.version)).scalars().all() == [migration.VERSION]
    assert business_data(app) == before
    assert len(pages(admin_client.post('/admin/labels/generate', data=label_form([row['id']])))) == 2
    assert business_data(app) == before


def test_existing_numbers_and_import_history_survive_repeated_application(app, admin_client):
    row = create(admin_client)[0]
    data = label_form([row['id']])
    first = pages(admin_client.post('/admin/labels/generate', data=data))
    with app.app_context():
        db.session.execute(RUNS.insert().values(version='001_shipment_database', namespace=uuid4(),
            source_digest='a'*64, target_digest='b'*64, source_schema={}, counts={}, warnings=[], completed_at=datetime.now(UTC)))
        db.session.commit()
    before = snapshot(app)
    with app.app_context():
        assert migration.migrate(db.engine, apply=True)['status'] == 'applied'
    after = snapshot(app)
    assert after['label_runs'] == before['label_runs']
    assert before['admin_migration_runs'][0] in after['admin_migration_runs']
    assert business_data(app) == {k: v for k, v in before.items() if k not in ('label_runs', 'admin_migration_runs')}
    with app.app_context():
        assert migration.migrate(db.engine, apply=True)['status'] == 'already_applied'
    assert snapshot(app) == after
    reprint = pages(admin_client.post('/admin/labels/generate', data=data))
    assert first[0].extract_text() == reprint[0].extract_text()


def test_failure_rolls_back_new_table_and_history_entry(app):
    remove_test_label_table(app)
    with app.app_context():
        engine = db.engine
        def fail_history(connection, cursor, statement, parameters, context, many):
            if statement.startswith('INSERT INTO admin_migration_runs'):
                raise RuntimeError('Simulated history write failure')
        event.listen(engine, 'before_cursor_execute', fail_history)
        try:
            with pytest.raises(RuntimeError, match='Simulated history'):
                migration.migrate(engine, apply=True)
        finally:
            event.remove(engine, 'before_cursor_execute', fail_history)
        assert not inspect(engine).has_table('label_runs')
        assert db.session.execute(select(RUNS)).first() is None


def test_incompatible_table_is_not_replaced(app):
    remove_test_label_table(app)
    with app.app_context():
        with db.engine.begin() as connection:
            connection.execute(text('CREATE TABLE label_runs (id integer primary key, note text)'))
            connection.execute(text("INSERT INTO label_runs VALUES (1, 'Keep this record')"))
        with pytest.raises(MigrationError, match='incompatible'):
            migration.migrate(db.engine, apply=True)
        assert db.session.execute(text('SELECT note FROM label_runs')).scalar_one() == 'Keep this record'
        assert db.session.execute(select(RUNS)).first() is None


def test_recorded_but_missing_table_does_not_reset_numbering(app):
    with app.app_context():
        migration.migrate(db.engine, apply=True)
    remove_test_label_table(app)
    with app.app_context():
        with pytest.raises(MigrationError, match='Restore its numbering history'):
            migration.migrate(db.engine, apply=True)
        assert not inspect(db.engine).has_table('label_runs')


def test_non_postgresql_target_is_rejected_before_connecting():
    with pytest.raises(MigrationError, match='PostgreSQL'):
        migration.migrate(SimpleNamespace(dialect=SimpleNamespace(name='sqlite')), apply=True)


def test_cli_uses_app_connection_disables_startup_ddl_and_reports_committed_revision(app, monkeypatch, capsys):
    import app as app_module
    remove_test_label_table(app)
    def configured_application(settings):
        assert settings == {'AUTO_CREATE_TABLES': False}
        return app
    monkeypatch.setattr(app_module, 'create_app', configured_application)
    assert migration.main([]) == 0
    assert 'No database changes were made' in capsys.readouterr().out
    with app.app_context():
        assert not inspect(db.engine).has_table('label_runs')
    assert migration.main(['--apply']) == 0
    output = capsys.readouterr().out
    assert 'Migration committed and verified in PostgreSQL' in output
    with app.app_context():
        assert inspect(db.engine).has_table('label_runs')


def test_cli_connection_errors_do_not_expose_secrets(monkeypatch, capsys):
    import app
    def fail(_):
        raise RuntimeError('postgresql://private-user:secret-password@private-host/db')
    monkeypatch.setattr(app, 'create_app', fail)
    assert migration.main(['--apply']) == 1
    message = capsys.readouterr().err
    assert 'could not be confirmed' in message
    assert 'secret-password' not in message and 'private-host' not in message
