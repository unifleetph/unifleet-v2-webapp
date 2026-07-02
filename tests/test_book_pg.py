"""
tests/test_book_pg.py — /book customer resolution via Postgres + CSV fallback (CT3).

Stubs the module-level `main.repo` so no Postgres is needed; redirects
CUSTOMERS_CSV / PRESETS_DIR to temp paths.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import data_paths
import main


CUST = {
    "account_code": "HARR",
    "company_name": "Harrods",
    "contact_name": "Harry",
    "contact_number": "0900-000-0000",
    "email": "",
    "fleet_size": 12,
    "areas": "",
    "refuel_locations": "",
    "hq_locations": "",
}


class RepoStub:
    def __init__(self, customer=None, get_error=None):
        self._customer = customer
        self._get_error = get_error
        self.booked = []

    def get_customer(self, code):
        if self._get_error:
            raise self._get_error
        return self._customer

    def customer_exists(self, code):
        return self._customer is not None

    def create_unverified_booking(self, row):
        self.booked.append(dict(row))
        return {"voucher_id": "UF-TEST-00001", **row}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(data_paths, "CUSTOMERS_CSV", tmp_path / "customers.csv")
    monkeypatch.setattr(data_paths, "PRESETS_DIR", tmp_path)
    main.app.config.update(TESTING=True)
    return tmp_path


@pytest.fixture
def client(env):
    return main.app.test_client()


def _write_customers_csv(path, code="HARR"):
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["account_code", "company_name", "contact_name", "contact_number"])
        w.writerow([code, "Harrods", "Harry", "0900-000-0000"])


# ============================================================
# Customer resolution
# ============================================================

def test_resolves_via_postgres(client, monkeypatch):
    """get_customer hit renders the welcome + prefilled contact."""
    monkeypatch.setattr(main, "repo", RepoStub(customer=dict(CUST)))
    resp = client.post("/book", data={"account_code": "HARR"})
    assert resp.status_code == 200
    assert b"Welcome, Harrods" in resp.data
    assert b"Harry" in resp.data


def test_unknown_account_renders_empty_form(client, monkeypatch):
    """No PG hit and no CSV match -> customer=None (asks for code)."""
    monkeypatch.setattr(main, "repo", RepoStub(customer=None))
    resp = client.post("/book", data={"account_code": "ZZZZ"})
    assert resp.status_code == 200
    assert b"Welcome, Harrods" not in resp.data
    assert b"Enter Your 4-Letter Account Code" in resp.data


# ============================================================
# Fallback & resilience
# ============================================================

def test_csv_fallback_on_pg_miss(client, env, monkeypatch):
    """get_customer None but code in customers.csv -> resolved via fallback."""
    _write_customers_csv(env / "customers.csv")
    monkeypatch.setattr(main, "repo", RepoStub(customer=None))
    resp = client.post("/book", data={"account_code": "HARR"})
    assert resp.status_code == 200
    assert b"Welcome, Harrods" in resp.data


def test_pg_down_falls_back_to_csv(client, env, monkeypatch):
    """get_customer raises -> CSV fallback serves the booking (no 500)."""
    _write_customers_csv(env / "customers.csv")
    monkeypatch.setattr(main, "repo", RepoStub(get_error=RuntimeError("pg down")))
    resp = client.post("/book", data={"account_code": "HARR"})
    assert resp.status_code == 200
    assert b"Welcome, Harrods" in resp.data


# ============================================================
# Regression — booking save still resolves account_code
# ============================================================

def test_booking_save_resolves_account_code(client, monkeypatch):
    """A resolved customer -> voucher save receives the account_code."""
    stub = RepoStub(customer=dict(CUST))
    monkeypatch.setattr(main, "repo", stub)

    manila = ZoneInfo("Asia/Manila")
    refuel = (datetime.now(manila) + timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M")

    resp = client.post("/book", data={
        "account_code": "HARR",
        "station": "Test Station",
        "requested_amount_php": "1000",
        "refuel_datetime": refuel,
        "driver_mode": "new",
        "driver_name": "Dave",
        "vehicle_plate": "XYZ-123",
        "truck_make": "Isuzu",
        "truck_model": "NQR",
        "number_of_wheels": "6",
        "fuel_type": "Diesel",
        "contact_number": "Harry – 0900-000-0000",
    })

    assert resp.status_code == 200
    assert len(stub.booked) == 1
    assert stub.booked[0]["account_code"] == "HARR"
