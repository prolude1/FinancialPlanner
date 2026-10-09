import hashlib
import os
import time
import uuid
import warnings
from datetime import timedelta
from copy import deepcopy
from unittest.mock import Mock
import pytest
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import delete

os.environ.setdefault("SESSION_SECRET", "test-session-secret-" * 3)
os.environ.setdefault("BOT_API_SECRET", "test-bot-secret")
os.environ.setdefault("WEB_PASSWORD_HASH", "00" * 16 + ":" + hashlib.pbkdf2_hmac("sha256", b"test-password", bytes(16), 600000).hex())
with warnings.catch_warnings(record=True) as testclient_import_warnings:
    warnings.simplefilter("always")
    from fastapi.testclient import TestClient
assert not any("Using httpx with starlette.testclient is deprecated" in str(warning.message)
               for warning in testclient_import_warnings), \
    "Starlette TestClient must use the pinned httpx2 adapter"
from app.core import store, keycloak_identity
from app.api_server import main as api
from app.telegram_bot import handlers as bot
from app.telegram_bot.client import callback_message
from app.core.domain import empty


def handle_bot_for_test(update, owner, query, mutate, resolver=None, calculate=None,
                        confirm_link=None):
    """Adapt older handler scenario tests to the actor-scoped production interface.

    This adapter exists only in tests; production code never reads planner_state
    for a Telegram conversation or ledger request.
    """
    actor_id = (update.get("message") or {}).get("from", {}).get("id", owner)
    runtime = store.read_bot_runtime()
    if update["update_id"] < runtime.get("offset", 0):
        return None
    def dashboard():
        result = query() or {}
        legacy = store.read()
        if "accounts" not in result:
            result["accounts"] = [dict(value) for value in legacy["accounts"].values()]
        if "credit_accounts" not in result:
            result["credit_accounts"] = [dict(value) for value in legacy["credit_accounts"].values()]
        return result
    def load_state():
        state = store.read()["bot"]
        return {"session": state.get("session"), "last_tvm": state.get("last_tvm")}
    def save_state(**fields):
        with store.transaction() as state:
            state["bot"].update(fields)
    result = bot.handle(update, actor_id, dashboard, mutate, load_state, save_state,
                        calculate=calculate, confirm_link=confirm_link)
    runtime = store.read_bot_runtime()
    runtime["offset"] = max(runtime.get("offset", 0), update["update_id"] + 1)
    store.write_bot_runtime(runtime)
    return result


@pytest.fixture
def client():
    store.migrate()
    with store.transaction() as s:
        s.clear(); s.update(empty())
    with store.engine.begin() as connection:
        connection.execute(delete(store.telegram_link_challenges))
        connection.execute(delete(store.telegram_connections))
        connection.execute(delete(store.telegram_user_state))
        connection.execute(delete(store.tenant_ledger))
        connection.execute(delete(store.tenant_portfolio_history))
        connection.execute(delete(store.legacy_claim_codes))
        connection.execute(store.legacy_claim.update().values(claimed_by=None, claimed_at=None))
        connection.execute(store.bot_runtime.update().values(data={"offset": 0, "session": None}))
    api.attempts.clear()
    api.claim_attempts.clear()
    return TestClient(api.app)


def test_keycloak_linking_and_financial_endpoints_are_principal_scoped(client, monkeypatch):
    issuer, audience = "https://sso.example/realms/planner", "planner-api"
    monkeypatch.setenv("KEYCLOAK_ISSUER", issuer)
    monkeypatch.setenv("KEYCLOAK_AUDIENCE", audience)
    monkeypatch.delenv("KEYCLOAK_JWKS_URL", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "PlannerBot")
    monkeypatch.delenv("TELEGRAM_LINK_BOT_SECRET", raising=False)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    class FakeJwks:
        def get_signing_key_from_jwt(self, token):
            return type("SigningKey", (), {"key": private_key.public_key()})()
    monkeypatch.setattr(keycloak_identity, "jwks_client", lambda _url: FakeJwks())

    def token(subject):
        now = int(time.time())
        return jwt.encode({"iss": issuer, "aud": audience, "sub": subject, "typ": "Bearer",
                           "iat": now, "exp": now + 60}, private_key, algorithm="RS256",
                          headers={"kid": "test-key"})

    assert client.get("/api/me/telegram-link", headers={"X-User-ID": "principal-a"}).status_code == 401
    assert client.get("/api/me/telegram-link", headers={"Authorization": "Bearer invalid"}).status_code == 401
    owner_headers = {"Authorization": "Bearer " + token("owner-a"), "X-User-ID": "attacker"}
    other_headers = {"Authorization": "Bearer " + token("owner-b"), "X-User-ID": "owner-a"}
    assert client.get("/api/me/telegram-link", headers=owner_headers).json() == {"connected": False}
    assert client.get("/api/dashboard", headers=owner_headers).status_code == 200
    challenge_url = "/api/me/telegram-link/challenge"
    assert client.post(challenge_url, headers=owner_headers).status_code == 503
    legacy_secret = "legacy-bot-service-secret-" * 2
    monkeypatch.setenv("BOT_API_SECRET", legacy_secret)
    for unusable_secret in ("short", legacy_secret):
        monkeypatch.setenv("TELEGRAM_LINK_BOT_SECRET", unusable_secret)
        assert client.post(challenge_url, headers=owner_headers).status_code == 503
    monkeypatch.setenv("TELEGRAM_LINK_BOT_SECRET", "link-bot-distinct-secret-" * 2)

    created = client.post(challenge_url, headers=owner_headers)
    assert created.status_code == 201 and created.headers["cache-control"] == "no-store"
    challenge_a = created.json()["challenge"]
    assert created.json()["bot_url"] == f"https://t.me/PlannerBot?start=link_{challenge_a}"
    body = {"challenge": challenge_a, "telegram_user_id": 123456, "telegram_chat_id": 123456,
            "chat_type": "private"}
    confirm_url = "/internal/bot/telegram-link/confirm"
    assert client.post(confirm_url, json=body).status_code == 401
    bot_headers = {"Authorization": "Bearer " + "link-bot-distinct-secret-" * 2}
    assert client.post(confirm_url, json={**body, "chat_type": "group"}, headers=bot_headers).status_code == 422
    assert client.post(confirm_url, json={**body, "telegram_chat_id": -123456}, headers=bot_headers).status_code == 409
    confirmed = client.post(confirm_url, json=body, headers=bot_headers)
    assert confirmed.status_code == 200 and confirmed.json() == {"linked": True}
    assert client.get("/api/me/telegram-link", headers=other_headers).json() == {"connected": False}
    assert client.delete("/api/me/telegram-link", headers=other_headers).json() == {"connected": False}
    assert client.get("/api/me/telegram-link", headers=owner_headers).json()["connected"] is True
    challenge_b = client.post(challenge_url, headers=other_headers).json()["challenge"]
    collision = client.post(confirm_url, headers=bot_headers,
                            json={"challenge": challenge_b, "telegram_user_id": 123456,
                                  "telegram_chat_id": 123456, "chat_type": "private"})
    assert collision.status_code == 409 and collision.json()["detail"]["code"] == "telegram_already_linked"
    assert client.get("/api/me/telegram-link", headers=owner_headers).json()["connected"] is True
    assert client.delete("/api/me/telegram-link", headers=owner_headers).json() == {"connected": False}
    assert client.delete("/api/me/telegram-link", headers=owner_headers).json() == {"connected": False}
    assert client.post(confirm_url, headers=bot_headers,
                       json={"challenge": challenge_b, "telegram_user_id": 123456,
                             "telegram_chat_id": 123456, "chat_type": "private"}).status_code == 200
    assert client.post(confirm_url, headers=bot_headers,
                       json={"challenge": challenge_b, "telegram_user_id": 123456,
                             "telegram_chat_id": 123456, "chat_type": "private"}).status_code == 409


def test_authentication_csrf_and_bot_scope(client):
    assert client.get("/api/dashboard").status_code == 401
    assert client.get("/api/cashflow").status_code == 401
    assert client.post("/api/login", json={"password": "test-password"}).status_code == 403
    r = client.post("/api/login", json={"password": "test-password"}, headers={"Origin": "http://localhost:8080"})
    assert r.status_code == 200
    assert "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/api/dashboard").status_code == 200
    cashflow = client.get("/api/cashflow")
    assert cashflow.status_code == 200
    assert cashflow.json()["selection"]["type"] == "last_12_months"
    assert cashflow.json()["credit_card_spending"]["currencies"] == {}
    assert cashflow.json()["spending_by_transaction_date"]["currencies"] == {}
    year = client.get("/api/cashflow?year=2025")
    assert year.status_code == 200
    assert year.json()["selection"]["start_month"] == "2025-01"
    assert year.json()["selection"]["end_month"] == "2025-12"
    assert not year.json()["selection"]["current_month_partial"]
    assert client.get("/api/cashflow?year=9999").status_code == 422
    assert client.post("/api/commands/account_add", json={}).status_code == 403
    headers = {"Origin": "http://localhost:8080", "X-CSRF-Token": r.json()["csrf"], "Idempotency-Key": "new"}
    assert client.post("/api/commands/account_add", json={"name": "Bank", "type": "bank", "currency": "SGD"}, headers=headers).status_code == 200
    assert client.post("/api/commands/loan_add", json={}, headers={"Authorization": "Bearer test-bot-secret", "Idempotency-Key": "loan"}).status_code == 422


def test_web_transaction_history_exposes_audit_edit_contract_and_conflicts(client):
    login = client.post("/api/login", json={"password": "test-password"},
                        headers={"Origin": "http://localhost:8080"})
    headers = {"Origin": "http://localhost:8080", "X-CSRF-Token": login.json()["csrf"],
               "Idempotency-Key": "history-create-account"}
    account = client.post("/api/commands/account_add", headers=headers,
                          json={"name": "Activity bank", "type": "bank", "currency": "SGD"}).json()
    headers["Idempotency-Key"] = "history-deposit"
    original = client.post("/api/commands/deposit", headers=headers,
                           json={"account": account["id"], "currency": "SGD", "amount": "25.0000000000",
                                 "description": "Salary", "date": "2026-01-02"}).json()
    row = next(entry for entry in client.get("/api/dashboard").json()["history"] if entry["id"] == original["id"])
    assert row["status"] == "active" and row["data"]["amount"] == "25.0000000000"
    assert row["can_edit"] and row["can_void"] and "amount" in row["editable_fields"]
    headers["Idempotency-Key"] = "history-correction"
    corrected = client.post("/api/commands/correct", headers=headers,
                            json={"transaction": original["id"], "changes": {"amount": "30.0000000000"}})
    assert corrected.status_code == 200 and corrected.json()["replaces"] == original["id"]
    history = client.get("/api/dashboard").json()["history"]
    old = next(entry for entry in history if entry["id"] == original["id"])
    assert old["status"] == "superseded" and old["replaced_by"] == corrected.json()["id"]
    assert not old["can_edit"] and not old["editable_fields"]
    headers["Idempotency-Key"] = "history-stale-void"
    stale = client.post("/api/commands/void", headers=headers, json={"transaction": original["id"]})
    assert stale.status_code == 409 and "refresh history" in stale.json()["detail"]
    headers["Idempotency-Key"] = "history-void-current"
    voided = client.post("/api/commands/void", headers=headers,
                         json={"transaction": corrected.json()["id"]})
    assert voided.status_code == 200 and voided.json() == {"id": corrected.json()["id"], "status": "void"}


def test_bot_cannot_use_shared_correction_or_void_commands(client):
    headers = {"Authorization": "Bearer test-bot-secret", "Idempotency-Key": "bot-history-account"}
    account = client.post("/api/commands/account_add", headers=headers,
                          json={"name": "Bot bank", "type": "bank", "currency": "SGD"}).json()
    headers["Idempotency-Key"] = "bot-history-deposit"
    event = client.post("/api/commands/deposit", headers=headers,
                        json={"account": account["id"], "currency": "SGD", "amount": "10.00",
                              "date": "2026-01-01"}).json()
    history = client.get("/api/dashboard", headers={"Authorization": "Bearer test-bot-secret"}).json()["history"]
    assert not next(row for row in history if row["id"] == event["id"])["can_edit"]
    headers["Idempotency-Key"] = "bot-history-correct"
    corrected = client.post("/api/commands/correct", headers=headers,
                            json={"transaction": event["id"], "changes": {"amount": "20.00"}})
    assert corrected.status_code == 422 and "local web UI" in corrected.json()["detail"]
    headers["Idempotency-Key"] = "bot-history-void"
    voided = client.post("/api/commands/void", headers=headers, json={"transaction": event["id"]})
    assert voided.status_code == 422 and "local web UI" in voided.json()["detail"]


def test_api_rolls_back_failed_transaction(client):
    headers = {"Authorization": "Bearer test-bot-secret", "Idempotency-Key": "one"}
    a = client.post("/api/commands/account_add", json={"name": "Bank", "type": "bank"}, headers=headers).json()
    before = store.read()
    headers["Idempotency-Key"] = "two"
    r = client.post("/api/commands/withdraw", json={"account": a["id"], "currency": "SGD", "amount": "10", "date": "2026-01-01"}, headers=headers)
    assert r.status_code == 422 and store.read() == before


def test_pydantic_rejects_missing_and_unexpected_command_fields(client):
    headers = {"Authorization": "Bearer test-bot-secret", "Idempotency-Key": "schema-one"}
    missing = client.post("/api/commands/credit_purchase", json={"amount": "10"}, headers=headers)
    assert missing.status_code == 422 and "Field required" in missing.json()["detail"]
    headers["Idempotency-Key"] = "schema-two"
    extra = client.post("/api/commands/account_add",
        json={"name": "Bank", "type": "bank", "currency": "SGD", "unexpected": True}, headers=headers)
    assert extra.status_code == 422 and "Extra inputs are not permitted" in extra.json()["detail"]


def test_telegram_owner_private_chat_and_guided_commit(client):
    owner = 123
    def update(uid, text, sender=owner, chat_type="private"):
        return {"update_id": uid, "message": {"from": {"id": sender}, "chat": {"id": owner, "type": chat_type}, "text": text}}
    assert not bot.authorized(update(1, "/accounts", sender=999), owner)
    assert not bot.authorized(update(1, "/accounts", chat_type="group"), owner)
    assert not bot.authorized(update(1, "/accounts"), 0)
    saved = []
    def mutate(cmd, p, key):
        saved.append((cmd, deepcopy(p), key)); return {"id": "saved"}
    for uid, text in enumerate(["/account_add", "DBS", "bank account", "sgd"], 1):
        reply = handle_bot_for_test(update(uid, text), owner, lambda: {}, mutate)
    assert "/confirm" in reply and not saved
    assert "CPF subtype" not in reply
    assert "Saved" in handle_bot_for_test(update(5, "/confirm"), owner, lambda: {}, mutate)
    assert saved[0][0] == "account_add" and saved[0][1]["name"] == "DBS"
    assert saved[0][1] == {"name": "DBS", "type": "bank", "currency": "SGD", "cpf_type": ""}
    assert handle_bot_for_test(update(5, "/confirm"), owner, lambda: {}, mutate) is None
    assert len(saved) == 1


def test_telegram_brokerage_skips_cpf_but_cpf_requires_subtype(client):
    owner = 456
    def update(uid, text):
        return {"update_id": uid, "message": {"from": {"id": owner},
                "chat": {"id": owner, "type": "private"}, "text": text}}
    saved = []
    mutate = lambda cmd, payload, key: saved.append((cmd, deepcopy(payload))) or {"id": "saved"}

    replies = [handle_bot_for_test(update(i, text), owner, lambda: {}, mutate)
               for i, text in enumerate(["/account_add", "IBKR", "Brokerage account", "usd"], 100)]
    assert "CPF subtype" not in "\n".join(replies)
    assert "/confirm" in replies[-1]
    handle_bot_for_test(update(104, "/confirm"), owner, lambda: {}, mutate)
    assert saved[-1][1] == {"name": "IBKR", "type": "brokerage", "currency": "USD", "cpf_type": ""}

    replies = [handle_bot_for_test(update(i, text), owner, lambda: {}, mutate)
               for i, text in enumerate(["/account_add", "CPF OA", "CPF", "sgd"], 105)]
    assert replies[-1] == "CPF subtype: OA, SA or MA?"
    reply = handle_bot_for_test(update(109, "oa"), owner, lambda: {}, mutate)
    assert "/confirm" in reply


def test_account_prompts_include_copyable_references_and_rename(client):
    owner = 789
    account_id = "a1b2c3d4e5"
    def update(uid, text):
        return {"update_id": uid, "message": {"from": {"id": owner},
                "chat": {"id": owner, "type": "private"}, "text": text}}
    with store.transaction() as state:
        state["accounts"][account_id] = {"id": account_id, "name": "Main bank", "type": "bank",
            "currency": "SGD", "cpf_type": "", "archived": False}
    saved = []
    mutate = lambda cmd, payload, key: saved.append((cmd, deepcopy(payload))) or {"id": account_id}

    reply = handle_bot_for_test(update(200, "/deposit"), owner, lambda: {}, mutate)
    assert "Tap and hold an ID" in reply and account_id in reply
    assert bot.code_entities(reply) == [{"type": "code", "offset": reply.index(account_id), "length": 10}]
    handle_bot_for_test(update(201, "/cancel"), owner, lambda: {}, mutate)

    reply = handle_bot_for_test(update(202, "/account_rename"), owner, lambda: {}, mutate)
    assert account_id in reply
    assert handle_bot_for_test(update(203, account_id), owner, lambda: {}, mutate) == "What should its new nickname be?"
    assert "/confirm" in handle_bot_for_test(update(204, "Rainy day fund"), owner, lambda: {}, mutate)
    handle_bot_for_test(update(205, "/confirm"), owner, lambda: {}, mutate)
    assert saved[-1][0] == "account_rename"
    assert saved[-1][1] == {"account": account_id, "name": "Rainy day fund"}


def test_telegram_bank_withdrawal_collects_description(client):
    owner, account_id = 788, "0a1b2c3d4e"
    with store.transaction() as state:
        state["accounts"][account_id] = {"id": account_id, "name": "Main bank", "type": "bank",
            "currency": "SGD", "cpf_type": "", "archived": False}
    update = lambda uid, text: {"update_id": uid, "message": {"from": {"id": owner},
        "chat": {"id": owner, "type": "private"}, "text": text}}
    saved = []
    mutate = lambda cmd, payload, key: saved.append((cmd, deepcopy(payload))) or {"id": "saved"}
    replies = [handle_bot_for_test(update(uid, text), owner, lambda: {}, mutate)
               for uid, text in enumerate(["/withdraw", account_id, "SGD 12.50", "Lunch", "today"], 220)]
    assert replies[2] == "Description, e.g. groceries or utilities?"
    assert "tap Today" in replies[3]
    handle_bot_for_test(update(225, "/confirm"), owner, lambda: {}, mutate)
    assert saved[-1][1]["description"] == "Lunch"
    assert saved[-1][1]["currency"] == "SGD"
    assert saved[-1][1]["amount"] == "12.50"


def test_telegram_money_input_retries_and_transfer_collects_both_sides(client):
    owner, source, destination = 787, "1111111111", "2222222222"
    with store.transaction() as state:
        state["accounts"] = {
            source: {"id": source, "name": "SGD bank", "type": "bank", "currency": "SGD",
                     "cpf_type": "", "archived": False},
            destination: {"id": destination, "name": "USD bank", "type": "bank", "currency": "USD",
                          "cpf_type": "", "archived": False},
        }
    update = lambda uid, text: {"update_id": uid, "message": {"from": {"id": owner},
        "chat": {"id": owner, "type": "private"}, "text": text}}
    saved = []
    mutate = lambda cmd, payload, key: saved.append((cmd, deepcopy(payload))) or {"id": "saved"}

    handle_bot_for_test(update(300, "/transfer"), owner, lambda: {}, mutate)
    handle_bot_for_test(update(301, source), owner, lambda: {}, mutate)
    handle_bot_for_test(update(302, destination), owner, lambda: {}, mutate)
    retry = handle_bot_for_test(update(303, "EUR 100"), owner, lambda: {}, mutate)
    assert "using SGD or USD" in retry and "Please retry" in retry
    assert store.read()["bot"]["session"]["index"] == 2
    retry = handle_bot_for_test(update(304, "SGD1,2"), owner, lambda: {}, mutate)
    assert "Please retry" in retry
    assert store.read()["bot"]["session"]["index"] == 2
    assert "received" in handle_bot_for_test(update(305, "sgd135"), owner, lambda: {}, mutate).lower()
    retry = handle_bot_for_test(update(306, "USD"), owner, lambda: {}, mutate)
    assert "Please retry" in retry
    assert "YYYY-MM-DD" in handle_bot_for_test(update(307, "USD1,000.50"), owner, lambda: {}, mutate)
    assert "/confirm" in handle_bot_for_test(update(308, "today"), owner, lambda: {}, mutate)
    handle_bot_for_test(update(309, "/confirm"), owner, lambda: {}, mutate)
    payload = saved[-1][1]
    assert {key: payload[key] for key in ("account", "destination", "currency", "amount", "to_currency", "received")} == {
        "account": source, "destination": destination, "currency": "SGD", "amount": "135",
        "to_currency": "USD", "received": "1000.50"}


def test_telegram_date_button_and_validation_are_shared_across_transaction_types(client):
    owner = 733
    commands = {
        "deposit": {"account": "a", "currency": "SGD", "amount": "5", "description": "test"},
        "buy": {"account": "a", "exchange": "NASDAQ", "symbol": "AAPL", "asset_class": "equity",
                "currency": "USD", "quantity": "1", "price": "5"},
        "credit_purchase": {"card": "card", "description": "test", "amount": "5"},
    }
    for command, data in commands.items():
        session = {"command": command, "data": data, "index": len(bot.FIELDS[command]) - 1,
                   "started": 1, "nonce": "test1234"}
        assert bot.session_keyboard(session, {}) == {"inline_keyboard": [[
            {"text": "Today", "callback_data": "date:today:test1234"}]]}

    owner, account_id = 733, "1111111111"
    with store.transaction() as state:
        state["accounts"][account_id] = {"id": account_id, "name": "Bank", "type": "bank",
            "currency": "SGD", "cpf_type": "", "archived": False}
    def update(uid, text):
        return {"update_id": uid, "message": {"from": {"id": owner},
                "chat": {"id": owner, "type": "private"}, "text": text}}
    saved = []
    mutate = lambda *args: saved.append(args) or {"id": "saved"}
    handle_bot_for_test(update(740, "/deposit"), owner, lambda: {}, mutate)
    handle_bot_for_test(update(741, account_id), owner, lambda: {}, mutate)
    handle_bot_for_test(update(742, "SGD 5"), owner, lambda: {}, mutate)
    handle_bot_for_test(update(743, "Test"), owner, lambda: {}, mutate)
    button_data = store.read()["bot"]["pending_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert button_data.startswith("date:today:")

    callback = {"update_id": 744, "callback_query": {"id": "date-click", "data": button_data,
        "from": {"id": owner}, "message": {"chat": {"id": owner, "type": "private"}}}}
    class Telegram:
        def post(self, *args, **kwargs):
            return Mock(raise_for_status=lambda: None)
    callback_message(callback, Telegram(), "https://telegram.invalid/")
    handle_bot_for_test(callback, owner, lambda: {}, mutate)
    assert "/confirm" in store.read()["bot"]["pending_reply"]
    assert store.read()["bot"]["session"]["data"]["date"] == str(bot.today_in_app_timezone())
    handle_bot_for_test(update(745, "/cancel"), owner, lambda: {}, mutate)
    assert saved == []


def test_telegram_date_retries_cancel_and_valid_date_resets_count(client):
    owner, account_id = 734, "2222222222"
    assert bot.valid_transaction_date("2024-02-29")
    assert not bot.valid_transaction_date("2026-2-01")
    assert not bot.valid_transaction_date("2023-02-29")
    assert not bot.valid_transaction_date("1969-12-31")
    with store.transaction() as state:
        state["accounts"][account_id] = {"id": account_id, "name": "Bank", "type": "bank",
            "currency": "SGD", "cpf_type": "", "archived": False}
        state["bot"]["session"] = {"command": "deposit", "data": {"account": account_id,
            "currency": "SGD", "amount": "5", "description": "test"}, "index": 3, "started": 9999999999}
    def update(uid, text):
        return {"update_id": uid, "message": {"from": {"id": owner},
                "chat": {"id": owner, "type": "private"}, "text": text}}
    saved = []
    mutate = lambda *args: saved.append(args) or {"id": "saved"}
    for uid, invalid in ((750, "1969-12-31"), (751, "2025-02-29")):
        reply = handle_bot_for_test(update(uid, invalid), owner, lambda: {}, mutate)
        assert "attempt(s) remain" in reply
        assert store.read()["bot"]["session"]["index"] == 3
    future = str(bot.today_in_app_timezone() + timedelta(days=1))
    reply = handle_bot_for_test(update(752, future), owner, lambda: {}, mutate)
    assert "cancelled after 3 attempts" in reply
    assert store.read()["bot"]["session"] is None
    assert saved == []

    with store.transaction() as state:
        state["bot"]["offset"] = 753
        state["bot"]["session"] = {"command": "deposit", "data": {"account": account_id,
            "currency": "SGD", "amount": "5", "description": "test"}, "index": 3,
            "date_attempts": 2, "started": 9999999999}
    reply = handle_bot_for_test(update(753, "2024-02-29"), owner, lambda: {}, mutate)
    assert "/confirm" in reply
    assert store.read()["bot"]["session"]["date_attempts"] == 0
    assert store.read()["bot"]["session"]["data"]["date"] == "2024-02-29"


def test_telegram_stale_today_callback_does_not_change_session(client):
    owner = 735
    session = {"command": "deposit", "data": {"account": "2222222222", "currency": "SGD",
        "amount": "5", "description": "test"}, "index": 3, "started": 9999999999,
               "nonce": "new12345"}
    with store.transaction() as state:
        state["bot"]["session"] = session
    callback = {"update_id": 760, "callback_query": {"id": "stale", "data": "date:today:old12345",
        "from": {"id": owner}, "message": {"chat": {"id": owner, "type": "private"}}},
        "message": {"from": {"id": owner}, "chat": {"id": owner, "type": "private"}, "text": "today"}}
    saved = []
    reply = handle_bot_for_test(callback, owner, lambda: {}, lambda *args: saved.append(args))
    assert "expired" in reply and saved == []
    assert store.read()["bot"]["session"] == session


def test_telegram_account_currency_is_limited_to_sgd_and_usd(client):
    owner = 786
    update = lambda uid, text: {"update_id": uid, "message": {"from": {"id": owner},
        "chat": {"id": owner, "type": "private"}, "text": text}}
    handle_bot_for_test(update(320, "/account_add"), owner, lambda: {}, lambda *_: {})
    handle_bot_for_test(update(321, "Travel"), owner, lambda: {}, lambda *_: {})
    handle_bot_for_test(update(322, "bank"), owner, lambda: {}, lambda *_: {})
    retry = handle_bot_for_test(update(323, "EUR"), owner, lambda: {}, lambda *_: {})
    assert "Only SGD and USD are supported" in retry
    assert store.read()["bot"]["session"]["index"] == 2
    assert "/confirm" in handle_bot_for_test(update(324, "usd"), owner, lambda: {}, lambda *_: {})


def test_telegram_accounts_are_grouped_by_type(client):
    owner = 790
    def update(uid, text):
        return {"update_id": uid, "message": {"from": {"id": owner},
                "chat": {"id": owner, "type": "private"}, "text": text}}
    accounts = [
        {"id": "0000000001", "name": "Zeta bank", "type": "bank", "archived": False, "native": {}, "complete": True},
        {"id": "0000000002", "name": "Alpha broker", "type": "brokerage", "archived": False, "native": {"USD": "10"}, "complete": True},
        {"id": "0000000003", "name": "CPF OA", "type": "cpf", "archived": False, "native": {"SGD": "20"}, "complete": True},
        {"id": "0000000004", "name": "Alpha bank", "type": "bank", "archived": False, "native": {}, "complete": True},
    ]
    reply = handle_bot_for_test(update(250, "/accounts"), owner, lambda: {"accounts": accounts}, lambda *args: {})
    assert reply.index("Bank accounts:") < reply.index("Brokerage accounts:") < reply.index("CPF accounts:")
    assert reply.index("Alpha bank") < reply.index("Zeta bank")


@pytest.mark.parametrize("exchange,symbol,expected,expected_class", [
    ("NASDAQ", "AAPL", "USD", "equity"), ("LSE", "VWRA", "USD", "etf"),
])
def test_trade_inputs_are_manual_without_stock_lookup(client, exchange, symbol, expected, expected_class):
    owner, account_id = 901, "b1c2d3e4f5"
    def update(uid, text):
        return {"update_id": uid, "message": {"from": {"id": owner},
                "chat": {"id": owner, "type": "private"}, "text": text}}
    with store.transaction() as state:
        state["accounts"][account_id] = {"id": account_id, "name": "Broker", "type": "brokerage",
            "currency": expected, "cpf_type": "", "archived": False}
    saved = []
    mutate = lambda cmd, payload, key: saved.append((cmd, deepcopy(payload))) or {"id": "saved"}
    inputs = ["/buy", account_id, exchange, symbol, expected_class]
    if exchange == "LSE":
        inputs.append(expected)
    inputs.extend(["2", "100", "2026-09-22"])
    replies = [handle_bot_for_test(update(i, text), owner, lambda: {}, mutate)
               for i, text in enumerate(inputs, 300)]
    assert "Trading symbol" not in replies[-1]
    assert "/confirm" in replies[-1]
    handle_bot_for_test(update(300 + len(inputs), "/confirm"), owner, lambda: {}, mutate)
    assert saved[-1][1]["currency"] == expected
    assert saved[-1][1]["asset_class"] == expected_class


def test_trade_records_manual_ticker_without_reference_lookup(client):
    owner, account_id = 902, "c1d2e3f4a5"
    def update(uid, text):
        return {"update_id": uid, "message": {"from": {"id": owner},
                "chat": {"id": owner, "type": "private"}, "text": text}}
    with store.transaction() as state:
        state["accounts"][account_id] = {"id": account_id, "name": "Broker", "type": "brokerage",
            "currency": "USD", "cpf_type": "", "archived": False}
    mutate = lambda cmd, payload, key: {"id": "saved"}
    for uid, text in enumerate(["/buy", account_id, "LSE", "UNKNOWN", "equity", "USD"], 400):
        reply = handle_bot_for_test(update(uid, text), owner, lambda: {}, mutate)
    assert "Number of shares" in reply


def test_buy_accepts_quantity_slash_unit_price(client):
    owner, account_id = 903, "d1e2f3a4b5"
    def update(uid, text):
        return {"update_id": uid, "message": {"from": {"id": owner},
                "chat": {"id": owner, "type": "private"}, "text": text}}
    with store.transaction() as state:
        state["accounts"][account_id] = {"id": account_id, "name": "Broker", "type": "brokerage",
            "currency": "USD", "cpf_type": "", "archived": False}
    saved = []
    mutate = lambda cmd, payload, key: saved.append((cmd, deepcopy(payload))) or {"id": "saved"}
    inputs = ["/buy", account_id, "NASDAQ", "AAPL", "equity", "2.5/193.40", "2026-09-22"]
    replies = [handle_bot_for_test(update(i, text), owner, lambda: {}, mutate)
               for i, text in enumerate(inputs, 500)]
    assert "/confirm" in replies[-1]
    handle_bot_for_test(update(507, "/confirm"), owner, lambda: {}, mutate)
    assert saved[-1][1]["quantity"] == "2.5"
    assert saved[-1][1]["price"] == "193.40"


def test_trade_accepts_exchange_colon_ticker_shortcut(client):
    owner, account_id = 905, "f1e2d3c4b5"
    def update(uid, text):
        return {"update_id": uid, "message": {"from": {"id": owner},
                "chat": {"id": owner, "type": "private"}, "text": text}}
    with store.transaction() as state:
        state["accounts"][account_id] = {"id": account_id, "name": "Broker", "type": "brokerage",
            "currency": "USD", "cpf_type": "", "archived": False}
    saved = []
    mutate = lambda cmd, payload, key: saved.append((cmd, deepcopy(payload))) or {"id": "saved"}
    inputs = ["/buy", account_id, "NYSE:VOO", "etf", "2/500", "2026-09-22"]
    replies = [handle_bot_for_test(update(i, text), owner, lambda: {}, mutate)
               for i, text in enumerate(inputs, 700)]
    assert "Number of shares" in replies[3]
    assert "/confirm" in replies[-1]
    handle_bot_for_test(update(706, "/confirm"), owner, lambda: {}, mutate)
    assert saved[-1][1]["exchange"] == "NYSE" and saved[-1][1]["symbol"] == "VOO"


def test_telegram_command_menu_keeps_top_level_choices_compact(client):
    commands = bot.telegram_commands()
    names = {item["command"] for item in commands}
    assert {"start", "help", "account", "creditcard", "deposit", "withdraw",
            "transfer", "recurring", "cpf_set", "purchase", "payment", "buy", "sell", "opening_holding",
            "split", "cancel", "calculator"} == names
    assert "stock" not in names
    assert not ({"account_add", "credit_purchase", "confirm"} & names)
    assert not ({"history", "correct", "void"} & names)
    assert len(names) == len(commands)
    expected_order = ["deposit", "withdraw", "purchase", "payment", "buy", "sell", "transfer", "recurring",
                      "cpf_set", "account", "creditcard", "calculator", "opening_holding", "split",
                      "help", "start", "cancel"]
    assert [item["command"] for item in commands] == expected_order

    class Response:
        def raise_for_status(self):
            pass
    calls = []
    class TelegramClient:
        def post(self, url, json):
            calls.append((url, json))
            return Response()
    bot.configure_command_menu(TelegramClient(), "https://telegram.invalid/", 123)
    assert [item["command"] for item in calls[0][1]["commands"]] == expected_order

    owner = 123
    update = lambda uid, text: {"update_id": uid, "message": {"from": {"id": owner},
        "chat": {"id": owner, "type": "private"}, "text": text}}
    for uid, command in ((1, "/help"), (2, "/start")):
        reply = handle_bot_for_test(update(uid, command), owner, lambda: {}, lambda *_: {})
        listing = reply.split("Available commands:\n", 1)[1].split("\nView ", 1)[0]
        listed = [part.strip()[1:] for line in listing.splitlines() for part in line.split(" · ")]
        assert listed == expected_order


def test_telegram_recurring_schedule_create_contract_and_idempotency(client, monkeypatch):
    principal, telegram_id = "recurring-owner", 543210
    bot_secret = "test-recurring-bot-secret-long-enough-123456"
    monkeypatch.setattr(api, "BOT_SECRET", bot_secret)
    with store.engine.begin() as connection:
        connection.execute(store.telegram_connections.insert().values(
            principal=principal, telegram_user_id=telegram_id, linked_at=int(time.time())))
    with store.tenant_transaction(principal) as ledger:
        ledger["accounts"]["BANK000001"] = {
            "id": "BANK000001", "name": "Main bank", "type": "bank",
            "currency": "SGD", "cpf_type": "", "archived": False,
        }

    request = {"telegram_user_id": telegram_id, "telegram_chat_id": telegram_id,
               "schedule": {"account": "BANK000001", "amount": "25.50",
                            "description": "Phone plan", "cadence": "monthly",
                            "day_of_month": 15, "month_of_year": None}}
    headers = {"Authorization": "Bearer " + bot_secret, "Idempotency-Key": "telegram-99001"}
    created = client.post("/internal/bot/financial/recurring-transactions",
                          json=request, headers=headers)
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    schedule = created.json()["schedule"]
    assert {key: schedule[key] for key in (
        "account", "account_name", "amount", "currency", "description", "cadence",
        "day_of_month", "month_of_year", "status", "last_posted_date", "posted_count"
    )} == {
        "account": "BANK000001", "account_name": "Main bank", "amount": "25.50",
        "currency": "SGD", "description": "Phone plan", "cadence": "monthly",
        "day_of_month": 15, "month_of_year": None, "status": "active",
        "last_posted_date": None, "posted_count": 0,
    }
    repeated = client.post("/internal/bot/financial/recurring-transactions",
                           json=request, headers=headers)
    assert repeated.status_code == 201
    assert repeated.json()["schedule"] == schedule

    changed = {**request, "schedule": {**request["schedule"], "amount": "26.00"}}
    conflict = client.post("/internal/bot/financial/recurring-transactions",
                           json=changed, headers=headers)
    assert conflict.status_code == 422


def test_retired_telegram_history_and_edit_commands_redirect_without_mutation(client):
    owner = 918
    update = lambda uid, text: {"update_id": uid, "message": {"from": {"id": owner},
        "chat": {"id": owner, "type": "private"}, "text": text}}
    mutated = []
    mutate = lambda *args: mutated.append(args)

    for uid, command in enumerate(("/history", "/correct", "/void"), 880):
        reply = handle_bot_for_test(update(uid, command), owner, lambda: pytest.fail("must not query history"), mutate)
        assert "web app" in reply and "No changes were made" in reply
    assert mutated == []

    for uid, legacy_command in ((883, "correct"), (884, "void")):
        with store.transaction() as state:
            state["bot"]["session"] = {"command": legacy_command, "data": {"transaction": "deadbeef01"},
                                        "index": 1, "started": 9999999999}
            state["bot"]["offset"] = uid
        reply = handle_bot_for_test(update(uid, "amount"), owner, lambda: {}, mutate)
        assert "older Telegram edit was cancelled" in reply
        assert store.read()["bot"]["session"] is None
    assert mutated == []


def test_help_text_omits_retired_transaction_commands(client):
    owner = 917
    update = lambda uid, text: {"update_id": uid, "message": {"from": {"id": owner},
        "chat": {"id": owner, "type": "private"}, "text": text}}
    for uid, command in ((870, "/help"), (871, "/start")):
        reply = handle_bot_for_test(update(uid, command), owner, lambda: {}, lambda *_: {})
        assert "View or edit transactions in the local web app" in reply
        assert "/history" not in reply and "/correct" not in reply and "/void" not in reply


def test_telegram_start_link_confirms_matching_private_update_without_financial_calls(client):
    challenge, user_id = "A" * 43, 123456789
    pending = {"command": "deposit", "data": {"amount": "5"}, "index": 2,
               "started": 9999999999}
    with store.transaction() as state:
        state["bot"]["session"] = pending
    update = {"update_id": 1000, "message": {"from": {"id": user_id},
        "chat": {"id": user_id, "type": "private"}, "text": "/start link_" + challenge}}
    calls = []
    reply = handle_bot_for_test(update, 999999999, pytest.fail, pytest.fail,
                       confirm_link=lambda *args: calls.append(args) or "linked")

    assert "linked successfully" in reply
    assert calls == [(challenge, user_id, user_id)]
    assert store.read()["bot"]["session"] == pending


@pytest.mark.parametrize("chat_type,chat_id", [("group", 7654321), ("private", 7654321)])
def test_telegram_start_link_rejects_non_private_or_mismatched_chat(client, chat_type, chat_id):
    user_id = 123456789
    update = {"update_id": 1001, "message": {"from": {"id": user_id},
        "chat": {"id": chat_id, "type": chat_type}, "text": "/start link_" + "B" * 43}}
    calls = []
    reply = handle_bot_for_test(update, user_id, pytest.fail, pytest.fail,
                       confirm_link=lambda *args: calls.append(args) or "linked")

    assert "private chat" in reply and "No changes" in reply
    assert calls == []


def test_telegram_start_link_maps_failure_to_safe_copy_and_rejects_malformed_challenge(client):
    user_id, challenge = 123456789, "C" * 43
    def update(uid, value):
        return {"update_id": uid, "message": {"from": {"id": user_id},
            "chat": {"id": user_id, "type": "private"}, "text": "/start link_" + value}}

    expired = handle_bot_for_test(update(1002, challenge), user_id, pytest.fail, pytest.fail,
                         confirm_link=lambda *_: "invalid")
    unavailable = handle_bot_for_test(update(1003, challenge), user_id, pytest.fail, pytest.fail,
                             confirm_link=lambda *_: "unavailable")
    calls = []
    malformed = handle_bot_for_test(update(1004, "short"), user_id, pytest.fail, pytest.fail,
                           confirm_link=lambda *args: calls.append(args) or "linked")

    assert "expired" in expired and challenge not in expired
    assert "temporarily unavailable" in unavailable and challenge not in unavailable
    assert "link is invalid" in malformed and calls == []


def test_account_menu_and_selection_use_inline_buttons(client):
    owner, account_id = 919, "abcde12345"
    with store.transaction() as state:
        state["accounts"][account_id] = {"id": account_id, "name": "Daily bank", "type": "bank",
            "currency": "SGD", "cpf_type": "", "archived": False}
    update = lambda uid, text: {"update_id": uid, "message": {"from": {"id": owner},
        "chat": {"id": owner, "type": "private"}, "text": text}}
    handle_bot_for_test(update(900, "/account"), owner, lambda: {}, lambda *_: {}, None)
    state = store.read()
    assert state["bot"]["pending_markup"]["inline_keyboard"][0][0]["callback_data"] == "cmd:accounts"

    handle_bot_for_test(update(901, "/deposit"), owner, lambda: {}, lambda *_: {}, None)
    state = store.read()
    button = state["bot"]["pending_markup"]["inline_keyboard"][0][0]
    assert button == {"text": "Daily bank", "callback_data": "value:" + account_id}


def test_credit_purchase_chooses_from_all_cards_in_one_step(client):
    owner = 920
    first_group, second_group = "1111111111", "2222222222"
    first_card, second_card = "aaaaaaaaaa", "bbbbbbbbbb"
    with store.transaction() as state:
        state["credit_accounts"] = {
            first_group: {"id": first_group, "name": "Bank A cards", "currency": "SGD",
                          "cards": {first_card: {"id": first_card, "name": "Visa"}}},
            second_group: {"id": second_group, "name": "Bank B cards", "currency": "SGD",
                           "cards": {second_card: {"id": second_card, "name": "Mastercard"}}},
        }
    update = lambda uid, text: {"update_id": uid, "message": {"from": {"id": owner},
        "chat": {"id": owner, "type": "private"}, "text": text}}
    handle_bot_for_test(update(910, "/purchase"), owner, lambda: {}, lambda *_: {}, None)
    buttons = [row[0] for row in store.read()["bot"]["pending_markup"]["inline_keyboard"]]
    assert buttons == [
        {"text": "Bank A cards · Visa", "callback_data": f"card:{first_group}:{first_card}"},
        {"text": "Bank B cards · Mastercard", "callback_data": f"card:{second_group}:{second_card}"},
    ]

    reply = handle_bot_for_test(update(911, f"{second_group}:{second_card}"), owner,
                       lambda: {}, lambda *_: {}, None)
    assert reply == "Purchase description?"
    session = store.read()["bot"]["session"]
    assert session["data"]["credit_account"] == second_group
    assert session["data"]["card"] == second_card


def test_telegram_future_value_uses_shared_calculator_and_buttons(client):
    owner, captured = 921, []
    update = lambda uid, text: {"update_id": uid, "message": {"from": {"id": owner},
        "chat": {"id": owner, "type": "private"}, "text": text}}
    response = {"calculation": "future_value", "result": "99999.5",
        "initial_value_component": "17908.48", "cashflow_component": "82091.02",
        "total_contributions": "70000", "total_interest_or_returns": "29999.5",
        "effective_annual_rate": "6.1678",
        "schedule": [{"period": 12, "closing_balance": "17000"}],
        "assumptions": {"cashflow_timing": "end", "cashflow_frequency": "monthly",
                        "compounding_frequency": "monthly", "annual_rate_percent": "6", "periods": 120}}
    calculate = lambda payload: captured.append(payload) or response
    inputs = ["/futurevalue", "10000", "500", "monthly", "6", "10", "monthly", "end"]
    replies = [handle_bot_for_test(update(uid, text), owner, lambda: {}, lambda *_: {}, None, calculate)
               for uid, text in enumerate(inputs, 930)]
    assert "Future value: 99,999.50" in replies[-1]
    assert captured[0]["calculation"] == "future_value"
    assert captured[0]["include_schedule"] is True
    markup = store.read()["bot"]["pending_markup"]
    assert markup["inline_keyboard"][0][0]["callback_data"] == "tvm:breakdown"


def test_financial_calculator_menu_groups_pv_and_fv(client):
    owner = 922
    update = {"update_id": 950, "message": {"from": {"id": owner},
              "chat": {"id": owner, "type": "private"}, "text": "/calculator"}}
    reply = handle_bot_for_test(update, owner, lambda: {}, lambda *_: {})
    assert reply.startswith("Financial calculator:")
    buttons = [row[0] for row in store.read()["bot"]["pending_markup"]["inline_keyboard"]]
    assert buttons == [
        {"text": "Future value", "callback_data": "cmd:futurevalue"},
        {"text": "Present value", "callback_data": "cmd:presentvalue"},
    ]


def test_telegram_command_menu_is_registered_for_all_users():
    class Response:
        def raise_for_status(self): pass
    class Client:
        def __init__(self): self.calls = []
        def post(self, url, json):
            self.calls.append((url, json))
            return Response()

    client = Client()
    bot.configure_command_menu(client, "https://telegram.test/")
    assert client.calls[0][0].endswith("setMyCommands")
    assert "scope" not in client.calls[0][1]
    assert len(client.calls) == 1


def test_buy_rejects_malformed_quantity_slash_price(client):
    owner, account_id = 904, "e1f2a3b4c5"
    def update(uid, text):
        return {"update_id": uid, "message": {"from": {"id": owner},
                "chat": {"id": owner, "type": "private"}, "text": text}}
    with store.transaction() as state:
        state["accounts"][account_id] = {"id": account_id, "name": "Broker", "type": "brokerage",
            "currency": "USD", "cpf_type": "", "archived": False}
    for uid, text in enumerate(["/buy", account_id, "NASDAQ", "AAPL", "equity", "2/abc"], 600):
        reply = handle_bot_for_test(update(uid, text), owner, lambda: {}, lambda *args: {})
    assert "Use quantity/price" in reply
    assert "Number of shares" in reply


@pytest.mark.parametrize('body', ['[]', 'null', '"password"', '{}', '{"password": 123}', '{', '{"password":"test-password","extra":true}'])
def test_login_rejects_invalid_bodies_without_server_error(client, body):
    response = client.post('/api/login', content=body,
        headers={'Origin': api.ORIGIN, 'Content-Type': 'application/json'})
    assert response.status_code == 422
    assert 'set-cookie' not in response.headers
    assert client.get('/api/dashboard').status_code == 401


def test_login_preserves_password_whitespace(client, monkeypatch):
    password = '  spaced-password  '
    monkeypatch.setattr(api, 'PASSWORD_HASH', '00' * 16 + ':' + hashlib.pbkdf2_hmac(
        'sha256', password.encode(), bytes(16), 600000).hex())
    assert client.post('/api/login', json={'password': password},
        headers={'Origin': api.ORIGIN}).status_code == 200


def test_time_value_calculator_authentication_and_csrf(client):
    payload = {"calculation": "future_value", "initial_value": 1000, "cashflow": 100,
               "cashflow_frequency": "monthly", "cashflow_timing": "end", "annual_rate": 0,
               "duration_years": 1, "compounding_frequency": "monthly"}
    endpoint = "/api/calculators/time-value"
    assert client.post(endpoint, json=payload).status_code == 401
    bot = client.post(endpoint, json=payload, headers={"Authorization": "Bearer test-bot-secret"})
    assert bot.status_code == 200 and bot.json()["result"] == "2200"
    login = client.post("/api/login", json={"password": "test-password"},
                        headers={"Origin": "http://localhost:8080"})
    assert client.post(endpoint, json=payload).status_code == 403
    web = client.post(endpoint, json=payload, headers={"Origin": "http://localhost:8080",
        "X-CSRF-Token": login.json()["csrf"]})
    assert web.status_code == 200 and web.json()["assumptions"]["periods"] == 12


def test_time_value_calculator_rejects_unexpected_fields(client):
    payload = {"calculation": "future_value", "initial_value": 1000, "annual_rate": 6,
               "duration_years": 1, "unexpected": True}
    response = client.post("/api/calculators/time-value", json=payload,
                           headers={"Authorization": "Bearer test-bot-secret"})
    assert response.status_code == 422


def test_time_value_calculator_accepts_multiple_return_streams(client):
    payload = {"calculation": "future_value", "duration_years": 2, "streams": [
        {"name": "Savings", "initial_value": 1000, "cashflow": 100, "annual_rate": 2,
         "cashflow_duration_months": 6},
        {"name": "Investments", "initial_value": 2000, "cashflow": 200, "annual_rate": 7,
         "cashflow_frequency": "quarterly", "compounding_frequency": "annual"}]}
    response = client.post("/api/calculators/time-value", json=payload,
                           headers={"Authorization": "Bearer test-bot-secret"})
    assert response.status_code == 200
    assert response.json()["assumptions"]["stream_count"] == 2
    assert [stream["name"] for stream in response.json()["streams"]] == ["Savings", "Investments"]
    assert response.json()["streams"][0]["schedule"][6]["cashflow"] == "0"


def test_time_value_calculator_rejects_mixed_single_and_stream_fields(client):
    payload = {"calculation": "future_value", "duration_years": 1,
               "initial_value": 1000, "annual_rate": 5,
               "streams": [{"initial_value": 1000, "annual_rate": 6}]}
    response = client.post("/api/calculators/time-value", json=payload,
                           headers={"Authorization": "Bearer test-bot-secret"})
    assert response.status_code == 422


def test_stock_endpoints_require_auth_and_expose_structured_contract(client, monkeypatch):
    overview = {"security": {"security_id": "NASDAQ:AAPL"}, "latest_price": {
        "session_date": "2026-09-22", "close": "101", "prior_close": "100",
        "change": "1", "change_percent": "1", "market_status": "completed"},
        "cache": {"stale": False}}
    fake = Mock()
    fake.overview.return_value = overview
    monkeypatch.setattr(api, "stock_service", fake)
    assert client.get("/api/stocks/NASDAQ:AAPL/overview").status_code == 401
    response = client.get("/api/stocks/NASDAQ:AAPL/overview",
                          headers={"Authorization": "Bearer test-bot-secret"})
    assert response.status_code == 200 and response.json() == overview
    fake.overview.assert_called_once_with("NASDAQ:AAPL")


def test_stock_identity_endpoint_is_authenticated(client, monkeypatch):
    identity = {"security_id": "NASDAQ:AAPL", "symbol": "AAPL", "exchange": "NASDAQ",
                "name": "Apple Inc.", "asset_class": "equity", "currency": "USD",
                "provider_symbol": "AAPL"}
    fake = Mock()
    fake.identity.return_value = identity
    monkeypatch.setattr(api, "stock_service", fake)
    assert client.get("/api/stocks/NASDAQ:AAPL/identity").status_code == 401
    response = client.get("/api/stocks/NASDAQ:AAPL/identity",
                          headers={"Authorization": "Bearer test-bot-secret"})
    assert response.status_code == 200 and response.json() == identity
    fake.identity.assert_called_once_with("NASDAQ:AAPL")


def test_data_export_and_import_require_keycloak_principal_and_are_tenant_scoped(client, monkeypatch):
    monkeypatch.setattr(api, "keycloak_principal", lambda request: "owner-a"
        if request.headers.get("Authorization") == "Bearer owner-a" else (_ for _ in ()).throw(
            api.HTTPException(401, "Sign in with Keycloak")))
    owner = {"Authorization": "Bearer owner-a"}
    bot = {"Authorization": "Bearer test-bot-secret"}
    assert client.get("/api/data-export", headers=bot).status_code == 401
    assert client.post("/api/data-import/validate", headers=bot, files={"file": ("backup.xlsx", b"bad")}).status_code == 401
    assert client.post("/api/data-import/commit", headers=bot, json={}).status_code == 401
    login = client.post("/api/login", json={"password": "test-password"}, headers={"Origin": "http://localhost:8080"})
    assert login.status_code == 200
    assert client.get("/api/data-export").status_code == 401
    exported = client.get("/api/data-export", headers=owner)
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert "financial-planner-export-" in exported.headers["content-disposition"]
    before = store.read()
    no_auth = client.post("/api/data-import/validate", files={"file": ("backup.xlsx", b"bad")})
    assert no_auth.status_code == 401
    assert client.post("/api/data-import/commit", json={}, headers=owner).status_code == 422
    invalid = client.post("/api/data-import/validate", headers=owner,
                          files={"file": ("backup.xlsx", b"not an Excel workbook")})
    assert invalid.status_code == 422
    template = client.get("/api/data-template", headers=owner)
    assert template.status_code == 200
    assert "financial-planner-data-template.xlsx" in template.headers["content-disposition"]
    wrong_extension = client.post("/api/data-import/validate", headers=owner,
                                  files={"file": ("backup.zip", b"not a workbook")})
    assert wrong_extension.status_code == 422
    assert store.read() == before


def test_stock_query_validation_and_provider_errors(client, monkeypatch):
    headers = {"Authorization": "Bearer test-bot-secret"}
    assert client.get("/api/stocks/search?q=A&exchange=INVALID", headers=headers).status_code == 422
    assert client.get("/api/stocks/NASDAQ:AAPL/candles?range=2y", headers=headers).status_code == 422
    fake = Mock()
    fake.overview.side_effect = api.StockProviderError("provider_unavailable", "offline")
    monkeypatch.setattr(api, "stock_service", fake)
    response = client.get("/api/stocks/NASDAQ:AAPL/overview", headers=headers)
    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "provider_unavailable", "message": "offline",
                                          "retryable": True, "stale_available": False}


def test_recurring_schedule_api_is_tenant_and_link_scoped(client, monkeypatch):
    def principal(request):
        token = request.headers.get("Authorization")
        if token == "Bearer owner-a":
            return "owner-a"
        if token == "Bearer owner-b":
            return "owner-b"
        raise api.HTTPException(401, "Sign in with Keycloak")
    monkeypatch.setattr(api, "keycloak_principal", principal)
    with store.tenant_transaction("owner-a") as state:
        state["accounts"]["bank-a"] = {"id": "bank-a", "name": "Salary", "type": "bank",
            "currency": "USD", "archived": False}
    body = {"account": "bank-a", "amount": "20.00", "description": "Monthly saving",
        "cadence": "monthly", "day_of_month": 15, "month_of_year": None}
    assert client.get("/api/recurring-transactions").status_code == 401
    headers_a = {"Authorization": "Bearer owner-a", "Idempotency-Key": "web-schedule-1"}
    created = client.post("/api/recurring-transactions", json=body, headers=headers_a)
    assert created.status_code == 201
    schedule = created.json()["schedule"]
    assert schedule["currency"] == "USD" and schedule["account_name"] == "Salary"
    assert schedule["cadence"] == "monthly" and schedule["status"] == "active"
    retry = client.post("/api/recurring-transactions", json=body, headers=headers_a)
    assert retry.status_code == 201 and retry.json()["schedule"]["id"] == schedule["id"]
    assert len(client.get("/api/recurring-transactions", headers=headers_a).json()["schedules"]) == 1

    headers_b = {"Authorization": "Bearer owner-b"}
    assert client.get("/api/recurring-transactions", headers=headers_b).json()["schedules"] == []
    assert client.patch(f"/api/recurring-transactions/{schedule['id']}", headers=headers_b,
                        json={"amount": "99.00"}).status_code == 404
    edited = client.patch(f"/api/recurring-transactions/{schedule['id']}", headers=headers_a,
                          json={"amount": "25.00"})
    assert edited.status_code == 200 and edited.json()["schedule"]["amount"] == "25.00"
    paused = client.patch(f"/api/recurring-transactions/{schedule['id']}", headers=headers_a,
                          json={"status": "paused"})
    assert paused.status_code == 200 and paused.json()["schedule"]["status"] == "paused"
    assert client.delete(f"/api/recurring-transactions/{schedule['id']}", headers=headers_b).status_code == 404
    deleted = client.delete(f"/api/recurring-transactions/{schedule['id']}", headers=headers_a)
    assert deleted.status_code == 200 and deleted.json()["schedule"]["id"] == schedule["id"]
    assert client.get("/api/recurring-transactions", headers=headers_a).json()["schedules"] == []
    assert client.delete(f"/api/recurring-transactions/{schedule['id']}", headers=headers_a).status_code == 404
    invalid = client.post("/api/recurring-transactions", headers=headers_a,
                          json={**body, "day_of_month": 29, "id": "forged"})
    assert invalid.status_code == 422

    bot_secret = "recurring-test-bot-secret-0123456789012345"
    monkeypatch.setattr(api, "BOT_SECRET", bot_secret)
    bot_headers = {"Authorization": "Bearer " + bot_secret, "Idempotency-Key": "telegram-77"}
    bot_body = {"telegram_user_id": 123456, "telegram_chat_id": 123456, "schedule": body}
    assert client.post("/internal/bot/financial/recurring-transactions", headers=bot_headers,
                       json=bot_body).status_code == 403
    with store.engine.begin() as connection:
        connection.execute(store.telegram_connections.insert().values(
            principal="owner-a", telegram_user_id=123456, linked_at=int(time.time())))
    bot_created = client.post("/internal/bot/financial/recurring-transactions", headers=bot_headers,
                              json=bot_body)
    assert bot_created.status_code == 201
    bot_retry = client.post("/internal/bot/financial/recurring-transactions", headers=bot_headers,
                            json=bot_body)
    assert bot_retry.status_code == 201
    assert bot_retry.json()["schedule"]["id"] == bot_created.json()["schedule"]["id"]
