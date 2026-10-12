from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest
from sqlalchemy import create_engine, event, inspect, insert, select, text

from app.core import migration_runner, store


@pytest.fixture
def migration_engine(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "migrations.db"),
                           connect_args={"timeout": 10})
    yield engine
    engine.dispose()


def test_fresh_database_upgrades_to_head_and_repeat_is_idempotent(migration_engine):
    runner = migration_runner.MigrationRunner(migration_engine)
    assert runner.status()["current"] == []
    assert "alembic_version" not in inspect(migration_engine).get_table_names()
    upgraded = runner.upgrade()
    assert upgraded["current"] == upgraded["heads"] == ["0002_compatibility_schema"]
    with migration_engine.connect() as connection:
        data = connection.execute(select(store.state.c.data)).scalar_one()
        runtime = connection.execute(select(store.bot_runtime.c.data)).scalar_one()
    assert data == {"accounts": {}, "credit_accounts": {}, "events": [], "loans": {},
        "prices": {}, "fx": {}, "revision": 0, "receipts": {},
        "recurring_schedules": [], "provider_status": {}}
    assert runtime == {"offset": 0, "session": None}
    assert runner.upgrade()["current"] == ["0002_compatibility_schema"]


def test_current_unversioned_schema_is_validated_adopted_and_preserved(migration_engine):
    store.metadata.create_all(migration_engine)
    now = datetime.now(timezone.utc)
    state = {"accounts": {"a": {"id": "a"}}, "credit_accounts": {}, "events": [], "loans": {},
        "prices": {}, "fx": {}, "revision": 7, "receipts": {}, "recurring_schedules": [],
        "provider_status": {}, "bot": {"offset": 12, "session": {"command": "deposit"}}}
    with migration_engine.begin() as connection:
        connection.execute(insert(store.state).values(id=1, schema_version=1, data=state))
        connection.execute(insert(store.portfolio_history).values(account="a", date="2026-01-01",
            value_sgd="14", gross_change_sgd=None, adjusted_change_sgd=None,
            external_flow_sgd=None, updated_at=now))
        connection.execute(insert(store.portfolio_deposit_history).values(account="a", date="2026-01-01",
            amount_sgd="10", missing_fx_count=0, stale_fx_count=1,
            stale_fx_last_success_at="2025-12-31T00:00:00Z"))

    runner = migration_runner.MigrationRunner(migration_engine)
    runner.upgrade()
    with migration_engine.connect() as connection:
        migrated = connection.execute(select(store.state.c.data)).scalar_one()
        runtime = connection.execute(select(store.bot_runtime.c.data)).scalar_one()
        value = connection.execute(select(store.portfolio_history.c.value_sgd)).scalar_one()
        deposit = connection.execute(select(store.portfolio_deposit_history)).mappings().one()
    assert migrated == {key: value for key, value in state.items() if key != "bot"}
    assert runtime == state["bot"]
    assert value == "14"
    assert deposit["amount_sgd"] == "10" and deposit["stale_fx_count"] == 1
    assert runner.status()["up_to_date"]


def test_older_unversioned_schema_adds_known_tables_columns_and_moves_bot(migration_engine):
    for table in (store.state, store.instruments, store.portfolio_history, store.stock_cache):
        table.create(migration_engine)
    from sqlalchemy import Column, MetaData, String, Table
    old_metadata = MetaData()
    old_deposits = Table("portfolio_deposit_history", old_metadata,
        Column("account", String(10), primary_key=True), Column("date", String(10), primary_key=True),
        Column("amount_sgd", String(64)))
    old_deposits.create(migration_engine)
    old_tenant_deposits = Table("tenant_portfolio_deposit_history", old_metadata,
        Column("principal", String(64), primary_key=True), Column("account", String(10), primary_key=True),
        Column("date", String(10), primary_key=True), Column("amount_sgd", String(64)))
    old_tenant_deposits.create(migration_engine)
    old_state = {"accounts": {}, "events": [], "loans": {}, "prices": {}, "fx": {}, "revision": 3,
        "receipts": {}, "bot": {"offset": 42, "session": {"command": "deposit"}}}
    with migration_engine.begin() as connection:
        connection.execute(insert(store.state).values(id=1, schema_version=1, data=old_state))
        connection.execute(insert(old_deposits).values(account="legacy", date="2026-01-01", amount_sgd="5"))

    migration_runner.MigrationRunner(migration_engine).upgrade()
    with migration_engine.connect() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("portfolio_deposit_history")}
        deposit = connection.execute(select(store.portfolio_deposit_history)).mappings().one()
        ledger_data = connection.execute(select(store.state.c.data)).scalar_one()
        bot_data = connection.execute(select(store.bot_runtime.c.data)).scalar_one()
    assert {"missing_fx_count", "stale_fx_count", "stale_fx_last_success_at"} <= columns
    assert deposit["amount_sgd"] == "5" and deposit["missing_fx_count"] == 0
    assert "bot" not in ledger_data
    assert bot_data == old_state["bot"]


def test_read_only_revision_commands_do_not_create_version_table(migration_engine):
    runner = migration_runner.MigrationRunner(migration_engine)
    assert runner.current() == []
    assert runner.heads() == ["0002_compatibility_schema"]
    assert runner.history()[0]["revision"] == "0002_compatibility_schema"
    assert "alembic_version" not in inspect(migration_engine).get_table_names()


def test_failed_revision_rolls_back_ddl_and_does_not_stamp(migration_engine):
    def fail_on_compatibility_table(_connection, _cursor, statement, _parameters, _context, _many):
        if "CREATE TABLE tenant_ledger" in statement:
            raise RuntimeError("injected migration failure with synthetic secret")

    event.listen(migration_engine, "before_cursor_execute", fail_on_compatibility_table)
    runner = migration_runner.MigrationRunner(migration_engine)
    with pytest.raises(RuntimeError, match="synthetic secret"):
        runner.upgrade()
    event.remove(migration_engine, "before_cursor_execute", fail_on_compatibility_table)
    assert inspect(migration_engine).get_table_names() == []
    assert runner.upgrade()["up_to_date"]


def test_unknown_alembic_revision_fails_without_schema_changes(migration_engine):
    with migration_engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('9999_future_revision')"))
    before = set(inspect(migration_engine).get_table_names())
    with pytest.raises(Exception):
        migration_runner.MigrationRunner(migration_engine).upgrade()
    assert set(inspect(migration_engine).get_table_names()) == before
    with migration_engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "9999_future_revision"


def test_unversioned_incompatible_workbook_column_type_is_rejected(migration_engine):
    store.metadata.create_all(migration_engine)
    with migration_engine.begin() as connection:
        connection.execute(text("DROP TABLE workbook_confirmations"))
        connection.execute(text("""CREATE TABLE workbook_confirmations (
            token_hash VARCHAR(64) NOT NULL PRIMARY KEY,
            principal VARCHAR(255),
            workbook_sha256 VARCHAR(64) NOT NULL,
            current_revision INTEGER NOT NULL,
            current_etag VARCHAR(64) NOT NULL,
            created_at BIGINT NOT NULL,
            expires_at BIGINT NOT NULL,
            workbook_bytes INTEGER NOT NULL)"""))
    with pytest.raises(migration_runner.MigrationError, match="unsupported column type"):
        migration_runner.MigrationRunner(migration_engine).upgrade()
    assert "alembic_version" not in inspect(migration_engine).get_table_names()


def test_cli_failure_hides_database_exception_details(monkeypatch, capsys):
    class FailingRunner:
        def __init__(self, _engine):
            pass

        def upgrade(self):
            raise RuntimeError("synthetic secret financial marker")

    monkeypatch.setattr(migration_runner, "MigrationRunner", FailingRunner)
    with pytest.raises(SystemExit) as result:
        migration_runner.main(["upgrade"])
    output = capsys.readouterr()
    assert result.value.code == 1
    assert "RuntimeError" in output.err
    assert "synthetic secret financial marker" not in output.err


def test_concurrent_sqlite_upgrade_runners_serialize(migration_engine):
    barrier = Barrier(2)
    def upgrade(_index):
        barrier.wait()
        return migration_runner.MigrationRunner(migration_engine).upgrade()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(upgrade, range(2)))
    assert all(result["current"] == ["0002_compatibility_schema"] for result in results)
