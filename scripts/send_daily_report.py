#!/usr/bin/env python3
"""
Nightly supplier PDF for UniFleet (T13, ARCH-brief-11-email-notifications).

Builds the supplier sheet over ALL stations and every currently live order,
then enqueues one outbox row per active internal recipient. Sending itself is
the worker's job — the cron only queues, so a provider blip at midnight
retries on the normal ladder instead of losing the report (A10).

Designed for the Railway Cron Schedule service, scheduled at 16:00 UTC, which
is 00:00 Asia/Manila year-round (PHT has no DST). Also runnable by hand:

    python scripts/send_daily_report.py [--dry-run]

There is deliberately NO date window. Expiring orders are cancelled by
admins and drop out of the live set, so filtering by date would only hide
orders that are still redeemable (REQ R12). Station filtering is likewise
absent: the on-demand /supplier-sheet.pdf reads a per-admin browser cookie,
which does not exist in a scheduled context, so the nightly report always
covers every station (A9).

Idempotent: the dedupe key is daily:<Manila date>:<recipient>, so however
often the cron fires or the service restarts, each recipient gets one report
per Manila day (N3).

Exit codes:
  0 — report enqueued, or there were no recipients to send to
  1 — DATABASE_URL not set, or the database was unreachable
  2 — PDF generation failed (nothing was enqueued; better a visibly missed
      run than an email carrying a corrupt report)
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import data_paths  # noqa: E402
import notifications  # noqa: E402
import price_store  # noqa: E402
import report_recipients  # noqa: E402
from persistence import get_repo  # noqa: E402
from report_pdf import build_supplier_pdf  # noqa: E402

MANILA = ZoneInfo("Asia/Manila")

REPORT_SUBJECT = "UniFleet Daily Supplier Sheet"


def log(msg: str) -> None:
    print(f"[{dt.datetime.now(dt.timezone.utc).isoformat()}] {msg}", flush=True)


def manila_date() -> str:
    """Today's date in Manila — the report's identity for dedupe purposes.

    Read from Manila rather than the host clock: Railway runs UTC, so a 16:00
    UTC run would otherwise stamp the previous day's date.
    """
    return dt.datetime.now(MANILA).strftime("%Y-%m-%d")


def live_vouchers(repo) -> list:
    """Every order still awaiting redemption, soft-deleted ones excluded.

    Matches what /supplier-sheet.pdf shows: status Unredeemed, minus anything
    an admin has deleted. No date filter — see the module docstring.
    """
    rows = repo.list_all_vouchers()
    return [
        r for r in rows
        if not r.get("deleted_at")
        and str(r.get("status") or "").strip() == "Unredeemed"
    ]


def build_report(repo) -> bytes:
    """Build the all-stations supplier PDF."""
    stations = price_store.list_stations("Biodiesel")
    return build_supplier_pdf(
        vouchers=live_vouchers(repo),
        target_station_ids=set(s.get("id") for s in stations if s.get("id")),
        stations=stations,
        logo_path=data_paths.STATIC_LOGO_PATH,
    )


def report_body(date_str: str, voucher_count: int) -> str:
    if voucher_count == 0:
        return (
            f"Attached is the UniFleet supplier sheet for {date_str}.\n"
            "\n"
            "There are no live orders at this time.\n"
        )
    return (
        f"Attached is the UniFleet supplier sheet for {date_str}.\n"
        "\n"
        f"Live orders awaiting redemption: {voucher_count}\n"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the report and print what would be enqueued; write nothing.",
    )
    args = parser.parse_args(argv)

    if not (os.environ.get("DATABASE_URL") or os.environ.get("UNIFLEET_DB_DSN")):
        log("ERROR: DATABASE_URL is not set")
        return 1

    try:
        # strict: an unreachable database must exit 1, not look like an empty
        # list and exit 0. Without it the documented exit code was
        # unreachable and a week of missed reports looked like a week of green
        # cron runs (review finding F12).
        recipients = report_recipients.active_emails(strict=True)
    except Exception as e:
        log(f"ERROR: could not read the recipient list: {e}")
        return 1

    if not recipients:
        # Not an error: the list is admin-managed and may legitimately be
        # empty. Say so plainly so an operator reading cron logs can tell
        # this apart from a failure.
        log("No active report recipients; nothing to send.")
        return 0

    repo = get_repo(os.environ.get("PERSISTENCE_BACKEND", "csv"))

    try:
        vouchers = live_vouchers(repo)
        pdf_bytes = build_report(repo)
    except Exception as e:
        log(f"ERROR: could not build the supplier PDF: {e}")
        return 2

    date_str = manila_date()
    pdf_path = data_paths.daily_report_pdf_path(date_str)
    filename = pdf_path.name
    body = report_body(date_str, len(vouchers))

    log(f"Supplier sheet for {date_str}: {len(vouchers)} live orders, "
        f"{len(pdf_bytes)} bytes, {len(recipients)} recipient(s)")

    if args.dry_run:
        for recipient in recipients:
            log(f"DRY RUN: would enqueue {filename} to {recipient}")
        return 0

    # Write the PDF where the worker will look for it at send time. Doing
    # this before enqueuing means a row never references a file that is not
    # there yet (A11 resolves attachments at send time).
    try:
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(pdf_bytes)
    except OSError as e:
        log(f"ERROR: could not write {pdf_path}: {e}")
        return 2

    queued = 0
    for recipient in recipients:
        row_id = notifications.enqueue(
            "daily_report",
            recipient=recipient,
            subject=f"{REPORT_SUBJECT} — {date_str}",
            body=body,
            dedupe_key=notifications.dedupe_daily_report(date_str, recipient),
            attachment_ref=f"daily_pdf:{date_str}",
        )
        if row_id is None:
            # Already queued for this recipient today, or the insert failed;
            # enqueue logs the reason either way.
            log(f"Skipped {recipient} (already queued for {date_str}, or write failed)")
            continue
        queued += 1
        log(f"Queued {filename} to {recipient} (notification {row_id})")

    log(f"Done: {queued} of {len(recipients)} recipient(s) queued.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
