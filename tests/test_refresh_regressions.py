from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine

from app.core import catalog, store, worker
from app.core.domain import apply, empty


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


def test_deposit_refresh_runs_when_price_history_provider_fails(isolated_store, monkeypatch):
    today = date.today()
    state = empty()
    account = apply(state, 'account_add', {'name': 'Broker', 'type': 'brokerage'},
                    'test', 'account', today)['id']
    apply(state, 'opening_holding', {'account': account, 'exchange': 'NASDAQ',
        'symbol': 'AAPL', 'asset_class': 'equity', 'currency': 'USD',
        'quantity': '2', 'price': 'unknown', 'date': str(today)}, 'test', 'holding', today)
    apply(state, 'deposit', {'account': account, 'currency': 'SGD', 'amount': '25',
        'date': str(today)}, 'test', 'deposit', today)
    apply(state, 'deposit', {'account': account, 'currency': 'USD', 'amount': '10',
        'date': str(today)}, 'test', 'deposit-usd', today)
    with store.transaction() as saved:
        saved.clear()
        saved.update(state)
    valuation = [{'account': account, 'date': str(today), 'value_sgd': '100',
        'gross_change_sgd': None, 'adjusted_change_sgd': None, 'external_flow_sgd': '0',
        'updated_at': datetime.now(timezone.utc)}]
    store.replace_portfolio_history(valuation)
    store.replace_portfolio_deposit_history([{'account': account, 'date': 'old-date',
        'amount_sgd': '1', 'missing_fx_count': 0}])

    monkeypatch.setattr(worker, 'fetch_price_history', Mock(side_effect=RuntimeError('private payload')))
    monkeypatch.setattr(worker, 'fetch_fx_history', Mock(side_effect=RuntimeError('private fx payload')))
    assert worker.refresh_all_portfolio_deposit_history() == 1
    assert store.read_portfolio_deposit_history()[account] == [{'date': str(today),
        'amount_sgd': '25', 'missing_fx_count': 1}]
    before = store.read_portfolio_history()
    assert worker.refresh_all_portfolio_history() == 0
    assert store.read_portfolio_history() == before


def test_deposit_refresh_is_tenant_scoped(isolated_store):
    rows = [{'account': 'account-a', 'date': '2026-01-01', 'amount_sgd': '12',
             'missing_fx_count': 0}]
    store.replace_portfolio_deposit_history(rows, principal='tenant-a')
    assert store.read_portfolio_deposit_history('tenant-a')['account-a'][0]['amount_sgd'] == '12'
    assert store.read_portfolio_deposit_history('tenant-b') == {}
    assert store.read_portfolio_deposit_history() == {}


def test_deposit_refresh_fetches_prior_week_fx_for_first_day(isolated_store, monkeypatch):
    state = empty()
    account = apply(state, 'account_add', {'name': 'Broker', 'type': 'brokerage'},
                    'test', 'account', date(2026, 1, 2))['id']
    apply(state, 'deposit', {'account': account, 'currency': 'USD', 'amount': '10',
        'date': '2026-01-01'}, 'test', 'deposit', date(2026, 1, 2))
    with store.transaction() as saved:
        saved.clear()
        saved.update(state)
    fetched = Mock(return_value={date(2025, 12, 31): Decimal('1.5')})
    monkeypatch.setattr(worker, 'fetch_fx_history', fetched)
    assert worker.refresh_portfolio_deposit_history() == 1
    assert fetched.call_args.args[1] == date(2025, 12, 25)
    assert store.read_portfolio_deposit_history()[account][0]['amount_sgd'] == '15.0'


def test_worker_failure_log_has_safe_context_without_exception_message(caplog):
    caplog.set_level('INFO', logger='financial_planner.worker')
    principals = ['private-user-identifier', 'another-private-identifier']
    refreshed = []
    def fail_first(principal):
        if principal == principals[0]:
            raise RuntimeError('password=do-not-log')
        refreshed.append(principal)
        return 2
    class Repository:
        @staticmethod
        def tenant_principals():
            return principals
        @staticmethod
        def assert_legacy_unclaimed():
            raise PermissionError()
    refresher = worker.LedgerBatchRefresher(Repository())
    assert refresher.run(fail_first, tenant_errors_are_isolated=True, job_name='test-job',
                         sum_results=True) == 2
    assert refreshed == [principals[1]]
    message = caplog.text
    assert 'job=test-job' in message
    assert 'RuntimeError' in message and 'fail' in message
    assert 'tenant_index=1' in message and 'tenants_failed=1' in message
    assert 'password=do-not-log' not in message
    assert all(principal not in message for principal in principals)


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
