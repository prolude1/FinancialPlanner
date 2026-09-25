from datetime import date
from decimal import Decimal
from .domain import replay, credit_replay, loan_view, month_after, day
from .providers import price_needs_attention


def dashboard(s, today):
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
    return {"revision": s["revision"], "as_of": str(today), "accounts": accounts, "loans": loans,
            "credit_accounts": credit_accounts,
            "assets": str(assets), "liabilities": str(liabilities), "net_worth": str(assets - liabilities),
            "allocation": {k: str(v) for k, v in classes.items()}, "complete": not missing,
            "missing": sorted(set(missing)), "warnings": sorted(set(warnings)), "fx": fx_used,
            "history": [{**e, "adjustment": adjustments.get(e["id"])} for e in reversed(s["events"])],
            "provider_status": s["provider_status"]}
