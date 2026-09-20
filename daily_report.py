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
from typing import List, Optional
from zoneinfo import ZoneInfo

import data_paths
from report_pdf import build_supplier_pdf

MANILA = ZoneInfo("Asia/Manila")

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
