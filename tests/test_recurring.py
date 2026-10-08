from datetime import date, datetime, timezone

import pytest

from app.core.domain import Invalid, empty
from app.core import recurring, store
from app.core.worker import RecurringTransactionWorker


def bank_state():
    state = empty()
    state["accounts"]["bank-1"] = {"id": "bank-1", "name": "Main", "type": "bank",
                                    "currency": "SGD", "archived": False}
    state["events"].append({"id": "opening", "kind": "opening_cash", "date": "2026-01-01",
        "order": 0, "status": "active", "actor": "test",
        "data": {"account": "bank-1", "amount": "1000.00", "currency": "SGD", "date": "2026-01-01"}})
    return state


def create(state, *, cadence="monthly", day_of_month=15, month_of_year=None,
           amount="10.00", account="bank-1", description="Rent"):
    return recurring.create_schedule(state, {"account": account, "amount": amount,
        "description": description, "cadence": cadence, "day_of_month": day_of_month,
        "month_of_year": month_of_year}, date(2026, 1, 10))


def test_schedule_dates_use_first_occurrence_and_strict_annual_validity():
    state = bank_state()
    monthly = create(state, day_of_month=10)
    assert monthly["next_due_date"] == "2026-01-10"
    later_month = recurring._next_occurrence(
        {"cadence": "monthly", "day_of_month": 10}, date(2026, 1, 11))
    assert later_month == date(2026, 2, 10)
    annual = create(state, cadence="annual", day_of_month=28, month_of_year=2)
    assert annual["next_due_date"] == "2026-02-28"
    assert recurring._next_occurrence({"cadence": "annual", "day_of_month": 28,
                                       "month_of_year": 2}, date(2026, 3, 1)) == date(2027, 2, 28)
    with pytest.raises(Invalid, match="day 1–28"):
        create(bank_state(), day_of_month=29)
    with pytest.raises(Invalid, match="valid every year"):
        create(bank_state(), cadence="annual", day_of_month=29, month_of_year=2)
    with pytest.raises(Invalid, match="valid every year"):
        create(bank_state(), cadence="annual", day_of_month=31, month_of_year=4)


def test_due_postings_catch_up_once_and_amount_change_preserves_past_amount():
    state = bank_state()
    schedule = recurring.create_schedule(state, {"account": "bank-1", "amount": "10.00",
        "description": "Rent", "cadence": "monthly", "day_of_month": 5,
        "month_of_year": None}, date(2026, 1, 1))
    assert schedule["next_due_date"] == "2026-01-05"
    assert recurring.post_due(state, date(2026, 3, 5)) == 3
    events = [event for event in state["events"] if event["actor"] == "recurring"]
    assert [event["date"] for event in events] == ["2026-01-05", "2026-02-05", "2026-03-05"]
    assert all(event["data"]["amount"] == "10.00" for event in events)
    assert recurring.post_due(state, date(2026, 3, 5)) == 0

    updated = recurring.update_schedule(state, schedule["id"], {"amount": "12.50"}, date(2026, 4, 5))
    assert updated["last_posted_date"] == "2026-04-05"
    assert updated["amount"] == "12.50"
    april = next(event for event in state["events"] if event["date"] == "2026-04-05")
    assert april["data"]["amount"] == "10.00"
    assert recurring.post_due(state, date(2026, 5, 5)) == 1
    may = next(event for event in state["events"] if event["date"] == "2026-05-05")
    assert may["data"]["amount"] == "12.50"


def test_pause_skips_due_dates_and_stopped_schedule_is_terminal():
    state = bank_state()
    schedule = recurring.create_schedule(state, {"account": "bank-1", "amount": "10.00",
        "description": "Rent", "cadence": "monthly", "day_of_month": 5,
        "month_of_year": None}, date(2026, 1, 1))
    recurring.update_schedule(state, schedule["id"], {"status": "paused"}, date(2026, 1, 3))
    assert recurring.post_due(state, date(2026, 4, 5)) == 0
    resumed = recurring.update_schedule(state, schedule["id"], {"status": "active"}, date(2026, 4, 5))
    assert resumed["next_due_date"] == "2026-04-05"
    assert recurring.post_due(state, date(2026, 4, 5)) == 1
    stopped = recurring.update_schedule(state, schedule["id"], {"status": "stopped"}, date(2026, 4, 6))
    assert stopped["status"] == "stopped"
    with pytest.raises(RuntimeError, match="cannot be changed"):
        recurring.update_schedule(state, schedule["id"], {"amount": "11.00"}, date(2026, 4, 7))


def test_insufficient_funds_pauses_only_that_schedule_without_posting_or_skipping_due_date():
    state = bank_state()
    state["accounts"]["bank-empty"] = {"id": "bank-empty", "name": "Empty", "type": "bank",
                                        "currency": "SGD", "archived": False}
    failing = recurring.create_schedule(state, {"account": "bank-empty", "amount": "500.00",
        "description": "Rent", "cadence": "monthly", "day_of_month": 5,
        "month_of_year": None}, date(2026, 1, 1))
    funded = recurring.create_schedule(state, {"account": "bank-1", "amount": "100.00",
        "description": "Utilities", "cadence": "monthly", "day_of_month": 5,
        "month_of_year": None}, date(2026, 1, 1))

    assert recurring.post_due(state, date(2026, 1, 5)) == 1
    failed_after = next(item for item in state["recurring_schedules"] if item["id"] == failing["id"])
    funded_after = next(item for item in state["recurring_schedules"] if item["id"] == funded["id"])
    assert failed_after["status"] == "paused"
    assert failed_after["pause_reason"] == "insufficient_funds"
    assert failed_after["next_due_date"] == "2026-01-05"
    assert funded_after["last_posted_date"] == "2026-01-05"
    assert len([event for event in state["events"] if event["actor"] == "recurring"]) == 1


def test_schedule_creation_idempotency_is_payload_bound():
    state = bank_state()
    payload = {"account": "bank-1", "amount": "10.00", "description": "Rent",
        "cadence": "monthly", "day_of_month": 5, "month_of_year": None}
    first = recurring.create_schedule(state, payload, date(2026, 1, 1),
        actor="bot", idempotency_key="telegram-123")
    retry = recurring.create_schedule(state, payload, date(2026, 1, 1),
        actor="bot", idempotency_key="telegram-123")
    assert retry == first
    assert len(state["recurring_schedules"]) == 1
    changed = {**payload, "amount": "20.00"}
    with pytest.raises(Invalid, match="different request"):
        recurring.create_schedule(state, changed, date(2026, 1, 1),
            actor="bot", idempotency_key="telegram-123")


def test_worker_catches_up_after_restart_without_duplicate_transactions():
    store.migrate()
    with store.engine.begin() as connection:
        connection.execute(store.tenant_ledger.delete())
    principal = "recurring-worker-test"
    with store.tenant_transaction(principal) as state:
        state["accounts"]["bank-1"] = {"id": "bank-1", "name": "Main", "type": "bank",
                                        "currency": "SGD", "archived": False}
        state["events"].append({"id": "opening", "kind": "opening_cash", "date": "2026-01-01",
            "order": 0, "status": "active", "actor": "test",
            "data": {"account": "bank-1", "amount": "1000.00", "currency": "SGD", "date": "2026-01-01"}})
        recurring.create_schedule(state, {"account": "bank-1", "amount": "10.00",
            "description": "Rent", "cadence": "monthly", "day_of_month": 5,
            "month_of_year": None}, date(2026, 1, 1))
    worker = RecurringTransactionWorker()
    now = datetime(2026, 3, 5, 12, tzinfo=timezone.utc)
    assert worker.run(now) == 3
    assert worker.run(now) == 0
    saved = store.read_for(principal)
    events = [event for event in saved["events"] if event["actor"] == "recurring"]
    assert [event["date"] for event in events] == ["2026-01-05", "2026-02-05", "2026-03-05"]
    assert saved["recurring_schedules"][0]["posted_count"] == 3
