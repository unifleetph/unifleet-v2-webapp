"""
notifications.py — the email outbox (T3/T4, ARCH-brief-11-email-notifications).

Every outbound email is a row in the `notifications` table before it is a
network call. Request handlers enqueue and return immediately; a worker
thread drains the queue and calls mailer.send(). That indirection is what
keeps a slow or dead provider from stalling the single gunicorn worker and
from failing a customer's registration, booking, or approval (ARCH A1, N1).

Postgres-only, reached through db.pool rather than the persistence repo
abstraction — the same shape as audit_log.py and margin_store.py (ARCH A3).
Every failure here is swallowed: an outbox problem must never break the
request that triggered it.

This module must not import main or touch Flask's request context (ARCH
A15) — it runs inside a bare worker thread and a bare cron process, where
no request exists.

Public API (T3):
  render_account_code(code)     -> (subject, body)
  render_booking_received()     -> (subject, body)
  render_booking_confirmed()    -> (subject, body)
  enqueue(kind, ...)            -> int | None   (None on dedupe collision or DB failure)
  enqueue_skipped(kind, ...)    -> None

Email copy is verbatim from docs/Brief-11_email.md — client-approved, so it
is not to be "improved". The markdown fence's 4-space indentation and a few
trailing spaces are dropped as the typing artifacts they are; the en dash
and curly apostrophe are the client's and are preserved.
"""

import sys
from typing import Optional

from db.pool import get_pool

# ============================================================
# Email copy (REQ R1, R3, R4)
# ============================================================

# enqueue() runs inside the request handler, so waiting on the pool's default
# 30s timeout would stall a registration or booking for half a minute before
# swallowing the failure — exactly the stall N1 exists to prevent. Two seconds
# is far more than a healthy local pool needs and short enough that a customer
# never notices a dead database.
#
# CAVEAT: this bounds the checkout wait once the pool exists, which is the
# steady-state case. It does NOT bound pool construction: db.pool.get_pool()
# calls pool.wait(), whose own timeout argument defaults to 30s regardless of
# the pool's timeout= setting. So the first enqueue in a process whose
# database is unreachable still blocks ~30s. Fixing that is T14.
ENQUEUE_POOL_TIMEOUT_SECONDS = 2

ACCOUNT_CODE_SUBJECT = "UniFleet Account Code"
BOOKING_RECEIVED_SUBJECT = "Booking Request Received - UniFleet"
BOOKING_CONFIRMED_SUBJECT = "Booking Confirmed - UniFleet"

_ACCOUNT_CODE_BODY = (
    "Hi,\n"
    "Thank you for registering with UniFleet!\n"
    "Your Account Code is:\n"
    "{account_code}\n"
    "Please keep this code for your future UniFleet bookings and transactions.\n"
    "Thank you,\n"
    "UniFleet"
)

_BOOKING_RECEIVED_BODY = (
    "Hi,\n"
    "We’ve received your UniFleet booking request.\n"
    "Your request is currently being reviewed and is not yet confirmed. "
    "You will receive another email once your booking has been confirmed.\n"
    "Please wait for our Confirmation and Fuel Voucher email before "
    "proceeding to the station.\n"
    "Thank you,\n"
    "UniFleet"
)

_BOOKING_CONFIRMED_BODY = (
    "Hi,\n"
    "\n"
    "Your UniFleet booking has been confirmed and your voucher is here!\n"
    "\n"
    "Please bring the following requirements to the station to redeem:\n"
    "1. Fuel Voucher with QR code (attached)\n"
    "2. Valid ID\n"
    "3. Registered Vehicle\n"
    "\n"
    "Reminder to refuel at your booking time – if unredeemed after 48 hours "
    "from your booking time, funds will be returned to your UniFleet account "
    "(we will contact you).\n"
    "\n"
    "Thank you for choosing UniFleet!"
)


def render_account_code(account_code: str):
    """The registration email. The account code is the only substitution
    anywhere in this module's copy — see N4."""
    return ACCOUNT_CODE_SUBJECT, _ACCOUNT_CODE_BODY.format(account_code=account_code)


def render_booking_received():
    """The booking acknowledgement. Must read as 'received, NOT confirmed' —
    customers were turning up at stations before confirmation."""
    return BOOKING_RECEIVED_SUBJECT, _BOOKING_RECEIVED_BODY


def render_booking_confirmed():
    """The approval email. Ships with the voucher PNG attached."""
    return BOOKING_CONFIRMED_SUBJECT, _BOOKING_CONFIRMED_BODY


# ============================================================
# Dedupe keys (ARCH A7)
# ============================================================
# The UNIQUE constraint on notifications.dedupe_key is what makes sending
# idempotent. It is a database constraint rather than an application check
# because that is the only version that holds when two admins approve the
# same voucher at once, or when the nightly cron fires twice.

def dedupe_account_code(account_code: str) -> str:
    """One account-code email per customer, ever."""
    return f"acct:{account_code}"


def dedupe_booking_received(voucher_id: str) -> str:
    """One received email per booking."""
    return f"booked:{voucher_id}"


def dedupe_booking_confirmed(voucher_id: str, fingerprint: str) -> str:
    """R10: re-approving an unchanged voucher collides on this key and sends
    nothing; a changed amount, station, or voucher image yields a new
    fingerprint, a new key, and a fresh email."""
    return f"confirmed:{voucher_id}:{fingerprint}"


def dedupe_daily_report(manila_date: str, recipient: str) -> str:
    """One report per recipient per Manila day, however often the cron
    fires or the service restarts (N3)."""
    return f"daily:{manila_date}:{recipient}"


# ============================================================
# Enqueue
# ============================================================

def enqueue(
    kind: str,
    *,
    recipient: str,
    subject: str,
    body: str,
    dedupe_key: str,
    account_code: Optional[str] = None,
    voucher_id: Optional[str] = None,
    attachment_ref: Optional[str] = None,
    fingerprint: Optional[str] = None,
    dsn: Optional[str] = None,
) -> Optional[int]:
    """Queue one email for the worker to send.

    Returns the new row id, or None when the dedupe key already exists (the
    send is already accounted for) or the database is unreachable. Never
    raises: the caller is a request handler mid-registration, mid-booking,
    or mid-approval, and none of those may fail because of email.
    """
    try:
        pool = get_pool(dsn=dsn, timeout=ENQUEUE_POOL_TIMEOUT_SECONDS)
        with pool.connection(timeout=ENQUEUE_POOL_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO notifications "
                    "  (kind, recipient, account_code, voucher_id, dedupe_key, "
                    "   voucher_fingerprint, attachment_ref, subject, body) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (dedupe_key) DO NOTHING "
                    "RETURNING id",
                    (
                        kind, recipient, account_code, voucher_id, dedupe_key,
                        fingerprint, attachment_ref, subject, body,
                    ),
                )
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        # Same posture as audit_log.append_audit: log and swallow. Under
        # PERSISTENCE_BACKEND=csv with no DATABASE_URL this is the normal
        # path, and the app must run exactly as it did before.
        print(f"⚠️ notifications.enqueue({kind}) failed: {e}", file=sys.stderr)
        return None


def enqueue_skipped(
    kind: str,
    *,
    reason: str,
    account_code: Optional[str] = None,
    voucher_id: Optional[str] = None,
    dsn: Optional[str] = None,
) -> None:
    """Record that an email was deliberately not attempted, because there was
    no address to send it to (REQ R6).

    This is a terminal row, not a queued one — the worker never picks it up.
    It exists so the admin UI can flag the booking, and so an admin who later
    fills in the customer's email can hit Resend against something.

    dedupe_key is left NULL: Postgres allows many NULLs under a UNIQUE
    constraint, so two bookings by the same emailless customer both get their
    own flag rather than silently collapsing into one.
    """
    try:
        pool = get_pool(dsn=dsn, timeout=ENQUEUE_POOL_TIMEOUT_SECONDS)
        with pool.connection(timeout=ENQUEUE_POOL_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO notifications "
                    "  (kind, recipient, account_code, voucher_id, status, "
                    "   last_error, subject, body) "
                    "VALUES (%s, NULL, %s, %s, 'skipped', %s, '', '')",
                    (kind, account_code, voucher_id, reason),
                )
    except Exception as e:
        print(f"⚠️ notifications.enqueue_skipped({kind}) failed: {e}", file=sys.stderr)
