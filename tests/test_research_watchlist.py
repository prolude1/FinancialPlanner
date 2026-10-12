from contextlib import contextmanager
from copy import deepcopy

import pytest

from app.core.research_watchlist import ResearchWatchlistService
from app.core.stocks import StockProviderError


class FakeLedgers:
    def __init__(self):
        self.ledgers = {}

    def read(self, principal):
        return deepcopy(self.ledgers.setdefault(principal, {"revision": 0}))

    @contextmanager
    def transaction(self, principal):
        data = self.read(principal)
        yield data
        self.ledgers[principal] = data


def test_watchlist_is_scoped_canonical_and_idempotent():
    ledger = FakeLedgers()
    service = ResearchWatchlistService(ledger)
    assert service.update('owner-a', 'nasdaq:aapl') == {"security_ids": ["NASDAQ:AAPL"]}
    service.update('owner-a', 'NASDAQ:AAPL')
    assert ledger.read('owner-a')['revision'] == 1
    assert service.read('owner-b') == {"security_ids": []}
    service.update('owner-b', 'SGX:D05')
    assert service.read('owner-a')['security_ids'] == ['NASDAQ:AAPL']
    service.update('owner-a', 'NASDAQ:AAPL', remove=True)
    assert service.read('owner-a')['security_ids'] == []
    assert ledger.read('owner-a')['revision'] == 2


def test_watchlist_limits_and_invalid_ids():
    ledger = FakeLedgers()
    service = ResearchWatchlistService(ledger)
    with pytest.raises(StockProviderError):
        service.update('owner', '<script>')
    for index in range(100):
        service.update('owner', f'NASDAQ:A{index}')
    with pytest.raises(ValueError, match='100'):
        service.update('owner', 'NASDAQ:EXTRA')
    service.update('owner', 'NASDAQ:A0')
    service.update('owner', 'NASDAQ:A0', remove=True)
    service.update('owner', 'NASDAQ:EXTRA')
