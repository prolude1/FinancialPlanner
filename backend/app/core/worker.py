"""Free daily quote adapters. Provider failures never erase cached valuations."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
import os
import time
from zoneinfo import ZoneInfo
import httpx
from . import store
from .domain import replay, active_events, instrument, decimal
from .providers import StockPriceProviderFactory, completed_session


stock_price_provider = StockPriceProviderFactory.create(
    os.environ.get("STOCK_PRICE_PROVIDER", "yahoo"))


def provider_symbol(exchange, symbol):
    return stock_price_provider.provider_symbol(exchange, symbol)


def normalize_provider_currency(value):
    # Yahoo identifies penny-denominated London lines as GBp. The ledger keeps
    # cash and prices in pounds, so those quote values are scaled by 0.01.
    return stock_price_provider.normalize_currency(value)


def resolve_instrument(exchange, symbol):
    return stock_price_provider.resolve_instrument(exchange, symbol)


def resolve_trading_currency(exchange, symbol):
    """Compatibility helper for callers that only need currency."""
    if exchange.upper() in ("NYSE", "NASDAQ"):
        return "USD"
    return resolve_instrument(exchange, symbol)["currency"]


def fetch_price(ins, now=None):
    return stock_price_provider.fetch_price(ins, now)


def fetch_fx(cur):
    response = httpx.get(f"https://api.frankfurter.dev/v2/rate/{cur}/SGD", timeout=20)
    response.raise_for_status()
    row = response.json()
    rate = Decimal(str(row["rate"]))
    if not rate.is_finite() or rate <= 0:
        raise ValueError("Invalid FX rate")
    return {"date": row["date"], "rate": str(rate), "source": "Frankfurter daily reference",
            "retrieved_at": datetime.now(timezone.utc).isoformat()}


def fetch_price_history(ins, start, end):
    return stock_price_provider.fetch_price_history(ins, start, end)


def fetch_fx_history(cur, start, end):
    if cur == "SGD":
        return {start: Decimal(1)}
    response = httpx.get("https://api.frankfurter.dev/v2/rates", params={
        "from": str(start), "to": str(end), "base": cur, "quotes": "SGD"}, timeout=30)
    response.raise_for_status()
    return {datetime.fromisoformat(row["date"]).date(): Decimal(str(row["rate"]))
            for row in response.json()}


def _at_or_before(series, when):
    available = [(date_key, value) for date_key, value in series.items() if date_key <= when]
    return max(available, key=lambda item: item[0])[1] if available else None


def build_portfolio_history(snapshot, quotes, rates, today):
    """Derive daily SGD values and cash-flow-adjusted movement from the event ledger."""
    events = active_events(snapshot, today)
    if not events:
        return []
    first = min(datetime.fromisoformat(e["date"]).date() for e in events)
    accounts = [a for a in snapshot["accounts"].values() if a["type"] == "brokerage"]
    previous = {}
    rows = []
    when = first
    while when <= today:
        cash, positions, instruments, *_ = replay(snapshot, when)
        for account_row in accounts:
            aid, total, complete = account_row["id"], Decimal(0), True
            for (cash_account, cur), amount in cash.items():
                if cash_account != aid or not amount:
                    continue
                rate = Decimal(1) if cur == "SGD" else _at_or_before(rates.get(cur, {}), when)
                if rate is None:
                    complete = False
                else:
                    total += amount * rate
            for (position_account, iid), quantity in positions.items():
                if position_account != aid or not quantity:
                    continue
                close = _at_or_before(quotes.get(iid, {}), when)
                cur = instruments[iid]["currency"]
                rate = Decimal(1) if cur == "SGD" else _at_or_before(rates.get(cur, {}), when)
                if close is None or rate is None:
                    complete = False
                else:
                    total += quantity * close * rate
            if not complete:
                continue
            flow = Decimal(0)
            for event in (e for e in events if e["date"] == str(when) and e["status"] == "active"):
                p, kind = event["data"], event["kind"]
                if p.get("account") == aid and kind in ("opening_cash", "deposit", "withdraw", "loan_disbursement"):
                    amount = decimal(p["amount"])
                    if kind == "withdraw":
                        amount = -amount
                    rate = Decimal(1) if p.get("currency", "SGD") == "SGD" else _at_or_before(rates.get(p["currency"], {}), when)
                    if rate is not None:
                        flow += amount * rate
                if kind == "transfer":
                    if p.get("account") == aid:
                        rate = Decimal(1) if p["currency"] == "SGD" else _at_or_before(rates.get(p["currency"], {}), when)
                        if rate is not None:
                            flow -= decimal(p["amount"]) * rate
                    if p.get("destination") == aid:
                        rate = Decimal(1) if p["to_currency"] == "SGD" else _at_or_before(rates.get(p["to_currency"], {}), when)
                        if rate is not None:
                            flow += decimal(p["received"]) * rate
                if kind == "opening_holding" and p.get("account") == aid:
                    iid = instrument(p)["id"]
                    close = _at_or_before(quotes.get(iid, {}), when)
                    rate = Decimal(1) if p["currency"] == "SGD" else _at_or_before(rates.get(p["currency"], {}), when)
                    if close is not None and rate is not None:
                        flow += decimal(p["quantity"]) * close * rate
                if kind == "repayment":
                    flow -= sum((decimal(a["amount"]) for a in p["allocations"] if a["account"] == aid), Decimal(0))
                if kind == "credit_payment" and p.get("funding_account") == aid:
                    flow -= decimal(p["amount"])
            gross = None if aid not in previous else total - previous[aid]
            adjusted = None if gross is None else gross - flow
            rows.append({"account": aid, "date": str(when), "value_sgd": str(total),
                         "gross_change_sgd": None if gross is None else str(gross),
                         "adjusted_change_sgd": None if adjusted is None else str(adjusted),
                         "external_flow_sgd": str(flow), "updated_at": datetime.now(timezone.utc)})
            previous[aid] = total
        when += timedelta(days=1)
    return rows


def refresh_portfolio_history():
    snapshot = store.read()
    today = datetime.now(ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Singapore"))).date()
    events = active_events(snapshot, today)
    if not events:
        store.replace_portfolio_history([])
        return 0
    start = min(datetime.fromisoformat(e["date"]).date() for e in events) - timedelta(days=7)
    instrument_rows = {}
    currencies = {"SGD"}
    for event in events:
        p = event["data"]
        currencies.update(cur for cur in (p.get("currency"), p.get("to_currency")) if cur)
        if event["kind"] in ("buy", "sell", "opening_holding", "split"):
            ins = instrument(p)
            instrument_rows[ins["id"]] = ins
            currencies.add(ins["currency"])
    quotes, rates = {}, {"SGD": {start: Decimal(1)}}
    for iid, ins in instrument_rows.items():
        try:
            quotes[iid] = fetch_price_history(ins, start, today)
            if not quotes[iid]:
                raise ValueError("Historical price response was empty")
        except Exception:
            logging.warning("Historical price refresh failed for %s", iid)
            raise
    for cur in currencies - {"SGD"}:
        try:
            rates[cur] = fetch_fx_history(cur, start, today)
            if not rates[cur]:
                raise ValueError("Historical FX response was empty")
        except Exception:
            logging.warning("Historical FX refresh failed for %s/SGD", cur)
            raise
    rows = build_portfolio_history(snapshot, quotes, rates, today)
    store.replace_portfolio_history(rows)
    return len(rows)


def refresh():
    snapshot = store.read()
    today = datetime.now(ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Singapore"))).date()
    cash, positions, instruments, *_ = replay(snapshot, today)
    currencies = {cur for (_, cur), amount in cash.items() if amount}
    quotes, rates, status = {}, {}, {}
    for iid in sorted({iid for (_, iid), qty in positions.items() if qty}):
        currencies.add(instruments[iid]["currency"])
        try:
            quotes[iid] = fetch_price(instruments[iid])
            status[iid] = {"ok": True, "checked_at": datetime.now(timezone.utc).isoformat()}
        except Exception:
            status[iid] = {"ok": False, "checked_at": datetime.now(timezone.utc).isoformat(),
                           "message": "Price refresh unavailable; retaining previous quote if any"}
            logging.warning("Price refresh failed for %s; cached data retained", iid)
    for cur in sorted(currencies - {"SGD"}):
        try:
            rates[cur] = fetch_fx(cur)
            status[cur + "/SGD"] = {"ok": True, "checked_at": datetime.now(timezone.utc).isoformat()}
        except Exception:
            status[cur + "/SGD"] = {"ok": False, "checked_at": datetime.now(timezone.utc).isoformat(),
                                    "message": "FX refresh unavailable; retaining previous rate if any"}
    with store.transaction() as s:
        s["prices"].update(quotes)
        s["fx"].update(rates)
        s["provider_status"].update(status)
        s["revision"] += 1


def run_due(last_attempt, interval, action, label, now=None):
    """Run one independently scheduled provider job and back off after failures."""
    now = now or time.time()
    if now - last_attempt < interval:
        return last_attempt
    try:
        count = action()
        logging.info("%s completed%s", label, " (%s rows)" % count if count is not None else "")
    except Exception:
        # Mark the attempt even on failure. This is especially important for
        # paid providers: a failure must not trigger a chargeable tight retry.
        logging.warning("%s failed; retaining previous data until the next scheduled attempt", label)
    return now


def main():
    from .catalog import refresh_us_catalog
    last_catalog_refresh = 0
    last_history_refresh = 0
    while True:
        last_catalog_refresh = run_due(last_catalog_refresh,
            int(os.environ.get("CATALOG_REFRESH_SECONDS", "86400")),
            refresh_us_catalog, "US catalogue refresh")
        try:
            refresh()
        except Exception:
            logging.warning("Market refresh failed; retrying on next cycle")
        last_history_refresh = run_due(last_history_refresh,
            int(os.environ.get("HISTORY_REFRESH_SECONDS", "21600")),
            refresh_portfolio_history, "Brokerage history refresh")
        time.sleep(max(60, int(os.environ.get("MARKET_POLL_SECONDS", "900"))))


if __name__ == "__main__":
    main()
