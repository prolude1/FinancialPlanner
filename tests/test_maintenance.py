from threading import Event, Thread

import pytest
from sqlalchemy import create_engine, delete, insert, select

from app.core import catalog, maintenance, store, worker


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "maintenance.db"))
    monkeypatch.setattr(store, "engine", engine)
    store.migrate()
    with engine.begin() as connection:
        connection.execute(delete(store.workbook_confirmations))
    yield engine
    engine.dispose()


def _confirmation(token_hash, principal, expires_at):
    return {"token_hash": token_hash, "principal": principal, "workbook_sha256": "a" * 64,
        "current_revision": 1, "current_etag": "b" * 64, "created_at": expires_at - 600,
        "expires_at": expires_at, "workbook_bytes": b"workbook"}


def test_repository_cleanup_removes_expired_across_scopes_and_is_idempotent(isolated_store):
    with store.engine.begin() as connection:
        connection.execute(insert(store.workbook_confirmations), [
            _confirmation("expired-legacy", None, 99),
            _confirmation("expired-tenant-a", "tenant-a", 99),
            _confirmation("active-tenant-b", "tenant-b", 101),
            _confirmation("at-boundary", "tenant-c", 100),
        ])

    assert store.delete_expired_workbook_confirmations(100) == 2
    assert store.delete_expired_workbook_confirmations(100) == 0
    with store.engine.connect() as connection:
        rows = connection.execute(select(store.workbook_confirmations.c.token_hash)).scalars().all()
    assert set(rows) == {"active-tenant-b", "at-boundary"}


def test_new_preview_reuses_cleanup_and_preserves_expiry_boundary(isolated_store):
    with store.engine.begin() as connection:
        connection.execute(insert(store.workbook_confirmations), [
            _confirmation("expired", None, 99), _confirmation("boundary", "tenant", 100)])
    store.create_workbook_confirmation("new", "tenant", "c" * 64, 1, "d" * 64,
        100, 700, b"new workbook", 100)
    with store.engine.connect() as connection:
        rows = connection.execute(select(store.workbook_confirmations.c.token_hash)).scalars().all()
    assert set(rows) == {"boundary", "new"}


def test_cleaner_retries_failures_and_is_stoppable():
    class Repository:
        def __init__(self):
            self.calls = 0

        def delete_expired_workbook_confirmations(self, now):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("private database details")
            return self.calls - 2

    class StopAfterThreeWaits:
        def __init__(self):
            self.waits = 0

        def is_set(self):
            return False

        def wait(self, _seconds):
            self.waits += 1
            return self.waits == 3

    class Logger:
        def __init__(self):
            self.messages = []

        def warning(self, message, *args):
            self.messages.append((message, *args))

        def info(self, message, count):
            self.messages.append((message, count))

    repository, stop_event, logger = Repository(), StopAfterThreeWaits(), Logger()
    cleaner = maintenance.ExpiredWorkbookConfirmationCleaner(
        repository, interval_seconds=60, clock=lambda: 123, logger=logger)
    cleaner.run(stop_event)
    assert repository.calls == 3
    assert logger.messages == [("Workbook confirmation cleanup failed error_type=%s", "RuntimeError"),
        ("Workbook confirmation cleanup completed rows=%d", 0),
        ("Workbook confirmation cleanup completed rows=%d", 1)]


def test_market_worker_starts_cleanup_independently_of_blocked_refresh(monkeypatch):
    from app.core import store as store_module

    refresh_started, cleanup_completed = Event(), Event()
    monkeypatch.setattr(catalog, "refresh_us_catalog", lambda: None)
    monkeypatch.setattr(store_module, "delete_expired_workbook_confirmations",
                        lambda _now: cleanup_completed.set() or 0)

    def blocked_market_refresh():
        refresh_started.set()
        assert cleanup_completed.wait(2)

    monkeypatch.setattr(worker, "refresh_all_ledgers", blocked_market_refresh)
    monkeypatch.setattr(worker.recurring_transaction_worker, "run", lambda: None)
    monkeypatch.setattr(worker, "refresh_all_portfolio_history", lambda: 0)
    monkeypatch.setattr(worker, "refresh_all_portfolio_deposit_history", lambda: 0)
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt()))

    def run_worker():
        try:
            worker.main()
        except KeyboardInterrupt:
            pass

    thread = Thread(target=run_worker)
    thread.start()
    thread.join(3)
    assert refresh_started.is_set()
    assert cleanup_completed.is_set()
    assert not thread.is_alive()
