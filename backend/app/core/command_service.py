"""Validate and apply financial commands through the caller's ledger."""
from pydantic import ValidationError

from .domain import TransactionConflict


class CommandPayloadError(ValueError):
    def __init__(self, field, message):
        self.field = field
        super().__init__(message)


class CommandConflictError(ValueError):
    """The requested correction targets an event that is no longer active."""


class CommandFieldsError(ValueError):
    """The command payload is missing a required field or has invalid types."""


class FinancialCommandService:
    """Apply validated ledger commands without depending on HTTP or Telegram."""

    def __init__(self, ledger, apply_command, validate_command, current_date):
        self._ledger = ledger
        self._apply_command = apply_command
        self._validate_command = validate_command
        self._current_date = current_date

    def execute(
        self,
        command,
        payload,
        *,
        principal,
        actor,
        idempotency_key,
        telegram_user_id=None,
    ):
        validated = self._validate_payload(command, payload)
        try:
            with self._ledger.transaction(
                principal, telegram_user_id=telegram_user_id
            ) as state:
                return self._apply_command(
                    state,
                    command,
                    validated,
                    actor,
                    idempotency_key,
                    self._current_date(),
                )
        except TransactionConflict as exc:
            raise CommandConflictError(str(exc)) from exc
        except (KeyError, TypeError) as exc:
            raise CommandFieldsError("Missing or invalid operation fields") from exc

    def _validate_payload(self, command, payload):
        try:
            return self._validate_command(command, payload)
        except ValidationError as exc:
            issue = exc.errors(include_url=False)[0]
            field = ".".join(str(part) for part in issue["loc"]) or "request"
            raise CommandPayloadError(field, issue["msg"]) from exc
