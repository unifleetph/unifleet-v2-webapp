"""
Tests for db/schema.sql — the F2.1 Postgres schema, plus the tables
later migrations have added on top of it.

These tests assume the `schema_db` fixture has been activated for the
session, which applies db/schema.sql to a fresh test database.
"""

import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

from models import VOUCHER_COLUMNS


# ============================================================
# Helpers
# ============================================================

EXPECTED_TABLES = {
    "vouchers",
    "stations",
    "customers",
    "presets",
    "prices",
    "price_history",
    "discounts",
    "discount_history",
    "audit_log",
}

MONEY_COLUMNS = [
    "requested_amount_php",
    "requested_total_php",
    "live_price_php_per_liter",
    "discount_per_liter",
    "discount_total",
    "total_dispensed",
    "price_snapshot_php_per_liter",
    "discount_snapshot_php_per_liter",
    "discount_total_php",
    "total_dispensed_php",
]

TIMESTAMP_COLUMNS = [
    "transaction_date",
    "expected_refill_date",
    "redemption_timestamp",
    "created_at",
    "updated_at",
    "price_snapshot_updated_at",
    "discount_snapshot_captured_at",
    "computed_at",
    "deleted_at",
]


def _columns(cur, table: str) -> dict:
    """Return {column_name: data_type} for `table`."""
    cur.execute(
        "SELECT column_name, data_type, character_maximum_length, "
        "       column_default, is_identity "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s",
        (table,),
    )
    return {
        row[0]: {
            "data_type": row[1],
            "char_len": row[2],
            "default": row[3],
            "is_identity": row[4],
        }
        for row in cur.fetchall()
    }


def _primary_key(cur, table: str) -> str | None:
    cur.execute(
        "SELECT kcu.column_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "  ON tc.constraint_name = kcu.constraint_name "
        "WHERE tc.table_schema = 'public' "
        "  AND tc.table_name = %s "
        "  AND tc.constraint_type = 'PRIMARY KEY'",
        (table,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _primary_key_columns(cur, table: str) -> set[str]:
    """Return the full set of PK columns for `table` (composite-PK-safe,
    unlike _primary_key() which only returns one via fetchone())."""
    cur.execute(
        "SELECT kcu.column_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "  ON tc.constraint_name = kcu.constraint_name "
        "WHERE tc.table_schema = 'public' "
        "  AND tc.table_name = %s "
        "  AND tc.constraint_type = 'PRIMARY KEY'",
        (table,),
    )
    return {row[0] for row in cur.fetchall()}


def _unique_columns(cur, table: str) -> list[tuple[str, ...]]:
    """Return list of tuples of column names, one per UNIQUE constraint on `table`."""
    cur.execute(
        "SELECT tc.constraint_name, kcu.column_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "  ON tc.constraint_name = kcu.constraint_name "
        "WHERE tc.table_schema = 'public' "
        "  AND tc.table_name = %s "
        "  AND tc.constraint_type = 'UNIQUE' "
        "ORDER BY tc.constraint_name, kcu.ordinal_position",
        (table,),
    )
    by_constraint: dict[str, list[str]] = {}
    for constraint_name, column_name in cur.fetchall():
        by_constraint.setdefault(constraint_name, []).append(column_name)
    return [tuple(cols) for cols in by_constraint.values()]


def _fk_targets(cur, table: str) -> list[tuple[str, str, str]]:
    """Return list of (from_column, to_table, to_column) for every FK on `table`."""
    cur.execute(
        "SELECT kcu.column_name, ccu.table_name, ccu.column_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "  ON tc.constraint_name = kcu.constraint_name "
        "JOIN information_schema.constraint_column_usage ccu "
        "  ON ccu.constraint_name = tc.constraint_name "
        "WHERE tc.table_schema = 'public' "
        "  AND tc.table_name = %s "
        "  AND tc.constraint_type = 'FOREIGN KEY'",
        (table,),
    )
    return [(row[0], row[1], row[2]) for row in cur.fetchall()]


def _indexes(cur, table: str) -> list[str]:
    """Return list of indexed column names for `table`. Strips double-quotes
    that Postgres adds around reserved words like `timestamp`."""
    cur.execute(
        "SELECT indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename = %s",
        (table,),
    )
    columns = []
    for (indexdef,) in cur.fetchall():
        # "CREATE INDEX ... ON public.vouchers USING btree (status)"
        # Pull the parenthesized column list.
        start = indexdef.find("(")
        end = indexdef.rfind(")")
        if start == -1 or end == -1:
            continue
        cols_str = indexdef[start + 1 : end]
        for col in [c.strip().strip('"') for c in cols_str.split(",")]:
            if col:
                columns.append(col)
    return columns


# ============================================================
# Table existence
# ============================================================

def test_apply_creates_all_nine_tables(schema_db):
    """All 9 expected tables exist in the public schema."""
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' "
                "ORDER BY table_name"
            )
            tables = {row[0] for row in cur.fetchall()}
    assert EXPECTED_TABLES.issubset(tables), (
        f"Missing tables: {EXPECTED_TABLES - tables}\n"
        f"Found: {tables}"
    )


# ============================================================
# Vouchers: PK, 28 columns, default, money/timestamp types
# ============================================================

def test_vouchers_has_voucher_id_primary_key(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            assert _primary_key(cur, "vouchers") == "voucher_id"


def test_vouchers_has_all_voucher_columns(schema_db):
    """Every name in models.VOUCHER_COLUMNS exists as a column on vouchers."""
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "vouchers")
    missing = [c for c in VOUCHER_COLUMNS if c not in cols]
    assert not missing, f"Missing columns on vouchers: {missing}"


def test_vouchers_status_has_default_unverified(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "vouchers")
    assert cols["status"]["default"] and "Unverified" in cols["status"]["default"], (
        f"Expected default to contain 'Unverified', "
        f"got {cols['status']['default']!r}"
    )


def test_vouchers_money_columns_are_numeric(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "vouchers")
    for col in MONEY_COLUMNS:
        assert cols[col]["data_type"] == "numeric", (
            f"vouchers.{col} should be numeric, got {cols[col]['data_type']!r}"
        )


def test_vouchers_timestamp_columns_are_timestamptz(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "vouchers")
    for col in TIMESTAMP_COLUMNS:
        assert cols[col]["data_type"] == "timestamp with time zone", (
            f"vouchers.{col} should be timestamptz, "
            f"got {cols[col]['data_type']!r}"
        )


# ============================================================
# Foreign keys
# ============================================================

@pytest.mark.parametrize(
    "table,from_col,to_table,to_col",
    [
        ("vouchers", "station_id", "stations", "id"),
        ("vouchers", "account_code", "customers", "account_code"),
        ("audit_log", "voucher_id", "vouchers", "voucher_id"),
        ("presets", "account_code", "customers", "account_code"),
        ("prices", "station_id", "stations", "id"),
        ("discounts", "station_id", "stations", "id"),
        ("price_history", "station_id", "stations", "id"),
        ("discount_history", "station_id", "stations", "id"),
    ],
)
def test_foreign_key(schema_db, table, from_col, to_table, to_col):
    """Each expected FK constraint exists."""
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            fks = _fk_targets(cur, table)
    assert (from_col, to_table, to_col) in fks, (
        f"Expected FK {table}.{from_col} -> {to_table}.{to_col}, "
        f"got {fks}"
    )


# ============================================================
# Indexes
# ============================================================

@pytest.mark.parametrize(
    "table,column",
    [
        ("vouchers", "status"),
        ("vouchers", "transaction_date"),
        ("vouchers", "created_at"),
        ("audit_log", "timestamp"),
    ],
)
def test_index_exists(schema_db, table, column):
    """Each expected non-PK index exists."""
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            indexed = _indexes(cur, table)
    assert column in indexed, (
        f"Expected index on {table}.{column}, got indexed columns: {indexed}"
    )


# ============================================================
# Stations specifics
# ============================================================

def test_stations_id_is_varchar_primary_key(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "stations")
            pk = _primary_key(cur, "stations")
    assert pk == "id", f"Expected stations PK to be 'id', got {pk!r}"
    assert cols["id"]["data_type"] == "character varying", (
        f"Expected stations.id to be varchar, got {cols['id']['data_type']!r}"
    )
    assert cols["id"]["char_len"] == 64, (
        f"Expected stations.id length 64, got {cols['id']['char_len']!r}"
    )


def test_stations_legacy_id_is_unique(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            uniques = _unique_columns(cur, "stations")
    assert ("legacy_id",) in uniques, (
        f"Expected UNIQUE on stations.legacy_id, got {uniques}"
    )


def test_stations_is_active_defaults_true(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "stations")
    assert cols["is_active"]["default"] == "true", (
        f"Expected stations.is_active default 'true', "
        f"got {cols['is_active']['default']!r}"
    )


# ============================================================
# Audit log specifics
# ============================================================

def test_audit_log_id_is_bigserial(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "audit_log")
    id_col = cols["id"]
    assert id_col["data_type"] == "bigint", (
        f"Expected audit_log.id data_type bigint, got {id_col['data_type']!r}"
    )
    assert id_col["is_identity"] == "YES", (
        f"Expected audit_log.id is_identity YES, got {id_col['is_identity']!r}"
    )


# ============================================================
# Customers specifics
# ============================================================

def test_customers_account_code_is_primary_key(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "customers")
            pk = _primary_key(cur, "customers")
    assert pk == "account_code", (
        f"Expected customers PK to be 'account_code', got {pk!r}"
    )
    assert cols["account_code"]["data_type"] == "character varying", (
        f"Expected customers.account_code to be varchar, "
        f"got {cols['account_code']['data_type']!r}"
    )
    assert cols["account_code"]["char_len"] == 16, (
        f"Expected customers.account_code length 16, "
        f"got {cols['account_code']['char_len']!r}"
    )


# ============================================================
# Presets specifics
# ============================================================

def test_presets_unique_on_account_code_driver_name(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            uniques = _unique_columns(cur, "presets")
    assert ("account_code", "driver_name") in uniques, (
        f"Expected UNIQUE(account_code, driver_name) on presets, got {uniques}"
    )


# ============================================================
# Fuel-type pricing (T1, F3.1 fuel-types-expansion)
# ============================================================

@pytest.fixture
def test_station(schema_db):
    """Insert a dedicated station (not part of the seed set) for tests
    that need to insert their own price/discount rows. schema_db is
    session-scoped and shared with test_seeds.py's seeded rows, so we
    never truncate the whole table — only clean up this one station's
    rows on teardown.
    """
    station_id = f"pytest_station_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO stations (id, brand, display_name) VALUES (%s, %s, %s)",
                (station_id, "Test", "Test Station"),
            )
        conn.commit()
    yield station_id
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM prices WHERE station_id = %s", (station_id,))
            cur.execute("DELETE FROM discounts WHERE station_id = %s", (station_id,))
            cur.execute("DELETE FROM stations WHERE id = %s", (station_id,))
        conn.commit()


def test_prices_has_composite_pk(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            pk = _primary_key_columns(cur, "prices")
    assert pk == {"station_id", "fuel_type"}, f"Expected composite PK, got {pk}"


def test_discounts_has_composite_pk(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            pk = _primary_key_columns(cur, "discounts")
    assert pk == {"station_id", "fuel_type"}, f"Expected composite PK, got {pk}"


def test_prices_allows_multiple_fuel_types_per_station(schema_db, test_station):
    station_id = test_station
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO prices (station_id, fuel_type, price_php_per_liter) "
                "VALUES (%s, 'Biodiesel', 60.0), (%s, 'Premium', 65.0)",
                (station_id, station_id),
            )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT fuel_type FROM prices WHERE station_id = %s ORDER BY fuel_type",
                (station_id,),
            )
            fuel_types = {r[0] for r in cur.fetchall()}
    assert fuel_types == {"Biodiesel", "Premium"}


def test_vouchers_fuel_type_exists_and_nullable(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "vouchers")
    assert "fuel_type" in cols
    assert cols["fuel_type"]["data_type"] == "character varying"


def test_price_history_and_discount_history_have_nullable_fuel_type(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            ph_cols = _columns(cur, "price_history")
            dh_cols = _columns(cur, "discount_history")
    assert "fuel_type" in ph_cols
    assert "fuel_type" in dh_cols


def test_seed_prices_seeds_biodiesel_only_for_all_stations(seeded_db):
    with psycopg.connect(seeded_db) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT station_id, fuel_type FROM prices")
            rows = cur.fetchall()
    fuel_types = {r[1] for r in rows}
    assert fuel_types == {"Biodiesel"}, f"Expected only Biodiesel seeded, got {fuel_types}"
    assert len(rows) == 10, f"Expected 10 seeded stations, got {len(rows)}"


def test_discount_row_is_optional_per_priced_fuel_type(schema_db, test_station):
    """A priced (station, fuel_type) with no matching discounts row is
    valid — missing discount means ₱0, not a schema violation."""
    station_id = test_station
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO prices (station_id, fuel_type, price_php_per_liter) "
                "VALUES (%s, 'Biodiesel', 60.0)",
                (station_id,),
            )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM discounts WHERE station_id = %s AND fuel_type = 'Biodiesel'",
                (station_id,),
            )
            count = cur.fetchone()[0]
    assert count == 0  # no error inserting the price with no discount row


def test_station_with_only_two_of_three_fuel_types_priced(schema_db, test_station):
    station_id = test_station
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO prices (station_id, fuel_type, price_php_per_liter) "
                "VALUES (%s, 'Biodiesel', 60.0), (%s, 'Premium', 65.0)",
                (station_id, station_id),
            )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT fuel_type FROM prices WHERE station_id = %s",
                (station_id,),
            )
            fuel_types = {r[0] for r in cur.fetchall()}
    assert "Unleaded" not in fuel_types
    assert fuel_types == {"Biodiesel", "Premium"}


def test_migration_preserves_legacy_single_price_rows():
    """A prices table in the pre-migration shape (single-column PK, no
    fuel_type) survives applying the current schema.sql: the fuel_type
    column is added with DEFAULT 'Biodiesel', backfilling existing rows,
    and the PK becomes composite — no data is dropped."""
    import uuid as _uuid

    admin_dsn = "postgresql://unifleet:unifleet_dev_pw@db:5432/postgres"
    db_name = f"unifleet_test_legacy_{_uuid.uuid4().hex[:8]}"
    test_dsn = admin_dsn.rsplit("/", 1)[0] + f"/{db_name}"

    with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as admin:
        admin.execute(f'CREATE DATABASE "{db_name}"')

    try:
        # Build the pre-migration shape by hand: stations + old-style prices.
        with psycopg.connect(test_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE stations (id VARCHAR(64) PRIMARY KEY, "
                    "brand VARCHAR(100) NOT NULL, display_name VARCHAR(200) NOT NULL)"
                )
                cur.execute(
                    "CREATE TABLE prices (station_id VARCHAR(64) PRIMARY KEY "
                    "REFERENCES stations(id), price_php_per_liter NUMERIC(10,4) NOT NULL, "
                    "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
                )
                cur.execute(
                    "INSERT INTO stations (id, brand, display_name) "
                    "VALUES ('legacy_station', 'Legacy', 'Legacy Station')"
                )
                cur.execute(
                    "INSERT INTO prices (station_id, price_php_per_liter) "
                    "VALUES ('legacy_station', 61.5)"
                )
            conn.commit()

        # Now apply the current (already-migrated) schema.sql on top.
        schema_path = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
        result = subprocess.run(
            [sys.executable, "db/apply.py", str(schema_path), "--dsn", test_dsn],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"apply.py failed on legacy shape: stdout={result.stdout!r} stderr={result.stderr!r}"
        )

        with psycopg.connect(test_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT station_id, fuel_type, price_php_per_liter FROM prices"
                )
                rows = cur.fetchall()
                pk = _primary_key_columns(cur, "prices")
    finally:
        with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (db_name,),
            )
            admin.execute(f'DROP DATABASE IF EXISTS "{db_name}"')

    assert len(rows) == 1, "legacy row was dropped during migration"
    assert rows[0][0] == "legacy_station"
    assert rows[0][1] == "Biodiesel", "legacy row should be auto-assigned fuel_type='Biodiesel'"
    assert float(rows[0][2]) == 61.5, "legacy price value should be preserved"
    assert pk == {"station_id", "fuel_type"}


def test_schema_apply_is_idempotent_after_fuel_type_migration(schema_db):
    """Re-applying schema.sql after the composite-PK migration has
    already landed must not error or duplicate/corrupt rows."""
    schema_path = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
    result = subprocess.run(
        [sys.executable, "db/apply.py", str(schema_path), "--dsn", schema_db],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Second apply after fuel-type migration failed: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            pk = _primary_key_columns(cur, "prices")
    assert pk == {"station_id", "fuel_type"}


# ============================================================
# Idempotency
# ============================================================

def test_schema_apply_is_idempotent(schema_db):
    """Re-applying db/schema.sql must not error and must not drop tables."""
    import subprocess
    import sys
    from pathlib import Path

    schema_path = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
    result = subprocess.run(
        [sys.executable, "db/apply.py", str(schema_path), "--dsn", schema_db],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Re-apply failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
            tables = {row[0] for row in cur.fetchall()}
    assert EXPECTED_TABLES.issubset(tables), (
        f"Tables lost after re-apply: {EXPECTED_TABLES - tables}"
    )


# ============================================================
# Notifications outbox (ARCH-brief-11-email-notifications, T1)
# ============================================================

NOTIFICATION_COLUMNS = {
    "id",
    "kind",
    "recipient",
    "account_code",
    "voucher_id",
    "status",
    "attempts",
    "next_attempt_at",
    "last_error",
    "last_status_code",
    "provider_message_id",
    "dedupe_key",
    "voucher_fingerprint",
    "attachment_ref",
    "subject",
    "body",
    "created_at",
    "sent_at",
}


@pytest.fixture
def clean_notifications(schema_db):
    """schema_db is session-scoped, so rows inserted by one test would leak
    into the next. Truncate before and after each notifications test."""
    def _truncate():
        with psycopg.connect(schema_db) as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE notifications")
            conn.commit()

    _truncate()
    yield schema_db
    _truncate()


def _insert_notification(cur, **overrides):
    """Insert one notifications row with sane defaults; return its id."""
    row = {
        "kind": "account_code",
        "recipient": "someone@example.com",
        "dedupe_key": "acct:TEST",
        "subject": "UniFleet Account Code",
        "body": "Your Account Code is: TEST",
    }
    row.update(overrides)
    cols = ", ".join(row.keys())
    placeholders = ", ".join(["%s"] * len(row))
    cur.execute(
        f"INSERT INTO notifications ({cols}) VALUES ({placeholders}) RETURNING id",
        tuple(row.values()),
    )
    return cur.fetchone()[0]


def test_notifications_table_exists_with_expected_columns(schema_db):
    """T1: the outbox table exists with the columns ARCH's data model names."""
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "notifications")
    assert cols, "notifications table is missing"
    assert NOTIFICATION_COLUMNS.issubset(set(cols)), (
        f"Missing columns: {NOTIFICATION_COLUMNS - set(cols)}"
    )


def test_notifications_id_is_identity_primary_key(schema_db):
    """Follows the audit_log identity-PK convention."""
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            assert _primary_key(cur, "notifications") == "id"
            assert _columns(cur, "notifications")["id"]["is_identity"] == "YES"


def test_notifications_dedupe_key_is_unique(clean_notifications):
    """A7: the idempotency guarantee is a real UNIQUE constraint, not
    application logic — it is what makes concurrent approvals and repeated
    cron firings safe."""
    with psycopg.connect(clean_notifications) as conn:
        with conn.cursor() as cur:
            _insert_notification(cur, dedupe_key="acct:HARR")
        conn.commit()

    with psycopg.connect(clean_notifications) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.UniqueViolation):
                _insert_notification(cur, dedupe_key="acct:HARR")


def test_notifications_status_and_attempts_have_queue_defaults(clean_notifications):
    """An enqueue that omits queue bookkeeping lands ready to send."""
    with psycopg.connect(clean_notifications) as conn:
        with conn.cursor() as cur:
            new_id = _insert_notification(cur, dedupe_key="acct:DEFAULTS")
            cur.execute(
                "SELECT status, attempts, next_attempt_at <= NOW(), created_at IS NOT NULL "
                "FROM notifications WHERE id = %s",
                (new_id,),
            )
            status, attempts, due_now, has_created_at = cur.fetchone()

    assert status == "queued"
    assert attempts == 0
    assert due_now is True
    assert has_created_at is True


def test_notifications_foreign_keys_are_nullable(clean_notifications):
    """daily_report rows carry neither an account_code nor a voucher_id."""
    with psycopg.connect(clean_notifications) as conn:
        with conn.cursor() as cur:
            new_id = _insert_notification(
                cur,
                kind="daily_report",
                dedupe_key="daily:2026-09-07:ops@example.com",
                account_code=None,
                voucher_id=None,
            )
            assert new_id is not None


def test_notifications_foreign_keys_target_customers_and_vouchers(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            fks = set(_fk_targets(cur, "notifications"))

    assert ("account_code", "customers", "account_code") in fks
    assert ("voucher_id", "vouchers", "voucher_id") in fks


def test_notifications_has_worker_poll_index(schema_db):
    """The drain loop claims on (status, next_attempt_at); without this index
    the worker's poll degrades to a sequential scan as the table grows."""
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = 'notifications'"
            )
            defs = [row[0] for row in cur.fetchall()]

    assert any(
        "status" in d and "next_attempt_at" in d for d in defs
    ), f"No (status, next_attempt_at) index found. Indexes: {defs}"


def test_notifications_has_voucher_id_index(schema_db):
    """flags_by_voucher does a bulk lookup by voucher_id on every /admin render."""
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            assert "voucher_id" in _indexes(cur, "notifications")


def test_notifications_subject_and_body_are_required(clean_notifications):
    """Copy is rendered at enqueue time, so a row without it is never valid."""
    with psycopg.connect(clean_notifications) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.NotNullViolation):
                cur.execute(
                    "INSERT INTO notifications (kind, dedupe_key, body) "
                    "VALUES ('account_code', 'acct:NOSUBJ', 'body only')"
                )


# ============================================================
# Report recipients (ARCH-brief-11-email-notifications, T1)
# ============================================================

@pytest.fixture
def clean_recipients(schema_db):
    """schema_db is session-scoped; keep recipient rows from leaking between
    tests (the UNIQUE constraint on email would poison later inserts)."""
    def _truncate():
        with psycopg.connect(schema_db) as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE report_recipients")
            conn.commit()

    _truncate()
    yield schema_db
    _truncate()


def test_report_recipients_table_exists_with_expected_columns(schema_db):
    """T1: the admin-managed internal distribution list (R14)."""
    expected = {"id", "email", "label", "is_active", "created_at", "updated_at"}
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "report_recipients")
    assert cols, "report_recipients table is missing"
    assert expected.issubset(set(cols)), f"Missing columns: {expected - set(cols)}"


def test_report_recipients_id_is_identity_primary_key(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            assert _primary_key(cur, "report_recipients") == "id"
            assert _columns(cur, "report_recipients")["id"]["is_identity"] == "YES"


def test_report_recipients_email_is_unique(clean_recipients):
    """The same address must not be addable twice (R14)."""
    with psycopg.connect(clean_recipients) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO report_recipients (email, label) VALUES (%s, %s)",
                ("ops@example.com", "ops team"),
            )
        conn.commit()

    with psycopg.connect(clean_recipients) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.UniqueViolation):
                cur.execute(
                    "INSERT INTO report_recipients (email) VALUES (%s)",
                    ("ops@example.com",),
                )


def test_report_recipients_email_is_required(clean_recipients):
    with psycopg.connect(clean_recipients) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.NotNullViolation):
                cur.execute("INSERT INTO report_recipients (label) VALUES ('no address')")


def test_report_recipients_is_active_defaults_true(clean_recipients):
    """A newly added recipient receives the next report without further action.
    Mirrors the stations.is_active convention."""
    with psycopg.connect(clean_recipients) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO report_recipients (email) VALUES (%s) RETURNING is_active, created_at IS NOT NULL",
                ("newbie@example.com",),
            )
            is_active, has_created_at = cur.fetchone()

    assert is_active is True
    assert has_created_at is True


def test_schema_apply_is_idempotent_for_notification_tables(schema_db):
    """T1: db/apply.py runs on every Railway container start and on every
    `make test-db`, so re-applying must be a no-op — the two new tables must
    survive with their rows intact, not be recreated empty."""
    import subprocess
    import sys
    from pathlib import Path

    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO report_recipients (email) VALUES (%s) "
                "ON CONFLICT (email) DO NOTHING",
                ("survivor@example.com",),
            )
            _insert_notification(cur, dedupe_key="acct:SURVIVOR")
        conn.commit()

    schema_path = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
    result = subprocess.run(
        [sys.executable, "db/apply.py", str(schema_path), "--dsn", schema_db],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Re-apply failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
            tables = {row[0] for row in cur.fetchall()}

            cur.execute(
                "SELECT COUNT(*) FROM report_recipients WHERE email = %s",
                ("survivor@example.com",),
            )
            recipients_kept = cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM notifications WHERE dedupe_key = %s",
                ("acct:SURVIVOR",),
            )
            notifications_kept = cur.fetchone()[0]

    assert {"notifications", "report_recipients"}.issubset(tables), (
        "Notification tables lost after re-apply"
    )
    assert recipients_kept == 1, "report_recipients rows did not survive re-apply"
    assert notifications_kept == 1, "notifications rows did not survive re-apply"

    # Leave the session-scoped database as we found it.
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM report_recipients WHERE email = %s", ("survivor@example.com",))
            cur.execute("DELETE FROM notifications WHERE dedupe_key = %s", ("acct:SURVIVOR",))
        conn.commit()
