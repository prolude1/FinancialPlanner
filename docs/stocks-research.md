# Stocks provider research

Research and live read-only checks were completed on 24 September 2026 before
feature implementation. No bulk dataset or provider response is committed.

## Sources evaluated

### Yahoo Finance through yfinance — selected primary adapter

The project already used yfinance for completed-session portfolio valuations.
Its documented APIs cover symbol search, daily history, annual and quarterly
income statements, cash-flow statements, metadata, and earnings dates. Live
checks covered `AAPL` and `MSFT` (NASDAQ), `D05.SI` and `C6L.SI` (SGX), and
`ULVR.L` and `VWRA.L` (LSE). Completed daily bars and currencies were returned
for all six. Operating companies returned four or five annual columns; the VWRA
ETF correctly returned no corporate statements. Explicit EBITDA was not present
for every issuer and explicit EBITA was generally absent.

Yahoo is an aggregated source accessed through an unofficial open-source client,
so availability and field coverage can change without notice. The application
labels the source, throttles calls, caches successful responses, serves stale
completed data during transient failures, and represents missing metrics as null.
It does not present intraday data as final. Provider financial restatements replace
the cache at the next refresh; historical response versions are not kept. Use
remains subject to Yahoo's terms and the yfinance project's personal-use notice.
Documentation: https://ranaroussi.github.io/yfinance/

### SEC EDGAR Company Facts — evaluated, not selected for the first adapter

`data.sec.gov` is an official, keyless JSON API with real-time filing updates.
The AAPL Company Facts response succeeded and was approximately 3.8 MB. It is a
strong future source for US filing facts and filing dates, but it is US-only,
taxonomy selection requires issuer-aware normalization, and EBITDA/EBITA are not
consistent standard facts. It cannot meet the multi-exchange requirement alone.
SEC asks automated clients to identify themselves and stay under ten requests per
second. Documentation: https://www.sec.gov/search-filings/edgar-application-programming-interfaces

### Alpha Vantage — evaluated, not selected

Alpha Vantage documents daily prices, income statements, and cash-flow data, but
requires an API key. Its standard free service is limited to 25 requests per day,
which is too small for interactive search plus five detail queries per security.
Documentation: https://www.alphavantage.co/support/

## Normalization and known limits

- Canonical IDs are `EXCHANGE:SYMBOL`; supported exchanges are NASDAQ, NYSE,
  LSE, and SGX. Yahoo suffixes (`.L`, `.SI`) stay provider-internal.
- Price currency normalizes Yahoo's `GBp` penny denomination to `GBP`, scaling
  prices by 0.01. Statement currency follows available provider metadata.
- Only completed sessions determined by exchange calendars are returned. Candle
  rows after the latest completed session are discarded.
- Revenue uses explicit `TotalRevenue` or `OperatingRevenue`; profit after tax
  uses explicit net-income fields; free cash flow, EBITDA, and EBITA are never
  synthesized. Missing fields are null.
- Yahoo does not reliably expose filing dates alongside statement tables, so
  `report_date` is null when unavailable. Some issuers expose four rather than
  five annual periods; coverage metadata makes that shortfall explicit.
- Responses are cached for 30 minutes (overview), six hours (candles and latest
  earnings), or 24 hours (search and annual financials). Expired cache data is
  returned with `stale: true` only after a retryable provider failure.

## Quarterly results timeline

The Stocks page requests up to eight quarterly periods from
`GET /api/stocks/{security_id}/results/quarterly?quarters=8` (authenticated,
quarters 1–8). A separate `QuarterlyResultsTimeline` class matches comparable
period ends 350–380 days earlier to accommodate 52/53-week calendars. Revenue,
net income and explicit free cash flow are displayed with YoY percentages only
when both values exist and the prior value is positive. Comparisons fetch up to
four additional older periods when the provider supplies them. Missing quarters
are not filled or substituted with annual figures.

The timeline uses `financialCurrency`, independently of the security's quote
currency; an absent reporting currency is explicitly shown as unavailable.
Period ends are not filing or announcement dates. The provider adapter currently
has no reliable per-statement report dates, so these remain null. ETFs receive
an explicit unsupported response. Statements can contain restatements and
limited quarterly history: eight quarters is a request, not guaranteed coverage.
Results are cached for 24 hours with the existing stale-cache fallback. Timeline
request failures are displayed within the timeline without hiding price data.
The renderer lives in `web/features/stocks/quarterly-results.js` as an ES module.

## Holdings research, watchlist and financial trends

The Stocks page lists current positive-quantity holdings from the authenticated
portfolio snapshot, grouped by exchange and ticker across brokerage accounts.
It does not fetch financial statements for every holding in the background;
opening a security loads its research data through the existing cached queries.
ETFs can appear in the holdings list and watchlist, while corporate quarterly
results remain unavailable for ETFs.

A separate research watchlist supports candidates without creating ledger trades.
`GET /api/research/watchlist`, `PUT /api/research/watchlist/{security_id}` and
`DELETE /api/research/watchlist/{security_id}` derive ownership from the verified
caller. Legacy access retains existing claim and CSRF restrictions. The OOP
`ResearchWatchlistService` uses the shared ledger service for storage selection.
IDs are canonical, additions/deletions are idempotent, and each user can save up
to 100 securities. Changed watchlists increment the aggregate revision. The
optional `research_watchlist` state field survives database backups and workbook
export/import; import validates IDs and older backups remain compatible.

Financial trends reuse the quarterly results and five-year annual statements.
Separate SVG plots show revenue, profit after tax, explicit free cash flow and
profit margin (profit after tax divided by positive revenue). The view offers
quarterly/annual selection and an exact-value table. Chart scales are independent,
include zero and support losses. Missing values or omitted periods break lines.
Dates reflect fiscal period ends, not announcement dates. Annual financials now
use reporting currency too, with a new cache key to avoid reusing responses
labelled with the trading currency. The view does not forecast future results.
