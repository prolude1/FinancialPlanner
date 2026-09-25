import pytest

from app.core.providers import StockPriceProvider, StockPriceProviderFactory, YahooStockPriceProvider


def test_factory_builds_configured_stock_price_provider():
    provider = StockPriceProviderFactory.create("YAHOO")
    assert isinstance(provider, StockPriceProvider)
    assert isinstance(provider, YahooStockPriceProvider)


def test_factory_rejects_unknown_stock_price_provider():
    with pytest.raises(ValueError, match="Unknown stock price provider"):
        StockPriceProviderFactory.create("missing")


def test_yahoo_exchange_symbols_and_pence_currency():
    provider = YahooStockPriceProvider()
    assert provider.provider_symbol("SGX", "D05") == "D05.SI"
    assert provider.provider_symbol("LSE", "VWRA") == "VWRA.L"
    assert provider.provider_symbol("NASDAQ", "AAPL") == "AAPL"
    assert provider.normalize_currency("GBp") == "GBP"
