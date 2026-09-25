"""Telegram command definitions, formatting, and guided conversation state."""
import os
import re
import time
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from ..core import store

FIELDS = {
    "stock": [("query", "Enter a ticker or company/security name.")],
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
COMMAND_DESCRIPTIONS = {
    "start": "Open the private financial ledger",
    "help": "Show every available command",
    "account": "Account actions and balances",
    "creditcard": "Credit-card actions and balances",
    "deposit": "Record money entering an account",
    "withdraw": "Record spending or money leaving",
    "transfer": "Move money between accounts",
    "cpf_set": "Reconcile a CPF statement balance",
    "purchase": "Record a credit-card purchase",
    "payment": "Pay a credit-card balance",
    "calculator": "Open financial calculators",
    "stock": "Look up prices and financials",
    "cancel": "Cancel the current operation",
    "buy": "Buy shares or ETFs",
    "sell": "Sell shares or ETFs",
    "opening_holding": "Record an existing holding",
    "split": "Record a stock split",
}

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
            for command, description in COMMAND_DESCRIPTIONS.items()]


def configure_command_menu(client, base, owner):
    """Publish native slash-command suggestions for the owner's private chat."""
    registered = client.post(base + "setMyCommands", json={
        "commands": telegram_commands(),
        "scope": {"type": "chat", "chat_id": owner},
    })
    registered.raise_for_status()
    menu = client.post(base + "setChatMenuButton", json={
        "chat_id": owner,
        "menu_button": {"type": "commands"},
    })
    menu.raise_for_status()


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


def valid_security_id(value):
    return bool(re.fullmatch(r"(?:NASDAQ|NYSE|LSE|SGX):[A-Z0-9.^-]{1,20}", str(value)))


def metric(value):
    return "-" if value is None else format_amount(value)


def cache_note(payload):
    cache = payload.get("cache") or {}
    return "\n⚠ Cached/stale data." if cache.get("stale") else ""


def format_stock_price(overview):
    security, price = overview["security"], overview["latest_price"]
    change_text = "-"
    if price.get("change") is not None:
        change = Decimal(price["change"])
        percent = Decimal(price["change_percent"]) if price.get("change_percent") is not None else None
        change_text = f"{change:+,.2f}" + (f" ({percent:+,.2f}%)" if percent is not None else "")
    return (f"{security['name']} ({security['exchange']}:{security['symbol']})\n"
            f"Latest completed-session close: {price['currency']} {metric(price.get('close'))}\n"
            f"Change vs prior session: {change_text}\n"
            f"Session date: {price.get('session_date') or '-'}\n"
            f"As of: {price.get('data_timestamp') or price.get('retrieved_at') or '-'}"
            + cache_note(overview))


def format_earnings(earnings):
    return (f"Latest earnings/report ({earnings.get('period_type') or '-'})\n"
            f"Period end: {earnings.get('period_end') or '-'}\n"
            f"Report/filing date: {earnings.get('report_date') or '-'}\n"
            f"Currency: {earnings.get('currency') or '-'}\n"
            f"Revenue: {metric(earnings.get('revenue'))}\n"
            f"Free cash flow: {metric(earnings.get('free_cash_flow'))}\n"
            f"Profit after tax/net income: {metric(earnings.get('profit_after_tax'))}\n"
            f"EBITDA: {metric(earnings.get('ebitda'))}\n"
            f"EBITA: {metric(earnings.get('ebita'))}" + cache_note(earnings))


def format_financials(financials):
    lines = [f"5-year financials · {financials.get('currency') or '-'}"]
    for row in financials.get("fiscal_years", []):
        lines.extend([f"\n{row.get('fiscal_year', '-')} · period end {row.get('period_end') or '-'}",
                      f"Report date: {row.get('report_date') or '-'}",
                      f"Revenue: {metric(row.get('revenue'))}",
                      f"Free cash flow: {metric(row.get('free_cash_flow'))}",
                      f"Profit after tax/net income: {metric(row.get('profit_after_tax'))}",
                      f"EBITDA: {metric(row.get('ebitda'))}",
                      f"EBITA: {metric(row.get('ebita'))}"])
    coverage = financials.get("coverage") or {}
    if not coverage.get("complete", True):
        lines.append(f"\n⚠ Coverage incomplete: {coverage.get('returned_years', 0)} of {coverage.get('requested_years', 5)} years available.")
    return "\n".join(lines) + cache_note(financials)


def stock_keyboard(security_id):
    return {"inline_keyboard": [
        [{"text": "Latest price", "callback_data": "stock:price:" + security_id},
         {"text": "Latest earnings", "callback_data": "stock:earnings:" + security_id}],
        [{"text": "5-year financials", "callback_data": "stock:financials:" + security_id}],
        [{"text": "Change stock", "callback_data": "cmd:stock"},
         {"text": "Refresh", "callback_data": "stock:refresh:" + security_id}],
    ]}


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
    if field == "destination" and session["data"].get("account"):
        accounts = [a for a in accounts if a["id"] != session["data"]["account"]]
    if field == "funding_account":
        accounts = [a for a in accounts if a["type"] != "cpf"]
    return sorted(accounts, key=lambda a: (ACCOUNT_TYPE_ORDER.index(a["type"]), a["name"].casefold()))


def session_keyboard(session, state):
    if not session:
        return None
    if session["index"] == len(FIELDS[session["command"]]):
        return {"inline_keyboard": [[
            {"text": "Yes, save", "callback_data": "confirm"},
            {"text": "No, cancel", "callback_data": "cancel"},
        ]]}
    field = FIELDS[session["command"]][session["index"]][0]
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


def authorized(update, owner):
    msg = update.get("message", {})
    return bool(owner and msg.get("from", {}).get("id") == owner and
                msg.get("chat", {}).get("type") == "private" and msg.get("chat", {}).get("id") == owner)


def handle(update, owner, query, mutate, resolve_instrument=None, calculate=None, stocks=None):
    if not authorized(update, owner):
        return None
    text = update["message"].get("text", "").strip()
    uid = update["update_id"]
    state = store.read()
    if uid < state["bot"]["offset"]:
        return None
    session = state["bot"]["session"]
    retired_commands = {"history", "correct", "void"}
    legacy_edit_session = bool(session and session.get("command") in {"correct", "void"})
    if legacy_edit_session:
        # Persisted conversation state can outlive a bot restart. Never let a
        # post-upgrade reply finish an edit workflow removed from Telegram.
        session = None
    if session and time.time() - session["started"] > 1800:
        session = None
    result = "Use /help for available commands."
    pending_markup = None
    calculator_result = None
    last_stock = None
    command = text.split(" ", 1)[0].split("@", 1)[0].lstrip("/").lower()
    command = COMMAND_ALIASES.get(command, command)
    if legacy_edit_session:
        result = "This older Telegram edit was cancelled without changes. View and edit transactions in the web app."
    elif text.startswith("/") and command in retired_commands:
        result = "Transaction history and edits are handled in the web app. No changes were made."
    elif text.startswith("/"):
        if command in ("start", "help"):
            result = "Your private financial ledger.\n/account · /creditcard · /calculator\n/deposit · /withdraw · /transfer · /cpf_set\n/purchase · /payment\n/buy · /sell · /opening_holding · /split · /cancel\nView or edit transactions in the local web app. Loans are managed there too."
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
        elif command in ("stock_show", "stock_price", "stock_earnings", "stock_financials", "stock_refresh"):
            session = None
            security_id = text.partition(" ")[2].strip().upper()
            if not valid_security_id(security_id):
                result = "This stock selection is invalid or expired. Use /stock to search again."
            elif stocks is None:
                result = "Stock service is unavailable. Please try again later."
            else:
                last_stock = security_id
                if command in ("stock_show", "stock_refresh"):
                    overview = stocks("overview", security_id)
                    earnings = stocks("earnings", security_id)
                    result = format_stock_price(overview) + "\n\n" + format_earnings(earnings)
                elif command == "stock_price":
                    result = format_stock_price(stocks("overview", security_id))
                elif command == "stock_earnings":
                    result = format_earnings(stocks("earnings", security_id))
                else:
                    result = format_financials(stocks("financials", security_id))
                pending_markup = stock_keyboard(security_id)
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
                saved = mutate(session["command"], payload, "telegram-" + str(uid))
                result = "Saved. Reference: " + str(saved.get("id", "ok")) + ". The dashboard will refresh automatically."
                session = None
        elif command in FIELDS:
            session = {"command": command, "data": {}, "index": 0, "started": time.time()}
            result = prompt(session, state)
    elif session and session["index"] < len(FIELDS[session["command"]]):
        field = FIELDS[session["command"]][session["index"]][0]
        instrument_note = ""
        instrument_shortcut = None
        if field == "date" and text.lower() == "today":
            text = str(datetime.now(ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Singapore"))).date())
        if session["command"] == "account_add" and field == "type":
            account_types = {
                "bank": "bank", "bank account": "bank",
                "brokerage": "brokerage", "brokerage account": "brokerage", "broker": "brokerage",
                "cpf": "cpf", "cpf account": "cpf",
            }
            text = account_types.get(text.strip().lower(), text.strip().lower())
        if session["command"] == "account_add" and field in ("currency", "cpf_type"):
            text = text.strip().upper()
        if session["command"] == "account_add" and field == "currency" and text not in ("SGD", "USD"):
            result = "Only SGD and USD are supported. Enter SGD or USD, then retry.\n" + prompt(session, state)
            with store.transaction() as s:
                s["bot"]["session"] = session
                s["bot"]["offset"] = uid + 1
                s["bot"]["pending_reply"] = result
                s["bot"]["pending_markup"] = session_keyboard(session, state)
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
                    with store.transaction() as s:
                        s["bot"]["session"] = session
                        s["bot"]["offset"] = uid + 1
                        s["bot"]["pending_reply"] = result
                        s["bot"]["pending_markup"] = session_keyboard(session, state)
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
                with store.transaction() as s:
                    s["bot"]["session"] = session
                    s["bot"]["offset"] = uid + 1
                    s["bot"]["pending_reply"] = result
                    s["bot"]["pending_markup"] = session_keyboard(session, state)
                return result
            text = parts[0]
            session["data"]["price"] = parts[1]
        money_shortcut = False
        if field in ("money", "received_money"):
            match = re.fullmatch(r"\s*(SGD|USD)\s+(\d+(?:\.\d+)?)\s*", text, re.IGNORECASE)
            if not match:
                result = ("Enter currency and amount together using SGD or USD, "
                          "for example SGD 100 or USD 25.50. Please retry.\n" + prompt(session, state))
                with store.transaction() as s:
                    s["bot"]["session"] = session
                    s["bot"]["offset"] = uid + 1
                    s["bot"]["pending_reply"] = result
                    s["bot"]["pending_markup"] = session_keyboard(session, state)
                return result
            currency_code, amount = match.groups()
            if field == "money":
                session["data"]["currency"] = currency_code.upper()
                session["data"]["amount"] = amount
            else:
                session["data"]["to_currency"] = currency_code.upper()
                session["data"]["received"] = amount
            money_shortcut = True
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
                with store.transaction() as s:
                    s["bot"]["session"] = session
                    s["bot"]["offset"] = uid + 1
                    s["bot"]["pending_reply"] = result
                    s["bot"]["pending_markup"] = session_keyboard(session, state)
                return result
        if not instrument_shortcut and not card_shortcut and not money_shortcut:
            session["data"][field] = text
        if (field == "symbol" or instrument_shortcut) and session["command"] in ("buy", "sell", "opening_holding", "split"):
            exchange = session["data"].get("exchange", "").upper()
            symbol = session["data"].get("symbol", "").upper()
            if exchange in ("NYSE", "NASDAQ"):
                session["data"]["currency"] = "USD"
            try:
                detected = resolve_instrument(exchange, symbol) if resolve_instrument else None
            except Exception as exc:
                # Keep the guided session on the symbol question so a typo or
                # unsupported security can be corrected immediately.
                session["data"].pop("symbol", None)
                if field == "exchange":
                    session["data"].pop("exchange", None)
                result = "Ticker not accepted: " + str(exc) + ".\n" + prompt(session, state)
                with store.transaction() as s:
                    s["bot"]["session"] = session
                    s["bot"]["offset"] = uid + 1
                    s["bot"]["pending_reply"] = result
                    s["bot"]["pending_markup"] = session_keyboard(session, state)
                return result
            if detected:
                detected_currency = str(detected.get("currency", "")).upper()
                detected_class = str(detected.get("asset_class", "")).lower()
                if len(detected_currency) == 3 and detected_currency.isalpha():
                    session["data"]["currency"] = detected_currency
                if detected_class in ("equity", "etf"):
                    session["data"]["asset_class"] = detected_class
            notes = []
            if session["data"].get("asset_class"):
                notes.append("Detected asset class: " + session["data"]["asset_class"].upper() + ".")
            if session["data"].get("currency"):
                notes.append("Detected trading currency: " + session["data"]["currency"] + ".")
            if notes:
                instrument_note = "\n".join(notes) + "\n"
        session["index"] += 1
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
        elif session["command"] == "stock":
            matches = stocks("search", session["data"]["query"]) if stocks else {"results": []}
            rows = matches.get("results", [])
            session = None
            if not rows:
                result = "No matching securities found. Use /stock to try another ticker or company name."
            else:
                result = "Choose one security. Tickers on different exchanges are kept separate:"
                pending_markup = {"inline_keyboard": [[{
                    "text": f"{row['name']} · {row['exchange']}:{row['symbol']}",
                    "callback_data": "stock:show:" + row["security_id"]}]
                    for row in rows if valid_security_id(row.get("security_id"))]}
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
    with store.transaction() as s:
        s["bot"]["session"] = session
        s["bot"]["offset"] = uid + 1
        s["bot"]["pending_reply"] = result
        s["bot"]["pending_markup"] = pending_markup or session_keyboard(session, state)
        if calculator_result is not None:
            s["bot"]["last_tvm"] = calculator_result
        if last_stock is not None:
            s["bot"]["last_stock"] = last_stock
    return result
