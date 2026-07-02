"""
tests/test_root_redirect.py — root redirects to the Booking page (T1).
"""

import main


def test_redirects_root_to_booking():
    """GET / -> 302 to /book."""
    client = main.app.test_client()
    resp = client.get("/")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/book")


def test_booking_page_still_reachable():
    """GET /book -> 200 (regression guard)."""
    client = main.app.test_client()
    resp = client.get("/book")
    assert resp.status_code == 200
