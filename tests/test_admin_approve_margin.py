"""
tests/test_admin_approve_margin.py — Approve-flow (ops_set_status) live
discount fallback must apply margin too (T3, REQ-profit-margin).

Regresses the specific gap identified in ARCH-profit-margin: when a
voucher's discount_snapshot_php_per_liter is exactly 0.0 (missing/never
captured), ops_set_status() falls back to a *live* discount_store lookup
— that fallback must run the same margin transform as everywhere else,
or it silently leaks the raw supplier discount for this one edge case.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import main
from margin_store import MarginStore


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
    def __init__(self, voucher=None):
        self._voucher = voucher
        self.updated_fields = None

    def get_voucher(self, voucher_id):
        if self.updated_fields:
            merged = dict(self._voucher)
            merged.update(self.updated_fields)
            return merged
        return self._voucher

    def update_voucher_fields(self, voucher_id, fields):
        self.updated_fields = fields

    def set_status(self, voucher_id, status, ts):
        pass


@pytest.fixture
def client(monkeypatch):
    main.app.config.update(TESTING=True)
    monkeypatch.setattr(main, "generate_assets_for_row", lambda row: None)
    return main.app.test_client()


def _voucher(discount_snapshot=0.0):
    return {
        "voucher_id": "UF-TEST-MARGIN-1",
        "station": "EcoOil - Cainta",
        "requested_amount_php": 927.66,
        "requested_total_php": 1000.0,
        "price_snapshot_php_per_liter": 76.03,
        "price_snapshot_updated_at": 0,
        "discount_snapshot_php_per_liter": discount_snapshot,
        "discount_snapshot_captured_at": 0,
        "status": "Unverified",
    }


def test_fallback_applies_margin_when_snapshot_zero_and_station_not_exempt(client, monkeypatch):
    stub = RepoStub(voucher=_voucher(discount_snapshot=0.0))
    monkeypatch.setattr(main, "repo", stub)
    monkeypatch.setattr(main.margin_store, "get", lambda: 12.25)
    monkeypatch.setattr(
        main.discount_store, "get_with_exempt",
        lambda station, fuel_type: {"value": 5.50, "margin_exempt": False}
    )

    resp = client.get("/ops/voucher/UF-TEST-MARGIN-1/status/Unredeemed")

    assert resp.status_code == 302
    fields = stub.updated_fields
    assert fields is not None

    expected_dpl = MarginStore.apply(5.50, 12.25, exempt=False)
    liters_requested = round(1000.0 / 76.03, 2)
    expected_discount_total = round(liters_requested * expected_dpl, 2)

    assert fields["discount_per_liter"] == pytest.approx(expected_dpl, abs=0.0001)
    assert fields["discount_total_php"] == pytest.approx(expected_discount_total, abs=0.01)
    # Regression check: the raw (pre-margin) discount must NOT be what
    # was used — this is exactly the leak the fix closes.
    raw_discount_total = round(liters_requested * 5.50, 2)
    assert fields["discount_total_php"] != pytest.approx(raw_discount_total, abs=0.01)


def test_fallback_returns_raw_for_exempt_station_zero_snapshot(client, monkeypatch):
    stub = RepoStub(voucher=_voucher(discount_snapshot=0.0))
    monkeypatch.setattr(main, "repo", stub)
    monkeypatch.setattr(main.margin_store, "get", lambda: 12.25)
    monkeypatch.setattr(
        main.discount_store, "get_with_exempt",
        lambda station, fuel_type: {"value": 5.50, "margin_exempt": True}
    )

    resp = client.get("/ops/voucher/UF-TEST-MARGIN-1/status/Unredeemed")

    assert resp.status_code == 302
    fields = stub.updated_fields
    assert fields is not None

    liters_requested = round(1000.0 / 76.03, 2)
    expected_discount_total = round(liters_requested * 5.50, 2)

    assert fields["discount_per_liter"] == pytest.approx(5.50, abs=0.0001)
    assert fields["discount_total_php"] == pytest.approx(expected_discount_total, abs=0.01)
