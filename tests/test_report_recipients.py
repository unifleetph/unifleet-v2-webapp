"""
tests/test_report_recipients.py — the internal distribution list for the
nightly supplier PDF (T10, ARCH-brief-11-email-notifications).

Postgres-backed like margin_store.py, reached through db.pool. Admin-managed
rather than an env var because staff churn must not require a redeploy (A13).
"""

import psycopg
import pytest

import main
import report_recipients


@pytest.fixture(autouse=True)
def _use_test_dsn(schema_db):
    import db.pool as pool_module

    pool_module.reset_pool()
    yield
    pool_module.reset_pool()


@pytest.fixture(autouse=True)
def _clean(schema_db):
    def _truncate():
        with psycopg.connect(schema_db) as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE report_recipients")
            conn.commit()

    _truncate()
    yield
    _truncate()


# ============================================================
# CRUD
# ============================================================

def test_add_then_list(schema_db):
    """GIVEN an empty list WHEN a recipient is added THEN it appears with its
    label (verifies R14)."""
    report_recipients.add("ops@example.com", label="ops team", dsn=schema_db)

    rows = report_recipients.list_all(dsn=schema_db)

    assert len(rows) == 1
    assert rows[0]["email"] == "ops@example.com"
    assert rows[0]["label"] == "ops team"
    assert rows[0]["is_active"] is True


def test_delete_removes_the_recipient(schema_db):
    added = report_recipients.add("ops@example.com", dsn=schema_db)

    assert report_recipients.delete(added["id"], dsn=schema_db) is True
    assert report_recipients.list_all(dsn=schema_db) == []


def test_delete_returns_false_for_an_unknown_id(schema_db):
    assert report_recipients.delete(999999, dsn=schema_db) is False


def test_a_duplicate_address_is_rejected(schema_db):
    """GIVEN an existing recipient WHEN the same address is added again THEN
    it returns None and no second row appears."""
    report_recipients.add("ops@example.com", dsn=schema_db)

    assert report_recipients.add("ops@example.com", dsn=schema_db) is None
    assert len(report_recipients.list_all(dsn=schema_db)) == 1


def test_addresses_are_normalised_to_lowercase(schema_db):
    """Otherwise Ops@example.com and ops@example.com are two rows, and the
    nightly report goes out twice to one mailbox."""
    report_recipients.add("  Ops@Example.COM  ", dsn=schema_db)

    rows = report_recipients.list_all(dsn=schema_db)

    assert rows[0]["email"] == "ops@example.com"
    assert report_recipients.add("ops@example.com", dsn=schema_db) is None


def test_active_emails_excludes_inactive(schema_db):
    """Soft-disabling stops the report without losing who was on the list."""
    active = report_recipients.add("ops@example.com", dsn=schema_db)
    muted = report_recipients.add("onleave@example.com", dsn=schema_db)
    report_recipients.set_active(muted["id"], False, dsn=schema_db)

    emails = report_recipients.active_emails(dsn=schema_db)

    assert emails == ["ops@example.com"]
    assert active["id"] != muted["id"]
    assert len(report_recipients.list_all(include_inactive=True, dsn=schema_db)) == 2


def test_an_empty_list_is_a_valid_state(schema_db):
    """The list ships empty by design; the cron treats that as 'nothing to do',
    not an error (verifies REQ edge case)."""
    assert report_recipients.active_emails(dsn=schema_db) == []
    assert report_recipients.list_all(dsn=schema_db) == []


def test_a_malformed_address_is_rejected(schema_db):
    assert report_recipients.add("not-an-email", dsn=schema_db) is None
    assert report_recipients.list_all(dsn=schema_db) == []


def test_a_database_failure_is_swallowed():
    """Consistent with the rest of the mail modules: the admin page shows an
    empty list rather than a stack trace."""
    dead = "postgresql://nobody:nobody@127.0.0.1:1/nonexistent?connect_timeout=1"

    assert report_recipients.list_all(dsn=dead) == []
    assert report_recipients.active_emails(dsn=dead) == []


# ============================================================
# HTTP routes
# ============================================================

@pytest.fixture
def client(monkeypatch, schema_db):
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "s3cret")
    monkeypatch.setattr(main, "ADMIN_KEY", "testkey")
    main.app.config.update(TESTING=True)
    return main.app.test_client()


def _login(client):
    client.post("/admin/login", data={"password": "s3cret"})


def test_the_recipients_page_requires_admin(client):
    resp = client.get("/admin/recipients")

    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_the_add_route_requires_admin(client, monkeypatch):
    added = []
    monkeypatch.setattr(main.report_recipients, "add",
                        lambda email, label="": added.append(email))

    resp = client.post("/admin/recipients", data={"email": "ops@example.com"})

    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]
    assert added == []


def test_the_delete_route_requires_admin(client, monkeypatch):
    deleted = []
    monkeypatch.setattr(main.report_recipients, "delete", lambda rid: deleted.append(rid))

    resp = client.post("/admin/recipients/1/delete")

    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]
    assert deleted == []


def test_adding_through_the_route_stores_the_recipient(client, monkeypatch):
    added = []
    monkeypatch.setattr(
        main.report_recipients, "add",
        lambda email, label="": added.append((email, label)) or {"id": 1, "email": email},
    )
    monkeypatch.setattr(main.report_recipients, "list_all", lambda **kw: [])
    _login(client)

    resp = client.post("/admin/recipients",
                       data={"email": "ops@example.com", "label": "ops"})

    assert resp.status_code == 302
    assert added == [("ops@example.com", "ops")]


def test_the_route_rejects_a_malformed_address(client, monkeypatch):
    added = []
    monkeypatch.setattr(main.report_recipients, "add",
                        lambda email, label="": added.append(email))
    monkeypatch.setattr(main.report_recipients, "list_all", lambda **kw: [])
    _login(client)

    resp = client.post("/admin/recipients", data={"email": "nope"})

    assert resp.status_code == 302
    assert added == []


def test_recipient_changes_are_audited(client, monkeypatch):
    audits = []
    monkeypatch.setattr(
        main.report_recipients, "add",
        lambda email, label="": {"id": 1, "email": email},
    )
    monkeypatch.setattr(main.report_recipients, "list_all", lambda **kw: [])
    monkeypatch.setattr(
        main, "append_audit",
        lambda action, voucher_id, **kw: audits.append(action),
    )
    _login(client)

    client.post("/admin/recipients", data={"email": "ops@example.com"})

    assert "recipient_add" in audits


# ============================================================
# T11 — page seams
# ============================================================
# Aesthetics are verified by eye (see the T11 checklist); these cover what
# automation can judge: the page renders in both states, conditional content
# appears, flashes surface, and the delete form targets the right route.

def test_the_page_renders_the_empty_state(client, monkeypatch):
    """The list ships empty, so this is the first thing an admin ever sees —
    it must explain the state rather than showing a bare heading."""
    monkeypatch.setattr(main.report_recipients, "list_all", lambda **kw: [])
    _login(client)

    html = client.get("/admin/recipients").get_data(as_text=True)

    assert "No recipients yet" in html
    assert "<table" not in html


def test_the_page_renders_each_recipient(client, monkeypatch):
    import datetime as dt

    monkeypatch.setattr(main.report_recipients, "list_all", lambda **kw: [
        {"id": 1, "email": "ops@example.com", "label": "Ops team",
         "is_active": True, "created_at": dt.datetime(2026, 9, 7),
         "updated_at": dt.datetime(2026, 9, 7)},
        {"id": 2, "email": "onleave@example.com", "label": None,
         "is_active": False, "created_at": dt.datetime(2026, 9, 7),
         "updated_at": dt.datetime(2026, 9, 7)},
    ])
    _login(client)

    html = client.get("/admin/recipients").get_data(as_text=True)

    assert "ops@example.com" in html
    assert "onleave@example.com" in html
    assert "No recipients yet" not in html


def test_an_inactive_recipient_is_shown_as_inactive(client, monkeypatch):
    """Muted rather than hidden — 'still on the list, not currently
    receiving' is the useful state, matching the station page's convention."""
    import datetime as dt

    monkeypatch.setattr(main.report_recipients, "list_all", lambda **kw: [
        {"id": 2, "email": "onleave@example.com", "label": None,
         "is_active": False, "created_at": dt.datetime(2026, 9, 7),
         "updated_at": dt.datetime(2026, 9, 7)},
    ])
    _login(client)

    html = client.get("/admin/recipients").get_data(as_text=True)

    assert "inactive-row" in html
    assert "Inactive" in html


def test_the_duplicate_flash_reaches_the_page(client, monkeypatch):
    """A silent no-op would read as success; the admin must see why nothing
    changed."""
    monkeypatch.setattr(main.report_recipients, "add", lambda email, label="": None)
    monkeypatch.setattr(main.report_recipients, "list_all", lambda **kw: [])
    _login(client)

    resp = client.post("/admin/recipients",
                       data={"email": "ops@example.com"}, follow_redirects=True)

    assert b"already on the list" in resp.data


def test_the_delete_control_targets_the_delete_route_and_confirms(client, monkeypatch):
    """Removing someone from the nightly report is easy to do by accident."""
    import datetime as dt

    monkeypatch.setattr(main.report_recipients, "list_all", lambda **kw: [
        {"id": 7, "email": "ops@example.com", "label": "Ops",
         "is_active": True, "created_at": dt.datetime(2026, 9, 7),
         "updated_at": dt.datetime(2026, 9, 7)},
    ])
    _login(client)

    html = client.get("/admin/recipients").get_data(as_text=True)

    assert "/admin/recipients/7/delete" in html
    assert "confirm(" in html


def test_the_email_input_is_labelled(client, monkeypatch):
    """Accessibility basic: the field has a real label, not just a placeholder."""
    monkeypatch.setattr(main.report_recipients, "list_all", lambda **kw: [])
    _login(client)

    html = client.get("/admin/recipients").get_data(as_text=True)

    assert 'for="new-email"' in html
    assert 'id="new-email"' in html


def test_the_delete_confirmation_does_not_interpolate_the_address(client, monkeypatch):
    """A stored address must never reach a JS string literal.

    Jinja escapes `'` to `&#39;`, but the HTML parser decodes that back to a
    real quote before the JS parser sees it, so escaping does not hold inside
    an onsubmit handler. _EMAIL_RE permits `'` (it only excludes @ and
    whitespace), so a crafted address would break out and execute in an
    authenticated admin session (review finding B6).
    """
    import datetime as dt

    hostile = "x'-alert(1)-'@evil.co"
    monkeypatch.setattr(main.report_recipients, "list_all", lambda **kw: [
        {"id": 1, "email": hostile, "label": None, "is_active": True,
         "created_at": dt.datetime(2026, 9, 8), "updated_at": dt.datetime(2026, 9, 8)},
    ])
    _login(client)

    html = client.get("/admin/recipients").get_data(as_text=True)

    # The address still renders in text context, where escaping does hold.
    assert "evil.co" in html
    # But no confirm() handler may carry any part of it.
    for line in html.splitlines():
        if "confirm(" in line:
            assert "alert(1)" not in line, f"address reached a JS handler: {line.strip()}"
            assert "evil.co" not in line, f"address reached a JS handler: {line.strip()}"


# ============================================================
# F12 — an unreachable database must not look like an empty list
# ============================================================

def test_strict_mode_raises_instead_of_returning_empty():
    """GIVEN the database is unreachable WHEN active_emails(strict=True) is
    called THEN it raises.

    The nightly cron needs this: swallowing turned an outage into an empty
    list, so the script took its "no recipients, nothing to send" branch and
    exited 0 — a green Railway run on the night the report did not go out
    (review finding F12).
    """
    dead = "postgresql://nobody:nobody@127.0.0.1:1/nonexistent?connect_timeout=1"

    with pytest.raises(Exception):
        report_recipients.active_emails(dsn=dead, strict=True)


def test_the_web_app_default_still_swallows():
    """The admin page prefers an empty list to a stack trace."""
    dead = "postgresql://nobody:nobody@127.0.0.1:1/nonexistent?connect_timeout=1"

    assert report_recipients.active_emails(dsn=dead) == []
    assert report_recipients.list_all(dsn=dead) == []
