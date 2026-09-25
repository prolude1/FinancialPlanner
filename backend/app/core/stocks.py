"""Stock research queries with completed-session filtering and durable cache fallback."""
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math
import threading
import time

from . import store
from .providers import YahooStockPriceProvider, completed_session


EXCHANGE_TIMEZONES = {"NASDAQ": "America/New_York", "NYSE": "America/New_York",
                      "LSE": "Europe/London", "SGX": "Asia/Singapore"}
RANGES = {"1y": "1y", "3y": "3y", "5y": "5y", "max": "max"}
SOURCE = {"name": "Yahoo Finance via yfinance", "type": "aggregated market data",
          "url": "https://finance.yahoo.com/"}


class StockProviderError(RuntimeError):
    def __init__(self, code, message, retryable=True):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


def parse_security_id(value):
    parts = str(value).upper().split(":", 1)
    if len(parts) != 2 or parts[0] not in EXCHANGE_TIMEZONES or not parts[1]:
        raise StockProviderError("invalid_security_id", "Use EXCHANGE:SYMBOL", False)
    exchange, symbol = parts
    if any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.^-" for character in symbol) or len(symbol) > 20:
        raise StockProviderError("invalid_security_id", "Security symbol is invalid", False)
    return exchange, symbol


def _number(value):
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except Exception:
        return None
    return format(result, "f") if result.is_finite() else None


class RequestGate:
    """Process-local provider limiter; the durable cache avoids most calls."""
    def __init__(self, limit=30, window=60):
        self.limit, self.window, self.calls, self.lock = limit, window, deque(), threading.Lock()

    def acquire(self):
        with self.lock:
            now = time.monotonic()
            while self.calls and self.calls[0] <= now - self.window:
                self.calls.popleft()
            if len(self.calls) >= self.limit:
                raise StockProviderError("provider_rate_limited", "Market-data request limit reached; retry shortly")
            self.calls.append(now)


class YahooStockResearchProvider:
    def __init__(self):
        self.prices = YahooStockPriceProvider()
        self.gate = RequestGate()

    def _ticker(self, exchange, symbol):
        import yfinance as yf
        self.gate.acquire()
        return yf.Ticker(self.prices.provider_symbol(exchange, symbol))

    @staticmethod
    def _exchange(row):
        raw = str(row.get("exchange") or row.get("exchDisp") or "").upper()
        if raw in ("NMS", "NGM", "NCM", "NASDAQ"):
            return "NASDAQ"
        if raw in ("NYQ", "ASE", "PCX", "NYSE", "NYSEARCA"):
            return "NYSE"
        if raw in ("LSE", "LONDON"):
            return "LSE"
        if raw in ("SES", "SGX", "SINGAPORE"):
            return "SGX"
        return None

    def search(self, query, exchange=None):
        import yfinance as yf
        self.gate.acquire()
        try:
            rows = yf.Search(query, max_results=12, news_count=0, enable_fuzzy_query=False).quotes
        except Exception as exc:
            raise StockProviderError("provider_unavailable", "Security search is temporarily unavailable") from exc
        results = []
        for row in rows:
            mapped = self._exchange(row)
            quote_type = str(row.get("quoteType", "")).upper()
            if not mapped or (exchange and mapped != exchange) or quote_type not in ("EQUITY", "ETF"):
                continue
            provider_symbol = str(row.get("symbol", "")).upper()
            suffix = {"LSE": ".L", "SGX": ".SI"}.get(mapped, "")
            symbol = provider_symbol[:-len(suffix)] if suffix and provider_symbol.endswith(suffix) else provider_symbol
            results.append({"security_id": f"{mapped}:{symbol}", "symbol": symbol, "exchange": mapped,
                "name": row.get("longname") or row.get("shortname") or symbol,
                "asset_class": quote_type.lower(), "currency": row.get("currency"),
                "provider_symbol": provider_symbol})
        return results[:10]

    def identity(self, exchange, symbol):
        known = store.find_instrument(exchange, symbol)
        if known and known.get("currency") and known.get("asset_class"):
            details = known
        else:
            try:
                details = self.prices.resolve_instrument(exchange, symbol)
            except Exception as exc:
                raise StockProviderError("security_not_found", "Security could not be resolved", False) from exc
        return {"security_id": f"{exchange}:{symbol}", "symbol": symbol, "exchange": exchange,
                "name": details.get("name") or symbol, "asset_class": details.get("asset_class"),
                "currency": details.get("currency"),
                "provider_symbol": self.prices.provider_symbol(exchange, symbol)}

    def overview(self, exchange, symbol):
        security = self.identity(exchange, symbol)
        try:
            quote = self.prices.fetch_price(security)
            ticker = self._ticker(exchange, symbol)
            frame = ticker.history(period="1mo", interval="1d", auto_adjust=False,
                                   actions=False, raise_errors=True)
            expected = completed_session(exchange)
            frame = frame[[stamp.date() <= expected for stamp in frame.index]].dropna(subset=["Close"])
            scale = Decimal("0.01") if ticker.history_metadata.get("currency") == "GBp" else Decimal(1)
            previous = _number(Decimal(str(frame.iloc[-2]["Close"])) * scale) if len(frame) >= 2 else None
        except Exception as exc:
            raise StockProviderError("provider_unavailable", "Completed-session price is unavailable") from exc
        current = Decimal(quote["close"])
        prior = Decimal(previous) if previous is not None else None
        change = current - prior if prior is not None else None
        change_percent = change / prior * 100 if prior not in (None, Decimal(0)) else None
        return {"security": security, "latest_price": {
            "session_date": quote["date"], "close": quote["close"], "high": quote["high"],
            "low": quote["low"], "prior_close": _number(prior), "change": _number(change),
            "change_percent": _number(change_percent), "currency": quote["currency"],
            "exchange_timezone": EXCHANGE_TIMEZONES[exchange], "market_status": "completed",
            "data_timestamp": quote["date"], "retrieved_at": quote["retrieved_at"]}, "source": SOURCE}

    def candles(self, exchange, symbol, range_name):
        security = self.identity(exchange, symbol)
        ticker = self._ticker(exchange, symbol)
        try:
            frame = ticker.history(period=RANGES[range_name], interval="1d", auto_adjust=False,
                                   actions=False, raise_errors=True)
        except Exception as exc:
            raise StockProviderError("provider_unavailable", "Daily candles are temporarily unavailable") from exc
        expected = completed_session(exchange)
        rows = []
        for stamp, row in frame.iterrows():
            when = stamp.date()
            values = [_number(row.get(key)) for key in ("Open", "High", "Low", "Close")]
            if when > expected or any(value is None for value in values):
                continue
            volume = row.get("Volume")
            rows.append({"date": str(when), "open": values[0], "high": values[1], "low": values[2],
                         "close": values[3], "volume": None if volume is None or math.isnan(float(volume)) else int(volume)})
        return {"security_id": security["security_id"], "range": range_name, "interval": "1d",
                "currency": security["currency"], "candles": rows,
                "as_of": rows[-1]["date"] if rows else None, "source": SOURCE}

    @staticmethod
    def _statement_value(frame, column, names):
        for name in names:
            if name in frame.index:
                return _number(frame.at[name, column])
        return None

    def _financial_rows(self, exchange, symbol, frequency, limit):
        ticker = self._ticker(exchange, symbol)
        try:
            income = ticker.get_income_stmt(freq=frequency)
            cash = ticker.get_cash_flow(freq=frequency)
            metadata = ticker.history_metadata
        except Exception as exc:
            raise StockProviderError("provider_unavailable", "Financial statements are temporarily unavailable") from exc
        columns = sorted(set(income.columns).union(cash.columns), reverse=True)[:limit]
        rows = []
        for column in columns:
            revenue = self._statement_value(income, column, ("TotalRevenue", "OperatingRevenue"))
            profit = self._statement_value(income, column, ("NetIncome", "NetIncomeCommonStockholders"))
            ebitda = self._statement_value(income, column, ("EBITDA", "NormalizedEBITDA"))
            ebita = self._statement_value(income, column, ("EBITA",))
            fcf = self._statement_value(cash, column, ("FreeCashFlow",))
            rows.append({"period_end": str(column.date()), "report_date": None,
                         "revenue": revenue, "free_cash_flow": fcf,
                         "profit_after_tax": profit, "ebitda": ebitda, "ebita": ebita})
        currency = self.prices.normalize_currency(metadata.get("currency"))
        return rows, currency

    def financials(self, exchange, symbol, years):
        security = self.identity(exchange, symbol)
        rows, currency = self._financial_rows(exchange, symbol, "yearly", years)
        for row in rows:
            row["fiscal_year"] = int(row["period_end"][:4])
        return {"security_id": security["security_id"], "currency": currency,
                "fiscal_years": rows,
                "coverage": {"requested_years": years, "returned_years": len(rows),
                             "complete": len(rows) == years},
                "metric_definitions": {"revenue": "Provider TotalRevenue or OperatingRevenue",
                    "free_cash_flow": "Provider FreeCashFlow; not derived",
                    "profit_after_tax": "Provider NetIncome or NetIncomeCommonStockholders",
                    "ebitda": "Provider EBITDA or NormalizedEBITDA; not derived",
                    "ebita": "Provider EBITA only; null when unavailable"}, "source": SOURCE}

    def latest_earnings(self, exchange, symbol):
        security = self.identity(exchange, symbol)
        rows, currency = self._financial_rows(exchange, symbol, "quarterly", 1)
        if not rows:
            rows, currency = self._financial_rows(exchange, symbol, "yearly", 1)
            period_type = "annual"
        else:
            period_type = "quarterly"
        row = rows[0] if rows else {key: None for key in
            ("period_end", "report_date", "revenue", "free_cash_flow", "profit_after_tax", "ebitda", "ebita")}
        return {"security_id": security["security_id"], "period_type": period_type,
                "currency": currency, **row, "source": SOURCE}


class StockService:
    TTLS = {"search": timedelta(hours=24), "overview": timedelta(minutes=30),
            "candles": timedelta(hours=6), "financials": timedelta(hours=24),
            "earnings": timedelta(hours=6)}

    def __init__(self, provider=None, retries=2):
        self.provider = provider or YahooStockResearchProvider()
        self.retries = retries

    def _load(self, cache_id, kind, loader):
        now = datetime.now(timezone.utc)
        cached = store.read_stock_cache(cache_id, kind)
        if cached:
            for field in ("fetched_at", "expires_at"):
                if cached[field].tzinfo is None:
                    cached[field] = cached[field].replace(tzinfo=timezone.utc)
        if cached and cached["expires_at"] > now:
            return {**cached["data"], "cache": {"stale": False,
                "fetched_at": cached["fetched_at"].isoformat(), "expires_at": cached["expires_at"].isoformat()}}
        error = None
        for attempt in range(self.retries):
            try:
                data = loader()
                expires = now + self.TTLS[kind.split(":", 1)[0]]
                store.write_stock_cache(cache_id, kind, data, now, expires)
                return {**data, "cache": {"stale": False, "fetched_at": now.isoformat(),
                                           "expires_at": expires.isoformat()}}
            except StockProviderError as exc:
                error = exc
                if not exc.retryable:
                    break
                if attempt + 1 < self.retries:
                    time.sleep(0.1 * (2 ** attempt))
        if cached:
            return {**cached["data"], "cache": {"stale": True,
                "fetched_at": cached["fetched_at"].isoformat(), "expires_at": cached["expires_at"].isoformat(),
                "provider_error": error.code if error else "provider_unavailable"}}
        raise error or StockProviderError("provider_unavailable", "Stock data is unavailable")

    def search(self, query, exchange=None):
        local = store.search_instruments(query, exchange)
        if local:
            results = [{"security_id": f"{row['exchange']}:{row['symbol']}", "symbol": row["symbol"],
                "exchange": row["exchange"], "name": row["name"], "asset_class": row["asset_class"],
                "currency": row["currency"], "provider_symbol": self.provider.prices.provider_symbol(
                    row["exchange"], row["symbol"])} for row in local]
            return {"query": query, "results": results, "source": {"name": "Local instrument catalogue"},
                    "cache": {"stale": False, "fetched_at": None, "expires_at": None}}
        key = f"SEARCH:{exchange or 'ALL'}:{query.upper()}"
        return self._load(key, "search", lambda: {"query": query,
            "results": self.provider.search(query, exchange), "source": SOURCE})

    def overview(self, security_id):
        exchange, symbol = parse_security_id(security_id)
        return self._load(security_id, "overview", lambda: self.provider.overview(exchange, symbol))

    def identity(self, security_id):
        """Resolve the canonical instrument identity used by command clients."""
        exchange, symbol = parse_security_id(security_id)
        return self.provider.identity(exchange, symbol)

    def candles(self, security_id, range_name):
        exchange, symbol = parse_security_id(security_id)
        return self._load(security_id, f"candles:{range_name}",
                          lambda: self.provider.candles(exchange, symbol, range_name))

    def financials(self, security_id, years):
        exchange, symbol = parse_security_id(security_id)
        return self._load(security_id, f"financials:{years}",
                          lambda: self.provider.financials(exchange, symbol, years))

    def latest_earnings(self, security_id):
        exchange, symbol = parse_security_id(security_id)
        return self._load(security_id, "earnings", lambda: self.provider.latest_earnings(exchange, symbol))


service = StockService()
