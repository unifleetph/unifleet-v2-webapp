"""
tests/test_admin_auth.py — admin dashboard session authentication (T3).

Session login + logout, require_admin guard (session OR legacy ?key= fallback),
ADMIN_PASSWORD / ADMIN_KEY via module globals (monkeypatched, no env needed).
"""

import pytest

import main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "s3cret")
    monkeypatch.setattr(main, "ADMIN_KEY", "testkey")
    main.app.config.update(TESTING=True)
    return main.app.test_client()


# ============================================================
# Login
# ============================================================

def test_login_page_renders(client):
    """GET /admin/login renders a password form."""
    r = client.get("/admin/login")
    assert r.status_code == 200
    assert b'name="password"' in r.data


def test_correct_password_grants_session(client):
    """Correct password -> 302 to /admin/prices, then /admin/prices is 200."""
    r = client.post("/admin/login", data={"password": "s3cret"})
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/admin/prices")
    r2 = client.get("/admin/prices")
    assert r2.status_code == 200


def test_wrong_password_rejected(client):
    """Wrong password does not authenticate; /admin/prices still gated."""
    client.post("/admin/login", data={"password": "nope"})
    r = client.get("/admin/prices")
    assert r.status_code == 302
    assert "/admin/login" in r.headers["Location"]


# ============================================================
# Access control
# ============================================================

def test_unauthenticated_admin_redirected_to_login(client):
    """No session and no key -> redirect to /admin/login."""
    r = client.get("/admin/prices")
    assert r.status_code == 302
    assert "/admin/login" in r.headers["Location"]


def test_logout_clears_session(client):
    """After logout, /admin/prices is gated again."""
    client.post("/admin/login", data={"password": "s3cret"})
    client.get("/admin/logout")
    r = client.get("/admin/prices")
    assert r.status_code == 302
    assert "/admin/login" in r.headers["Location"]


# ============================================================
# Regression — legacy ?key= fallback
# ============================================================

def test_legacy_key_fallback_still_works(client):
    """GET /admin/prices?key=<ADMIN_KEY> authenticates without a session."""
    r = client.get("/admin/prices?key=testkey")
    assert r.status_code == 200
