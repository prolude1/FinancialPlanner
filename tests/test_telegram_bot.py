"""Telegram transport boundaries that are independent of handler scenarios."""
import ast
from pathlib import Path
from unittest.mock import Mock
import pytest

from app.telegram_bot.client import BackendClient, send_handler_reply


def test_handler_reply_includes_saved_inline_keyboard():
    telegram = Mock()
    telegram.post.return_value = Mock()
    keyboard = {"inline_keyboard": [[{"text": "Accounts", "callback_data": "cmd:accounts"}]]}

    send_handler_reply(telegram, "https://telegram.test/bot/", 303, "Choose an action", {
        "pending_markup": keyboard,
    })

    telegram.post.assert_called_once_with(
        "https://telegram.test/bot/sendMessage",
        json={"chat_id": 303, "text": "Choose an action", "entities": [],
              "reply_markup": keyboard},
    )


def test_backend_client_uses_sender_identity_for_scoped_financial_calls(monkeypatch):
    monkeypatch.setenv("BOT_API_SECRET", "bot-secret")
    response = Mock()
    response.json.return_value = {"accounts": []}
    http = Mock()
    http.post.return_value = response

    backend = BackendClient(http)
    assert backend.dashboard(123, 123) == {"accounts": []}
    backend.mutate("deposit", {"amount": "12"}, "telegram-45", 123, 123)
    backend.user_state(123, 123, "get")
    backend.user_state(123, 123, "set", session=None, pending_reply="saved",
                       pending_markup=None, last_tvm={"result": "500"})

    assert [call.args[0] for call in http.post.call_args_list] == [
        "/internal/bot/financial/dashboard",
        "/internal/bot/financial/command/deposit",
        "/internal/bot/financial/state",
        "/internal/bot/financial/state",
    ]
    assert http.post.call_args_list[0].kwargs["json"] == {
        "telegram_user_id": 123, "telegram_chat_id": 123}
    assert http.post.call_args_list[0].kwargs["headers"] == {"Authorization": "Bearer bot-secret"}
    assert http.post.call_args_list[1].kwargs["json"] == {
        "telegram_user_id": 123, "telegram_chat_id": 123, "payload": {"amount": "12"}}
    assert "tenant" not in repr(http.post.call_args_list)
    assert http.post.call_args_list[3].kwargs["json"]["pending_reply"] == "saved"
    assert http.post.call_args_list[3].kwargs["json"]["last_tvm"] == {"result": "500"}
    assert all(call.kwargs["headers"]["Authorization"] == "Bearer bot-secret"
               for call in http.post.call_args_list)


def test_backend_client_rejects_non_private_actor_shape_before_request():
    http = Mock()
    backend = BackendClient(http)
    with pytest.raises(ValueError):
        backend.dashboard(123, 456)
    http.post.assert_not_called()


def test_backend_client_validates_all_four_scoped_state_fields(monkeypatch):
    monkeypatch.setenv("BOT_API_SECRET", "bot-secret")
    http = Mock()
    backend = BackendClient(http)
    with pytest.raises(ValueError):
        backend.user_state(12, 12, "set", last_tvm="not-an-object")
    with pytest.raises(ValueError):
        backend.user_state(12, 12, "set", principal="tenant-a")
    http.post.assert_not_called()


def test_handler_state_is_scoped_to_each_telegram_actor():
    from app.telegram_bot.handlers import handle

    state_by_user = {101: {"session": None}, 202: {"session": None}}
    ledger_by_user = {
        101: {"accounts": [{"id": "account-101", "name": "A", "type": "bank",
                            "archived": False, "native": {}, "complete": True}]},
        202: {"accounts": [{"id": "account-202", "name": "B", "type": "bank",
                            "archived": False, "native": {}, "complete": True}]},
    }

    def send(actor, update_id, text):
        def save(**fields):
            state_by_user[actor].update(fields)
        update = {"update_id": update_id, "message": {
            "from": {"id": actor}, "chat": {"id": actor, "type": "private"}, "text": text}}
        return handle(update, actor, lambda: ledger_by_user[actor],
                      lambda *_: {"id": "saved"},
                      lambda: state_by_user[actor], save)

    send(101, 1, "/deposit")
    send(202, 2, "/withdraw")
    send(101, 3, "account-101")
    send(202, 4, "account-202")

    assert state_by_user[101]["session"]["command"] == "deposit"
    assert state_by_user[101]["session"]["data"]["account"] == "account-101"
    assert state_by_user[202]["session"]["command"] == "withdraw"
    assert state_by_user[202]["session"]["data"]["account"] == "account-202"


def test_handler_rejects_group_or_mismatched_actor_without_backend_calls():
    from app.telegram_bot.handlers import handle

    for chat_type, chat_id in (("group", 7), ("private", 8)):
        update = {"update_id": 1, "message": {"from": {"id": 7},
            "chat": {"id": chat_id, "type": chat_type}, "text": "/accounts"}}
        query, mutate, load, save = Mock(), Mock(), Mock(), Mock()
        assert handle(update, 7, query, mutate, load, save) is None
        query.assert_not_called()
        mutate.assert_not_called()
        load.assert_not_called()
        save.assert_not_called()


def test_time_value_breakdown_survives_separate_update_via_user_state():
    from app.telegram_bot.handlers import handle

    user_id = 303
    persisted = {"session": None, "pending_reply": None, "pending_markup": None,
                 "last_tvm": None}
    calculated = {
        "calculation": "future_value", "result": "99999.5",
        "initial_value_component": "17908.48", "cashflow_component": "82091.02",
        "total_contributions": "70000", "total_interest_or_returns": "29999.5",
        "schedule": [{"period": 12, "closing_balance": "17000"}],
        "assumptions": {"cashflow_timing": "end", "cashflow_frequency": "monthly",
                        "compounding_frequency": "monthly", "annual_rate_percent": "6",
                        "periods": 120},
    }
    def update(update_id, text):
        return {"update_id": update_id, "message": {"from": {"id": user_id},
            "chat": {"id": user_id, "type": "private"}, "text": text}}
    def send(update_id, text):
        return handle(update(update_id, text), user_id, lambda: {"accounts": []},
                      lambda *_: {"id": "saved"}, lambda: dict(persisted),
                      lambda **fields: persisted.update(fields), calculate=lambda _: calculated)

    for update_id, text in enumerate(
            ("/futurevalue", "10000", "500", "monthly", "6", "10", "monthly", "end"), 1):
        send(update_id, text)
    assert persisted["last_tvm"] == calculated

    breakdown = send(9, "/tvm_breakdown")
    assert "Year 1: 17,000.00" in breakdown


def test_backend_client_confirms_link_with_separate_secret_and_private_identity(monkeypatch):
    link_secret = "distinct-link-secret-long-enough-123456"
    monkeypatch.setenv("TELEGRAM_LINK_BOT_SECRET", link_secret)
    monkeypatch.setenv("BOT_API_SECRET", "regular-api-secret")
    response = Mock(status_code=200)
    response.json.return_value = {"linked": True}
    http = Mock()
    http.post.return_value = response

    assert BackendClient(http).confirm_telegram_link("A" * 43, 123, 123) == "linked"
    http.post.assert_called_once_with("/internal/bot/telegram-link/confirm", json={
        "challenge": "A" * 43,
        "telegram_user_id": 123,
        "telegram_chat_id": 123,
        "chat_type": "private",
    }, headers={"Authorization": "Bearer " + link_secret})


def test_backend_client_maps_link_conflicts_and_configuration_failures(monkeypatch):
    monkeypatch.setenv("TELEGRAM_LINK_BOT_SECRET", "distinct-link-secret-long-enough-123456")
    monkeypatch.setenv("BOT_API_SECRET", "regular-api-secret")
    http = Mock()
    backend = BackendClient(http)
    http.post.return_value = Mock(status_code=409)
    assert backend.confirm_telegram_link("A" * 43, 123, 123) == "invalid"
    http.post.return_value = Mock(status_code=503)
    assert backend.confirm_telegram_link("A" * 43, 123, 123) == "unavailable"

    monkeypatch.setenv("TELEGRAM_LINK_BOT_SECRET", "short")
    assert BackendClient(http).confirm_telegram_link("A" * 43, 123, 123) == "unavailable"


def test_telegram_package_does_not_import_server_or_worker_internals():
    package = Path(__file__).parents[1] / "backend" / "app" / "telegram_bot"
    forbidden = ("api_server", "worker", "catalog")
    violations = []
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            violations.extend((path.name, module) for module in modules
                              if any(part in module for part in forbidden))
    assert violations == []
