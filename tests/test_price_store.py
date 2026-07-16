"""
tests/test_price_store.py — price_store.py's fuel-type-aware API
(T2, ARCH-fuel-types-expansion).

Follows the schema_db fixture pattern established in tests/test_schema.py.
"""

import uuid

import psycopg
import pytest

import price_store


@pytest.fixture
def test_station(schema_db):
    """Insert a dedicated station for this test, cleaned up on teardown.
    Never touches the seeded stations/prices other test files depend on.
    """
    station_id = f"pytest_ps_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO stations (id, brand, display_name, location) VALUES (%s, %s, %s, %s)",
                (station_id, "Test", "Test Station", "Test Location"),
            )
        conn.commit()
    yield station_id
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM price_history WHERE station_id = %s", (station_id,))
            cur.execute("DELETE FROM prices WHERE station_id = %s", (station_id,))
            cur.execute("DELETE FROM stations WHERE id = %s", (station_id,))
        conn.commit()


@pytest.fixture(autouse=True)
def _use_test_dsn(schema_db, monkeypatch):
    """Point price_store's get_pool calls at the test database.

    db.pool.get_pool() is a process-wide singleton ("first DSN wins") —
    by the time this test module runs, other tests (e.g. hitting /admin
    via the Flask test client) may have already constructed it against
    the real DATABASE_URL, not schema_db's ephemeral test DB. Reset it
    before the test so price_store's own get_pool() call constructs a
    fresh pool against schema_db, then reset again after so later test
    files get a correctly-DATABASE_URL-bound pool of their own.
    """
    import db.pool as pool_module

    pool_module.reset_pool()

    def _get_pool(dsn=None, **kwargs):
        return pool_module.get_pool(dsn=schema_db, **kwargs)

    monkeypatch.setattr(price_store, "get_pool", _get_pool)
    yield
    pool_module.reset_pool()


# ============================================================
# Per-Fuel-Type Reads
# ============================================================

def test_list_stations_returns_only_stations_priced_for_fuel_type(test_station):
    price_store.set_price(test_station, "Premium", 65.0)

    result = price_store.list_stations("Premium")
    other = price_store.list_stations("Unleaded")

    ids = {s["id"] for s in result}
    assert test_station in ids
    assert test_station not in {s["id"] for s in other}


def test_get_station_returns_none_for_unpriced_combo(test_station):
    price_store.set_price(test_station, "Biodiesel", 60.0)

    result = price_store.get_station(test_station, "Unleaded")

    assert result is None


# ============================================================
# Writes
# ============================================================

def test_set_price_upserts_without_affecting_other_fuel_types(test_station):
    price_store.set_price(test_station, "Biodiesel", 60.0)
    price_store.set_price(test_station, "Premium", 65.0)

    biodiesel = price_store.get_station(test_station, "Biodiesel")
    premium = price_store.get_station(test_station, "Premium")

    assert biodiesel["price_php_per_liter"] == 60.0
    assert premium["price_php_per_liter"] == 65.0


def test_upsert_station_creates_identity_only(schema_db):
    station_id = f"pytest_ps_bare_{uuid.uuid4().hex[:8]}"
    try:
        result = price_store.upsert_station({
            "id": station_id,
            "brand": "Bare",
            "name": "Bare Station",
            "location": "Nowhere",
        })
        assert result["id"] == station_id

        # No price row was created for any fuel type
        assert price_store.get_station(station_id, "Biodiesel") is None
    finally:
        with psycopg.connect(schema_db) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM stations WHERE id = %s", (station_id,))
            conn.commit()


# ============================================================
# Edge Cases
# ============================================================

def test_station_can_go_from_zero_to_three_fuel_types_priced(test_station):
    assert price_store.get_station(test_station, "Biodiesel") is None
    assert price_store.get_station(test_station, "Premium") is None
    assert price_store.get_station(test_station, "Unleaded") is None

    price_store.set_price(test_station, "Biodiesel", 60.0)
    price_store.set_price(test_station, "Premium", 65.0)
    price_store.set_price(test_station, "Unleaded", 63.0)

    assert price_store.get_station(test_station, "Biodiesel")["price_php_per_liter"] == 60.0
    assert price_store.get_station(test_station, "Premium")["price_php_per_liter"] == 65.0
    assert price_store.get_station(test_station, "Unleaded")["price_php_per_liter"] == 63.0


# ============================================================
# Regression Guard
# ============================================================

def test_default_stations_shape_unchanged():
    """test_seeds.py cross-checks price_store._DEFAULT_STATIONS; confirm
    this task didn't change its shape."""
    assert len(price_store._DEFAULT_STATIONS) == 10
    for s in price_store._DEFAULT_STATIONS:
        assert set(s.keys()) == {"id", "brand", "name", "location", "price_php_per_liter", "updated_at"}
