"""
tests/test_daily_report.py — the daily report's wiring and its manual trigger
(T7, ARCH-midnight-supplier-report).

What the report contains and when it is queued is tested in
test_daily_report_tick.py. Here:

  * main.py registers the send-time PDF builder and the once-a-minute tick, so
    the report needs no separate scheduled service (R11);
  * scripts/send_daily_report.py is the manual trigger: same code, same exit
    codes as before, and it can never double-queue a day (R9).

Tests marked with `wired_db` use the ephemeral test database, so they need
`make test-db`; the rest stub the repo, recipients and outbox.
"""

import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import daily_report  # noqa: E402
import main  # noqa: E402
import notifications  # noqa: E402
import report_recipients  # noqa: E402
import send_daily_report as sdr  # noqa: E402
from test_report_pdf import _pdf_text  # noqa: E402


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


class RepoStub:
    def __init__(self, vouchers=None):
        self._vouchers = vouchers if vouchers is not None else VOUCHERS

    def list_all_vouchers(self):
        return [dict(v) for v in self._vouchers]


# ============================================================
# main.py wiring (R11)
# ============================================================

@pytest.fixture
def hooks_restored():
    """Tests that clear the periodic-task registry must put main's tick back."""
    yield
    notifications._reset_periodic_tasks_for_tests()
    main._register_notification_hooks()


def test_the_send_time_builder_returns_a_dated_pdf_with_redeemed_orders(monkeypatch):
    """GIVEN the resolver asked for 2026-09-20 WHEN it runs THEN it returns that
    day's file name and a PDF that includes Redeemed orders (verifies R3)."""
    monkeypatch.setattr(main, "repo", RepoStub())

    filename, pdf, mimetype = main._build_daily_report_pdf("2026-09-20")

    assert filename == "UniFleet_Supplier_Sheet_2026-09-20.pdf"
    assert mimetype == "application/pdf"
    text = _pdf_text(pdf)
    assert "V3" in text and "Redeemed" in text          # a Redeemed order is in
    assert "V2" in text and "Unverified" in text        # so is an Unverified one
    assert "V4" not in text                             # a deleted one is not
    assert "Report for 2026-09-20" in text


def test_the_app_registers_the_resolver_and_exactly_one_tick(hooks_restored):
    notifications._reset_periodic_tasks_for_tests()

    main._register_notification_hooks()
    main._register_notification_hooks()          # idempotent

    fns = [t["fn"] for t in notifications._periodic_tasks]
    assert fns == [main._daily_report_tick]
    assert notifications._periodic_tasks[0]["interval"] == main.DAILY_REPORT_TICK_SECONDS
    assert notifications._ATTACHMENT_RESOLVERS["daily_pdf"] is main._build_daily_report_pdf


def test_importing_the_app_registers_the_tick():
    """The registration happens at import, not just when the helper is called.
    Checked in a fresh interpreter, since the suite's own import has already
    happened (and other tests clear the registry)."""
    env = dict(os.environ, NOTIFICATIONS_ENABLED="false")   # register, don't start a worker
    out = subprocess.run(
        [sys.executable, "-c",
         "import main, notifications; "
         "print([t['fn'].__name__ for t in notifications._periodic_tasks])"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=90,
    )

    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "['_daily_report_tick']"


def test_the_tick_does_nothing_before_the_repo_exists(monkeypatch):
    """The worker thread starts while main.py is still importing, before `repo`
    is defined. That first pass must be a quiet no-op, not a NameError."""
    monkeypatch.setattr(main, "repo", None)
    calls = []
    monkeypatch.setattr(daily_report, "ensure_today_queued", lambda r, **kw: calls.append(r) or 1)

    assert main._daily_report_tick() == 0
    assert calls == []


def test_the_tick_hands_the_apps_repo_to_the_report(monkeypatch):
    marker = RepoStub([])
    monkeypatch.setattr(main, "repo", marker)
    calls = []
    monkeypatch.setattr(daily_report, "ensure_today_queued", lambda r, **kw: calls.append(r) or 3)

    assert main._daily_report_tick() == 3
    assert calls == [marker]


# --- Regression guard: the routes are still bound to their own handlers ---
# (CLAUDE.md gotcha: a helper inserted between @app.route and its def silently
# rebinds the decorator to the wrong function.)

@pytest.mark.parametrize("rule,handler", [
    ("/supplier-sheet.pdf", "supplier_sheet_pdf"),
    ("/admin/recipients", "admin_recipients"),
    ("/admin/recipients/<int:recipient_id>/active", "admin_recipient_set_active"),
    ("/admin/recipients/<int:recipient_id>/delete", "admin_recipient_delete"),
])
def test_routes_are_still_bound_to_their_own_handlers(rule, handler):
    endpoints = [r.endpoint for r in main.app.url_map.iter_rules() if r.rule == rule]

    assert endpoints == [handler]
    assert main.app.view_functions[handler] is getattr(main, handler)


# ============================================================
# The manual script (guards scripts/send_daily_report.py)
# ============================================================

@pytest.fixture
def env(monkeypatch):
    """A working shell for the script: a DSN, two recipients, a stubbed repo and
    a captured queue."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://stub/stub")
    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgres")
    monkeypatch.setattr(sdr, "get_repo", lambda backend: RepoStub())
    monkeypatch.setattr(
        sdr.report_recipients, "active_emails",
        lambda strict=False: ["ops@example.com", "finance@example.com"],
    )
    queued_calls = []
    monkeypatch.setattr(
        sdr.daily_report, "ensure_today_queued",
        lambda repo, **kw: queued_calls.append(repo) or 2,
    )
    return queued_calls


def test_the_script_queues_through_the_shared_code(env):
    assert sdr.main([]) == 0
    assert len(env) == 1                       # one call: the same code the worker runs


def test_dry_run_writes_nothing(env):
    """--dry-run is how an operator checks the wiring safely."""
    assert sdr.main(["--dry-run"]) == 0
    assert env == []


def test_an_empty_recipient_list_exits_cleanly(env, monkeypatch):
    monkeypatch.setattr(sdr.report_recipients, "active_emails", lambda strict=False: [])

    assert sdr.main([]) == 0
    assert env == []


def test_a_missing_database_url_exits_1(env, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("UNIFLEET_DB_DSN", raising=False)

    assert sdr.main([]) == 1
    assert env == []


def test_an_unset_persistence_backend_exits_1(env, monkeypatch):
    """The script does not inherit the web service's variables, so defaulting to
    csv would build the sheet from stale or absent CSVs and mail a wrong report
    rather than failing (review finding F21)."""
    monkeypatch.delenv("PERSISTENCE_BACKEND", raising=False)

    assert sdr.main([]) == 1
    assert env == []


def test_a_pdf_build_failure_exits_2_and_queues_nothing(env, monkeypatch):
    def boom(repo, report_date):
        raise RuntimeError("reportlab exploded")

    monkeypatch.setattr(sdr.daily_report, "build_pdf", boom)

    assert sdr.main([]) == 2
    assert env == []


def test_an_unreadable_recipient_list_exits_1(env, monkeypatch):
    """The strict variant is required, so a dead database exits 1 rather than
    looking like an empty list and exiting 0 (finding F12)."""
    def boom(strict=False):
        assert strict is True, "the script must ask for the raising variant"
        raise RuntimeError("pg down")

    monkeypatch.setattr(sdr.report_recipients, "active_emails", boom)

    assert sdr.main([]) == 1
    assert env == []


# ============================================================
# The worker tick and the script share one queue (R9)
# ============================================================

@pytest.fixture
def wired_db(schema_db, monkeypatch):
    import db.pool as pool_module

    monkeypatch.setenv("NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", schema_db)
    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgres")

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


def _queued_rows(dsn):
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT recipient, dedupe_key FROM notifications ORDER BY id")
            return cur.fetchall()


def test_with_no_cron_service_the_tick_alone_queues_todays_report(wired_db, monkeypatch):
    """GIVEN no separately scheduled service WHEN the registered tick is called
    THEN today's report is queued for every recipient (verifies R11)."""
    monkeypatch.setattr(main, "repo", RepoStub())
    assert report_recipients.add("a@example.com", dsn=wired_db)
    assert report_recipients.add("b@example.com", dsn=wired_db)

    assert main._daily_report_tick() == 2

    today = daily_report.manila_date()
    assert sorted(_queued_rows(wired_db)) == [
        ("a@example.com", f"daily:{today}:a@example.com"),
        ("b@example.com", f"daily:{today}:b@example.com"),
    ]


def test_the_tick_and_the_script_never_queue_the_same_day_twice(wired_db, monkeypatch):
    """Guards ARCH backward-regression risk: a duplicate report on rollout."""
    monkeypatch.setattr(main, "repo", RepoStub())
    monkeypatch.setattr(sdr, "get_repo", lambda backend: RepoStub())
    assert report_recipients.add("a@example.com", dsn=wired_db)
    assert report_recipients.add("b@example.com", dsn=wired_db)

    assert sdr.main([]) == 0                      # the operator runs the script first
    assert main._daily_report_tick() == 0         # the worker's next tick finds it done
    assert sdr.main([]) == 0                      # and running it again is harmless

    rows = _queued_rows(wired_db)
    assert len(rows) == 2
    assert len({r[0] for r in rows}) == 2         # one row per recipient
