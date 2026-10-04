"""Periodic database backup."""
import logging
import os
import time
from typing import Callable

logger = logging.getLogger(__name__)


class DatabaseBackup:
    """Copies the database next to itself as `<path>.bak`, replacing the file atomically."""

    def __init__(self, db) -> None:
        self._db = db

    def run(self) -> None:
        """Writes the backup; an in-memory database is skipped."""
        if self._db.path == ":memory:":
            return
        dest = self._db.path + ".bak"
        self._db.backup(dest + ".tmp")
        os.replace(dest + ".tmp", dest)


class BackupSchedule:
    """Runs an action once every `interval_seconds`, polled from a housekeeping loop."""

    def __init__(self, action: Callable[[], None], interval_seconds: float,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._action   = action
        self._interval = interval_seconds
        self._clock    = clock
        self._next_at  = clock() + interval_seconds

    def tick(self) -> bool:
        """Runs the action if it is due.

        A failing action is logged and not retried until the next interval.

        Returns:
            True when the action was due (whether or not it succeeded).
        """
        now = self._clock()
        if now < self._next_at:
            return False
        self._next_at = now + self._interval
        try:
            self._action()
        except Exception:
            logger.exception("scheduled backup failed")
        return True
