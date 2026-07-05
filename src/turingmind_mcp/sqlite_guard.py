"""Serialize SQLite writes and retry on lock/busy errors."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Generator

logger = logging.getLogger("turingmind-mcp.sqlite-guard")

WRITE_LOCK = threading.RLock()
MAX_RETRIES = 3
BACKOFF_SECONDS = (0.1, 0.2, 0.4)


def is_sqlite_locked(exc: BaseException) -> bool:
    """True when SQLite reports a transient lock/busy condition."""
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message or "locked" in message


def commit_with_retry(conn: sqlite3.Connection) -> None:
    """Commit with exponential backoff on transient SQLite lock errors."""
    last_err: sqlite3.OperationalError | None = None
    for attempt in range(MAX_RETRIES):
        try:
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            last_err = exc
            if not is_sqlite_locked(exc) or attempt >= MAX_RETRIES - 1:
                raise
            delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
            logger.warning(
                "SQLite commit locked (attempt %s/%s), retrying in %.1fs",
                attempt + 1,
                MAX_RETRIES,
                delay,
            )
            time.sleep(delay)
    if last_err:
        raise last_err


@contextmanager
def serialized_sqlite_write() -> Generator[None, None, None]:
    """Hold the process-wide write lock for mutating API handlers."""
    with WRITE_LOCK:
        yield
