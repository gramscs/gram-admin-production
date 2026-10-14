"""Additive upgrade for existing local and PostgreSQL shipment tables."""

from sqlalchemy import inspect, text


def upgrade_measurement_columns(engine):
    """Rename legacy measurement columns without changing their stored values."""
    renamed = []
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(741239601)"))
        for table_name in ("consignment", "consignments"):
            inspector = inspect(connection)
            if not inspector.has_table(table_name):
                continue
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            for old_name, new_name in (
                ("chargeable_weight", "weight"),
                ("chargeable_volume", "volume"),
            ):
                if old_name in columns and new_name in columns:
                    raise RuntimeError(
                        f"Cannot rename {table_name}.{old_name}: "
                        f"both {old_name} and {new_name} exist."
                    )
                if old_name in columns:
                    connection.execute(text(
                        f'ALTER TABLE "{table_name}" RENAME COLUMN "{old_name}" TO "{new_name}"'
                    ))
                    columns.remove(old_name)
                    columns.add(new_name)
                    renamed.append(f"{table_name}.{new_name}")
    return renamed


def upgrade_consignment_fields(engine):
    added = upgrade_measurement_columns(engine)
    with engine.begin() as connection:
        inspector = inspect(connection)
        if not inspector.has_table("consignment"):
            return added
        columns = {column["name"]: column for column in inspector.get_columns("consignment")}
        definitions = {
            "identifier_type": "VARCHAR(12) NOT NULL DEFAULT 'LRN'",
            "pieces": "INTEGER NOT NULL DEFAULT 1",
            "weight": "NUMERIC(12, 3)",
            "volume": "NUMERIC(12, 3)",
            "measurement_mode": "VARCHAR(12)",
            "volume_unit": "VARCHAR(3)",
            "package_contents": "JSON",
            "company_id": "INTEGER REFERENCES company(id)",
            "pod_original_name": "VARCHAR(255)",
            "invoice_file": "VARCHAR(1024)",
            "invoice_original_name": "VARCHAR(255)",
        }
        for name, definition in definitions.items():
            if name not in columns:
                connection.execute(text(f'ALTER TABLE consignment ADD COLUMN {name} {definition}'))
                added.append(name)
        if engine.dialect.name == "postgresql" and getattr(columns["consignment_number"]["type"], "length", None) is not None and columns["consignment_number"]["type"].length < 64:
            connection.execute(text("ALTER TABLE consignment ALTER COLUMN consignment_number TYPE VARCHAR(64)"))
            added.append("consignment_number length")
    return added


def upgrade_admin_fields(engine):
    """Create master tables and add fields without replacing legacy records."""
    from app.models import Company, CompanyLocation, MisReport, MisView
    Company.__table__.create(engine, checkfirst=True)
    CompanyLocation.__table__.create(engine, checkfirst=True)
    added = upgrade_consignment_fields(engine)
    for model in (MisView, MisReport):
        if not inspect(engine).has_table(model.__tablename__):
            model.__table__.create(engine, checkfirst=True)
            added.append(model.__tablename__ + ' table')
    with engine.begin() as connection:
        inspector = inspect(connection)
        if inspector.has_table("lead"):
            columns = {column["name"] for column in inspector.get_columns("lead")}
            for name, definition in {
                "company_name": "VARCHAR(200)",
                "status": "VARCHAR(20) NOT NULL DEFAULT 'New'",
                "notes": "TEXT",
                "follow_up_date": "DATE",
            }.items():
                if name not in columns:
                    connection.execute(text(f'ALTER TABLE lead ADD COLUMN {name} {definition}'))
                    added.append("lead." + name)
    return added
