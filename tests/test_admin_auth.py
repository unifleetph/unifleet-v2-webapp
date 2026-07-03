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
    """Correct password with no next -> 302 to /admin (dashboard)."""
    r = client.post("/admin/login", data={"password": "s3cret"})
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/admin")
    r2 = client.get("/admin")
    assert r2.status_code == 200


def test_login_redirects_to_next(client):
    """Login honors a safe ?next= target (the page the user was headed to)."""
    r = client.post("/admin/login", data={"password": "s3cret", "next": "/admin/prices"})
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/admin/prices")


def test_login_rejects_open_redirect(client):
    """An off-site next is ignored; falls back to /admin."""
    r = client.post("/admin/login", data={"password": "s3cret", "next": "//evil.com"})
    assert r.status_code == 302
    assert "evil.com" not in r.headers["Location"]
    assert r.headers["Location"].endswith("/admin")


def test_guard_passes_next_to_login(client):
    """Hitting /admin unauthenticated redirects to login carrying next=/admin."""
    r = client.get("/admin")
    assert r.status_code == 302
    assert "next=%2Fadmin" in r.headers["Location"] or "next=/admin" in r.headers["Location"]


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


# ============================================================
# Admin dashboard (/admin, formerly /form) is gated
# ============================================================

def test_admin_dashboard_requires_auth(client):
    """GET /admin with no session/key redirects to login."""
    r = client.get("/admin")
    assert r.status_code == 302
    assert "/admin/login" in r.headers["Location"]


def test_admin_dashboard_key_fallback(client):
    """GET /admin?key=<ADMIN_KEY> loads the dashboard."""
    r = client.get("/admin?key=testkey")
    assert r.status_code == 200


def test_admin_dashboard_session_access(client):
    """After login, /admin loads."""
    client.post("/admin/login", data={"password": "s3cret"})
    r = client.get("/admin")
    assert r.status_code == 200
