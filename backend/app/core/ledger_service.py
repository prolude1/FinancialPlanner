"""Application service for reading and writing a caller's financial ledger.

The API authenticates a request and resolves its principal. This service then
selects the matching storage path, keeping tenant and legacy ledger access in
one place. Route handlers remain responsible for HTTP policy and projections.
"""
from contextlib import AbstractContextManager
from typing import Any, Protocol


class LedgerRepository(Protocol):
    """Storage operations required by :class:`FinancialLedgerService`."""

    def read_for(self, principal: str, telegram_user_id: int | None = None) -> dict[str, Any]: ...

    def read_legacy(self) -> dict[str, Any]: ...

    def tenant_transaction(
        self, principal: str, telegram_user_id: int | None = None
    ) -> AbstractContextManager[dict[str, Any]]: ...

    def transaction(self) -> AbstractContextManager[dict[str, Any]]: ...

    def read_portfolio_history(self, principal: str | None = None) -> dict[str, Any]: ...


class FinancialLedgerService:
    """Resolve storage operations for an already-authenticated caller.

    ``principal=None`` explicitly selects the unclaimed legacy ledger. Linked
    Telegram requests pass their verified Telegram user ID so the repository
    can enforce the existing link check as part of the read or write.
    """

    def __init__(self, repository: LedgerRepository):
        self._repository = repository

    def read(
        self, principal: str | None, *, telegram_user_id: int | None = None
    ) -> dict[str, Any]:
        if principal is None:
            if telegram_user_id is not None:
                raise ValueError("Telegram ledger access requires a linked principal")
            return self._repository.read_legacy()
        return self._repository.read_for(principal, telegram_user_id=telegram_user_id)

    def transaction(
        self, principal: str | None, *, telegram_user_id: int | None = None
    ) -> AbstractContextManager[dict[str, Any]]:
        if principal is None:
            if telegram_user_id is not None:
                raise ValueError("Telegram ledger access requires a linked principal")
            return self._repository.transaction()
        return self._repository.tenant_transaction(
            principal, telegram_user_id=telegram_user_id
        )

    def portfolio_history(self, principal: str | None) -> dict[str, Any]:
        return self._repository.read_portfolio_history(principal)
