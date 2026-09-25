from copy import deepcopy
from datetime import date
from decimal import Decimal
import uuid
import pytest
from app.domain import apply, empty, replay, credit_replay, Invalid, loan_balance, loan_view
from app.views import dashboard

TODAY = date(2026, 9, 22)


def command(s, kind, p, actor="bot", key=None, today=TODAY):
    # Model the repository's commit/rollback boundary.
    work = deepcopy(s)
    result = apply(work, kind, p, actor, key or uuid.uuid4().hex, today)
    s.clear()
    s.update(work)
    return result


def account(s, name="Broker", type="brokerage", cur="USD", **extra):
    return command(s, "account_add", {"name": name, "type": type, "currency": cur, **extra})["id"]


def cash(s, a, amount="1000", cur="USD", when="2026-01-01"):
    return command(s, "opening_cash", {"account": a, "amount": amount, "currency": cur, "date": when})


def trade(a, **kw):
    return {"account": a, "exchange": "NASDAQ", "symbol": "AAPL", "asset_class": "equity", "currency": "USD", "quantity": "2", "price": "100", "date": "2026-02-01", **kw}


def loan(s, **kw):
    return command(s, "loan_add", {"name": "HDB", "principal": "100000", "rate": "2.6", "installment": "1000", "as_of": "2026-01-31", "next_due": "2026-02-15", **kw}, "web")


def test_buy_sell_and_opening_holdings():
    s = empty(); a = account(s); cash(s, a)
    command(s, "buy", trade(a))
    command(s, "sell", trade(a, quantity="1", price="110", date="2026-02-02"))
    balances, holdings, *_ = replay(s, TODAY)
    assert balances[a, "USD"] == Decimal("910")
    assert holdings[a, "NASDAQ:AAPL"] == 1
    command(s, "opening_holding", trade(a, symbol="MSFT", quantity="10", price="unknown"))
    assert replay(s, TODAY)[0][a, "USD"] == Decimal("910")


def test_atomic_rejection_and_backdated_correction():
    s = empty(); a = account(s); opening = cash(s, a)
    command(s, "buy", trade(a, quantity="9"))
    before = deepcopy(s)
    with pytest.raises(Invalid, match="Insufficient"):
        command(s, "correct", {"transaction": opening["id"], "changes": {"amount": "500"}})
    assert s == before
    with pytest.raises(Invalid, match="Insufficient"):
        command(s, "sell", trade(a, quantity="10"))
    assert s == before


def test_same_and_cross_currency_transfer():
    s = empty(); a = account(s, "Bank A", "bank", "SGD"); b = account(s, "Bank B", "bank", "SGD")
    cash(s, a, "2350", "SGD")
    command(s, "transfer", {"account": a, "destination": b, "currency": "SGD", "amount": "1000", "date": "2026-02-01"})
    e = command(s, "transfer", {"account": a, "destination": b, "currency": "SGD", "amount": "1350", "received": "1000", "to_currency": "USD", "date": "2026-02-02"})
    balances = replay(s, TODAY)[0]
    assert balances[a, "SGD"] == 0 and balances[b, "SGD"] == 1000 and balances[b, "USD"] == 1000
    assert e["data"]["rate_direction"] == "USD per SGD"
    assert Decimal(e["data"]["rate"]) == Decimal(1000) / Decimal(1350)


def test_duplicate_request_and_key_collision():
    s = empty(); a = account(s)
    p = {"account": a, "amount": "10", "currency": "USD", "date": "2026-01-01"}
    first = command(s, "deposit", p, key="same")
    assert command(s, "deposit", p, key="same") == first
    assert len(s["events"]) == 1
    with pytest.raises(Invalid, match="different request"):
        command(s, "deposit", {**p, "amount": "11"}, key="same")


def test_bank_cash_activity_keeps_description():
    s = empty()
    bank = account(s, "Daily", "bank", "SGD")
    deposited = command(s, "deposit", {"account": bank, "currency": "SGD", "amount": "100",
                        "description": "Salary", "date": "2026-09-01"})
    spent = command(s, "withdraw", {"account": bank, "currency": "SGD", "amount": "12.50",
                    "description": "Lunch", "date": "2026-09-02"})
    assert deposited["data"]["description"] == "Salary"
    assert spent["data"]["description"] == "Lunch"
    assert replay(s, TODAY)[0][bank, "SGD"] == Decimal("87.50")


def test_cpf_targets_replay_and_month_boundary():
    s = empty(); a = account(s, "CPF OA", "cpf", "SGD", cpf_type="OA")
    opening = cash(s, a, "20000", "SGD", "2026-08-20")
    assert dashboard(s, TODAY)["accounts"][0]["cpf_stale"]
    e = command(s, "cpf_set", {"account": a, "amount": "20500", "date": "2026-09-21"})
    assert replay(s, TODAY)[4][e["id"]] == "500"
    command(s, "correct", {"transaction": opening["id"], "changes": {"amount": "19000"}})
    assert replay(s, TODAY)[0][a, "SGD"] == 20500
    assert replay(s, TODAY)[4][e["id"]] == "1500"
    assert not dashboard(s, TODAY)["accounts"][0]["cpf_stale"]


def test_cancellation_preserves_audit():
    s = empty(); a = account(s); cash(s, a)
    buy = command(s, "buy", trade(a))
    corrected = command(s, "correct", {"transaction": buy["id"], "changes": {"quantity": "3"}})
    assert corrected["replaces"] == buy["id"]
    assert s["events"][1]["status"] == "superseded"
    command(s, "void", {"transaction": corrected["id"]})
    assert replay(s, TODAY)[0][a, "USD"] == 1000
    assert len(s["events"]) == 3


def test_missing_valuations_and_cpf_not_double_counted():
    s = empty(); a = account(s); cash(s, a)
    command(s, "opening_holding", trade(a))
    cpf = account(s, "OA", "cpf", "SGD", cpf_type="OA"); cash(s, cpf, "20000", "SGD")
    view = dashboard(s, TODAY)
    assert not view["complete"] and view["assets"] == "20000"
    assert view["allocation"]["cpf"] == "20000" and view["allocation"]["cash"] == "0"
    s["fx"]["USD"] = {"rate": "1.3", "date": "2026-09-21"}
    s["prices"]["NASDAQ:AAPL"] = {"close": "120", "midpoint": "119", "date": "2026-09-21", "currency": "USD"}
    view = dashboard(s, TODAY)
    assert view["complete"] and Decimal(view["assets"]) == Decimal("21612")


def test_loan_monthly_interest_and_split_funding():
    s = empty(); a = account(s, "Bank", "bank", "SGD"); b = account(s, "CPF OA", "cpf", "SGD", cpf_type="OA")
    cash(s, a, "300", "SGD"); cash(s, b, "1000", "SGD")
    l = loan(s)
    assert loan_balance(s, l, date(2026, 2, 1))[:2] == (Decimal("100000"), Decimal("216.67"))
    p = {"loan": l["id"], "amount": "500", "date": "2026-02-15", "allocations": [{"account": a, "amount": "300"}, {"account": b, "amount": "200"}]}
    command(s, "repayment", p, "web")
    principal, interest, history = loan_balance(s, l, date(2026, 2, 28))
    assert principal == Decimal("99716.67") and interest == 0
    assert history[-1]["interest"] == "216.67"
    balances = replay(s, TODAY)[0]
    assert balances[a, "SGD"] == 0 and balances[b, "SGD"] == 800
    before = deepcopy(s)
    with pytest.raises(Invalid, match="Insufficient"):
        command(s, "repayment", p, "web")
    assert s == before


def test_bot_cannot_mutate_loan_or_repayment():
    s = empty(); a = account(s, "Bank", "bank", "SGD"); cash(s, a, "2000", "SGD"); l = loan(s)
    p = {"loan": l["id"], "amount": "500", "date": "2026-02-15", "allocations": [{"account": a, "amount": "500"}]}
    with pytest.raises(Invalid, match="local web"):
        command(s, "repayment", p)
    payment = command(s, "repayment", p, "web")
    with pytest.raises(Invalid, match="web-only"):
        command(s, "void", {"transaction": payment["id"]})
    command(s, "void", {"transaction": payment["id"]}, "web")
    assert replay(s, TODAY)[0][a, "SGD"] == 2000


def test_rate_change_and_projection_do_not_post_cash():
    s = empty(); l = loan(s)
    command(s, "loan_rate", {"loan": l["id"], "date": "2026-03-01", "rate": "3"}, "web")
    l = s["loans"][l["id"]]
    assert loan_balance(s, l, date(2026, 3, 1))[1] == Decimal("466.67")
    before = deepcopy(s)
    view = loan_view(s, l, date(2026, 2, 1))
    assert view["schedule"][0]["interest"] == "216.67"
    assert view["schedule_complete"]
    assert s == before


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "1e100", "0.00000000001"])
def test_invalid_decimal_rejected(value):
    s = empty(); a = account(s)
    with pytest.raises(Invalid):
        cash(s, a, value)


def test_archiving_and_split():
    s = empty(); a = account(s); command(s, "opening_holding", trade(a, price="unknown"))
    command(s, "split", {**trade(a), "date": "2026-03-01", "ratio": "4"})
    assert replay(s, TODAY)[1][a, "NASDAQ:AAPL"] == 8
    with pytest.raises(Invalid, match="Sell holdings"):
        command(s, "account_archive", {"account": a})


def test_account_nickname_can_be_changed_without_changing_reference():
    s = empty()
    bank = account(s, "DBS Multiplier", "bank", "SGD")
    broker = account(s, "IBKR", "brokerage", "USD")
    renamed = command(s, "account_rename", {"account": bank, "name": "Daily spending"})
    assert renamed["id"] == bank
    assert s["accounts"][bank]["name"] == "Daily spending"
    assert s["accounts"][broker]["name"] == "IBKR"
    with pytest.raises(Invalid, match="already exists"):
        command(s, "account_rename", {"account": bank, "name": "ibkr"})


def test_month_end_schedule_keeps_original_due_day():
    s = empty(); l = loan(s, as_of="2026-01-01", next_due="2026-01-31")
    view = loan_view(s, l, date(2026, 1, 15))
    assert [r["date"] for r in view["schedule"][:3]] == ["2026-01-31", "2026-02-28", "2026-03-31"]


def test_cannot_restore_balance_in_archived_funding_account():
    s = empty(); a = account(s, "Bank", "bank", "SGD"); cash(s, a, "500", "SGD"); l = loan(s)
    payment = command(s, "repayment", {"loan": l["id"], "amount": "500", "date": "2026-02-15", "allocations": [{"account": a, "amount": "500"}]}, "web")
    command(s, "account_archive", {"account": a})
    with pytest.raises(Invalid, match="archived"):
        command(s, "void", {"transaction": payment["id"]}, "web")


def test_credit_card_balance_is_derived_from_activity_and_funded_payment():
    s = empty()
    bank = account(s, "Daily account", "bank", "SGD")
    cash(s, bank, "1000", "SGD")
    credit = command(s, "credit_account_add", {"name": "DBS Cards"})
    card = command(s, "credit_card_add", {"credit_account": credit["id"], "name": "Altitude"})
    command(s, "credit_purchase", {"credit_account": credit["id"], "card": card["id"],
            "description": "Groceries", "amount": "20", "date": "2026-09-02"})
    command(s, "credit_refund", {"credit_account": credit["id"], "card": card["id"],
            "description": "Returned item", "amount": "5", "date": "2026-09-03"})
    command(s, "credit_payment", {"credit_account": credit["id"], "funding_account": bank,
            "amount": "10", "date": "2026-09-04"})
    balances, activity, _ = credit_replay(s, TODAY)
    assert balances[credit["id"]] == Decimal("5")
    assert activity[credit["id"], card["id"]] == Decimal("15")
    assert replay(s, TODAY)[0][bank, "SGD"] == Decimal("990")
    view = dashboard(s, TODAY)
    assert view["credit_accounts"][0]["balance"] == "5"
    assert view["credit_accounts"][0]["cards"][0]["activity"] == "15"
    assert view["liabilities"] == "5" and view["assets"] == "990"


def test_credit_card_balance_cannot_be_manually_set():
    s = empty()
    credit = command(s, "credit_account_add", {"name": "Cards"})
    with pytest.raises(Invalid, match="calculated from purchases"):
        command(s, "credit_set", {"credit_account": credit["id"], "amount": "100",
                                  "date": "2026-09-01"})


def test_credit_card_payment_requires_available_non_cpf_sgd_cash():
    s = empty()
    bank = account(s, "Daily account", "bank", "SGD")
    cash(s, bank, "10", "SGD")
    credit = command(s, "credit_account_add", {"name": "Cards"})
    with pytest.raises(Invalid, match="Insufficient"):
        command(s, "credit_payment", {"credit_account": credit["id"], "funding_account": bank,
                "amount": "20", "date": "2026-09-04"})
