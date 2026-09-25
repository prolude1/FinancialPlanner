"""Instrument catalogue: official US bulk listings plus verified cached lookups."""
from datetime import datetime, timezone
import csv
import io
import httpx
from . import store

NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
US_SOURCE = "Nasdaq Trader Symbol Directory"


def _rows(text):
    return list(csv.DictReader(io.StringIO(text.strip()), delimiter="|"))


def parse_us_catalog(nasdaq_text, other_text, updated_at=None):
    updated_at = updated_at or datetime.now(timezone.utc)
    result = []
    for row in _rows(nasdaq_text):
        symbol = row.get("Symbol", "").strip().upper()
        if not symbol or symbol.startswith("FILE CREATION TIME") or row.get("Test Issue") == "Y":
            continue
        result.append({"exchange": "NASDAQ", "symbol": symbol,
                       "name": row.get("Security Name", "").strip() or symbol,
                       "asset_class": "etf" if row.get("ETF") == "Y" else None,
                       "currency": "USD", "native_exchange": "NASDAQ",
                       "source": US_SOURCE, "active": True, "updated_at": updated_at})
    exchange_names = {"N": "NYSE", "A": "NYSE American", "P": "NYSE Arca",
                      "Z": "Cboe BZX", "V": "IEX"}
    for row in _rows(other_text):
        symbol = (row.get("ACT Symbol") or row.get("NASDAQ Symbol") or "").strip().upper()
        if not symbol or symbol.startswith("FILE CREATION TIME") or row.get("Test Issue") == "Y":
            continue
        # The app's NYSE choice is the user-facing bucket for non-Nasdaq US
        # listings, including NYSE Arca ETFs such as VOO. Preserve the venue.
        result.append({"exchange": "NYSE", "symbol": symbol,
                       "name": row.get("Security Name", "").strip() or symbol,
                       "asset_class": "etf" if row.get("ETF") == "Y" else None,
                       "currency": "USD", "native_exchange": exchange_names.get(row.get("Exchange"), row.get("Exchange")),
                       "source": US_SOURCE, "active": True, "updated_at": updated_at})
    return result


def refresh_us_catalog(client=None):
    client = client or httpx.Client(timeout=45, follow_redirects=True)
    first = client.get(NASDAQ_LISTED)
    second = client.get(OTHER_LISTED)
    first.raise_for_status()
    second.raise_for_status()
    rows = parse_us_catalog(first.text, second.text)
    if len(rows) < 1000:
        raise ValueError("US listing download was unexpectedly small")
    store.replace_catalog_source(US_SOURCE, rows)
    return len(rows)


def resolve(exchange, symbol, market_resolver):
    exchange, symbol = str(exchange).upper(), str(symbol).upper()
    if exchange not in ("NYSE", "NASDAQ", "LSE", "SGX"):
        raise ValueError("Choose NYSE, NASDAQ, LSE or SGX")
    known = store.find_instrument(exchange, symbol)
    if known and known.get("asset_class") and known.get("currency"):
        return known
    # Bulk US rows establish that the symbol exists, but a live lookup fills
    # type metadata when the directory does not explicitly mark an ETF.
    complete_catalog = store.catalog_count(exchange, source=US_SOURCE) if exchange in ("NYSE", "NASDAQ") else 0
    if complete_catalog and not known:
        region = "SGX" if exchange == "SGX" else "US"
        raise ValueError(f"{exchange}:{symbol} was not found in the {region} listing catalogue")
    details = market_resolver(exchange, symbol)
    if details.get("asset_class") not in ("equity", "etf"):
        raise ValueError("The symbol exists but is not identified as a stock or ETF")
    row = {"exchange": exchange, "symbol": symbol,
           "name": (known or {}).get("name") or details.get("name") or symbol,
           "asset_class": details["asset_class"], "currency": details["currency"],
           "native_exchange": details.get("native_exchange"),
           "source": (known or {}).get("source") or "Yahoo Finance verified cache",
           "active": True, "updated_at": datetime.now(timezone.utc)}
    store.cache_instrument(row)
    return row
