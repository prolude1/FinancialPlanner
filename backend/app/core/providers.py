"""Market-data provider interfaces and construction.

Keep vendor-specific symbol, metadata, and quote handling out of the worker so
another provider can be added without changing portfolio orchestration.
"""
from abc import ABC, abstractmethod
from datetime import datetime, time as wall_time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo


class StockPriceProvider(ABC):
    """Contract implemented by stock and ETF price providers."""

    name: str

    @abstractmethod
    def resolve_instrument(self, exchange, symbol):
        """Validate a trading line and return its currency and asset class."""

    @abstractmethod
    def fetch_price(self, instrument, now=None):
        """Return the latest completed-session OHLC valuation."""

    @abstractmethod
    def fetch_price_history(self, instrument, start, end):
        """Return completed-session closing prices keyed by date."""


class YahooStockPriceProvider(StockPriceProvider):
    name = "yahoo"

    @staticmethod
    def provider_symbol(exchange, symbol):
        suffix = {"LSE": ".L", "SGX": ".SI"}.get(exchange, "")
        return symbol + suffix

    @staticmethod
    def normalize_currency(value):
        # Yahoo identifies penny-denominated London lines as GBp. The ledger
        # stores cash and prices in pounds, so scale these quotes by 0.01.
        return "GBP" if value == "GBp" else value

    def resolve_instrument(self, exchange, symbol):
        exchange, symbol = exchange.upper(), symbol.upper()
        if exchange not in ("NYSE", "NASDAQ", "LSE", "SGX"):
            raise ValueError("Unsupported exchange")
        import yfinance as yf
        ticker = yf.Ticker(self.provider_symbol(exchange, symbol))
        frame = ticker.history(period="5d", interval="1d", auto_adjust=False,
                               actions=False, raise_errors=True)
        if frame.empty:
            raise ValueError("Trading line was not found")
        currency = self.normalize_currency(ticker.history_metadata.get("currency"))
        if not currency or len(currency) != 3 or not currency.isalpha():
            raise ValueError("Provider did not return a valid trading currency")
        instrument_type = str(ticker.history_metadata.get("instrumentType", "")).upper()
        asset_class = {"EQUITY": "equity", "ETF": "etf"}.get(instrument_type)
        return {"currency": currency.upper(), "asset_class": asset_class,
                "provider_type": instrument_type or None,
                "name": ticker.history_metadata.get("shortName"),
                "native_exchange": ticker.history_metadata.get("exchangeName")}

    def fetch_price(self, instrument, now=None):
        import yfinance as yf

        now = now or datetime.now(timezone.utc)
        expected = completed_session(instrument["exchange"], now)
        ticker = yf.Ticker(self.provider_symbol(instrument["exchange"], instrument["symbol"]))
        frame = ticker.history(period="1mo", interval="1d", auto_adjust=False,
                               actions=False, raise_errors=True)
        frame = frame[[d.date() <= expected for d in frame.index]].dropna(subset=["Close", "High", "Low"])
        if frame.empty:
            raise ValueError("Provider has not published a completed daily bar")
        row = frame.iloc[-1]
        provider_currency = ticker.history_metadata.get("currency")
        scale = Decimal("0.01") if provider_currency == "GBp" else Decimal(1)
        currency = self.normalize_currency(provider_currency)
        if currency != instrument["currency"]:
            raise ValueError("Provider currency does not match the instrument")
        close, high, low = [Decimal(str(row[key])) * scale for key in ("Close", "High", "Low")]
        if not all(value.is_finite() and value > 0 for value in (close, high, low)) or not low <= close <= high:
            raise ValueError("Invalid provider OHLC data")
        actual = frame.index[-1].date()
        return {"date": str(actual), "close": str(close), "high": str(high), "low": str(low),
                "midpoint": str((high + low) / 2), "currency": currency,
                "source": "Yahoo Finance via yfinance", "retrieved_at": now.isoformat(),
                "expected_session": str(expected), "stale": actual < expected,
                "finality": "Completed session + 30-minute buffer; provider does not certify official finality"}

    def fetch_price_history(self, instrument, start, end):
        import yfinance as yf
        ticker = yf.Ticker(self.provider_symbol(instrument["exchange"], instrument["symbol"]))
        frame = ticker.history(start=str(start), end=str(end + timedelta(days=1)), interval="1d",
                               auto_adjust=False, actions=False, raise_errors=True)
        provider_currency = ticker.history_metadata.get("currency")
        scale = Decimal("0.01") if provider_currency == "GBp" else Decimal(1)
        if self.normalize_currency(provider_currency) != instrument["currency"]:
            raise ValueError("Historical provider currency does not match instrument")
        return {row_date.date(): Decimal(str(row["Close"])) * scale
                for row_date, row in frame.iterrows() if row.get("Close") == row.get("Close")}


class StockPriceProviderFactory:
    _providers = {"yahoo": YahooStockPriceProvider}

    @classmethod
    def create(cls, name="yahoo"):
        provider = cls._providers.get(str(name).strip().lower())
        if provider is None:
            choices = ", ".join(sorted(cls._providers))
            raise ValueError(f"Unknown stock price provider {name!r}; choose {choices}")
        return provider()


def price_needs_attention(exchange, quote_date, now=None):
    """Warn after 48 hours only when a newer completed session is expected."""
    import exchange_calendars as xcals
    now = now or datetime.now(timezone.utc)
    quoted_day = datetime.fromisoformat(quote_date).date()
    calendar = xcals.get_calendar({"LSE": "XLON", "SGX": "XSES"}.get(exchange, "XNYS"))
    session = calendar.schedule.loc[quote_date:quote_date]
    if not session.empty:
        closed_at = session.iloc[0]["close"].to_pydatetime()
    else:
        # Outside calendar coverage, retain local close times and weekend handling.
        zone, close = {"SGX": ("Asia/Singapore", wall_time(17)),
                       "LSE": ("Europe/London", wall_time(16, 30)),
                       "NYSE": ("America/New_York", wall_time(16)),
                       "NASDAQ": ("America/New_York", wall_time(16))}[exchange]
        closed_at = datetime.combine(quoted_day, close, ZoneInfo(zone))
    return now - closed_at > timedelta(hours=48) and quoted_day < completed_session(exchange, now)


def completed_session(exchange, now=None, grace_minutes=30):
    """Return the most recent exchange session whose close buffer has elapsed."""
    import exchange_calendars as xcals
    import pandas as pd
    now = now or datetime.now(timezone.utc)
    calendar = xcals.get_calendar({"LSE": "XLON", "SGX": "XSES"}.get(exchange, "XNYS"))
    schedule = calendar.schedule.loc[str((now - timedelta(days=15)).date()):str(now.date())]
    completed = schedule[schedule["close"] + pd.Timedelta(minutes=grace_minutes) <= pd.Timestamp(now)]
    if not completed.empty:
        return completed.index[-1].date()

    # Some calendars ship exchange-specific holiday data only through a fixed
    # year (XSES currently ends in 2025). Yahoo's returned bars still establish
    # whether a holiday traded; this fallback only supplies an upper date bound.
    zones_and_closes = {
        "SGX": ("Asia/Singapore", wall_time(17, 0)),
        "LSE": ("Europe/London", wall_time(16, 30)),
        "NYSE": ("America/New_York", wall_time(16, 0)),
        "NASDAQ": ("America/New_York", wall_time(16, 0)),
    }
    zone_name, market_close = zones_and_closes[exchange]
    local_now = now.astimezone(ZoneInfo(zone_name))
    candidate = local_now.date()
    close_with_buffer = datetime.combine(candidate, market_close, ZoneInfo(zone_name)) + timedelta(minutes=grace_minutes)
    if local_now < close_with_buffer:
        candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate
