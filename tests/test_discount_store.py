"""
tests/test_discount_store.py — discount_store.py's fuel-type-aware API
(T2, ARCH-fuel-types-expansion).

Follows the schema_db fixture pattern established in tests/test_schema.py.
DiscountStore already accepts a dsn= constructor arg, so no pool-singleton
workaround is needed here (unlike price_store.py's module-level functions).
"""

import uuid

import psycopg
import pytest

from discount_store import DiscountStore


@pytest.fixture(autouse=True)
def _use_test_dsn(schema_db):
    """db.pool.get_pool() is a process-wide singleton ("first DSN wins") —
    by the time this test module runs, other tests may have already
    constructed it against a different DSN, making DiscountStore's own
    dsn= constructor arg a no-op (it also calls the shared get_pool()
    internally). Reset around each test so this file's DiscountStore
    instances really do talk to schema_db, not whatever DSN won first.
    """
    import db.pool as pool_module

    pool_module.reset_pool()
    yield
    pool_module.reset_pool()


@pytest.fixture
def store(schema_db):
    return DiscountStore(dsn=schema_db)


@pytest.fixture
def test_station(schema_db):
    """Insert a dedicated station for this test, cleaned up on teardown."""
    station_id = f"pytest_ds_{uuid.uuid4().hex[:8]}"
    display_name = f"Pytest Station {station_id}"
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO stations (id, brand, display_name, location) VALUES (%s, %s, %s, %s)",
                (station_id, "Test", display_name, "Test Location"),
            )
        conn.commit()
    yield {"id": station_id, "name": display_name}
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM discount_history WHERE station_id = %s", (station_id,))
            cur.execute("DELETE FROM discounts WHERE station_id = %s", (station_id,))
            cur.execute("DELETE FROM stations WHERE id = %s", (station_id,))
        conn.commit()


# ============================================================
# Per-Fuel-Type Reads
# ============================================================

def test_get_all_returns_only_given_fuel_types_discounts(store, test_station):
    store.set(test_station["name"], "Biodiesel", 2.0, actor="test", reason="setup")
    store.set(test_station["name"], "Unleaded", 3.5, actor="test", reason="setup")

    biodiesel_discounts = store.get_all("Biodiesel")
    unleaded_discounts = store.get_all("Unleaded")

    assert biodiesel_discounts.get(test_station["name"]) == 2.0
    assert unleaded_discounts.get(test_station["name"]) == 3.5


def test_get_returns_none_when_no_discount_row(store, test_station):
    result = store.get(test_station["name"], "Premium")
    assert result is None


# ============================================================
# Writes
# ============================================================

def test_set_upserts_and_appends_history_with_fuel_type(store, test_station, schema_db):
    store.set(test_station["name"], "Premium", 2.5, actor="tester", reason="manual")

    assert store.get(test_station["name"], "Premium") == 2.5

    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT fuel_type, new_discount_per_liter FROM discount_history "
                "WHERE station_id = %s ORDER BY id DESC LIMIT 1",
                (test_station["id"],),
            )
            row = cur.fetchone()
    assert row[0] == "Premium"
    assert float(row[1]) == 2.5


def test_set_many_applies_per_update_fuel_type_atomically(store, test_station):
    store.set_many([
        {"station": test_station["name"], "fuel_type": "Biodiesel", "value": 1.5},
        {"station": test_station["name"], "fuel_type": "Premium", "value": 2.0},
    ], actor="tester", reason="bulk")

    assert store.get(test_station["name"], "Biodiesel") == 1.5
    assert store.get(test_station["name"], "Premium") == 2.0


# ============================================================
# Edge Cases
# ============================================================

def test_station_can_have_independent_discounts_per_fuel_type(store, test_station):
    store.set(test_station["name"], "Biodiesel", 1.0, actor="t", reason="r")
    store.set(test_station["name"], "Premium", 2.0, actor="t", reason="r")
    store.set(test_station["name"], "Unleaded", 3.0, actor="t", reason="r")

    assert store.get(test_station["name"], "Biodiesel") == 1.0
    assert store.get(test_station["name"], "Premium") == 2.0
    assert store.get(test_station["name"], "Unleaded") == 3.0


# ============================================================
# get_all_with_updated_at (T7, F3.1)
# ============================================================

def test_get_all_with_updated_at_includes_value_and_timestamp(store, test_station):
    store.set(test_station["name"], "Biodiesel", 2.0, actor="t", reason="r")

    result = store.get_all_with_updated_at("Biodiesel")

    entry = result[test_station["name"]]
    assert entry["value"] == 2.0
    assert entry["updated_at"] > 0


def test_get_all_with_updated_at_omits_stations_without_a_row(store, test_station):
    result = store.get_all_with_updated_at("Premium")
    assert test_station["name"] not in result


# ============================================================
# Regression Guard
# ============================================================

def test_clear_all_removes_discounts_across_all_fuel_types(store, test_station):
    store.set(test_station["name"], "Biodiesel", 1.0, actor="t", reason="r")
    store.set(test_station["name"], "Premium", 2.0, actor="t", reason="r")

    store.clear_all(actor="t", reason="clear")

    assert store.get(test_station["name"], "Biodiesel") is None
    assert store.get(test_station["name"], "Premium") is None


def _margin_exempt(schema_db, station_id, fuel_type):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT margin_exempt FROM discounts WHERE station_id = %s AND fuel_type = %s",
                (station_id, fuel_type),
            )
            row = cur.fetchone()
    return row[0] if row else None


# ============================================================
# Grandfather Flag on Write (T2, REQ-profit-margin)
# ============================================================

def test_new_set_insert_marks_margin_exempt_false(store, test_station, schema_db):
    store.set(test_station["name"], "Biodiesel", 2.0, actor="t", reason="r")
    assert _margin_exempt(schema_db, test_station["id"], "Biodiesel") is False


def test_existing_row_set_update_leaves_margin_exempt_untouched(store, test_station, schema_db):
    store.set(test_station["name"], "Biodiesel", 2.0, actor="t", reason="r")
    # Simulate a pre-existing (grandfathered) row by flipping the flag directly,
    # the way the migration's DEFAULT TRUE backfill would have set it.
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE discounts SET margin_exempt = TRUE WHERE station_id = %s AND fuel_type = %s",
                (test_station["id"], "Biodiesel"),
            )
        conn.commit()

    store.set(test_station["name"], "Biodiesel", 3.5, actor="t", reason="edit")

    assert _margin_exempt(schema_db, test_station["id"], "Biodiesel") is True


def test_repeated_edits_never_flip_margin_exempt(store, test_station, schema_db):
    store.set(test_station["name"], "Biodiesel", 1.0, actor="t", reason="r")
    assert _margin_exempt(schema_db, test_station["id"], "Biodiesel") is False

    for value in (2.0, 3.0, 4.0):
        store.set(test_station["name"], "Biodiesel", value, actor="t", reason="r")
        assert _margin_exempt(schema_db, test_station["id"], "Biodiesel") is False


def test_set_many_new_row_insert_marks_margin_exempt_false(store, test_station, schema_db):
    store.set_many([
        {"station": test_station["name"], "fuel_type": "Premium", "value": 2.0},
    ], actor="t", reason="bulk")
    assert _margin_exempt(schema_db, test_station["id"], "Premium") is False


def test_set_many_existing_row_update_leaves_margin_exempt_untouched(store, test_station, schema_db):
    store.set(test_station["name"], "Premium", 2.0, actor="t", reason="r")
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE discounts SET margin_exempt = TRUE WHERE station_id = %s AND fuel_type = %s",
                (test_station["id"], "Premium"),
            )
        conn.commit()

    store.set_many([
        {"station": test_station["name"], "fuel_type": "Premium", "value": 3.0},
    ], actor="t", reason="bulk edit")

    assert _margin_exempt(schema_db, test_station["id"], "Premium") is True


def test_column_default_backfills_pre_existing_style_row_as_exempt(test_station, schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO discounts (station_id, fuel_type, discount_per_liter) VALUES (%s, %s, %s)",
                (test_station["id"], "Unleaded", 1.5),
            )
        conn.commit()
    assert _margin_exempt(schema_db, test_station["id"], "Unleaded") is True


# ============================================================
# Exempt-Aware Reads (T2, REQ-profit-margin)
# ============================================================

def test_get_all_with_exempt_returns_correct_flags_for_mixed_rows(store, test_station, schema_db):
    store.set(test_station["name"], "Biodiesel", 1.0, actor="t", reason="r")  # margin_exempt=False
    store.set(test_station["name"], "Premium", 2.0, actor="t", reason="r")
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE discounts SET margin_exempt = TRUE WHERE station_id = %s AND fuel_type = %s",
                (test_station["id"], "Premium"),
            )
        conn.commit()

    biodiesel = store.get_all_with_exempt("Biodiesel")
    premium = store.get_all_with_exempt("Premium")

    assert biodiesel[test_station["name"]]["value"] == 1.0
    assert biodiesel[test_station["name"]]["margin_exempt"] is False
    assert premium[test_station["name"]]["value"] == 2.0
    assert premium[test_station["name"]]["margin_exempt"] is True


def test_get_with_exempt_returns_value_and_flag(store, test_station):
    store.set(test_station["name"], "Biodiesel", 1.0, actor="t", reason="r")
    result = store.get_with_exempt(test_station["name"], "Biodiesel")
    assert result == {"value": 1.0, "margin_exempt": False}


def test_get_with_exempt_returns_none_when_no_discount_row(store, test_station):
    assert store.get_with_exempt(test_station["name"], "Premium") is None


# ============================================================
# Regression Guard — clear_all() unaffected by the new column
# ============================================================

def test_clear_all_removes_both_exempt_and_non_exempt_rows(store, test_station, schema_db):
    store.set(test_station["name"], "Biodiesel", 1.0, actor="t", reason="r")
    store.set(test_station["name"], "Premium", 2.0, actor="t", reason="r")
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE discounts SET margin_exempt = TRUE WHERE station_id = %s AND fuel_type = %s",
                (test_station["id"], "Premium"),
            )
        conn.commit()

    store.clear_all(actor="t", reason="clear")

    assert store.get(test_station["name"], "Biodiesel") is None
    assert store.get(test_station["name"], "Premium") is None
