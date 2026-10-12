"""Comparable quarterly metrics, independent of the market-data adapter."""
from datetime import date
from decimal import Decimal


class QuarterlyResultsTimeline:
    METRICS = ("revenue", "profit_after_tax", "free_cash_flow")

    def build(self, rows, limit):
        ordered = sorted(rows, key=lambda row: row["period_end"], reverse=True)
        results = []
        for row in ordered[:limit]:
            current_date = date.fromisoformat(row["period_end"])
            # Allow 52/53-week fiscal calendars, without treating a missing
            # quarter as the fourth preceding available row.
            candidates = [prior for prior in ordered if 350 <=
                          (current_date - date.fromisoformat(prior["period_end"])).days <= 380]
            prior = min(candidates, key=lambda item: abs(
                (current_date - date.fromisoformat(item["period_end"])).days - 365), default=None)
            comparisons = {metric: self.compare(row.get(metric), prior.get(metric) if prior else None)
                           for metric in self.METRICS}
            results.append({**row, "comparison_period_end": prior["period_end"] if prior else None,
                            "year_over_year": comparisons})
        return results

    @staticmethod
    def compare(current, previous):
        if current is None or previous is None:
            return {"percent": None, "reason": "missing_value"}
        current, previous = Decimal(current), Decimal(previous)
        if previous <= 0:
            return {"percent": None, "reason": "non_positive_base"}
        return {"percent": format((current - previous) / previous * 100, ".2f"), "reason": None}
