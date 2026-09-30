"""
daily_report.py — the midnight supplier report (ARCH-midnight-supplier-report).

One place for what the daily report is: which orders are "live", which
Manila day it covers, and how its PDF is built. The web worker's tick, the
send-time PDF builder in main.py and scripts/send_daily_report.py all read it
from here; the same rule used to be written out separately in each of them.

The report is a snapshot of every live order at the moment it is produced.
It is not limited to yesterday and not to new orders, and it applies no
station filter (the admin's on-demand /supplier-sheet.pdf keeps its own
Unredeemed-only rule and is deliberately not touched).
"""

from __future__ import annotations

import datetime as dt
import sys
from typing import List, Optional
from zoneinfo import ZoneInfo

import data_paths
import notifications
import report_recipients
from db.pool import get_pool
from report_pdf import build_supplier_pdf

MANILA = ZoneInfo("Asia/Manila")

REPORT_SUBJECT = "UniFleet Daily Supplier Sheet"

# A report queued more than this long after 00:00 Manila says it was sent late.
LATE_AFTER_MINUTES = 60

# The tick asks about "today's report" every minute, so it must be cheap and
# must not wait on a stalled database the way a request handler must not.
POOL_TIMEOUT_SECONDS = 2

# An order is live when it has not been deleted and is in one of these
# statuses (REQ R4). A blank status is an order nobody has verified yet.
LIVE_STATUSES = ("Unverified", "Unredeemed", "Redeemed")


def manila_date(now: Optional[dt.datetime] = None) -> str:
    """The report's identity: today's date in Manila, as YYYY-MM-DD.

    Read from Manila rather than the host clock: Railway runs UTC, so at
    00:00 Manila (16:00 UTC) the host would still say the previous day.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    return now.astimezone(MANILA).strftime("%Y-%m-%d")


def _status(order: dict) -> str:
    return str(order.get("status") or "").strip() or "Unverified"


def _is_live(order: dict) -> bool:
    return not order.get("deleted_at") and _status(order) in LIVE_STATUSES


def _oldest_first(order: dict) -> tuple:
    when = order.get("created_at") or order.get("transaction_date") or ""
    return (str(when), str(order.get("voucher_id") or ""))


def live_orders(repo) -> List[dict]:
    """Every live order, over all time, oldest first (REQ R3, R4, R5).

    No date window and no payment-method filter: the recipients open the PDF
    and check it, so it always shows what is current.
    """
    rows = repo.list_all_vouchers()
    return sorted((r for r in rows if _is_live(r)), key=_oldest_first)


def build_pdf(repo, report_date: str) -> bytes:
    """The daily supplier PDF for `report_date` (YYYY-MM-DD).

    `report_date` labels the PDF with the day it covers, so a report that goes
    out late is never mistaken for a later day's. No station filter is passed:
    an order with a blank or unlisted station must still appear.
    """
    return build_supplier_pdf(
        vouchers=live_orders(repo),
        target_station_ids=[],
        stations=[],
        logo_path=data_paths.STATIC_LOGO_PATH,
        include_status=True,
        report_date=report_date,
    )


# ============================================================
# The tick (ARCH-midnight-supplier-report A1, A3, A4, A11)
# ============================================================

# The empty-list line is logged once per report day, not once a minute.
_empty_list_logged_for: Optional[str] = None


def _log(msg: str) -> None:
    print(f"[{dt.datetime.now(dt.timezone.utc).isoformat()}] daily report: {msg}", flush=True)


def _warn(msg: str) -> None:
    print(f"\u26a0\ufe0f daily report: {msg}", file=sys.stderr, flush=True)


def _report_already_queued(report_date: str, dsn: Optional[str] = None) -> bool:
    """True once any recipient has a report queued for `report_date`.

    "Any" is deliberate (A3): a recipient added after the day was queued
    waits for tomorrow, and the day is queued atomically so it is never
    half done.
    """
    prefix = notifications.dedupe_daily_report(report_date, "") + "%"
    pool = get_pool(dsn=dsn)
    with pool.connection(timeout=POOL_TIMEOUT_SECONDS) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM notifications WHERE dedupe_key LIKE %s LIMIT 1",
                (prefix,),
            )
            return cur.fetchone() is not None


def _is_late(now: dt.datetime) -> bool:
    manila_now = now.astimezone(MANILA)
    midnight = manila_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return manila_now - midnight > dt.timedelta(minutes=LATE_AFTER_MINUTES)


def report_body(report_date: str, live_count: int, late: bool = False) -> str:
    lines = [f"Attached is the UniFleet supplier sheet for {report_date}.", ""]
    if late:
        lines += [f"This report for {report_date} is being sent late.", ""]
    if live_count == 0:
        lines.append("There are no live orders at this time.")
    else:
        lines.append(f"Live orders in this report: {live_count}")
    return "\n".join(lines) + "\n"


def ensure_today_queued(repo, *, now: Optional[dt.datetime] = None,
                        dsn: Optional[str] = None) -> int:
    """Queue today's report if it has not been queued yet. Returns how many
    rows were queued this call (0 means done already, nothing to do yet, or a
    problem worth retrying on the next tick). Never raises.

    Safe to call every minute from the worker: it does one cheap existence
    check and only builds the PDF when a report is actually due. It catches up
    after downtime by design (a tick at 09:00 queues that morning's report,
    labelled late) and does not backfill days that were missed (A4).
    """
    global _empty_list_logged_for

    if not notifications.is_enabled():
        return 0

    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    report_date = manila_date(now)

    try:
        if _report_already_queued(report_date, dsn=dsn):
            return 0
    except Exception as e:
        _warn(f"could not check whether {report_date} was queued: {e}")
        return 0

    try:
        # strict: an unreachable database must not look like an empty list.
        recipients = report_recipients.active_emails(dsn=dsn, strict=True)
    except Exception as e:
        _warn(f"could not read the recipient list: {e}")
        return 0

    if not recipients:
        # Legitimate but worth seeing: without recipients there is no report,
        # which is how this bug first looked. Keep checking each tick; the
        # first address added today gets today's report (A11).
        if _empty_list_logged_for != report_date:
            _log(f"no active recipients for {report_date}; will keep checking")
            _empty_list_logged_for = report_date
        return 0

    try:
        live_count = len(live_orders(repo))
        pdf = build_pdf(repo, report_date)
    except Exception as e:
        _warn(f"could not build the report for {report_date}: {e}")
        return 0

    body = report_body(report_date, live_count, late=_is_late(now))
    rows = [
        {
            "kind": notifications.DAILY_REPORT_KIND,
            "recipient": recipient,
            "subject": f"{REPORT_SUBJECT} \u2014 {report_date}",
            "body": body,
            "dedupe_key": notifications.dedupe_daily_report(report_date, recipient),
            "attachment_ref": f"daily_pdf:{report_date}",
        }
        for recipient in recipients
    ]

    queued = notifications.enqueue_batch(rows, dsn=dsn)
    if queued is None:
        _warn(f"could not queue the report for {report_date}; will retry")
        return 0

    _log(f"queued {report_date} for {queued} of {len(recipients)} recipient(s); "
         f"{live_count} live orders, PDF {len(pdf)} bytes")
    return queued
