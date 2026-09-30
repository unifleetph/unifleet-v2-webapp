"""
tests/test_daily_report_tick.py — the daily supplier report's own logic
(T5/T6, ARCH-midnight-supplier-report).

daily_report.py owns the one definition of a "live order", the Manila report
date, the PDF build, and (T6) the idempotent "queue today's report" tick. The
repo, recipients and outbox are stubbed, so no Postgres is needed here.
"""

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import daily_report  # noqa: E402
from test_report_pdf import _page_count, _pdf_text  # noqa: E402


class RepoStub:
    def __init__(self, vouchers=None):
        self._vouchers = list(vouchers or [])

    def list_all_vouchers(self):
        return [dict(v) for v in self._vouchers]


def _v(voucher_id, status="Unredeemed", **extra):
    row = {
        "voucher_id": voucher_id, "status": status, "station": "EcoOil - Cainta",
        "driver_name": "Dave", "vehicle_plate": "XYZ-123", "fuel_type": "Biodiesel",
        "requested_amount_php": 1000, "created_at": "2026-09-01T10:00:00",
    }
    row.update(extra)
    return row


def _ids(rows):
    return [r["voucher_id"] for r in rows]


# ============================================================
# Live-order rule (R3, R4, R5)
# ============================================================

def test_deleted_orders_are_out_and_the_three_live_statuses_are_in():
    repo = RepoStub([
        _v("UF-UNVERIFIED", "Unverified"),
        _v("UF-UNREDEEMED", "Unredeemed"),
        _v("UF-REDEEMED", "Redeemed"),
        _v("UF-DELETED", "Unredeemed", deleted_at="2026-09-02T00:00:00"),
    ])

    assert set(_ids(daily_report.live_orders(repo))) == {
        "UF-UNVERIFIED", "UF-UNREDEEMED", "UF-REDEEMED",
    }


def test_a_status_outside_the_three_is_not_live():
    repo = RepoStub([_v("UF-OK", "Unredeemed"), _v("UF-ODD", "Something Else")])

    assert _ids(daily_report.live_orders(repo)) == ["UF-OK"]


def test_a_blank_status_is_live_and_reads_unverified_in_the_pdf():
    repo = RepoStub([_v("UF-BLANK", ""), _v("UF-NONE", None)])

    assert set(_ids(daily_report.live_orders(repo))) == {"UF-BLANK", "UF-NONE"}
    text = _pdf_text(daily_report.build_pdf(repo, "2026-09-20"))
    assert "UF-BLANK" in text and "Unverified" in text


def test_there_is_no_date_window():
    repo = RepoStub([_v("UF-OLD", "Redeemed", created_at="2024-01-05T08:00:00")])

    assert _ids(daily_report.live_orders(repo)) == ["UF-OLD"]


def test_orders_come_back_oldest_first():
    repo = RepoStub([
        _v("UF-B", created_at="2026-09-03T00:00:00"),
        _v("UF-A", created_at="2026-09-01T00:00:00"),
        _v("UF-C", created_at="2026-09-05T00:00:00"),
    ])

    assert _ids(daily_report.live_orders(repo)) == ["UF-A", "UF-B", "UF-C"]


def test_nothing_filters_by_payment_method_so_voucher_orders_are_included():
    repo = RepoStub([
        _v("UF-VOUCHER", "Unredeemed", discount_total=250, discount_per_liter=1.5),
        _v("UF-PLAIN", "Unredeemed"),
    ])

    assert set(_ids(daily_report.live_orders(repo))) == {"UF-VOUCHER", "UF-PLAIN"}


# ============================================================
# PDF build (R3, R6, R8)
# ============================================================

def test_an_order_with_a_blank_or_unknown_station_still_appears():
    """The on-demand sheet keeps only orders at listed stations; the daily report
    must not, or an Unverified order with no station silently vanishes."""
    repo = RepoStub([
        _v("UF-BLANKSTN", station=""),
        _v("UF-NOSTN", station=None),
        _v("UF-UNKNOWN", station="Some Station Nobody Listed"),
    ])

    text = _pdf_text(daily_report.build_pdf(repo, "2026-09-20"))

    for vid in ("UF-BLANKSTN", "UF-NOSTN", "UF-UNKNOWN"):
        assert vid in text


def test_the_pdf_is_labelled_with_the_report_date_and_shows_status():
    repo = RepoStub([_v("UF-1", "Redeemed"), _v("UF-2", "Unverified")])

    pdf = daily_report.build_pdf(repo, "2026-09-20")
    text = _pdf_text(pdf)

    assert pdf.startswith(b"%PDF")
    assert "Report for 2026-09-20" in text
    assert "Redeemed" in text and "Unverified" in text
    assert "Status" in text


def test_with_no_live_orders_the_pdf_says_so():
    repo = RepoStub([_v("UF-GONE", deleted_at="2026-09-02T00:00:00")])

    text = _pdf_text(daily_report.build_pdf(repo, "2026-09-20"))

    assert "No orders for 2026-09-20" in text
    assert "UF-GONE" not in text


def test_every_live_order_is_in_a_long_report():
    repo = RepoStub([_v(f"UF-{i:04d}", "Redeemed") for i in range(1, 251)])

    pdf = daily_report.build_pdf(repo, "2026-09-20")

    text = _pdf_text(pdf)
    assert _page_count(pdf) > 2
    assert all(f"UF-{i:04d}" in text for i in range(1, 251))


# ============================================================
# Manila date
# ============================================================

def test_the_manila_date_is_read_in_manila_not_utc():
    """16:00 UTC is midnight in Manila: one minute earlier is still the old day."""
    utc = dt.timezone.utc
    assert daily_report.manila_date(dt.datetime(2026, 9, 20, 15, 59, tzinfo=utc)) == "2026-09-20"
    assert daily_report.manila_date(dt.datetime(2026, 9, 20, 16, 0, tzinfo=utc)) == "2026-09-21"


# ============================================================
# Regression guard: the on-demand sheet is untouched (A12)
# ============================================================

def test_the_on_demand_supplier_sheet_still_lists_only_unredeemed_orders(monkeypatch):
    """Guards ARCH backward-regression risk for main.py /supplier-sheet.pdf."""
    import main

    captured = {}

    def fake_build(vouchers, target_station_ids, stations, logo_path):
        # Exactly these four arguments: no Status column, no report date.
        captured["ids"] = _ids(vouchers)
        return b"%PDF-fake"

    monkeypatch.setattr(main, "ADMIN_PASSWORD", "s3cret")
    monkeypatch.setattr(main, "build_supplier_pdf", fake_build)
    monkeypatch.setattr(main, "repo", RepoStub([
        _v("UF-UNVERIFIED", "Unverified"),
        _v("UF-UNREDEEMED", "Unredeemed"),
        _v("UF-REDEEMED", "Redeemed"),
        _v("UF-DELETED", "Unredeemed", deleted_at="2026-09-02T00:00:00"),
    ]))
    monkeypatch.setattr(main.price_store, "list_stations", lambda *a, **kw: [])
    main.app.config.update(TESTING=True)
    client = main.app.test_client()
    client.post("/admin/login", data={"password": "s3cret"})

    r = client.get("/supplier-sheet.pdf")

    assert r.status_code == 200
    assert captured["ids"] == ["UF-UNREDEEMED"]


# ============================================================
# ensure_today_queued (T6) — the idempotent tick body
#
# These use the real outbox and recipient list on the ephemeral test database
# (`schema_db`, so `make test-db`), because "already queued today" and "all
# rows or none" are properties of the database, not of a stub.
# ============================================================

import psycopg  # noqa: E402

import notifications  # noqa: E402
import report_recipients  # noqa: E402

UTC = dt.timezone.utc


def _utc(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=UTC)


# 00:05 Manila on 2026-09-21 is 16:05 UTC on 2026-09-20.
MIDNIGHT_PLUS_5 = _utc(2026, 9, 20, 16, 5)


@pytest.fixture
def tick_db(schema_db, monkeypatch):
    import db.pool as pool_module

    monkeypatch.setenv("NOTIFICATIONS_ENABLED", "true")

    def _clean():
        with psycopg.connect(schema_db) as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE notifications")
                cur.execute("DELETE FROM report_recipients")
            conn.commit()

    pool_module.reset_pool()
    _clean()
    yield schema_db
    pool_module.reset_pool()
    _clean()


def _add_recipient(dsn, email):
    assert report_recipients.add(email, dsn=dsn) is not None


def _rows(dsn):
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT kind, recipient, dedupe_key, attachment_ref, subject, body, status "
                "FROM notifications ORDER BY id"
            )
            cols = ["kind", "recipient", "dedupe_key", "attachment_ref", "subject", "body", "status"]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def _tick(repo, dsn, now=MIDNIGHT_PLUS_5):
    return daily_report.ensure_today_queued(repo, now=now, dsn=dsn)


def test_one_row_is_queued_per_recipient_with_the_days_keys(tick_db):
    _add_recipient(tick_db, "a@example.com")
    _add_recipient(tick_db, "b@example.com")

    queued = _tick(RepoStub([_v("UF-1")]), tick_db)

    rows = _rows(tick_db)
    assert queued == 2
    assert {r["recipient"] for r in rows} == {"a@example.com", "b@example.com"}
    for r in rows:
        assert r["kind"] == "daily_report"
        assert r["status"] == "queued"
        assert r["dedupe_key"] == f"daily:2026-09-21:{r['recipient']}"
        assert r["attachment_ref"] == "daily_pdf:2026-09-21"
        assert "2026-09-21" in r["subject"]


def test_a_second_tick_the_same_day_queues_nothing(tick_db):
    _add_recipient(tick_db, "a@example.com")
    repo = RepoStub([_v("UF-1")])

    first = _tick(repo, tick_db)
    second = _tick(repo, tick_db, now=_utc(2026, 9, 21, 3, 0))  # later, same Manila day

    assert first == 1 and second == 0
    assert len(_rows(tick_db)) == 1


def test_a_day_with_no_live_orders_is_still_queued(tick_db):
    _add_recipient(tick_db, "a@example.com")

    queued = _tick(RepoStub([]), tick_db)

    assert queued == 1
    assert "no live orders" in _rows(tick_db)[0]["body"].lower()


def test_a_recipient_added_after_the_day_was_queued_waits_for_tomorrow(tick_db):
    _add_recipient(tick_db, "a@example.com")
    repo = RepoStub([_v("UF-1")])
    _tick(repo, tick_db)

    _add_recipient(tick_db, "late@example.com")
    again = _tick(repo, tick_db)

    assert again == 0
    assert {r["recipient"] for r in _rows(tick_db)} == {"a@example.com"}
    # ...and the next Manila day includes them.
    next_day = _tick(repo, tick_db, now=_utc(2026, 9, 21, 16, 5))
    assert next_day == 2


def test_an_empty_recipient_list_queues_nothing_then_catches_up(tick_db):
    """GIVEN nobody is on the list WHEN the tick runs THEN nothing is queued and
    nothing raises; once someone is added the same day, the next tick queues
    that day's report (verifies R10)."""
    repo = RepoStub([_v("UF-1")])

    assert _tick(repo, tick_db) == 0
    assert _rows(tick_db) == []

    _add_recipient(tick_db, "first@example.com")
    assert _tick(repo, tick_db) == 1
    assert [r["recipient"] for r in _rows(tick_db)] == ["first@example.com"]


def test_a_pdf_that_cannot_be_built_queues_nothing_and_is_retried(tick_db, monkeypatch):
    _add_recipient(tick_db, "a@example.com")
    repo = RepoStub([_v("UF-1")])
    real_build = daily_report.build_pdf

    def boom(repo_, report_date):
        raise RuntimeError("reportlab exploded")

    monkeypatch.setattr(daily_report, "build_pdf", boom)
    assert _tick(repo, tick_db) == 0            # quietly, no exception
    assert _rows(tick_db) == []

    monkeypatch.setattr(daily_report, "build_pdf", real_build)
    assert _tick(repo, tick_db) == 1            # the next tick tries again


def test_the_report_day_is_the_manila_day_not_the_utc_day(tick_db):
    _add_recipient(tick_db, "a@example.com")
    repo = RepoStub([_v("UF-1")])

    _tick(repo, tick_db, now=_utc(2026, 9, 20, 15, 59))   # still 2026-09-20 in Manila
    _tick(repo, tick_db, now=_utc(2026, 9, 20, 16, 0))    # midnight: 2026-09-21

    assert [r["dedupe_key"] for r in _rows(tick_db)] == [
        "daily:2026-09-20:a@example.com",
        "daily:2026-09-21:a@example.com",
    ]


def test_missed_days_are_not_backfilled(tick_db):
    _add_recipient(tick_db, "a@example.com")

    queued = _tick(RepoStub([_v("UF-1")]), tick_db, now=_utc(2026, 9, 25, 5, 0))

    rows = _rows(tick_db)
    assert queued == 1
    assert [r["dedupe_key"] for r in rows] == ["daily:2026-09-25:a@example.com"]


def test_the_email_says_it_was_sent_late_only_after_an_hour(tick_db):
    """GIVEN a report queued 61 minutes after 00:00 Manila WHEN the body is built
    THEN it says so and names the day; at 00:05 it does not (verifies R8)."""
    _add_recipient(tick_db, "a@example.com")
    repo = RepoStub([_v("UF-1")])

    _tick(repo, tick_db, now=_utc(2026, 9, 20, 16, 5))     # 00:05 Manila on 2026-09-21
    _tick(repo, tick_db, now=_utc(2026, 9, 21, 17, 1))     # 01:01 Manila on 2026-09-22

    on_time, late = _rows(tick_db)
    assert "sent late" not in on_time["body"].lower()
    assert "sent late" in late["body"].lower()
    assert "2026-09-22" in late["body"]


def test_the_body_reports_how_many_live_orders_the_pdf_holds(tick_db):
    _add_recipient(tick_db, "a@example.com")

    _tick(RepoStub([_v("UF-1"), _v("UF-2", "Redeemed"), _v("UF-3", deleted_at="2026-09-01")]),
          tick_db)

    assert "Live orders in this report: 2" in _rows(tick_db)[0]["body"]


def test_an_unreachable_database_is_survived_quietly(tick_db, monkeypatch, capsys):
    def down(*a, **kw):
        raise RuntimeError("database is down")

    monkeypatch.setattr(daily_report, "get_pool", down)

    assert _tick(RepoStub([_v("UF-1")]), tick_db) == 0
    assert "database is down" in capsys.readouterr().err


def test_nothing_is_queued_when_the_kill_switch_is_off(tick_db, monkeypatch):
    _add_recipient(tick_db, "a@example.com")
    monkeypatch.setenv("NOTIFICATIONS_ENABLED", "false")

    assert _tick(RepoStub([_v("UF-1")]), tick_db) == 0
    assert _rows(tick_db) == []
