# Validation record

Validated on 22 September 2026 on this laptop, using Docker Desktop and Linux ARM64 images. No Python virtual environment remains in the repository.

## Passed

- Backend image build: `financial-planner-backend:local`.
- Web image build: `financial-planner-web:local`.
- 23 automated tests run inside the backend image, covering the ledger, API, simulated Telegram updates, and exchange-calendar boundaries.
- Isolated full Compose startup with PostgreSQL health gating, successful migration, API readiness, web proxy, worker, and an intentionally unconfigured/idle bot.
- PostgreSQL integration test: 24 concurrent deposits preserved; eight concurrent identical requests recorded exactly one transaction.
- Failed insufficient-funds transaction rolled back without changing history.
- Local login, CSRF protection, unauthorized reads, and bot restrictions on loan operations.
- HDB interest calculation and a repayment funded jointly from bank cash and CPF OA.
- Browser walkthrough: sign-in, dashboard/account allocation, loan screens, and a further SGD 100 synthetic repayment split SGD 40/60 across two accounts. Outstanding principal updated correctly.
- Live free-provider price fetch for AAPL and VOO, plus USD/SGD reference FX. Account values and allocation displayed successfully.
- PostgreSQL dump restored into a separate test database; the full aggregate checksum matched exactly.
- All containers recreated against the same named volume; test transaction history and loan records remained intact.
- JavaScript syntax, local documentation links, and absence of `.venv` checked.

## Limits

The test stack uses clearly named synthetic accounts on port 18080, separate from the personal deployment. It does not create accounts in the user's own production database.

Live Telegram delivery was not exercised: no real bot token or user ID was supplied. The simulated tests cover owner/private-chat authorization, guided input, confirmation, and duplicate handling. Live London/VWRA fetches were not validated.

Financial calculations are application tests, not confirmation that every HDB contract exception is modeled. Opening/partial-month interest, penalties, and lender reconciliation are documented limitations. Free daily bars do not certify official exchange finality. A third-party test-client dependency emits a deprecation warning; all tests pass.
