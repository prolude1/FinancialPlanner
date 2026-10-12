"""User-owned research candidates, kept within the backed-up ledger aggregate."""
from .stocks import parse_security_id


class ResearchWatchlistService:
    def __init__(self, ledger_service):
        self.ledger_service = ledger_service

    def read(self, principal):
        data = self.ledger_service.read(principal)
        return {"security_ids": sorted(data.get("research_watchlist", []))}

    def update(self, principal, security_id, *, remove=False):
        exchange, symbol = parse_security_id(security_id)
        canonical = f"{exchange}:{symbol}"
        transaction = self.ledger_service.transaction(principal)
        with transaction as data:
            candidates = set(data.get("research_watchlist", []))
            if remove:
                candidates.discard(canonical)
            else:
                if len(candidates) >= 100 and canonical not in candidates:
                    raise ValueError("Watchlist is limited to 100 securities")
                candidates.add(canonical)
            updated = sorted(candidates)
            if updated != data.get("research_watchlist", []):
                data["research_watchlist"] = updated
                data["revision"] += 1
        return {"security_ids": sorted(candidates)}
