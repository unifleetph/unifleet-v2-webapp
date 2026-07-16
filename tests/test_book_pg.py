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
    # F3.1 (T3): server-side validation now requires a real price for the
    # submitted (station, fuel_type) — mock one so this test keeps
    # exercising account_code passthrough, not the price-gate.
    monkeypatch.setattr(
        main.price_store, "list_stations",
        lambda fuel_type: [{"id": "teststation", "name": "Test Station",
                             "price_php_per_liter": 60.0, "updated_at": 0}]
    )
    monkeypatch.setattr(main.discount_store, "get_all", lambda fuel_type: {})
    monkeypatch.setattr(main.discount_store, "get", lambda station, fuel_type: None)

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
        "fuel_type": "Biodiesel",
        "contact_number": "Harry – 0900-000-0000",
    })

    assert resp.status_code == 200
    assert len(stub.booked) == 1
    assert stub.booked[0]["account_code"] == "HARR"


# ============================================================
# T3 (F3.1): fuel_type persistence & server-side price-existence gate
# ============================================================

def _stub_priced_station(monkeypatch, fuel_types=("Biodiesel",), name="Test Station"):
    monkeypatch.setattr(
        main.price_store, "list_stations",
        lambda fuel_type: (
            [{"id": "teststation", "name": name, "price_php_per_liter": 60.0, "updated_at": 0}]
            if fuel_type in fuel_types else []
        )
    )
    monkeypatch.setattr(main.discount_store, "get_all", lambda fuel_type: {})
    monkeypatch.setattr(main.discount_store, "get", lambda station, fuel_type: None)


def _valid_refuel():
    manila = ZoneInfo("Asia/Manila")
    return (datetime.now(manila) + timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M")


def test_booking_row_includes_fuel_type(client, monkeypatch):
    stub = RepoStub(customer=dict(CUST))
    monkeypatch.setattr(main, "repo", stub)
    _stub_priced_station(monkeypatch, fuel_types=("Premium",))

    resp = client.post("/book", data={
        "account_code": "HARR", "station": "Test Station",
        "requested_amount_php": "1000", "refuel_datetime": _valid_refuel(),
        "driver_mode": "new", "driver_name": "Dave", "vehicle_plate": "XYZ-123",
        "truck_make": "Isuzu", "truck_model": "NQR", "number_of_wheels": "6",
        "fuel_type": "Premium", "contact_number": "Harry – 0900-000-0000",
    })

    assert resp.status_code == 200
    assert len(stub.booked) == 1
    assert stub.booked[0]["fuel_type"] == "Premium"


def test_overridden_fuel_type_persists_as_submitted(client, monkeypatch, env):
    """Preset default is Unleaded; booking overrides to Premium — the
    persisted row must reflect Premium, not the preset's stored default."""
    stub = RepoStub(customer=dict(CUST))
    monkeypatch.setattr(main, "repo", stub)
    _stub_priced_station(monkeypatch, fuel_types=("Premium",))

    preset_path = data_paths.preset_csv_path("HARR")
    preset_path.write_text(
        "driver_name,vehicle_plate,truck_make,truck_model,number_of_wheels,fuel_type\n"
        "Dave,XYZ-123,Isuzu,NQR,6,Unleaded\n"
    )

    resp = client.post("/book", data={
        "account_code": "HARR", "station": "Test Station",
        "requested_amount_php": "1000", "refuel_datetime": _valid_refuel(),
        "driver_mode": "preset", "driver_select": "Dave|XYZ-123|Isuzu|NQR|6|Unleaded",
        "fuel_type": "Premium", "contact_number": "Harry – 0900-000-0000",
    })

    assert resp.status_code == 200
    assert len(stub.booked) == 1
    assert stub.booked[0]["fuel_type"] == "Premium"


def test_voucher_columns_includes_fuel_type():
    from models import VOUCHER_COLUMNS
    assert "fuel_type" in VOUCHER_COLUMNS


def test_override_does_not_mutate_preset_stored_default(client, monkeypatch, env):
    stub = RepoStub(customer=dict(CUST))
    monkeypatch.setattr(main, "repo", stub)
    _stub_priced_station(monkeypatch, fuel_types=("Premium",))

    preset_path = data_paths.preset_csv_path("HARR")
    preset_path.write_text(
        "driver_name,vehicle_plate,truck_make,truck_model,number_of_wheels,fuel_type\n"
        "Dave,XYZ-123,Isuzu,NQR,6,Unleaded\n"
    )

    client.post("/book", data={
        "account_code": "HARR", "station": "Test Station",
        "requested_amount_php": "1000", "refuel_datetime": _valid_refuel(),
        "driver_mode": "preset", "driver_select": "Dave|XYZ-123|Isuzu|NQR|6|Unleaded",
        "fuel_type": "Premium", "contact_number": "Harry – 0900-000-0000",
    })

    import pandas as pd
    after = pd.read_csv(preset_path, encoding="utf-8-sig")
    assert after.iloc[0]["fuel_type"] == "Unleaded"


def test_missing_price_for_station_fuel_type_rejects_booking(client, monkeypatch):
    stub = RepoStub(customer=dict(CUST))
    monkeypatch.setattr(main, "repo", stub)
    _stub_priced_station(monkeypatch, fuel_types=("Biodiesel",))  # no Unleaded price

    resp = client.post("/book", data={
        "account_code": "HARR", "station": "Test Station",
        "requested_amount_php": "1000", "refuel_datetime": _valid_refuel(),
        "driver_mode": "new", "driver_name": "Dave", "vehicle_plate": "XYZ-123",
        "truck_make": "Isuzu", "truck_model": "NQR", "number_of_wheels": "6",
        "fuel_type": "Unleaded", "contact_number": "Harry – 0900-000-0000",
    })

    assert resp.status_code == 200
    assert len(stub.booked) == 0
    assert b"does not have a price set" in resp.data


def test_price_present_discount_absent_still_succeeds_with_zero_discount(client, monkeypatch):
    stub = RepoStub(customer=dict(CUST))
    monkeypatch.setattr(main, "repo", stub)
    _stub_priced_station(monkeypatch, fuel_types=("Biodiesel",))  # no discount stubbed

    resp = client.post("/book", data={
        "account_code": "HARR", "station": "Test Station",
        "requested_amount_php": "1000", "refuel_datetime": _valid_refuel(),
        "driver_mode": "new", "driver_name": "Dave", "vehicle_plate": "XYZ-123",
        "truck_make": "Isuzu", "truck_model": "NQR", "number_of_wheels": "6",
        "fuel_type": "Biodiesel", "contact_number": "Harry – 0900-000-0000",
    })

    assert resp.status_code == 200
    assert len(stub.booked) == 1
    assert stub.booked[0]["discount_snapshot_php_per_liter"] == 0.0


def test_no_price_row_at_all_is_not_confused_with_zero_price(client, monkeypatch):
    """An absent station (list_stations returns []) rejects the booking,
    distinct from a station present with a legitimately low/zero price."""
    stub = RepoStub(customer=dict(CUST))
    monkeypatch.setattr(main, "repo", stub)
    monkeypatch.setattr(main.price_store, "list_stations", lambda fuel_type: [])
    monkeypatch.setattr(main.discount_store, "get_all", lambda fuel_type: {})
    monkeypatch.setattr(main.discount_store, "get", lambda station, fuel_type: None)

    resp = client.post("/book", data={
        "account_code": "HARR", "station": "Test Station",
        "requested_amount_php": "1000", "refuel_datetime": _valid_refuel(),
        "driver_mode": "new", "driver_name": "Dave", "vehicle_plate": "XYZ-123",
        "truck_make": "Isuzu", "truck_model": "NQR", "number_of_wheels": "6",
        "fuel_type": "Biodiesel", "contact_number": "Harry – 0900-000-0000",
    })

    assert resp.status_code == 200
    assert len(stub.booked) == 0


def test_blank_fuel_type_rejects_booking_no_special_casing(client, monkeypatch):
    """A blank/missing fuel_type (e.g. legacy preset with no default)
    is rejected the same as any other missing-price case — no silent
    default to 'Diesel' or 'Biodiesel'."""
    stub = RepoStub(customer=dict(CUST))
    monkeypatch.setattr(main, "repo", stub)
    _stub_priced_station(monkeypatch, fuel_types=("Biodiesel",))

    resp = client.post("/book", data={
        "account_code": "HARR", "station": "Test Station",
        "requested_amount_php": "1000", "refuel_datetime": _valid_refuel(),
        "driver_mode": "new", "driver_name": "Dave", "vehicle_plate": "XYZ-123",
        "truck_make": "Isuzu", "truck_model": "NQR", "number_of_wheels": "6",
        "fuel_type": "", "contact_number": "Harry – 0900-000-0000",
    })

    assert resp.status_code == 200
    assert len(stub.booked) == 0
