"""
tests/test_margin_store.py — margin_store.py's global profit-margin API
(T1, ARCH-profit-margin).

Follows the schema_db fixture pattern established in tests/test_discount_store.py.
MarginStore accepts a dsn= constructor arg, so no pool-singleton workaround
is needed beyond resetting the shared pool between tests (same reason as
test_discount_store.py: db.pool.get_pool() is a process-wide singleton).
"""

import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from margin_store import MarginStore, MarginValueError


@pytest.fixture(autouse=True)
def _use_test_dsn(schema_db):
    import db.pool as pool_module

    pool_module.reset_pool()
    yield
    pool_module.reset_pool()


@pytest.fixture(autouse=True)
def _reset_margin_row(schema_db):
    """margin_settings is a true singleton (id=1) shared across the whole
    session-scoped schema_db — reset it to the seeded 0% before every test
    so tests don't leak state into each other (unlike discount_store's
    tests, which create a fresh station per test)."""
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE margin_settings SET margin_pct = 0, updated_by = NULL, updated_at = NULL WHERE id = 1"
            )
        conn.commit()
    yield


@pytest.fixture
def store(schema_db):
    return MarginStore(dsn=schema_db)


# ============================================================
# Margin CRUD
# ============================================================

def test_defaults_to_zero_when_never_set(store):
    assert store.get() == 0.0


def test_set_then_get_round_trips_at_two_decimal_precision(store):
    store.set(12.25, actor="admin", reason="quarterly review")
    assert store.get() == 12.25


def test_set_records_actor_and_reason_and_updated_at(store, schema_db):
    store.set(12.25, actor="admin", reason="quarterly review")
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT updated_by, updated_at FROM margin_settings WHERE id = 1")
            updated_by, updated_at = cur.fetchone()
    assert updated_by == "admin"
    assert updated_at is not None


def test_accepts_boundary_values_zero_and_hundred(store):
    store.set(0)
    assert store.get() == 0.0
    store.set(100)
    assert store.get() == 100.0


# ============================================================
# Margin Validation
# ============================================================

def test_rejects_third_decimal_place(store):
    with pytest.raises(MarginValueError):
        store.set(12.255)


def test_rejects_out_of_range_values(store):
    with pytest.raises(MarginValueError):
        store.set(-1)
    with pytest.raises(MarginValueError):
        store.set(101)


def test_rejects_non_numeric_input(store):
    with pytest.raises(MarginValueError):
        store.set("abc")


# ============================================================
# Apply Transform
# ============================================================

def test_apply_passthrough_when_exempt():
    assert MarginStore.apply(10.0, 12.25, exempt=True) == 10.0


def test_apply_reduces_when_not_exempt():
    assert MarginStore.apply(10.0, 12.25, exempt=False) == round(10.0 * (1 - 0.1225), 4)


def test_apply_at_zero_margin_is_a_noop_regardless_of_exempt():
    assert MarginStore.apply(10.0, 0, exempt=False) == 10.0


# ============================================================
# Regression Guard
# ============================================================

def test_schema_migration_is_idempotent(store, schema_db):
    # An admin has already configured a non-zero margin before schema.sql
    # is re-applied (e.g. on the next deploy) — re-applying must not reset
    # the singleton row back to the seed default.
    store.set(7.5, actor="admin")

    schema_path = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
    result = subprocess.run(
        [sys.executable, "db/apply.py", str(schema_path), "--dsn", schema_db],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"

    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), MAX(margin_pct) FROM margin_settings")
            count, margin_pct = cur.fetchone()
    assert count == 1
    assert float(margin_pct) == 7.5
