"""
tests/test_admin_stations.py — station CRUD/activation admin routes
(T2, ARCH-station-management).
"""

from contextlib import contextmanager

import pytest
from flask import template_rendered

import main


@contextmanager
def captured_templates(app):
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template, context))

    template_rendered.connect(record, app)
    try:
        yield recorded
    finally:
        template_rendered.disconnect(record, app)


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
        self.delete_calls = []

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

    def delete_station(self, station_id):
        self.delete_calls.append(station_id)
        if station_id not in self.stations:
            raise KeyError(f"Station '{station_id}' not found")
        del self.stations[station_id]


@pytest.fixture
def fake_price_store(monkeypatch):
    fps = FakePriceStore()
    monkeypatch.setattr(main, "price_store", fps)
    return fps


class FakeRepo:
    def __init__(self, vouchers=None):
        self._vouchers = vouchers or []

    def list_all_vouchers(self):
        return list(self._vouchers)


@pytest.fixture
def fake_repo(monkeypatch):
    repo = FakeRepo()
    monkeypatch.setattr(main, "repo", repo)
    return repo


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
# Delete (T2, ARCH-booking-confirmation-note-and-station-delete)
# ============================================================

def test_post_admin_stations_delete_succeeds_no_booking_history(client, fake_price_store, fake_repo):
    _login(client)
    fake_price_store.stations["petron_makati"] = {
        "id": "petron_makati", "brand": "Petron", "name": "Makati", "location": "EDSA", "is_active": False,
    }
    fake_repo._vouchers = [{"station": "Some Other Station"}]

    r = client.post("/admin/stations/petron_makati/delete")

    assert r.status_code == 302
    assert fake_price_store.delete_calls == ["petron_makati"]


def test_post_admin_stations_delete_blocked_matching_voucher_name(client, fake_price_store, fake_repo):
    _login(client)
    fake_price_store.stations["petron_makati"] = {
        "id": "petron_makati", "brand": "Petron", "name": "Makati", "location": "EDSA", "is_active": False,
    }
    fake_repo._vouchers = [{"station": "Makati"}]

    r = client.post("/admin/stations/petron_makati/delete", follow_redirects=True)

    assert fake_price_store.delete_calls == []
    body = r.data.decode("utf-8")
    assert "Cannot delete: station has existing bookings." in body


def test_post_admin_stations_delete_unknown_id_returns_404(client, fake_price_store, fake_repo):
    _login(client)

    r = client.post("/admin/stations/does_not_exist/delete")

    assert r.status_code == 404


def test_post_admin_stations_delete_preserves_key_query_param_on_redirect(client, fake_price_store, fake_repo):
    _login(client)
    fake_price_store.stations["petron_makati"] = {
        "id": "petron_makati", "brand": "Petron", "name": "Makati", "location": "EDSA", "is_active": False,
    }

    r = client.post("/admin/stations/petron_makati/delete?key=testkey")

    assert r.status_code == 302
    assert "key=testkey" in r.headers["Location"]


def test_post_admin_stations_delete_store_error_flashes_instead_of_500(client, fake_price_store, fake_repo, monkeypatch):
    fake_price_store.stations["petron_makati"] = {
        "id": "petron_makati", "brand": "Petron", "name": "Makati", "location": "EDSA", "is_active": False,
    }
    def _raise(*a, **kw):
        raise RuntimeError("db unavailable")
    monkeypatch.setattr(fake_price_store, "delete_station", _raise)
    _login(client)

    r = client.post("/admin/stations/petron_makati/delete")

    assert r.status_code == 302


# ============================================================
# Template Rendering (T4, ARCH-station-management)
# ============================================================

def test_get_admin_stations_context_contains_full_station_list_incl_inactive(client, fake_price_store):
    _login(client)
    fake_price_store.stations["active1"] = {
        "id": "active1", "brand": "A", "name": "Active One", "location": "X", "is_active": True,
    }
    fake_price_store.stations["inactive1"] = {
        "id": "inactive1", "brand": "B", "name": "Inactive One", "location": "Y", "is_active": False,
    }

    with captured_templates(main.app) as templates:
        r = client.get("/admin/stations")

    assert r.status_code == 200
    assert len(templates) == 1
    template, context = templates[0]
    assert template.name == "admin_stations.html"
    ids = {s["id"] for s in context["stations"]}
    assert ids == {"active1", "inactive1"}


def test_get_admin_stations_inactive_flag_reaches_template_context(client, fake_price_store):
    _login(client)
    fake_price_store.stations["inactive1"] = {
        "id": "inactive1", "brand": "B", "name": "Inactive One", "location": "Y", "is_active": False,
    }

    with captured_templates(main.app) as templates:
        client.get("/admin/stations")

    _, context = templates[0]
    station = next(s for s in context["stations"] if s["id"] == "inactive1")
    assert station["is_active"] is False


def test_get_admin_stations_renders_inactive_row_css_class(client, fake_price_store):
    _login(client)
    fake_price_store.stations["inactive1"] = {
        "id": "inactive1", "brand": "B", "name": "Inactive One", "location": "Y", "is_active": False,
    }

    r = client.get("/admin/stations")

    assert 'class="inactive-row"' in r.data.decode("utf-8")


def test_get_admin_stations_delete_button_only_for_inactive_stations(client, fake_price_store):
    _login(client)
    fake_price_store.stations["active1"] = {
        "id": "active1", "brand": "A", "name": "Active One", "location": "X", "is_active": True,
    }
    fake_price_store.stations["inactive1"] = {
        "id": "inactive1", "brand": "B", "name": "Inactive One", "location": "Y", "is_active": False,
    }

    body = client.get("/admin/stations").data.decode("utf-8")

    active_row = body.split('data-station-id="active1"')[1].split("</tr>")[0]
    inactive_row = body.split('data-station-id="inactive1"')[1].split("</tr>")[0]
    assert "/admin/stations/active1/delete" not in active_row
    assert "/admin/stations/inactive1/delete" in inactive_row


def test_get_admin_stations_delete_form_preserves_key_query_param(client, fake_price_store):
    _login(client)
    fake_price_store.stations["inactive1"] = {
        "id": "inactive1", "brand": "B", "name": "Inactive One", "location": "Y", "is_active": False,
    }

    body = client.get("/admin/stations?key=testkey").data.decode("utf-8")

    assert "/admin/stations/inactive1/delete?key=testkey" in body


# ============================================================
# Key-Auth Redirect Preservation (review fix, High finding)
# ============================================================

def test_post_admin_stations_create_preserves_key_query_param_on_redirect(client, fake_price_store):
    _login(client)
    monkeypatch_key = "testkey"
    r = client.post(f"/admin/stations?key={monkeypatch_key}", data={
        "brand": "Petron", "name": "Makati", "location": "EDSA",
    })

    assert r.status_code == 302
    assert f"key={monkeypatch_key}" in r.headers["Location"]


def test_post_admin_stations_edit_preserves_key_query_param_on_redirect(client, fake_price_store):
    _login(client)
    fake_price_store.stations["petron_makati"] = {
        "id": "petron_makati", "brand": "Petron", "name": "Makati", "location": "EDSA", "is_active": True,
    }

    r = client.post("/admin/stations/petron_makati/edit?key=testkey", data={
        "brand": "Petron", "name": "Makati", "location": "EDSA",
    })

    assert r.status_code == 302
    assert "key=testkey" in r.headers["Location"]


def test_post_admin_stations_deactivate_preserves_key_query_param_on_redirect(client, fake_price_store):
    _login(client)
    fake_price_store.stations["petron_makati"] = {
        "id": "petron_makati", "brand": "Petron", "name": "Makati", "location": "EDSA", "is_active": True,
    }

    r = client.post("/admin/stations/petron_makati/deactivate?key=testkey")

    assert r.status_code == 302
    assert "key=testkey" in r.headers["Location"]


def test_post_admin_stations_reactivate_preserves_key_query_param_on_redirect(client, fake_price_store):
    _login(client)
    fake_price_store.stations["petron_makati"] = {
        "id": "petron_makati", "brand": "Petron", "name": "Makati", "location": "EDSA", "is_active": False,
    }

    r = client.post("/admin/stations/petron_makati/reactivate?key=testkey")

    assert r.status_code == 302
    assert "key=testkey" in r.headers["Location"]


# ============================================================
# Store-Error Handling (review fix, High finding)
# ============================================================

def test_post_admin_stations_create_store_error_flashes_instead_of_500(client, fake_price_store, monkeypatch):
    def _raise(*a, **kw):
        raise RuntimeError("db unavailable")
    monkeypatch.setattr(fake_price_store, "upsert_station", _raise)
    _login(client)

    r = client.post("/admin/stations", data={"brand": "Petron", "name": "Makati", "location": "EDSA"})

    assert r.status_code == 302


def test_post_admin_stations_edit_store_error_flashes_instead_of_500(client, fake_price_store, monkeypatch):
    fake_price_store.stations["petron_makati"] = {
        "id": "petron_makati", "brand": "Petron", "name": "Makati", "location": "EDSA", "is_active": True,
    }
    def _raise(*a, **kw):
        raise RuntimeError("db unavailable")
    monkeypatch.setattr(fake_price_store, "upsert_station", _raise)
    _login(client)

    r = client.post("/admin/stations/petron_makati/edit", data={
        "brand": "Petron", "name": "Renamed", "location": "EDSA",
    })

    assert r.status_code == 302


# ============================================================
# Regression Guard
# ============================================================

@pytest.mark.parametrize("method,path", [
    ("post", "/admin/stations"),
    ("post", "/admin/stations/some_id/edit"),
    ("post", "/admin/stations/some_id/deactivate"),
    ("post", "/admin/stations/some_id/reactivate"),
    ("post", "/admin/stations/some_id/delete"),
])
def test_all_station_routes_require_admin(client, fake_price_store, method, path):
    r = getattr(client, method)(path)

    assert r.status_code == 302
    assert "/admin/login" in r.headers["Location"]
