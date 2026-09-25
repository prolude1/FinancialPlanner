"""Telegram long-polling transport and private backend API client."""
import logging
import os
import time
from urllib.parse import quote

import httpx

from ..core import store
from .handlers import code_entities, configure_command_menu, handle, valid_security_id


class BackendClient:
    """Typed-by-operation adapter for the bot's authenticated HTTP API."""

    def __init__(self, client):
        self.client = client

    def dashboard(self):
        response = self.client.get("/api/dashboard")
        response.raise_for_status()
        return response.json()

    def mutate(self, command, payload, key):
        response = self.client.post("/api/commands/" + command, json=payload,
                                    headers={"Idempotency-Key": key})
        response.raise_for_status()
        return response.json()

    def calculate(self, payload):
        response = self.client.post("/api/calculators/time-value", json=payload)
        response.raise_for_status()
        return response.json()

    def stocks(self, action, value):
        if action == "search":
            response = self.client.get("/api/stocks/search", params={"q": value})
        else:
            encoded = quote(value, safe="")
            endpoints = {
                "overview": f"/api/stocks/{encoded}/overview",
                "candles": f"/api/stocks/{encoded}/candles",
                "earnings": f"/api/stocks/{encoded}/earnings/latest",
                "financials": f"/api/stocks/{encoded}/financials",
                "identity": f"/api/stocks/{encoded}/identity",
            }
            response = self.client.get(endpoints[action], params={
                **({"range": "1y", "interval": "1d"} if action == "candles" else {}),
                **({"years": 5} if action == "financials" else {}),
            })
        response.raise_for_status()
        return response.json()

    def resolve_instrument(self, exchange, symbol):
        return self.stocks("identity", f"{exchange}:{symbol}")


def callback_message(update, telegram_client, base):
    """Convert an inline-button callback into the same text consumed by handlers."""
    callback = update.get("callback_query")
    if not callback:
        return None
    data = callback.get("data", "")
    if data.startswith("cmd:"):
        callback_text = "/" + data[4:]
    elif data.startswith("value:"):
        callback_text = data[6:]
    elif data.startswith("view:"):
        callback_text = "/account " + data[5:]
    elif data.startswith("card:"):
        callback_text = data[5:]
    elif data == "tvm:breakdown":
        callback_text = "/tvm_breakdown"
    elif data.startswith("stock:"):
        parts = data.split(":", 2)
        if (len(parts) == 3 and parts[1] in {"show", "price", "earnings", "financials", "refresh"}
                and valid_security_id(parts[2])):
            callback_text = f"/stock_{parts[1]} {parts[2]}"
        else:
            callback_text = "/stock"
    elif data in ("confirm", "cancel"):
        callback_text = "/" + data
    else:
        callback_text = "/help"
    update["message"] = {"from": callback.get("from", {}),
                         "chat": callback.get("message", {}).get("chat", {}),
                         "text": callback_text}
    answered = telegram_client.post(base + "answerCallbackQuery", json={
        "callback_query_id": callback["id"]})
    answered.raise_for_status()
    return callback


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    owner = int(os.environ.get("TELEGRAM_OWNER_ID", "0") or "0")
    if not token or not owner:
        logging.warning("Telegram is not configured; bot is idle. Set token and owner ID, then recreate bot.")
        while True:
            time.sleep(60)
    headers = {"Authorization": "Bearer " + os.environ["BOT_API_SECRET"]}
    api_http = httpx.Client(base_url=os.environ.get("API_URL", "http://api:8000"),
                            headers=headers, timeout=20)
    backend = BackendClient(api_http)
    telegram = httpx.Client(timeout=45)
    base = "https://api.telegram.org/bot" + token + "/"
    commands_registered = False

    def flush_reply():
        bot_state = store.read()["bot"]
        pending = bot_state.get("pending_reply")
        markup = bot_state.get("pending_markup")
        if pending:
            starts = list(range(0, len(pending), 3500))
            for start in starts:
                chunk = pending[start:start + 3500]
                payload = {"chat_id": owner, "text": chunk, "entities": code_entities(chunk)}
                if markup and start == starts[-1]:
                    payload["reply_markup"] = markup
                sent = telegram.post(base + "sendMessage", json=payload)
                sent.raise_for_status()
            with store.transaction() as state:
                state["bot"]["pending_reply"] = None
                state["bot"]["pending_markup"] = None

    while True:
        try:
            if not commands_registered:
                configure_command_menu(telegram, base, owner)
                commands_registered = True
            flush_reply()
            offset = store.read()["bot"]["offset"]
            response = telegram.post(base + "getUpdates", json={"offset": offset, "timeout": 30,
                                     "allowed_updates": ["message", "callback_query"]})
            response.raise_for_status()
            for update in response.json().get("result", []):
                callback = None
                try:
                    callback = callback_message(update, telegram, base)
                    result = handle(update, owner, backend.dashboard, backend.mutate,
                                    backend.resolve_instrument, backend.calculate, backend.stocks)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 422:
                        detail = exc.response.json().get("detail", "Invalid input")
                        message = detail.get("message") if isinstance(detail, dict) else str(detail)
                        result = "Request not completed: " + message + ". Use /cancel or try again."
                        with store.transaction() as state:
                            state["bot"]["pending_reply"] = result
                    else:
                        active = store.read()["bot"].get("session")
                        if not (update.get("message", {}).get("text", "").startswith("/stock")
                                or (callback and callback.get("data", "").startswith("stock:"))
                                or (active and active.get("command") == "stock")):
                            raise
                        try:
                            detail = exc.response.json().get("detail", {})
                            message = detail.get("message") if isinstance(detail, dict) else str(detail)
                        except Exception:
                            message = "Provider temporarily unavailable"
                        result = "Stock data unavailable: " + (message or "Please try again later.")
                        with store.transaction() as state:
                            state["bot"]["pending_reply"] = result
                if result:
                    flush_reply()
                with store.transaction() as state:
                    state["bot"]["offset"] = max(state["bot"]["offset"], update["update_id"] + 1)
        except Exception:
            # Exception strings may include Telegram token-bearing URLs.
            logging.warning("Bot connection/processing failed; retrying in 10 seconds (credentials omitted).")
            time.sleep(10)
