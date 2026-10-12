"""Class-based, locked Alembic runner for application schema upgrades."""
import argparse
from pathlib import Path
import sys

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, Table, UniqueConstraint, inspect, select, text


BASE_REVISION = "0001_initial_schema"
ADVISORY_LOCK_ID = 731940252
_KNOWN_LEDGER_KEYS = {"accounts", "credit_accounts", "events", "loans", "prices", "fx", "revision",
    "receipts", "recurring_schedules", "provider_status", "bot"}
_OPTIONAL_LEGACY_COLUMNS = {
    "portfolio_deposit_history": {"missing_fx_count", "stale_fx_count", "stale_fx_last_success_at"},
    "tenant_portfolio_deposit_history": {"missing_fx_count", "stale_fx_count", "stale_fx_last_success_at"},
}
_LEGACY_NOT_NULLABLE_COLUMNS = {
    ("portfolio_history", "external_flow_sgd"),
    ("tenant_portfolio_history", "external_flow_sgd"),
}
_BASE_REQUIRED_NULLABLE = {
    "planner_state": {"id": False, "schema_version": False, "data": False},
    "instrument_catalog": {"exchange": False, "symbol": False, "name": False,
        "asset_class": True, "currency": True, "native_exchange": True,
        "source": False, "active": False, "updated_at": False},
    "portfolio_history": {"account": False, "date": False, "value_sgd": False,
        "gross_change_sgd": True, "adjusted_change_sgd": True, "external_flow_sgd": None,
        "updated_at": False},
    "stock_cache": {"security_id": False, "kind": False, "data": False,
        "fetched_at": False, "expires_at": False},
}


class MigrationError(RuntimeError):
    """The database schema is not a known safe migration starting point."""


def _matches_column_type(actual, expected):
    from sqlalchemy import DateTime, LargeBinary, String
    if isinstance(expected, String):
        return isinstance(actual, String) and actual.length == expected.length
    if isinstance(expected, DateTime):
        # SQLite stores timestamps without the timezone flag even though the
        # frozen model declares timezone-aware DateTime columns.
        return isinstance(actual, DateTime)
    if isinstance(expected, LargeBinary):
        return isinstance(actual, LargeBinary)
    return type(actual) is type(expected) or str(actual).upper() == str(expected).upper()


class MigrationRunner:
    """Run schema upgrades on one explicitly locked SQLAlchemy connection."""

    def __init__(self, engine, config_path=None):
        self.engine = engine
        self.config_path = Path(config_path) if config_path else (
            Path(__file__).resolve().parents[2] / "alembic.ini")

    def _config(self, connection=None):
        config = Config(str(self.config_path))
        if connection is not None:
            config.attributes["connection"] = connection
        return config

    def _schema_contracts(self):
        scripts = ScriptDirectory.from_config(self._config())
        contracts = {}
        baseline = scripts.get_revision(BASE_REVISION)
        compatibility = scripts.get_revision("0002_compatibility_schema")
        contracts.update({table.name: table for table in baseline.module._frozen_tables()})
        contracts.update({table.name: table for table in compatibility.module._frozen_tables()})
        return contracts

    def _validate_unversioned_schema(self, connection):
        inspector = inspect(connection)
        table_names = set(inspector.get_table_names()) - {"alembic_version"}
        if not table_names:
            return False
        contracts = self._schema_contracts()
        unknown_tables = table_names - set(contracts)
        if unknown_tables:
            raise MigrationError("Unversioned database has unrecognized tables; migration stopped safely")
        if not {"planner_state", "instrument_catalog", "portfolio_history", "stock_cache"} <= table_names:
            raise MigrationError("Unversioned database is missing required base tables; migration stopped safely")
        for table in table_names:
            column_rows = inspector.get_columns(table)
            expected_table = contracts[table]
            expected_columns = {column.name: column for column in expected_table.columns}
            columns = {item["name"] for item in column_rows}
            allowed_missing = _OPTIONAL_LEGACY_COLUMNS.get(table, set())
            if columns - set(expected_columns) or set(expected_columns) - columns - allowed_missing:
                raise MigrationError(f"Unversioned table {table} has an unsupported shape; migration stopped safely")
            for item in column_rows:
                expected = expected_columns[item["name"]]
                if not _matches_column_type(item["type"], expected.type):
                    raise MigrationError(f"Unversioned table {table}.{item['name']} has an unsupported column type "
                                         f"({item['type']}); migration stopped safely")
                expected_nullable = expected.nullable
                if ((table, item["name"]) in _LEGACY_NOT_NULLABLE_COLUMNS
                        and not item["nullable"]):
                    continue
                if item["nullable"] != expected_nullable:
                    raise MigrationError(f"Unversioned table {table}.{item['name']} has unsupported nullability; migration stopped safely")
            primary_key = set(inspector.get_pk_constraint(table).get("constrained_columns") or [])
            expected_key = {column.name for column in expected_table.primary_key.columns}
            if primary_key != expected_key:
                raise MigrationError(f"Unversioned table {table} has an unsupported primary key; migration stopped safely")
            actual_unique = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(table)}
            actual_unique.update(tuple(index["column_names"]) for index in inspector.get_indexes(table)
                                 if index.get("unique"))
            expected_unique = {
                tuple(column.name for column in constraint.columns)
                for constraint in expected_table.constraints
                if isinstance(constraint, UniqueConstraint)
            }
            if not expected_unique.issubset(actual_unique):
                raise MigrationError(f"Unversioned table {table} is missing a required unique constraint; migration stopped safely")
            actual_indexes = {tuple(item["column_names"]) for item in inspector.get_indexes(table)}
            expected_indexes = {tuple(column.name for column in index.columns)
                                for index in expected_table.indexes}
            if not expected_indexes.issubset(actual_indexes):
                raise MigrationError(f"Unversioned table {table} is missing a required index; migration stopped safely")

        metadata = MetaData()
        state = Table("planner_state", metadata, autoload_with=connection)
        state_rows = connection.execute(select(state.c.id, state.c.schema_version, state.c.data)).all()
        if any(row.id != 1 for row in state_rows) or len(state_rows) > 1:
            raise MigrationError("Unversioned planner_state singleton is unsupported; migration stopped safely")
        for _identifier, version, data in state_rows:
            if version != 1 or not isinstance(data, dict) or set(data) - _KNOWN_LEDGER_KEYS:
                raise MigrationError("Unversioned planner state is unsupported; migration stopped safely")
        if "tenant_ledger" in table_names:
            tenants = Table("tenant_ledger", metadata, autoload_with=connection)
            tenant_rows = connection.execute(select(tenants.c.schema_version, tenants.c.data)).all()
            for version, data in tenant_rows:
                if version != 1 or not isinstance(data, dict) or set(data) - (_KNOWN_LEDGER_KEYS - {"bot"}):
                    raise MigrationError("Unversioned tenant ledger is unsupported; migration stopped safely")
        return True

    def upgrade(self):
        """Adopt recognized unversioned schemas and upgrade atomically to head."""
        with self.engine.connect() as connection:
            try:
                if self.engine.dialect.name == "sqlite":
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    connection.begin()
                    if self.engine.dialect.name == "postgresql":
                        connection.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"),
                                           {"lock_id": ADVISORY_LOCK_ID})
                        # The bot-state migration updates planner_state, so serialize
                        # with application writers using their established lock too.
                        connection.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"),
                                           {"lock_id": 731940251})

                tables = set(inspect(connection).get_table_names())
                if "alembic_version" not in tables:
                    if self._validate_unversioned_schema(connection):
                        command.stamp(self._config(connection), BASE_REVISION)
                else:
                    version_table = sa_table_for_version(connection)
                    versions = connection.execute(select(version_table.c.version_num)).scalars().all()
                    if not versions and tables - {"alembic_version"}:
                        raise MigrationError("Empty Alembic version table with application data; migration stopped safely")

                command.upgrade(self._config(connection), "head")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.status()

    def status(self):
        """Read current and head revisions without creating migration tables."""
        with self.engine.connect() as connection:
            if inspect(connection).has_table("alembic_version"):
                version_table = sa_table_for_version(connection)
                current = connection.execute(select(version_table.c.version_num)).scalars().all()
            else:
                current = []
        heads = self.heads()
        return {"current": current, "heads": heads, "up_to_date": bool(current) and set(current) == set(heads)}

    def heads(self):
        scripts = ScriptDirectory.from_config(self._config())
        return scripts.get_heads()

    def history(self):
        scripts = ScriptDirectory.from_config(self._config())
        return [{"revision": revision.revision, "down_revision": revision.down_revision,
                 "message": revision.doc or ""}
                for revision in scripts.iterate_revisions("heads", "base")]

    def current(self):
        return self.status()["current"]


def sa_table_for_version(connection):
    from sqlalchemy import MetaData, Table
    return Table("alembic_version", MetaData(), autoload_with=connection)


def main(argv=None):
    from . import store

    parser = argparse.ArgumentParser(description="Manage database schema revisions")
    parser.add_argument("command", choices=("upgrade", "status", "current", "heads", "history"),
                        nargs="?", default="upgrade")
    args = parser.parse_args(argv)
    runner = MigrationRunner(store.engine)
    try:
        if args.command == "upgrade":
            print(runner.upgrade())
        elif args.command == "status":
            print(runner.status())
        elif args.command == "current":
            print("\n".join(runner.current()) or "unversioned")
        elif args.command == "heads":
            print("\n".join(runner.heads()))
        else:
            for row in runner.history():
                print(f"{row['revision']} <- {row['down_revision']}: {row['message']}")
    except MigrationError as exc:
        print(f"Database migration stopped safely: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as exc:
        print(f"Database migration failed ({type(exc).__name__}); inspect schema status and restore a backup before manual recovery.",
              file=sys.stderr)
        raise SystemExit(1) from None
