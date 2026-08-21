"""
price_store.py — Postgres-backed station price store.

F2.3 of the UniFleet v2 → Railway + Postgres migration. Replaces the
JSON-on-disk implementation with a thin wrapper over the F2.1
schema's `stations` + `prices` + `price_history` tables.

F3.1 (fuel-types-expansion): prices are now keyed per (station_id,
fuel_type) instead of per station alone, so list_stations/get_station/
set_price all take a required `fuel_type` argument. This is a
deliberate breaking change to the "Public API (unchanged)" contract
promised by F2.3 — every call site in main.py was updated alongside
this module. `upsert_station()` no longer touches pricing at all;
station identity and per-fuel-type price are separate concerns now
(a station can exist with 0-3 fuel types priced independently — see
set_price).

Public API (F3.1):
  init_if_missing()             no-op in PG (data is seeded by F2.1)
  load_all(fuel_type)           {"stations": [...]}  (back-compat shim)
  save_all(obj)                 no-op in PG (use set_price / upsert_station)
  list_stations(fuel_type)      [Station dicts with id, brand, name, location,
                                 price_php_per_liter, updated_at (epoch int)]
                                 — only stations with a price row for this
                                 fuel_type are returned.
  get_station(id, fuel_type)    Single station dict, or None if no price
                                 row exists for this (station, fuel_type).
  set_price(id, fuel_type, price)  Updates price; appends to price_history
  upsert_station(st)            Inserts/updates station identity only
                                 (id, brand, name, location) — no price.

The `_DEFAULT_STATIONS` constant is preserved (consumed by the F2.1
seed file and by tests/test_seeds.py for cross-validation).
"""

import os
import re
import time
from typing import List, Dict, Any, Optional

import psycopg
from psycopg.rows import dict_row

from db.pool import get_pool


# ============================================================
# Default stations (preserved for F2.1 seed + test cross-check)
# ============================================================
_DEFAULT_STATIONS = [
    {
      "id": "cleanfuel_valenzuela",
      "brand": "Cleanfuel",
      "name": "Cleanfuel – Valenzuela",
      "location": "NLEX Southbound",
      "price_php_per_liter": 60.0,
      "updated_at": 1756654640
    },
    {
      "id": "unioil_mandaluyong",
      "brand": "Unioil",
      "name": "Unioil – Mandaluyong",
      "location": "EDSA",
      "price_php_per_liter": 59.1,
      "updated_at": 0
    },
    {
      "id": "seaoil_bicutan",
      "brand": "Seaoil",
      "name": "Seaoil – Bicutan",
      "location": "SLEX Northbound",
      "price_php_per_liter": 58.9,
      "updated_at": 0
    },
    {
      "id": "ecooil_qc",
      "brand": "EcoOil",
      "name": "EcoOil – QC",
      "location": "Commonwealth",
      "price_php_per_liter": 58.3,
      "updated_at": 0
    },
    {
      "id": "maximumfuel_val",
      "brand": "Maximum Fuel",
      "name": "Maximum Fuel – Valenzuela",
      "location": "Punturin",
      "price_php_per_liter": 57.95,
      "updated_at": 0
    },
    {
      "id": "phoenix_meyc",
      "brand": "Phoenix",
      "name": "Phoenix – Meycauayan",
      "location": "NLEX",
      "price_php_per_liter": 58.2,
      "updated_at": 0
    },
    {
      "id": "petro_gsanj",
      "brand": "Petro G",
      "name": "Petro G – San Jose",
      "location": "Bulacan",
      "price_php_per_liter": 58.0,
      "updated_at": 0
    },
    {
      "id": "gazz_binan",
      "brand": "Gazz",
      "name": "Gazz – Biñan",
      "location": "SLEX Southbound",
      "price_php_per_liter": 57.8,
      "updated_at": 0
    },
    {
      "id": "filoil_stamesa",
      "brand": "FilOil",
      "name": "FilOil – Sta. Mesa",
      "location": "Manila",
      "price_php_per_liter": 59.4,
      "updated_at": 0
    },
    {
      "id": "petron_port",
      "brand": "Petron",
      "name": "Petron – Port Area",
      "location": "Port of Manila",
      "price_php_per_liter": 59.9,
      "updated_at": 0
    }
]


# ============================================================
# Public API
# ============================================================

def init_if_missing() -> None:
    """No-op in PG. Stations are seeded by F2.1's db/seed_stations.sql.
    Kept as a no-op for back-compat with the JSON-era import-time call
    in main.py:105 (`price_store.init_if_missing()`).
    """
    return None


def load_all(fuel_type: str) -> Dict[str, Any]:
    """Return the whole data structure in legacy shape: {"stations": [...]}."""
    return {"stations": list_stations(fuel_type)}


def save_all(obj: Dict[str, Any]) -> None:
    """No-op in PG. The JSON-era callers wrote the whole blob in one
    shot, but in PG we use the targeted setters (set_price,
    upsert_station) so there's no equivalent operation. Kept as a
    no-op for back-compat with any code that still calls it.
    """
    return None


def list_stations(fuel_type: str, include_inactive: bool = False) -> List[Dict[str, Any]]:
    """Return stations that have a price row for `fuel_type`.

    Each row: {id, brand, name, location, price_php_per_liter, updated_at}
    - `updated_at` is the price's `updated_at` converted to Unix epoch
      seconds (int), matching the legacy JSON shape so existing
      call sites like `int(s.get("updated_at", 0) or 0)` keep working.
    - Stations without a price row for this fuel_type are excluded
      entirely (availability is price-gated — see ARCH R7/R9).
    - Deactivated stations (`is_active = FALSE`) are excluded unless
      `include_inactive=True` (ARCH-station-management A4).
    """
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT
                    s.id,
                    s.brand,
                    s.display_name AS name,
                    s.location,
                    p.price_php_per_liter,
                    COALESCE(EXTRACT(EPOCH FROM p.updated_at)::BIGINT, 0) AS updated_at
                FROM stations s
                JOIN prices p ON p.station_id = s.id AND p.fuel_type = %s
                WHERE (%s OR s.is_active)
                ORDER BY s.brand, s.display_name
            """, (fuel_type, include_inactive))
            rows = cur.fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "brand": r["brand"],
            "name": r["name"],
            "location": r["location"],
            "price_php_per_liter": float(r["price_php_per_liter"]),
            "updated_at": int(r.get("updated_at") or 0),
        })
    return out


def list_all_stations(include_inactive: bool = True) -> List[Dict[str, Any]]:
    """Return all stations, identity only, no price join and no fuel
    filter — the base list for the Manage Stations page and for
    admin_prices()'s per-fuel-type overlay (ARCH-station-management A5).

    Each row: {id, brand, name, location, is_active}
    """
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT id, brand, display_name AS name, location, is_active
                FROM stations
                WHERE (%s OR is_active)
                ORDER BY brand, display_name
            """, (include_inactive,))
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_station(station_id: str, fuel_type: str) -> Optional[Dict[str, Any]]:
    """Return a single station's price for `fuel_type`, or None if no
    price row exists for this (station, fuel_type) combo."""
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT
                    s.id,
                    s.brand,
                    s.display_name AS name,
                    s.location,
                    p.price_php_per_liter,
                    COALESCE(EXTRACT(EPOCH FROM p.updated_at)::BIGINT, 0) AS updated_at
                FROM stations s
                JOIN prices p ON p.station_id = s.id AND p.fuel_type = %s
                WHERE s.id = %s
            """, (fuel_type, station_id))
            r = cur.fetchone()
    if r is None:
        return None
    return {
        "id": r["id"],
        "brand": r["brand"],
        "name": r["name"],
        "location": r["location"],
        "price_php_per_liter": float(r["price_php_per_liter"]),
        "updated_at": int(r.get("updated_at") or 0),
    }


def set_price(station_id: str, fuel_type: str, new_price: float) -> Dict[str, Any]:
    """Update a station's price for `fuel_type`; append a row to
    price_history. Creates the (station, fuel_type) price row if it
    doesn't exist yet — a station can go from 0 to 3 fuel types priced
    independently.

    Returns the updated station dict (same shape as get_station).
    Raises ValueError if the new price is out of range, KeyError if
    the station does not exist.
    """
    if new_price is None or new_price <= 0 or new_price > 200:
        raise ValueError("Unreasonable price. Must be 0 < price ≤ 200.")

    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # Read the old price for the history row (NULL if no prior price).
            cur.execute(
                "SELECT price_php_per_liter FROM prices WHERE station_id = %s AND fuel_type = %s",
                (station_id, fuel_type),
            )
            old_row = cur.fetchone()
            if old_row is None and not _station_exists(cur, station_id):
                raise KeyError(f"Station '{station_id}' not found")
            old_price = old_row["price_php_per_liter"] if old_row else None

            # UPSERT the price.
            cur.execute("""
                INSERT INTO prices (station_id, fuel_type, price_php_per_liter, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (station_id, fuel_type) DO UPDATE
                SET price_php_per_liter = EXCLUDED.price_php_per_liter,
                    updated_at = NOW()
            """, (station_id, fuel_type, round(float(new_price), 2)))

            # Append to price_history. actor_ip and user_agent are
            # NULL by default; a future revision of the caller can
            # pass them in if/when the admin UI starts sending them.
            cur.execute("""
                INSERT INTO price_history
                    (station_id, fuel_type, old_price, new_price, timestamp_iso, timestamp_unix)
                VALUES
                    (%s, %s, %s, %s, NOW(), EXTRACT(EPOCH FROM NOW())::BIGINT)
            """, (station_id, fuel_type, old_price, round(float(new_price), 2)))
        conn.commit()

    # Return the updated station dict (fresh from the DB)
    updated = get_station(station_id, fuel_type)
    assert updated is not None
    return updated


def upsert_station(st: Dict[str, Any]) -> Dict[str, Any]:
    """Add or replace a station's identity — id, brand, name, location.

    F3.1: no longer touches pricing. Use set_price() separately for
    each fuel type a station should carry a price for (0-3 of them).

    Required keys: id, brand, name, location.
    """
    required = {"id", "brand", "name", "location"}
    if not required.issubset(st.keys()):
        missing = required - set(st.keys())
        raise ValueError(f"Missing keys: {missing}")

    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO stations (id, brand, display_name, location, is_active)
                VALUES (%s, %s, %s, %s, TRUE)
                ON CONFLICT (id) DO UPDATE
                SET brand = EXCLUDED.brand,
                    display_name = EXCLUDED.display_name,
                    location = EXCLUDED.location,
                    updated_at = NOW()
            """, (st["id"], st["brand"], st["name"], st.get("location")))
        conn.commit()

    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, brand, display_name AS name, location FROM stations WHERE id = %s",
                (st["id"],),
            )
            r = cur.fetchone()
    return dict(r)


def generate_unique_station_id(brand: str, name: str) -> str:
    """Slugify `brand`+`name` into a station id, auto-suffixing with
    `-2`, `-3`, ... on collision with an existing station id
    (ARCH-station-management A2).
    """
    base = _slug(f"{brand} {name}")

    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM stations WHERE id = %s", (base,))
            if cur.fetchone() is None:
                return base

            n = 2
            while True:
                candidate = f"{base}-{n}"
                cur.execute("SELECT 1 FROM stations WHERE id = %s", (candidate,))
                if cur.fetchone() is None:
                    return candidate
                n += 1


def set_station_active(station_id: str, is_active: bool) -> Dict[str, Any]:
    """Flip a station's `is_active` flag. Raises KeyError if the
    station doesn't exist (ARCH-station-management A3/A4)."""
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                UPDATE stations
                SET is_active = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING id, brand, display_name AS name, location, is_active
            """, (is_active, station_id))
            r = cur.fetchone()
        conn.commit()
    if r is None:
        raise KeyError(f"Station '{station_id}' not found")
    return dict(r)


# ============================================================
# Internal helpers
# ============================================================


def _norm_dashes(s: str) -> str:
    s = str(s or '')
    return s.replace('—', '-').replace('–', '-').strip().lower()


def _slug(s: str) -> str:
    s = _norm_dashes(s)
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s-]+', '_', s)
    return s.strip('_')

def _station_exists(cur, station_id: str) -> bool:
    """Return True if a station row exists with this id (cheap existence check)."""
    cur.execute("SELECT 1 FROM stations WHERE id = %s", (station_id,))
    return cur.fetchone() is not None
