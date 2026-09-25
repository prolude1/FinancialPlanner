from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine

from app import store
from app.stocks import StockProviderError, StockService, parse_security_id


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
