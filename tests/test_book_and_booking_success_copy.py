"""
tests/test_book_and_booking_success_copy.py — content changes on /book's
pre-registration prompt, /register's info-box, and the booking
confirmation page (Brief-4).
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


# ============================================================
# /book — pre-registration prompt
# ============================================================

def test_book_page_title_updated(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customer=None))
    r = client.get("/book")
    assert b"Book a Refuel (Prepay for Discounts)" in r.data


def test_book_pre_registration_prompt_updated(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customer=None))
    r = client.get("/book")
    body = r.data.decode("utf-8")
    # ARCH-brief-7 T2 (R9 removed the Tagalog tagline, kept the English
    # one; R10 added the Facebook link) + follow-up: the >>> <<< account-code
    # callout link was removed entirely. ARCH-brief-8 T1 (R3) re-adds the
    # Tagalog tagline above the English one.
    assert "Register Vehicle To Get 4-Letter Account CODE" not in body
    assert "Malaking tipid. Mas mahabang biyahe." in body
    assert "Big savings. Longer trips." in body
    assert "Fuel Discounts by UniFleet" in body
    assert "facebook.com/people/Fuel-Discounts-by-UniFleet" in body


# ============================================================
# /register — info-box copy
# ============================================================

def test_register_info_box_updated(client):
    r = client.get("/register")
    body = r.data.decode("utf-8")
    assert "Save up to ₱1,000 on every refill with UniFleet" in body
    assert "Example Voucher" not in body


# ============================================================
# Booking confirmation page — InstaPay copy, no QR
# ============================================================

def _valid_refuel():
    manila = ZoneInfo("Asia/Manila")
    return (datetime.now(manila) + timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M")


def test_booking_success_shows_instapay_copy_and_qr(client, monkeypatch):
    stub = RepoStub(customer=dict(CUST))
    monkeypatch.setattr(main, "repo", stub)
    monkeypatch.setattr(
        main.price_store, "list_stations",
        lambda fuel_type: [{"id": "teststation", "name": "Test Station",
                             "price_php_per_liter": 60.0, "updated_at": 0}]
    )
    monkeypatch.setattr(main.discount_store, "get_all", lambda fuel_type: {})
    monkeypatch.setattr(main.discount_store, "get", lambda station, fuel_type: None)

    resp = client.post("/book", data={
        "account_code": "HARR",
        "station": "Test Station",
        "requested_amount_php": "10000",
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
    body = resp.data.decode("utf-8")

    # R1: PESONet removed, exact amount-due text (Brief-5, ARCH-brief-5 T7)
    assert "PESONet" not in body
    # ARCH-brief-7 T1: due_amount = computed_pay_php (total - discount);
    # discount stubbed to 0 here, so pay == the raw total entered
    assert "Amount Due: ₱10,000.00" in body

    # R2: InstaPay QR present with caption
    assert "instapay_qr.png" in body
    assert "Scan with your banking app (InstaPay) to pay." in body

    # unrelated payment-box content unchanged
    assert "Send to INSTAPAY" in body
    assert "000228034271" in body

    # regression guard: this repo previously reverted a GoTyme QR attempt —
    # must not reintroduce it
    assert "GoTyme" not in body
    assert "payment_qr.png" not in body
    assert 'alt="UniFleet GoTyme payment QR code"' not in body


def test_booking_success_shows_post_discount_amount_due(client, monkeypatch):
    """ARCH-brief-7 T1: Amount Due must reflect the post-discount amount
    (total - discount), not the raw pre-discount total entered — proves
    computed_pay_php, not requested_amount_php, drives the display."""
    stub = RepoStub(customer=dict(CUST))
    monkeypatch.setattr(main, "repo", stub)
    monkeypatch.setattr(
        main.price_store, "list_stations",
        lambda fuel_type: [{"id": "teststation", "name": "Test Station",
                             "price_php_per_liter": 60.0, "updated_at": 0}]
    )
    # price ₱60/L, discount ₱5/L; total ₱1200 -> 20L -> discount ₱100 -> pay ₱1,100.00
    monkeypatch.setattr(main.discount_store, "get_all", lambda fuel_type: {"Test Station": 5.0})
    monkeypatch.setattr(main.discount_store, "get", lambda station, fuel_type: 5.0)

    resp = client.post("/book", data={
        "account_code": "HARR",
        "station": "Test Station",
        "requested_amount_php": "1200",
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
    body = resp.data.decode("utf-8")
    assert "Amount Due: ₱1,100.00" in body
    assert "Amount Due: ₱1,200.00" not in body


def test_booking_rejected_when_discount_exceeds_fuel_amount(client, monkeypatch):
    """Regression guard (ARCH-brief-7 T1): the computed-pay-<=0 rejection
    path in main.py's book() sits right next to the due_amount fix and
    must be unaffected by it — still re-renders book.html with an error,
    never reaches booking_success.html."""
    stub = RepoStub(customer=dict(CUST))
    monkeypatch.setattr(main, "repo", stub)
    monkeypatch.setattr(
        main.price_store, "list_stations",
        lambda fuel_type: [{"id": "teststation", "name": "Test Station",
                             "price_php_per_liter": 60.0, "updated_at": 0}]
    )
    # discount (₱100/L) far exceeds price (₱60/L) — guarantees pay <= 0
    monkeypatch.setattr(main.discount_store, "get_all", lambda fuel_type: {"Test Station": 100.0})
    monkeypatch.setattr(main.discount_store, "get", lambda station, fuel_type: 100.0)

    resp = client.post("/book", data={
        "account_code": "HARR",
        "station": "Test Station",
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
    })

    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "discount for this station exceeds the fuel amount entered" in body
    assert "Amount Due" not in body
