#!/usr/bin/env python3
"""
Queue today's supplier report by hand (ARCH-midnight-supplier-report).

The report normally needs nothing from you: the web service's outbox worker
checks once a minute whether today's report (Manila date) has been queued and
queues it when it has not, so it goes out at 00:00 Asia/Manila (16:00 UTC)
and catches up after any downtime. There is no separate cron service to
provision. This script is the manual trigger, for an operator who wants to
check the wiring or queue today's report straight away:

    python scripts/send_daily_report.py [--dry-run]

It does the same thing the worker does, through the same code
(daily_report.ensure_today_queued), so the two can never disagree about what
the report contains or send it twice: a report is queued once per Manila day,
whoever asks first. Sending is still the web worker's job.

The report is every live order (not deleted; Unverified, Unredeemed or
Redeemed), over all time, from every station.

Run from a shell that has the web service's variables. It does not guess:
  DATABASE_URL         the Postgres the outbox and recipient list live in
  PERSISTENCE_BACKEND  must match the web service; defaulting to csv would
                       build the report from stale or absent CSVs and mail a
                       wrong sheet

Exit codes:
  0 — queued, already queued today, or there were no recipients
  1 — DATABASE_URL or PERSISTENCE_BACKEND not set, or the database or the
      recipient list was unreachable
  2 — the PDF could not be built (nothing was queued)
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import daily_report  # noqa: E402
import report_recipients  # noqa: E402
from persistence import get_repo  # noqa: E402


def log(msg: str) -> None:
    print(f"[{dt.datetime.now(dt.timezone.utc).isoformat()}] {msg}", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the report and print what would be queued; write nothing.",
    )
    args = parser.parse_args(argv)

    if not (os.environ.get("DATABASE_URL") or os.environ.get("UNIFLEET_DB_DSN")):
        log("ERROR: DATABASE_URL is not set")
        return 1

    backend = (os.environ.get("PERSISTENCE_BACKEND") or "").strip()
    if not backend:
        log("ERROR: PERSISTENCE_BACKEND is not set — refusing to guess the "
            "backend for a report that goes to staff")
        return 1

    try:
        # strict: an unreachable database must exit 1, not look like an empty
        # list and exit 0.
        recipients = report_recipients.active_emails(strict=True)
    except Exception as e:
        log(f"ERROR: could not read the recipient list: {e}")
        return 1

    if not recipients:
        # Not an error: the list is admin-managed and may legitimately be
        # empty. The recipients page warns about it.
        log("No active report recipients; nothing to send.")
        return 0

    repo = get_repo(backend)
    report_date = daily_report.manila_date()

    # Build once up front so a report that cannot be produced fails visibly
    # here (exit 2) rather than being quietly retried by the worker.
    try:
        live_count = len(daily_report.live_orders(repo))
        pdf_bytes = daily_report.build_pdf(repo, report_date)
    except Exception as e:
        log(f"ERROR: could not build the supplier PDF: {e}")
        return 2

    log(f"Supplier sheet for {report_date}: {live_count} live orders, "
        f"{len(pdf_bytes)} bytes, {len(recipients)} recipient(s)")

    if args.dry_run:
        for recipient in recipients:
            log(f"DRY RUN: would queue the {report_date} report to {recipient}")
        return 0

    queued = daily_report.ensure_today_queued(repo)
    if queued:
        log(f"Done: queued the {report_date} report for {queued} recipient(s).")
    else:
        log(f"Nothing new queued: the {report_date} report was already queued "
            "today, or the lines above say why not.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
