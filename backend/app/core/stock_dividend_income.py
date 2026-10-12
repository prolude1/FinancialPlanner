"""Principal-scoped historical gross dividend estimates from ledger holdings."""
from datetime import date, timedelta
from decimal import Decimal

from .domain import replay
from .stocks import parse_security_id


class HoldingsDividendIncomeService:
    def __init__(self, ledger_service):
        self.ledger_service = ledger_service

    def estimate(self, principal, security_id, dividend_history):
        exchange, symbol = parse_security_id(security_id)
        canonical_id = f"{exchange}:{symbol}"
        # The provider/cache call is completed before this separate principal-scoped ledger read.
        snapshot = self.ledger_service.read(principal)
        accounts = snapshot.get("accounts", {})
        all_events = snapshot.get("events", [])
        matching = [event for event in all_events if event.get("status") == "active" and
                    self._instrument_id(event) == canonical_id]
        provider_cache = dividend_history.get("cache")
        dividends = []
        for payout in dividend_history.get("dividends", []):
            ex_date = date.fromisoformat(payout["ex_date"])
            later_ledger_splits = [event["date"] for event in matching if event["kind"] == "split" and
                                   date.fromisoformat(event["date"]) > ex_date]
            reason = None
            if payout.get("split_adjustment_status") == "unsupported" or later_ledger_splits:
                reason = "split_adjustment_unverified"
            if payout.get("denomination_status") == "unverified" or not payout.get("currency"):
                reason = "dividend_denomination_unverified"
            same_day = {event["data"].get("account") for event in matching if event["date"] == str(ex_date) and
                        event["kind"] in ("opening_holding", "buy", "sell", "split")}
            prior = replay(snapshot, ex_date - timedelta(days=1))
            _, positions, _, _, _ = prior
            account_rows = []
            if reason:
                status = "unsupported"
            else:
                per_share = Decimal(str(payout["amount_per_share"]))
                affected = set(same_day)
                affected.update(account_id for account_id, instrument_id in positions if instrument_id == canonical_id)
                for account_id in sorted(affected):
                    shares = positions.get((account_id, canonical_id), Decimal(0))
                    account = accounts.get(account_id, {})
                    if account_id in same_day:
                        account_rows.append({"account_id": account_id, "account_name": account.get("name"),
                            "shares": None, "gross_income": None, "currency": payout["currency"],
                            "account_currency": account.get("currency"),
                            "status": "unknown_same_day_activity"})
                        continue
                    if shares <= 0:
                        continue
                    account_rows.append({"account_id": account_id,
                        "account_name": account.get("name"), "shares": format(shares.normalize(), "f"),
                        "gross_income": format((shares * per_share).normalize(), "f"),
                        "currency": payout["currency"], "account_currency": account.get("currency"),
                        "status": "estimated"})
                if any(row["status"] == "unknown_same_day_activity" for row in account_rows):
                    status = "estimated_with_unknown_accounts" if any(row["status"] == "estimated" for row in account_rows) else "unknown"
                elif account_rows:
                    status = "estimated"
                elif not matching:
                    status = "no_recorded_holding_history"
                elif not any(date.fromisoformat(event["date"]) < ex_date for event in matching):
                    status = "no_recorded_holding_history"
                else:
                    status = "no_holding"
            dividends.append({"ex_date": payout["ex_date"], "amount_per_share": payout["amount_per_share"],
                "currency": payout["currency"], "status": status,
                "accounts": account_rows, "unsupported_reason": reason,
                "total_gross_income": self._total(account_rows, payout["currency"])
                    if status in ("estimated", "estimated_with_unknown_accounts") else None})
        return {"security_id": canonical_id, "period_years": dividend_history.get("period_years", 5),
                "date_semantics": "ex_date", "basis": "shares held before ex-date",
                "currency": dividend_history.get("currency"), "cache": provider_cache, "dividends": dividends,
                "limitations": ["Gross estimate only; excludes withholding tax, FX, and reinvestment.",
                    "Historical estimates do not create ledger entries.",
                    "Split-affected dates are unsupported when dividend adjustment cannot be verified."],
                "source": dividend_history.get("source")}

    @staticmethod
    def _instrument_id(event):
        data = event.get("data", {})
        if event.get("kind") not in ("opening_holding", "buy", "sell", "split"):
            return None
        return f"{str(data.get('exchange', '')).upper()}:{str(data.get('symbol', '')).upper()}"

    @staticmethod
    def _total(rows, currency):
        total = sum((Decimal(row["gross_income"]) for row in rows if row.get("currency") == currency), Decimal(0))
        return format(total.normalize(), "f") if total else "0"
