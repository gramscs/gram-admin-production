"""Standalone admin application; no dependency on the public website."""

import os
from pathlib import Path

import click
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.models import db

PROJECT_ROOT = Path(__file__).resolve().parent.parent
limiter = Limiter(key_func=get_remote_address)


def create_app(test_config=None):
    env_file = PROJECT_ROOT / ".env"
    example_file = PROJECT_ROOT / ".env.example"
    if env_file.exists():
        load_dotenv(env_file, override=False)
        if not os.getenv("DATABASE_URL", "").strip() and example_file.exists():
            load_dotenv(example_file, override=False)
    elif example_file.exists():
        load_dotenv(example_file, override=False)
    overrides = dict(test_config or {})
    instance_path = Path(overrides.pop("INSTANCE_PATH", PROJECT_ROOT / "instance")).resolve()
    instance_path.mkdir(parents=True, exist_ok=True)
    app = Flask(__name__, instance_path=str(instance_path))
    production = os.getenv("FLASK_ENV", "development").lower() == "production"
    secret_key = os.getenv("SECRET_KEY", "").strip()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if production and not (secret_key or overrides.get("SECRET_KEY")):
        raise RuntimeError("Set SECRET_KEY before starting in production.")
    if production and not os.getenv("ADMIN_PASSWORD_HASH", "").strip():
        raise RuntimeError("Set ADMIN_PASSWORD_HASH before starting in production.")
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://"):]
    placeholder_markers = ("[YOUR_PASSWORD]", "[YOUR-PASSWORD]", "POOLER_HOST", "your_password")
    if database_url and any(marker.lower() in database_url.lower() for marker in placeholder_markers):
        if production:
            raise RuntimeError("Replace the password placeholder in DATABASE_URL before starting in production.")
        app.logger.warning("DATABASE_URL still contains placeholder values; falling back to the local SQLite database for development.")
        database_url = ""
    if production and not (database_url or overrides.get("SQLALCHEMY_DATABASE_URI")):
        raise RuntimeError("Set DATABASE_URL before starting in production.")
    database_url = database_url or f"sqlite:///{instance_path / 'admin.db'}"
    url = make_url(overrides.get("SQLALCHEMY_DATABASE_URI", database_url))
    is_postgres = url.get_backend_name() == "postgresql"
    if is_postgres and url.password == "[YOUR-PASSWORD]":
        raise RuntimeError("Replace the password placeholder in DATABASE_URL through your secure environment settings.")
    is_supabase = bool(url.host) and (
        url.host.endswith(".supabase.co") or url.host.endswith(".pooler.supabase.com")
    )
    if is_postgres and is_supabase and "sslmode" not in url.query:
        url = url.update_query_dict({"sslmode": "require"})
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        database_path = Path(url.database)
        if not database_path.is_absolute():
            database_path = PROJECT_ROOT / database_path
        database_path = database_path.resolve()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        url = url.set(database=str(database_path))

    app.config.update(
        SECRET_KEY=secret_key or "dev-local-secret-key",
        SQLALCHEMY_DATABASE_URI=url.render_as_string(hide_password=False),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production,
        RATELIMIT_STORAGE_URI=os.getenv("RATELIMIT_STORAGE_URI") or os.getenv("REDIS_URL") or "memory://",
        RATELIMIT_HEADERS_ENABLED=True,
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
        AUTO_CREATE_TABLES=os.getenv("AUTO_CREATE_TABLES", "false" if production or is_postgres else "true").lower() == "true",
        ADMIN_DATABASE_MODEL=os.getenv("ADMIN_DATABASE_MODEL", "auto"),
    )
    if is_postgres:
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "pool_pre_ping": True,
            "pool_recycle": 180,
            "pool_size": 3,
            "max_overflow": 2,
            "pool_timeout": 30,
            "connect_args": {"connect_timeout": 10},
        }
    app.config.update(overrides)
    # Use the normalized URI even when it was supplied through test_config.
    app.config["SQLALCHEMY_DATABASE_URI"] = url.render_as_string(hide_password=False)
    db.init_app(app)
    with app.app_context():
        if db.engine.dialect.name == 'sqlite':
            from sqlalchemy import event

            @event.listens_for(db.engine, 'connect')
            def enforce_sqlite_relations(connection, record):
                connection.execute('PRAGMA foreign_keys=ON')
    limiter.init_app(app)

    from app.admin import admin_bp
    app.register_blueprint(admin_bp)
    from app.seed_data import seed_demo
    app.cli.add_command(seed_demo)
    from app.admin.document_lifecycle import purge_removed_documents
    app.cli.add_command(purge_removed_documents)

    @app.get("/")
    def index():
        return redirect(url_for("admin.dashboard"))

    @app.get("/admin")
    def admin_index():
        return redirect(url_for("admin.dashboard"))

    @app.get("/health")
    def health():
        return jsonify(status="ok", application="gram-scs-admin")

    @app.get("/health/db")
    def database_health():
        try:
            db.session.execute(text("SELECT 1"))
            return jsonify(status="ok", database=db.engine.dialect.name)
        except Exception:
            db.session.rollback()
            app.logger.exception("Database health check failed")
            return jsonify(status="error", message="Database connection failed"), 503

    @app.cli.command("init-db")
    def init_db():
        """Create missing tables; does not import or replace existing records."""
        from app.orm import using_er
        if using_er():
            from app.er_adapter import prepare_backend
            prepare_backend(db.engine)
            click.echo("ER backend ready. Existing model tables and data preserved.")
            return
        db.create_all()
        click.echo("Database tables created.")

    @app.cli.command("upgrade-db")
    def upgrade_db():
        """Add admin fields and master tables without deleting records."""
        from app.orm import using_er
        if using_er():
            from app.er_adapter import prepare_backend
            prepare_backend(db.engine)
            click.echo("ER backend ready. Archived screen fields restored; original records preserved.")
            return
        from app.schema_upgrade import upgrade_admin_fields
        changed = upgrade_admin_fields(db.engine)
        db.create_all()
        click.echo("Database upgraded. Existing records preserved. " + ("Added: " + ", ".join(changed) if changed else "Schema already current."))

    @app.cli.command("check-db")
    def check_db():
        """Test the connection and required table structure without changing data."""
        from sqlalchemy import inspect
        try:
            db.session.execute(text("SELECT 1"))
            inspector = inspect(db.engine)
            missing = []
            from app.orm import tables
            for table in tables():
                if not inspector.has_table(table.name):
                    missing.append(f"table {table.name}")
                    continue
                columns = {column["name"] for column in inspector.get_columns(table.name)}
                missing.extend(f"column {table.name}.{column.name}" for column in table.columns if column.name not in columns)
        except Exception:
            db.session.rollback()
            # Connection errors can contain sensitive connection details.
            raise click.ClickException("Database connection failed. Check DATABASE_URL, network access, and Supabase project status.") from None
        click.echo(f"Connected to {db.engine.dialect.name}.")
        if missing:
            raise click.ClickException("Missing required database structure: " + ", ".join(missing))
        click.echo("All required admin tables and columns are present. No data was changed.")

    @app.cli.command("check-storage")
    def check_storage():
        """Check document storage configuration and bucket reads without uploading."""
        from app.admin.consignment_controller import _get_supabase_client
        try:
            client = _get_supabase_client()
        except RuntimeError as error:
            raise click.ClickException(str(error)) from None
        if client is None:
            if os.getenv('SUPABASE_MIS_BUCKET', '').strip():
                raise click.ClickException('Set SUPABASE_URL and SUPABASE_KEY to use the configured report bucket. No files were changed.')
            click.echo('Storage: local. SUPABASE_URL and SUPABASE_KEY are both blank.')
            click.echo(f'Uploads are saved in {Path(app.instance_path, "uploads")}.')
            click.echo('DATABASE_URL connects shipment data only; it does not enable Supabase Storage.')
            click.echo('No files or database records were changed.')
            return
        click.echo('Storage: Supabase.')
        buckets = {
            'POD': os.getenv('SUPABASE_BUCKET', '').strip() or 'pod-uploads',
            'Invoice': os.getenv('SUPABASE_INVOICE_BUCKET', '').strip() or 'invoice-uploads',
        }
        if mis_bucket := os.getenv('SUPABASE_MIS_BUCKET', '').strip():
            if mis_bucket in buckets.values():
                raise click.ClickException('SUPABASE_MIS_BUCKET must be separate from the POD and invoice buckets. No files were changed.')
            buckets['MIS'] = mis_bucket
        else:
            click.echo('MIS: private local report storage (SUPABASE_MIS_BUCKET is blank).')
        failed = []
        for kind, bucket in buckets.items():
            namespace = 'mis_reports' if kind == 'MIS' else 'consignments'
            try:
                client.storage.get_bucket(bucket)
                client.storage.from_(bucket).list(namespace, {'limit': 1})
            except Exception:
                click.echo(f'{kind}: cannot read {bucket}/{namespace}. Check the project, bucket, key and Storage permissions.')
                failed.append(bucket)
            else:
                click.echo(f'{kind}: readable at {bucket}/{namespace}.')
        click.echo('No files or database records were changed. Upload permissions were not tested.')
        if failed:
            raise click.ClickException('Storage checks failed. Existing uploaded files were left untouched.')
        click.echo('Read-only storage check passed.')

    @app.cli.command("repair-consignment-schema")
    def repair_schema():
        """Add missing shipment columns to an existing PostgreSQL database."""
        from app.orm import using_er
        if using_er():
            raise click.ClickException("This migrated database uses the ER model. Use upgrade-db to prepare its backend.")
        if db.engine.dialect.name != "postgresql":
            raise click.ClickException("This command is only for PostgreSQL.")
        from app.db_maintenance import ensure_consignment_columns
        ensure_consignment_columns(app.config["SQLALCHEMY_DATABASE_URI"], app.logger)
        click.echo("Consignment columns checked.")

    if app.config["AUTO_CREATE_TABLES"]:
        with app.app_context():
            from app.orm import using_er
            if using_er():
                from app.er_adapter import prepare_backend
                prepare_backend(db.engine)
            elif db.engine.dialect.name == "sqlite":
                from app.schema_upgrade import upgrade_admin_fields
                upgrade_admin_fields(db.engine)
                db.create_all()
            else:
                db.create_all()

    return app
