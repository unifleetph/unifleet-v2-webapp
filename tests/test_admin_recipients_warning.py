"""
tests/test_admin_recipients_warning.py — the admin recipients page warns when
nobody is set to receive the daily supplier report (T8,
ARCH-midnight-supplier-report A11).

With no active recipient there is nobody to send the report to, which from the
outside looks exactly like the report not working. The page says so. The
recipient list itself is stubbed, so no database is needed.
"""

import pytest

import main


def _recipient(rid, email, active=True):
    return {"id": rid, "email": email, "label": "", "is_active": active, "created_at": None}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "s3cret")
    main.app.config.update(TESTING=True)
    c = main.app.test_client()
    c.post("/admin/login", data={"password": "s3cret"})
    return c


def _page(client, monkeypatch, recipients):
    monkeypatch.setattr(main.report_recipients, "list_all",
                        lambda include_inactive=False, **kw: list(recipients))
    resp = client.get("/admin/recipients")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


WARNING = "No active recipients"


def test_the_warning_shows_when_the_list_is_empty(client, monkeypatch):
    html = _page(client, monkeypatch, [])

    assert WARNING in html


def test_the_warning_shows_when_everyone_is_paused(client, monkeypatch):
    html = _page(client, monkeypatch, [
        _recipient(1, "a@example.com", active=False),
        _recipient(2, "b@example.com", active=False),
    ])

    assert WARNING in html
    assert "a@example.com" in html          # paused rows are still listed


def test_there_is_no_warning_with_one_active_recipient(client, monkeypatch):
    html = _page(client, monkeypatch, [
        _recipient(1, "a@example.com", active=True),
        _recipient(2, "b@example.com", active=False),
    ])

    assert WARNING not in html


def test_the_warning_is_announced_as_text_not_only_colour(client, monkeypatch):
    html = _page(client, monkeypatch, [])

    assert 'role="alert"' in html
    assert "nothing will be sent" in html.lower()


def test_the_add_form_and_lists_are_still_there(client, monkeypatch):
    """Guards the page: the warning is added, nothing else moves."""
    html = _page(client, monkeypatch, [_recipient(7, "a@example.com")])

    assert 'name="email"' in html
    assert "/admin/recipients/7/delete" in html
    assert "/admin/recipients/7/active" in html


def test_the_page_still_reports_unavailable_email_support(client, monkeypatch):
    """Guards the existing behaviour when the notifications module failed to
    load: the page redirects away with the explanatory flash, no warning."""
    monkeypatch.setattr(main, "report_recipients", None)
    monkeypatch.setattr(main, "_NOTIFICATIONS_IMPORT_ERROR", "boom")

    resp = client.get("/admin/recipients")

    assert resp.status_code == 302
    assert "/admin/recipients" not in resp.headers["Location"]
