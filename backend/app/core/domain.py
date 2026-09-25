"""Pure decimal ledger. Every mutation is replayed before it is committed."""
from calendar import monthrange
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
import uuid


class Invalid(ValueError):
    pass


def decimal(value, positive=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise Invalid("Enter a valid decimal number")
    if not result.is_finite() or abs(result) > Decimal("1e15") or result.as_tuple().exponent < -10:
        raise Invalid("Number must be finite, at most 1e15 and at most 10 decimal places")
    if result < 0 or (positive and result == 0):
        raise Invalid("Amount must be positive" if positive else "Amount cannot be negative")
    return result


def signed_decimal(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise Invalid("Enter a valid decimal number")
    if not result.is_finite() or abs(result) > Decimal("1e15") or result.as_tuple().exponent < -10:
        raise Invalid("Number must be finite, at most 1e15 and at most 10 decimal places")
    return result


def money(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def nominal_periodic_rate(annual_percent, periods_per_year):
    """Convert a nominal annual percentage to its periodic decimal rate."""
    return Decimal(annual_percent) / Decimal(100 * periods_per_year)


def currency(value):
    value = str(value).upper()
    if not re.fullmatch(r"[A-Z]{3}", value):
        raise Invalid("Use a three-letter currency code, such as SGD or USD")
    return value


def day(value):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise Invalid("Use a date in YYYY-MM-DD format")


def month_after(d):
    year, month = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(year, month, min(d.day, monthrange(year, month)[1]))


def next_due(d, anchor_day):
    following = month_after(d)
    return following.replace(day=min(anchor_day, monthrange(following.year, following.month)[1]))


def empty():
    return {"accounts": {}, "credit_accounts": {}, "events": [], "loans": {}, "prices": {}, "fx": {},
            "revision": 0, "receipts": {}, "bot": {"offset": 0, "session": None},
            "provider_status": {}}


def account(s, identifier, active=True):
    a = s["accounts"].get(str(identifier))
    if not a or (active and a["archived"]):
        raise Invalid("Account does not exist or is archived")
    return a


def instrument(p):
    exchange = str(p.get("exchange", "")).upper()
    symbol = str(p.get("symbol", "")).upper()
    if exchange not in ("NYSE", "NASDAQ", "LSE", "SGX"):
        raise Invalid("Exchange must be NYSE, NASDAQ, LSE or SGX")
    if not re.fullmatch(r"[A-Z0-9.^-]{1,20}", symbol):
        raise Invalid("Invalid symbol")
    if p.get("asset_class") not in ("equity", "etf"):
        raise Invalid("Asset class must be equity or etf")
    return {"id": exchange + ":" + symbol, "symbol": symbol, "exchange": exchange,
            "currency": currency(p["currency"]), "asset_class": p["asset_class"]}


def active_events(s, until):
    return sorted((e for e in s["events"] if e["status"] == "active" and day(e["date"]) <= until),
                  key=lambda e: (e["date"], e["order"]))


def replay(s, until=None):
    until = until or date.today()
    cash, positions, instruments, reconciled = defaultdict(Decimal), defaultdict(Decimal), {}, {}
    adjustments = {}
    for e in active_events(s, until):
        p, kind = e["data"], e["kind"]
        aid, cur = p.get("account"), p.get("currency")
        if kind in ("opening_cash", "deposit", "withdraw"):
            cash[aid, cur] += decimal(p["amount"]) * (-1 if kind == "withdraw" else 1)
            if kind == "opening_cash" and s["accounts"][aid]["type"] == "cpf":
                reconciled[aid] = e["date"]
        elif kind == "cpf_set":
            adjustments[e["id"]] = str(decimal(p["amount"]) - cash[aid, cur])
            cash[aid, cur] = decimal(p["amount"])
            reconciled[aid] = e["date"]
        elif kind in ("buy", "sell", "opening_holding", "split"):
            ins = instrument(p)
            old = instruments.get(ins["id"])
            if old and old != ins:
                raise Invalid("Instrument class/currency conflicts with existing history")
            instruments[ins["id"]] = ins
            key = aid, ins["id"]
            if kind == "split":
                if not positions[key]:
                    raise Invalid("Cannot split an empty holding")
                positions[key] *= decimal(p["ratio"], True)
            else:
                qty = decimal(p["quantity"], True)
                positions[key] += qty * (-1 if kind == "sell" else 1)
                if kind != "opening_holding":
                    cash[aid, cur] += qty * decimal(p["price"], True) * (1 if kind == "sell" else -1)
        elif kind == "transfer":
            cash[aid, cur] -= decimal(p["amount"], True)
            cash[p["destination"], p["to_currency"]] += decimal(p["received"], True)
        elif kind == "repayment":
            for allocation in p["allocations"]:
                cash[allocation["account"], "SGD"] -= decimal(allocation["amount"], True)
        elif kind == "loan_disbursement":
            cash[aid, "SGD"] += decimal(p["amount"], True)
        elif kind == "credit_payment":
            cash[p["funding_account"], "SGD"] -= decimal(p["amount"], True)
        if any(v < 0 for v in cash.values()) or any(v < 0 for v in positions.values()):
            raise Invalid(f"Insufficient cash or holdings at transaction {e['id']} ({e['date']}); check earlier entries")
    return cash, positions, instruments, reconciled, adjustments


def credit_replay(s, until=None):
    """Return authoritative account balances and unallocated per-card activity."""
    until = until or date.today()
    balances, activity, adjustments = defaultdict(Decimal), defaultdict(Decimal), {}
    for e in active_events(s, until):
        p, kind = e["data"], e["kind"]
        if kind == "credit_purchase":
            balances[p["credit_account"]] += decimal(p["amount"], True)
            activity[p["credit_account"], p["card"]] += decimal(p["amount"], True)
        elif kind == "credit_refund":
            balances[p["credit_account"]] -= decimal(p["amount"], True)
            activity[p["credit_account"], p["card"]] -= decimal(p["amount"], True)
        elif kind == "credit_payment":
            balances[p["credit_account"]] -= decimal(p["amount"], True)
        elif kind == "credit_set":
            adjustment = signed_decimal(p["amount"]) - balances[p["credit_account"]]
            balances[p["credit_account"]] = signed_decimal(p["amount"])
            adjustments[e["id"]] = str(adjustment)
    return balances, activity, adjustments


def credit_account(s, identifier):
    item = s.get("credit_accounts", {}).get(str(identifier))
    if not item:
        raise Invalid("Credit-card account does not exist")
    return item


def loan_balance(s, loan, until):
    principal = decimal(loan["principal"])
    interest = decimal(loan["opening_interest"])
    start = day(loan["as_of"])
    if until < start:
        return Decimal(0), Decimal(0), []
    timeline = []
    first = month_after(start.replace(day=1))
    while first <= until:
        timeline.append((first, -1, None))
        first = month_after(first)
    for e in active_events(s, until):
        if e["kind"] == "repayment" and e["data"]["loan"] == loan["id"]:
            timeline.append((day(e["date"]), e["order"], e))
    detail = []
    for when, _, e in sorted(timeline, key=lambda item: (item[0], item[1])):
        if e is None:
            rates = [r for r in loan["rates"] if day(r["date"]) <= when]
            rate = decimal(sorted(rates, key=lambda r: r["date"])[-1]["rate"])
            charge = money(principal * nominal_periodic_rate(rate, 12))
            interest += charge
            detail.append({"date": str(when), "kind": "interest", "amount": str(charge)})
        else:
            if when <= start:
                raise Invalid("Repayment must be after the loan's end-of-day opening date")
            amount = decimal(e["data"]["amount"], True)
            if amount > principal + interest:
                raise Invalid("Repayment exceeds outstanding principal plus accrued interest")
            paid_interest = min(amount, interest)
            interest -= paid_interest
            principal -= amount - paid_interest
            detail.append({"date": str(when), "kind": "repayment", "id": e["id"],
                           "amount": str(amount), "interest": str(paid_interest),
                           "principal": str(amount - paid_interest)})
    return principal, interest, detail


def loan_view(s, loan, today):
    principal, interest, history = loan_balance(s, loan, today)
    projected, unpaid = principal, interest
    due = day(loan["next_due"])
    anchor_day = due.day
    while due <= today:
        due = next_due(due, anchor_day)
    cursor = today
    schedule = []
    for _ in range(600):
        if projected + unpaid <= 0:
            break
        first = month_after(cursor.replace(day=1))
        while first <= due:
            rate = sorted((r for r in loan["rates"] if day(r["date"]) <= first), key=lambda r: r["date"])[-1]
            unpaid += money(projected * nominal_periodic_rate(decimal(rate["rate"]), 12))
            first = month_after(first)
        payment = min(decimal(loan["installment"], True), projected + unpaid)
        paid_interest = min(unpaid, payment)
        unpaid -= paid_interest
        projected -= payment - paid_interest
        schedule.append({"date": str(due), "payment": str(payment), "interest": str(paid_interest),
                         "principal": str(payment - paid_interest), "remaining": str(projected + unpaid)})
        cursor, due = due, next_due(due, anchor_day)
    return {**loan, "outstanding_principal": str(principal), "accrued_interest": str(interest),
            "outstanding": str(principal + interest), "history": history, "schedule": schedule,
            "schedule_complete": projected + unpaid == 0}


def validate_payload(s, kind, p, today):
    when = day(p.get("date", str(today)))
    if when > today or when.year < 1970:
        raise Invalid("Transactions must be dated between 1970 and today")
    p["date"] = str(when)
    if kind in ("credit_set", "credit_purchase", "credit_refund", "credit_payment"):
        credit = credit_account(s, p.get("credit_account"))
        amount = signed_decimal(p["amount"]) if kind == "credit_set" else decimal(p["amount"], True)
        if money(amount) != amount:
            raise Invalid("Credit-card amounts use SGD cents")
        p["currency"] = "SGD"
        if kind in ("credit_purchase", "credit_refund"):
            if p.get("card") not in credit["cards"]:
                raise Invalid("Card does not belong to this credit-card account")
            description = str(p.get("description", "")).strip()
            if not description or len(description) > 160:
                raise Invalid("Description must be 1–160 characters")
            p["description"] = description
        if kind == "credit_payment":
            funding = account(s, p.get("funding_account"))
            if funding["type"] == "cpf":
                raise Invalid("Credit-card payments cannot be funded from CPF")
        return
    if kind == "repayment":
        if p.get("loan") not in s["loans"]:
            raise Invalid("Loan not found")
        amount = decimal(p["amount"], True)
        if money(amount) != amount:
            raise Invalid("Repayments use whole cents")
        allocations = p.get("allocations", [])
        if not allocations or len(allocations) > 50:
            raise Invalid("Provide repayment funding accounts")
        for x in allocations:
            a = account(s, x["account"])
            if a["type"] == "cpf" and a["cpf_type"] != "OA":
                raise Invalid("Only CPF OA can fund housing repayments")
            if money(decimal(x["amount"], True)) != decimal(x["amount"]):
                raise Invalid("Funding allocations use whole cents")
        if sum(decimal(x["amount"]) for x in allocations) != amount:
            raise Invalid("Funding allocations must equal the repayment")
        return
    a = account(s, p.get("account"))
    p["currency"] = currency(p.get("currency", a["currency"]))
    if a["type"] == "cpf" and p["currency"] != "SGD":
        raise Invalid("CPF balances use SGD")
    if kind in ("opening_cash", "deposit", "withdraw", "cpf_set", "transfer"):
        decimal(p["amount"], kind in ("deposit", "withdraw", "transfer"))
    if kind in ("deposit", "withdraw") and p.get("description") is not None:
        description = str(p["description"]).strip()
        if not description or len(description) > 160:
            raise Invalid("Description must be 1–160 characters")
        p["description"] = description
    if kind == "cpf_set" and a["type"] != "cpf":
        raise Invalid("Balance reconciliation is available for CPF accounts")
    if kind == "opening_cash":
        if any(e["status"] == "active" and e["kind"] == kind and e["data"]["account"] == a["id"] and e["data"]["currency"] == p["currency"] for e in s["events"]):
            raise Invalid("An opening cash balance already exists for this account and currency")
    if kind in ("buy", "sell", "opening_holding", "split"):
        if a["type"] != "brokerage":
            raise Invalid("Investments require a brokerage account")
        ins = instrument(p)
        if ins["exchange"] in ("NYSE", "NASDAQ") and ins["currency"] != "USD":
            raise Invalid("US trading lines use USD")
        if kind == "split":
            decimal(p["ratio"], True)
        else:
            decimal(p["quantity"], True)
            if kind != "opening_holding" or p.get("price") not in (None, "", "unknown"):
                decimal(p["price"], True)
        if kind == "opening_holding" and any(e["status"] == "active" and e["kind"] == kind and e["data"]["account"] == a["id"] and instrument(e["data"])["id"] == ins["id"] for e in s["events"]):
            raise Invalid("An opening holding already exists; correct it instead")
    if kind == "transfer":
        dest = account(s, p.get("destination"))
        p["to_currency"] = currency(p.get("to_currency", p["currency"]))
        if dest["type"] == "cpf" and p["to_currency"] != "SGD":
            raise Invalid("CPF balances use SGD")
        if dest["id"] == a["id"] and p["to_currency"] == p["currency"]:
            raise Invalid("Choose a different account or currency")
        received = decimal(p.get("received", p["amount"]), True)
        p["received"] = str(received)
        if p["to_currency"] == p["currency"] and received != decimal(p["amount"]):
            raise Invalid("Same-currency transfers must have equal amounts")
        p["rate"] = str(received / decimal(p["amount"]))
        p["rate_direction"] = f"{p['to_currency']} per {p['currency']}"


KINDS = {"opening_cash", "deposit", "withdraw", "buy", "sell", "opening_holding", "transfer", "cpf_set", "split", "repayment",
         "credit_set", "credit_purchase", "credit_refund", "credit_payment"}


def apply(s, command, payload, actor, key, today=None):
    """Mutates a caller-owned copy. Repository commits only when this returns."""
    today = today or date.today()
    if command == "credit_set":
        raise Invalid("Credit-card balances are calculated from purchases, refunds, and payments")
    if not key or len(key) > 160:
        raise Invalid("A valid idempotency key is required")
    receipt_key = actor + ":" + key
    if receipt_key in s["receipts"]:
        receipt = s["receipts"][receipt_key]
        if receipt["command"] != command or receipt["payload"] != payload:
            raise Invalid("Idempotency key was already used for a different request")
        return receipt["result"]
    original_payload, p = deepcopy(payload), deepcopy(payload)
    if actor == "bot" and (command.startswith("loan") or command == "repayment"):
        raise Invalid("Loan operations are available only in the local web UI")
    result = {}
    if command == "credit_account_add":
        name = str(p.get("name", "")).strip()
        if not name or len(name) > 100:
            raise Invalid("Credit-card account name must be 1–100 characters")
        if any(item["name"].casefold() == name.casefold() for item in s.setdefault("credit_accounts", {}).values()):
            raise Invalid("A credit-card account with this name already exists")
        ident = uuid.uuid4().hex[:10]
        s["credit_accounts"][ident] = {"id": ident, "name": name, "currency": "SGD", "cards": {}}
        result = deepcopy(s["credit_accounts"][ident])
    elif command == "credit_card_add":
        credit = credit_account(s, p.get("credit_account"))
        name = str(p.get("name", "")).strip()
        if not name or len(name) > 100:
            raise Invalid("Card name must be 1–100 characters")
        if any(card["name"].casefold() == name.casefold() for card in credit["cards"].values()):
            raise Invalid("A card with this name already exists in that account")
        ident = uuid.uuid4().hex[:10]
        credit["cards"][ident] = {"id": ident, "name": name}
        result = deepcopy(credit["cards"][ident])
    elif command == "account_add":
        name = str(p.get("name", "")).strip()
        if not name or len(name) > 100:
            raise Invalid("Account name must be 1–100 characters")
        if p.get("type") not in ("bank", "brokerage", "cpf"):
            raise Invalid("Account type must be bank, brokerage or cpf")
        if any(a["name"].casefold() == name.casefold() for a in s["accounts"].values()):
            raise Invalid("An account with this name already exists")
        cur = currency(p.get("currency", "SGD"))
        cpf_type = str(p.get("cpf_type", "")).upper()
        if p["type"] == "cpf" and (cpf_type not in ("OA", "SA", "MA") or cur != "SGD"):
            raise Invalid("CPF requires SGD and subtype OA, SA or MA")
        ident = uuid.uuid4().hex[:10]
        s["accounts"][ident] = {"id": ident, "name": name, "type": p["type"], "currency": cur,
                                   "cpf_type": cpf_type, "archived": False}
        result = s["accounts"][ident]
    elif command == "account_rename":
        a = account(s, p.get("account"))
        name = str(p.get("name", "")).strip()
        if not name or len(name) > 100:
            raise Invalid("Account nickname must be 1–100 characters")
        if any(other["id"] != a["id"] and other["name"].casefold() == name.casefold()
               for other in s["accounts"].values()):
            raise Invalid("An account with this nickname already exists")
        a["name"] = name
        result = deepcopy(a)
    elif command == "account_archive":
        a = account(s, p.get("account"))
        cash, pos, *_ = replay(s, today)
        if any(v for (aid, _), v in list(cash.items()) + list(pos.items()) if aid == a["id"]):
            raise Invalid("Sell holdings and transfer or withdraw cash before archiving")
        a["archived"] = True
        result = a
    elif command == "loan_add":
        principal, installment = decimal(p["principal"], True), decimal(p["installment"], True)
        rate, interest = decimal(p["rate"]), decimal(p.get("opening_interest", "0"))
        if any(money(v) != v for v in (principal, installment, interest)) or rate > 100:
            raise Invalid("Loan amounts require cents and rate must be between 0 and 100 percent")
        start, due = day(p["as_of"]), day(p["next_due"])
        if start > today or start.year < 1970 or due <= start or due > month_after(start):
            raise Invalid("Opening date must be today or earlier; next due date must be within one month after opening")
        name = str(p.get("name", "HDB loan")).strip()
        if not name or len(name) > 100:
            raise Invalid("Enter a loan name up to 100 characters")
        ident = uuid.uuid4().hex[:10]
        loan = {"id": ident, "name": name, "currency": "SGD", "principal": str(principal),
                "installment": str(installment), "as_of": str(start), "next_due": str(due),
                "opening_interest": str(interest), "rates": [{"date": str(start), "rate": str(rate)}]}
        s["loans"][ident] = loan
        if p.get("disburse_to"):
            destination = account(s, p["disburse_to"])
            if destination["type"] == "cpf":
                raise Invalid("Loan proceeds cannot be deposited into CPF")
            s["events"].append({"id": uuid.uuid4().hex[:10], "kind": "loan_disbursement", "date": str(start),
                "order": len(s["events"]), "status": "active", "actor": actor,
                "data": {"account": p["disburse_to"], "amount": str(principal), "loan": ident, "currency": "SGD"}})
        result = loan
    elif command == "loan_rate":
        loan = s["loans"].get(p.get("loan"))
        if not loan:
            raise Invalid("Loan not found")
        effective, rate = day(p["date"]), decimal(p["rate"])
        if effective.day != 1 or effective <= day(loan["as_of"]) or rate > 100:
            raise Invalid("Rate changes must start on the first of a month after loan opening, at 0–100 percent")
        if any(r["date"] == str(effective) for r in loan["rates"]):
            raise Invalid("A rate already exists for that date")
        loan["rates"].append({"date": str(effective), "rate": str(rate)})
        result = loan
    elif command in KINDS or command in ("correct", "void"):
        previous = None
        kind = command
        if command in ("correct", "void"):
            previous = next((e for e in s["events"] if e["id"] == p.get("transaction") and e["status"] == "active"), None)
            if not previous:
                raise Invalid("Active transaction not found")
            if previous["kind"] == "loan_disbursement":
                raise Invalid("Loan opening disbursements cannot be edited independently")
            if actor == "bot" and previous["kind"] == "repayment":
                raise Invalid("Repayment corrections are web-only")
            if any(s["accounts"].get(aid, {}).get("archived") for aid in [previous["data"].get("account"), previous["data"].get("destination")]):
                raise Invalid("Transactions in archived accounts cannot be changed")
            previous["status"] = "superseded" if command == "correct" else "void"
            previous["changed_by"] = actor
            kind = previous["kind"]
            p = {**previous["data"], **p.get("changes", {})}
        if command != "void":
            validate_payload(s, kind, p, today)
            event = {"id": uuid.uuid4().hex[:10], "kind": kind, "data": p, "date": p["date"],
                     "order": previous["order"] if previous else len(s["events"]), "status": "active",
                     "actor": actor, "created_at": datetime.now(timezone.utc).isoformat()}
            if previous:
                event["replaces"] = previous["id"]
            s["events"].append(event)
            result = event
        else:
            result = {"id": previous["id"], "status": "void"}
    else:
        raise Invalid("Unknown operation")
    cash_balances, holding_balances, *_ = replay(s, today)
    for (aid, _), balance in list(cash_balances.items()) + list(holding_balances.items()):
        if balance and s["accounts"][aid]["archived"]:
            raise Invalid("This change would restore a balance in an archived account")
    for loan in s["loans"].values():
        loan_balance(s, loan, today)
    s["revision"] += 1
    s["receipts"][receipt_key] = {"command": command, "payload": original_payload, "result": deepcopy(result),
                                  "at": datetime.now(timezone.utc).isoformat()}
    return result
