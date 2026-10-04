"""Telegram long-polling transport and private backend API client."""
import logging
import os
import time

import httpx

from ..core import store
from .handlers import code_entities, configure_command_menu, handle


def send_reply(telegram_client, base, chat_id, pending, markup=None):
    """Send a reply to its originating private chat, attaching its own keyboard."""
    if not pending:
        return
    starts = list(range(0, len(pending), 3500))
    for start in starts:
        chunk = pending[start:start + 3500]
        payload = {"chat_id": chat_id, "text": chunk, "entities": code_entities(chunk)}
        if markup and start == starts[-1]:
            payload["reply_markup"] = markup
        sent = telegram_client.post(base + "sendMessage", json=payload)
        sent.raise_for_status()


def send_handler_reply(telegram_client, base, chat_id, text, user_state):
    """Deliver a handler result with the keyboard saved alongside that result."""
    send_reply(telegram_client, base, chat_id, text, user_state.get("pending_markup"))


class BackendClient:
    """Typed-by-operation adapter for the bot's authenticated HTTP API."""

    def __init__(self, client, telegram_link_secret=None, bot_api_secret=None):
        self.client = client
        self.telegram_link_secret = (os.environ.get("TELEGRAM_LINK_BOT_SECRET", "")
                                     if telegram_link_secret is None else telegram_link_secret)
        self.bot_api_secret = (os.environ.get("BOT_API_SECRET", "")
                               if bot_api_secret is None else bot_api_secret)

    @staticmethod
    def _actor_body(telegram_user_id, telegram_chat_id):
        if (isinstance(telegram_user_id, bool) or not isinstance(telegram_user_id, int)
                or isinstance(telegram_chat_id, bool) or not isinstance(telegram_chat_id, int)
                or not 0 < telegram_user_id <= 4503599627370495
                or telegram_user_id != telegram_chat_id):
            raise ValueError("Telegram actor must be a verified private-chat user")
        return {"telegram_user_id": telegram_user_id, "telegram_chat_id": telegram_chat_id}

    def _auth_headers(self):
        if not self.bot_api_secret:
            raise RuntimeError("BOT_API_SECRET is required for financial bot requests")
        return {"Authorization": "Bearer " + self.bot_api_secret}

    def dashboard(self, telegram_user_id, telegram_chat_id):
        response = self.client.post("/internal/bot/financial/dashboard",
                                    json=self._actor_body(telegram_user_id, telegram_chat_id),
                                    headers=self._auth_headers())
        response.raise_for_status()
        return response.json()

    def mutate(self, command, payload, key, telegram_user_id, telegram_chat_id):
        response = self.client.post("/internal/bot/financial/command/" + command,
                                    json={**self._actor_body(telegram_user_id, telegram_chat_id),
                                          "payload": payload},
                                    headers={**self._auth_headers(), "Idempotency-Key": key})
        response.raise_for_status()
        return response.json()

    def user_state(self, telegram_user_id, telegram_chat_id, action, **fields):
        allowed_state_fields = {"session", "pending_reply", "pending_markup", "last_tvm"}
        if action == "set" and (not fields or set(fields) - allowed_state_fields):
            raise ValueError("Invalid Telegram state fields")
        if action != "set" and fields:
            raise ValueError("Telegram state fields are only valid for set")
        state_types = {"session": dict, "pending_reply": str,
                       "pending_markup": dict, "last_tvm": dict}
        if action == "set" and any(value is not None and not isinstance(value, state_types[key])
                                    for key, value in fields.items()):
            raise ValueError("Invalid Telegram state value")
        body = {**self._actor_body(telegram_user_id, telegram_chat_id),
                "action": action, **fields}
        response = self.client.post("/internal/bot/financial/state",
                                    json=body, headers=self._auth_headers())
        response.raise_for_status()
        return response.json()

    def calculate(self, payload):
        response = self.client.post("/api/calculators/time-value", json=payload,
                                    headers=self._auth_headers())
        response.raise_for_status()
        return response.json()

    def confirm_telegram_link(self, challenge, telegram_user_id, telegram_chat_id):
        secret = self.telegram_link_secret
        if (len(secret) < 32 or not secret.isascii()
                or (self.bot_api_secret and secret == self.bot_api_secret)):
            return "unavailable"
        try:
            response = self.client.post("/internal/bot/telegram-link/confirm", json={
                "challenge": challenge,
                "telegram_user_id": telegram_user_id,
                "telegram_chat_id": telegram_chat_id,
                "chat_type": "private",
            }, headers={"Authorization": "Bearer " + secret})
        except httpx.HTTPError:
            return "unavailable"
        if response.status_code == 409:
            return "invalid"
        if not 200 <= response.status_code < 300:
            return "unavailable"
        try:
            return "linked" if response.json().get("linked") is True else "unavailable"
        except (AttributeError, ValueError):
            return "unavailable"


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
    elif data.startswith("date:today:"):
        callback_text = "today"
    elif data == "tvm:breakdown":
        callback_text = "/tvm_breakdown"
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


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token or not os.environ.get("BOT_API_SECRET"):
        logging.warning("Telegram is not configured; bot is idle. Set token and bot API secret, then recreate bot.")
        while True:
            time.sleep(60)
    headers = {"Authorization": "Bearer " + os.environ["BOT_API_SECRET"]}
    api_http = httpx.Client(base_url=os.environ.get("API_URL", "http://api:8000"),
                            headers=headers, timeout=20)
    backend = BackendClient(api_http)
    telegram = httpx.Client(timeout=45)
    base = "https://api.telegram.org/bot" + token + "/"
    commands_registered = False

    def actor_ids(update):
        message = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
        sender = (update.get("message") or {}).get("from") or (update.get("callback_query") or {}).get("from") or {}
        chat = message.get("chat", {})
        uid, cid = sender.get("id"), chat.get("id")
        if (chat.get("type") != "private" or isinstance(uid, bool) or not isinstance(uid, int)
                or isinstance(cid, bool) or not isinstance(cid, int)
                or not 0 < uid <= 4503599627370495 or cid != uid):
            return None
        return uid, cid

    while True:
        try:
            if not commands_registered:
                configure_command_menu(telegram, base)
                commands_registered = True
            offset = store.read_bot_runtime().get("offset", 0)
            response = telegram.post(base + "getUpdates", json={"offset": offset, "timeout": 30,
                                     "allowed_updates": ["message", "callback_query"]})
            response.raise_for_status()
            for update in response.json().get("result", []):
                actor = actor_ids(update)
                if actor is None:
                    runtime = store.read_bot_runtime()
                    runtime["offset"] = max(runtime.get("offset", 0), update["update_id"] + 1)
                    store.write_bot_runtime(runtime)
                    continue
                uid, chat_id = actor
                try:
                    callback_message(update, telegram, base)
                    link_challenge = None
                    if update.get("message", {}).get("text", "").strip().startswith("/start link_"):
                        from .handlers import start_link_challenge, handle_link_start
                        link_challenge = start_link_challenge(update["message"]["text"].strip())
                    if link_challenge is not None:
                        result = handle_link_start(update, link_challenge, backend.confirm_telegram_link)
                        send_reply(telegram, base, chat_id, result)
                    else:
                        def load_state():
                            return backend.user_state(uid, chat_id, "get")

                        def persist_state():
                            backend.user_state(uid, chat_id, "set",
                                               session=state_cache.get("session"),
                                               pending_reply=state_cache.get("pending_reply"),
                                               pending_markup=state_cache.get("pending_markup"),
                                               last_tvm=state_cache.get("last_tvm"))

                        def save_state(**fields):
                            state_cache.update(fields)
                            persist_state()

                        # Resolve the linked actor before any dashboard or command request.
                        current = load_state()
                        state_cache = {"session": current.get("session"),
                                       "pending_reply": current.get("pending_reply"),
                                       "pending_markup": current.get("pending_markup"),
                                       "last_tvm": current.get("last_tvm")}
                        pending = current.get("pending_reply")
                        if pending:
                            send_reply(telegram, base, chat_id, pending,
                                       current.get("pending_markup"))
                            state_cache.update(pending_reply=None, pending_markup=None)
                            persist_state()
                        result = handle(update, uid,
                                        lambda: backend.dashboard(uid, chat_id),
                                        lambda command, payload, key: backend.mutate(
                                            command, payload, key, uid, chat_id),
                                        lambda: state_cache, save_state,
                                        calculate=backend.calculate,
                                        confirm_link=backend.confirm_telegram_link)
                        if result:
                            # Handler writes a per-user pending reply before delivery so a
                            # failed Telegram send can be retried on that user's next update.
                            send_handler_reply(telegram, base, chat_id, result, state_cache)
                            state_cache.update(pending_reply=None, pending_markup=None)
                            persist_state()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 403:
                        send_reply(telegram, base, chat_id,
                                   "This Telegram account is not linked to a financial planner account. "
                                   "Use the web app's Telegram linking option, then try again.")
                    elif exc.response.status_code == 422:
                        detail = exc.response.json().get("detail", "Invalid input")
                        message = detail.get("message") if isinstance(detail, dict) else str(detail)
                        result = "Request not completed: " + message + ". Use /cancel or try again."
                        send_reply(telegram, base, chat_id, result)
                    else:
                        raise
                runtime = store.read_bot_runtime()
                runtime["offset"] = max(runtime.get("offset", 0), update["update_id"] + 1)
                store.write_bot_runtime(runtime)
        except Exception:
            # Exception strings may include Telegram token-bearing URLs.
            logging.warning("Bot connection/processing failed; retrying in 10 seconds (credentials omitted).")
            time.sleep(10)
