"""
tests/test_api_v1_pricing.py — /api/v1/prices, /api/v1/discounts,
/api/v1/price_preview gain a backward-compatible fuel_type query param
(T9, ARCH-fuel-types-expansion).
"""

import pytest

import main


class FakePriceStore:
    def list_stations(self, fuel_type):
        data = {
            "Biodiesel": [{"id": "s1", "name": "Cleanfuel", "price_php_per_liter": 60.0, "updated_at": 0}],
            "Premium": [{"id": "s1", "name": "Cleanfuel", "price_php_per_liter": 65.0, "updated_at": 0}],
        }
        return data.get(fuel_type, [])


class FakeDiscountStore:
    def get_all(self, fuel_type):
        data = {
            "Biodiesel": {"Cleanfuel": 2.0},
            "Unleaded": {"Cleanfuel": 3.5},
        }
        return data.get(fuel_type, {})


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "price_store", FakePriceStore())
    monkeypatch.setattr(main, "discount_store", FakeDiscountStore())
    main.app.config.update(TESTING=True)
    return main.app.test_client()


# ============================================================
# Default (backward-compat) behavior
# ============================================================

def test_prices_defaults_to_biodiesel(client):
    r = client.get("/api/v1/prices")
    assert r.status_code == 200
    data = r.get_json()
    assert data["stations"][0]["name"] == "Cleanfuel"
    assert data["stations"][0]["price_php_per_liter"] == 60.0


def test_discounts_defaults_to_biodiesel(client):
    r = client.get("/api/v1/discounts")
    assert r.status_code == 200
    assert r.get_json()["discounts"] == {"Cleanfuel": 2.0}


def test_price_preview_defaults_to_biodiesel(client):
    r = client.get("/api/v1/price_preview?station=Cleanfuel&amount=1000")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    # Biodiesel price is 60.0 -> ~16.67 liters
    assert round(data["liters_requested"], 2) == round(1000 / 60.0, 2)


# ============================================================
# Explicit fuel_type
# ============================================================

def test_prices_explicit_premium(client):
    r = client.get("/api/v1/prices?fuel_type=Premium")
    data = r.get_json()
    assert data["stations"][0]["price_php_per_liter"] == 65.0


def test_discounts_explicit_unleaded(client):
    r = client.get("/api/v1/discounts?fuel_type=Unleaded")
    assert r.get_json()["discounts"] == {"Cleanfuel": 3.5}


def test_price_preview_explicit_premium(client):
    r = client.get("/api/v1/price_preview?station=Cleanfuel&amount=1000&fuel_type=Premium")
    data = r.get_json()
    assert round(data["liters_requested"], 2) == round(1000 / 65.0, 2)


# ============================================================
# Edge case: unrecognized fuel_type falls back silently
# ============================================================

def test_prices_unrecognized_fuel_type_falls_back_to_biodiesel(client):
    r = client.get("/api/v1/prices?fuel_type=Regular")
    assert r.status_code == 200
    data = r.get_json()
    assert data["stations"][0]["price_php_per_liter"] == 60.0


def test_discounts_unrecognized_fuel_type_falls_back_to_biodiesel(client):
    r = client.get("/api/v1/discounts?fuel_type=Regular")
    assert r.status_code == 200
    assert r.get_json()["discounts"] == {"Cleanfuel": 2.0}


def test_price_preview_unrecognized_fuel_type_falls_back_to_biodiesel(client):
    r = client.get("/api/v1/price_preview?station=Cleanfuel&amount=1000&fuel_type=Regular")
    data = r.get_json()
    assert round(data["liters_requested"], 2) == round(1000 / 60.0, 2)


# ============================================================
# Regression guard: existing behavior unaffected
# ============================================================

def test_price_preview_station_not_found_still_404(client):
    r = client.get("/api/v1/price_preview?station=Nonexistent&amount=1000")
    assert r.status_code == 404


def test_price_preview_invalid_amount_still_400(client):
    r = client.get("/api/v1/price_preview?station=Cleanfuel&amount=notanumber")
    assert r.status_code == 400


# ============================================================
# Brief-5 (ARCH-brief-5, T3): subtract-based model — amount now means
# "total fuel amount" (T), not prepaid cash.
# ============================================================

def test_price_preview_returns_you_pay_php(client):
    # Biodiesel price 60.0, amount=1000 (T), discount_per_liter=2.0
    # liters = 1000/60 = 16.6667 -> round 16.67, discount_total = 16.67*2.0 = 33.34
    # you_pay_php = 1000 - 33.34 = 966.66
    r = client.get("/api/v1/price_preview?station=Cleanfuel&amount=1000&discount_per_liter=2.0")
    data = r.get_json()
    assert data["ok"] is True
    assert data["you_pay_php"] == pytest.approx(966.66, abs=0.01)


def test_price_preview_total_dispensed_reflects_new_semantics(client):
    # total_dispensed now represents the entered total (T) itself, not
    # amount + discount_total (the old add-on-top intent) — it must equal
    # the entered amount, not amount + discount_total.
    r = client.get("/api/v1/price_preview?station=Cleanfuel&amount=1000&discount_per_liter=2.0")
    data = r.get_json()
    assert data["total_dispensed"] == pytest.approx(1000.0, abs=0.01)
    assert data["total_dispensed"] != pytest.approx(1000.0 + data["discount_total"], abs=0.01)


# ============================================================
# Regression guard: existing response shape/formulas unaffected
# ============================================================

def test_price_preview_existing_fields_still_present_and_correct(client):
    r = client.get("/api/v1/price_preview?station=Cleanfuel&amount=1000&discount_per_liter=2.0")
    data = r.get_json()
    assert round(data["liters_requested"], 2) == round(1000 / 60.0, 2)
    assert data["discount_total"] == pytest.approx(round(data["liters_requested"] * 2.0, 2), abs=0.01)
    assert data["price_php_per_liter"] == 60.0
    assert data["station_id"] == "s1"
    assert data["station_name"] == "Cleanfuel"
    assert "price_is_stale" in data
