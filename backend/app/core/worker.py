"""Free daily quote adapters. Provider failures never erase cached valuations."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from bisect import bisect_right
import logging
import os
import time
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx
from . import store
from .domain import (replay, active_events, instrument, decimal,
                     new_replay_state, apply_replay_event)
from .providers import StockPriceProviderFactory, completed_session
from .recurring import post_due as post_due_recurring


stock_price_provider = StockPriceProviderFactory.create(
    os.environ.get("STOCK_PRICE_PROVIDER", "yahoo"))
worker_logger = logging.getLogger("financial_planner.worker")


def log_worker_failure(logger, job, exc, tenant_index=None, legacy=False,
                       elapsed_seconds=None, retry_in=None):
    """Log actionable traceback locations without exception payloads or secrets."""
    context = f"job={job}"
    if tenant_index is not None:
        context += f" scope=tenant tenant_index={tenant_index}"
    elif legacy:
        context += " scope=legacy"
    if elapsed_seconds is not None:
        context += f" elapsed_seconds={elapsed_seconds:.2f}"
    if retry_in is not None:
        context += f" retry_in={retry_in}"
    frames = "\n".join(
        f"  {Path(frame.filename).name}:{frame.lineno} in {frame.name}"
        for frame in traceback.extract_tb(exc.__traceback__)
    )
    logger.error("Worker failure (%s): %s%s", context, type(exc).__name__,
                 f"\n{frames}" if frames else "")


class LedgerBatchRefresher:
    """Run one refresh operation against each tenant and then legacy data.

    The caller supplies the single-ledger operation because market values and
    portfolio history have different retry policies. Ledger enumeration and
    the claimed-legacy gate stay consistent between both scheduled jobs.
    """

    def __init__(self, repository=store, logger=worker_logger):
        self._repository = repository
        self._logger = logger

    def run(
        self,
        refresh_one,
        *,
        tenant_errors_are_isolated=False,
        sum_results=False,
        job_name="ledger-refresh",
    ):
        results = []
        failed = 0
        started = time.monotonic()
        for index, principal in enumerate(self._repository.tenant_principals(), start=1):
            try:
                results.append(refresh_one(principal))
            except Exception as exc:
                if not tenant_errors_are_isolated:
                    raise
                failed += 1
                log_worker_failure(self._logger, job_name, exc, tenant_index=index,
                                   elapsed_seconds=time.monotonic() - started, retry_in="next poll")

        try:
            self._repository.assert_legacy_unclaimed()
        except PermissionError:
            if failed:
                self._logger.info("Worker batch completed job=%s tenants_failed=%s partial=true",
                                  job_name, failed)
            return sum(results) if sum_results else None

        try:
            results.append(refresh_one())
        except Exception as exc:
            if not tenant_errors_are_isolated:
                raise
            failed += 1
            log_worker_failure(self._logger, job_name, exc, legacy=True,
                               elapsed_seconds=time.monotonic() - started, retry_in="next poll")
        self._logger.info("Worker batch completed job=%s tenants_failed=%s partial=%s",
                          job_name, failed, str(failed > 0).lower())
        return sum(results) if sum_results else None


ledger_batch_refresher = LedgerBatchRefresher()


class RecurringTransactionWorker:
    """Post tenant schedules atomically; failed tenants retry on a later poll."""

    def __init__(self, repository=store, logger=worker_logger):
        self._repository = repository
        self._logger = logger

    def run(self, now=None):
        today = (now or datetime.now(ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Singapore")))).date()
        total = 0
        for index, principal in enumerate(self._repository.tenant_principals(), start=1):
            try:
                with self._repository.tenant_transaction(principal) as state:
                    total += post_due_recurring(state, today)
            except Exception as exc:
                log_worker_failure(self._logger, "recurring-ledger-posting", exc,
                                   tenant_index=index, retry_in="next poll")
        return total


recurring_transaction_worker = RecurringTransactionWorker()


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


class _DateSeriesIndex:
    """Sorted market observations with logarithmic as-of lookup."""

    def __init__(self, series):
        self._dates = sorted(series)
        self._values = [series[when] for when in self._dates]

    def at_or_before(self, when):
        index = bisect_right(self._dates, when) - 1
        return self._values[index] if index >= 0 else None


_EMPTY_DATE_SERIES = _DateSeriesIndex({})


def _portfolio_external_flow(events, account_id, when, quote_indexes, rate_indexes):
    """Convert this date's external flows and report whether every input is known."""
    flow = Decimal(0)
    complete = True
    for event in events:
        payload, kind = event["data"], event["kind"]
        if payload.get("account") == account_id and kind in (
                "opening_cash", "deposit", "withdraw", "loan_disbursement"):
            amount = decimal(payload["amount"])
            if kind == "withdraw":
                amount = -amount
            currency = payload.get("currency", "SGD")
            rate = Decimal(1) if currency == "SGD" else rate_indexes.get(currency, _EMPTY_DATE_SERIES).at_or_before(when)
            if rate is not None:
                flow += amount * rate
            else:
                complete = False
        if kind == "transfer":
            if payload.get("account") == account_id:
                currency = payload["currency"]
                rate = Decimal(1) if currency == "SGD" else rate_indexes.get(currency, _EMPTY_DATE_SERIES).at_or_before(when)
                if rate is not None:
                    flow -= decimal(payload["amount"]) * rate
                else:
                    complete = False
            if payload.get("destination") == account_id:
                currency = payload["to_currency"]
                rate = Decimal(1) if currency == "SGD" else rate_indexes.get(currency, _EMPTY_DATE_SERIES).at_or_before(when)
                if rate is not None:
                    flow += decimal(payload["received"]) * rate
                else:
                    complete = False
        if kind == "opening_holding" and payload.get("account") == account_id:
            iid = instrument(payload)["id"]
            close = quote_indexes.get(iid, _EMPTY_DATE_SERIES).at_or_before(when)
            currency = payload["currency"]
            rate = Decimal(1) if currency == "SGD" else rate_indexes.get(currency, _EMPTY_DATE_SERIES).at_or_before(when)
            if close is not None and rate is not None:
                flow += decimal(payload["quantity"]) * close * rate
            else:
                complete = False
        if kind == "repayment":
            flow -= sum((decimal(item["amount"]) for item in payload["allocations"]
                         if item["account"] == account_id), Decimal(0))
        if kind == "credit_payment" and payload.get("funding_account") == account_id:
            flow -= decimal(payload["amount"])
    return flow, complete


def build_portfolio_history(snapshot, quotes, rates, today):
    """Derive daily SGD values in one chronological ledger replay."""
    events = active_events(snapshot, today)
    if not events:
        return []
    first = min(datetime.fromisoformat(e["date"]).date() for e in events)
    events_by_date = {}
    for event in events:
        events_by_date.setdefault(datetime.fromisoformat(event["date"]).date(), []).append(event)
    quote_indexes = {iid: _DateSeriesIndex(series) for iid, series in quotes.items()}
    rate_indexes = {currency: _DateSeriesIndex(series) for currency, series in rates.items()}
    accounts = [a for a in snapshot["accounts"].values() if a["type"] == "brokerage"]
    previous = {}
    accumulated_flow = {}
    accumulated_flow_complete = {}
    rows = []
    state = new_replay_state()
    when = first
    while when <= today:
        daily_events = events_by_date.get(when, [])
        for event in daily_events:
            apply_replay_event(snapshot, event, state)
        cash, positions, instruments, *_ = state
        for account_row in accounts:
            aid, total, complete = account_row["id"], Decimal(0), True
            flow, flow_complete = _portfolio_external_flow(
                daily_events, aid, when, quote_indexes, rate_indexes)
            accumulated_flow[aid] = accumulated_flow.get(aid, Decimal(0)) + flow
            accumulated_flow_complete[aid] = accumulated_flow_complete.get(aid, True) and flow_complete
            for (cash_account, cur), amount in cash.items():
                if cash_account != aid or not amount:
                    continue
                rate = Decimal(1) if cur == "SGD" else rate_indexes.get(cur, _EMPTY_DATE_SERIES).at_or_before(when)
                if rate is None:
                    complete = False
                else:
                    total += amount * rate
            for (position_account, iid), quantity in positions.items():
                if position_account != aid or not quantity:
                    continue
                close = quote_indexes.get(iid, _EMPTY_DATE_SERIES).at_or_before(when)
                cur = instruments[iid]["currency"]
                rate = Decimal(1) if cur == "SGD" else rate_indexes.get(cur, _EMPTY_DATE_SERIES).at_or_before(when)
                if close is None or rate is None:
                    complete = False
                else:
                    total += quantity * close * rate
            if not complete:
                continue
            gross = None if aid not in previous else total - previous[aid]
            flow_known = accumulated_flow_complete[aid]
            adjusted = None if gross is None or not flow_known else gross - accumulated_flow[aid]
            rows.append({"account": aid, "date": str(when), "value_sgd": str(total),
                         "gross_change_sgd": None if gross is None else str(gross),
                         "adjusted_change_sgd": None if adjusted is None else str(adjusted),
                         "external_flow_sgd": str(accumulated_flow[aid]) if flow_known else None,
                         "updated_at": datetime.now(timezone.utc)})
            previous[aid] = total
            accumulated_flow[aid] = Decimal(0)
            accumulated_flow_complete[aid] = True
        when += timedelta(days=1)
    return rows


def build_portfolio_deposit_history(snapshot, rates, today, stale_rate_fetches=None):
    """Convert active brokerage deposits independently of daily price coverage."""
    accounts = {account_id for account_id, account in snapshot["accounts"].items()
                if account["type"] == "brokerage" and not account.get("archived")}
    deposits = {}
    for event in active_events(snapshot, today):
        payload = event["data"]
        account_id = payload.get("account")
        if event["kind"] != "deposit" or account_id not in accounts:
            continue
        key = (account_id, event["date"])
        deposit = deposits.setdefault(key, {"account": account_id, "date": event["date"],
            "amount": Decimal(0), "missing_fx_count": 0, "stale_fx_count": 0,
            "stale_fx_last_success_at": None})
        currency = payload.get("currency", "SGD")
        rate_date = None if currency == "SGD" else _latest_rate_date(
            rates.get(currency, {}), date.fromisoformat(event["date"]))
        rate = Decimal(1) if currency == "SGD" else (
            rates.get(currency, {}).get(rate_date) if rate_date is not None else None)
        if rate is None:
            deposit["missing_fx_count"] += 1
        else:
            deposit["amount"] += decimal(payload["amount"]) * rate
            fetched_at = (stale_rate_fetches or {}).get((currency, rate_date))
            if fetched_at is not None:
                deposit["stale_fx_count"] += 1
                previous = deposit["stale_fx_last_success_at"]
                if previous is None or fetched_at < previous[0]:
                    deposit["stale_fx_last_success_at"] = (
                        fetched_at, _iso_timestamp_from_ns(fetched_at))

    return [{"account": deposit["account"], "date": deposit["date"],
             "amount_sgd": str(deposit["amount"]),
             "missing_fx_count": deposit["missing_fx_count"],
             "stale_fx_count": deposit["stale_fx_count"],
             "stale_fx_last_success_at": (deposit["stale_fx_last_success_at"][1]
                                          if deposit["stale_fx_last_success_at"] else None)}
            for deposit in sorted(deposits.values(), key=lambda item: (item["account"], item["date"]))]


def _latest_rate_date(series, when):
    available = [date_key for date_key in series if date_key <= when]
    return max(available) if available else None


def _iso_timestamp_from_ns(value):
    seconds, nanoseconds = divmod(value, 1_000_000_000)
    stamp = datetime.fromtimestamp(seconds, timezone.utc).replace(
        microsecond=nanoseconds // 1000)
    return stamp.isoformat(timespec="microseconds").replace("+00:00", "Z")


def refresh_portfolio_history(principal=None):
    snapshot = store.read() if principal is None else store.read_for(principal)
    today = datetime.now(ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Singapore"))).date()
    events = active_events(snapshot, today)
    if not events:
        store.replace_portfolio_history([], principal=principal)
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
        except Exception as exc:
            log_worker_failure(worker_logger, "historical-price-provider", exc)
            raise
    for cur in currencies - {"SGD"}:
        try:
            rates[cur] = fetch_fx_history(cur, start, today)
            if not rates[cur]:
                raise ValueError("Historical FX response was empty")
        except Exception as exc:
            log_worker_failure(worker_logger, "historical-fx-provider", exc)
            raise
    rows = build_portfolio_history(snapshot, quotes, rates, today)
    store.replace_portfolio_history(rows, principal=principal)
    return len(rows)


def refresh_all_portfolio_history():
    """Refresh each financial history independently; never combine tenant events."""
    return ledger_batch_refresher.run(refresh_portfolio_history, sum_results=True,
        tenant_errors_are_isolated=True, job_name="brokerage-valuation-history")


def refresh_portfolio_deposit_history(principal=None):
    """Refresh deposits using FX history only, independent of price providers."""
    snapshot = store.read() if principal is None else store.read_for(principal)
    today = datetime.now(ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Singapore"))).date()
    brokerage_accounts = {account_id for account_id, account in snapshot["accounts"].items()
                          if account["type"] == "brokerage" and not account.get("archived")}
    events = [event for event in active_events(snapshot, today)
              if event["kind"] == "deposit" and event["data"].get("account") in brokerage_accounts]
    if not events:
        store.replace_portfolio_deposit_history([], principal=principal)
        return 0
    start = min(date.fromisoformat(event["date"]) for event in events) - timedelta(days=7)
    currencies = {event["data"].get("currency", "SGD") for event in events} - {"SGD"}
    fetch_started_at = time.time_ns()
    for currency in currencies:
        try:
            fetched = fetch_fx_history(currency, start, today)
        except Exception as exc:
            log_worker_failure(worker_logger, "deposit-fx-provider", exc)
            continue
        fetched_at = time.time_ns()
        fresh_rows = []
        for raw_date, raw_rate in (fetched or {}).items():
            try:
                rate_date = raw_date.date() if isinstance(raw_date, datetime) else (
                    raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date)))
                rate = Decimal(str(raw_rate))
            except (TypeError, ValueError, ArithmeticError):
                continue
            if rate_date < start or rate_date > today or not rate.is_finite() or rate <= 0:
                continue
            fresh_rows.append({"currency": currency, "date": str(rate_date),
                               "rate": str(rate), "fetched_at": fetched_at})
        if fresh_rows:
            store.upsert_portfolio_deposit_fx_rates(fresh_rows, principal=principal)
        else:
            worker_logger.warning("Historical FX response had no usable rates; cached rates retained")

    cached = store.read_portfolio_deposit_fx_rates(
        principal, currencies=currencies, start_date=start)
    rates, stale_rate_fetches = {"SGD": {}}, {}
    for currency, observations in cached.items():
        rates[currency] = {}
        for date_text, observation in observations.items():
            rate_date = date.fromisoformat(date_text)
            rates[currency][rate_date] = Decimal(observation["rate"])
            if observation["fetched_at"] < fetch_started_at:
                stale_rate_fetches[(currency, rate_date)] = observation["fetched_at"]
    rows = build_portfolio_deposit_history(snapshot, rates, today, stale_rate_fetches)
    store.replace_portfolio_deposit_history(rows, principal=principal)
    missing_fx_count = sum(row["missing_fx_count"] for row in rows)
    worker_logger.info("Deposit projection refreshed rows=%s missing_fx_count=%s partial=%s",
                       len(rows), missing_fx_count, str(missing_fx_count > 0).lower())
    return len(rows)


def refresh_all_portfolio_deposit_history():
    """Refresh deposit projections independently for each tenant and legacy data."""
    return ledger_batch_refresher.run(
        refresh_portfolio_deposit_history,
        tenant_errors_are_isolated=True,
        sum_results=True,
        job_name="brokerage-deposit-history",
    )


def refresh(principal=None):
    snapshot = store.read() if principal is None else store.read_for(principal)
    today = datetime.now(ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Singapore"))).date()
    cash, positions, instruments, *_ = replay(snapshot, today)
    currencies = {cur for (_, cur), amount in cash.items() if amount}
    quotes, rates, status = {}, {}, {}
    for iid in sorted({iid for (_, iid), qty in positions.items() if qty}):
        currencies.add(instruments[iid]["currency"])
        try:
            quotes[iid] = fetch_price(instruments[iid])
            status[iid] = {"ok": True, "checked_at": datetime.now(timezone.utc).isoformat()}
        except Exception as exc:
            status[iid] = {"ok": False, "checked_at": datetime.now(timezone.utc).isoformat(),
                           "message": "Price refresh unavailable; retaining previous quote if any"}
            log_worker_failure(worker_logger, "spot-price-provider", exc)
    for cur in sorted(currencies - {"SGD"}):
        try:
            rates[cur] = fetch_fx(cur)
            status[cur + "/SGD"] = {"ok": True, "checked_at": datetime.now(timezone.utc).isoformat()}
        except Exception as exc:
            status[cur + "/SGD"] = {"ok": False, "checked_at": datetime.now(timezone.utc).isoformat(),
                                    "message": "FX refresh unavailable; retaining previous rate if any"}
            log_worker_failure(worker_logger, "spot-fx-provider", exc)
    transaction = store.transaction() if principal is None else store.tenant_transaction(principal)
    with transaction as s:
        s["prices"].update(quotes)
        s["fx"].update(rates)
        s["provider_status"].update(status)
        s["revision"] += 1


def refresh_all_ledgers():
    """Refresh caches/history per ledger; shared market providers are read-only inputs."""
    ledger_batch_refresher.run(
        refresh,
        tenant_errors_are_isolated=True,
        job_name="market-refresh",
    )


def run_due(last_attempt, interval, action, label, now=None):
    """Run one independently scheduled provider job and back off after failures."""
    now = now or time.time()
    if now - last_attempt < interval:
        return last_attempt
    started = time.monotonic()
    try:
        count = action()
        worker_logger.info("Worker job completed job=%s elapsed_seconds=%.2f%s", label,
                           time.monotonic() - started, " rows=%s" % count if count is not None else "")
    except Exception as exc:
        # Mark the attempt even on failure. This is especially important for
        # paid providers: a failure must not trigger a chargeable tight retry.
        log_worker_failure(worker_logger, label, exc, elapsed_seconds=time.monotonic() - started,
                           retry_in=f"{interval}s")
    return now


def main():
    from .catalog import refresh_us_catalog
    logging.basicConfig(level="WARNING")
    logging.getLogger().setLevel(logging.WARNING)
    for logger_name in ("httpx", "httpcore", "yfinance", "urllib3", "requests"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
    worker_logger.setLevel(os.environ.get("WORKER_LOG_LEVEL", "INFO").upper())
    last_catalog_refresh = 0
    last_history_refresh = 0
    last_deposit_history_refresh = 0
    while True:
        last_catalog_refresh = run_due(last_catalog_refresh,
            int(os.environ.get("CATALOG_REFRESH_SECONDS", "86400")),
            refresh_us_catalog, "US catalogue refresh")
        try:
            refresh_all_ledgers()
        except Exception as exc:
            log_worker_failure(worker_logger, "market-refresh", exc)
        recurring_transaction_worker.run()
        last_history_refresh = run_due(last_history_refresh,
            int(os.environ.get("HISTORY_REFRESH_SECONDS", "21600")),
            refresh_all_portfolio_history, "Brokerage history refresh")
        last_deposit_history_refresh = run_due(last_deposit_history_refresh,
            int(os.environ.get("DEPOSIT_HISTORY_REFRESH_SECONDS", "0")),
            refresh_all_portfolio_deposit_history, "Brokerage deposit history refresh")
        time.sleep(max(60, int(os.environ.get("MARKET_POLL_SECONDS", "900"))))


if __name__ == "__main__":
    main()
