"""
tests/test_customer_repo.py — unit tests for CSVRepo customer methods (CT1).

Covers create_customer / get_customer / customer_exists over
data/customers.csv. The CSVRepo tests need no Postgres — CUSTOMERS_CSV is
redirected to a temp file via monkeypatch (same data_paths module object
that persistence.py holds a reference to).

The PostgresRepo.update_customer_email tests at the end of this file DO
need a live database and use the schema_db fixture; they live here so the
two implementations of one method are read side by side (T8,
ARCH-brief-11-email-notifications).
"""

import pytest

import data_paths
from persistence import CSVRepo


SAMPLE = {
    "account_code": "HARR",
    "contact_name": "Harry",
    "contact_number": "0900-000-0000",
    "email": "harry@example.com",
    "company_name": "Harrods",
    "fleet_size": "12",
    "areas": "QC",
    "refuel_locations": "",
    "hq_locations": "",
}

CUSTOMER_KEYS = [
    "account_code", "contact_name", "contact_number", "email",
    "company_name", "fleet_size", "areas", "refuel_locations", "hq_locations",
]


@pytest.fixture
def csv_repo(tmp_path, monkeypatch):
    """CSVRepo with CUSTOMERS_CSV pointed at a temp file."""
    monkeypatch.setattr(data_paths, "CUSTOMERS_CSV", tmp_path / "customers.csv")
    return CSVRepo()


# ============================================================
# create_customer / get_customer
# ============================================================

def test_create_customer_inserts(csv_repo):
    """A created customer is retrievable with all 9 contract fields."""
    csv_repo.create_customer(dict(SAMPLE))
    got = csv_repo.get_customer("HARR")

    assert got is not None
    for k in CUSTOMER_KEYS:
        assert k in got, f"missing key {k}"
    assert got["account_code"] == "HARR"
    assert got["company_name"] == "Harrods"


def test_create_customer_upserts(csv_repo, monkeypatch):
    """Re-creating the same account_code updates in place (single row)."""
    import pandas as pd

    csv_repo.create_customer(dict(SAMPLE))
    csv_repo.create_customer({**SAMPLE, "company_name": "Harrods Ltd"})

    df = pd.read_csv(data_paths.CUSTOMERS_CSV, dtype=str).fillna("")
    n = (df["account_code"].str.strip().str.upper() == "HARR").sum()
    assert n == 1
    assert csv_repo.get_customer("HARR")["company_name"] == "Harrods Ltd"


def test_get_customer_none_when_absent(csv_repo):
    """Unknown account_code returns None."""
    assert csv_repo.get_customer("ZZZZ") is None


def test_customer_exists_true_false(csv_repo):
    """customer_exists is True for a stored code, False otherwise."""
    csv_repo.create_customer(dict(SAMPLE))
    assert csv_repo.customer_exists("HARR") is True
    assert csv_repo.customer_exists("ZZZZ") is False


def test_account_code_case_insensitive(csv_repo):
    """A code stored as ABCD resolves via abcd / AbCd."""
    csv_repo.create_customer({**SAMPLE, "account_code": "ABCD"})
    assert csv_repo.get_customer("abcd") is not None
    assert csv_repo.get_customer("AbCd")["account_code"] == "ABCD"


def test_fleet_size_coercion(csv_repo):
    """fleet_size '12' -> int 12; '' -> None."""
    csv_repo.create_customer({**SAMPLE, "account_code": "NUMC", "fleet_size": "12"})
    csv_repo.create_customer({**SAMPLE, "account_code": "BLNK", "fleet_size": ""})
    assert csv_repo.get_customer("NUMC")["fleet_size"] == 12
    assert csv_repo.get_customer("BLNK")["fleet_size"] is None


# ============================================================
# create_customer_if_absent (review fix, account_code uniqueness)
# ============================================================

def test_create_customer_if_absent_succeeds_when_free(csv_repo):
    result = csv_repo.create_customer_if_absent(dict(SAMPLE))

    assert result is not None
    assert result["account_code"] == "HARR"
    assert csv_repo.get_customer("HARR") is not None


def test_create_customer_if_absent_returns_none_on_conflict(csv_repo):
    csv_repo.create_customer(dict(SAMPLE))

    result = csv_repo.create_customer_if_absent({**SAMPLE, "company_name": "Different Co"})

    assert result is None
    # original row untouched — no silent merge
    assert csv_repo.get_customer("HARR")["company_name"] == "Harrods"


def test_create_customer_if_absent_locking_smoke_test(csv_repo):
    """Two sequential calls with the same code: second returns None,
    exactly one row exists for that code (proves the lock-acquire/
    release cycle doesn't deadlock or leak, and the check-then-write
    is correctly serialized against itself)."""
    import pandas as pd

    first = csv_repo.create_customer_if_absent(dict(SAMPLE))
    second = csv_repo.create_customer_if_absent(dict(SAMPLE))

    assert first is not None
    assert second is None
    df = pd.read_csv(data_paths.CUSTOMERS_CSV, dtype=str).fillna("")
    assert (df["account_code"].str.strip().str.upper() == "HARR").sum() == 1


# ============================================================
# list_customers (T2, ARCH-customer-details-page)
# ============================================================

def test_list_customers_returns_all_with_fleet_size_coerced(csv_repo):
    """Every stored customer is returned, fleet_size coerced to int."""
    csv_repo.create_customer({**SAMPLE, "account_code": "HARR", "fleet_size": "12"})
    csv_repo.create_customer({**SAMPLE, "account_code": "ABCD", "fleet_size": "5"})

    got = csv_repo.list_customers()

    codes = {c["account_code"] for c in got}
    assert codes == {"HARR", "ABCD"}
    fleet_sizes = {c["account_code"]: c["fleet_size"] for c in got}
    assert fleet_sizes["HARR"] == 12
    assert fleet_sizes["ABCD"] == 5


def test_list_customers_empty_returns_empty_list(csv_repo):
    """No customers stored -> []."""
    assert csv_repo.list_customers() == []


def test_list_customers_tolerates_blank_optional_fields(csv_repo):
    """A customer with blank fleet_size/areas still appears, not crashing."""
    csv_repo.create_customer({**SAMPLE, "account_code": "BLNK", "fleet_size": "", "areas": ""})

    got = csv_repo.list_customers()

    assert len(got) == 1
    assert got[0]["account_code"] == "BLNK"
    assert got[0]["fleet_size"] is None


# ============================================================
# T8 — update_customer_email (ARCH-brief-11-email-notifications)
# ============================================================
# Legacy customers registered before email became required have no address
# on file, so their bookings produce skipped notifications that can never be
# recovered. This is the method that closes that loop.

def test_update_customer_email_sets_a_missing_address(csv_repo):
    """GIVEN a customer with no email WHEN an admin sets one THEN it is
    stored (verifies R9)."""
    csv_repo.create_customer(dict(SAMPLE, email=""))

    assert csv_repo.update_customer_email("HARR", "new@example.com") is True
    assert csv_repo.get_customer("HARR")["email"] == "new@example.com"


def test_update_customer_email_leaves_other_columns_alone(csv_repo):
    """Only the address changes — this runs against live customer records."""
    csv_repo.create_customer(dict(SAMPLE))

    csv_repo.update_customer_email("HARR", "changed@example.com")
    row = csv_repo.get_customer("HARR")

    assert row["email"] == "changed@example.com"
    assert row["contact_name"] == "Harry"
    assert row["company_name"] == "Harrods"
    assert row["contact_number"] == "0900-000-0000"
    assert row["areas"] == "QC"


def test_update_customer_email_does_not_disturb_other_customers(csv_repo):
    """The CSV is rewritten wholesale, so the neighbouring rows are the thing
    most at risk (guards ARCH backward-regression risk for customers.csv)."""
    csv_repo.create_customer(dict(SAMPLE))
    csv_repo.create_customer(dict(SAMPLE, account_code="JETI", contact_name="Jet",
                                   email="jet@example.com"))

    csv_repo.update_customer_email("HARR", "changed@example.com")

    assert csv_repo.get_customer("JETI")["email"] == "jet@example.com"
    assert csv_repo.get_customer("JETI")["contact_name"] == "Jet"
    assert len(csv_repo.list_customers()) == 2


def test_update_customer_email_is_case_insensitive_on_the_code(csv_repo):
    csv_repo.create_customer(dict(SAMPLE))

    assert csv_repo.update_customer_email("harr", "lower@example.com") is True
    assert csv_repo.get_customer("HARR")["email"] == "lower@example.com"


def test_update_customer_email_returns_false_for_an_unknown_code(csv_repo):
    """An unknown code must be a no-op, not an accidental insert."""
    csv_repo.create_customer(dict(SAMPLE))

    assert csv_repo.update_customer_email("ZZZZ", "nobody@example.com") is False
    assert len(csv_repo.list_customers()) == 1


def test_update_customer_email_holds_the_lock_file(csv_repo, tmp_path, monkeypatch):
    """The CSV is dual-written by /register's own append path, so a rewrite
    that ignores the sidecar lock can lose rows (guards ARCH backward-
    regression risk for customers.csv)."""
    import persistence

    locked = []
    real_flock = persistence.fcntl.flock

    def spy_flock(fd, op):
        locked.append(op)
        return real_flock(fd, op)

    csv_repo.create_customer(dict(SAMPLE))
    monkeypatch.setattr(persistence.fcntl, "flock", spy_flock)

    csv_repo.update_customer_email("HARR", "locked@example.com")

    assert locked, "update_customer_email must serialise on the sidecar lock"
    assert (tmp_path / "customers.csv.lock").exists()


# ============================================================
# PostgresRepo.update_customer_email (T8)
# ============================================================
# These need a live database, unlike the CSVRepo tests above; they use the
# session-scoped schema_db fixture from conftest.py.

@pytest.fixture
def pg_repo(schema_db):
    import db.pool as pool_module
    from db.postgres_repo import PostgresRepo

    pool_module.reset_pool()
    repo = PostgresRepo(dsn=schema_db)
    yield repo
    with __import__("psycopg").connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM notifications")
            cur.execute("DELETE FROM customers WHERE account_code IN ('HARR', 'JETI')")
        conn.commit()
    pool_module.reset_pool()


def test_pg_update_customer_email_sets_a_missing_address(pg_repo):
    """GIVEN a Postgres customer with no email WHEN an admin sets one THEN it
    is stored (verifies R9)."""
    pg_repo.create_customer(dict(SAMPLE, email=""))

    assert pg_repo.update_customer_email("HARR", "new@example.com") is True
    assert pg_repo.get_customer("HARR")["email"] == "new@example.com"


def test_pg_update_customer_email_leaves_other_columns_alone(pg_repo):
    pg_repo.create_customer(dict(SAMPLE))

    pg_repo.update_customer_email("HARR", "changed@example.com")
    row = pg_repo.get_customer("HARR")

    assert row["email"] == "changed@example.com"
    assert row["contact_name"] == "Harry"
    assert row["company_name"] == "Harrods"


def test_pg_update_customer_email_returns_false_for_an_unknown_code(pg_repo):
    pg_repo.create_customer(dict(SAMPLE))

    assert pg_repo.update_customer_email("ZZZZ", "nobody@example.com") is False


def test_pg_update_customer_email_is_case_insensitive_on_the_code(pg_repo):
    pg_repo.create_customer(dict(SAMPLE))

    assert pg_repo.update_customer_email("harr", "lower@example.com") is True
    assert pg_repo.get_customer("HARR")["email"] == "lower@example.com"
