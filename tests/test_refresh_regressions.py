from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine

from app import catalog, store, worker
from app.domain import apply, empty


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'planner.db'))
    monkeypatch.setattr(store, 'engine', engine)
    store.migrate()
    yield
    engine.dispose()


@pytest.mark.parametrize('provider', ['price', 'fx'])
@pytest.mark.parametrize('failure', ['exception', 'empty'])
def test_history_refresh_preserves_cached_rows_on_provider_failure(isolated_store, monkeypatch, provider, failure):
    today = date.today()
    state = empty()
    account = apply(state, 'account_add', {'name': 'Broker', 'type': 'brokerage'},
                    'test', 'account', today)['id']
    apply(state, 'opening_holding', {'account': account, 'exchange': 'NASDAQ',
        'symbol': 'AAPL', 'asset_class': 'equity', 'currency': 'USD',
        'quantity': '2', 'price': 'unknown', 'date': str(today)}, 'test', 'holding', today)
    with store.transaction() as saved:
        saved.clear()
        saved.update(state)
    store.replace_portfolio_history([{'account': account, 'date': str(today),
        'value_sgd': '26', 'gross_change_sgd': None, 'adjusted_change_sgd': None,
        'external_flow_sgd': '26', 'updated_at': datetime.now(timezone.utc)}])
    before = store.read_portfolio_history()
    monkeypatch.setattr(worker, 'fetch_price_history', Mock(return_value={today: Decimal('10')}))
    monkeypatch.setattr(worker, 'fetch_fx_history', Mock(return_value={today: Decimal('1.3')}))
    failed = Mock(side_effect=RuntimeError('offline')) if failure == 'exception' else Mock(return_value={})
    monkeypatch.setattr(worker, 'fetch_' + provider + '_history', failed)
    with pytest.raises((RuntimeError, ValueError)):
        worker.refresh_portfolio_history()
    assert store.read_portfolio_history() == before
    # A recovered provider can replace the snapshots normally.
    monkeypatch.setattr(worker, 'fetch_' + provider + '_history',
                        Mock(return_value={today: Decimal('20') if provider == 'price' else Decimal('2')}))
    assert worker.refresh_portfolio_history() > 0
    assert store.read_portfolio_history() != before


def test_cached_lookup_does_not_imply_complete_bulk_catalog(isolated_store):
    resolver = Mock(return_value={'currency': 'USD', 'asset_class': 'equity'})
    assert catalog.resolve('NASDAQ', 'AAPL', resolver)['symbol'] == 'AAPL'
    assert catalog.resolve('NASDAQ', 'MSFT', resolver)['symbol'] == 'MSFT'
    assert resolver.call_count == 2
    catalog.resolve('NASDAQ', 'AAPL', resolver)
    assert resolver.call_count == 2


def test_bulk_catalog_still_rejects_unknown_symbols(isolated_store):
    store.replace_catalog_source(catalog.US_SOURCE, [{
        'exchange': 'NASDAQ', 'symbol': 'AAPL', 'name': 'Apple',
        'asset_class': 'equity', 'currency': 'USD', 'native_exchange': 'NASDAQ',
        'source': catalog.US_SOURCE, 'active': True, 'updated_at': datetime.now(timezone.utc)}])
    resolver = Mock()
    with pytest.raises(ValueError, match='not found'):
        catalog.resolve('NASDAQ', 'INVALID', resolver)
    resolver.assert_not_called()
