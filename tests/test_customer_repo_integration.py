"""
tests/test_customer_repo_integration.py — PostgresRepo customer methods (CT1).

Covers create_customer / get_customer / customer_exists against a real
Postgres (schema_db fixture). The customers table is empty in schema_db,
so each test owns its rows; an autouse TRUNCATE keeps them isolated
(mirrors clean_vouchers in test_postgres_repo.py).
"""

import psycopg
import pytest

from db.postgres_repo import PostgresRepo


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


@pytest.fixture(autouse=True)
def clean_customers(schema_db):
    """Truncate customers before each test for isolation."""
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE customers CASCADE")
        conn.commit()
    yield


# ============================================================
# create_customer / get_customer
# ============================================================

def test_create_customer_inserts(schema_db):
    """A created customer is retrievable with all 9 contract fields."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.create_customer(dict(SAMPLE))
        got = repo.get_customer("HARR")
    finally:
        repo.close()

    assert got is not None
    for k in CUSTOMER_KEYS:
        assert k in got, f"missing key {k}"
    assert got["account_code"] == "HARR"
    assert got["company_name"] == "Harrods"


def test_create_customer_upserts(schema_db):
    """Re-creating the same account_code updates in place (single row)."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.create_customer(dict(SAMPLE))
        repo.create_customer({**SAMPLE, "company_name": "Harrods Ltd"})
        got = repo.get_customer("HARR")
    finally:
        repo.close()

    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM customers WHERE account_code = %s", ("HARR",)
            )
            n = cur.fetchone()[0]
    assert n == 1
    assert got["company_name"] == "Harrods Ltd"


def test_get_customer_none_when_absent(schema_db):
    """Unknown account_code returns None."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        assert repo.get_customer("ZZZZ") is None
    finally:
        repo.close()


def test_customer_exists_true_false(schema_db):
    """customer_exists is True for a stored code, False otherwise."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.create_customer(dict(SAMPLE))
        assert repo.customer_exists("HARR") is True
        assert repo.customer_exists("ZZZZ") is False
    finally:
        repo.close()


def test_account_code_case_insensitive(schema_db):
    """A code stored as ABCD resolves via abcd / AbCd."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.create_customer({**SAMPLE, "account_code": "ABCD"})
        assert repo.get_customer("abcd") is not None
        assert repo.get_customer("AbCd")["account_code"] == "ABCD"
    finally:
        repo.close()


def test_fleet_size_coercion(schema_db):
    """fleet_size '12' -> int 12; '' -> None."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.create_customer({**SAMPLE, "account_code": "NUMC", "fleet_size": "12"})
        repo.create_customer({**SAMPLE, "account_code": "BLNK", "fleet_size": ""})
        num = repo.get_customer("NUMC")
        blk = repo.get_customer("BLNK")
    finally:
        repo.close()
    assert num["fleet_size"] == 12
    assert blk["fleet_size"] is None
