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

_MISSING = object()


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


def _stub_flags(monkeypatch, flags, requeue_result=True, requeued=None, row=_MISSING):
    monkeypatch.setattr(
        main.notifications, "flags_by_voucher", lambda ids, **kw: flags
    )

    def fake_requeue(notification_id, recipient=None, **kw):
        if requeued is not None:
            requeued.append((notification_id, recipient))
        return requeue_result

    monkeypatch.setattr(main.notifications, "requeue", fake_requeue)
    monkeypatch.setattr(main.notifications, "list_flagged", lambda **kw: [])

    stored = {
        "id": 7, "kind": "booking_confirmed", "recipient": "driver@example.com",
        "account_code": "HARR", "voucher_id": "UF-TEST-00001",
        "status": "failed", "attempts": 5, "last_error": "provider said no",
    } if row is _MISSING else row
    monkeypatch.setattr(main.notifications, "get", lambda nid, **kw: stored)


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


def test_resend_reports_an_unknown_notification(client, monkeypatch):
    """An unknown id flashes and redirects, like every other admin route.

    It used to return a bare 404. That both broke the convention and, because
    requeue returns False on any exception, told the admin "not found" when
    the truth was "the database is down" (review finding).
    """
    _stub_flags(monkeypatch, {}, requeue_result=False, row=None)
    _login(client)

    resp = client.post("/admin/notifications/999999/resend", follow_redirects=True)

    assert resp.status_code == 200
    assert b"Notification not found" in resp.data


def test_resend_distinguishes_a_requeue_failure_from_a_missing_row(client, monkeypatch):
    """A row that exists but cannot be requeued (e.g. the database went away
    between the read and the write) must not be reported as missing."""
    _stub_flags(monkeypatch, {}, requeue_result=False)
    monkeypatch.setattr(main, "repo", _CustomerRepoStub())
    _login(client)

    resp = client.post("/admin/notifications/7/resend", follow_redirects=True)

    assert b"Could not requeue" in resp.data
    assert b"not found" not in resp.data


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


# ============================================================
# B1 — Resend must pick up an address added after the skip
# ============================================================

class _CustomerRepoStub(RepoStub):
    def __init__(self, customers=None, **kw):
        super().__init__(**kw)
        self._customers = customers or {}

    def get_customer(self, account_code):
        return self._customers.get(str(account_code or "").strip().upper())


def test_resend_resolves_a_missing_recipient_from_the_customer_record(client, monkeypatch):
    """GIVEN a skipped notification whose customer now HAS an email WHEN an
    admin clicks Resend THEN the row is requeued against that address.

    This is the R6 -> R9 -> R8 recovery: a legacy customer books with no email
    on file, an admin adds one, and Resend delivers. Before the fix the route
    passed recipient=None and the worker was handed a row it could only fail
    to send (review finding B1).
    """
    requeued = []
    skipped_row = {
        "id": 9, "kind": "booking_received", "recipient": None,
        "account_code": "HARR", "voucher_id": "UF-TEST-00001",
        "status": "skipped", "attempts": 0, "last_error": "no email on file",
    }
    _stub_flags(monkeypatch, {}, requeued=requeued, row=skipped_row)
    monkeypatch.setattr(
        main, "repo", _CustomerRepoStub(customers={"HARR": {"email": "fixed@example.com"}})
    )
    _login(client)

    resp = client.post("/admin/notifications/9/resend")

    assert resp.status_code == 302
    assert requeued == [(9, "fixed@example.com")], (
        "Resend must requeue against the address the admin just added"
    )


def test_resend_refuses_when_the_customer_still_has_no_email(client, monkeypatch):
    """GIVEN a skipped row whose customer still has no address WHEN Resend is
    clicked THEN nothing is requeued and the admin is told what to do."""
    requeued = []
    skipped_row = {
        "id": 9, "kind": "booking_received", "recipient": None,
        "account_code": "HARR", "voucher_id": "UF-TEST-00001",
        "status": "skipped", "attempts": 0, "last_error": "no email on file",
    }
    _stub_flags(monkeypatch, {}, requeued=requeued, row=skipped_row)
    monkeypatch.setattr(main, "repo", _CustomerRepoStub(customers={"HARR": {"email": ""}}))
    _login(client)

    resp = client.post("/admin/notifications/9/resend", follow_redirects=True)

    assert requeued == [], "must not requeue a row the worker cannot send"
    assert b"still has no email address on file" in resp.data


def test_resend_keeps_an_existing_recipient(client, monkeypatch):
    """A failed row already has an address; the lookup must not disturb it."""
    requeued = []
    _stub_flags(monkeypatch, {}, requeued=requeued)
    monkeypatch.setattr(
        main, "repo", _CustomerRepoStub(customers={"HARR": {"email": "other@example.com"}})
    )
    _login(client)

    client.post("/admin/notifications/7/resend")

    assert requeued == [(7, None)], (
        "an existing recipient is left alone; requeue keeps the row's own address"
    )


def test_an_explicit_recipient_override_still_wins(client, monkeypatch):
    requeued = []
    _stub_flags(monkeypatch, {}, requeued=requeued)
    monkeypatch.setattr(main, "repo", _CustomerRepoStub())
    _login(client)

    client.post("/admin/notifications/7/resend", data={"recipient": "override@example.com"})

    assert requeued == [(7, "override@example.com")]


# ============================================================
# F7 / F8 — flags that no voucher row can carry
# ============================================================

def _stub_flagged(monkeypatch, rows):
    monkeypatch.setattr(main.notifications, "list_flagged", lambda **kw: rows)


def test_a_failed_account_code_email_is_visible(client, monkeypatch):
    """GIVEN a failed registration email WHEN /admin renders THEN it appears.

    Account-code notifications carry voucher_id = NULL, so the per-row join
    can never show them — a failed registration email was invisible in every
    admin surface, and R7 requires it to be visible (review finding F7).
    """
    _stub_flags(monkeypatch, {})
    _stub_flagged(monkeypatch, [{
        "id": 11, "kind": "account_code", "recipient": "new@example.com",
        "account_code": "HARR", "voucher_id": None, "status": "failed",
        "attempts": 5, "last_error": "provider said no", "created_at": None,
    }])
    _login(client)

    html = client.get("/admin").get_data(as_text=True)

    assert "account_code" in html
    assert "new@example.com" in html
    assert "/admin/notifications/11/resend" in html


def test_an_attachment_missing_send_is_flagged(client, monkeypatch):
    """A confirmation that shipped without its voucher PNG is `sent`, so it
    was filtered out of both admin queries — invisible, despite A6 promising
    a manual-follow-up flag (review finding F8)."""
    _stub_flags(monkeypatch, {
        "UF-TEST-00001": {
            "notification_id": 12, "kind": "booking_confirmed",
            "status": "sent", "attempts": 1, "last_error": "attachment_missing",
        }
    })
    _stub_flagged(monkeypatch, [])
    _login(client)

    html = client.get("/admin").get_data(as_text=True)

    assert "Sent, no voucher" in html
    assert "/admin/notifications/12/resend" in html


def test_the_problem_list_is_absent_when_nothing_is_flagged(client, monkeypatch):
    _stub_flags(monkeypatch, {})
    _stub_flagged(monkeypatch, [])
    _login(client)

    html = client.get("/admin").get_data(as_text=True)

    assert "Email problems needing attention" not in html


def test_the_dashboard_survives_a_failing_flagged_list(client, monkeypatch):
    """/admin must degrade, not 500, if the outbox is unavailable."""
    _stub_flags(monkeypatch, {})

    def boom(**kw):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(main.notifications, "list_flagged", boom)
    _login(client)

    resp = client.get("/admin")

    assert resp.status_code == 200
    assert b"UF-TEST-00001" in resp.data


def test_resend_does_not_redirect_off_site(client, monkeypatch):
    """Referer is attacker-controlled, so a cross-site POST could bounce an
    authenticated admin to any external page — a workable phishing step. The
    project already had _safe_next() for this and admin_login used it; the new
    routes did not (review finding F18)."""
    _stub_flags(monkeypatch, {})
    monkeypatch.setattr(main, "repo", _CustomerRepoStub())
    _login(client)

    resp = client.post(
        "/admin/notifications/7/resend",
        headers={"Referer": "https://evil.tld/phish"},
    )

    assert resp.status_code == 302
    assert "evil.tld" not in resp.headers["Location"]
    assert resp.headers["Location"].startswith("/")


def test_resend_still_returns_to_a_same_site_referrer(client, monkeypatch):
    """The guard must not break the normal case."""
    _stub_flags(monkeypatch, {})
    monkeypatch.setattr(main, "repo", _CustomerRepoStub())
    _login(client)

    resp = client.post(
        "/admin/notifications/7/resend",
        headers={"Referer": "http://localhost/admin?q=1"},
    )

    assert resp.headers["Location"].endswith("/admin?q=1")


def test_a_protocol_relative_referrer_is_rejected(client, monkeypatch):
    """//evil.tld is a URL the browser treats as absolute — the classic way
    past a naive startswith('/') check."""
    _stub_flags(monkeypatch, {})
    monkeypatch.setattr(main, "repo", _CustomerRepoStub())
    _login(client)

    resp = client.post(
        "/admin/notifications/7/resend",
        headers={"Referer": "//evil.tld/phish"},
    )

    assert "evil.tld" not in resp.headers["Location"]
