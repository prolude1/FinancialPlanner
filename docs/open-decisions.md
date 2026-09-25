# Implementation decisions and remaining work

Implementation was authorized after the requirements review. The first version uses these visible defaults:

- HDB loans first; car and private-bank housing products are deferred.
- Existing loan balances are entered as an end-of-day snapshot with optional already-accrued interest. New loan creation can optionally record proceeds into a non-CPF account.
- Interest accrues on the first of each following month, on opening principal at the applicable annual percentage rate divided by twelve, rounded half-up to cents. Payments settle interest first, then principal.
- Extra payments shorten the projected term while retaining the entered monthly installment. Due dates preserve the chosen day, clamped to month end where necessary.
- Total liabilities include principal and accrued unpaid interest. Schedules are projections, not actual payments or automatic bank instructions.
- Repayments may use several SGD balances, including CPF OA. Convert other currencies separately first.
- No late fees, payment holidays, daily accrual, or automatic HDB rate synchronization. Check projections against actual statements.
- Yahoo Finance via yfinance supplies daily prices and instrument verification, Frankfurter supplies daily FX, and exchange_calendars supplies session boundaries.
- The free quote source does not certify an official closing auction price or final publication. The implementation uses the completed-session daily close, a 30-minute buffer, visible provenance, and retries. An exchange-certified feed is not claimed.
- Manual stock splits via `/split`; no dividends, interest income, or fees initially.
- Local owner login, loopback binding, five-second dashboard polling, no margin/shorting/negative cash, and archive-only account removal.

## Remaining work

1. Test the real Telegram conversation with the owner's configured bot token and user ID. Simulated update authorization and guided command tests pass.
2. Confirm the loan conventions against an actual HDB statement; exact lender reconciliation and exceptional charges remain outside the first version.
3. Add dedicated correction forms for loan opening terms and existing rate entries, plus installment changes and stored schedule snapshots. Current audit receipts preserve submitted terms; schedules are generated from them.
4. Extend live provider verification to London/VWRA. The data model and adapter already accept LSE identifiers and validate trading currency.
5. Add the planned web forms for accounts and assets using the existing shared command API.
6. Add bank-statement/broker imports, richer history filtering, automated backup schedules, and additional loan products only when requested.
7. If the single-user event history grows large, migrate the versioned aggregate to normalized tables and incremental projections. This first release prioritizes atomic consistency and simple recovery.

No paid services, external trade execution, or automatic transfer of funds are part of this release.
