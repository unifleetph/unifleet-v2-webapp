"""
tests/test_pool.py — db/pool.py's shared connection-pool singleton
(T14, ARCH-brief-11-email-notifications).

get_pool() calls pool.wait() at construction, whose own timeout argument
defaults to 30 seconds and is independent of the pool's timeout= setting. The
first caller to open the pool against an unreachable database therefore blocked
for ~30s. That matters because notifications.enqueue runs inside /register,
/book and the approve handler: with gunicorn --workers 1, one such request
stalls the whole app for half a minute before the failure is swallowed.

This file is the only direct coverage of db/pool.py, which every
Postgres-touching module in the codebase shares — the regression guards below
matter as much as the fast-failure tests.
"""

import time

import psycopg
import pytest
from psycopg_pool import PoolTimeout

import db.pool as pool_module


DEAD_DSN = "postgresql://nobody:nobody@127.0.0.1:1/nonexistent?connect_timeout=1"


@pytest.fixture(autouse=True)
def _clean_pool():
    pool_module.reset_pool()
    yield
    pool_module.reset_pool()


# ============================================================
# Fast failure
# ============================================================

def test_construction_against_a_dead_database_fails_fast():
    """GIVEN an unreachable host WHEN get_pool() is called THEN it raises
    within a few seconds rather than ~30 (verifies N1)."""
    started = time.monotonic()

    with pytest.raises(Exception):
        pool_module.get_pool(dsn=DEAD_DSN)

    elapsed = time.monotonic() - started
    assert elapsed < 10, f"get_pool blocked for {elapsed:.1f}s on a dead database"


def test_the_wait_bound_is_configurable_per_call():
    """A caller that genuinely wants to wait longer still can."""
    started = time.monotonic()

    # PoolTimeout specifically: with `Exception` this test also passes when
    # the parameter does not exist at all, because the TypeError satisfies it.
    with pytest.raises(PoolTimeout):
        pool_module.get_pool(dsn=DEAD_DSN, wait_timeout=1)

    assert time.monotonic() - started < 6


def test_the_wait_bound_is_configurable_by_environment(monkeypatch):
    """Operators can raise it without a code change if a slow environment
    needs it."""
    monkeypatch.setenv("UNIFLEET_POOL_WAIT_SECONDS", "1")
    started = time.monotonic()

    with pytest.raises(Exception):
        pool_module.get_pool(dsn=DEAD_DSN)

    assert time.monotonic() - started < 6


def test_a_failed_construction_is_not_cached():
    """A broken pool must not become the process-wide singleton — the next
    caller, after the database recovers, has to be able to build a working
    one."""
    with pytest.raises(Exception):
        pool_module.get_pool(dsn=DEAD_DSN)

    assert pool_module._pool is None


def test_a_failed_construction_leaves_no_open_pool_behind():
    """The pool from a failed construction must not be left open.

    psycopg_pool closes it itself on a wait() timeout, so this asserts the
    invariant rather than our implementation of it — get_pool keeps an
    idempotent close() as belt-and-braces against a version that does not.
    """
    constructed = []
    real_pool_cls = pool_module.ConnectionPool

    class SpyPool(real_pool_cls):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            constructed.append(self)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pool_module, "ConnectionPool", SpyPool)
        with pytest.raises(PoolTimeout):
            pool_module.get_pool(dsn=DEAD_DSN)

    assert constructed, "no pool was constructed"
    assert constructed[0].closed is True


def test_the_failure_is_a_pool_timeout():
    """Callers that already catch broadly keep working; anything catching the
    specific type keeps working too."""
    with pytest.raises(PoolTimeout):
        pool_module.get_pool(dsn=DEAD_DSN)


# ============================================================
# Regression guards — every Postgres-touching module shares this
# ============================================================

def test_a_healthy_pool_still_opens_and_serves_queries(schema_db):
    """GIVEN a reachable database WHEN get_pool() is called THEN it returns a
    usable pool (guards every module depending on db/pool.py)."""
    pool = pool_module.get_pool(dsn=schema_db)

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1


def test_the_singleton_still_holds(schema_db):
    """First DSN wins, as the docstring promises and the suite relies on."""
    first = pool_module.get_pool(dsn=schema_db)
    second = pool_module.get_pool(dsn=DEAD_DSN)

    assert first is second


def test_reset_pool_still_closes_and_clears(schema_db):
    """The whole test suite depends on this."""
    first = pool_module.get_pool(dsn=schema_db)
    pool_module.reset_pool()

    assert pool_module._pool is None
    second = pool_module.get_pool(dsn=schema_db)
    assert second is not first

    with second.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1


def test_a_missing_dsn_still_raises_value_error(monkeypatch):
    """Unchanged behaviour: a misconfiguration is not a timeout."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("UNIFLEET_DB_DSN", raising=False)

    with pytest.raises(ValueError):
        pool_module.get_pool()


def test_the_dsn_still_comes_from_the_environment(monkeypatch, schema_db):
    monkeypatch.setenv("DATABASE_URL", schema_db)

    pool = pool_module.get_pool()

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1


def test_a_caller_specific_timeout_does_not_become_the_process_default(schema_db):
    """get_pool is a first-caller-wins singleton, and its `timeout` argument
    sets the DEFAULT checkout timeout for every later consumer.

    notifications.enqueue used to pass its own 2s bound here. Because an
    enqueue runs on /register and /book, it was often the first DB touch in
    the process — silently dropping price_store, discount_store, margin_store
    and audit_log from 30s to 2s, depending on which request arrived first
    after a restart (review finding F10). A per-call bound belongs on
    pool.connection(timeout=...), not on construction.
    """
    pool = pool_module.get_pool(dsn=schema_db, timeout=2)

    assert pool.timeout == 2, "sanity: the argument really does set the default"
    assert pool_module.get_pool().timeout == 2, (
        "and every later consumer inherits it — which is why callers must not "
        "pass their own bound here"
    )
