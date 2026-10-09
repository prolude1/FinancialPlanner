"""Telegram command definitions, formatting, and guided conversation state."""
import os
import re
import secrets
import time
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

FIELDS = {
    "futurevalue": [("initial_value", "Starting amount?"), ("cashflow", "Recurring contribution (use a negative number for withdrawals)?"),
                    ("cashflow_frequency", "Contribution frequency?"), ("annual_rate", "Expected annual return, in percent?"),
                    ("duration_years", "Duration in whole years?"), ("compounding_frequency", "Compounding frequency?"),
                    ("cashflow_timing", "Contribute at the beginning or end of each period?")],
    "presentvalue": [("initial_value", "Desired future amount?"), ("cashflow", "Recurring contribution (use a negative number for withdrawals)?"),
                     ("cashflow_frequency", "Contribution frequency?"), ("annual_rate", "Expected annual return, in percent?"),
                     ("duration_years", "Duration in whole years?"), ("compounding_frequency", "Compounding frequency?"),
                     ("cashflow_timing", "Contribute at the beginning or end of each period?")],
    "account_add": [("name", "Account name?"), ("type", "Type: bank, brokerage or CPF?"),
                    ("currency", "Default currency, e.g. SGD?"), ("cpf_type", "CPF subtype: OA, SA or MA?")],
    "account_rename": [("account", "Which account do you want to rename?"),
                       ("name", "What should its new nickname be?")],
    "account_archive": [("account", "Account ID to archive (must be empty)?")],
    "opening_cash": [("account", "Account ID?"), ("money", "Currency and opening amount, e.g. SGD 100 or USD 25.50?"), ("date", "Opening date (YYYY-MM-DD)?")],
    "deposit": [("account", "Account ID?"), ("money", "Currency and amount deposited, e.g. SGD 100 or USD 25.50?"), ("description", "Description, e.g. salary or reimbursement?"), ("date", "Date (YYYY-MM-DD or today)?")],
    "withdraw": [("account", "Account ID?"), ("money", "Currency and amount withdrawn, e.g. SGD 100 or USD 25.50?"), ("description", "Description, e.g. groceries or utilities?"), ("date", "Date (YYYY-MM-DD or today)?")],
    "cpf_set": [("account", "CPF account ID?"), ("amount", "Actual SGD balance from your statement?"), ("date", "Statement date (YYYY-MM-DD or today)?")],
    "transfer": [("account", "Source account ID?"), ("destination", "Destination account ID?"),
                 ("money", "Currency and amount sent, e.g. SGD 100 or USD 25.50?"),
                 ("received_money", "Currency and amount received, e.g. SGD 100 or USD 25.50?"),
                 ("date", "Transfer date?")],
    "credit_account_add": [("name", "Credit-card account name, e.g. DBS Cards?")],
    "credit_card_add": [("credit_account", "Credit-card account ID?"), ("name", "Card nickname?")],
    "credit_purchase": [("card", "Which card was used?"), ("description", "Purchase description?"), ("amount", "Purchase amount in SGD?"), ("date", "Purchase date (YYYY-MM-DD or today)?")],
    "credit_refund": [("credit_account", "Credit-card account ID?"), ("card", "Card ID?"), ("description", "Refund description?"), ("amount", "Refund amount in SGD?"), ("date", "Refund date (YYYY-MM-DD or today)?")],
    "credit_payment": [("credit_account", "Credit-card account ID?"), ("funding_account", "Account ID funding the payment?"), ("amount", "Payment amount in SGD?"), ("date", "Payment date (YYYY-MM-DD or today)?")],
    "recurring": [("account", "Bank account to deduct from?"),
                  ("cadence", "How often: monthly or annual?"),
                  ("money", "Amount in the account currency (e.g. SGD 100 or USD 25.50)?"),
                  ("description", "Description for each transaction?"),
                  ("day_of_month", "Due day?"),
                  ("month_of_year", "Month of the year (1–12)?")],
}
TRADE = [("account", "Brokerage account ID?"), ("exchange", "Exchange: NYSE, NASDAQ, LSE or SGX? You can also enter EXCHANGE:TICKER, e.g. NASDAQ:AAPL."),
         ("symbol", "Trading symbol, e.g. AAPL or VWRA?"), ("asset_class", "Asset class: equity or etf?"),
         ("currency", "Trading currency, e.g. USD?"), ("quantity", "Number of shares (fractions allowed)?"),
         ("price", "Actual unit price?"), ("date", "Date (YYYY-MM-DD or today)?")]
FIELDS["buy"] = TRADE
FIELDS["sell"] = TRADE
FIELDS["opening_holding"] = [(k, "Original unit cost, or unknown?" if k == "price" else v) for k, v in TRADE]
FIELDS["split"] = TRADE[:5] + [("ratio", "New shares per old share, e.g. 4 for a 4-for-1 split?"), ("date", "Split effective date (YYYY-MM-DD)?")]
ACCOUNT_TYPE_ORDER = ("bank", "brokerage", "cpf")
ACCOUNT_TYPE_LABELS = {"bank": "Bank accounts", "brokerage": "Brokerage accounts", "cpf": "CPF accounts"}
TOP_LEVEL_COMMANDS = (
    ("deposit", "Record money entering an account"),
    ("withdraw", "Record spending or money leaving"),
    ("purchase", "Record a credit-card purchase"),
    ("payment", "Pay a credit-card balance"),
    ("buy", "Buy shares or ETFs"),
    ("sell", "Sell shares or ETFs"),
    ("transfer", "Move money between accounts"),
    ("recurring", "Schedule a recurring bank deduction"),
    ("cpf_set", "Reconcile a CPF statement balance"),
    ("account", "Account actions and balances"),
    ("creditcard", "Credit-card actions and balances"),
    ("calculator", "Open financial calculators"),
    ("opening_holding", "Record an existing holding"),
    ("split", "Record a stock split"),
    ("help", "Show every available command"),
    ("start", "Open the private financial ledger"),
    ("cancel", "Cancel the current operation"),
)

ACCOUNT_ACTIONS = [
    ("accounts", "List accounts"), ("account_view", "View account"),
    ("account_add", "Add account"), ("account_rename", "Rename account"),
    ("account_archive", "Archive account"), ("opening_cash", "Set opening cash"),
]
CREDITCARD_ACTIONS = [
    ("credit_accounts", "List cards"), ("credit_account_add", "Add card account"),
    ("credit_card_add", "Add card"), ("credit_refund", "Refund"),
]
CALCULATOR_ACTIONS = [
    ("futurevalue", "Future value"),
    ("presentvalue", "Present value"),
]
COMMAND_ALIASES = {"purchase": "credit_purchase", "payment": "credit_payment"}


def telegram_commands():
    return [{"command": command, "description": description}
            for command, description in TOP_LEVEL_COMMANDS]


def top_level_command_text():
    commands = ["/" + command for command, _ in TOP_LEVEL_COMMANDS]
    return "\n".join(" · ".join(commands[start:start + 4])
                     for start in range(0, len(commands), 4))


def configure_command_menu(client, base, _legacy_owner_ignored=None):
    """Publish slash-command suggestions for any linked private user."""
    registered = client.post(base + "setMyCommands", json={
        "commands": telegram_commands(),
    })
    registered.raise_for_status()


def action_keyboard(actions):
    return {"inline_keyboard": [[{"text": label, "callback_data": "cmd:" + command}]
                                 for command, label in actions]}


def format_amount(value):
    return f"{Decimal(str(value)):,.2f}"


def format_time_value(result):
    future = result["calculation"] == "future_value"
    title = "Future value" if future else "Present value required"
    growth = "Investment growth" if future else "Interest/return effect"
    assumptions = result["assumptions"]
    timing = assumptions["cashflow_timing"]
    return (f"{title}: {format_amount(result['result'])}\n\n"
            f"Initial-value component: {format_amount(result['initial_value_component'])}\n"
            f"Cash-flow component: {format_amount(result['cashflow_component'])}\n"
            f"Total contributions: {format_amount(result['total_contributions'])}\n"
            f"{growth}: {format_amount(result['total_interest_or_returns'])}\n\n"
            f"Assumptions: {assumptions['annual_rate_percent']}% nominal annual rate, "
            f"compounded {assumptions['compounding_frequency']}; "
            f"{assumptions['cashflow_frequency']} cash flow at the {timing} of each period.")


def yearly_breakdown(result):
    schedule = result.get("schedule", [])
    per_year = {"monthly": 12, "quarterly": 4, "semiannual": 2, "annual": 1}[
        result["assumptions"]["cashflow_frequency"]]
    rows = [row for row in schedule if row["period"] % per_year == 0]
    if schedule and (not rows or rows[-1]["period"] != schedule[-1]["period"]):
        rows.append(schedule[-1])
    return "Yearly breakdown:\n" + "\n".join(
        f"Year {(row['period'] + per_year - 1) // per_year}: {format_amount(row['closing_balance'])}"
        for row in rows)


def eligible_accounts(session, state):
    field = FIELDS[session["command"]][session["index"]][0]
    if field in ("cashflow_frequency", "compounding_frequency"):
        return {"inline_keyboard": [[{"text": label, "callback_data": "value:" + value}]
                                    for value, label in (("monthly", "Monthly"), ("quarterly", "Quarterly"),
                                                         ("semiannual", "Semiannual"), ("annual", "Annual"))]}
    if field == "cashflow_timing":
        return {"inline_keyboard": [[{"text": "End of period", "callback_data": "value:end"}],
                                    [{"text": "Beginning of period", "callback_data": "value:beginning"}]]}
    accounts = [a for a in state["accounts"].values() if not a["archived"]]
    if field == "account" and session["command"] in ("buy", "sell", "opening_holding", "split"):
        accounts = [a for a in accounts if a["type"] == "brokerage"]
    if field == "account" and session["command"] == "cpf_set":
        accounts = [a for a in accounts if a["type"] == "cpf"]
    if field == "account" and session["command"] == "recurring":
        accounts = [a for a in accounts if a["type"] == "bank"]
    if field == "destination" and session["data"].get("account"):
        accounts = [a for a in accounts if a["id"] != session["data"]["account"]]
    if field == "funding_account":
        accounts = [a for a in accounts if a["type"] != "cpf"]
    return sorted(accounts, key=lambda a: (ACCOUNT_TYPE_ORDER.index(a["type"]), a["name"].casefold()))


def today_in_app_timezone():
    return datetime.now(ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Singapore"))).date()


def valid_transaction_date(value, today=None):
    """Accept only a real YYYY-MM-DD date within the ledger's supported range."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
        return False
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return date(1970, 1, 1) <= parsed <= (today or today_in_app_timezone())


def date_step(session):
    return bool(session and session.get("index", 0) < len(FIELDS.get(session.get("command"), []))
                and FIELDS[session["command"]][session["index"]][0] == "date")


def date_retry(session, today, state):
    """Count invalid date submissions and return the user-facing retry/cancel reply."""
    attempts = int(session.get("date_attempts", 0)) + 1
    if attempts >= 3:
        return None, "Invalid date. This transaction was cancelled after 3 attempts. Nothing was saved."
    session["date_attempts"] = attempts
    message = (f"Enter a real date from 1970-01-01 through {today} in exact YYYY-MM-DD format. "
               f"Attempt {attempts} of 3 was invalid; {3 - attempts} attempt(s) remain.\n"
               + prompt(session, state))
    return session, message


def date_keyboard(session):
    nonce = session.setdefault("nonce", secrets.token_hex(4))
    return {"inline_keyboard": [[{"text": "Today",
                                  "callback_data": "date:today:" + nonce}]]}


def session_keyboard(session, state):
    if not session:
        return None
    if session["index"] == len(FIELDS[session["command"]]):
        return {"inline_keyboard": [[
            {"text": "Yes, save", "callback_data": "confirm"},
            {"text": "No, cancel", "callback_data": "cancel"},
        ]]}
    field = FIELDS[session["command"]][session["index"]][0]
    if field == "date":
        return date_keyboard(session)
    if field == "cadence" and session["command"] == "recurring":
        return {"inline_keyboard": [[{"text": "Monthly", "callback_data": "value:monthly"}],
                                     [{"text": "Annual", "callback_data": "value:annual"}]]}
    if field in ("account", "destination", "funding_account"):
        rows = [[{"text": account["name"], "callback_data": "value:" + account["id"]}]
                for account in eligible_accounts(session, state)]
        return {"inline_keyboard": rows} if rows else None
    if field == "credit_account":
        rows = [[{"text": account["name"], "callback_data": "value:" + account["id"]}]
                for account in sorted(state.get("credit_accounts", {}).values(),
                                      key=lambda a: a["name"].casefold())]
        return {"inline_keyboard": rows} if rows else None
    if field == "card":
        if session["command"] == "credit_purchase":
            rows = []
            for credit in sorted(state.get("credit_accounts", {}).values(),
                                 key=lambda a: a["name"].casefold()):
                for card in sorted(credit.get("cards", {}).values(), key=lambda a: a["name"].casefold()):
                    rows.append([{"text": credit["name"] + " · " + card["name"],
                                  "callback_data": "card:" + credit["id"] + ":" + card["id"]}])
            return {"inline_keyboard": rows} if rows else None
        credit = state.get("credit_accounts", {}).get(session["data"].get("credit_account"), {})
        rows = [[{"text": card["name"], "callback_data": "value:" + card["id"]}]
                for card in sorted(credit.get("cards", {}).values(), key=lambda a: a["name"].casefold())]
        return {"inline_keyboard": rows} if rows else None
    return None


def grouped_accounts(accounts, render):
    """Render accounts under stable type headings, then alphabetically by name."""
    sections = []
    for account_type in ACCOUNT_TYPE_ORDER:
        members = sorted((a for a in accounts if a["type"] == account_type),
                         key=lambda a: a["name"].casefold())
        if members:
            sections.append(ACCOUNT_TYPE_LABELS[account_type] + ":\n" + "\n".join(render(a) for a in members))
    return "\n\n".join(sections)


def prompt(session, state):
    """Return the current question, adding copyable account references where useful."""
    field, question = FIELDS[session["command"]][session["index"]]
    if field == "date":
        return "Date (YYYY-MM-DD). You can also type today or tap Today."
    if field == "money" and session["command"] == "recurring":
        account = state["accounts"].get(session["data"].get("account"), {})
        currency = account.get("currency", "SGD")
        return f"Amount in {currency} (enter currency and amount together, e.g. {currency} 100)."
    if field == "day_of_month" and session["command"] == "recurring":
        cadence = session["data"].get("cadence")
        return "Monthly due day (1–28)?" if cadence == "monthly" else "Annual due day (1–31, valid for the selected month every year)?"
    if field == "credit_account":
        items = sorted(state.get("credit_accounts", {}).values(), key=lambda a: a["name"].casefold())
        if not items:
            return question + "\nNo credit-card accounts found. Use /credit_account_add first."
        choices = "\n".join(f"• {a['name']} — {a['id']}" for a in items)
        return question + "\nTap and hold an ID to copy it:\n" + choices
    if field == "card":
        if session["command"] == "credit_purchase":
            cards = [(credit, card)
                     for credit in sorted(state.get("credit_accounts", {}).values(),
                                          key=lambda a: a["name"].casefold())
                     for card in sorted(credit.get("cards", {}).values(),
                                        key=lambda a: a["name"].casefold())]
            if not cards:
                return question + "\nNo cards found. Use /cancel, then add a card."
            choices = "\n".join(f"• {credit['name']} · {card['name']} — {card['id']}"
                                for credit, card in cards)
            return question + "\nChoose any card:\n" + choices
        credit = state.get("credit_accounts", {}).get(session["data"].get("credit_account"), {})
        cards = sorted(credit.get("cards", {}).values(), key=lambda a: a["name"].casefold())
        if not cards:
            return question + "\nNo cards found in this account. Use /cancel, then /credit_card_add."
        choices = "\n".join(f"• {card['name']} — {card['id']}" for card in cards)
        return question + "\nTap and hold an ID to copy it:\n" + choices
    if field not in ("account", "destination", "funding_account"):
        return question
    accounts = [a for a in state["accounts"].values() if not a["archived"]]
    if field == "account" and session["command"] in ("buy", "sell", "opening_holding", "split"):
        accounts = [a for a in accounts if a["type"] == "brokerage"]
    if field == "account" and session["command"] == "cpf_set":
        accounts = [a for a in accounts if a["type"] == "cpf"]
    if field == "account" and session["command"] == "recurring":
        accounts = [a for a in accounts if a["type"] == "bank"]
    if field == "destination" and session["data"].get("account"):
        accounts = [a for a in accounts if a["id"] != session["data"]["account"]]
    if field == "funding_account":
        accounts = [a for a in accounts if a["type"] != "cpf"]
    if not accounts:
        return question + "\nNo eligible active accounts were found. Use /cancel if you need to create one first."
    choices = grouped_accounts(accounts, lambda a: f"• {a['name']} — {a['id']}")
    return question + "\nTap and hold an ID to copy it:\n" + choices


def code_entities(text):
    """Mark stable 10-hex references as Telegram code entities for easy copying."""
    entities = []
    for match in re.finditer(r"(?<![0-9a-f])[0-9a-f]{10}(?![0-9a-f])", text):
        offset = len(text[:match.start()].encode("utf-16-le")) // 2
        length = len(match.group().encode("utf-16-le")) // 2
        entities.append({"type": "code", "offset": offset, "length": length})
    return entities


def authorized(update, expected_actor=None):
    msg = update.get("message", {})
    sender = msg.get("from", {}).get("id")
    chat = msg.get("chat", {})
    return bool(isinstance(sender, int) and not isinstance(sender, bool) and sender > 0
                and (expected_actor is None or sender == expected_actor)
                and sender <= 4503599627370495
                and isinstance(chat.get("id"), int) and not isinstance(chat.get("id"), bool)
                and chat.get("type") == "private" and chat.get("id") == sender)


def start_link_challenge(text):
    """Return a deep-link challenge, an empty string for malformed link args, or None."""
    command, _, arguments = text.partition(" ")
    command = command.split("@", 1)[0].lower()
    if command != "/start" or not arguments.startswith("link_"):
        return None
    challenge = arguments[5:]
    return challenge if re.fullmatch(r"[A-Za-z0-9_-]{32,128}", challenge) else ""


def handle_link_start(update, challenge, confirm_link):
    """Confirm one Keycloak-issued challenge from a verified private Bot API update."""
    uid = update["update_id"]
    message = update.get("message", {})
    sender = message.get("from", {})
    chat = message.get("chat", {})
    telegram_user_id = sender.get("id")
    telegram_chat_id = chat.get("id")
    valid_ids = all(isinstance(value, int) and not isinstance(value, bool)
                    and 0 < value <= 4503599627370495
                    for value in (telegram_user_id, telegram_chat_id))
    if chat.get("type") != "private" or not valid_ids or telegram_chat_id != telegram_user_id:
        result = "Open this link in a private chat with the bot. No changes were made."
    elif not challenge:
        result = "This link is invalid. Create a new link from the web app."
    else:
        try:
            confirmation = confirm_link(challenge, telegram_user_id, telegram_chat_id) if confirm_link else "unavailable"
        except Exception:
            confirmation = "unavailable"
        if confirmation == "linked":
            result = "Telegram account linked successfully. Return to the web app to check the connection."
        elif confirmation == "invalid":
            result = ("This link is invalid, expired, already used, or cannot be linked. "
                      "Check the web app or create a new link.")
        else:
            result = "Telegram linking is temporarily unavailable. Try again later."
    return result


def handle(update, actor_id, query, mutate, load_state, save_state,
           calculate=None, confirm_link=None, create_recurring=None):
    text = update.get("message", {}).get("text", "").strip()
    link_challenge = start_link_challenge(text)
    if link_challenge is not None:
        return handle_link_start(update, link_challenge, confirm_link)
    if not authorized(update) or update.get("message", {}).get("from", {}).get("id") != actor_id:
        return None
    uid = update["update_id"]
    command = text.split(" ", 1)[0].split("@", 1)[0].lstrip("/").lower()
    command = COMMAND_ALIASES.get(command, command)
    if text.startswith("/") and command in {"history", "correct", "void"}:
        return "Transaction history and edits are handled in the web app. No changes were made."
    dashboard = query()
    remote_state = load_state()
    state = {"accounts": {a["id"]: a for a in dashboard.get("accounts", [])},
             "credit_accounts": {a["id"]: {**a, "cards": {
                 c["id"]: c for c in (a.get("cards", []) if isinstance(a.get("cards", []), list)
                                      else a.get("cards", {}).values())}}
                                 for a in dashboard.get("credit_accounts", [])},
             "bot": {"session": remote_state.get("session"),
                     "last_tvm": remote_state.get("last_tvm")}}
    session = remote_state.get("session")
    retired_commands = {"history", "correct", "void"}
    legacy_edit_session = bool(session and session.get("command") in {"correct", "void"})
    unsupported_legacy_session = bool(session and session.get("command") not in FIELDS
                                      and not legacy_edit_session)
    if legacy_edit_session:
        # Persisted conversation state can outlive a bot restart. Never let a
        # post-upgrade reply finish an edit workflow removed from Telegram.
        session = None
    elif unsupported_legacy_session:
        # Persisted state may refer to a guided flow no longer available in Telegram.
        session = None
    if text.split(" ", 1)[0].split("@", 1)[0].lower() == "/start":
        # A fresh start discards any conversation left over from a prior
        # unlink/relink cycle before it can be confirmed against a new link.
        session = None
    if session and time.time() - session["started"] > 1800:
        session = None
    callback_data = update.get("callback_query", {}).get("data", "")
    today_callback = callback_data.startswith("date:today:")
    if today_callback:
        callback_nonce = callback_data.partition("date:today:")[2]
        if not date_step(session) or callback_nonce != session.get("nonce"):
            result = "That Today button has expired. No changes were made."
            save_state(session=session, pending_reply=result, pending_markup=session_keyboard(session, state))
            return result
        text = "today"
    result = "Use /help for available commands."
    pending_markup = None
    calculator_result = None
    if legacy_edit_session:
        result = "This older Telegram edit was cancelled without changes. View and edit transactions in the web app."
    elif unsupported_legacy_session:
        result = "That older Telegram operation is no longer available. Use /help to continue."
    elif text.startswith("/") and command in retired_commands:
        result = "Transaction history and edits are handled in the web app. No changes were made."
    elif text.startswith("/"):
        if command in ("start", "help"):
            result = ("Your private financial ledger.\nAvailable commands:\n"
                      + top_level_command_text()
                      + "\nView or edit transactions in the local web app. Loans are managed there too.")
        elif command == "cancel":
            session, result = None, "Cancelled. Nothing saved."
        elif command == "creditcard":
            session = None
            result = "Choose a credit-card action:"
            pending_markup = action_keyboard(CREDITCARD_ACTIONS)
        elif command == "calculator":
            session = None
            result = "Financial calculator:\nChoose what you want to calculate."
            pending_markup = action_keyboard(CALCULATOR_ACTIONS)
        elif command == "account" and not text.partition(" ")[2].strip():
            session = None
            result = "Choose an account action:"
            pending_markup = action_keyboard(ACCOUNT_ACTIONS)
        elif command == "account_view":
            session = None
            accounts = [a for a in state["accounts"].values() if not a["archived"]]
            result = "Choose an account to view:"
            pending_markup = {"inline_keyboard": [[{
                "text": a["name"], "callback_data": "view:" + a["id"]}]
                for a in sorted(accounts, key=lambda a: a["name"].casefold())]}
        elif command == "tvm_breakdown":
            session = None
            calculator_result = state["bot"].get("last_tvm")
            result = yearly_breakdown(calculator_result) if calculator_result else "No recent calculation found."
        elif command in ("accounts", "credit_accounts", "account"):
            data = query()
            if command == "accounts":
                active = [a for a in data["accounts"] if not a["archived"]]
                result = "Accounts (tap and hold an ID to copy it):\n" + (grouped_accounts(
                    active, lambda a: f"• {a['name']} — {a['id']} — "
                    + (", ".join(f"{cur} {value}" for cur, value in a["native"].items()) or "No balances")
                    + (" [incomplete valuation]" if not a["complete"] else "")) or "No active accounts")
            elif command == "credit_accounts":
                items = data.get("credit_accounts", [])
                result = "Credit-card accounts (tap and hold an ID to copy it):\n" + ("\n\n".join(
                    f"{item['name']} — {item['id']}\nCombined balance: SGD {item['balance']}\n"
                    + ("\n".join(f"• {card['name']} — {card['id']} — purchases/refunds SGD {card['activity']}" for card in item["cards"]) or "No cards")
                    for item in items) or "No credit-card accounts")
            else:
                ident = text.partition(" ")[2].strip()
                a = next((a for a in data["accounts"] if a["id"] == ident), None)
                result = "Use /account ACCOUNT_ID. Find IDs using /accounts."
                if a:
                    result = a["name"] + "\nCash:\n" + "\n".join(f"{x['currency']} {x['amount']}" for x in a["cash"])
                    result += "\nHoldings:\n" + "\n".join(f"{x['id']}: {x['quantity']} shares · {x['currency']} {x['market_value'] or 'unpriced'}" for x in a["holdings"])
                    if a["type"] == "cpf":
                        result += f"\nManually maintained. Last reconciliation: {a['last_reconciled'] or 'never'}." + (" Update is overdue." if a["cpf_stale"] else "")
        elif command == "confirm":
            if not session or session["index"] != len(FIELDS[session["command"]]):
                result = "No completed operation to confirm."
            else:
                payload = dict(session["data"])
                if session["command"] == "recurring":
                    if create_recurring is None:
                        result = "Recurring deductions are unavailable until the schedule service is ready. Nothing was saved."
                        save_state(session=session, pending_reply=result,
                                   pending_markup=session_keyboard(session, state))
                        return result
                    payload = {key: payload[key] for key in
                               ("account", "amount", "description", "cadence",
                                "day_of_month", "month_of_year")}
                    saved = create_recurring(payload, "telegram-" + str(update["update_id"]))
                    schedule = saved.get("schedule", saved)
                    result = (f"Recurring deduction saved. Reference: {schedule.get('id', 'ok')}.\n"
                              f"Next due: {schedule.get('next_due_date', 'pending')}. "
                              "The planner records each transaction on its due date; it does not send bank payments.")
                    session = None
                else:
                    saved = mutate(session["command"], payload, "telegram-" + str(uid))
                    result = "Saved. Reference: " + str(saved.get("id", "ok")) + ". The dashboard will refresh automatically."
                    session = None
        elif command in FIELDS:
            session = {"command": command, "data": {}, "index": 0, "started": time.time(),
                       "nonce": secrets.token_hex(4)}
            result = prompt(session, state)
    elif session and session["index"] < len(FIELDS[session["command"]]):
        field = FIELDS[session["command"]][session["index"]][0]
        instrument_note = ""
        instrument_shortcut = None
        if field == "date":
            today = today_in_app_timezone()
            candidate = str(today) if text.lower() == "today" else text
            if not valid_transaction_date(candidate, today):
                session, result = date_retry(session, today, state)
                markup = session_keyboard(session, state)
                save_state(session=session, pending_reply=result, pending_markup=markup)
                return result
            text = candidate
            session["date_attempts"] = 0
        if session["command"] == "account_add" and field == "type":
            account_types = {
                "bank": "bank", "bank account": "bank",
                "brokerage": "brokerage", "brokerage account": "brokerage", "broker": "brokerage",
                "cpf": "cpf", "cpf account": "cpf",
            }
            text = account_types.get(text.strip().lower(), text.strip().lower())
        if session["command"] == "account_add" and field in ("currency", "cpf_type"):
            text = text.strip().upper()
        if session["command"] == "recurring" and field == "cadence":
            text = text.strip().lower()
            if text not in ("monthly", "annual"):
                result = "Choose monthly or annual, then retry.\n" + prompt(session, state)
                save_state(session=session, pending_reply=result, pending_markup=session_keyboard(session, state))
                return result
        if session["command"] == "account_add" and field == "currency" and text not in ("SGD", "USD"):
            result = "Only SGD and USD are supported. Enter SGD or USD, then retry.\n" + prompt(session, state)
            save_state(session=session, pending_reply=result, pending_markup=session_keyboard(session, state))
            return result
        if session["command"] in ("futurevalue", "presentvalue") and field in (
                "cashflow_frequency", "compounding_frequency", "cashflow_timing"):
            text = text.strip().lower()
        if session["command"] in ("buy", "sell", "opening_holding", "split"):
            if field in ("exchange", "symbol", "currency"):
                text = text.strip().upper()
            if field == "asset_class":
                text = text.strip().lower()
            if field in ("exchange", "symbol") and ":" in text:
                parts = [part.strip().upper() for part in text.split(":")]
                if (len(parts) != 2 or parts[0] not in ("NYSE", "NASDAQ", "LSE", "SGX")
                        or not re.fullmatch(r"[A-Z0-9.^-]{1,20}", parts[1])):
                    result = "Use EXCHANGE:TICKER, for example NASDAQ:AAPL.\n" + prompt(session, state)
                    save_state(session=session, pending_reply=result, pending_markup=session_keyboard(session, state))
                    return result
                instrument_shortcut = tuple(parts)
                session["data"]["exchange"], session["data"]["symbol"] = instrument_shortcut
        # Convenience syntax at the /buy quantity prompt: quantity/unit-price.
        # Domain validation still checks both values when the command is saved.
        if session["command"] == "buy" and field == "quantity" and "/" in text:
            parts = [part.strip() for part in text.split("/")]
            if (len(parts) != 2
                    or not re.fullmatch(r"\d+(?:\.\d+)?", parts[0])
                    or not re.fullmatch(r"\d+(?:\.\d+)?", parts[1])):
                result = "Use quantity/price, for example 10/123.45.\n" + prompt(session, state)
                save_state(session=session, pending_reply=result, pending_markup=session_keyboard(session, state))
                return result
            text = parts[0]
            session["data"]["price"] = parts[1]
        money_shortcut = False
        if field in ("money", "received_money"):
            amount_pattern = r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?"
            match = re.fullmatch(rf"\s*(SGD|USD)\s*({amount_pattern})\s*", text, re.IGNORECASE)
            if not match:
                result = ("Enter currency and amount together using SGD or USD, "
                          "for example SGD100, SGD 1,234.50, or USD25.50. Please retry.\n"
                          + prompt(session, state))
                save_state(session=session, pending_reply=result, pending_markup=session_keyboard(session, state))
                return result
            currency_code, amount = match.groups()
            amount = amount.replace(",", "")
            if field == "money":
                session["data"]["currency"] = currency_code.upper()
                session["data"]["amount"] = amount
            else:
                session["data"]["to_currency"] = currency_code.upper()
                session["data"]["received"] = amount
            money_shortcut = True
            if session["command"] == "recurring":
                account = state["accounts"].get(session["data"].get("account"), {})
                expected_currency = str(account.get("currency", "SGD")).upper()
                if currency_code.upper() != expected_currency:
                    result = (f"This account uses {expected_currency}. Enter the amount in {expected_currency} and retry.\n"
                              + prompt(session, state))
                    save_state(session=session, pending_reply=result,
                               pending_markup=session_keyboard(session, state))
                    return result
                if Decimal(amount) <= 0:
                    result = "Enter an amount greater than zero, then retry.\n" + prompt(session, state)
                    save_state(session=session, pending_reply=result,
                               pending_markup=session_keyboard(session, state))
                    return result
        card_shortcut = False
        if session["command"] == "credit_purchase" and field == "card":
            parts = text.split(":", 1)
            if len(parts) == 2:
                credit_id, card_id = parts
                credit = state.get("credit_accounts", {}).get(credit_id)
                if credit and card_id in credit.get("cards", {}):
                    session["data"]["credit_account"] = credit_id
                    session["data"]["card"] = card_id
                    card_shortcut = True
            else:
                matches = [(credit["id"], text) for credit in state.get("credit_accounts", {}).values()
                           if text in credit.get("cards", {})]
                if len(matches) == 1:
                    session["data"]["credit_account"], session["data"]["card"] = matches[0]
                    card_shortcut = True
            if not card_shortcut:
                result = "Choose one of the listed cards.\n" + prompt(session, state)
                save_state(session=session, pending_reply=result, pending_markup=session_keyboard(session, state))
                return result
        if not instrument_shortcut and not card_shortcut and not money_shortcut:
            session["data"][field] = text
        if (field == "symbol" or instrument_shortcut) and session["command"] in ("buy", "sell", "opening_holding", "split"):
            exchange = session["data"].get("exchange", "").upper()
            if exchange in ("NYSE", "NASDAQ"):
                session["data"]["currency"] = "USD"
            notes = []
            if session["data"].get("asset_class"):
                notes.append("Detected asset class: " + session["data"]["asset_class"].upper() + ".")
            if session["data"].get("currency"):
                notes.append("Detected trading currency: " + session["data"]["currency"] + ".")
            if notes:
                instrument_note = "\n".join(notes) + "\n"
        session["index"] += 1
        if session["command"] == "recurring":
            cadence = session["data"].get("cadence")
            if (session["index"] < len(FIELDS["recurring"])
                    and FIELDS["recurring"][session["index"]][0] == "month_of_year"
                    and cadence == "monthly"):
                session["data"]["month_of_year"] = None
                session["index"] += 1
            if field == "day_of_month":
                max_day = 28 if session["data"].get("cadence") == "monthly" else 31
                if not re.fullmatch(r"(?:[1-9]|[12]\d|3[01])", text.strip()) or int(text) > max_day:
                    session["index"] -= 1
                    session["data"].pop("day_of_month", None)
                    result = f"Enter a day from 1 to {max_day}, then retry.\n" + prompt(session, state)
                    save_state(session=session, pending_reply=result,
                               pending_markup=session_keyboard(session, state))
                    return result
                session["data"]["day_of_month"] = int(text)
            if field == "month_of_year":
                if not re.fullmatch(r"(?:[1-9]|1[0-2])", text.strip()):
                    session["index"] -= 1
                    session["data"].pop("month_of_year", None)
                    result = "Enter a month from 1 to 12, then retry.\n" + prompt(session, state)
                    save_state(session=session, pending_reply=result,
                               pending_markup=session_keyboard(session, state))
                    return result
                month, day_of_month = int(text), int(session["data"]["day_of_month"])
                # Annual dates must exist every year; reject Feb 29 and all other
                # dates that would need leap-year or month-end clamping.
                try:
                    date(2001, month, day_of_month)
                except ValueError:
                    session["index"] -= 1
                    session["data"].pop("month_of_year", None)
                    result = "That annual date is not valid every year. Enter a month/day that exists each year, then retry.\n" + prompt(session, state)
                    save_state(session=session, pending_reply=result,
                               pending_markup=session_keyboard(session, state))
                    return result
                # The generic field handler stores text. The backend schema
                # requires an integer month, so normalize it before review/save.
                session["data"]["month_of_year"] = month
        # CPF subtype is meaningful only for CPF accounts. Bank and brokerage
        # setup proceeds directly to confirmation after the currency prompt.
        if (session["command"] == "account_add"
                and session["index"] < len(FIELDS["account_add"])
                and FIELDS["account_add"][session["index"]][0] == "cpf_type"
                and session["data"].get("type") != "cpf"):
            session["data"]["cpf_type"] = ""
            session["index"] += 1
        # Skip detected instrument fields. If only one was detected, the other
        # remains a normal guided question.
        if session["command"] in ("buy", "sell", "opening_holding", "split"):
            while session["index"] < len(FIELDS[session["command"]]):
                next_field = FIELDS[session["command"]][session["index"]][0]
                if next_field not in ("symbol", "asset_class", "currency", "price") or not session["data"].get(next_field):
                    break
                session["index"] += 1
        if session["index"] < len(FIELDS[session["command"]]):
            result = instrument_note + prompt(session, state)
        elif session["command"] in ("futurevalue", "presentvalue"):
            if calculate is None:
                result = "Calculator service is unavailable. Please try again later."
            else:
                payload = {**session["data"],
                           "calculation": "future_value" if session["command"] == "futurevalue" else "present_value",
                           "duration_months": 0, "include_schedule": True}
                calculator_result = calculate(payload)
                result = format_time_value(calculator_result)
                pending_markup = {"inline_keyboard": [
                    [{"text": "View yearly breakdown", "callback_data": "tvm:breakdown"}],
                    [{"text": "Calculate future value", "callback_data": "cmd:futurevalue"},
                     {"text": "Calculate present value", "callback_data": "cmd:presentvalue"}],
                ]}
            session = None
        else:
            result = "Review /" + session["command"] + ":\n" + "\n".join(f"{k}: {v}" for k, v in session["data"].items()) + "\n/confirm to save or /cancel."
    save_state(session=session, pending_reply=result,
               pending_markup=pending_markup or session_keyboard(session, state),
               last_tvm=calculator_result if calculator_result is not None else state["bot"].get("last_tvm"))
    return result
