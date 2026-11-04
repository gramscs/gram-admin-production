"""SQL Editor export resolves schemas and shares the original 005 migration ledger."""
from pathlib import Path
import pytest
from sqlalchemy import inspect, text
from app.models import db
from migrations.export_lr_sql import migration, render_sql


def execute_export(engine):
    with engine.begin() as connection:
        schema = connection.execute(text('SELECT current_schema()')).scalar_one()
        script = render_sql(schema).replace('\nBEGIN;\n', '\n', 1).replace('\nCOMMIT;', '')
        connection.exec_driver_sql(script, execution_options={'no_parameters': True})


def test_sql_editor_export_and_python_runner_share_history(app):
    with app.app_context():
        execute_export(db.engine)
        with db.engine.begin() as connection:
            who = connection.execute(text('SELECT id FROM admin_users LIMIT 1')).scalar_one()
            connection.execute(text("SELECT set_config('gram.actor_id', :who, true)"), {'who': str(who)})
            location = connection.execute(text("INSERT INTO issuing_locations(location_name,location_code) VALUES('Export HQ','HQ') RETURNING id")).scalar_one()
            connection.execute(text("UPDATE issuing_locations SET reconciliation_digest=:digest,reconciliation_reason='Reviewed disposable fixture',opening_counters='{}' WHERE id=:id"), {'digest': '0'*64, 'id': location})
            number = connection.execute(text('INSERT INTO consignments(location_id) VALUES(:id) RETURNING identifier_value'), {'id': location}).scalar_one()
            assert number.startswith('HQ') and number.endswith('000001')
        assert migration.migrate(db.engine, apply=True)['status'] == 'already_applied'
        execute_export(db.engine)
        with db.engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM admin_migration_runs WHERE version='005_centralized_lr'")).scalar_one() == 1
            config = connection.execute(text("SELECT proconfig FROM pg_proc WHERE oid='gram_lr_identity_guard()'::regprocedure")).scalar_one()
            assert any('search_path=' in entry and '__SCHEMA__' not in entry for entry in config)


def test_partial_tables_are_preserved_and_not_marked_applied(app):
    with app.app_context():
        with db.engine.begin() as connection:
            connection.execute(text('CREATE TABLE issuing_locations (id integer PRIMARY KEY)'))
            connection.execute(text('INSERT INTO issuing_locations VALUES (41)'))
        with pytest.raises(Exception, match='Unrecorded LR table'):
            execute_export(db.engine)
        with db.engine.connect() as connection:
            assert connection.execute(text('SELECT id FROM issuing_locations')).scalar_one() == 41
            assert not connection.execute(text("SELECT 1 FROM admin_migration_runs WHERE version='005_centralized_lr'")).first()
        assert not inspect(db.engine).has_table('lr_sequences')


def test_template_failure_rolls_back_and_export_recovers(app):
    with app.app_context():
        with pytest.raises(Exception, match='issuing_locations'):
            with db.engine.begin() as connection:
                connection.exec_driver_sql(migration.SQL_FILE.read_text(), execution_options={'no_parameters': True})
        assert not inspect(db.engine).has_table('issuing_locations')
        execute_export(db.engine)
        assert inspect(db.engine).has_table('issuing_locations')


def test_committed_export_matches_generator():
    path = Path(__file__).resolve().parents[1] / 'migrations/sql/005_centralized_lr_supabase.sql'
    assert path.read_text() == render_sql()
