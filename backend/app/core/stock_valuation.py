"""User-assumption-only per-share valuation scenarios."""
from decimal import Decimal, InvalidOperation, localcontext
import re


class ValuationInputError(ValueError):
    pass


def _decimal(value, name, *, maximum=Decimal("1e12")):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValuationInputError(f"{name} must be a finite decimal") from None
    if not number.is_finite() or abs(number) > maximum or number.as_tuple().exponent < -10:
        raise ValuationInputError(f"{name} must be finite, within ±{maximum}, and use at most 10 decimal places")
    return number


def _format(number):
    return format(number.normalize(), "f") if number else "0"


def _currency(value):
    code = str(value or "").upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ValuationInputError("currency must be a three-letter code")
    return code


class StockValuationService:
    """Calculate P/E and per-share DCF values without provider-derived inputs."""

    def calculate(self, security_id, payload):
        method = payload.get("method")
        currency = _currency(payload.get("currency"))
        reference = payload.get("reference_price")
        reference = _decimal(reference, "reference_price") if reference is not None else None
        if reference is not None and reference <= 0:
            raise ValuationInputError("reference_price must be greater than zero")
        if method == "pe":
            return self._pe(security_id, payload, currency, reference)
        if method == "dcf":
            return self._dcf(security_id, payload, currency, reference)
        raise ValuationInputError("method must be 'pe' or 'dcf'")

    def _pe(self, security_id, payload, currency, reference):
        allowed = {"method", "currency", "eps", "multiples", "reference_price"}
        if set(payload) - allowed or payload.get("eps") is None or not isinstance(payload.get("multiples"), list):
            raise ValuationInputError("P/E requires eps and multiples and accepts no DCF assumptions")
        eps = _decimal(payload["eps"], "eps")
        multiples = payload["multiples"]
        if not 1 <= len(multiples) <= 12:
            raise ValuationInputError("multiples must contain between 1 and 12 scenarios")
        scenarios = []
        for raw in multiples:
            multiple = _decimal(raw, "multiple", maximum=Decimal("1000"))
            if multiple < 0:
                raise ValuationInputError("P/E multiples cannot be negative")
            meaningful = eps > 0
            value = eps * multiple if meaningful else None
            scenarios.append({"multiple": _format(multiple), "status": "calculated" if meaningful else "not_meaningful",
                              "value_per_share": _format(value) if value is not None else None,
                              "comparison_percent": self._comparison(value, reference)})
        return {"security_id": security_id, "method": "pe", "currency": currency,
                "status": "calculated" if eps > 0 else "not_meaningful",
                "assumptions": {"eps": _format(eps), "multiples": [_format(_decimal(v, "multiple", maximum=Decimal("1000"))) for v in multiples],
                                "reference_price": _format(reference) if reference is not None else None},
                "scenarios": scenarios, "source": "User supplied assumptions; no recommendation"}

    def _dcf(self, security_id, payload, currency, reference):
        allowed = {"method", "currency", "free_cash_flow_per_share", "annual_growth_percent",
                   "discount_rate_percent", "terminal_growth_percent", "years", "reference_price"}
        required = allowed - {"reference_price"}
        if set(payload) - allowed or any(payload.get(key) is None for key in required):
            raise ValuationInputError("DCF requires free_cash_flow_per_share, growth, discount, terminal growth and years")
        cashflow = _decimal(payload["free_cash_flow_per_share"], "free_cash_flow_per_share")
        growth = _decimal(payload["annual_growth_percent"], "annual_growth_percent", maximum=Decimal("100"))
        discount = _decimal(payload["discount_rate_percent"], "discount_rate_percent", maximum=Decimal("100"))
        terminal_growth = _decimal(payload["terminal_growth_percent"], "terminal_growth_percent", maximum=Decimal("100"))
        years = payload["years"]
        if isinstance(years, bool) or not isinstance(years, int) or not 1 <= years <= 30:
            raise ValuationInputError("years must be an integer from 1 to 30")
        if growth <= -100 or terminal_growth <= -100:
            raise ValuationInputError("growth rates must be greater than -100 percent")
        if discount <= 0 or discount <= terminal_growth:
            raise ValuationInputError("discount_rate_percent must be positive and greater than terminal_growth_percent")
        if cashflow <= 0:
            return {"security_id": security_id, "method": "dcf", "currency": currency,
                    "status": "not_meaningful", "value_per_share": None,
                    "assumptions": self._dcf_assumptions(cashflow, growth, discount, terminal_growth, years, reference),
                    "schedule": [], "terminal_value_per_share": None, "terminal_value_present_per_share": None,
                    "comparison_percent": None, "source": "User supplied assumptions; no recommendation"}
        with localcontext() as context:
            context.prec = 40
            g, r, tg = growth / 100, discount / 100, terminal_growth / 100
            schedule = []
            current = cashflow
            total = Decimal(0)
            for year in range(1, years + 1):
                current *= 1 + g
                present = current / ((1 + r) ** year)
                total += present
                schedule.append({"year": year, "cash_flow_per_share": _format(current),
                                 "discounted_cash_flow_per_share": _format(present)})
            terminal = current * (1 + tg) / (r - tg)
            terminal_present = terminal / ((1 + r) ** years)
            value = total + terminal_present
        return {"security_id": security_id, "method": "dcf", "currency": currency, "status": "calculated",
                "value_per_share": _format(value),
                "assumptions": self._dcf_assumptions(cashflow, growth, discount, terminal_growth, years, reference),
                "schedule": schedule, "terminal_value_per_share": _format(terminal),
                "terminal_value_present_per_share": _format(terminal_present),
                "comparison_percent": self._comparison(value, reference),
                "source": "User supplied assumptions; no recommendation"}

    @staticmethod
    def _dcf_assumptions(cashflow, growth, discount, terminal_growth, years, reference):
        return {"free_cash_flow_per_share": _format(cashflow), "annual_growth_percent": _format(growth),
                "discount_rate_percent": _format(discount), "terminal_growth_percent": _format(terminal_growth),
                "years": years, "reference_price": _format(reference) if reference is not None else None}

    @staticmethod
    def _comparison(value, reference):
        if value is None or reference is None:
            return None
        return _format((value / reference - 1) * 100)
