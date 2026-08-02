"""
tests/test_supplier_api_liters_base.py — supplier_api()'s liters_requested
fallback must derive from requested_total_php (T), not requested_amount_php
(post-discount pay), for vouchers still awaiting approval (code-review fix
for ARCH-brief-5: previously understated liters to the supplier).
"""

import pytest

import main


class RepoStub:
    def __init__(self, voucher):
        self._voucher = voucher

    def get_voucher(self, voucher_id):
        return self._voucher


@pytest.fixture
def client(monkeypatch):
    main.app.config.update(TESTING=True)
    return main.app.test_client()


def test_liters_requested_derives_from_total_not_pay_when_unapproved(client, monkeypatch):
    # T=1000, pay=927.66 (discount already applied at booking time),
    # liters_requested not yet populated (voucher still Unverified).
    voucher = {
        "voucher_id": "UF-TEST-SUPPLIER-1",
        "station": "EcoOil - Cainta",
        "requested_amount_php": 927.66,
        "requested_total_php": 1000.0,
        "price_snapshot_php_per_liter": 76.03,
        "status": "Unverified",
        "liters_requested": None,
    }
    monkeypatch.setattr(main, "repo", RepoStub(voucher))

    resp = client.get(f"/supplier-api/UF-TEST-SUPPLIER-1?token={main.SUPPLIER_API_TOKEN}")

    assert resp.status_code == 200
    data = resp.get_json()
    # liters = 1000/76.03, NOT 927.66/76.03
    assert data["litersRequested"] == pytest.approx(1000 / 76.03, abs=0.01)


def test_liters_requested_falls_back_to_pay_when_total_missing(client, monkeypatch):
    # Pre-migration voucher: requested_total_php absent entirely.
    voucher = {
        "voucher_id": "UF-TEST-SUPPLIER-2",
        "station": "EcoOil - Cainta",
        "requested_amount_php": 1000.0,
        "price_snapshot_php_per_liter": 76.03,
        "status": "Unverified",
        "liters_requested": None,
    }
    monkeypatch.setattr(main, "repo", RepoStub(voucher))

    resp = client.get(f"/supplier-api/UF-TEST-SUPPLIER-2?token={main.SUPPLIER_API_TOKEN}")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["litersRequested"] == pytest.approx(1000 / 76.03, abs=0.01)
