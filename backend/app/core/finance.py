"""Pure time-value-of-money calculations shared by every client."""
from decimal import Decimal, InvalidOperation, localcontext

from .domain import Invalid, nominal_periodic_rate


FREQUENCIES = {"annual": 1, "semiannual": 2, "quarterly": 4, "monthly": 12}


def _decimal(value, label):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise Invalid(f"{label} must be a valid decimal number") from None
    if not result.is_finite() or abs(result) > Decimal("1e15"):
        raise Invalid(f"{label} must be finite and at most 1e15")
    return result


def _text(value):
    """Return a non-exponential, full-precision JSON-safe decimal string."""
    value = Decimal(0) if value == 0 else value
    return format(value, "f")


def _single_time_value(payload):
    """Calculate one FV stream or its required starting PV.

    Rates are nominal annual percentages. Interest is compounded at the chosen
    frequency. A fractional number of compounding periods between cash flows is
    represented by the mathematically equivalent periodic growth factor.
    """
    calculation = payload["calculation"]
    timing = payload["cashflow_timing"]
    cash_frequency = payload["cashflow_frequency"]
    compound_frequency = payload["compounding_frequency"]
    cash_periods_per_year = FREQUENCIES[cash_frequency]
    compounds_per_year = FREQUENCIES[compound_frequency]
    years, months = payload["duration_years"], payload["duration_months"]
    total_months = years * 12 + months
    if total_months <= 0:
        raise Invalid("Duration must be at least one month")
    if total_months * cash_periods_per_year % 12:
        raise Invalid(f"Duration must contain a whole number of {cash_frequency} cash-flow periods")
    periods = total_months * cash_periods_per_year // 12
    if periods > 1200:
        raise Invalid("Duration produces more than 1,200 cash-flow periods")

    start_month = payload.get("cashflow_start_month", 0)
    cashflow_months = payload.get("cashflow_duration_months")
    if start_month >= total_months:
        raise Invalid("Cash-flow start must be before the end of the calculation")
    if start_month * cash_periods_per_year % 12:
        raise Invalid(f"Cash-flow start must align with a {cash_frequency} period")
    if cashflow_months is None:
        cashflow_months = total_months - start_month
    if start_month + cashflow_months > total_months:
        raise Invalid("Cash-flow window cannot extend beyond the calculation duration")
    if cashflow_months * cash_periods_per_year % 12:
        raise Invalid(f"Cash-flow duration must contain whole {cash_frequency} periods")
    cashflow_start_period = start_month * cash_periods_per_year // 12
    cashflow_periods = cashflow_months * cash_periods_per_year // 12
    cashflow_end_period = cashflow_start_period + cashflow_periods

    initial = _decimal(payload["initial_value"], "Initial value")
    cashflow = _decimal(payload["cashflow"], "Cash flow")
    annual_rate = _decimal(payload["annual_rate"], "Annual rate")
    if initial < 0:
        raise Invalid("Initial value cannot be negative")
    if annual_rate < -100 or annual_rate > 1000:
        raise Invalid("Annual rate must be between -100 and 1,000 percent")

    with localcontext() as context:
        context.prec = 34
        compound_base = Decimal(1) + nominal_periodic_rate(annual_rate, compounds_per_year)
        if compound_base <= 0:
            raise Invalid("Annual rate is incompatible with the compounding frequency")
        growth = compound_base ** (Decimal(compounds_per_year) / Decimal(cash_periods_per_year))
        effective_annual = compound_base ** compounds_per_year - 1
        total_growth = growth ** periods

        cashflow_future = Decimal(0)
        for period_index in range(periods):
            active_cashflow = cashflow if cashflow_start_period <= period_index < cashflow_end_period else Decimal(0)
            if timing == "beginning":
                cashflow_future = (cashflow_future + active_cashflow) * growth
            else:
                cashflow_future = cashflow_future * growth + active_cashflow

        if calculation == "future_value":
            starting_balance = initial
            result = initial * total_growth + cashflow_future
            initial_component = initial * total_growth
            cashflow_component = cashflow_future
            terminal_value = result
        else:
            terminal_value = initial
            initial_component = initial / total_growth
            cashflow_component = -cashflow_future / total_growth
            result = initial_component + cashflow_component
            starting_balance = result

        schedule = []
        balance = starting_balance
        for period in range(1, periods + 1):
            opening = balance
            applied_cashflow = cashflow if cashflow_start_period <= period - 1 < cashflow_end_period else Decimal(0)
            if timing == "beginning":
                interest = (balance + applied_cashflow) * (growth - 1)
                balance = balance + applied_cashflow + interest
            else:
                interest = balance * (growth - 1)
                balance = balance + interest + applied_cashflow
            if payload.get("include_schedule", True):
                schedule.append({"period": period,
                                 "elapsed_month": period * 12 // cash_periods_per_year,
                                 "opening_balance": _text(opening),
                                 "cashflow": _text(applied_cashflow), "interest": _text(interest),
                                 "closing_balance": _text(balance)})

        total_contributions = starting_balance + cashflow * cashflow_periods
        returns = terminal_value - total_contributions
        return {"calculation": calculation, "result": _text(result),
                "initial_value_component": _text(initial_component),
                "cashflow_component": _text(cashflow_component),
                "total_contributions": _text(total_contributions),
                "total_interest_or_returns": _text(returns),
                "effective_annual_rate": _text(effective_annual * 100),
                "schedule": schedule,
                "assumptions": {"cashflow_timing": timing,
                                "cashflow_frequency": cash_frequency,
                                "compounding_frequency": compound_frequency,
                                "periods": periods,
                                "cashflow_start_month": start_month,
                                "cashflow_duration_months": cashflow_months,
                                "cashflow_periods": cashflow_periods,
                                "annual_rate_percent": _text(annual_rate),
                                "rounding": "Values retain Decimal calculation precision; clients round only for display."}}


def time_value(payload):
    """Calculate one or aggregate several independently compounded streams.

    A multi-stream present-value request assigns a terminal target to each
    stream. This avoids inventing an allocation of one target across returns.
    """
    streams = payload.get("streams") or []
    if not streams:
        single = dict(payload)
        single["cashflow"] = single.get("cashflow", "0")
        single.setdefault("cashflow_frequency", "monthly")
        single.setdefault("cashflow_timing", "end")
        single.setdefault("compounding_frequency", "monthly")
        single.setdefault("duration_months", 0)
        return _single_time_value(single)

    common = {"calculation": payload["calculation"],
              "duration_years": payload.get("duration_years", 0),
              "duration_months": payload.get("duration_months", 0),
              "include_schedule": payload.get("include_schedule", True)}
    results = []
    for index, stream in enumerate(streams, 1):
        item = _single_time_value({**common, **stream})
        item["name"] = stream.get("name") or f"Stream {index}"
        results.append(item)

    def total(field):
        with localcontext() as context:
            context.prec = 34
            return _text(sum((Decimal(item[field]) for item in results), Decimal(0)))

    return {"calculation": payload["calculation"], "result": total("result"),
            "initial_value_component": total("initial_value_component"),
            "cashflow_component": total("cashflow_component"),
            "total_contributions": total("total_contributions"),
            "total_interest_or_returns": total("total_interest_or_returns"),
            "effective_annual_rate": None, "schedule": [], "streams": results,
            "assumptions": {"stream_count": len(results),
                            "duration_years": common["duration_years"],
                            "duration_months": common["duration_months"],
                            "effective_annual_rate": "Reported per stream because returns differ.",
                            "rounding": "Values retain Decimal calculation precision; clients round only for display."}}
