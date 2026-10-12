from copy import deepcopy
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import delete, insert, select, text, inspect
from fastapi.testclient import TestClient

from app.core import store, data_workbook, worker
from app.core import telegram_linking
from app.core.domain import empty
from app.api_server import main as api


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    engine = create_engine("sqlite:///" + str(tmp_path / "tenants.db"))
    monkeypatch.setattr(store, "engine", engine)
    monkeypatch.setenv("DATA_ROLLBACK_DIR", str(tmp_path / "rollback"))
    store.migrate()
    with store.transaction() as state:
        state.clear()
        state.update(empty())
    with engine.begin() as connection:
        connection.execute(delete(store.tenant_ledger))
        connection.execute(delete(store.tenant_portfolio_history))
        connection.execute(delete(store.tenant_portfolio_deposit_history))
        connection.execute(delete(store.tenant_portfolio_deposit_fx_rates))
        connection.execute(delete(store.portfolio_deposit_history))
        connection.execute(delete(store.portfolio_deposit_fx_rates))
        connection.execute(delete(store.workbook_confirmations))
        connection.execute(delete(store.legacy_claim_codes))
        connection.execute(store.legacy_claim.update().values(claimed_by=None, claimed_at=None))
    yield engine
    engine.dispose()


def test_tenant_ledgers_are_empty_on_first_read_and_isolated(isolated_store):
    assert store.read_for("principal-a")["accounts"] == {}
    with store.tenant_transaction("principal-a") as state:
        state["accounts"]["a"] = {"id": "a", "name": "A", "type": "bank",
            "currency": "SGD", "cpf_type": "", "archived": False}
        state["revision"] += 1
    assert "a" in store.read_for("principal-a")["accounts"]
    assert store.read_for("principal-b")["accounts"] == {}
    assert store.read_legacy()["accounts"] == {}
    assert store.read_for("principal-a").get("bot") is None


def test_additive_migration_moves_legacy_bot_state_to_singleton_runtime(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    engine = create_engine("sqlite:///" + str(tmp_path / "old-schema.db"))
    monkeypatch.setattr(store, "engine", engine)
    store.state.create(engine)
    old = empty()
    old["bot"] = {"offset": 42, "session": {"command": "deposit"},
        "pending_reply": "resume", "pending_markup": None}
    with engine.begin() as connection:
        connection.execute(store.state.insert().values(id=1, schema_version=1, data=old))
    store.migrate()
    with engine.connect() as connection:
        legacy_row = connection.execute(select(store.state.c.data)).scalar_one()
        runtime = connection.execute(select(store.bot_runtime.c.data)).scalar_one()
    assert "bot" not in legacy_row
    assert runtime == old["bot"]
    assert store.read()["bot"] == old["bot"]
    engine.dispose()


def test_deposit_missing_fx_column_migrates_existing_projection_tables(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    engine = create_engine("sqlite:///" + str(tmp_path / "old-deposit-schema.db"))
    monkeypatch.setattr(store, "engine", engine)
    store.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE portfolio_deposit_history"))
        connection.execute(text("DROP TABLE tenant_portfolio_deposit_history"))
        connection.execute(text("CREATE TABLE portfolio_deposit_history (account VARCHAR(10) NOT NULL, date VARCHAR(10) NOT NULL, amount_sgd VARCHAR(64), PRIMARY KEY (account, date))"))
        connection.execute(text("CREATE TABLE tenant_portfolio_deposit_history (principal VARCHAR(64) NOT NULL, account VARCHAR(10) NOT NULL, date VARCHAR(10) NOT NULL, amount_sgd VARCHAR(64), PRIMARY KEY (principal, account, date))"))
        connection.execute(text("INSERT INTO portfolio_deposit_history VALUES ('a', '2026-01-01', '10')"))
    store.migrate()
    assert "missing_fx_count" in {column["name"] for column in inspect(engine).get_columns("portfolio_deposit_history")}
    assert "missing_fx_count" in {column["name"] for column in inspect(engine).get_columns("tenant_portfolio_deposit_history")}
    assert "stale_fx_count" in {column["name"] for column in inspect(engine).get_columns("portfolio_deposit_history")}
    assert "stale_fx_count" in {column["name"] for column in inspect(engine).get_columns("tenant_portfolio_deposit_history")}
    assert "stale_fx_last_success_at" in {column["name"] for column in inspect(engine).get_columns("portfolio_deposit_history")}
    assert "stale_fx_last_success_at" in {column["name"] for column in inspect(engine).get_columns("tenant_portfolio_deposit_history")}
    assert store.read_portfolio_deposit_history()["a"][0]["amount_sgd"] == "10"
    assert store.read_portfolio_deposit_history()["a"][0]["missing_fx_count"] == 0
    assert store.read_portfolio_deposit_history()["a"][0]["stale_fx_count"] == 0
    assert store.read_portfolio_deposit_history()["a"][0]["stale_fx_last_success_at"] is None
    engine.dispose()


def test_history_migration_allows_null_external_flow_and_preserves_rows(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    engine = create_engine("sqlite:///" + str(tmp_path / "old-history-schema.db"))
    monkeypatch.setattr(store, "engine", engine)
    store.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE portfolio_history"))
        connection.execute(text("DROP TABLE tenant_portfolio_history"))
        connection.execute(text("CREATE TABLE portfolio_history (account VARCHAR(10) NOT NULL, date VARCHAR(10) NOT NULL, value_sgd VARCHAR(64) NOT NULL, gross_change_sgd VARCHAR(64), adjusted_change_sgd VARCHAR(64), external_flow_sgd VARCHAR(64) NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (account, date))"))
        connection.execute(text("CREATE TABLE tenant_portfolio_history (principal VARCHAR(64) NOT NULL, account VARCHAR(10) NOT NULL, date VARCHAR(10) NOT NULL, value_sgd VARCHAR(64) NOT NULL, gross_change_sgd VARCHAR(64), adjusted_change_sgd VARCHAR(64), external_flow_sgd VARCHAR(64) NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (principal, account, date))"))
        connection.execute(text("INSERT INTO portfolio_history VALUES ('a', '2026-01-01', '10', NULL, NULL, '10', '2026-01-01 00:00:00')"))
        connection.execute(text("INSERT INTO tenant_portfolio_history VALUES ('owner', 'b', '2026-01-01', '20', NULL, NULL, '20', '2026-01-01 00:00:00')"))
    store.migrate()
    assert next(column for column in inspect(engine).get_columns("portfolio_history")
                if column["name"] == "external_flow_sgd")["nullable"]
    assert next(column for column in inspect(engine).get_columns("tenant_portfolio_history")
                if column["name"] == "external_flow_sgd")["nullable"]
    store.replace_portfolio_history([{"account": "a", "date": "2026-01-02", "value_sgd": "11",
        "gross_change_sgd": "1", "adjusted_change_sgd": None, "external_flow_sgd": None,
        "updated_at": datetime.now(timezone.utc)}])
    assert store.read_portfolio_history()["a"][0]["external_flow_sgd"] is None
    engine.dispose()


def test_legacy_claim_is_hash_only_atomic_one_use_and_moves_history(isolated_store):
    with store.transaction() as state:
        state["accounts"]["legacy"] = {"id": "legacy", "name": "Legacy", "type": "bank",
            "currency": "SGD", "cpf_type": "", "archived": False}
        state["revision"] = 5
    now = datetime.now(timezone.utc)
    with isolated_store.begin() as connection:
        connection.execute(insert(store.portfolio_history).values(account="legacy", date="2026-01-01",
            value_sgd="12", gross_change_sgd=None, adjusted_change_sgd=None,
            external_flow_sgd="12", updated_at=now))
        connection.execute(insert(store.portfolio_deposit_history).values(account="legacy", date="2026-01-01",
            amount_sgd="50", missing_fx_count=0))
        connection.execute(insert(store.portfolio_deposit_fx_rates).values(currency="USD", date="2026-01-01",
            rate="1.5", fetched_at=1767225600000000000))
    # A login/bootstrap request may already have created the owner's empty tenant.
    assert store.read_for("owner")["accounts"] == {}
    code = store.issue_legacy_claim(now=1000)
    digest = __import__("hashlib").sha256(code.encode()).hexdigest()
    with isolated_store.connect() as connection:
        row = connection.execute(select(store.legacy_claim_codes)).mappings().one()
    assert row["token_hash"] == digest and code not in row["token_hash"]
    with pytest.raises(ValueError):
        store.redeem_legacy_claim("owner", "incorrect", now=1001)
    assert store.redeem_legacy_claim("owner", code, now=1001) == {"status": "claimed"}
    assert store.read_for("owner")["accounts"]["legacy"]["name"] == "Legacy"
    assert store.read_portfolio_history("owner")["legacy"][0]["value_sgd"] == "12"
    assert store.read_portfolio_deposit_history("owner")["legacy"][0]["amount_sgd"] == "50"
    assert store.read_portfolio_deposit_fx_rates("owner") == {
        "USD": {"2026-01-01": {"rate": "1.5", "fetched_at": 1767225600000000000}}}
    assert store.read_portfolio_history() == {}
    assert store.read_portfolio_deposit_history() == {}
    assert store.read_portfolio_deposit_fx_rates() == {}
    with pytest.raises(PermissionError):
        store.read_legacy()
    with pytest.raises(ValueError):
        store.redeem_legacy_claim("another", code, now=1002)
    with pytest.raises(ValueError):
        store.issue_legacy_claim(now=1003)


def test_expired_or_otherwise_invalid_claim_has_same_failure(isolated_store):
    expired = store.issue_legacy_claim(now=1000)
    with pytest.raises(ValueError, match="invalid, expired, or already used"):
        store.redeem_legacy_claim("owner", expired, now=1601)


def test_claim_code_not_consumed_when_target_has_financial_data(isolated_store):
    with store.tenant_transaction("occupied") as state:
        state["accounts"]["a"] = {"id": "a", "name": "A", "type": "bank",
            "currency": "SGD", "cpf_type": "", "archived": False}
    code = store.issue_legacy_claim(now=1000)
    with pytest.raises(ValueError):
        store.redeem_legacy_claim("occupied", code, now=1001)
    assert store.redeem_legacy_claim("owner", code, now=1001) == {"status": "claimed"}


def test_simultaneous_claimers_have_one_winner(isolated_store):
    code = store.issue_legacy_claim(now=1000)
    barrier = Barrier(2)

    def redeem(principal):
        barrier.wait()
        try:
            store.redeem_legacy_claim(principal, code, now=1001)
            return "claimed"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(redeem, ("owner-a", "owner-b")))
    assert sorted(outcomes) == ["claimed", "rejected"]
    winner = "owner-a" if store.tenant_principals() == ["owner-a"] else "owner-b"
    assert store.read_for(winner)["accounts"] == {}


def test_tenant_workbook_excludes_shared_market_tables_and_import_isolated(isolated_store):
    with store.tenant_transaction("owner-a") as state:
        state["accounts"]["a"] = {"id": "a", "name": "A", "type": "bank",
            "currency": "SGD", "cpf_type": "", "archived": False}
        state["revision"] = 1
    now = datetime.now(timezone.utc)
    with isolated_store.begin() as connection:
        connection.execute(insert(store.instruments).values(exchange="NASDAQ", symbol="ABC", name="ABC Inc",
            asset_class="equity", currency="USD", native_exchange="NASDAQ", source="test", active=True,
            updated_at=now))
        connection.execute(insert(store.stock_cache).values(security_id="NASDAQ:ABC", kind="overview",
            data={"price": "12"}, fetched_at=now, expires_at=now))
    store.write_bot_runtime({"offset": 987, "session": {"secret": "global"}, "pending_reply": "legacy"})
    archive, _ = data_workbook.snapshot_bytes(principal="owner-a")
    decoded = data_workbook.read_workbook(archive)["tables"]
    assert decoded["planner_state"]["data"]["accounts"].keys() == {"a"}
    assert decoded["planner_state"]["data"]["bot"] == {"offset": 0, "session": None}
    assert decoded["instrument_catalog"] == [] and decoded["stock_cache"] == []
    preview = data_workbook.validate_workbook(archive, principal="owner-a")
    other_archive, _ = data_workbook.snapshot_bytes(principal="owner-b")
    data_workbook.validate_workbook(other_archive, principal="owner-b")
    first_commit = data_workbook.commit_import(preview["archive_sha256"], preview["current_revision"],
        preview["current_etag"], preview["confirmation_token"], principal="owner-a")
    assert first_commit["status"] == "committed"
    changed = deepcopy(decoded["planner_state"]["data"])
    changed["accounts"]["a"]["name"] = "Restored"
    changed["revision"] = 2
    # Generate a valid replacement archive using the workbook's supported node format.
    snapshot = {"planner_state": [{"id": 1, "schema_version": 1, "data": changed}],
        "instrument_catalog": [], "portfolio_history": [], "stock_cache": []}
    replacement, _ = data_workbook._build_xlsx(snapshot)
    replacement_preview = data_workbook.validate_workbook(replacement, principal="owner-a")
    with pytest.raises(data_workbook.WorkbookError, match="Confirmation token"):
        data_workbook.commit_import(replacement_preview["archive_sha256"],
            replacement_preview["current_revision"], replacement_preview["current_etag"],
            replacement_preview["confirmation_token"], principal="owner-b")
    # A principal-mismatched attempt must not consume the owner's confirmation.
    result = data_workbook.commit_import(replacement_preview["archive_sha256"],
        replacement_preview["current_revision"], replacement_preview["current_etag"],
        replacement_preview["confirmation_token"], principal="owner-a")
    assert result["status"] == "committed"
    assert store.read_for("owner-a")["accounts"]["a"]["name"] == "Restored"
    assert store.read_for("owner-b")["accounts"] == {}
    other_archive, _ = data_workbook.snapshot_bytes(principal="owner-b")
    assert data_workbook.read_workbook(other_archive)["tables"]["planner_state"]["data"]["accounts"] == {}
    with isolated_store.connect() as connection:
        assert connection.execute(select(store.instruments.c.symbol)).scalar_one() == "ABC"
        assert connection.execute(select(store.stock_cache.c.security_id)).scalar_one() == "NASDAQ:ABC"


def test_worker_rebuilds_histories_per_principal_without_crossing_data(isolated_store, monkeypatch):
    today = datetime(2026, 1, 2).date()
    for principal, account in (("owner-a", "a"), ("owner-b", "b")):
        with store.tenant_transaction(principal) as state:
            state["accounts"][account] = {"id": account, "name": account, "type": "brokerage",
                "currency": "SGD", "cpf_type": "", "archived": False}
            state["events"].append({"id": principal, "kind": "opening_cash", "date": str(today),
                "order": 0, "status": "active", "data": {"account": account, "currency": "SGD",
                "amount": "10", "date": str(today)}})
    monkeypatch.setattr(worker, "fetch_price_history", lambda *args: {})
    monkeypatch.setattr(worker, "fetch_fx_history", lambda *args: {})
    # No positions or non-SGD values; each owner independently gets no market calls.
    assert worker.refresh_portfolio_history("owner-a") > 0
    assert set(store.read_portfolio_history("owner-a")) == {"a"}
    assert store.read_portfolio_history("owner-b") == {}


def test_financial_routes_derive_tenant_only_from_authenticated_principal(isolated_store, monkeypatch):
    monkeypatch.setattr(api, "verified_keycloak_principal", lambda request: request.headers["Authorization"].split()[-1])
    client = TestClient(api.app)
    headers_a = {"Authorization": "Bearer owner-a"}
    headers_b = {"Authorization": "Bearer owner-b"}
    response = client.post("/api/commands/account_add", headers={**headers_a, "Idempotency-Key": "web-a-1"}, json={
        "name": "A", "type": "bank", "currency": "SGD"})
    assert response.status_code == 200
    assert client.get("/api/dashboard", headers=headers_b).json()["accounts"] == []
    assert client.get("/api/dashboard", headers=headers_a).json()["accounts"]
    assert client.get("/api/dashboard").status_code == 401


def test_account_brokerage_history_is_authenticated_scoped_and_never_builds_dashboard(
        isolated_store, monkeypatch):
    for principal, account_id in (("owner-a", "acct-a"), ("owner-b", "acct-b")):
        with store.tenant_transaction(principal) as state:
            state["accounts"][account_id] = {"id": account_id, "name": account_id,
                "type": "brokerage", "currency": "SGD", "cpf_type": "", "archived": False}
            state["accounts"]["bank-" + account_id] = {"id": "bank-" + account_id,
                "name": "Bank", "type": "bank", "currency": "SGD", "cpf_type": "", "archived": False}
            if principal == "owner-a":
                state["accounts"]["extra-account"] = {"id": "extra-account", "name": "Other broker",
                    "type": "brokerage", "currency": "SGD", "cpf_type": "", "archived": False}
    now = datetime.now(timezone.utc)
    with isolated_store.begin() as connection:
        for principal, account_id in (("owner-a", "acct-a"), ("owner-b", "acct-b")):
            connection.execute(insert(store.tenant_portfolio_history).values(
                principal=principal, account=account_id, date="2026-01-01", value_sgd="100",
                gross_change_sgd=None, adjusted_change_sgd=None, external_flow_sgd=None, updated_at=now))
            connection.execute(insert(store.tenant_portfolio_deposit_history).values(
                principal=principal, account=account_id, date="2026-01-01", amount_sgd="25",
                missing_fx_count=1, stale_fx_count=2, stale_fx_last_success_at="2025-12-31T12:00:00Z"))
        connection.execute(insert(store.tenant_portfolio_history).values(
            principal="owner-a", account="extra-account", date="2026-01-01", value_sgd="999",
            gross_change_sgd=None, adjusted_change_sgd=None, external_flow_sgd=None, updated_at=now))
        connection.execute(insert(store.tenant_portfolio_deposit_history).values(
            principal="owner-a", account="extra-account", date="2026-01-01", amount_sgd="999",
            missing_fx_count=0, stale_fx_count=0, stale_fx_last_success_at=None))

    monkeypatch.setattr(api, "verified_keycloak_principal",
                        lambda request: request.headers["Authorization"].split()[-1])
    scoped_reads = []
    read_history = store.read_portfolio_history_for_account
    read_deposits = store.read_portfolio_deposit_history_for_account
    monkeypatch.setattr(store, "read_portfolio_history_for_account",
        lambda account_id, principal=None: (scoped_reads.append(("history", principal, account_id)),
            read_history(account_id, principal))[1])
    monkeypatch.setattr(store, "read_portfolio_deposit_history_for_account",
        lambda account_id, principal=None: (scoped_reads.append(("deposits", principal, account_id)),
            read_deposits(account_id, principal))[1])
    monkeypatch.setattr("app.api_server.dashboard_routes.build_dashboard",
                        lambda *_args, **_kwargs: pytest.fail("dashboard assembly should not run"))
    client = TestClient(api.app)
    endpoint = "/api/accounts/acct-a/brokerage-history"
    assert client.get(endpoint).status_code == 401
    response = client.get(endpoint, headers={"Authorization": "Bearer owner-a"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"account_id", "as_of", "portfolio_history", "portfolio_deposits"}
    assert body["account_id"] == "acct-a"
    assert body["as_of"] == api.today().isoformat()
    assert body["portfolio_history"] == [{"date": "2026-01-01", "value_sgd": "100",
        "gross_change_sgd": None, "adjusted_change_sgd": None, "external_flow_sgd": None}]
    assert body["portfolio_deposits"] == [{"date": "2026-01-01", "amount_sgd": "25",
        "missing_fx_count": 1, "stale_fx_count": 2,
        "stale_fx_last_success_at": "2025-12-31T12:00:00Z"}]
    assert scoped_reads == [("history", "owner-a", "acct-a"), ("deposits", "owner-a", "acct-a")]
    assert client.get("/api/accounts/acct-b/brokerage-history",
        headers={"Authorization": "Bearer owner-a"}).status_code == 404
    assert client.get("/api/accounts/bank-acct-a/brokerage-history",
        headers={"Authorization": "Bearer owner-a"}).status_code == 404
    assert client.get("/api/accounts/unknown/brokerage-history",
        headers={"Authorization": "Bearer owner-a"}).status_code == 404


def test_account_brokerage_history_allows_archived_account_with_empty_projections(isolated_store, monkeypatch):
    with store.tenant_transaction("owner") as state:
        state["accounts"]["archived"] = {"id": "archived", "name": "Old broker",
            "type": "brokerage", "currency": "SGD", "cpf_type": "", "archived": True}
    monkeypatch.setattr(api, "verified_keycloak_principal", lambda _request: "owner")
    response = TestClient(api.app).get("/api/accounts/archived/brokerage-history",
        headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    assert response.json()["portfolio_history"] == []
    assert response.json()["portfolio_deposits"] == []


def test_legacy_brokerage_history_stops_after_claim(isolated_store):
    with store.transaction() as state:
        state["accounts"]["legacy-broker"] = {"id": "legacy-broker", "name": "Legacy",
            "type": "brokerage", "currency": "SGD", "cpf_type": "", "archived": False}
    with isolated_store.begin() as connection:
        connection.execute(insert(store.portfolio_history).values(account="legacy-broker",
            date="2026-01-01", value_sgd="10", gross_change_sgd=None,
            adjusted_change_sgd=None, external_flow_sgd=None, updated_at=datetime.now(timezone.utc)))
    client = TestClient(api.app)
    login = client.post("/api/login", json={"password": "test-password"},
        headers={"Origin": "http://localhost:8080"})
    assert login.status_code == 200
    endpoint = "/api/accounts/legacy-broker/brokerage-history"
    assert client.get(endpoint).json()["portfolio_history"][0]["value_sgd"] == "10"
    code = store.issue_legacy_claim()
    store.redeem_legacy_claim("new-owner", code)
    assert client.get(endpoint).status_code == 403


def test_legacy_password_and_bot_credentials_cannot_read_claimed_tenant(isolated_store, monkeypatch):
    secret = "legacy-bot-service-secret-that-is-long-enough"
    monkeypatch.setattr(api, "BOT_SECRET", secret)
    code = store.issue_legacy_claim(now=1000)
    store.redeem_legacy_claim("owner-a", code, now=1001)
    with store.tenant_transaction("owner-a") as state:
        state["accounts"]["owned"] = {"id": "owned", "name": "Private", "type": "bank",
            "currency": "SGD", "cpf_type": "", "archived": False}
    monkeypatch.setattr(api, "verified_keycloak_principal", lambda request: request.headers["Authorization"].split()[-1])
    client = TestClient(api.app)
    login = client.post("/api/login", json={"password": "test-password"},
        headers={"Origin": "http://localhost:8080"})
    assert login.status_code == 200
    assert client.get("/api/dashboard").status_code == 403
    assert client.get("/api/dashboard", headers={"Authorization": "Bearer " + secret}).status_code == 403
    owned = client.get("/api/dashboard", headers={"Authorization": "Bearer owner-a"})
    assert owned.status_code == 200 and owned.json()["accounts"]
    assert client.get("/api/dashboard", headers={"Authorization": "Bearer owner-b"}).json()["accounts"] == []


def test_claim_http_contract_is_authenticated_no_store_and_generic_on_replay(isolated_store, monkeypatch):
    monkeypatch.setattr(api, "keycloak_principal", lambda _request: "owner-a")
    client = TestClient(api.app)
    headers = {"Authorization": "Bearer opaque-test-token"}
    code = store.issue_legacy_claim()
    me = client.get("/api/me", headers=headers)
    assert me.status_code == 200 and me.headers["cache-control"] == "no-store"
    assert me.json() == {"legacy_claim": {"available": True, "completed": False}}
    claimed = client.post("/api/owner-claim", headers=headers, json={"code": code})
    assert claimed.status_code == 200 and claimed.json() == {"status": "claimed"}
    assert claimed.headers["cache-control"] == "no-store"
    replay = client.post("/api/owner-claim", headers=headers, json={"code": code})
    assert replay.status_code == 400
    assert replay.json() == {"detail": {"code": "claim_failed",
        "message": "Claim code is invalid, expired, or already used"}}


def test_internal_bot_uses_linked_telegram_identity_and_isolates_persistent_state(isolated_store, monkeypatch):
    secret = "internal-bot-service-secret-that-is-long-enough"
    monkeypatch.setattr(api, "BOT_SECRET", secret)
    with isolated_store.begin() as connection:
        connection.execute(insert(store.telegram_connections).values(
            principal="owner-a", telegram_user_id=456, linked_at=1))
    client = TestClient(api.app)
    headers = {"Authorization": "Bearer " + secret}
    envelope = {"telegram_user_id": 456, "telegram_chat_id": 456}
    assert client.post("/internal/bot/financial/dashboard", headers=headers,
                       json={**envelope, "principal": "owner-b"}).status_code == 422
    assert client.post("/internal/bot/financial/dashboard", headers=headers,
                       json={"telegram_user_id": 456, "telegram_chat_id": 999}).status_code == 403
    assert client.post("/internal/bot/financial/dashboard", headers=headers,
                       json={"telegram_user_id": 123, "telegram_chat_id": 123}).status_code == 403
    added = client.post("/internal/bot/financial/command/account_add",
        json={**envelope, "payload": {"name": "Bot A", "type": "bank", "currency": "SGD"}},
        headers={**headers, "Idempotency-Key": "bot-a-1"})
    assert added.status_code == 200
    monkeypatch.setattr(api.ledger_service, "portfolio_history",
                        lambda principal: pytest.fail("Bot fetched portfolio history"))
    monkeypatch.setattr(api.ledger_service, "portfolio_deposits",
                        lambda principal: pytest.fail("Bot fetched deposit projections"))
    dashboard = client.post("/internal/bot/financial/dashboard", headers=headers, json=envelope)
    assert dashboard.status_code == 200
    assert "portfolio_history" not in dashboard.json()
    assert "portfolio_deposits" not in dashboard.json()
    assert store.read_for("owner-a")["accounts"]
    assert store.read_for("owner-b")["accounts"] == {}
    state = {"session": {"command": "deposit"}, "pending_reply": "private A",
        "pending_markup": None, "last_tvm": {"future_value": 1250}}
    saved = client.post("/internal/bot/financial/state", headers=headers,
        json={**envelope, "action": "set", **state})
    assert saved.status_code == 200, saved.text
    assert saved.json() == state
    other = client.post("/internal/bot/financial/state", headers=headers,
        json={"telegram_user_id": 789, "telegram_chat_id": 789, "action": "get"})
    assert other.status_code == 403
    loaded = client.post("/internal/bot/financial/state", headers=headers,
        json={**envelope, "action": "get"})
    assert loaded.json() == state
    assert client.post("/internal/bot/financial/dashboard", json=envelope).status_code == 401


def test_link_unlink_and_relink_clear_stale_bot_conversation_state(isolated_store):
    state = {"session": {"command": "deposit"}, "pending_reply": "old owner",
        "pending_markup": None, "last_tvm": {"future_value": 999}}
    telegram_linking.create_challenge("owner-a", now=1000)
    challenge = telegram_linking.create_challenge("owner-a", now=1001)["challenge"]
    telegram_linking.confirm_challenge(challenge, 555, 555, now=1002)
    store.write_telegram_user_state("owner-a", 555, state)
    telegram_linking.unlink("owner-a")
    with isolated_store.connect() as connection:
        assert connection.execute(select(store.telegram_user_state)).first() is None
    challenge = telegram_linking.create_challenge("owner-b", now=1003)["challenge"]
    telegram_linking.confirm_challenge(challenge, 555, 555, now=1004)
    assert store.read_telegram_user_state("owner-b", 555)["pending_reply"] is None
