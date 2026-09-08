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

    def list_all_stations(self, include_inactive=True):
        return [{"id": "cleanfuel_valenzuela", "name": "Cleanfuel – Valenzuela",
                  "brand": "Cleanfuel", "location": "Valenzuela", "is_active": True}]

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
# Brief-5 (ARCH-brief-5, T4): combined price+discount save
# ============================================================

def test_combined_save_writes_both_price_and_discount(client, fake_price_store, fake_discount_store, monkeypatch):
    _login(client)
    monkeypatch.setattr(main, "append_price_history", lambda **kw: None)

    r = client.post("/admin/prices/update", json={
        "station_id": "cleanfuel_valenzuela", "fuel_type": "Premium",
        "price": 65.0, "discount_per_liter": 2.5
    })

    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["price_php_per_liter"] == 65.0
    assert data["discount_per_liter"] == 2.5
    assert ("cleanfuel_valenzuela", "Premium", 65.0) in fake_price_store.set_calls
    assert ("Cleanfuel – Valenzuela", "Premium", 2.5) in fake_discount_store.set_calls


def test_combined_save_invalid_discount_saves_neither(client, fake_price_store, fake_discount_store, monkeypatch):
    _login(client)
    monkeypatch.setattr(main, "append_price_history", lambda **kw: None)

    r = client.post("/admin/prices/update", json={
        "station_id": "cleanfuel_valenzuela", "fuel_type": "Premium",
        "price": 65.0, "discount_per_liter": 99
    })

    assert r.status_code == 400
    data = r.get_json()
    assert data["ok"] is False
    assert data["field"] == "discount"
    assert fake_price_store.set_calls == []
    assert fake_discount_store.set_calls == []


def test_combined_save_invalid_price_saves_neither(client, fake_price_store, fake_discount_store, monkeypatch):
    _login(client)
    monkeypatch.setattr(main, "append_price_history", lambda **kw: None)

    r = client.post("/admin/prices/update", json={
        "station_id": "cleanfuel_valenzuela", "fuel_type": "Premium",
        "price": 999, "discount_per_liter": 2.5
    })

    assert r.status_code == 400
    data = r.get_json()
    assert data["ok"] is False
    assert data["field"] == "price"
    assert fake_price_store.set_calls == []
    assert fake_discount_store.set_calls == []


def test_combined_save_price_only_payload_still_works(client, fake_price_store, fake_discount_store, monkeypatch):
    """Backward compat: a payload with only `price` (no discount_per_liter)
    still saves price; discount is left untouched."""
    _login(client)
    monkeypatch.setattr(main, "append_price_history", lambda **kw: None)

    r = client.post("/admin/prices/update", json={
        "station_id": "cleanfuel_valenzuela", "fuel_type": "Premium", "price": 65.0
    })

    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert ("cleanfuel_valenzuela", "Premium", 65.0) in fake_price_store.set_calls
    assert fake_discount_store.set_calls == []


def test_combined_save_null_discount_treated_as_absent(client, fake_price_store, fake_discount_store, monkeypatch):
    """Code-review fix: the real UI always sends discount_per_liter, even
    as JSON null when the input is empty. A present-but-null value must be
    treated the same as an absent key (price-only save), not rejected."""
    _login(client)
    monkeypatch.setattr(main, "append_price_history", lambda **kw: None)

    r = client.post("/admin/prices/update", json={
        "station_id": "cleanfuel_valenzuela", "fuel_type": "Premium",
        "price": 65.0, "discount_per_liter": None
    })

    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert ("cleanfuel_valenzuela", "Premium", 65.0) in fake_price_store.set_calls
    assert fake_discount_store.set_calls == []


class FailingDiscountStore:
    def set(self, station, fuel_type, value, actor="system", reason=""):
        raise RuntimeError("transient DB error")


def test_combined_save_rolls_back_price_when_discount_write_fails(client, fake_price_store, monkeypatch):
    """Code-review fix: price_store.set_price and discount_store.set are
    separate DB round-trips. If the discount write fails after price
    already committed, the endpoint must roll price back to its prior
    value rather than leaving a half-saved state while claiming failure."""
    _login(client)
    monkeypatch.setattr(main, "append_price_history", lambda **kw: None)
    monkeypatch.setattr(main, "discount_store", FailingDiscountStore())
    fake_price_store.prices[("cleanfuel_valenzuela", "Premium")] = 60.0  # prior price

    r = client.post("/admin/prices/update", json={
        "station_id": "cleanfuel_valenzuela", "fuel_type": "Premium",
        "price": 70.0, "discount_per_liter": 2.5
    })

    assert r.status_code == 500
    data = r.get_json()
    assert data["ok"] is False
    assert data["field"] == "discount"
    # rolled back: last set_price call restores the prior price
    assert fake_price_store.set_calls[-1] == ("cleanfuel_valenzuela", "Premium", 60.0)
    assert fake_price_store.prices[("cleanfuel_valenzuela", "Premium")] == 60.0


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


# ============================================================
# admin_margin_update (T4, REQ-profit-margin)
# ============================================================

class FakeMarginStore:
    def __init__(self, value=0.0):
        self.value = value
        self.set_calls = []

    def get(self):
        return self.value

    def set(self, value, actor="system", reason=""):
        try:
            v = float(value)
        except (TypeError, ValueError):
            from margin_store import MarginValueError
            raise MarginValueError("margin_pct must be a number (float).")
        if v < 0 or v > 100:
            from margin_store import MarginValueError
            raise MarginValueError("margin_pct must be between 0 and 100.")
        if round(v, 2) != v:
            from margin_store import MarginValueError
            raise MarginValueError("margin_pct accepts at most 2 decimal places.")
        self.set_calls.append((v, actor, reason))
        self.value = v


@pytest.fixture
def fake_margin_store(monkeypatch):
    fms = FakeMarginStore()
    monkeypatch.setattr(main, "margin_store", fms)
    return fms


def test_valid_margin_update_succeeds(client, fake_margin_store):
    _login(client)
    r = client.post("/admin/margin/update", json={"margin_pct": 12.25})
    assert r.status_code == 200
    body = r.get_json()
    assert body == {"ok": True, "margin_pct": 12.25}
    assert fake_margin_store.set_calls[0][0] == 12.25


def test_margin_update_rejects_more_than_two_decimal_places(client, fake_margin_store):
    _login(client)
    r = client.post("/admin/margin/update", json={"margin_pct": 12.255})
    assert r.status_code == 400
    assert r.get_json()["field"] == "margin_pct"
    assert fake_margin_store.set_calls == []


def test_margin_update_rejects_negative_value(client, fake_margin_store):
    _login(client)
    r = client.post("/admin/margin/update", json={"margin_pct": -1})
    assert r.status_code == 400
    assert fake_margin_store.set_calls == []


def test_margin_update_rejects_value_over_100(client, fake_margin_store):
    _login(client)
    r = client.post("/admin/margin/update", json={"margin_pct": 101})
    assert r.status_code == 400
    assert fake_margin_store.set_calls == []


def test_margin_update_unauthenticated_returns_403(client, fake_margin_store):
    r = client.post("/admin/margin/update", json={"margin_pct": 12.25})
    assert r.status_code == 403
    assert fake_margin_store.set_calls == []


def test_admin_prices_context_includes_current_margin(client, fake_margin_store, monkeypatch):
    _login(client)
    fake_margin_store.value = 5.5
    monkeypatch.setattr(main.price_store, "list_all_stations", lambda: [])
    monkeypatch.setattr(main.price_store, "list_stations", lambda fuel_type, include_inactive=False: [])
    monkeypatch.setattr(main.discount_store, "get_all_with_updated_at", lambda fuel_type: {})

    captured = {}
    real_render = main.render_template

    def spy_render(template_name, **context):
        if template_name == "admin_prices.html":
            captured.update(context)
        return real_render(template_name, **context)

    monkeypatch.setattr(main, "render_template", spy_render)

    r = client.get("/admin/prices")

    assert r.status_code == 200
    assert captured.get("margin_pct") == 5.5
