"""
tests/test_daily_report.py — the nightly supplier PDF cron entrypoint
(T13, ARCH-brief-11-email-notifications).

The script builds the all-stations, live-orders supplier sheet, writes it
where the outbox worker can find it, and enqueues one row per active
recipient. It never sends: a provider blip at midnight retries on the normal
ladder instead of losing the report (A10).

These tests stub the repo, the recipient list and the outbox, so no Postgres
is required.
"""

import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import data_paths

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import send_daily_report as sdr  # noqa: E402


VOUCHERS = [
    {"voucher_id": "V1", "status": "Unredeemed", "station": "EcoOil - Cainta",
     "requested_amount_php": 1000, "driver_name": "Dave", "vehicle_plate": "XYZ-123"},
    {"voucher_id": "V2", "status": "Unverified", "station": "EcoOil - Cainta",
     "requested_amount_php": 500, "driver_name": "Eve", "vehicle_plate": "ABC-999"},
    {"voucher_id": "V3", "status": "Redeemed", "station": "EcoOil - Cainta",
     "requested_amount_php": 700, "driver_name": "Fay", "vehicle_plate": "DEF-111"},
    {"voucher_id": "V4", "status": "Unredeemed", "station": "Petron - Ortigas",
     "requested_amount_php": 900, "driver_name": "Gus", "vehicle_plate": "GHI-222",
     "deleted_at": "2026-09-01T10:00:00"},
]

STATIONS = [
    {"id": "ecooil-cainta", "name": "EcoOil - Cainta"},
    {"id": "petron-ortigas", "name": "Petron - Ortigas"},
]


class RepoStub:
    def __init__(self, vouchers=None):
        self._vouchers = vouchers if vouchers is not None else VOUCHERS

    def list_all_vouchers(self):
        return [dict(v) for v in self._vouchers]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A working cron environment: a DSN, two recipients, a stubbed repo and
    a captured outbox."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://stub/stub")
    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgres")
    monkeypatch.setattr(data_paths, "EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr(sdr, "get_repo", lambda backend: RepoStub())
    monkeypatch.setattr(sdr.price_store, "list_stations", lambda fuel_type: STATIONS)
    monkeypatch.setattr(
        sdr.report_recipients, "active_emails",
        lambda strict=False: ["ops@example.com", "finance@example.com"],
    )

    queued = []
    monkeypatch.setattr(
        sdr.notifications, "enqueue",
        lambda kind, **kw: queued.append({"kind": kind, **kw}) or len(queued),
    )
    return queued


# ============================================================
# Fan-out and content
# ============================================================

def test_one_row_is_queued_per_active_recipient(env):
    """GIVEN two active recipients WHEN the script runs THEN each gets one
    queued report (verifies R11)."""
    queued = env

    assert sdr.main([]) == 0
    assert len(queued) == 2
    assert {q["recipient"] for q in queued} == {"ops@example.com", "finance@example.com"}
    assert all(q["kind"] == "daily_report" for q in queued)


def test_only_unredeemed_undeleted_orders_are_reported(env):
    """GIVEN a mix of statuses and a soft-deleted order WHEN the report is
    built THEN only live Unredeemed orders are included (verifies R12; guards
    the soft-delete contract in test_supplier_exports_exclude_deleted.py)."""
    rows = sdr.live_vouchers(RepoStub())

    assert [r["voucher_id"] for r in rows] == ["V1"]


def test_no_date_window_is_applied(env, monkeypatch):
    """GIVEN live orders with transaction dates weeks apart WHEN the report is
    built THEN all of them appear. Expiry is handled by admins cancelling
    orders, so a date filter would only hide still-redeemable ones
    (verifies R12)."""
    old = dict(VOUCHERS[0], voucher_id="OLD", transaction_date="2026-01-01T00:00:00")
    new = dict(VOUCHERS[0], voucher_id="NEW", transaction_date="2026-09-07T00:00:00")

    rows = sdr.live_vouchers(RepoStub(vouchers=[old, new]))

    assert {r["voucher_id"] for r in rows} == {"OLD", "NEW"}


def test_the_report_covers_every_station(env, monkeypatch):
    """The on-demand PDF filters by a per-admin cookie, which does not exist
    in a scheduled context; the nightly one is always all-stations (A9)."""
    captured = {}

    def fake_build(*, vouchers, target_station_ids, stations, logo_path=None):
        captured["ids"] = set(target_station_ids)
        return b"%PDF-fake"

    monkeypatch.setattr(sdr, "build_supplier_pdf", fake_build)
    sdr.main([])

    assert captured["ids"] == {"ecooil-cainta", "petron-ortigas"}


def test_the_cron_enqueues_a_reference_and_writes_no_file(env, monkeypatch, tmp_path):
    """The cron hands over a reference, not bytes and not a file.

    It runs as its own Railway service and a Volume mounts to exactly one
    service, so anything it wrote to disk would be invisible to the web
    process that sends the mail. The worker rebuilds the PDF from the
    reference at send time (review finding B5).
    """
    monkeypatch.setattr(sdr, "build_supplier_pdf", lambda **kw: b"%PDF-nightly")
    queued = env

    sdr.main([])

    date_str = sdr.manila_date()
    assert queued[0]["attachment_ref"] == f"daily_pdf:{date_str}"
    assert list((tmp_path / "exports").glob("*.pdf")) == [], (
        "the cron must not leave PDFs on a volume the sender cannot read"
    )


# ============================================================
# Edge cases
# ============================================================

def test_an_empty_report_is_still_sent(env, monkeypatch):
    """GIVEN zero live orders WHEN the script runs THEN recipients still get
    the report, so they can tell 'no bookings' from 'the job broke'
    (verifies R13)."""
    monkeypatch.setattr(sdr, "get_repo", lambda backend: RepoStub(vouchers=[]))
    queued = env

    assert sdr.main([]) == 0
    assert len(queued) == 2
    assert "no live orders" in queued[0]["body"].lower()


def test_an_empty_recipient_list_exits_cleanly(env, monkeypatch):
    """GIVEN nobody on the list WHEN the script runs THEN it exits 0 having
    queued nothing — an empty list is a legitimate state, not a failure
    (verifies REQ edge case)."""
    monkeypatch.setattr(sdr.report_recipients, "active_emails", lambda strict=False: [])
    queued = env

    assert sdr.main([]) == 0
    assert queued == []


def test_dry_run_writes_nothing(env, monkeypatch):
    """--dry-run is how an operator checks the cron wiring safely."""
    queued = env

    assert sdr.main(["--dry-run"]) == 0
    assert queued == []


# ============================================================
# Idempotency
# ============================================================

def test_the_dedupe_key_is_scoped_to_the_manila_date_and_recipient(env):
    """N3: however often the cron fires, each recipient gets one report per
    Manila day."""
    queued = env
    sdr.main([])

    date_str = sdr.manila_date()
    assert queued[0]["dedupe_key"] == f"daily:{date_str}:ops@example.com"
    assert queued[1]["dedupe_key"] == f"daily:{date_str}:finance@example.com"


def test_a_second_run_the_same_day_queues_nothing_new(env, monkeypatch):
    """GIVEN the outbox rejects the duplicate keys WHEN the script runs again
    THEN nothing new is queued and it still exits 0 (verifies ARCH forward
    stress-test: the cron fires twice, or the service restarts)."""
    seen = set()

    def dedupe_aware_enqueue(kind, **kw):
        if kw["dedupe_key"] in seen:
            return None
        seen.add(kw["dedupe_key"])
        return len(seen)

    monkeypatch.setattr(sdr.notifications, "enqueue", dedupe_aware_enqueue)

    assert sdr.main([]) == 0
    assert len(seen) == 2
    assert sdr.main([]) == 0
    assert len(seen) == 2


def test_the_manila_date_is_read_in_manila_not_utc():
    """Railway runs UTC; a 16:00 UTC run must stamp the Manila date, not the
    previous day's (verifies A9)."""
    expected = dt.datetime.now(ZoneInfo("Asia/Manila")).strftime("%Y-%m-%d")

    assert sdr.manila_date() == expected


# ============================================================
# Failure modes
# ============================================================

def test_a_missing_database_url_exits_1(env, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("UNIFLEET_DB_DSN", raising=False)
    queued = env

    assert sdr.main([]) == 1
    assert queued == []


def test_a_pdf_build_failure_exits_2_and_queues_nothing(env, monkeypatch):
    """No email may carry a corrupt report; a visibly failed cron run is the
    better outcome (verifies ARCH forward stress-test)."""
    def boom(**kwargs):
        raise RuntimeError("reportlab exploded")

    monkeypatch.setattr(sdr, "build_supplier_pdf", boom)
    queued = env

    assert sdr.main([]) == 2
    assert queued == []


def test_an_unreadable_recipient_list_exits_1(env, monkeypatch):
    """The cron must call the strict variant, so a dead database exits 1
    rather than looking like an empty list and exiting 0 (finding F12)."""
    def boom(strict=False):
        assert strict is True, "the cron must ask for the raising variant"
        raise RuntimeError("pg down")

    monkeypatch.setattr(sdr.report_recipients, "active_emails", boom)
    queued = env

    assert sdr.main([]) == 1
    assert queued == []


def test_an_unset_persistence_backend_exits_1(env, monkeypatch):
    """The cron does not inherit the web service's variables, so defaulting to
    csv would build the nightly sheet from stale or absent CSVs and mail a
    wrong report rather than failing (review finding F21)."""
    monkeypatch.delenv("PERSISTENCE_BACKEND", raising=False)
    queued = env

    assert sdr.main([]) == 1
    assert queued == []
