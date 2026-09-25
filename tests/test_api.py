import hashlib
import os
import uuid
from copy import deepcopy
from unittest.mock import Mock
import pytest

os.environ.setdefault("SESSION_SECRET", "test-session-secret-" * 3)
os.environ.setdefault("BOT_API_SECRET", "test-bot-secret")
os.environ.setdefault("WEB_PASSWORD_HASH", "00" * 16 + ":" + hashlib.pbkdf2_hmac("sha256", b"test-password", bytes(16), 600000).hex())
from fastapi.testclient import TestClient
from app.core import store
from app.api_server import main as api
from app.telegram_bot import handlers as bot
from app.core.domain import empty


@pytest.fixture
def client():
    store.migrate()
    with store.transaction() as s:
        s.clear(); s.update(empty())
    api.attempts.clear()
    return TestClient(api.app)


def test_authentication_csrf_and_bot_scope(client):
    assert client.get("/api/dashboard").status_code == 401
    assert client.post("/api/login", json={"password": "test-password"}).status_code == 403
    r = client.post("/api/login", json={"password": "test-password"}, headers={"Origin": "http://localhost:8080"})
    assert r.status_code == 200
    assert "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/api/dashboard").status_code == 200
    assert client.post("/api/commands/account_add", json={}).status_code == 403
    headers = {"Origin": "http://localhost:8080", "X-CSRF-Token": r.json()["csrf"], "Idempotency-Key": "new"}
    assert client.post("/api/commands/account_add", json={"name": "Bank", "type": "bank", "currency": "SGD"}, headers=headers).status_code == 200
    assert client.post("/api/commands/loan_add", json={}, headers={"Authorization": "Bearer test-bot-secret", "Idempotency-Key": "loan"}).status_code == 422


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
        reply = bot.handle(update(uid, text), owner, lambda: {}, mutate)
    assert "/confirm" in reply and not saved
    assert "CPF subtype" not in reply
    assert "Saved" in bot.handle(update(5, "/confirm"), owner, lambda: {}, mutate)
    assert saved[0][0] == "account_add" and saved[0][1]["name"] == "DBS"
    assert saved[0][1] == {"name": "DBS", "type": "bank", "currency": "SGD", "cpf_type": ""}
    assert bot.handle(update(5, "/confirm"), owner, lambda: {}, mutate) is None
    assert len(saved) == 1


def test_telegram_brokerage_skips_cpf_but_cpf_requires_subtype(client):
    owner = 456
    def update(uid, text):
        return {"update_id": uid, "message": {"from": {"id": owner},
                "chat": {"id": owner, "type": "private"}, "text": text}}
    saved = []
    mutate = lambda cmd, payload, key: saved.append((cmd, deepcopy(payload))) or {"id": "saved"}

    replies = [bot.handle(update(i, text), owner, lambda: {}, mutate)
               for i, text in enumerate(["/account_add", "IBKR", "Brokerage account", "usd"], 100)]
    assert "CPF subtype" not in "\n".join(replies)
    assert "/confirm" in replies[-1]
    bot.handle(update(104, "/confirm"), owner, lambda: {}, mutate)
    assert saved[-1][1] == {"name": "IBKR", "type": "brokerage", "currency": "USD", "cpf_type": ""}

    replies = [bot.handle(update(i, text), owner, lambda: {}, mutate)
               for i, text in enumerate(["/account_add", "CPF OA", "CPF", "sgd"], 105)]
    assert replies[-1] == "CPF subtype: OA, SA or MA?"
    reply = bot.handle(update(109, "oa"), owner, lambda: {}, mutate)
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

    reply = bot.handle(update(200, "/deposit"), owner, lambda: {}, mutate)
    assert "Tap and hold an ID" in reply and account_id in reply
    assert bot.code_entities(reply) == [{"type": "code", "offset": reply.index(account_id), "length": 10}]
    bot.handle(update(201, "/cancel"), owner, lambda: {}, mutate)

    reply = bot.handle(update(202, "/account_rename"), owner, lambda: {}, mutate)
    assert account_id in reply
    assert bot.handle(update(203, account_id), owner, lambda: {}, mutate) == "What should its new nickname be?"
    assert "/confirm" in bot.handle(update(204, "Rainy day fund"), owner, lambda: {}, mutate)
    bot.handle(update(205, "/confirm"), owner, lambda: {}, mutate)
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
    replies = [bot.handle(update(uid, text), owner, lambda: {}, mutate)
               for uid, text in enumerate(["/withdraw", account_id, "SGD 12.50", "Lunch", "today"], 220)]
    assert replies[2] == "Description, e.g. groceries or utilities?"
    assert replies[3] == "Date (YYYY-MM-DD or today)?"
    bot.handle(update(225, "/confirm"), owner, lambda: {}, mutate)
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

    bot.handle(update(300, "/transfer"), owner, lambda: {}, mutate)
    bot.handle(update(301, source), owner, lambda: {}, mutate)
    bot.handle(update(302, destination), owner, lambda: {}, mutate)
    retry = bot.handle(update(303, "EUR 100"), owner, lambda: {}, mutate)
    assert "using SGD or USD" in retry and "Please retry" in retry
    assert store.read()["bot"]["session"]["index"] == 2
    assert "received" in bot.handle(update(304, "sgd 135"), owner, lambda: {}, mutate).lower()
    retry = bot.handle(update(305, "USD"), owner, lambda: {}, mutate)
    assert "Please retry" in retry
    assert "Transfer date" in bot.handle(update(306, "USD 100"), owner, lambda: {}, mutate)
    assert "/confirm" in bot.handle(update(307, "today"), owner, lambda: {}, mutate)
    bot.handle(update(308, "/confirm"), owner, lambda: {}, mutate)
    payload = saved[-1][1]
    assert {key: payload[key] for key in ("account", "destination", "currency", "amount", "to_currency", "received")} == {
        "account": source, "destination": destination, "currency": "SGD", "amount": "135",
        "to_currency": "USD", "received": "100"}


def test_telegram_account_currency_is_limited_to_sgd_and_usd(client):
    owner = 786
    update = lambda uid, text: {"update_id": uid, "message": {"from": {"id": owner},
        "chat": {"id": owner, "type": "private"}, "text": text}}
    bot.handle(update(320, "/account_add"), owner, lambda: {}, lambda *_: {})
    bot.handle(update(321, "Travel"), owner, lambda: {}, lambda *_: {})
    bot.handle(update(322, "bank"), owner, lambda: {}, lambda *_: {})
    retry = bot.handle(update(323, "EUR"), owner, lambda: {}, lambda *_: {})
    assert "Only SGD and USD are supported" in retry
    assert store.read()["bot"]["session"]["index"] == 2
    assert "/confirm" in bot.handle(update(324, "usd"), owner, lambda: {}, lambda *_: {})


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
    reply = bot.handle(update(250, "/accounts"), owner, lambda: {"accounts": accounts}, lambda *args: {})
    assert reply.index("Bank accounts:") < reply.index("Brokerage accounts:") < reply.index("CPF accounts:")
    assert reply.index("Alpha bank") < reply.index("Zeta bank")


@pytest.mark.parametrize("exchange,symbol,resolver,expected,expected_class", [
    ("NASDAQ", "AAPL", lambda exchange, symbol: {"currency": "USD", "asset_class": "equity"}, "USD", "equity"),
    ("LSE", "VWRA", lambda exchange, symbol: {"currency": "USD", "asset_class": "etf"}, "USD", "etf"),
])
def test_trade_currency_is_detected_and_manual_question_skipped(client, exchange, symbol, resolver, expected, expected_class):
    owner, account_id = 901, "b1c2d3e4f5"
    def update(uid, text):
        return {"update_id": uid, "message": {"from": {"id": owner},
                "chat": {"id": owner, "type": "private"}, "text": text}}
    with store.transaction() as state:
        state["accounts"][account_id] = {"id": account_id, "name": "Broker", "type": "brokerage",
            "currency": expected, "cpf_type": "", "archived": False}
    saved = []
    mutate = lambda cmd, payload, key: saved.append((cmd, deepcopy(payload))) or {"id": "saved"}
    inputs = ["/buy", account_id, exchange, symbol, "2", "100", "2026-09-22"]
    replies = [bot.handle(update(i, text), owner, lambda: {}, mutate, resolver)
               for i, text in enumerate(inputs, 300)]
    assert "Detected trading currency: " + expected in "\n".join(replies)
    assert "Detected asset class: " + expected_class.upper() in "\n".join(replies)
    assert all(reply != "Trading currency, e.g. USD?" for reply in replies)
    assert "/confirm" in replies[-1]
    bot.handle(update(300 + len(inputs), "/confirm"), owner, lambda: {}, mutate, resolver)
    assert saved[-1][1]["currency"] == expected
    assert saved[-1][1]["asset_class"] == expected_class


def test_trade_rejects_unknown_ticker_and_keeps_symbol_prompt(client):
    owner, account_id = 902, "c1d2e3f4a5"
    def update(uid, text):
        return {"update_id": uid, "message": {"from": {"id": owner},
                "chat": {"id": owner, "type": "private"}, "text": text}}
    with store.transaction() as state:
        state["accounts"][account_id] = {"id": account_id, "name": "Broker", "type": "brokerage",
            "currency": "USD", "cpf_type": "", "archived": False}
    mutate = lambda cmd, payload, key: {"id": "saved"}
    for uid, text in enumerate(["/buy", account_id, "LSE", "UNKNOWN"], 400):
        reply = bot.handle(update(uid, text), owner, lambda: {}, mutate,
                           lambda exchange, symbol: (_ for _ in ()).throw(ValueError("not found")))
    assert "Ticker not accepted: not found" in reply
    assert "Trading symbol" in reply

    reply = bot.handle(update(404, "VWRA"), owner, lambda: {}, mutate,
                       lambda exchange, symbol: {"currency": "USD", "asset_class": "etf"})
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
    resolver = lambda exchange, symbol: {"currency": "USD", "asset_class": "equity"}
    inputs = ["/buy", account_id, "NASDAQ", "AAPL", "2.5/193.40", "2026-09-22"]
    replies = [bot.handle(update(i, text), owner, lambda: {}, mutate, resolver)
               for i, text in enumerate(inputs, 500)]
    assert all(reply != "Actual unit price?" for reply in replies)
    assert "/confirm" in replies[-1]
    bot.handle(update(506, "/confirm"), owner, lambda: {}, mutate, resolver)
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
    resolver = lambda exchange, symbol: {"currency": "USD", "asset_class": "etf"}
    inputs = ["/buy", account_id, "NYSE:VOO", "2/500", "2026-09-22"]
    replies = [bot.handle(update(i, text), owner, lambda: {}, mutate, resolver)
               for i, text in enumerate(inputs, 700)]
    assert "Detected asset class: ETF" in replies[2]
    assert "Number of shares" in replies[2]
    assert "/confirm" in replies[-1]
    bot.handle(update(705, "/confirm"), owner, lambda: {}, mutate, resolver)
    assert saved[-1][1]["exchange"] == "NYSE" and saved[-1][1]["symbol"] == "VOO"


def test_telegram_command_menu_keeps_top_level_choices_compact():
    commands = bot.telegram_commands()
    names = {item["command"] for item in commands}
    assert {"start", "help", "account", "creditcard", "history", "deposit", "withdraw",
            "transfer", "cpf_set", "purchase", "payment", "buy", "sell", "opening_holding",
            "split", "correct", "void", "cancel", "calculator", "stock"} == names
    assert not ({"account_add", "credit_purchase", "confirm"} & names)
    assert len(names) == len(commands)


def test_account_menu_and_selection_use_inline_buttons(client):
    owner, account_id = 919, "abcde12345"
    with store.transaction() as state:
        state["accounts"][account_id] = {"id": account_id, "name": "Daily bank", "type": "bank",
            "currency": "SGD", "cpf_type": "", "archived": False}
    update = lambda uid, text: {"update_id": uid, "message": {"from": {"id": owner},
        "chat": {"id": owner, "type": "private"}, "text": text}}
    bot.handle(update(900, "/account"), owner, lambda: {}, lambda *_: {}, None)
    state = store.read()
    assert state["bot"]["pending_markup"]["inline_keyboard"][0][0]["callback_data"] == "cmd:accounts"

    bot.handle(update(901, "/deposit"), owner, lambda: {}, lambda *_: {}, None)
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
    bot.handle(update(910, "/purchase"), owner, lambda: {}, lambda *_: {}, None)
    buttons = [row[0] for row in store.read()["bot"]["pending_markup"]["inline_keyboard"]]
    assert buttons == [
        {"text": "Bank A cards · Visa", "callback_data": f"card:{first_group}:{first_card}"},
        {"text": "Bank B cards · Mastercard", "callback_data": f"card:{second_group}:{second_card}"},
    ]

    reply = bot.handle(update(911, f"{second_group}:{second_card}"), owner,
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
    replies = [bot.handle(update(uid, text), owner, lambda: {}, lambda *_: {}, None, calculate)
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
    reply = bot.handle(update, owner, lambda: {}, lambda *_: {})
    assert reply.startswith("Financial calculator:")
    buttons = [row[0] for row in store.read()["bot"]["pending_markup"]["inline_keyboard"]]
    assert buttons == [
        {"text": "Future value", "callback_data": "cmd:futurevalue"},
        {"text": "Present value", "callback_data": "cmd:presentvalue"},
    ]


def test_stock_search_preserves_exchange_ambiguity_in_callbacks(client):
    owner = 923
    update = lambda uid, text: {"update_id": uid, "message": {"from": {"id": owner},
        "chat": {"id": owner, "type": "private"}, "text": text}}
    search = lambda action, value: {"results": [
        {"security_id": "NASDAQ:ABC", "symbol": "ABC", "exchange": "NASDAQ", "name": "Alpha"},
        {"security_id": "NYSE:ABC", "symbol": "ABC", "exchange": "NYSE", "name": "Another"},
    ]}
    assert bot.handle(update(960, "/stock"), owner, lambda: {}, lambda *_: {}, stocks=search).startswith("Enter")
    bot.handle(update(961, "ABC"), owner, lambda: {}, lambda *_: {}, stocks=search)
    buttons = [row[0] for row in store.read()["bot"]["pending_markup"]["inline_keyboard"]]
    assert [button["callback_data"] for button in buttons] == [
        "stock:show:NASDAQ:ABC", "stock:show:NYSE:ABC"]


def test_stock_formatting_marks_missing_metrics_stale_and_completed_change():
    overview = {"security": {"name": "Alpha", "exchange": "NASDAQ", "symbol": "ABC"},
        "latest_price": {"currency": "USD", "close": "11", "session_date": "2026-09-23",
                         "prior_close": "10", "change": "1", "change_percent": "10",
                         "data_timestamp": "2026-09-23T20:00:00Z"},
        "cache": {"stale": True}}
    price = bot.format_stock_price(overview)
    assert "Session date: 2026-09-23" in price
    assert "+1.00 (+10.00%)" in price and "Cached/stale" in price
    earnings = bot.format_earnings({"period_type": "quarterly", "period_end": "2026-06-30",
        "report_date": None, "currency": "USD", "revenue": None, "free_cash_flow": None,
        "profit_after_tax": None, "ebitda": None, "ebita": None, "cache": {}})
    assert "Report/filing date: -" in earnings
    assert "Revenue: -" in earnings and "EBITA: -" in earnings


def test_stock_callback_security_id_validation():
    assert bot.valid_security_id("SGX:D05")
    assert bot.valid_security_id("LSE:VWRA")
    assert not bot.valid_security_id("D05")
    assert not bot.valid_security_id("NASDAQ:AAPL:evil")


def test_telegram_command_menu_is_scoped_to_owner_chat():
    class Response:
        def raise_for_status(self): pass
    class Client:
        def __init__(self): self.calls = []
        def post(self, url, json):
            self.calls.append((url, json))
            return Response()

    client = Client()
    bot.configure_command_menu(client, "https://telegram.test/", 12345)
    assert client.calls[0][0].endswith("setMyCommands")
    assert client.calls[0][1]["scope"] == {"type": "chat", "chat_id": 12345}
    assert client.calls[1] == ("https://telegram.test/setChatMenuButton", {
        "chat_id": 12345, "menu_button": {"type": "commands"}})


def test_buy_rejects_malformed_quantity_slash_price(client):
    owner, account_id = 904, "e1f2a3b4c5"
    def update(uid, text):
        return {"update_id": uid, "message": {"from": {"id": owner},
                "chat": {"id": owner, "type": "private"}, "text": text}}
    with store.transaction() as state:
        state["accounts"][account_id] = {"id": account_id, "name": "Broker", "type": "brokerage",
            "currency": "USD", "cpf_type": "", "archived": False}
    resolver = lambda exchange, symbol: {"currency": "USD", "asset_class": "equity"}
    for uid, text in enumerate(["/buy", account_id, "NASDAQ", "AAPL", "2/abc"], 600):
        reply = bot.handle(update(uid, text), owner, lambda: {}, lambda *args: {}, resolver)
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


def test_data_export_and_import_require_browser_session_and_csrf(client):
    bot = {"Authorization": "Bearer test-bot-secret"}
    assert client.get("/api/data-export", headers=bot).status_code == 401
    assert client.post("/api/data-import/validate", headers=bot, files={"file": ("backup.xlsx", b"bad")}).status_code == 401
    assert client.post("/api/data-import/commit", headers=bot, json={}).status_code == 401
    login = client.post("/api/login", json={"password": "test-password"}, headers={"Origin": "http://localhost:8080"})
    assert login.status_code == 200
    exported = client.get("/api/data-export")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert "financial-planner-export-" in exported.headers["content-disposition"]
    before = store.read()
    no_csrf = client.post("/api/data-import/validate", files={"file": ("backup.xlsx", b"bad")})
    assert no_csrf.status_code == 403
    assert client.post("/api/data-import/commit", json={}).status_code == 403
    headers = {"Origin": "http://localhost:8080", "X-CSRF-Token": login.json()["csrf"]}
    invalid = client.post("/api/data-import/validate", headers=headers,
                          files={"file": ("backup.xlsx", b"not an Excel workbook")})
    assert invalid.status_code == 422
    assert client.get("/api/data-template").status_code == 200
    assert "financial-planner-data-template.xlsx" in client.get("/api/data-template").headers["content-disposition"]
    wrong_extension = client.post("/api/data-import/validate", headers=headers,
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
