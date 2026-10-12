from datetime import date, datetime, timezone
from decimal import Decimal
import uuid
import pytest
from app.core import worker
from app.core.worker import completed_session, resolve_trading_currency, provider_symbol
from app.core.worker import build_portfolio_deposit_history, build_portfolio_history, run_due
from app.core.catalog import parse_us_catalog
from app.core.domain import Invalid, apply, empty


def test_us_exchange_currency_is_deterministic_without_provider_lookup():
    assert resolve_trading_currency("NYSE", "VOO") == "USD"
    assert resolve_trading_currency("NASDAQ", "AAPL") == "USD"


def test_during_session_and_after_publication_buffer():
    # 21 September 2026 is a Monday, NYSE close 20:00 UTC.
    assert str(completed_session("NYSE", datetime(2026, 9, 21, 18, tzinfo=timezone.utc))) == "2026-09-18"
    assert str(completed_session("NYSE", datetime(2026, 9, 21, 20, 15, tzinfo=timezone.utc))) == "2026-09-18"
    assert str(completed_session("NYSE", datetime(2026, 9, 21, 21, tzinfo=timezone.utc))) == "2026-09-21"


def test_holiday_and_early_close():
    assert str(completed_session("NYSE", datetime(2026, 7, 3, 23, tzinfo=timezone.utc))) == "2026-07-02"
    assert str(completed_session("NYSE", datetime(2026, 11, 27, 19, tzinfo=timezone.utc))) == "2026-11-27"


def test_sgx_session_after_calendar_holiday_data_range():
    assert str(completed_session("SGX", datetime(2026, 9, 23, 8, tzinfo=timezone.utc))) == "2026-09-22"
    assert str(completed_session("SGX", datetime(2026, 9, 23, 10, tzinfo=timezone.utc))) == "2026-09-23"


def test_provider_symbols_cover_london_and_singapore():
    assert provider_symbol("LSE", "VWRA") == "VWRA.L"
    assert provider_symbol("SGX", "D05") == "D05.SI"


def test_official_us_catalog_parser_maps_non_nasdaq_bucket_and_etfs():
    nasdaq = "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\nAAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N\nQQQ|Invesco QQQ|G|N|N|100|Y|N\nFile Creation Time: 1|"
    other = "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\nVOO|Vanguard S&P 500 ETF|P|VOO|Y|100|N|VOO\nIBM|IBM Common Stock|N|IBM|N|100|N|IBM\nFile Creation Time: 1|"
    rows = parse_us_catalog(nasdaq, other)
    mapped = {(r["exchange"], r["symbol"]): r for r in rows}
    assert mapped["NASDAQ", "AAPL"]["currency"] == "USD"
    assert mapped["NASDAQ", "QQQ"]["asset_class"] == "etf"
    assert mapped["NYSE", "VOO"]["native_exchange"] == "NYSE Arca"
    assert mapped["NYSE", "VOO"]["asset_class"] == "etf"


def test_brokerage_history_separates_cash_flow_from_investment_movement():
    state = empty()
    def run(command, payload):
        return apply(state, command, payload, "test", uuid.uuid4().hex, date(2026, 1, 2))
    account = run("account_add", {"name": "Broker", "type": "brokerage", "currency": "USD"})["id"]
    run("opening_cash", {"account": account, "currency": "USD", "amount": "100", "date": "2026-01-01"})
    run("opening_holding", {"account": account, "exchange": "NASDAQ", "symbol": "AAPL",
        "asset_class": "equity", "currency": "USD", "quantity": "2", "price": "unknown", "date": "2026-01-01"})
    run("deposit", {"account": account, "currency": "USD", "amount": "10", "date": "2026-01-02"})
    rows = build_portfolio_history(state,
        {"NASDAQ:AAPL": {date(2026, 1, 1): Decimal("10"), date(2026, 1, 2): Decimal("12")}},
        {"USD": {date(2026, 1, 1): Decimal("1.3"), date(2026, 1, 2): Decimal("1.4")}},
        date(2026, 1, 2))
    assert rows[0]["value_sgd"] == "156.0"
    assert rows[1]["value_sgd"] == "187.6"
    assert rows[1]["gross_change_sgd"] == "31.6"
    assert rows[1]["external_flow_sgd"] == "14.0"
    assert rows[1]["adjusted_change_sgd"] == "17.6"
    deposits = build_portfolio_deposit_history(state,
        {"USD": {date(2026, 1, 2): Decimal("1.4")}}, date(2026, 1, 2))
    assert deposits == [{"account": account, "date": "2026-01-02", "amount_sgd": "14.0",
                         "missing_fx_count": 0}]


def test_portfolio_history_replays_events_once_and_preserves_missing_market_data_gaps(monkeypatch):
    state = empty()
    first, second = date(2026, 1, 1), date(2026, 1, 2)
    def run(command, payload, key):
        return apply(state, command, payload, "test", key, second)
    invested = run("account_add", {"name": "Invested", "type": "brokerage", "currency": "SGD"}, "a1")['id']
    cash_only = run("account_add", {"name": "Cash only", "type": "brokerage", "currency": "SGD"}, "a2")['id']
    run("opening_cash", {"account": invested, "currency": "SGD", "amount": "100", "date": str(first)}, "e1")
    run("opening_cash", {"account": invested, "currency": "USD", "amount": "20", "date": str(first)}, "e2")
    run("opening_holding", {"account": invested, "exchange": "NASDAQ", "symbol": "AAPL",
        "asset_class": "equity", "currency": "USD", "quantity": "2", "price": "unknown",
        "date": str(first)}, "e3")
    run("deposit", {"account": invested, "currency": "SGD", "amount": "10", "date": str(second)}, "e4")
    run("buy", {"account": invested, "exchange": "NASDAQ", "symbol": "AAPL",
        "asset_class": "equity", "currency": "USD", "quantity": "1", "price": "5",
        "date": str(second)}, "e5")
    run("opening_cash", {"account": cash_only, "currency": "SGD", "amount": "5", "date": str(first)}, "e6")

    calls = []
    replay_event = worker.apply_replay_event
    def count_replay(*args):
        calls.append(args[1]['id'])
        return replay_event(*args)
    monkeypatch.setattr(worker, "apply_replay_event", count_replay)
    rows = worker.build_portfolio_history(
        state,
        {"NASDAQ:AAPL": {second: Decimal("12")}},
        {"USD": {first: Decimal("1.3"), second: Decimal("1.4")}},
        second,
    )

    assert calls == [event["id"] for event in sorted(state["events"], key=lambda event: (event["date"], event["order"]))]
    assert [(row["account"], row["date"], row["value_sgd"], row["gross_change_sgd"],
             row["adjusted_change_sgd"], row["external_flow_sgd"]) for row in rows] == [
        (cash_only, str(first), "5", None, None, "5"),
        (invested, str(second), "181.4", None, None, "10"),
        (cash_only, str(second), "5", "0", "0", "0"),
    ]


def test_portfolio_history_still_validates_events_before_market_data_is_complete():
    state = empty()
    first, second = date(2026, 1, 1), date(2026, 1, 2)
    account = apply(state, "account_add", {"name": "Broker", "type": "brokerage"},
                    "test", "account", second)["id"]
    opening = apply(state, "opening_holding", {"account": account, "exchange": "NASDAQ",
        "symbol": "AAPL", "asset_class": "equity", "currency": "USD", "quantity": "1",
        "price": "unknown", "date": str(first)}, "test", "opening", second)
    state["events"].append({"id": "invalid-buy", "kind": "buy", "date": str(second),
        "order": opening["order"] + 1, "status": "active", "data": {
            "account": account, "exchange": "NASDAQ", "symbol": "AAPL", "asset_class": "equity",
            "currency": "USD", "quantity": "1", "price": "10", "date": str(second)}})

    with pytest.raises(Invalid, match="Insufficient cash or holdings at transaction invalid-buy"):
        worker.build_portfolio_history(state, {}, {}, second)


def test_deposit_history_is_independent_of_prices_and_excludes_non_deposits():
    state = empty()
    def run(command, payload):
        return apply(state, command, payload, "test", uuid.uuid4().hex, date(2026, 1, 2))
    account = run("account_add", {"name": "Broker", "type": "brokerage", "currency": "SGD"})["id"]
    run("opening_cash", {"account": account, "currency": "SGD", "amount": "100", "date": "2026-01-01"})
    run("deposit", {"account": account, "currency": "SGD", "amount": "5000", "date": "2026-01-01"})
    run("withdraw", {"account": account, "currency": "SGD", "amount": "20", "date": "2026-01-02"})
    deposits = build_portfolio_deposit_history(state, {}, date(2026, 1, 2))
    assert deposits == [{"account": account, "date": "2026-01-01", "amount_sgd": "5000",
                         "missing_fx_count": 0}]


def test_deposit_projection_keeps_known_contributions_and_counts_missing_fx():
    state = empty()
    def run(command, payload):
        return apply(state, command, payload, "test", uuid.uuid4().hex, date(2026, 1, 2))
    account = run("account_add", {"name": "Broker", "type": "brokerage", "currency": "SGD"})["id"]
    run("deposit", {"account": account, "currency": "SGD", "amount": "5", "date": "2026-01-01"})
    run("deposit", {"account": account, "currency": "USD", "amount": "10", "date": "2026-01-01"})
    deposits = build_portfolio_deposit_history(state, {}, date(2026, 1, 2))
    assert deposits == [{"account": account, "date": "2026-01-01", "amount_sgd": "5",
                         "missing_fx_count": 1}]


def test_provider_schedule_backs_off_after_failure():
    calls = []
    def failing():
        calls.append(True)
        raise RuntimeError("provider unavailable")
    attempted = run_due(0, 100, failing, "test provider", now=1000)
    assert attempted == 1000 and len(calls) == 1
    assert run_due(attempted, 100, failing, "test provider", now=1050) == attempted
    assert len(calls) == 1


@pytest.mark.parametrize('now, expected', [
    (datetime(2026, 9, 22, 21, tzinfo=timezone.utc), False),
    (datetime(2026, 9, 23, 20, tzinfo=timezone.utc), False),
    (datetime(2026, 9, 23, 20, 1, tzinfo=timezone.utc), True),
])
def test_price_warning_strict_48_hour_threshold(now, expected):
    from app.core.providers import price_needs_attention
    assert price_needs_attention('NASDAQ', '2026-09-21', now) is expected


def test_price_warning_respects_weekends_holidays_and_missing_sessions():
    from app.core.providers import price_needs_attention
    # Independence Day closure and weekend: Thursday remains the latest close.
    assert not price_needs_attention('NYSE', '2026-07-02', datetime(2026, 7, 5, 23, tzinfo=timezone.utc))
    # An older Wednesday quote still warrants attention during that closure.
    assert price_needs_attention('NYSE', '2026-07-01', datetime(2026, 7, 5, 23, tzinfo=timezone.utc))
    # SGX outside calendar coverage still exempts a normal weekend.
    assert not price_needs_attention('SGX', '2026-09-18', datetime(2026, 9, 20, 12, tzinfo=timezone.utc))
