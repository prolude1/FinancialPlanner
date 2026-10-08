"""Tenant-owned recurring bank withdrawals and their ledger postings."""
from copy import deepcopy
from datetime import date, datetime, timezone
import uuid

from .domain import Invalid, apply, validate_payload


def _next_occurrence(schedule, from_date):
    if schedule["cadence"] == "monthly":
        month = date(from_date.year, from_date.month, 1)
        candidate = month.replace(day=schedule["day_of_month"])
        if candidate < from_date:
            year, month_number = (month.year + 1, 1) if month.month == 12 else (month.year, month.month + 1)
            candidate = date(year, month_number, schedule["day_of_month"])
        return candidate
    candidate = date(from_date.year, schedule["month_of_year"], schedule["day_of_month"])
    if candidate < from_date:
        candidate = candidate.replace(year=candidate.year + 1)
    return candidate


def _validate_schedule(state, payload, today):
    if not isinstance(payload, dict) or set(payload) != {
        "account", "amount", "description", "cadence", "day_of_month", "month_of_year"
    }:
        raise Invalid("Provide account, amount, description, cadence, day_of_month, and month_of_year")
    if payload["cadence"] not in ("monthly", "annual"):
        raise Invalid("Cadence must be monthly or annual")
    day_number = payload["day_of_month"]
    if isinstance(day_number, bool) or not isinstance(day_number, int):
        raise Invalid("day_of_month must be an integer")
    if payload["cadence"] == "monthly":
        if not 1 <= day_number <= 28 or payload["month_of_year"] is not None:
            raise Invalid("Monthly schedules use day 1–28 and no month_of_year")
    else:
        month_number = payload["month_of_year"]
        if isinstance(month_number, bool) or not isinstance(month_number, int) or not 1 <= month_number <= 12:
            raise Invalid("Annual schedules require month_of_year from 1 to 12")
        max_day = 28 if month_number == 2 else 30 if month_number in (4, 6, 9, 11) else 31
        if not 1 <= day_number <= max_day:
            raise Invalid("Annual date must be valid every year")

    account_id = payload["account"]
    account = state.get("accounts", {}).get(account_id)
    if not account or account.get("archived") or account.get("type") != "bank":
        raise Invalid("Choose an active bank account")
    if not isinstance(payload["amount"], str):
        raise Invalid("Amount must be exact decimal text")
    if not isinstance(payload["description"], str):
        raise Invalid("Description must be text")
    description = payload["description"].strip()
    if not description or len(description) > 160:
        raise Invalid("Description must be 1–160 characters")

    normalized = {"account": account_id, "amount": payload["amount"],
        "currency": account.get("currency", "SGD"), "description": description,
        "date": str(today)}
    validate_payload(state, "withdraw", normalized, today)
    return normalized, account


def _public(schedule, state):
    item = deepcopy(schedule)
    item.pop("initial_amount", None)
    item.pop("amount_changes", None)
    account = state.get("accounts", {}).get(item["account"], {})
    item["account_name"] = account.get("name", item["account"])
    item["currency"] = account.get("currency", item.get("currency", "SGD"))
    return item


def list_schedules(state):
    state.setdefault("recurring_schedules", [])
    return sorted((_public(item, state) for item in state["recurring_schedules"]),
                  key=lambda item: (item["status"] != "active", item["next_due_date"], item["id"]))


def create_schedule(state, payload, today, *, actor="web", idempotency_key=None):
    if idempotency_key is not None:
        if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 160:
            raise Invalid("A valid idempotency key is required")
        receipt_key = f"recurring_schedule_create:{actor}:{idempotency_key}"
        previous = state.setdefault("receipts", {}).get(receipt_key)
        if previous:
            if previous.get("payload") != payload:
                raise Invalid("Idempotency key was already used for a different request")
            return deepcopy(previous["result"])
    else:
        receipt_key = None
    normalized, account = _validate_schedule(state, payload, today)
    now = datetime.now(timezone.utc).isoformat()
    schedule = {"id": uuid.uuid4().hex, "account": normalized["account"],
        "amount": normalized["amount"], "currency": account.get("currency", "SGD"),
        "initial_amount": normalized["amount"], "amount_changes": [],
        "description": normalized["description"], "cadence": payload["cadence"],
        "day_of_month": payload["day_of_month"], "month_of_year": payload["month_of_year"],
        "status": "active", "next_due_date": str(_next_occurrence({
            "cadence": payload["cadence"], "day_of_month": payload["day_of_month"],
            "month_of_year": payload["month_of_year"]}, today)),
        "last_posted_date": None, "posted_count": 0,
        "created_at": now, "updated_at": now}
    state.setdefault("recurring_schedules", []).append(schedule)
    result = _public(schedule, state)
    state["revision"] = state.get("revision", 0) + 1
    if receipt_key:
        state["receipts"][receipt_key] = {"command": "recurring_schedule_create",
            "payload": deepcopy(payload), "result": deepcopy(result),
            "at": datetime.now(timezone.utc).isoformat()}
    return result


def _schedule(state, schedule_id):
    return next((item for item in state.setdefault("recurring_schedules", [])
                 if item.get("id") == schedule_id), None)


def update_schedule(state, schedule_id, changes, today):
    schedule = _schedule(state, schedule_id)
    if schedule is None:
        raise KeyError("Recurring schedule not found")
    if schedule["status"] == "stopped":
        raise RuntimeError("Stopped recurring schedules cannot be changed")
    if not isinstance(changes, dict) or len(changes) != 1:
        raise Invalid("Update either amount or status")

    # Post due dates with the old amount/state before a mutation so edits only
    # affect occurrences after the edit request.
    post_due(state, today)
    schedule = _schedule(state, schedule_id)
    prior_status = schedule["status"]
    prior_amount = schedule["amount"]
    if "amount" in changes:
        account = state.get("accounts", {}).get(schedule["account"])
        if not account or account.get("archived") or account.get("type") != "bank":
            raise Invalid("Choose an active bank account")
        amount = changes["amount"]
        if not isinstance(amount, str):
            raise Invalid("Amount must be exact decimal text")
        validate_payload(state, "withdraw", {"account": schedule["account"], "amount": amount,
            "currency": account.get("currency", "SGD"), "description": schedule["description"],
            "date": str(today)}, today)
        if amount != schedule["amount"]:
            schedule["amount"] = amount
            effective = _next_occurrence({key: schedule[key] for key in
                ("cadence", "day_of_month", "month_of_year")}, today)
            if effective <= today:
                effective = _following(schedule, effective)
            schedule.setdefault("amount_changes", []).append({
                "effective_from_due_date": str(effective), "amount": amount})
    elif "status" in changes:
        status = changes["status"]
        if status not in ("active", "paused", "stopped"):
            raise Invalid("Status must be active, paused, or stopped")
        if schedule["status"] == "stopped" and status != "stopped":
            raise RuntimeError("Stopped recurring schedules cannot be reactivated")
        if status == "active" and schedule["status"] == "paused":
            if schedule.get("pause_reason") != "insufficient_funds":
                cadence = {key: schedule[key] for key in ("cadence", "day_of_month", "month_of_year")}
                schedule["next_due_date"] = str(_next_occurrence(cadence, today))
            schedule.pop("pause_reason", None)
        schedule["status"] = status
    else:
        raise Invalid("Update either amount or status")
    if schedule["status"] != prior_status or schedule["amount"] != prior_amount:
        state["revision"] = state.get("revision", 0) + 1
    schedule["updated_at"] = datetime.now(timezone.utc).isoformat()
    return _public(schedule, state)


def _following(schedule, due_date):
    if schedule["cadence"] == "monthly":
        year, month = (due_date.year + 1, 1) if due_date.month == 12 else (due_date.year, due_date.month + 1)
        return date(year, month, schedule["day_of_month"])
    return due_date.replace(year=due_date.year + 1)


def _amount_for_due(schedule, due_date):
    amount = schedule.get("initial_amount", schedule["amount"])
    for change in schedule.get("amount_changes", []):
        if date.fromisoformat(change["effective_from_due_date"]) <= due_date:
            amount = change["amount"]
    return amount


def post_due(state, today):
    """Post every due withdrawal and advance schedules atomically with the ledger."""
    posted = 0
    state.setdefault("recurring_schedules", [])
    while True:
        pending = [schedule for schedule in state["recurring_schedules"]
                   if schedule.get("status") == "active"
                   and date.fromisoformat(schedule["next_due_date"]) <= today]
        if not pending:
            break
        schedule = min(pending, key=lambda item: (item["next_due_date"], item.get("created_at", ""), item["id"]))
        account = state.get("accounts", {}).get(schedule["account"])
        if not account or account.get("archived") or account.get("type") != "bank":
            schedule["status"] = "paused"
            schedule["pause_reason"] = "account_unavailable"
            schedule["updated_at"] = datetime.now(timezone.utc).isoformat()
            state["revision"] = state.get("revision", 0) + 1
            continue
        due = date.fromisoformat(schedule["next_due_date"])
        event_count = len(state["events"])
        try:
            result = apply(state, "withdraw", {"account": schedule["account"],
                "amount": _amount_for_due(schedule, due), "currency": account.get("currency", "SGD"),
                "description": schedule["description"], "date": str(due)},
                "recurring", f"{schedule['id']}:{due}", today=today)
        except Invalid as exc:
            # apply() appends a withdrawal before replay checks for insufficient
            # cash. Remove only that uncommitted event, then isolate the failure
            # to this schedule so other tenant schedules can proceed.
            del state["events"][event_count:]
            if "Insufficient cash or holdings at transaction" not in str(exc):
                raise
            schedule["status"] = "paused"
            schedule["pause_reason"] = "insufficient_funds"
            schedule["updated_at"] = datetime.now(timezone.utc).isoformat()
            state["revision"] = state.get("revision", 0) + 1
            continue
        schedule["last_posted_date"] = str(due)
        schedule["last_transaction_id"] = result["id"]
        schedule["posted_count"] = int(schedule.get("posted_count", 0)) + 1
        schedule["next_due_date"] = str(_following(schedule, due))
        schedule["updated_at"] = datetime.now(timezone.utc).isoformat()
        posted += 1
    return posted
