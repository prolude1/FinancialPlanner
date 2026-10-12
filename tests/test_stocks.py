from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine

from app.core import store
from app.core.stocks import StockProviderError, StockService, parse_security_id


class FakePrices:
    @staticmethod
    def provider_symbol(exchange, symbol):
        return symbol + {"LSE": ".L", "SGX": ".SI"}.get(exchange, "")


class FakeProvider:
    prices = FakePrices()

    def __init__(self):
        self.overview_calls = 0

    def overview(self, exchange, symbol):
        self.overview_calls += 1
        return {"security": {"security_id": f"{exchange}:{symbol}", "symbol": symbol,
            "exchange": exchange, "name": symbol, "asset_class": "equity", "currency": "USD",
            "provider_symbol": symbol}, "latest_price": {"session_date": "2026-09-22",
            "close": "101", "high": "102", "low": "99", "prior_close": "100",
            "change": "1", "change_percent": "1", "currency": "USD",
            "exchange_timezone": "America/New_York", "market_status": "completed",
            "data_timestamp": "2026-09-22", "retrieved_at": "2026-09-23T00:00:00+00:00"},
            "source": {"name": "fixture"}}

    def identity(self, exchange, symbol):
        return {"security_id": f"{exchange}:{symbol}", "symbol": symbol,
                "exchange": exchange, "name": symbol, "asset_class": "equity",
                "currency": "USD", "provider_symbol": symbol}


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "stocks.db"))
    monkeypatch.setattr(store, "engine", engine)
    store.migrate()
    yield
    engine.dispose()


def test_security_ids_are_exchange_qualified():
    assert parse_security_id("nasdaq:aapl") == ("NASDAQ", "AAPL")
    assert parse_security_id("sgx:d05") == ("SGX", "D05")
    with pytest.raises(StockProviderError, match="EXCHANGE:SYMBOL"):
        parse_security_id("AAPL")


def test_identity_returns_canonical_instrument_contract():
    result = StockService(FakeProvider(), retries=1).identity("nasdaq:aapl")
    assert result == {"security_id": "NASDAQ:AAPL", "symbol": "AAPL",
                      "exchange": "NASDAQ", "name": "AAPL", "asset_class": "equity",
                      "currency": "USD", "provider_symbol": "AAPL"}


def test_completed_price_fields_are_cached(isolated_store):
    provider = FakeProvider()
    service = StockService(provider, retries=1)
    first = service.overview("NASDAQ:AAPL")
    second = service.overview("NASDAQ:AAPL")
    assert provider.overview_calls == 1
    assert first["latest_price"]["prior_close"] == "100"
    assert first["latest_price"]["change_percent"] == "1"
    assert second["cache"]["stale"] is False


def test_expired_cache_is_served_stale_on_transient_failure(isolated_store):
    provider = FakeProvider()
    service = StockService(provider, retries=1)
    service.overview("NASDAQ:AAPL")
    cached = store.read_stock_cache("NASDAQ:AAPL", "overview")
    store.write_stock_cache("NASDAQ:AAPL", "overview", cached["data"],
                            datetime.now(timezone.utc) - timedelta(days=2),
                            datetime.now(timezone.utc) - timedelta(days=1))
    provider.overview = Mock(side_effect=StockProviderError("provider_unavailable", "offline"))
    result = service.overview("NASDAQ:AAPL")
    assert result["cache"]["stale"] is True
    assert result["cache"]["provider_error"] == "provider_unavailable"


def test_provider_failure_without_cache_is_structured(isolated_store):
    provider = FakeProvider()
    provider.overview = Mock(side_effect=StockProviderError("provider_unavailable", "offline"))
    with pytest.raises(StockProviderError) as issue:
        StockService(provider, retries=1).overview("NASDAQ:AAPL")
    assert issue.value.code == "provider_unavailable" and issue.value.retryable


def test_local_search_keeps_exchange_collisions_distinct(isolated_store):
    now = datetime.now(timezone.utc)
    store.cache_instrument({"exchange": "NASDAQ", "symbol": "ABC", "name": "US ABC",
        "asset_class": "equity", "currency": "USD", "native_exchange": "NASDAQ",
        "source": "fixture", "active": True, "updated_at": now})
    store.cache_instrument({"exchange": "SGX", "symbol": "ABC", "name": "SG ABC",
        "asset_class": "equity", "currency": "SGD", "native_exchange": "SGX",
        "source": "fixture", "active": True, "updated_at": now})
    result = StockService(FakeProvider(), retries=1).search("ABC")
    assert {item["security_id"] for item in result["results"]} == {"NASDAQ:ABC", "SGX:ABC"}


def test_quarterly_comparisons_match_dates_and_preserve_missing_values():
    from app.core.quarterly_results import QuarterlyResultsTimeline
    rows = [
        {"period_end": "2025-06-28", "revenue": "120", "profit_after_tax": "10", "free_cash_flow": None},
        {"period_end": "2025-03-29", "revenue": "100"},
        {"period_end": "2024-06-29", "revenue": "100", "profit_after_tax": "-5", "free_cash_flow": "20"},
    ]
    result = QuarterlyResultsTimeline().build(rows, 8)
    assert result[0]["year_over_year"]["revenue"]["percent"] == "20.00"
    assert result[0]["year_over_year"]["profit_after_tax"]["reason"] == "non_positive_base"
    assert result[0]["year_over_year"]["free_cash_flow"]["percent"] is None
    assert result[1]["comparison_period_end"] is None
    assert QuarterlyResultsTimeline.compare("1", "0")["percent"] is None


def test_quarterly_provider_handles_different_statement_dates(monkeypatch):
    import pandas as pd
    from app.core.stocks import YahooStockResearchProvider
    provider = YahooStockResearchProvider()
    monkeypatch.setattr(provider, "identity", lambda *_: {"security_id": "NASDAQ:AAPL", "asset_class": "equity"})
    ticker = Mock()
    ticker.get_income_stmt.return_value = pd.DataFrame({pd.Timestamp("2025-06-30"): {"TotalRevenue": 100}})
    ticker.get_cash_flow.return_value = pd.DataFrame({pd.Timestamp("2025-03-31"): {"FreeCashFlow": 20}})
    ticker.get_info.return_value = {"currency": "USD", "financialCurrency": "EUR"}
    monkeypatch.setattr(provider, "_ticker", lambda *_: ticker)
    result = provider.quarterly_results("NASDAQ", "AAPL", 8)
    assert result["currency"] == "EUR"
    assert result["quarters"][0]["free_cash_flow"] is None
    assert result["quarters"][1]["revenue"] is None
    assert result["coverage"] == {"requested_quarters": 8, "returned_quarters": 2, "complete": False}
    ticker.get_income_stmt.assert_called_once_with(freq="quarterly")


def test_etf_quarterly_results_do_not_fetch_statements(monkeypatch):
    from app.core.stocks import YahooStockResearchProvider
    provider = YahooStockResearchProvider()
    monkeypatch.setattr(provider, "identity", lambda *_: {"security_id": "LSE:VWRA", "asset_class": "etf"})
    ticker = Mock()
    monkeypatch.setattr(provider, "_ticker", ticker)
    result = provider.quarterly_results("LSE", "VWRA", 8)
    assert result["supported"] is False and result["quarters"] == []
    ticker.assert_not_called()


def test_quarterly_results_are_cached(isolated_store):
    provider = FakeProvider()
    provider.quarterly_results = Mock(return_value={"quarters": [], "currency": None})
    service = StockService(provider, retries=1)
    service.quarterly_results("NASDAQ:AAPL", 8)
    service.quarterly_results("NASDAQ:AAPL", 8)
    provider.quarterly_results.assert_called_once_with("NASDAQ", "AAPL", 8)
