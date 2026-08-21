"""
discount_store.py — Postgres-backed per-station discount store.

F2.3 of the UniFleet v2 → Railway + Postgres migration. Replaces the
JSON+CSV implementation with a thin wrapper over the F2.1 schema's
`stations` + `discounts` + `discount_history` tables.

F3.1 (fuel-types-expansion): discounts are now keyed per (station_id,
fuel_type) instead of per station alone, so get_all/get/set take a
required `fuel_type` argument, and set_many's update dicts each carry
their own fuel_type. This breaks the "Public API (unchanged)" promise
from F2.3 — every call site in main.py was updated alongside this
module. `clear_all()` is unchanged (dead code, no callers, still
clears every discount regardless of fuel type).

Public API (F3.1):
  DiscountStore(json_path=None, history_csv_path=None)  constructor
    - json_path / history_csv_path are accepted for back-compat but
      ignored; the data lives in Postgres now.
  .get_all(fuel_type)                          Dict[display_name, float]
  .get(station, fuel_type)                     Optional[float]
  .set(station, fuel_type, value, actor, reason) value=None removes the entry
  .set_many(updates, actor, reason)            bulk upsert/remove; each
                                                update dict has its own
                                                station/fuel_type/value
  .clear_all(actor, reason)                    remove all discounts (all
                                                fuel types, unchanged)

  DiscountValueError                  exception (preserved)

Concurrency: the legacy in-process `Lock` is no longer needed (the
DB provides row-level locking via the transaction). The Lock is kept
as an unused attribute to avoid breaking any caller that introspects
the instance.
"""

import os
from datetime import datetime
from threading import Lock
from typing import Dict, List, Optional, Iterable, Tuple, Any
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row

import data_paths  # F2.6: back-compat path constants now resolve via data_paths
from db.pool import get_pool


class DiscountValueError(ValueError):
    """Raised when an invalid discount value is provided."""
    pass


# Back-compat path constants; no longer used at runtime.
DEFAULT_JSON_PATH = str(data_paths.LEGACY_DISCOUNT_STORE_JSON)
DEFAULT_HISTORY_CSV_PATH = str(data_paths.LEGACY_DISCOUNT_HISTORY_CSV)
VALUE_PRECISION_DECIMALS = 4


class DiscountStore:
    """Per-station discount store, backed by the Postgres
    `discounts` and `discount_history` tables.

    The legacy `json_path` and `history_csv_path` constructor args
    are accepted but ignored. The data lives in Postgres now.
    """

    def __init__(self,
                 json_path: str = None,
                 history_csv_path: str = None,
                 dsn: Optional[str] = None):
        # Back-compat: accept the legacy paths but don't use them.
        self.json_path = json_path or DEFAULT_JSON_PATH
        self.history_csv_path = history_csv_path or DEFAULT_HISTORY_CSV_PATH
        # Kept for back-compat with callers that may introspect it.
        self._lock = Lock()
        # The DSN override is mostly for tests; production reads
        # DATABASE_URL / UNIFLEET_DB_DSN via the shared pool.
        self._dsn = dsn

    # -------------------------
    # Public API
    # -------------------------

    def get_all(self, fuel_type: str) -> Dict[str, float]:
        """Return a copy of all station -> discount_per_liter mappings
        for `fuel_type`.

        Keys are station display names (not slug ids), matching the
        legacy JSON shape that call sites in main.py expect.
        Stations without a discount row for this fuel_type are omitted.
        """
        pool = get_pool(dsn=self._dsn)
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("""
                    SELECT s.display_name AS name, d.discount_per_liter AS value
                    FROM stations s
                    JOIN discounts d ON d.station_id = s.id AND d.fuel_type = %s
                """, (fuel_type,))
                rows = cur.fetchall()
        return {r["name"]: float(r["value"]) for r in rows}

    def get_all_with_updated_at(self, fuel_type: str) -> Dict[str, Dict[str, Any]]:
        """Like get_all(), but each entry also carries updated_at (Unix
        epoch seconds, int) so callers can show a readable timestamp
        (T7, F3.1). Stations without a discount row for this fuel_type
        are omitted, same as get_all()."""
        pool = get_pool(dsn=self._dsn)
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("""
                    SELECT s.display_name AS name, d.discount_per_liter AS value,
                           EXTRACT(EPOCH FROM d.updated_at)::BIGINT AS updated_at
                    FROM stations s
                    JOIN discounts d ON d.station_id = s.id AND d.fuel_type = %s
                """, (fuel_type,))
                rows = cur.fetchall()
        return {
            r["name"]: {"value": float(r["value"]), "updated_at": int(r["updated_at"] or 0)}
            for r in rows
        }

    def get(self, station: str, fuel_type: str) -> Optional[float]:
        """Return discount for a (station, fuel_type) combo, or None if
        not set or station unknown. None means ₱0 downstream, not
        unavailability — see price_store for the availability gate."""
        key = self._normalize_station(station)
        if not key:
            return None
        pool = get_pool(dsn=self._dsn)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT d.discount_per_liter
                    FROM discounts d
                    JOIN stations s ON s.id = d.station_id
                    WHERE s.display_name = %s AND d.fuel_type = %s
                """, (key, fuel_type))
                row = cur.fetchone()
        if row is None or row[0] is None:
            return None
        return float(row[0])

    def set(self,
            station: str,
            fuel_type: str,
            discount_per_liter: Optional[float],
            actor: str = "system",
            reason: str = "") -> None:
        """Set (or clear) a station's discount for `fuel_type`.

        If `discount_per_liter` is None, the discounts row is
        removed and a history row is appended with new=NULL.
        Otherwise the discount is upserted and a history row is
        appended with the new value.
        """
        key = self._normalize_station(station)
        if not key:
            return

        # Look up the station id (or fail with KeyError if not found)
        pool = get_pool(dsn=self._dsn)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM stations WHERE display_name = %s",
                    (key,),
                )
                row = cur.fetchone()
                if row is None:
                    raise KeyError(f"Station '{key}' not found")
                station_id = row[0]

                # Read old value
                cur.execute(
                    "SELECT discount_per_liter FROM discounts WHERE station_id = %s AND fuel_type = %s",
                    (station_id, fuel_type),
                )
                old_row = cur.fetchone()
                old_val = old_row[0] if old_row else None

                if discount_per_liter is None:
                    # Remove the entry
                    cur.execute(
                        "DELETE FROM discounts WHERE station_id = %s AND fuel_type = %s",
                        (station_id, fuel_type),
                    )
                    new_val = None
                else:
                    new_val = self._validate_and_round(discount_per_liter)
                    cur.execute("""
                        INSERT INTO discounts (station_id, fuel_type, discount_per_liter, updated_at)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (station_id, fuel_type) DO UPDATE
                        SET discount_per_liter = EXCLUDED.discount_per_liter,
                            updated_at = NOW()
                    """, (station_id, fuel_type, new_val))

                # Append to discount_history
                cur.execute("""
                    INSERT INTO discount_history
                        (station_id, fuel_type, old_discount_per_liter, new_discount_per_liter,
                         timestamp_iso, actor, reason)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    station_id,
                    fuel_type,
                    old_val,
                    new_val,
                    self._now_iso(),
                    actor,
                    reason,
                ))
            conn.commit()

    def set_many(self,
                 updates: List[Dict[str, Any]],
                 actor: str = "system",
                 reason: str = "") -> None:
        """Bulk upsert/remove. Each update dict has keys `station`,
        `fuel_type`, and `value` (None removes that station's discount
        for that fuel type).

        All updates are applied in a single transaction so the
        history rows are appended atomically with the discounts
        table mutations.
        """
        if not updates:
            return

        pool = get_pool(dsn=self._dsn)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                for update in updates:
                    key = self._normalize_station(update.get("station"))
                    fuel_type = update.get("fuel_type")
                    value = update.get("value")
                    if not key or not fuel_type:
                        continue

                    cur.execute(
                        "SELECT id FROM stations WHERE display_name = %s",
                        (key,),
                    )
                    row = cur.fetchone()
                    if row is None:
                        # Skip unknown stations silently (legacy
                        # behavior: the JSON store didn't know about
                        # unknown stations either, but the schema
                        # would have raised an FK error). Log and
                        # move on.
                        continue
                    station_id = row[0]

                    cur.execute(
                        "SELECT discount_per_liter FROM discounts WHERE station_id = %s AND fuel_type = %s",
                        (station_id, fuel_type),
                    )
                    old_row = cur.fetchone()
                    old_val = old_row[0] if old_row else None

                    if value is None:
                        cur.execute(
                            "DELETE FROM discounts WHERE station_id = %s AND fuel_type = %s",
                            (station_id, fuel_type),
                        )
                        new_val = None
                    else:
                        new_val = self._validate_and_round(value)
                        cur.execute("""
                            INSERT INTO discounts (station_id, fuel_type, discount_per_liter, updated_at)
                            VALUES (%s, %s, %s, NOW())
                            ON CONFLICT (station_id, fuel_type) DO UPDATE
                            SET discount_per_liter = EXCLUDED.discount_per_liter,
                                updated_at = NOW()
                        """, (station_id, fuel_type, new_val))

                    cur.execute("""
                        INSERT INTO discount_history
                            (station_id, fuel_type, old_discount_per_liter, new_discount_per_liter,
                             timestamp_iso, actor, reason)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        station_id,
                        fuel_type,
                        old_val,
                        new_val,
                        self._now_iso(),
                        actor,
                        reason,
                    ))
            conn.commit()

    def clear_all(self,
                  actor: str = "system",
                  reason: str = "clear_all") -> None:
        """Remove all discounts. Appends one history row per cleared station."""
        pool = get_pool(dsn=self._dsn)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                # Capture all current (station_id, value) pairs, then
                # delete in one statement.
                cur.execute("""
                    SELECT station_id, discount_per_liter
                    FROM discounts
                """)
                current = cur.fetchall()
                if not current:
                    return
                cur.execute("DELETE FROM discounts")
                ts = self._now_iso()
                for station_id, val in current:
                    # new_discount_per_liter is NOT NULL in the schema; a
                    # cleared discount is recorded as 0, not NULL (pre-existing
                    # bug found and fixed here — clear_all() had zero prior
                    # test coverage and would have crashed on first real use).
                    cur.execute("""
                        INSERT INTO discount_history
                            (station_id, old_discount_per_liter, new_discount_per_liter,
                             timestamp_iso, actor, reason)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (station_id, val, 0, ts, actor, reason))
            conn.commit()

    # -------------------------
    # Internal helpers
    # -------------------------

    def _validate_and_round(self, value: float) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise DiscountValueError("discount_per_liter must be a number (float).")
        if v < 0:
            raise DiscountValueError("discount_per_liter cannot be negative.")
        return round(v, VALUE_PRECISION_DECIMALS)

    @staticmethod
    def _normalize_station(station: str) -> str:
        return (station or "").strip()

    @staticmethod
    def _now_iso() -> str:
        # Manila local time (ISO 8601 with +08:00 offset), seconds precision.
        # Matches the legacy JSON impl so the timestamp_iso column
        # is the same shape across both stores.
        return datetime.now(ZoneInfo("Asia/Manila")).isoformat(timespec="seconds")
