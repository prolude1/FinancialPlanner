# Requirements

## Scope and status

The first release is a single-user application running in Docker containers on the owner's laptop. Implementation was authorized after review; see the architecture and validation documents for actual behavior and known limits. No financial account integrations or automatic order execution are planned: recording a trade does not place an order with a broker.

## Agreed behavior

### Accounts and assets

- Create any number of bank and brokerage accounts.
- Track cash in its native currency, including multiple currencies within a brokerage account.
- Track individual equities and ETFs as distinct asset classes.
- Support US-listed investments initially. Design instrument identifiers and price adapters to support London-listed investments, including VWRA, later.
- Represent CPF OA, SA, and Medisave as separate manually maintained accounts. These are user-maintained records, not a model of CPF eligibility or withdrawal rules.
- Exclude property, crypto, fees, dividends, bank interest, and dedicated credit-card tracking from the initial scope.
- Enter existing cash and holdings through Telegram as opening balances. An opening holding does not deduct cash as if it were a new trade.

### Transaction history

- Derive cash balances and investment quantities from transaction history.
- Buys deduct cash from the selected brokerage account; sells increase its cash.
- Record quantity, unit price, currency, and transaction date. Fractional quantities are a proposed default.
- Support deposits and withdrawals on bank, brokerage, and CPF accounts.
- Transfer cash between accounts. No transfer of shares between brokerages is needed.
- For currency conversion, preserve the amount sent, amount received, and actual exchange rate. This rate is distinct from the market FX rate used for dashboard valuation.
- Correct or cancel erroneous transactions while retaining an audit record.
- A CPF balance overwrite is always permitted. Store it as a dated reconciliation adjustment, retaining the previous history. Warn when the account has not been reconciled for at least one calendar month.

### Valuation

- Show native currency balances by default.
- Report total assets, liabilities, and net worth in SGD.
- Value equities and ETFs using the official closing price of the latest completed regular trading session.
- Also display that session's high/low midpoint: `(high + low) / 2`. Label this as a midpoint, not a median of actual trades.
- Never use a partial ongoing session for the daily close or midpoint.
- Use free market-price and FX providers only.
- Refresh after final data becomes available, accounting for market time zones, holidays, and delayed provider publication.
- Display source/session dates and stale or missing data status. Never silently value an unpriced holding at zero.

### Dashboard

- Show total assets, total liabilities, and net worth in SGD.
- Show an asset-allocation pie chart with cash, individual equities, ETFs, and CPF. CPF is counted once as CPF rather than again as ordinary cash.
- Show an account list with native currency subtotals and an SGD equivalent.
- Open a specific account to see each holding, cash balance, valuation, and transaction history.
- Automatically refresh after Telegram mutations. Proposed target: within five seconds while the UI is open and connected.
- Initial asset/account screens are read-only. Design shared APIs so form-based editing can be added later.
- Loan creation and repayment forms are available locally in the first release as an explicit exception.

### Loans

- Create loans through the local web UI only, never through Telegram.
- Include interest calculations and repayment schedules for HDB housing loans initially. Car loans and private-bank housing loans are future extensions.
- Track the housing liability without adding property to tracked assets. Consequently, displayed net worth excludes property value.
- Record repayments, funding one payment from multiple accounts when necessary.
- Apply a repayment and all account deductions atomically: either the entire payment succeeds or nothing changes.
- Implemented interest conventions, rounding, allocation, and remaining limitations are recorded in [implementation decisions](open-decisions.md).

### Access and deployment

- Start all application services through `docker compose up` after supplying configuration and having Docker available.
- Accept bot operations only from the configured numeric Telegram user ID in its private chat.
- Run on the laptop; the bot cannot respond while the laptop is asleep or containers are stopped.
- Keep persistent data across container recreation and normal Compose shutdown.
- Proposed web access default: bind only to laptop loopback, with local owner authentication for loan mutations.

## Proposed safeguards

- Reject trades, withdrawals, transfers, and repayment allocations that would create negative cash or negative holding quantities. Margin and short selling are out of scope unless explicitly added.
- Archive an empty account instead of deleting its history. Require selling holdings and transferring or withdrawing remaining cash first.
- Confirm mutations with a concise preview; provide stable transaction references for corrections.
- Reject duplicate Telegram updates rather than recording a trade twice.
- Revalidate later transactions when changing a backdated entry. Reject a correction that invalidates later balances and explain the conflicting transactions.

## Acceptance examples

1. An opening USD 1,000 cash balance and a purchase of two shares at USD 100 produce USD 800 cash and two shares. A sale of one at USD 110 produces USD 910 cash and one share.
2. Opening an existing holding of ten shares leaves cash unchanged.
3. A transfer of SGD 1,000 from one bank account to another changes both account balances but not total wealth.
4. Sending SGD 1,350 and receiving USD 1,000 stores both amounts and the rate of 1.35 SGD per USD. Later valuation uses the current recorded valuation FX rate, not this transaction rate.
5. Recording the same Telegram update twice produces exactly one transaction.
6. A non-owner Telegram message produces no financial read or write operation.
7. A CPF reconciliation from SGD 20,000 to SGD 20,500 records an adjustment of SGD 500 and clears the stale-reconciliation warning.
8. During a US trading session, the holding still shows the previous completed session's close and midpoint. Once finalized data is available, both move to the new session together.
9. A missing price leaves native quantity visible and marks the SGD aggregate incomplete; it is not treated as a zero-value asset.
10. A loan repayment funded by SGD 300 from one account and SGD 200 from another posts one SGD 500 payment with two deductions. Insufficient funds in either source rejects the whole operation.
11. Cancelling a trade restores the derived balances while preserving the original entry and cancellation in the audit trail.
12. Recreating containers preserves accounts, transactions, loan schedules, and settings.

## First-release departures from the target

Free daily bars cannot certify official exchange closing-price finality. The release uses completed-session daily closes with a 30-minute buffer and visible source/date information. Loan projections use documented HDB conventions; no lender statement reconciliation or stored schedule snapshots are implemented. The web uses plain JavaScript rather than React, and PostgreSQL stores a locked versioned aggregate rather than normalized entity tables. See the architecture for the reasons and tradeoffs.
