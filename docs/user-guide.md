# User guide — first release

The first release is implemented. Run setup in the operations guide before using these commands.

## First use

1. Configure the private bot and local owner credentials as described in the [operations plan](operations.md).
2. Start the containers and open the local dashboard.
3. Open your private Telegram conversation with the bot and send `/start`.
4. Create bank, brokerage, and CPF accounts.
5. Enter opening cash balances and existing investment quantities as of a chosen starting date.
6. Verify the account list and each account's detail page before recording new activity.

Opening holdings may include original unit cost if known. Unknown cost must stay unknown; do not invent a zero purchase price. Profit/loss reporting is not yet an agreed first-release feature.

## Telegram interaction

Commands start guided conversations with field prompts. Use /accounts to find account IDs and enter those IDs when asked. There is no requirement to memorize a long command syntax. `/cancel` exits an unfinished conversation without saving it. A preview identifies the account, date, amounts, and resulting changes before confirmation.

| Command | Purpose and fields |
|---|---|
| `/accounts` | List active accounts and native currency balances; select an account for holdings and values |
| `/account_add` | Name, account type, default currency; brokerage accounts may hold additional currencies |
| `/account_rename` | Change an active account's nickname without changing its reference or history |
| `/account` | Open account actions: list, view, add, rename, archive, cash movements, transfers, and CPF reconciliation |
| `/creditcard` | Open credit-card actions: list, add, reconcile, purchase, refund, and payment |
| `/account_archive` | Archive an empty account after confirmation; the audit history is retained |
| `/opening_cash` | Account, currency, amount, opening date |
| `/opening_holding` | Brokerage, instrument/exchange, equity or ETF, quantity, currency, opening date, optional known unit cost |
| `/deposit` | Account, amount, currency, date |
| `/withdraw` | Account, amount, currency, date |
| `/buy` | Brokerage, instrument/exchange, quantity, unit price, currency, date |
| `/sell` | Brokerage, existing holding, quantity, unit price, currency, date |
| `/transfer` | Source, destination, date, amount sent and currency; received amount and currency if different |
| `/cpf_set` | CPF account, actual balance, effective date; records a reconciliation adjustment |
| `/split` | Brokerage, instrument, new shares per old share, effective date; quantity adjustment only |
| `/account ACCOUNT_ID` | Show cash and holdings for a specific account |
| `/help` | Show supported commands |

The Telegram command list keeps account administration under `/account` and less frequent card actions under `/creditcard`. Frequently used actions—`/deposit`, `/withdraw`, `/transfer`, `/cpf_set`, `/purchase`, and `/payment`—appear directly in the main command menu. `/purchase` records a credit-card purchase and `/payment` pays a credit-card balance from a cash account. Whenever a workflow asks for an account, credit-card account, or individual card, tap its name instead of copying an ID. For a purchase, every card appears in one combined list with its card-account group, so no separate group choice is required. The ID remains visible in text as a fallback. Completed operations show **Yes, save** and **No, cancel** buttons; `/confirm` and `/cancel` still work if you prefer typing.

`/calculator` opens the Financial calculator menu. Choose **Future value** to estimate how a starting amount and recurring cash flow grow over time, or **Present value** to calculate the starting amount needed to reach a desired future value. Both guide you through cash-flow frequency, annual return, duration, compounding, and beginning/end timing, then use the same backend calculator as the web UI. Results include contributions, returns, assumptions, and an inline yearly-breakdown action. Amounts are unitless, so use one consistent currency throughout a calculation.

`/stock` searches by ticker or company/security name. Always choose an exchange-qualified result, even when only one match appears. The stock view shows the latest completed-session close and change from the prior completed session, latest earnings/report metrics, and five-year annual financials. Missing financial values display as `-`; stale cached responses and incomplete coverage are labelled explicitly. Inline buttons switch between price, earnings, five-year financials, a new stock search, and refresh.

### Buying and selling

For a buy, select the brokerage and instrument, then enter quantity, actual unit price, currency, and date. At the quantity prompt, you may combine quantity and unit price as `quantity/price`, such as `10/123.45`; the bot then skips the separate price prompt. The bot shows the cash deduction before saving. A sell uses the normal separate fields and credits proceeds. No fees are applied in the first release.

At the exchange prompt, `EXCHANGE:TICKER` fills both fields at once, for example `NASDAQ:AAPL`, `NYSE:VOO`, `LSE:VWRA`, or `SGX:D05`. The ticker is still checked against the instrument catalogue. Telegram's command menu contains every supported slash command and is refreshed for the owner's private chat when the bot starts. Type `/` or tap the Commands menu button to see autocomplete suggestions.

After you enter the exchange and ticker, the bot checks an instrument catalogue before accepting it, then detects its asset class and currency. Choose NYSE or NASDAQ for US listings, LSE for London, and SGX for Singapore. For example, AAPL is detected as an equity in USD, while VOO and VWRA are detected as ETFs. Unknown symbols leave you at the ticker prompt so you can correct the entry. Penny-denominated London quotes are normalized to GBP, so enter the price in pounds rather than pence when the bot reports GBP.

The worker refreshes the official Nasdaq Trader US symbol files once a day. The `NYSE` choice is the app's simple bucket for non-Nasdaq US listings, including NYSE Arca ETFs; the catalogue retains the actual venue. London and SGX symbols are verified against Yahoo Finance on first use and cached in PostgreSQL.

An instrument is identified by exchange and symbol, not symbol alone. Its class is either individual equity or ETF. A trade's currency must match the supported trading line; currency conversion is a separate recorded operation.

### Deposits, withdrawals, and transfers

Use deposits and withdrawals for money entering or leaving the tracked system. Telegram asks for a description such as salary, reimbursement, groceries, or utilities, and shows it in transaction history. Use a transfer when both sides are tracked accounts, so the two entries remain linked.

For opening cash, deposits, withdrawals, and both sides of a transfer, enter the currency and amount in one message, such as `SGD 100` or `USD 25.50`. Manual cash entry currently supports SGD and USD. If the message is incomplete, malformed, or uses another currency, the bot keeps the operation at the same question and asks you to retry.

For a same-currency transfer, enter one amount. For a cross-currency transfer, enter both actual amounts. The system derives and displays the rate with an explicit direction, such as `1 USD = 1.35 SGD`, and retains it in history. It does not rewrite past transfers when market FX changes.

### CPF

Create OA, SA, and Medisave as three CPF accounts. Use deposits or withdrawals for known changes. When checking an actual CPF statement, use `/cpf_set` to enter the observed balance.

The UI always identifies CPF as manually maintained. A warning appears one calendar month after the latest reconciliation or opening balance. Ordinary deposits and withdrawals do not imply that the account has been reconciled against a statement. The warning never blocks updates.

### Transaction history and edits

Use the local web app's Activity page to review transaction history. Telegram is for recording new transactions. General transaction correction and cancellation controls are moving to the web app; until those controls are available, the retired `/history`, `/correct`, and `/void` commands point you to the web app and make no change. Loan repayment corrections are already available from the Loans page.

The backend retains original entries and audit links when an edit or cancellation is made. Posted transactions cannot be erased by archiving an account.

## Local dashboard

The overview displays assets, liabilities, and net worth in SGD, plus an allocation chart. The percentage legend is accompanied by the actual SGD value for every asset class and the total assets represented. Liabilities appear separately and are not represented as negative slices in the asset pie.

The account list shows native currency subtotals and SGD equivalents. Selecting a brokerage shows cash by currency and each position's symbol, exchange, class, quantity, latest completed session close, high/low midpoint, native market value, SGD value, and price date. Selecting a bank or CPF account shows balances and history.

Each brokerage detail page includes a daily SGD value chart. It opens at three months and can switch to one month, one year, or all available ledger history. Scroll over the graph to zoom around the pointer position, and move the pointer across it to inspect the date and SGD amount. The metric controls show total account value, raw daily movement, or investment movement adjusted for recorded deposits, withdrawals, transfers, opening balances, loan proceeds, repayments, and card payments.

The dashboard and Telegram `/accounts` listing group accounts by type in this order: bank, brokerage, and CPF. Names within each group are sorted alphabetically.

Totals show valuation timestamps and any missing or stale sources. If a holding or currency cannot be valued, the total is clearly marked incomplete and identifies the excluded component. The dashboard refreshes automatically; a connection indicator explains when live refresh is unavailable.

Asset forms are deferred. The initial layout should reserve clear account actions and detail sections so future editing can use conventional input boxes and selectors without changing the transaction rules.

## Credit cards through Telegram

Credit cards are maintained in SGD. A credit-card account represents one combined issuer balance and may contain multiple named cards. Create the account with `/credit_account_add`, add each physical or virtual card with `/credit_card_add`, and inspect the result with `/credit_accounts`.

`/credit_purchase` increases the outstanding balance and records activity against the selected card. `/credit_refund` records a card credit, reducing the balance and that card's activity total. `/credit_payment` reduces the balance and deducts the entered amount from a selected non-CPF account's SGD cash balance. The payment is rejected if that funding account lacks sufficient SGD cash.

The combined outstanding balance is calculated from recorded purchases minus refunds and payments. Payments are not allocated among cards. A combined balance below zero is allowed and appears as an account credit rather than a liability. Review card transactions in the web app's Activity page.

## Loans on the web

The initial loan type is HDB housing loan; car loans and private-bank housing loans are planned extensions. Property values remain excluded, so net worth includes the loan liability without the associated property asset.

Loan screens show outstanding principal, interest, next due payment, and schedule. Creation needs the loan currency, starting principal, start/as-of dates, interest model and rate, payment frequency, first due date, and term or payment amount. The initial model is HDB monthly-rest interest; conventions and remaining limitations are in [implementation decisions](open-decisions.md).

To record a repayment, choose the loan, payment date, total amount, and source allocations. For example, allocate SGD 300 from CPF OA and SGD 200 from a bank account to one SGD 500 payment. The allocations must sum to the payment, and each source must have enough cash. Before saving, the UI shows how much goes to interest and principal and what happens to the schedule.

The initial rule is same-currency funding: convert or transfer cash first if a repayment source holds another currency. Loan edits and repayment corrections remain local web operations, with audit history and the same balance validation as Telegram transactions.

## When something is unavailable

- Laptop asleep or containers stopped: the bot and dashboard backend are unavailable.
- Internet unavailable: cached balances and prices remain readable locally; Telegram and price refresh cannot work.
- Price provider delayed: retain the last valid completed-session data and show its date.
- Database unavailable: reject writes and show an error; do not claim a transaction was recorded.
- Bot confirmation interrupted: check the Activity page before trying again. Duplicate delivery of the same update is automatically ignored.

## HDB opening balances and corrections

Enter principal and already-accrued unpaid interest as of the end of the opening date. Monthly interest starts on the first of the following month. Enter the next payment date within one month after opening; its day is retained for later installments, clamped to month end. Use your statement's actual annual rate and installment.

Interest settles before principal. Extra payments retain the installment and shorten the projection. The Loans screen supports adding effective-dated rate changes on the first of a month, recording split-funded repayments, and correcting or cancelling existing repayments. Opening loan terms and existing rate entries do not yet have correction forms. Do not treat a projection as confirmation that a real payment has happened.

## Data refresh limits

Prices and FX refresh every fifteen minutes, and newly entered instruments may remain unpriced until that cycle. US catalogue listings refresh daily by default. Brokerage history is derived from the event ledger, historical daily closes, and historical FX, and refreshes every six hours by default. The close and midpoint come from the latest usable completed Yahoo daily bar with a 30-minute post-close buffer; the free provider does not certify an official closing auction price. Each quote shows its session date. Stock splits require /split and do not occur automatically.
