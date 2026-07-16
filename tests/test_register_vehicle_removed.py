"""
tests/test_register_vehicle_removed.py — /register-vehicle deleted, admin
nav repointed to /register (T1, ARCH-cleanup-register-vehicle).
"""

import pytest

import data_paths
import main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "s3cret")
    monkeypatch.setattr(main, "ADMIN_KEY", "testkey")
    main.app.config.update(TESTING=True)
    return main.app.test_client()


def _login(client):
    client.post("/admin/login", data={"password": "s3cret"})


def test_get_register_vehicle_returns_404(client):
    r = client.get("/register-vehicle")
    assert r.status_code == 404


def test_post_register_vehicle_returns_404(client):
    r = client.post("/register-vehicle", data={"account_code": "HARR"})
    assert r.status_code == 404


def test_admin_dashboard_button_points_to_register(client):
    _login(client)
    r = client.get("/admin")
    assert b'href="/register"' in r.data
    assert b">Register<" in r.data
    assert b"/register-vehicle" not in r.data
    assert b"Register Vehicle" not in r.data
