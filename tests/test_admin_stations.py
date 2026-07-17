"""
tests/test_admin_stations.py — station CRUD/activation admin routes
(T2, ARCH-station-management).
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
        self.stations = {}  # id -> station dict
        self.upsert_calls = []
        self.active_calls = []

    def list_all_stations(self, include_inactive=True):
        stations = list(self.stations.values())
        if not include_inactive:
            stations = [s for s in stations if s.get("is_active", True)]
        return stations

    def generate_unique_station_id(self, brand, name):
        return f"{brand}_{name}".lower().replace(" ", "_")

    def upsert_station(self, st):
        self.upsert_calls.append(dict(st))
        existing = self.stations.get(st["id"], {})
        merged = {**existing, **st, "is_active": existing.get("is_active", True)}
        self.stations[st["id"]] = merged
        return merged

    def set_station_active(self, station_id, is_active):
        self.active_calls.append((station_id, is_active))
        if station_id not in self.stations:
            raise KeyError(f"Station '{station_id}' not found")
        self.stations[station_id]["is_active"] = is_active
        return self.stations[station_id]


@pytest.fixture
def fake_price_store(monkeypatch):
    fps = FakePriceStore()
    monkeypatch.setattr(main, "price_store", fps)
    return fps


# ============================================================
# Auth & Creation
# ============================================================

def test_get_admin_stations_unauthenticated_redirects_to_login(client):
    r = client.get("/admin/stations")

    assert r.status_code == 302
    assert "/admin/login" in r.headers["Location"]


def test_post_admin_stations_creates_station_with_generated_id(client, fake_price_store):
    _login(client)

    r = client.post("/admin/stations", data={
        "brand": "Petron", "name": "Makati", "location": "EDSA",
    })

    assert r.status_code == 302
    assert len(fake_price_store.upsert_calls) == 1
    created = fake_price_store.upsert_calls[0]
    assert created["id"] == "petron_makati"
    assert created["brand"] == "Petron"
    assert created["name"] == "Makati"
    assert created["location"] == "EDSA"


def test_post_admin_stations_missing_brand_flashes_error_and_does_not_create(client, fake_price_store):
    _login(client)

    r = client.post("/admin/stations", data={"brand": "", "name": "Makati", "location": "EDSA"})

    assert r.status_code == 302
    assert len(fake_price_store.upsert_calls) == 0


def test_post_admin_stations_missing_name_flashes_error_and_does_not_create(client, fake_price_store):
    _login(client)

    r = client.post("/admin/stations", data={"brand": "Petron", "name": "", "location": "EDSA"})

    assert r.status_code == 302
    assert len(fake_price_store.upsert_calls) == 0


# ============================================================
# Edit
# ============================================================

def test_post_admin_stations_edit_updates_identity(client, fake_price_store):
    _login(client)
    fake_price_store.stations["petron_makati"] = {
        "id": "petron_makati", "brand": "Petron", "name": "Makati", "location": "EDSA", "is_active": True,
    }

    r = client.post("/admin/stations/petron_makati/edit", data={
        "brand": "Petron", "name": "Makati Renamed", "location": "New Location",
    })

    assert r.status_code == 302
    assert len(fake_price_store.upsert_calls) == 1
    updated = fake_price_store.upsert_calls[0]
    assert updated["id"] == "petron_makati"
    assert updated["name"] == "Makati Renamed"
    assert updated["location"] == "New Location"


def test_post_admin_stations_edit_unknown_id_returns_404(client, fake_price_store):
    _login(client)

    r = client.post("/admin/stations/does_not_exist/edit", data={
        "brand": "X", "name": "Y", "location": "Z",
    })

    assert r.status_code == 404


# ============================================================
# Deactivate / Reactivate
# ============================================================

def test_post_admin_stations_deactivate_calls_set_station_active_false(client, fake_price_store):
    _login(client)
    fake_price_store.stations["petron_makati"] = {
        "id": "petron_makati", "brand": "Petron", "name": "Makati", "location": "EDSA", "is_active": True,
    }

    r = client.post("/admin/stations/petron_makati/deactivate")

    assert r.status_code == 302
    assert ("petron_makati", False) in fake_price_store.active_calls


def test_post_admin_stations_reactivate_calls_set_station_active_true(client, fake_price_store):
    _login(client)
    fake_price_store.stations["petron_makati"] = {
        "id": "petron_makati", "brand": "Petron", "name": "Makati", "location": "EDSA", "is_active": False,
    }

    r = client.post("/admin/stations/petron_makati/reactivate")

    assert r.status_code == 302
    assert ("petron_makati", True) in fake_price_store.active_calls


def test_post_admin_stations_deactivate_unknown_id_returns_404(client, fake_price_store):
    _login(client)

    r = client.post("/admin/stations/does_not_exist/deactivate")

    assert r.status_code == 404


def test_post_admin_stations_reactivate_unknown_id_returns_404(client, fake_price_store):
    _login(client)

    r = client.post("/admin/stations/does_not_exist/reactivate")

    assert r.status_code == 404


# ============================================================
# Regression Guard
# ============================================================

@pytest.mark.parametrize("method,path", [
    ("post", "/admin/stations"),
    ("post", "/admin/stations/some_id/edit"),
    ("post", "/admin/stations/some_id/deactivate"),
    ("post", "/admin/stations/some_id/reactivate"),
])
def test_all_station_routes_require_admin(client, fake_price_store, method, path):
    r = getattr(client, method)(path)

    assert r.status_code == 302
    assert "/admin/login" in r.headers["Location"]
