# Ledger · Personal financial planner

A first working release of a single-user, Docker-based financial ledger: private Telegram input, a local account dashboard, and HDB loan forms. No local virtual environment or Node installation is required to run it.

## Start

1. Install/start Docker Desktop with Docker Compose.
2. If `.env` already exists, Compose uses it automatically; skip configuration and use the dashboard password recorded in that file. Otherwise, run `python3 scripts/configure.py` once. This uses Python's standard library to create a private `.env`, generate service secrets, and hash your chosen dashboard password. Enter your Telegram bot token and numeric user ID, or configure them later.
3. Run `docker compose up` (or `docker compose up --build` after source changes).
4. Open [the local dashboard](http://localhost:8080) and sign in with the password you chose.
5. In your private Telegram chat, use `/account_add`, then `/opening_cash` and `/opening_holding`.

Without Telegram credentials, the dashboard works and the bot stays idle. No demonstration accounts are created in your personal database.

## Images

- `financial-planner-backend:local`: API, Telegram bot, price/FX worker, and migration entry points.
- `financial-planner-web:local`: static dashboard and Nginx reverse proxy.
- PostgreSQL runs from `postgres:17.4-alpine` with a persistent named volume.

The backend image is reused by separate API, Telegram bot, worker, and migration
containers. The API runs from `app.api_server`, the polling bot from
`app.telegram_bot`, and shared domain/persistence code from `app.core`. The bot
does not publish a port because it uses outbound Telegram long polling.

Build independently of personal configuration:

```sh
docker build -t financial-planner-backend:local -f backend/Dockerfile .
docker build -t financial-planner-web:local web
docker run --rm financial-planner-backend:local python -m pytest -q -p no:cacheprovider
```

## Included

- Dynamic bank, brokerage, and CPF OA/SA/MA accounts; multiple currencies.
- Opening holdings, cash movements, buys/sells, transfers with recorded actual FX, CPF reconciliation, manual splits, corrections, and cancellations.
- SGD totals, asset allocation, account details, native currency balances, transaction history, and five-second dashboard refresh.
- HDB monthly-rest loan projections, effective-dated rates, and atomic repayments from several SGD accounts, including CPF OA.
- Owner-only Telegram checks, local dashboard login, audit records, duplicate-request protection, and database persistence.

## Current limits

The dashboard edits loans and repayments only; assets are entered through Telegram. Telegram is tested with simulated updates, but a live end-to-end bot test requires your own token and user ID. Rates must be entered manually for loans. HDB schedules are projections under the documented conventions, with no late fees or automatic payments. Loan creation terms and rate entries cannot yet be corrected through a dedicated form; repayment corrections are supported.

Free Yahoo Finance daily bars are obtained through yfinance; they are not a certified official closing-price feed. The worker excludes active sessions and waits 30 minutes after exchange close, then retries every 15 minutes. Missing or stale data is visible. Stock splits require a manual `/split`; fees, dividends, and bank interest are excluded. London identifiers are accepted by the adapter, but the release's live fetch verification covers US instruments.

## Documentation

- [Requirements and acceptance criteria](docs/requirements.md)
- [Usage guide and Telegram commands](docs/user-guide.md)
- [Actual container architecture and data design](docs/architecture.md)
- [Setup, operation, backup, and recovery](docs/operations.md)
- [Implementation decisions and remaining work](docs/open-decisions.md)
- [Validation record](docs/validation.md)
