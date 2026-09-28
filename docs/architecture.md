# Container architecture and data design

This describes the implemented first release. The web UI uses plain JavaScript and CSS, served by Nginx; the backend uses Python 3.12, FastAPI, SQLAlchemy, and PostgreSQL. A frontend build tool is unnecessary for these screens. Asset forms can call the same command API later.

## Containers and network

```mermaid
flowchart LR
    Browser[Local browser] -->|localhost:8080| Web[web: Nginx + dashboard]
    Browser -->|localhost:8081 OIDC| Keycloak[Keycloak realm]
    Web -->|internal HTTP| API[api: authentication + commands]
    API -->|internal JWKS| Keycloak
    Telegram[Telegram] <-->|outbound polling| Bot[bot: private guided commands]
    Bot -->|service bearer credential| API
    API --> DB[(db: PostgreSQL)]
    Bot -->|conversation state| DB
    Worker[worker: daily prices + FX] --> Providers[Free providers]
    Worker --> DB
    Migrate[migrate: schema initialization] --> DB
```

| Service | Image/entry point | Responsibility |
|---|---|---|
| `web` | `financial-planner-web:local` | Static screens, same-origin `/api` proxy, security headers |
| `keycloak` | `quay.io/keycloak/keycloak:26.7.4` | Local OIDC issuer using the existing `keycloak` database and realm |
| `api` | `financial-planner-backend:local`, `app.api_server.main:app` | Owner sessions, validation, atomic commands, dashboard views |
| `bot` | Same backend image, `python -m app.telegram_bot` | Owner checks, guided prompts, confirmations, polling, durable replies |
| `worker` | Same backend image, `python -m app.core.worker` | Exchange-session checks, daily OHLC prices, daily reference FX |
| `migrate` | Same backend image, `python -m app.core.store` | Idempotent version-1 schema initialization; exits on success |
| `db` | `postgres:17.4-alpine` | Persistent application state |

Web port 8080 and Keycloak port 8081 are published only on `127.0.0.1`. All backend/database traffic stays on the Compose network. Telegram uses outbound polling; no public webhook or remote web access is configured.

The backend image contains three explicit package boundaries. `app.api_server`
owns HTTP authentication, routes, and response mapping. `app.telegram_bot` owns
Telegram polling, handlers, conversation state, callbacks, and formatting.
`app.core` owns reusable domain rules, schemas, persistence, projections, market
providers, and background work. Core never imports either process package, and
the API and bot do not import one another. Legacy top-level module names remain
temporary import aliases for existing callers; deployed entry points use the
canonical packages above.

Compose waits for PostgreSQL health, runs migration successfully, then starts the API. Web, bot, and worker wait for API health. Bot and worker have process supervision; only API/web/database have active health probes. PostgreSQL uses the `planner-data` named volume. Normal container recreation retains all data.

## Storage and transaction model

Each financial principal has one PostgreSQL `tenant_ledger` row with a `schema_version` and JSON aggregate. A write locks the row, validates a copied state, and commits the complete aggregate atomically. The original `planner_state(id=1)` remains an unassigned legacy ledger for the old local-password/single-owner bot integration only until the owner explicitly claims it. No principal is inferred from the old row. `tenant_portfolio_history` is keyed by principal, account, and date. The instrument catalog and quote cache remain shared read-only market infrastructure.

### Keycloak identity and Telegram link foundation

The browser and API use Keycloak access tokens for dashboard, Telegram-link, calculator, and stock API requests. Configure `KEYCLOAK_ISSUER` to the exact realm issuer URL and `KEYCLOAK_AUDIENCE` to the API audience included by a Keycloak audience mapper. `KEYCLOAK_JWKS_URL` is optional; by default the API uses `{issuer}/protocol/openid-connect/certs`. The issuer must use HTTPS except for loopback development. JWKS must use HTTPS except for the explicitly configured Docker service URL `http://keycloak:8080/...`; other plain-HTTP hosts are rejected. Validation accepts RS256 tokens only, resolves signing keys from that configured JWKS (with caching), and verifies signature, exact issuer, audience, expiry, issued-at, and Keycloak's `typ: Bearer` claim. The stable internal principal is SHA-256 of the configured issuer plus a NUL separator plus the verified `sub`, namespaced so subjects from different issuers cannot collide. Forwarded user-ID headers and request-body identity fields are ignored.

`POST /api/me/telegram-link/challenge` requires `Authorization: Bearer <Keycloak access token>` and returns HTTP 201 with `{challenge, expires_at, expires_in_seconds, bot_url}`. `TELEGRAM_BOT_USERNAME` is the public username without `@`; `bot_url` is `https://t.me/{username}?start=link_{challenge}`. Challenge creation returns HTTP 503 until the username is valid and the separate bot secret is present, at least 32 ASCII characters, and distinct from `BOT_API_SECRET`. Challenges are 256-bit opaque values, stored only as SHA-256 hashes, valid for ten minutes, and issuing a new one revokes the user's earlier pending challenge. `GET /api/me/telegram-link` returns `{connected: false}` or `{connected: true, linked_at}` for the token's own principal. `DELETE /api/me/telegram-link` unlinks only that principal and is safe to repeat.

The bot confirms a challenge with `POST /internal/bot/telegram-link/confirm`, authenticated by the distinct `TELEGRAM_LINK_BOT_SECRET` from `.env.telegram-link`. Its body is `{challenge, telegram_user_id, telegram_chat_id, chat_type: "private"}`; the handler derives IDs from the verified Telegram update and confirms only a private message where `chat.id == from.id`. The API requires those IDs to match. The bot never sends a claimed Keycloak principal. Challenges are consumed and the unique principal↔Telegram binding is inserted atomically. A Telegram user ID can be linked to only one principal, and a principal can link only one Telegram user ID. Unknown, expired, or replayed challenges receive the same HTTP 409 error. The link secret must differ from `BOT_API_SECRET`. The bot's `/start link_<challenge>` flow does not alter ledger data or grant financial access.

Keycloak is the identity boundary for financial APIs. `GET /api/me` returns only `{legacy_claim: {available, completed}}`; it never returns the hashed principal. `GET /api/dashboard`, `/api/cashflow`, `/api/revision`, `/api/commands/{command}`, and workbook endpoints derive the tenant solely from the verified bearer token. Existing API payloads remain unchanged and no caller may supply a principal or tenant ID. A first verified Keycloak request creates an empty tenant ledger. Legacy cookie/BOT bearer access is confined to the unclaimed legacy row and is rejected after the claim; it never falls through to a tenant ledger.

The one-time legacy claim code is issued by a trusted local operator with `python scripts/issue_legacy_claim.py` while `DATABASE_URL` points at the intended application DB. The code is 256-bit random, valid for ten minutes, stored only as SHA-256, printed once, and must be delivered out-of-band. Redeem with `POST /api/owner-claim`, a Keycloak bearer, and exactly `{"code":"..."}` in the JSON body. Success is `{"status":"claimed"}`. All invalid, expired, replayed, nonempty-tenant/history, and already-claimed cases return the same generic HTTP 400. A pristine empty tenant created by sign-in can be claimed. A database lock makes the claim single-use and atomic: it copies the legacy aggregate and derived history to the verified principal, consumes the code, sets an immutable claim marker, and resets the unassigned legacy ledger/history. Code values must not be placed in URLs, logs, shell arguments, or browser storage.

For multi-user Telegram finance, the bot's separate `BOT_API_SECRET` authenticates the bot service only; it is not a financial owner identity. `POST /internal/bot/financial/dashboard` accepts `{telegram_user_id,telegram_chat_id}`; `POST /internal/bot/financial/command/{command}` accepts the same IDs plus the existing domain `payload`; and `POST /internal/bot/financial/state` accepts those IDs plus `action: get|set|clear`. A `set` may include any subset of `session`, `pending_reply`, `pending_markup`, or `last_tvm`; `get` returns all four and `clear` resets all four. The API requires a private-chat ID match and resolves the linked sender through `telegram_connections` to the tenant. Unlinked senders receive a generic 403; there is no fallback to legacy data. Per-sender conversation/reply state is keyed by both principal and Telegram sender in `telegram_user_state`; state is cleared transactionally on unlink and link confirmation so it cannot cross an identity change. Telegram's polling offset stays in singleton `bot_runtime` and never appears in tenant ledgers.

The dashboard at `/` and the separate Telegram-link page at `/telegram-link.html` use the pinned official `keycloak-js` adapter with Authorization Code and PKCE S256. Runtime public configuration (`KEYCLOAK_ISSUER`, `KEYCLOAK_REALM`, `KEYCLOAK_WEB_CLIENT_ID`) is served through `/runtime-config.js`; the client ID and issuer are public values and no client secret is shipped. The web CSP permits the configured Keycloak origin for adapter network requests. The Telegram-link page remains isolated from the dashboard bundle; both pages send access tokens as Bearer credentials without relying on the legacy session. Keycloak initialization errors are shown directly; neither page falls back to the local password login.

Every writer locks the row using `SELECT ... FOR UPDATE`, copies its state, validates a change, and writes the new aggregate within one SQL transaction. Network requests never occur while holding the database lock. Failed validation rolls back every part of the operation. Concurrent API and worker writes cannot overwrite one another's changes.

Each tenant aggregate contains:

- `accounts`: stable IDs, names, account type, default currency, CPF subtype, archive flag.
- `events`: financial transactions, effective dates, deterministic ordering, original payloads, channel, status, and replacement links.
- `loans`: opening principal/interest, monthly installment, due-date anchor, and effective-dated rates.
- `prices` / `fx`: cached daily values, source, dates, retrieval timestamps, and staleness information.
- `receipts`: immutable submitted command/payload/result records keyed by actor plus idempotency key; administrative changes also appear here.
- `revision` / `provider_status`: dashboard refresh and source availability metadata.

Bot polling and user conversation state are separate: global `bot_runtime` stores only the update offset; `telegram_user_state` stores each linked sender's flow session and pending output.

Decimal quantities, cash, unit prices, rates, and interest are stored as strings and calculated using Python `Decimal`. Browser numbers are used only for display/chart percentages. Money rounding for loan postings is explicit half-up to cents. Unit quantities and trade inputs allow at most ten decimal places.

This aggregate keeps per-user transactions atomic but is not intended for very large histories: replay and full-document writes grow with history. A later migration can split accounts/events into tables while preserving the command API. SQLite is used only in isolated unit tests; deployed persistence uses PostgreSQL.

## Ledger rules

`domain.py` is independent of HTTP, Telegram, and the database. Events replay by effective date and stable order. A correction retains its predecessor's ordering, marks the old record superseded, and appends a linked replacement. Cancellation marks an entry void. Original entries and operation receipts remain available for audit.

- Opening cash/holdings establish the starting position; opening holdings do not deduct cash.
- Buys deduct quantity times unit price from brokerage cash; sells credit proceeds.
- Transfers contain both accounts, both currency amounts, and the derived actual FX rate with direction.
- CPF reconciliation stores a target balance. Replay derives the adjustment necessary at that date, including after corrections to earlier history.
- Manual splits multiply position quantity without changing cash.
- Repayments deduct all source allocations together, and loan calculations allocate the actual payment between interest and principal.
- Credit-card purchases and refunds retain their card identity. The parent account's combined SGD balance is derived from purchases minus refunds and payments. Card payments also deduct SGD cash from their selected non-CPF funding account.

Every new event or correction replays the affected financial history before commit. Any negative balance at any point is rejected. Archived accounts must stay empty. Corporate actions are manual; changing a ticker or exchange through provider data never silently changes quantities.

Account display names are unique. Instruments use exchange-qualified symbols (`NASDAQ:AAPL`, `NYSE:VOO`, `LSE:VWRA`, `SGX:D05`) with a consistent asset class and trading currency. This prevents different trading lines from being merged accidentally.

The `instrument_catalog` relational table is separate from the locked ledger JSON. It stores exchange, symbol, name, class, currency, native venue, source, active state, and refresh timestamp. The worker atomically replaces official US bulk rows daily. Verified LSE and SGX lookups are cached individually. Keeping the catalogue outside the aggregate prevents every financial write from copying thousands of listing records.

The derived `tenant_portfolio_history` table stores each tenant's daily brokerage value, raw change, external flow, and cash-flow-adjusted change in SGD; `portfolio_history` remains for the unclaimed legacy ledger. The worker rebuilds each principal separately from its event history plus historical close and FX series. These snapshots can be regenerated without changing financial ledgers. Missing historical price or FX data suppresses incomplete account-day snapshots rather than treating an asset as zero.

The `stock_cache` table stores normalized successful Stocks query responses by
canonical security ID and response kind. Provider calls occur outside ledger
transactions. Fresh entries avoid repeat calls; expired entries remain available
as explicitly stale fallback data after transient provider failures.

## Command and query API

Financial API calls require a valid Keycloak bearer for tenant selection. Old session and bot credentials may access only the unclaimed legacy ledger, and are rejected once it is claimed. Linked Telegram users use the internal bot financial endpoints described above; service credentials do not choose the tenant. Stock catalog, quotes, and financial-data provider caches remain shared market data.

Every command payload is parsed through a command-specific Pydantic model with unexpected fields forbidden. Pydantic checks the external request shape and nested structures before a database transaction starts; `domain.py` then applies state-dependent financial rules using exact `Decimal` calculations.

| Endpoint | Behavior |
|---|---|
| `POST /api/login`, `POST /api/logout`, `GET /api/session` | Local password login, signed HttpOnly session, CSRF token |
| `GET /api/revision` | Current data revision and local date |
| `GET /api/dashboard` | Accounts, balances, holdings, allocation, valuations, loans, schedules, audit-facing transaction history |
| `POST /api/calculators/time-value` | Authenticated stateless present/future-value calculation with optional period schedule |
| `GET /api/stocks/search` | Ticker search with optional exchange filter and canonical security IDs |
| `GET /api/stocks/{security_id}/identity` | Resolve a canonical instrument identity for internal command clients |
| `GET /api/stocks/{security_id}/overview` | Latest completed-session price and backend-computed prior-session change |
| `GET /api/stocks/{security_id}/candles` | Completed daily OHLCV candles for validated ranges |
| `GET /api/stocks/{security_id}/financials` | Up to five completed fiscal years of normalized core metrics |
| `GET /api/stocks/{security_id}/earnings/latest` | Latest completed quarterly statement, with annual fallback |
| `POST /api/commands/{command}` | Shared business operations; requires `Idempotency-Key` |

Commands are `account_add`, `account_archive`, `opening_cash`, `opening_holding`, `deposit`, `withdraw`, `buy`, `sell`, `transfer`, `cpf_set`, `split`, `credit_purchase`, `credit_refund`, `credit_payment`, `correct`, `void`, `loan_add`, `loan_rate`, and `repayment`. The bot cannot correct or cancel any event, and cannot create loans, change rates, or post repayments. These restrictions are enforced by the API domain layer, not merely hidden in the Telegram menu.

`GET /api/dashboard` returns the complete event ledger in `history`, newest first. Each event includes its stable `id`, `kind`, complete `data` payload, `status`, actor/timestamp audit fields, `replaces`/`replaced_by` links, any derived adjustment, and `can_edit`, `can_void`, and `editable_fields` capabilities. The web client edits an active event with `POST /api/commands/correct` and `{transaction, changes}` where `changes` contains one or more partial fields from `editable_fields`; it cancels with `POST /api/commands/void` and `{transaction}`. Both require the normal signed-in session, CSRF check, and idempotency key. Corrections append a linked replacement and retain the prior event. Domain validation checks the event-specific field set and exact decimal text, then replays the full ledger under the application write lock before committing. If a stale event ID is no longer active, the command returns HTTP 409 so the client can refresh history. Invalid decimals, archived-account references, and any correction that breaks balances return HTTP 422. Loan disbursements are never independently editable; repayments are editable only from the web client. Bot-authenticated dashboard reads report all events as noneditable, and the domain rejects bot correction/void commands even if an old handler sends them.

The time-value endpoint accepts a present- or future-value mode, a lump sum or target,
a signed recurring cash flow, annual nominal percentage rate, duration in years and
months, cash-flow and compounding frequencies, and beginning/end timing. Its decimal
results are JSON strings so clients do not lose precision; clients round monetary
values only for display. Present-value mode interprets `initial_value` as the desired
terminal value and returns the starting value needed after accounting for recurring
cash flows. Both browser and bot use this endpoint, and browser calls require CSRF.
For allocations with different returns, callers may replace the flat amount/rate
fields with up to 20 named `streams`. Each stream has its own lump sum or terminal
target, recurring cash flow, return, timing, and frequencies while sharing the
overall duration. The response includes aggregate totals and a full result and
schedule for every stream. An aggregate effective annual rate is deliberately
omitted because it would be misleading across independently compounded returns.
Each stream may also set `cashflow_start_month` and `cashflow_duration_months`.

### Monthly cashflow summary

`GET /api/cashflow` returns a read-only actual-cashflow view for bank accounts, derived from active ledger events. Without a query parameter it covers the latest 12 calendar months; `?year=YYYY` selects a calendar year. The response has `as_of`, a `selection` object (`type`, `year`, `start_month`, `end_month`, `current_month_partial`), and a `currencies` object keyed by native currency. Each currency contains month rows and totals with decimal-string `inflow`, `outflow`, and `net`. Month rows include `by_kind` and `by_account`; each drilldown includes transaction rows with event ID, date, account ID/name, and amount. No unlike currencies are added or converted. Deposits are labeled as deposits and are not classified as income.

The v1 cash movement includes bank deposits and loan proceeds as inflows, and bank withdrawals, bank-funded repayments, and bank-funded credit-card payments as outflows. Brokerage and CPF activity, card purchases/refunds, opening balances, resets, securities, and other non-cash adjustments are excluded. Transfers between bank accounts are reported in separate `internal_transfer_in` and `internal_transfer_out` fields and are excluded from net cashflow; transfers crossing the bank boundary count only their bank-side leg as an inflow or outflow. Corrections contribute only through their active replacement event; void and superseded events are ignored. The current calendar month is flagged as partial and future months in the selected year are zero-filled.
Only the recurring cash flow is limited by that window: the stream's opening
value and all accumulated returns continue compounding through the shared overall
duration. Windows must align with the selected cash-flow frequency, which keeps
beginning/end timing unambiguous.

The browser exposes forms only for loan creation, rate additions, repayments, and repayment corrections/cancellations. Other web editing can be added using the existing commands. Account/transaction validation is not duplicated in the frontend.

## Dashboard and valuations

The browser checks the revision every five seconds while visible, refreshing data after a revision/date change and on focus. Dialog fields remain outside the refreshed page content. Network failures keep the last view and show offline status.

Cash is shown by currency. Investment market value is quantity times the most recent usable completed-session close; its high/low midpoint is informational. The overview converts values to SGD using current recorded daily FX. Transfer FX is historical and never substituted for current valuation FX.

CPF maps to its own asset class and is not counted again as cash. Loans are liabilities, not negative pie slices. Net worth subtracts principal and accrued unpaid interest from assets. Property assets remain excluded even when an HDB liability is present.

Unpriced holdings and missing FX make totals explicitly incomplete rather than silently worth zero. Price dates, provider failures, and stale values are visible. A CPF warning appears one calendar month after its opening/reconciliation date; ordinary deposits and withdrawals do not count as a statement reconciliation.

## Free price and FX adapters

`worker.py` orchestrates refresh jobs. Stock and ETF quotes use the
`StockPriceProvider` interface in `providers.py`; the
`StockPriceProviderFactory` selects the configured implementation. The first
implementation is Yahoo. Adding another quote source requires a provider class
and one factory registration rather than changes throughout the worker.

The provider implementations use:

- [exchange_calendars](https://github.com/gerrymanoim/exchange_calendars) for XNYS, XLON, and XSES sessions, holidays, early closes, and timezone-aware boundaries.
- [Nasdaq Trader Symbol Directory](https://www.nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs) for daily US listing catalogue refreshes.
- [yfinance](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html) with `auto_adjust=False` to retrieve daily OHLC and verify uncached instruments. London symbols map to `.L`, Singapore symbols to `.SI`, and GBp quotes are converted to GBP after validating trading currency.
- [Frankfurter v2](https://frankfurter.dev/) for free daily currency-to-SGD reference rates.

The worker waits 30 minutes after a session closes, chooses only daily rows dated no later than that completed session, validates OHLC/currency, and stores source and retrieval timestamps. It polls every fifteen minutes by default. Provider failure retains the previous quote. There is no guaranteed provider finality flag: this is a conservative completed-session daily bar, not a claim of exchange-certified official closing-price data. Delayed or revised provider data may change a later refresh.

Quote/FX refreshes, US catalogue refreshes, and brokerage-history rebuilds have independent schedules and failure boundaries. A provider failure cannot prevent the other jobs from running. Failed scheduled jobs retain prior data and wait for their full configured interval before retrying.

Prices refresh on startup and the periodic cycle; recording a new symbol may therefore wait until the next cycle for its first price. Historical portfolio performance and historical FX valuation are not implemented. The latest OHLC cache is not a full market-data warehouse.

## HDB interest and schedules

HDB's published monthly-rest basis uses the principal outstanding at the start of the month and the annual rate divided by twelve. The concessionary rate is linked to CPF OA and may change; this application stores manually entered effective-dated rates rather than hard-coding a permanent rate. See [HDB interest rules](https://www.hdb.gov.sg/managing-my-home/finances/loan-matters/interest-rate).

Enter an end-of-day opening snapshot with principal and already-accrued unpaid interest. New monthly interest begins on the first of the next month. Within each month, the month's interest accrual is ordered before repayments on the first day. Interest is rounded half-up to cents; payments settle unpaid interest first. No interest is charged on unpaid interest. Confirm these conventions against your statement.

Projected schedules simulate the entered installment from the next upcoming anchored due date. Extra actual repayments reduce principal and therefore the projected term. Generation does not post payments. Future rate entries affect projection from their effective month. Projections stop after 600 payments and flag loans that do not clear within that horizon.

Current schedules are calculated on read, not stored as separate version snapshots. Original terms/rate commands remain in audit receipts. Rate additions and repayment corrections revalidate the existing history. Exact lender reconciliation, late penalties, initial partial-month origination interest, term edits, and alternate loan models are not implemented.

## Security and privacy

The local password is PBKDF2-SHA256 hashed with a unique salt. Sessions expire after twelve hours, are HttpOnly and SameSite Strict, and all browser mutations validate both Origin and CSRF token. Login attempts are rate-limited in the single API process. The loopback deployment uses HTTP; remote deployment would need a separate HTTPS/access design.

Telegram compares numeric sender and private chat IDs before reading data. Pending replies and conversation state persist. The bot and worker are trusted internal services with database access; the service-credential restriction is an API boundary, not a defense against a compromised internal container. Only the API receives session/password settings; only the bot receives the Telegram token.

Images exclude `.env`, and backend/web processes run as non-root users. Logs avoid credentials and financial message payloads. The first release uses one PostgreSQL application role for schema and data; finer database role separation remains a future hardening step.

## Tests

Containerized tests cover balances, opening holdings, transfers, duplicate requests, invalid decimals, CPF reconciliation, corrections, archive invariants, loan interest and schedules, split funding, access control, CSRF, private bot updates, holidays, and early closes. A separate Compose smoke test verifies PostgreSQL concurrency and rollback. See [validation](validation.md) for the completed checks and limits.
