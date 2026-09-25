from decimal import Decimal, localcontext

import pytest

from app.core.domain import Invalid
from app.core.finance import time_value


def request(**changes):
    values = {"calculation": "future_value", "initial_value": "1000", "cashflow": "100",
              "cashflow_frequency": "monthly", "cashflow_timing": "end",
              "annual_rate": "6", "duration_years": 1, "duration_months": 0,
              "compounding_frequency": "monthly"}
    values.update(changes)
    return values


def test_zero_rate_and_zero_cashflow():
    assert Decimal(time_value(request(annual_rate="0"))["result"]) == Decimal("2200")
    result = time_value(request(initial_value="2500", cashflow="0", duration_years=10))
    with localcontext() as context:
        context.prec = 34
        assert Decimal(result["result"]) == Decimal("2500") * Decimal("1.005") ** 120
    assert result["cashflow_component"] == "0"


def test_beginning_cashflows_earn_one_extra_period():
    end = time_value(request(initial_value="0", duration_years=0, duration_months=2))
    beginning = time_value(request(initial_value="0", duration_years=0, duration_months=2,
                                   cashflow_timing="beginning"))
    assert Decimal(beginning["result"]) == Decimal(end["result"]) * Decimal("1.005")


def test_negative_return_and_withdrawals():
    result = time_value(request(initial_value="10000", cashflow="-100", annual_rate="-12"))
    assert Decimal(result["result"]) < Decimal("8800")
    assert Decimal(result["schedule"][-1]["closing_balance"]) == Decimal(result["result"])
    assert Decimal(result["total_interest_or_returns"]) < 0


def test_present_and_future_value_round_trip_with_different_frequencies():
    future = time_value(request(initial_value="4321.12", cashflow="75.50",
        cashflow_frequency="quarterly", compounding_frequency="monthly",
        cashflow_timing="beginning", annual_rate="5.25", duration_years=8))
    present = time_value(request(calculation="present_value", initial_value=future["result"],
        cashflow="75.50", cashflow_frequency="quarterly", compounding_frequency="monthly",
        cashflow_timing="beginning", annual_rate="5.25", duration_years=8))
    assert Decimal(present["result"]) == Decimal("4321.12")
    assert Decimal(present["schedule"][-1]["closing_balance"]) == Decimal(future["result"])
    assert present["assumptions"]["periods"] == 32


def test_present_value_without_cashflows_is_discounted_target():
    result = time_value(request(calculation="present_value", initial_value="1060",
                                cashflow="0", annual_rate="6", duration_years=1,
                                cashflow_frequency="annual", compounding_frequency="annual"))
    assert Decimal(result["result"]) == Decimal("1000")


def test_duration_must_align_with_cashflow_frequency():
    with pytest.raises(Invalid, match="whole number"):
        time_value(request(cashflow_frequency="annual", duration_years=0, duration_months=6))


def test_schedule_can_be_omitted():
    assert time_value(request(include_schedule=False))["schedule"] == []


def test_multiple_cashflow_streams_with_different_returns_are_aggregated():
    first = request(initial_value="1000", cashflow="100", annual_rate="4")
    second = request(initial_value="2000", cashflow="50", annual_rate="8",
                     cashflow_frequency="quarterly", compounding_frequency="annual")
    combined = time_value({"calculation": "future_value", "duration_years": 1,
        "duration_months": 0, "include_schedule": True,
        "streams": [{k: v for k, v in first.items() if k not in
                     {"calculation", "duration_years", "duration_months"}},
                    {"name": "Higher return", **{k: v for k, v in second.items() if k not in
                     {"calculation", "duration_years", "duration_months"}}}]})
    with localcontext() as context:
        context.prec = 34
        expected = Decimal(time_value(first)["result"]) + Decimal(time_value(second)["result"])
    assert Decimal(combined["result"]) == expected
    assert combined["effective_annual_rate"] is None
    assert combined["schedule"] == []
    assert combined["streams"][0]["name"] == "Stream 1"
    assert combined["streams"][1]["name"] == "Higher return"
    assert combined["streams"][0]["effective_annual_rate"] != combined["streams"][1]["effective_annual_rate"]
    assert combined["streams"][0]["schedule"][-1]["elapsed_month"] == 12


def test_multiple_stream_present_value_round_trip():
    streams = [
        {"name": "Cash", "initial_value": "1500", "cashflow": "25",
         "cashflow_frequency": "monthly", "cashflow_timing": "end",
         "annual_rate": "2", "compounding_frequency": "monthly"},
        {"name": "Equities", "initial_value": "4500", "cashflow": "200",
         "cashflow_frequency": "quarterly", "cashflow_timing": "beginning",
         "annual_rate": "7", "compounding_frequency": "annual"},
    ]
    future = time_value({"calculation": "future_value", "duration_years": 5,
                         "duration_months": 0, "streams": streams})
    targets = [{**source, "initial_value": result["result"]}
               for source, result in zip(streams, future["streams"])]
    present = time_value({"calculation": "present_value", "duration_years": 5,
                          "duration_months": 0, "streams": targets})
    assert abs(Decimal(present["result"]) - Decimal("6000")) < Decimal("1e-25")


def test_cashflow_can_stop_while_interest_continues():
    limited = time_value(request(initial_value="0", cashflow="100", annual_rate="12",
                                 duration_years=2, cashflow_duration_months=12))
    one_year = time_value(request(initial_value="0", cashflow="100", annual_rate="12",
                                  duration_years=1))
    with localcontext() as context:
        context.prec = 34
        expected = Decimal(one_year["result"]) * Decimal("1.01") ** 12
    assert Decimal(limited["result"]) == expected
    assert [row["cashflow"] for row in limited["schedule"][:12]] == ["100"] * 12
    assert [row["cashflow"] for row in limited["schedule"][12:]] == ["0"] * 12
    assert Decimal(limited["schedule"][-1]["interest"]) > 0
    assert limited["assumptions"]["cashflow_periods"] == 12


def test_each_stream_has_an_independent_cashflow_window():
    result = time_value({"calculation": "future_value", "duration_years": 3,
        "duration_months": 0, "streams": [
            {"name": "First year", "initial_value": "0", "cashflow": "10",
             "cashflow_frequency": "monthly", "cashflow_timing": "end", "annual_rate": "5",
             "compounding_frequency": "monthly", "cashflow_duration_months": 12},
            {"name": "Final year", "initial_value": "0", "cashflow": "20",
             "cashflow_frequency": "monthly", "cashflow_timing": "end", "annual_rate": "8",
             "compounding_frequency": "monthly", "cashflow_start_month": 24,
             "cashflow_duration_months": 12}]})
    first, second = result["streams"]
    assert first["assumptions"]["cashflow_start_month"] == 0
    assert second["assumptions"]["cashflow_start_month"] == 24
    assert first["schedule"][12]["cashflow"] == "0"
    assert Decimal(first["schedule"][12]["interest"]) > 0
    assert second["schedule"][23]["cashflow"] == "0"
    assert second["schedule"][24]["cashflow"] == "20"
    assert result["total_contributions"] == "360"


@pytest.mark.parametrize("changes", [
    {"cashflow_start_month": 12, "duration_years": 1},
    {"cashflow_duration_months": 13, "duration_years": 1},
    {"cashflow_frequency": "quarterly", "cashflow_start_month": 1},
])
def test_cashflow_window_must_fit_and_align(changes):
    with pytest.raises(Invalid):
        time_value(request(**changes))


@pytest.mark.parametrize("changes", [
    {"duration_years": 0, "duration_months": 0},
    {"initial_value": "NaN"},
    {"annual_rate": "-101"},
])
def test_invalid_calculation_inputs(changes):
    with pytest.raises(Invalid):
        time_value(request(**changes))
