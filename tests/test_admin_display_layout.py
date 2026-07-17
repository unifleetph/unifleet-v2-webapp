"""
tests/test_admin_display_layout.py — admin_prices.html 6-column layout,
admin.html Fuel Type column (T7, ARCH-fuel-types-expansion).
"""

import re

import pytest

import main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "s3cret")
    monkeypatch.setattr(main, "ADMIN_KEY", "testkey")
    main.app.config.update(TESTING=True)
    return main.app.test_client()


def _login(client):
    client.post("/admin/login", data={"password": "s3cret"})


class FakePriceStore:
    def list_stations(self, fuel_type, include_inactive=False):
        if fuel_type == "Biodiesel":
            return [{"id": "s1", "brand": "Cleanfuel", "name": "Cleanfuel", "location": "NLEX",
                      "price_php_per_liter": 60.0, "updated_at": 1750000000}]
        if fuel_type == "Premium":
            return [{"id": "s1", "brand": "Cleanfuel", "name": "Cleanfuel", "location": "NLEX",
                      "price_php_per_liter": 65.0, "updated_at": 1750000100}]
        return []

    def list_all_stations(self, include_inactive=True):
        return [{"id": "s1", "brand": "Cleanfuel", "name": "Cleanfuel", "location": "NLEX", "is_active": True}]


class FakeDiscountStore:
    def get_all_with_updated_at(self, fuel_type):
        if fuel_type == "Biodiesel":
            return {"Cleanfuel": {"value": 2.0, "updated_at": 1750000200}}
        return {}


@pytest.fixture
def admin_prices_page(client, monkeypatch):
    monkeypatch.setattr(main, "price_store", FakePriceStore())
    monkeypatch.setattr(main, "discount_store", FakeDiscountStore())
    _login(client)
    r = client.get("/admin/prices")
    assert r.status_code == 200
    return r.data.decode("utf-8")


# ============================================================
# admin_prices.html — 6-column layout
# ============================================================

def test_renders_price_and_discount_cells_per_fuel_type(admin_prices_page):
    price_cells = re.findall(r'<td class="price-cell" data-fuel-type="([^"]+)"', admin_prices_page)
    discount_cells = re.findall(r'<td class="discount-cell" data-fuel-type="([^"]+)"', admin_prices_page)
    assert price_cells == ["Biodiesel", "Premium", "Unleaded"]
    assert discount_cells == ["Biodiesel", "Premium", "Unleaded"]


def test_readable_timestamp_under_price_and_discount_cells(admin_prices_page):
    # Biodiesel price + discount both have a real epoch -> readable date shown
    # in the visible .updated-readable span. The raw epoch legitimately still
    # appears in data-epoch="..." (JS staleness calc), just not as visible text.
    readable_spans = re.findall(r'<span class="updated-readable">([^<]*)</span>', admin_prices_page)
    assert any("2025-" in s or "2026-" in s for s in readable_spans)
    assert not any(s.strip() == "1750000000" for s in readable_spans)


def test_unpriced_fuel_type_shows_dash_placeholder(admin_prices_page):
    # Unleaded has no price/discount at all for this station
    unleaded_block = admin_prices_page[admin_prices_page.index('data-fuel-type="Unleaded"'):]
    assert "—" in unleaded_block[:400]


# ============================================================
# Bare & Inactive Station Visibility (T3, ARCH-station-management)
# ============================================================

class FakePriceStoreWithBareAndInactive:
    def list_stations(self, fuel_type, include_inactive=False):
        return []  # no prices set for any fuel type, any station

    def list_all_stations(self, include_inactive=True):
        stations = [
            {"id": "bare1", "brand": "Bare Co", "name": "Bare Station", "location": "Nowhere", "is_active": True},
            {"id": "inactive1", "brand": "Old Co", "name": "Old Station", "location": "Nowhere", "is_active": False},
        ]
        if not include_inactive:
            stations = [s for s in stations if s["is_active"]]
        return stations


@pytest.fixture
def admin_prices_page_bare_inactive(client, monkeypatch):
    monkeypatch.setattr(main, "price_store", FakePriceStoreWithBareAndInactive())
    monkeypatch.setattr(main, "discount_store", FakeDiscountStore())
    _login(client)
    r = client.get("/admin/prices")
    assert r.status_code == 200
    return r.data.decode("utf-8")


def test_bare_station_appears_with_blank_price_cells(admin_prices_page_bare_inactive):
    assert 'data-station-id="bare1"' in admin_prices_page_bare_inactive


def test_inactive_station_appears_in_admin_prices_list(admin_prices_page_bare_inactive):
    assert 'data-station-id="inactive1"' in admin_prices_page_bare_inactive


def test_admin_prices_html_renders_inactive_row_css_class(admin_prices_page_bare_inactive):
    inactive_row = admin_prices_page_bare_inactive[
        admin_prices_page_bare_inactive.index('data-station-id="inactive1"'):
    ]
    assert 'inactive-row' in inactive_row[:200]


def test_active_priced_station_unchanged_from_t7_behavior(admin_prices_page):
    # regression guard: existing priced/active station's fuel data unaffected
    assert 'data-station-id="s1"' in admin_prices_page
    price_cells = re.findall(r'<td class="price-cell" data-fuel-type="([^"]+)"', admin_prices_page)
    assert price_cells == ["Biodiesel", "Premium", "Unleaded"]


# ============================================================
# admin.html — Fuel Type column
# ============================================================

class RepoStub:
    def __init__(self, vouchers):
        self._vouchers = vouchers

    def list_recent_vouchers(self, limit=50):
        return list(self._vouchers)


@pytest.fixture
def admin_dashboard(client, monkeypatch):
    monkeypatch.setattr(main.price_store, "list_stations", lambda: [])
    return client


def test_admin_dashboard_has_fuel_type_column(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub([]))
    _login(client)
    r = client.get("/admin")
    assert b"<th>Fuel Type</th>" in r.data


def test_dashboard_fuel_type_falls_back_to_diesel_for_null(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub([
        {"voucher_id": "UF-1", "driver_name": "Dave", "station": "Cleanfuel",
         "status": "Unverified", "fuel_type": None},
    ]))
    _login(client)
    r = client.get("/admin")
    body = r.data.decode("utf-8")
    assert "<td>Diesel</td>" in body


def test_dashboard_fuel_type_shows_real_value_when_present(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub([
        {"voucher_id": "UF-2", "driver_name": "Dave", "station": "Cleanfuel",
         "status": "Unverified", "fuel_type": "Premium"},
    ]))
    _login(client)
    r = client.get("/admin")
    body = r.data.decode("utf-8")
    assert "<td>Premium</td>" in body
