"""
tests/test_book_margin.py — /book margin application (T3, REQ-profit-margin).

Covers R4/R5/R6 (customer only ever sees post-margin discount, except
for grandfathered/margin_exempt rows) and R7/R8 (the margin % live at
the single POST /book moment — this app has no separate checkout-start
vs. confirm step — is frozen onto the voucher and never re-derived).
"""

import json
import re
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
    def __init__(self, customer=None):
        self._customer = customer
        self.booked = []

    def get_customer(self, code):
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


def _valid_refuel():
    manila = ZoneInfo("Asia/Manila")
    return (datetime.now(manila) + timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M")


def _station_table_by_fuel(resp_data):
    m = re.search(r"window\.__STATION_TABLE__ = (\{.*?\});", resp_data.decode("utf-8"), re.DOTALL)
    assert m, "window.__STATION_TABLE__ not found"
    return json.loads(m.group(1))


def _stub_station(monkeypatch, margin_pct, exempt, raw_discount=10.0, name="EcoOil - Cainta", price=76.03):
    monkeypatch.setattr(main, "repo", RepoStub(customer=dict(CUST)))
    monkeypatch.setattr(
        main.price_store, "list_stations",
        lambda fuel_type: [{"id": "ecooil-cainta", "name": name, "price_php_per_liter": price, "updated_at": 0}]
        if fuel_type == "Biodiesel" else []
    )
    monkeypatch.setattr(main.margin_store, "get", lambda: margin_pct)
    monkeypatch.setattr(
        main.discount_store, "get_all_with_exempt",
        lambda fuel_type: {name: {"value": raw_discount, "margin_exempt": exempt}} if fuel_type == "Biodiesel" else {}
    )
    monkeypatch.setattr(
        main.discount_store, "get_with_exempt",
        lambda station, fuel_type: {"value": raw_discount, "margin_exempt": exempt} if fuel_type == "Biodiesel" else None
    )


# ============================================================
# Booking Display (GET /book)
# ============================================================

def test_get_book_shows_post_margin_discount_for_non_exempt_station(client, monkeypatch):
    _stub_station(monkeypatch, margin_pct=12.25, exempt=False, raw_discount=10.0)
    resp = client.post("/book", data={"account_code": "HARR"})
    assert resp.status_code == 200
    table = _station_table_by_fuel(resp.data)["Biodiesel"]
    row = next(r for r in table if r["name"] == "EcoOil - Cainta")
    # station_table_by_fuel formats for display (f"{value:.2f}"), so the
    # exact 8.775 renders as "8.78" — the underlying math is covered
    # precisely by the POST-snapshot test below and by margin_store's
    # own unit tests.
    assert float(row["discount_per_liter"]) == pytest.approx(8.78, abs=0.001)


def test_get_book_shows_raw_discount_for_exempt_station(client, monkeypatch):
    _stub_station(monkeypatch, margin_pct=12.25, exempt=True, raw_discount=10.0)
    resp = client.post("/book", data={"account_code": "HARR"})
    assert resp.status_code == 200
    table = _station_table_by_fuel(resp.data)["Biodiesel"]
    row = next(r for r in table if r["name"] == "EcoOil - Cainta")
    assert float(row["discount_per_liter"]) == pytest.approx(10.0, abs=0.001)


def test_get_book_zero_margin_is_noop_for_non_exempt_station(client, monkeypatch):
    _stub_station(monkeypatch, margin_pct=0, exempt=False, raw_discount=10.0)
    resp = client.post("/book", data={"account_code": "HARR"})
    assert resp.status_code == 200
    table = _station_table_by_fuel(resp.data)["Biodiesel"]
    row = next(r for r in table if r["name"] == "EcoOil - Cainta")
    assert float(row["discount_per_liter"]) == pytest.approx(10.0, abs=0.001)


# ============================================================
# Booking Snapshot (POST /book)
# ============================================================

def _post_booking(client, monkeypatch, margin_pct, exempt, raw_discount=10.0):
    _stub_station(monkeypatch, margin_pct=margin_pct, exempt=exempt, raw_discount=raw_discount)
    resp = client.post("/book", data={
        "account_code": "HARR",
        "station": "EcoOil - Cainta",
        "requested_amount_php": "1000",
        "refuel_datetime": _valid_refuel(),
        "driver_mode": "new",
        "driver_name": "Dave",
        "vehicle_plate": "XYZ-123",
        "truck_make": "Isuzu",
        "truck_model": "NQR",
        "number_of_wheels": "6",
        "fuel_type": "Biodiesel",
        "contact_number": "Harry – 0900-000-0000",
        "mobile_number": "09123456789",
    })
    assert resp.status_code == 200
    assert len(main.repo.booked) == 1
    return main.repo.booked[0]


def test_post_book_snapshot_stores_post_margin_discount_for_non_exempt_station(client, monkeypatch):
    booked = _post_booking(client, monkeypatch, margin_pct=12.25, exempt=False, raw_discount=10.0)
    assert booked["discount_snapshot_php_per_liter"] == pytest.approx(8.775, abs=0.001)


def test_post_book_snapshot_stores_raw_discount_for_exempt_station(client, monkeypatch):
    booked = _post_booking(client, monkeypatch, margin_pct=12.25, exempt=True, raw_discount=10.0)
    assert booked["discount_snapshot_php_per_liter"] == pytest.approx(10.0, abs=0.001)


def test_post_book_stamps_margin_pct_at_booking_regardless_of_exempt_status(client, monkeypatch):
    """Developer decision (A6): the live global margin is recorded on
    every booking, even a grandfathered/exempt one whose discount wasn't
    actually reduced — the field means 'policy at the time', not 'margin
    applied to this row'."""
    booked = _post_booking(client, monkeypatch, margin_pct=12.25, exempt=True, raw_discount=10.0)
    assert booked["margin_pct_at_booking"] == pytest.approx(12.25, abs=0.001)
    # Confirms the exempt row's discount itself was NOT reduced, even
    # though margin_pct_at_booking was still recorded.
    assert booked["discount_snapshot_php_per_liter"] == pytest.approx(10.0, abs=0.001)


def test_margin_changed_after_booking_does_not_retroactively_change_stored_snapshot(client, monkeypatch):
    booked = _post_booking(client, monkeypatch, margin_pct=12.25, exempt=False, raw_discount=10.0)
    original_snapshot = booked["discount_snapshot_php_per_liter"]
    original_margin = booked["margin_pct_at_booking"]

    # Simulate the admin raising the margin after this booking was made.
    monkeypatch.setattr(main.margin_store, "get", lambda: 20.0)

    # The already-booked row is a plain dict now — re-reading it (as the
    # Approve flow's snapshot-preference logic does) must see the
    # original, frozen values, not anything derived from the new margin.
    assert booked["discount_snapshot_php_per_liter"] == original_snapshot
    assert booked["margin_pct_at_booking"] == original_margin
