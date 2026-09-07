"""
tests/test_admin_notification_flags.py — notification flags and Resend on the
/admin dashboard (T9, ARCH-brief-11-email-notifications).

Failed and skipped notifications surface where admins already work, with a
per-row Resend. This is the recovery mechanism the whole no-blocking design
depends on: without it, a provider outage would silently swallow customer
emails.

Stubs main.repo and main.notifications directly — no Postgres needed.
"""

import pytest

import main


VOUCHER = {
    "voucher_id": "UF-TEST-00001",
    "account_code": "HARR",
    "driver_name": "Dave",
    "station": "EcoOil - Cainta",
    "status": "Unredeemed",
    "requested_amount_php": 1000,
}


class RepoStub:
    def __init__(self, vouchers=None):
        self._vouchers = vouchers if vouchers is not None else [dict(VOUCHER)]

    def list_recent_vouchers(self, limit=50):
        return [dict(v) for v in self._vouchers]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "s3cret")
    monkeypatch.setattr(main, "ADMIN_KEY", "testkey")
    monkeypatch.setattr(main, "repo", RepoStub())
    main.app.config.update(TESTING=True)
    return main.app.test_client()


def _login(client):
    client.post("/admin/login", data={"password": "s3cret"})


def _stub_flags(monkeypatch, flags, requeue_result=True, requeued=None):
    monkeypatch.setattr(
        main.notifications, "flags_by_voucher", lambda ids, **kw: flags
    )

    def fake_requeue(notification_id, recipient=None, **kw):
        if requeued is not None:
            requeued.append((notification_id, recipient))
        return requeue_result

    monkeypatch.setattr(main.notifications, "requeue", fake_requeue)


# ============================================================
# Flag display
# ============================================================

def test_a_failed_notification_shows_a_flag(client, monkeypatch):
    """GIVEN a voucher whose email failed WHEN /admin renders THEN that row
    carries a visible failure flag (verifies R7)."""
    _stub_flags(monkeypatch, {
        "UF-TEST-00001": {
            "notification_id": 7, "kind": "booking_confirmed",
            "status": "failed", "attempts": 5, "last_error": "provider said no",
        }
    })
    _login(client)

    html = client.get("/admin").get_data(as_text=True)

    assert "Failed" in html
    assert "/admin/notifications/7/resend" in html


def test_a_skipped_notification_shows_the_no_email_flag(client, monkeypatch):
    """GIVEN a booking whose customer has no address WHEN /admin renders THEN
    the row says so, and points at the fix (verifies R6, R7)."""
    _stub_flags(monkeypatch, {
        "UF-TEST-00001": {
            "notification_id": 8, "kind": "booking_received",
            "status": "skipped", "attempts": 0,
            "last_error": "customer has no email on file",
        }
    })
    _login(client)

    html = client.get("/admin").get_data(as_text=True)

    assert "No email" in html
    assert "/admin/notifications/8/resend" in html


def test_rows_without_notifications_carry_no_flag(client, monkeypatch):
    """A delivered or unsent email must not light up the dashboard
    (guards ARCH backward-regression risk for templates/admin.html)."""
    _stub_flags(monkeypatch, {})
    _login(client)

    html = client.get("/admin").get_data(as_text=True)

    # The CSS block always defines .mail-flag; what must be absent is the
    # rendered badge and the resend form.
    assert 'class="mail-flag' not in html
    assert "/admin/notifications/" not in html


def test_the_dashboard_survives_an_unavailable_outbox(client, monkeypatch):
    """GIVEN the flag query raises WHEN /admin renders THEN the voucher table
    still loads. /admin is the operational hub; it must degrade to 'no flags',
    never to a 500 (verifies ARCH forward stress-test)."""
    def boom(ids, **kw):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(main.notifications, "flags_by_voucher", boom)
    _login(client)

    resp = client.get("/admin")

    assert resp.status_code == 200
    assert b"UF-TEST-00001" in resp.data


def test_the_dashboard_renders_without_the_notifications_module(client, monkeypatch):
    """The module import is guarded; the dashboard must not assume it loaded."""
    monkeypatch.setattr(main, "notifications", None)
    _login(client)

    resp = client.get("/admin")

    assert resp.status_code == 200
    assert b"UF-TEST-00001" in resp.data


def test_the_png_column_still_renders(client, monkeypatch):
    """The pre-existing PNG status column is unaffected by the new one
    (guards ARCH backward-regression risk for main.py's /admin row loop)."""
    _stub_flags(monkeypatch, {})
    _login(client)

    html = client.get("/admin").get_data(as_text=True)

    assert "/delete_png/UF-TEST-00001" in html or "Deleted" in html
    assert "_Official.png" in html


# ============================================================
# Resend
# ============================================================

def test_resend_requeues_the_notification(client, monkeypatch):
    """GIVEN a flagged notification WHEN an admin posts Resend THEN it is
    requeued (verifies R8)."""
    requeued = []
    _stub_flags(monkeypatch, {}, requeued=requeued)
    _login(client)

    resp = client.post("/admin/notifications/7/resend")

    assert resp.status_code == 302
    assert requeued == [(7, None)]


def test_resend_requires_admin(client, monkeypatch):
    requeued = []
    _stub_flags(monkeypatch, {}, requeued=requeued)

    resp = client.post("/admin/notifications/7/resend")

    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]
    assert requeued == []


def test_resend_returns_404_for_an_unknown_notification(client, monkeypatch):
    _stub_flags(monkeypatch, {}, requeue_result=False)
    _login(client)

    resp = client.post("/admin/notifications/999999/resend")

    assert resp.status_code == 404


def test_resend_is_audited(client, monkeypatch):
    """Admin actions on customer-facing mail belong in the audit trail."""
    audits = []
    _stub_flags(monkeypatch, {})
    monkeypatch.setattr(
        main, "append_audit",
        lambda action, voucher_id, **kw: audits.append((action, kw.get("note"))),
    )
    _login(client)

    client.post("/admin/notifications/7/resend")

    assert any(a[0] == "notification_resend" for a in audits)


def test_resend_rejects_a_malformed_recipient_override(client, monkeypatch):
    """The optional recipient override goes through the same validation as
    /register — an invalid address must not reach the outbox."""
    requeued = []
    _stub_flags(monkeypatch, {}, requeued=requeued)
    _login(client)

    resp = client.post("/admin/notifications/7/resend", data={"recipient": "nope"})

    assert resp.status_code == 302
    assert requeued == []


def test_the_dashboard_links_to_recipient_management(client, monkeypatch):
    """The recipients page is only reachable from here. Without this link it
    exists but nobody can find it — which is exactly what happened when T10
    and T11 shipped the routes and the page but no navigation to them."""
    _stub_flags(monkeypatch, {})
    _login(client)

    html = client.get("/admin").get_data(as_text=True)

    assert "/admin/recipients" in html
