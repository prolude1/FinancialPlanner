"""Account-scoped brokerage history endpoint and query service."""
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request, Response

from ..core.ledger_service import FinancialLedgerService


class BrokerageHistoryQueryService:
    """Authorize an account and read only its persisted chart projections."""

    def __init__(self, ledger: FinancialLedgerService, today: Callable):
        self._ledger = ledger
        self._today = today

    def get(self, principal, account_id):
        snapshot = self._ledger.read(principal)
        account = snapshot.get("accounts", {}).get(account_id)
        if account is None or account.get("type") != "brokerage":
            raise HTTPException(status_code=404, detail="Account not found")
        return {
            "account_id": account_id,
            "as_of": str(self._today()),
            "portfolio_history": self._ledger.portfolio_history_for_account(principal, account_id),
            "portfolio_deposits": self._ledger.portfolio_deposits_for_account(principal, account_id),
        }


def create_brokerage_history_router(query_service, ledger_access):
    router = APIRouter()

    @router.get("/api/accounts/{account_id}/brokerage-history")
    def get_brokerage_history(account_id: str, request: Request, response: Response):
        principal, _actor = ledger_access(request)
        response.headers["Cache-Control"] = "no-store"
        return query_service.get(principal, account_id)

    return router
