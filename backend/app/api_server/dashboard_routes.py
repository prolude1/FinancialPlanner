"""Dashboard endpoints and the read-side assembly service."""
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request, Response

from ..core.ledger_service import FinancialLedgerService
from ..core.views import dashboard as build_dashboard


class DashboardQueryService:
    """Assemble a dashboard from one ledger snapshot and its projections."""

    def __init__(self, ledger: FinancialLedgerService, today: Callable):
        self._ledger = ledger
        self._today = today

    def _assemble(self, principal, actor, telegram_user_id=None, include_portfolio=False):
        snapshot = self._ledger.read(principal, telegram_user_id=telegram_user_id)
        result = build_dashboard(snapshot, self._today(), actor=actor)
        if include_portfolio:
            result["portfolio_history"] = self._ledger.portfolio_history(principal)
            result["portfolio_deposits"] = self._ledger.portfolio_deposits(principal)
        return result

    def get(self, principal, actor):
        return self._assemble(principal, actor, include_portfolio=True)

    def get_for_telegram(self, principal, telegram_user_id):
        return self._assemble(principal, "bot", telegram_user_id=telegram_user_id)


def create_dashboard_router(query_service, ledger_access, linked_bot_principal):
    """Build dashboard routes with auth helpers supplied by the API app."""
    router = APIRouter()

    @router.get("/api/dashboard")
    def get_dashboard(request: Request):
        principal, actor = ledger_access(request)
        return query_service.get(principal, actor)

    @router.post("/internal/bot/financial/dashboard")
    async def bot_financial_dashboard(request: Request, response: Response):
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict) or set(body) != {"telegram_user_id", "telegram_chat_id"}:
            raise HTTPException(status_code=422, detail={"code": "invalid_bot_request",
                "message": "Invalid private Telegram request"})
        principal, telegram_user_id = linked_bot_principal(request, body)
        try:
            result = query_service.get_for_telegram(principal, telegram_user_id)
        except PermissionError:
            raise HTTPException(status_code=403, detail={"code": "telegram_not_linked",
                "message": "Link this private Telegram account to continue"}) from None
        response.headers["Cache-Control"] = "no-store"
        return result

    return router
