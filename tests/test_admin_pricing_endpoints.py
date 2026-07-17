"""
tests/test_admin_pricing_endpoints.py — admin_prices_update /
admin_discounts_update gain fuel_type validation (T6, ARCH-fuel-types-expansion).
"""

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
    def __init__(self):
        self.prices = {}  # (station_id, fuel_type) -> price
        self.set_calls = []

    def get_station(self, station_id, fuel_type):
        price = self.prices.get((station_id, fuel_type))
        if price is None:
            return None
        return {"id": station_id, "price_php_per_liter": price, "updated_at": 0}

    def set_price(self, station_id, fuel_type, new_price):
        if new_price <= 0 or new_price > 200:
            raise ValueError("Unreasonable price. Must be 0 < price ≤ 200.")
        self.set_calls.append((station_id, fuel_type, new_price))
        self.prices[(station_id, fuel_type)] = new_price
        return {"id": station_id, "price_php_per_liter": new_price, "updated_at": 12345}


class FakeDiscountStore:
    def __init__(self):
        self.set_calls = []

    def set(self, station, fuel_type, value, actor="system", reason=""):
        self.set_calls.append((station, fuel_type, value))


@pytest.fixture
def fake_price_store(monkeypatch):
    fps = FakePriceStore()
    monkeypatch.setattr(main, "price_store", fps)
    return fps


@pytest.fixture
def fake_discount_store(monkeypatch):
    fds = FakeDiscountStore()
    monkeypatch.setattr(main, "discount_store", fds)
    return fds


# ============================================================
# admin_prices_update
# ============================================================

def test_valid_price_update_succeeds(client, fake_price_store, monkeypatch):
    _login(client)
    monkeypatch.setattr(main, "append_price_history", lambda **kw: None)

    r = client.post("/admin/prices/update", json={
        "station_id": "cleanfuel_valenzuela", "fuel_type": "Premium", "price": 65.0
    })

    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["price_php_per_liter"] == 65.0
    assert ("cleanfuel_valenzuela", "Premium", 65.0) in fake_price_store.set_calls


def test_missing_fuel_type_rejected(client, fake_price_store):
    _login(client)
    r = client.post("/admin/prices/update", json={
        "station_id": "cleanfuel_valenzuela", "price": 65.0
    })
    assert r.status_code == 400


def test_unrecognized_fuel_type_rejected(client, fake_price_store):
    _login(client)
    r = client.post("/admin/prices/update", json={
        "station_id": "cleanfuel_valenzuela", "fuel_type": "Regular", "price": 65.0
    })
    assert r.status_code == 400


def test_unknown_station_returns_404(client, monkeypatch):
    _login(client)

    class RaisingStore:
        def get_station(self, station_id, fuel_type):
            return None

        def set_price(self, station_id, fuel_type, new_price):
            raise KeyError(f"Station '{station_id}' not found")

    monkeypatch.setattr(main, "price_store", RaisingStore())

    r = client.post("/admin/prices/update", json={
        "station_id": "nope", "fuel_type": "Biodiesel", "price": 60.0
    })
    assert r.status_code == 404


def test_out_of_range_price_returns_400(client, fake_price_store):
    _login(client)
    r = client.post("/admin/prices/update", json={
        "station_id": "cleanfuel_valenzuela", "fuel_type": "Biodiesel", "price": 999
    })
    assert r.status_code == 400


# ============================================================
# admin_discounts_update
# ============================================================

def test_valid_discount_update_succeeds(client, fake_discount_store):
    _login(client)
    r = client.post("/admin/discounts/update", data={
        "station": "Cleanfuel – Valenzuela", "fuel_type": "Premium", "discount_per_liter": "2.5"
    })
    assert r.status_code == 302
    assert ("Cleanfuel – Valenzuela", "Premium", 2.5) in fake_discount_store.set_calls


def test_discount_missing_fuel_type_rejected(client, fake_discount_store):
    _login(client)
    r = client.post("/admin/discounts/update", data={
        "station": "Cleanfuel – Valenzuela", "discount_per_liter": "2.5"
    })
    assert r.status_code == 302
    assert fake_discount_store.set_calls == []
    with client.session_transaction() as sess:
        flashes = sess.get("_flashes", [])
    assert any("fuel type" in msg.lower() for _, msg in flashes)


def test_discount_unrecognized_fuel_type_rejected(client, fake_discount_store):
    _login(client)
    r = client.post("/admin/discounts/update", data={
        "station": "Cleanfuel – Valenzuela", "fuel_type": "Regular", "discount_per_liter": "2.5"
    })
    assert fake_discount_store.set_calls == []


def test_discount_out_of_range_rejected(client, fake_discount_store):
    _login(client)
    r = client.post("/admin/discounts/update", data={
        "station": "Cleanfuel – Valenzuela", "fuel_type": "Biodiesel", "discount_per_liter": "99"
    })
    assert fake_discount_store.set_calls == []


# ============================================================
# Edge case: isolation between fuel types
# ============================================================

def test_updating_one_fuel_type_price_does_not_affect_another(client, fake_price_store, monkeypatch):
    _login(client)
    monkeypatch.setattr(main, "append_price_history", lambda **kw: None)
    fake_price_store.prices[("cleanfuel_valenzuela", "Biodiesel")] = 60.0

    client.post("/admin/prices/update", json={
        "station_id": "cleanfuel_valenzuela", "fuel_type": "Premium", "price": 65.0
    })

    assert fake_price_store.prices[("cleanfuel_valenzuela", "Biodiesel")] == 60.0
    assert fake_price_store.prices[("cleanfuel_valenzuela", "Premium")] == 65.0


# ============================================================
# Regression guard: auth unchanged
# ============================================================

def test_prices_update_unauthenticated_returns_403(client):
    r = client.post("/admin/prices/update", json={
        "station_id": "x", "fuel_type": "Biodiesel", "price": 60.0
    })
    assert r.status_code == 403


def test_discounts_update_unauthenticated_redirects_to_login(client):
    r = client.post("/admin/discounts/update", data={
        "station": "x", "fuel_type": "Biodiesel", "discount_per_liter": "1.0"
    })
    assert r.status_code == 302
    assert "/admin/login" in r.headers["Location"]
