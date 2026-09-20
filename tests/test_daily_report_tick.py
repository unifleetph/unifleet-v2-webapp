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
