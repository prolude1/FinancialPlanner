"""Background maintenance for short-lived database records."""
import logging
from threading import Event, Lock, Thread
import time

from . import store

maintenance_logger = logging.getLogger("financial_planner.maintenance")


class ExpiredWorkbookConfirmationCleaner:
    """Delete expired workbook upload previews on an independent schedule."""

    def __init__(self, repository=store, interval_seconds=60, clock=time.time, logger=None):
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.repository = repository
        self.interval_seconds = interval_seconds
        self.clock = clock
        self.logger = logger or maintenance_logger
        self._stop_event = Event()
        self._thread = None
        self._lifecycle_lock = Lock()

    def run_once(self):
        """Run one cleanup attempt; failures are retried on the next interval."""
        try:
            count = self.repository.delete_expired_workbook_confirmations(int(self.clock()))
        except Exception as exc:
            # Database exceptions can include connection details; keep the log
            # safe and let the next scheduled attempt retry the operation.
            self.logger.warning("Workbook confirmation cleanup failed error_type=%s",
                                type(exc).__name__)
            return None
        self.logger.info("Workbook confirmation cleanup completed rows=%d", count)
        return count

    def run(self, stop_event=None):
        """Run immediately, then repeat until the supplied event is set."""
        stop_event = stop_event or self._stop_event
        while not stop_event.is_set():
            self.run_once()
            if stop_event.wait(self.interval_seconds):
                return

    def start(self):
        """Start the daemon scheduler once and return its thread."""
        with self._lifecycle_lock:
            if self._thread and self._thread.is_alive():
                return self._thread
            self._stop_event.clear()
            self._thread = Thread(target=self.run, name="workbook-confirmation-cleanup", daemon=True)
            self._thread.start()
            return self._thread

    def stop(self, timeout=None):
        """Signal the scheduler and wait for its current database call to finish."""
        self._stop_event.set()
        thread = self._thread
        if thread:
            thread.join(timeout)
