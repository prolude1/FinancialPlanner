from datetime import date
from decimal import Decimal
from .domain import replay, credit_replay, loan_view, month_after, day, CORRECTION_FIELDS
from .providers import price_needs_attention


def cashflow_summary(s, today, year=None):
    """Return tracked bank cash movements by month and native currency.

    Passing ``year`` selects January through December (future months in the
    current year are zero-filled); omitting it selects the latest 12 months.
    Amounts are decimal strings and currencies are never converted or combined.
    """
    current = today.replace(day=1)
    if year is None:
        end = current
        start = current
        for _ in range(11):
            start = date(start.year - 1, 12, 1) if start.month == 1 else date(start.year, start.month - 1, 1)
        selection = "last_12_months"
    else:
        start, end = date(year, 1, 1), date(year, 12, 1)
        selection = "calendar_year"
    months = []
    cursor = start
    while cursor <= end:
        months.append(cursor.strftime("%Y-%m"))
        cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
    partial = start <= current <= end

    account_names = {key: value.get("name", key) for key, value in s.get("accounts", {}).items()}
    bank_ids = {key for key, value in s.get("accounts", {}).items() if value.get("type") == "bank"}
    series = {}

    def empty_series(cur):
        fields = ("inflow", "outflow", "net", "internal_transfer_in", "internal_transfer_out")
        return {"currency": cur, "totals": {key: Decimal(0) for key in fields},
                "months": {month: {"month": month, **{key: Decimal(0) for key in fields},
                                   "by_kind": {}, "by_account": {}} for month in months}}

    def add(event, cur, account_id, amount, field, description=None, direction=None):
        when = date.fromisoformat(event["date"])
        month = when.strftime("%Y-%m")
        if account_id not in bank_ids or month not in months or when > today:
            return
        bucket = series.setdefault(cur, empty_series(cur))
        m = bucket["months"][month]
        kind = event["kind"]
        detail = m["by_kind"].setdefault(kind, {"kind": kind, "inflow": Decimal(0), "outflow": Decimal(0),
            "internal_transfer_in": Decimal(0), "internal_transfer_out": Decimal(0), "transactions": []})
        magnitude = amount
        if field:
            m[field] += magnitude
            detail[field] += magnitude
        row = {"id": event["id"], "date": event["date"], "account": account_id,
               "account_name": account_names.get(account_id, account_id), "amount": str(magnitude)}
        if description:
            row["description"] = description
        if direction:
            row["direction"] = direction
        detail["transactions"].append(row)
        account_detail = m["by_account"].setdefault(account_id, {"account": account_id,
            "account_name": account_names.get(account_id, account_id),
            "inflow": Decimal(0), "outflow": Decimal(0), "internal_transfer_in": Decimal(0),
            "internal_transfer_out": Decimal(0), "transactions": []})
        if field:
            account_detail[field] += magnitude
        account_detail["transactions"].append({"id": event["id"], "kind": kind,
            "date": event["date"], "amount": str(magnitude), **({"direction": direction} if direction else {})})

    for event in s["events"]:
        kind = event.get("kind")
        if event.get("status") != "active" or kind not in ("deposit", "loan_disbursement", "withdraw",
                "repayment", "credit_payment", "transfer"):
            continue
        p = event["data"]
        if kind in ("deposit", "loan_disbursement", "withdraw"):
            account_id = p.get("account")
            if account_id in bank_ids:
                field = "outflow" if kind == "withdraw" else "inflow"
                add(event, str(p.get("currency", "SGD")).upper(), account_id,
                    Decimal(str(p["amount"])), field, p.get("description"))
            continue
        if kind == "repayment":
            for allocation in p.get("allocations", []):
                if allocation.get("account") in bank_ids:
                    add(event, "SGD", allocation.get("account"), Decimal(str(allocation["amount"])),
                        "outflow", s.get("loans", {}).get(p.get("loan"), {}).get("name"))
            continue
        if kind == "credit_payment":
            if p.get("funding_account") in bank_ids:
                add(event, "SGD", p.get("funding_account"), Decimal(str(p["amount"])), "outflow",
                    account_names.get(p.get("credit_account"), p.get("credit_account")))
            continue
        source, destination = p.get("account"), p.get("destination")
        source_bank, destination_bank = source in bank_ids, destination in bank_ids
        if source_bank and destination_bank:
            add(event, str(p.get("currency", "SGD")).upper(), source, Decimal(str(p["amount"])),
                "internal_transfer_out", direction="out")
            add(event, str(p.get("to_currency", p.get("currency", "SGD"))).upper(), destination,
                Decimal(str(p["received"])), "internal_transfer_in", direction="in")
        elif source_bank:
            add(event, str(p.get("currency", "SGD")).upper(), source, Decimal(str(p["amount"])),
                "outflow", direction="out")
        elif destination_bank:
            add(event, str(p.get("to_currency", p.get("currency", "SGD"))).upper(), destination,
                Decimal(str(p["received"])), "inflow", direction="in")

    output = {}
    for cur, bucket in sorted(series.items()):
        months_out = []
        totals = bucket["totals"]
        for month in months:
            m = bucket["months"][month]
            m["net"] = m["inflow"] - m["outflow"]
            for key in totals:
                totals[key] += m[key]
            for key in totals:
                m[key] = str(m[key])
            for detail in m["by_kind"].values():
                for key in ("inflow", "outflow", "internal_transfer_in", "internal_transfer_out"):
                    detail[key] = str(detail[key])
                detail["transactions"].sort(key=lambda row: (row["date"], row["id"], row["account"]))
            m["by_kind"] = [m["by_kind"][key] for key in sorted(m["by_kind"])]
            for account_detail in m["by_account"].values():
                for key in ("inflow", "outflow", "internal_transfer_in", "internal_transfer_out"):
                    account_detail[key] = str(account_detail[key])
                account_detail["transactions"].sort(key=lambda row: (row["date"], row["id"], row["kind"]))
            m["by_account"] = [m["by_account"][key] for key in sorted(m["by_account"])]
            months_out.append(m)
        totals["net"] = totals["inflow"] - totals["outflow"]
        bucket["totals"] = {key: str(value) for key, value in totals.items()}
        bucket["months"] = months_out
        output[cur] = bucket
    return {"as_of": str(today), "selection": {"type": selection, "year": year,
            "start_month": months[0], "end_month": months[-1], "current_month_partial": partial},
            "currencies": output}


def dashboard(s, today, actor="web"):
    cash, positions, instruments, reconciled, adjustments = replay(s, today)
    classes = {k: Decimal(0) for k in ("cash", "equity", "etf", "cpf")}
    warnings, missing = [], []
    fx_used = {}

    def convert(amount, cur, label):
        if cur == "SGD":
            return amount
        quote = s["fx"].get(cur)
        if not quote:
            missing.append(f"Missing {cur}/SGD rate for {label}")
            return None
        fx_used[cur] = quote
        if (today - day(quote["date"])).days > 4:
            warnings.append(f"{cur}/SGD rate is dated {quote['date']}")
        return amount * Decimal(quote["rate"])

    accounts = []
    for a in s["accounts"].values():
        balances, holdings, native = [], [], {}
        total, complete = Decimal(0), True
        for (aid, cur), amount in sorted(cash.items()):
            if aid != a["id"]:
                continue
            value = convert(amount, cur, a["name"]) if amount else Decimal(0)
            balances.append({"currency": cur, "amount": str(amount), "sgd": None if value is None else str(value)})
            native[cur] = native.get(cur, Decimal(0)) + amount
            if value is None:
                complete = False
            else:
                total += value
                classes["cpf" if a["type"] == "cpf" else "cash"] += value
        for (aid, iid), quantity in sorted(positions.items()):
            if aid != a["id"] or not quantity:
                continue
            ins, quote = instruments[iid], s["prices"].get(iid)
            value, market_value = None, None
            if quote and quote.get("currency") == ins["currency"]:
                market_value = quantity * Decimal(quote["close"])
                native[ins["currency"]] = native.get(ins["currency"], Decimal(0)) + market_value
                value = convert(market_value, ins["currency"], ins["symbol"])
                if price_needs_attention(ins["exchange"], quote["date"]):
                    warnings.append(f"{iid}: last available close is {quote['date']}")
            else:
                missing.append(f"Missing price for {iid}")
            if value is None:
                complete = False
            else:
                total += value
                classes[ins["asset_class"]] += value
            holdings.append({**ins, "quantity": str(quantity), "quote": quote,
                             "market_value": str(market_value) if market_value is not None else None,
                             "sgd": str(value) if value is not None else None})
        last = reconciled.get(a["id"])
        cpf_stale = a["type"] == "cpf" and (last is None or today >= month_after(day(last)))
        accounts.append({**a, "cash": balances, "holdings": holdings, "native": {k: str(v) for k, v in native.items()},
                         "sgd": str(total), "complete": complete, "last_reconciled": last, "cpf_stale": cpf_stale})
    loans = [loan_view(s, loan, today) for loan in s["loans"].values()]
    credit_balances, card_activity, credit_adjustments = credit_replay(s, today)
    credit_accounts = []
    credit_liability, credit_asset = Decimal(0), Decimal(0)
    for item in s.get("credit_accounts", {}).values():
        balance = credit_balances[item["id"]]
        credit_liability += max(balance, Decimal(0))
        credit_asset += max(-balance, Decimal(0))
        cards = [{**card, "activity": str(card_activity[item["id"], card["id"]])}
                 for card in item["cards"].values()]
        credit_accounts.append({**item, "balance": str(balance), "cards": cards})
    classes["cash"] += credit_asset
    adjustments.update(credit_adjustments)
    assets = sum(classes.values())
    liabilities = sum((Decimal(l["outstanding"]) for l in loans), Decimal(0)) + credit_liability
    replaced_by = {e["replaces"]: e["id"] for e in s["events"] if e.get("replaces")}
    history = []
    for event in reversed(s["events"]):
        kind = event["kind"]
        account_ids = ([allocation.get("account") for allocation in event["data"].get("allocations", [])]
                       if kind == "repayment" else
                       [event["data"].get("funding_account")] if kind == "credit_payment" else
                       [event["data"].get("account"), event["data"].get("destination")])
        archived = any(s["accounts"].get(aid, {}).get("archived") for aid in account_ids if aid)
        can_edit = (actor == "web" and event["status"] == "active"
                    and kind in CORRECTION_FIELDS and not archived)
        history.append({**event, "adjustment": adjustments.get(event["id"]),
                        "replaced_by": replaced_by.get(event["id"]),
                        "can_edit": can_edit, "can_void": can_edit,
                        "editable_fields": sorted(CORRECTION_FIELDS[kind]) if can_edit else []})
    return {"revision": s["revision"], "as_of": str(today), "accounts": accounts, "loans": loans,
            "credit_accounts": credit_accounts,
            "assets": str(assets), "liabilities": str(liabilities), "net_worth": str(assets - liabilities),
            "allocation": {k: str(v) for k, v in classes.items()}, "complete": not missing,
            "missing": sorted(set(missing)), "warnings": sorted(set(warnings)), "fx": fx_used,
            "history": history,
            "provider_status": s["provider_status"]}
