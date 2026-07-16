"""
tests/test_customer_repo.py — unit tests for CSVRepo customer methods (CT1).

Covers create_customer / get_customer / customer_exists over
data/customers.csv. No Postgres required — CUSTOMERS_CSV is redirected
to a temp file via monkeypatch (same data_paths module object that
persistence.py holds a reference to).
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
