"""
tests/test_book_calculator_inversion.py — /book calculator input now means
"total fuel amount" (T), not "prepaid cash". requested_amount_php keeps its
existing meaning (amount charged); a new requested_total_php column stores
T. Also covers the ops_set_status() Approve-flow formula fix that derives
liters/discount from requested_total_php instead of requested_amount_php
(ARCH-brief-5, T2).
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
    def __init__(self, customer=None, voucher=None):
        self._customer = customer
        self.booked = []
        self._voucher = voucher
        self.updated_fields = None
        self.status_calls = []

    def get_customer(self, code):
        return self._customer

    def customer_exists(self, code):
        return self._customer is not None

    def create_unverified_booking(self, row):
        self.booked.append(dict(row))
        return {"voucher_id": "UF-TEST-00001", **row}

    def get_voucher(self, voucher_id):
        if self.updated_fields:
            merged = dict(self._voucher)
            merged.update(self.updated_fields)
            return merged
        return self._voucher

    def update_voucher_fields(self, voucher_id, fields):
        self.updated_fields = fields

    def set_status(self, voucher_id, status, ts):
        self.status_calls.append((voucher_id, status, ts))


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


def _book(client, monkeypatch, repo_stub, amount, price=76.03, discount=5.50):
    monkeypatch.setattr(main, "repo", repo_stub)
    monkeypatch.setattr(
        main.price_store, "list_stations",
        lambda fuel_type: [{"id": "ecooil-cainta", "name": "EcoOil - Cainta",
                             "price_php_per_liter": price, "updated_at": 0}]
    )
    monkeypatch.setattr(main.discount_store, "get_all", lambda fuel_type: {})
    monkeypatch.setattr(main.discount_store, "get", lambda station, fuel_type: discount)

    return client.post("/book", data={
        "account_code": "HARR",
        "station": "EcoOil - Cainta",
        "requested_amount_php": str(amount),
        "refuel_datetime": _valid_refuel(),
        "driver_mode": "new",
        "driver_name": "Dave",
        "vehicle_plate": "XYZ-123",
        "truck_make": "Isuzu",
        "truck_model": "NQR",
        "number_of_wheels": "6",
        "fuel_type": "Biodiesel",
        "contact_number": "Harry – 0900-000-0000",
    })


# ============================================================
# Booking creation stores both values
# ============================================================

def test_stores_requested_total_and_computed_pay(client, monkeypatch):
    stub = RepoStub(customer=dict(CUST))
    resp = _book(client, monkeypatch, stub, amount=1000, price=76.03, discount=5.50)

    assert resp.status_code == 200
    assert len(stub.booked) == 1
    row = stub.booked[0]
    assert row["requested_total_php"] == pytest.approx(1000, abs=0.01)
    # liters = 1000/76.03 = 13.1526..., discount = liters * 5.50 = 72.34
    # pay = 1000 - 72.34 = 927.66 (matches REQ-brief-5 R5 acceptance example)
    assert row["requested_amount_php"] == pytest.approx(927.66, abs=0.01)


def test_discount_formula_matches_todays_per_liter_calc(client, monkeypatch):
    """discount(T) = (T / price) * dpl — same formula as today's client
    preview, just subtracted instead of added (REQ R6)."""
    stub = RepoStub(customer=dict(CUST))
    resp = _book(client, monkeypatch, stub, amount=2000, price=50.0, discount=2.0)

    assert resp.status_code == 200
    row = stub.booked[0]
    # liters = 2000/50 = 40, discount_total = 40*2.0 = 80, pay = 2000-80 = 1920
    assert row["requested_total_php"] == pytest.approx(2000, abs=0.01)
    assert row["requested_amount_php"] == pytest.approx(1920.0, abs=0.01)


# ============================================================
# Booking rejection on non-positive pay
# ============================================================

def test_rejects_when_computed_pay_is_non_positive(client, monkeypatch):
    """discount >= total => pay <= 0 => booking rejected, form re-rendered
    with submitted values retained (REQ R10, edge case)."""
    stub = RepoStub(customer=dict(CUST))
    # liters = 100/50 = 2, discount_total = 2*60 = 120 >= 100 => pay <= 0
    resp = _book(client, monkeypatch, stub, amount=100, price=50.0, discount=60.0)

    assert resp.status_code == 200
    assert len(stub.booked) == 0
    body = resp.data.decode("utf-8")
    assert "discount" in body.lower() and "exceeds" in body.lower()
    # form re-rendered with the submitted amount retained
    assert 'value="100"' in body


# ============================================================
# Approve-flow formula (ops_set_status)
# ============================================================

def test_total_dispensed_reconstructs_to_entered_total(client, monkeypatch):
    """The single most important regression check: liters/discount must be
    derived from requested_total_php, not requested_amount_php, so
    total_dispensed reconstructs exactly to the entered total (ARCH A2)."""
    monkeypatch.setattr(main, "generate_assets_for_row", lambda row: None)

    voucher = {
        "voucher_id": "UF-TEST-00002",
        "station": "EcoOil - Cainta",
        "requested_amount_php": 927.66,   # pay, computed at booking time
        "requested_total_php": 1000.0,    # T, entered at booking time
        "price_snapshot_php_per_liter": 76.03,
        "price_snapshot_updated_at": 0,
        "discount_snapshot_php_per_liter": 5.50,
        "discount_snapshot_captured_at": 0,
        "status": "Unverified",
    }
    stub = RepoStub(voucher=voucher)
    monkeypatch.setattr(main, "repo", stub)

    resp = client.get("/ops/voucher/UF-TEST-00002/status/Unredeemed")

    assert resp.status_code == 302
    fields = stub.updated_fields
    assert fields is not None
    # liters_requested is rounded to 2dp before the discount multiply
    # (existing formula shape, unchanged by T2) — liters=round(1000/76.03,2)=13.15,
    # discount_total=round(13.15*5.50,2)=72.33 (1-centavo rounding-cascade
    # artifact from rounding liters before multiplying, pre-existing pattern).
    assert fields["liters_requested"] == pytest.approx(13.15, abs=0.01)
    assert fields["discount_total_php"] == pytest.approx(72.33, abs=0.01)
    # total_dispensed = pay + discount_total = 927.66 + 72.33 = 999.99 ≈ T,
    # within the same 1-centavo rounding-cascade tolerance as above.
    assert fields["total_dispensed_php"] == pytest.approx(voucher["requested_total_php"], abs=0.02)


def test_falls_back_to_old_formula_when_requested_total_is_null(client, monkeypatch):
    """Pre-migration bookings have requested_total_php = NULL; must settle
    under today's formula (base = requested_amount_php), not crash or
    silently zero out (ARCH forward-stress scenario)."""
    monkeypatch.setattr(main, "generate_assets_for_row", lambda row: None)

    voucher = {
        "voucher_id": "UF-TEST-00003",
        "station": "EcoOil - Cainta",
        "requested_amount_php": 1000.0,
        "requested_total_php": None,
        "price_snapshot_php_per_liter": 76.03,
        "price_snapshot_updated_at": 0,
        "discount_snapshot_php_per_liter": 5.50,
        "discount_snapshot_captured_at": 0,
        "status": "Unverified",
    }
    stub = RepoStub(voucher=voucher)
    monkeypatch.setattr(main, "repo", stub)

    resp = client.get("/ops/voucher/UF-TEST-00003/status/Unredeemed")

    assert resp.status_code == 302
    fields = stub.updated_fields
    assert fields is not None
    # old formula: liters = requested_amount_php/price = 1000/76.03
    assert fields["liters_requested"] == pytest.approx(13.15, abs=0.01)
    assert fields["discount_total_php"] == pytest.approx(72.33, abs=0.01)
    assert fields["total_dispensed_php"] == pytest.approx(1072.33, abs=0.01)
