"""
db/pool.py — shared Postgres connection pool singleton.

F2.3 of the UniFleet v2 → Railway + Postgres migration. The sidecar
stores (price_store, discount_store) and the PostgresRepo class
(F2.2) all need a connection pool pointing at the same DSN. This
module exposes a single `get_pool()` factory that lazily constructs
a `psycopg_pool.ConnectionPool` and reuses it for the process
lifetime.

The DSN is read from the DATABASE_URL or UNIFLEET_DB_DSN env var.
For tests, you can pass a DSN directly via the `dsn` parameter.
"""

import os
import threading
from typing import Optional

from psycopg_pool import ConnectionPool


_pool: Optional[ConnectionPool] = None
_pool_lock = threading.Lock()

# How long pool construction may block waiting for its first connection.
#
# psycopg_pool's wait() takes its own timeout, which defaults to 30s and is
# independent of the pool's timeout= setting. Thirty seconds is far too long
# here: get_pool() is reached from inside request handlers (notifications
# enqueues from /register, /book and the approve flow), and with
# gunicorn --workers 1 a single cold call against an unreachable database
# stalls the entire app for that whole time before anything raises.
#
# Five seconds is generous for a healthy local or same-region pool and short
# enough that a customer never notices a dead database. Raise
# UNIFLEET_POOL_WAIT_SECONDS if an environment genuinely needs longer.
DEFAULT_WAIT_SECONDS = 5.0


def _wait_seconds() -> float:
    """Read the bound at call time, so the environment can be changed without
    re-importing the module."""
    raw = os.environ.get("UNIFLEET_POOL_WAIT_SECONDS", "")
    try:
        value = float(raw)
        return value if value > 0 else DEFAULT_WAIT_SECONDS
    except (TypeError, ValueError):
        return DEFAULT_WAIT_SECONDS


def get_pool(dsn: Optional[str] = None,
             min_size: int = 1,
             max_size: int = 8,
             timeout: int = 30,
             wait_timeout: Optional[float] = None) -> ConnectionPool:
    """Return the shared ConnectionPool, constructing it on first use.

    If `dsn` is None, the DSN is read from DATABASE_URL or
    UNIFLEET_DB_DSN. Calling with a non-None `dsn` after the pool
    is already constructed is a no-op (the first DSN wins) — tests
    must call `reset_pool()` if they need a different DSN between
    tests.

    `wait_timeout` bounds how long construction blocks waiting for the first
    connection; it defaults to UNIFLEET_POOL_WAIT_SECONDS or
    DEFAULT_WAIT_SECONDS. On timeout this raises PoolTimeout, as it always
    has — the only change is how quickly.
    """
    global _pool
    if _pool is not None:
        return _pool

    with _pool_lock:
        if _pool is not None:
            return _pool

        if dsn is None:
            dsn = os.environ.get("DATABASE_URL") or os.environ.get("UNIFLEET_DB_DSN")
        if not dsn:
            raise ValueError(
                "db.get_pool() requires a DSN (pass dsn= or set "
                "DATABASE_URL / UNIFLEET_DB_DSN env var)"
            )

        pool = ConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            open=False,
            timeout=timeout,
        )
        pool.open()
        try:
            pool.wait(timeout=wait_timeout if wait_timeout is not None
                      else _wait_seconds())
        except Exception:
            # psycopg_pool already closes the pool when wait() times out, so
            # this is belt-and-braces against a version that does not; close()
            # is idempotent. What actually matters here is that _pool stays
            # unset, so a failed construction is never cached and the next
            # caller gets a clean attempt once the database is back.
            pool.close()
            raise
        _pool = pool
        return _pool


def reset_pool() -> None:
    """Close and clear the shared pool. For tests / process shutdown."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None
