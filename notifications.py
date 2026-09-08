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

import os
import sys
import threading
import time
from typing import Optional

import psycopg

import data_paths
import mailer
from db.pool import get_pool
from mailer import SendResult

# ============================================================
# Email copy (REQ R1, R3, R4)
# ============================================================

# enqueue() runs inside the request handler, so waiting on the pool's default
# 30s timeout would stall a registration or booking for half a minute before
# swallowing the failure — exactly the stall N1 exists to prevent. Two seconds
# is far more than a healthy local pool needs and short enough that a customer
# never notices a dead database.
#
# This bounds the checkout wait once the pool exists. Pool *construction* is
# bounded separately by db.pool's own wait timeout (T14), so the first enqueue
# in a process whose database is unreachable no longer blocks for ~30s either.
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
        pool = get_pool(dsn=dsn)
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


def _is_fk_violation(exc) -> bool:
    return isinstance(exc, psycopg.errors.ForeignKeyViolation)


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
    def _insert(cur, code, vid, note):
        cur.execute(
            "INSERT INTO notifications "
            "  (kind, recipient, account_code, voucher_id, status, "
            "   last_error, subject, body) "
            "VALUES (%s, NULL, %s, %s, 'skipped', %s, '', '')",
            (kind, code, vid, note),
        )

    try:
        pool = get_pool(dsn=dsn)
        try:
            with pool.connection(timeout=ENQUEUE_POOL_TIMEOUT_SECONDS) as conn:
                with conn.cursor() as cur:
                    _insert(cur, account_code, voucher_id, reason)
        except Exception as e:
            if not _is_fk_violation(e):
                raise
            # The customer or voucher this refers to is not in Postgres: an
            # account code that matches no customer, a customer created while
            # Postgres was down (the /register pg_error path writes CSV only),
            # or a CSV-backed deployment. Those are precisely the cases R6's
            # "skip and flag" exists to make visible, and the FK was silently
            # swallowing every one of them (review finding F9).
            #
            # Keep the flag, drop the references, and preserve what they were
            # in the reason so an admin can still act on it.
            detail = f"{reason} (account_code={account_code or '-'}, " \
                     f"voucher_id={voucher_id or '-'}; not present in Postgres)"
            with pool.connection(timeout=ENQUEUE_POOL_TIMEOUT_SECONDS) as conn:
                with conn.cursor() as cur:
                    _insert(cur, None, None, detail)
    except Exception as e:
        print(f"⚠️ notifications.enqueue_skipped({kind}) failed: {e}", file=sys.stderr)


# ============================================================
# Delivery (T4)
# ============================================================

def _claim_due(cur, limit: int):
    """Claim up to `limit` due rows, flipping them to 'sending'.

    FOR UPDATE SKIP LOCKED means two drainers never grab the same row. There
    is only one worker today (gunicorn --workers 1), but the cost of getting
    this right now is one clause, and the cost of getting it wrong later is
    duplicate emails.

    next_attempt_at doubles as the claim timestamp for 'sending' rows — the
    table has no claimed_at column, and this is what the stale sweep reads.
    """
    cur.execute(
        "UPDATE notifications SET status = 'sending', next_attempt_at = NOW() "
        "WHERE id IN ("
        "    SELECT id FROM notifications "
        "    WHERE status = 'queued' AND next_attempt_at <= NOW() "
        "    ORDER BY id "
        "    FOR UPDATE SKIP LOCKED "
        "    LIMIT %s"
        ") "
        "RETURNING id, recipient, subject, body, attachment_ref, attempts",
        (limit,),
    )
    cols = ["id", "recipient", "subject", "body", "attachment_ref", "attempts"]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ARCH's retry ladder: the delay before attempt N+1, in seconds. A provider
# blip self-heals inside the first few steps; a real outage surfaces as an
# admin flag within roughly fifteen minutes.
RETRY_BACKOFF_SECONDS = [1, 5, 30, 120, 600]
MAX_ATTEMPTS = len(RETRY_BACKOFF_SECONDS)

# A row that has sat in 'sending' this long was almost certainly abandoned by
# a process that died mid-send (a redeploy). Reclaiming it risks one duplicate
# email; leaving it stuck loses the email entirely, and the REQ prefers the
# duplicate.
STALE_SENDING_MINUTES = 5


def _resolve_attachment(attachment_ref):
    """Turn an attachment reference into (filename, bytes, mimetype).

    Resolution happens here, at send time, rather than at enqueue time, so the
    customer always receives the voucher as it stands now — a voucher
    regenerated after enqueue would otherwise go out stale (A11).

    Returns (attachment, note). A missing file yields (None, "attachment_missing")
    rather than an exception: the customer still learns they are confirmed, and
    the note becomes the admin's manual-follow-up flag (A6).
    """
    if not attachment_ref:
        return None, None

    kind, _, ident = attachment_ref.partition(":")

    if kind == "voucher_png":
        path = data_paths.official_qr_png_path(ident)
        try:
            return (path.name, path.read_bytes(), "image/png"), None
        except OSError:
            return None, "attachment_missing"

    if kind == "daily_pdf":
        # scripts/send_daily_report.py writes the file, then enqueues this
        # reference; ident is the Manila date the report covers.
        path = data_paths.daily_report_pdf_path(ident)
        try:
            return (path.name, path.read_bytes(), "application/pdf"), None
        except OSError:
            return None, "attachment_missing"

    return None, f"unknown attachment_ref: {attachment_ref}"


def _mark_sent(cur, row_id: int, provider_message_id, note=None) -> None:
    cur.execute(
        "UPDATE notifications SET status = 'sent', sent_at = NOW(), "
        "       provider_message_id = %s, last_status_code = 200, last_error = %s "
        "WHERE id = %s",
        (provider_message_id, note, row_id),
    )


def _record_failure(cur, row_id: int, attempts: int, result) -> None:
    """Advance the retry ladder, or go terminal.

    A 4xx is the provider telling us this will never work — a malformed or
    blocked address. Retrying it burns fifteen minutes before showing the
    admin a flag they could have had immediately. 5xx and status_code 0 (no
    response at all) are the retryable cases.
    """
    permanent = 400 <= result.status_code < 500
    exhausted = attempts >= MAX_ATTEMPTS

    if permanent or exhausted:
        cur.execute(
            "UPDATE notifications SET status = 'failed', attempts = %s, "
            "       last_error = %s, last_status_code = %s "
            "WHERE id = %s",
            (attempts, result.error, result.status_code, row_id),
        )
        return

    delay = RETRY_BACKOFF_SECONDS[attempts - 1]
    cur.execute(
        "UPDATE notifications SET status = 'queued', attempts = %s, "
        "       next_attempt_at = NOW() + (%s * interval '1 second'), "
        "       last_error = %s, last_status_code = %s "
        "WHERE id = %s",
        (attempts, delay, result.error, result.status_code, row_id),
    )


def _reclaim_stale(cur) -> int:
    """Return rows abandoned mid-send to the queue.

    `attempts` is incremented here as well. Without it a row that kills the
    drain every time it is picked up — a malformed attachment, an unexpected
    exception — cycles queued -> sending -> reclaimed indefinitely: it never
    reaches MAX_ATTEMPTS, never becomes `failed`, and neither admin query
    selects `sending` or `queued`, so it is invisible forever. Counting the
    reclaim as an attempt gives that loop a termination condition
    (review finding B2).
    """
    cur.execute(
        "UPDATE notifications SET status = 'queued', attempts = attempts + 1 "
        "WHERE status = 'sending' "
        "  AND next_attempt_at < NOW() - (%s * interval '1 minute')",
        (STALE_SENDING_MINUTES,),
    )
    return cur.rowcount


def requeue(row_id: int, recipient: Optional[str] = None,
            dsn: Optional[str] = None) -> bool:
    """Admin Resend: put a failed or skipped row back on the queue.

    `recipient` repoints the row, which is how a skipped notification for a
    legacy emailless customer is recovered once an admin fills the address in.
    Idempotent — requeueing an already-queued row is a no-op, so a double
    click cannot produce two emails.
    """
    try:
        pool = get_pool(dsn=dsn)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                if recipient:
                    cur.execute(
                        "UPDATE notifications SET status = 'queued', attempts = 0, "
                        "       next_attempt_at = NOW(), recipient = %s "
                        "WHERE id = %s AND status <> 'sending'",
                        (recipient, row_id),
                    )
                else:
                    cur.execute(
                        "UPDATE notifications SET status = 'queued', attempts = 0, "
                        "       next_attempt_at = NOW() "
                        "WHERE id = %s AND status <> 'sending'",
                        (row_id,),
                    )
                updated = cur.rowcount
            conn.commit()
        return updated > 0
    except Exception as e:
        print(f"⚠️ notifications.requeue({row_id}) failed: {e}", file=sys.stderr)
        return False


def drain_once(limit: int = 20, dsn: Optional[str] = None) -> int:
    """Claim every due row, send it, and record the outcome. Returns the
    number of rows processed."""
    global _unconfigured_logged

    if not mailer.is_configured():
        # Leave everything queued: no number of retries fixes a missing API
        # key, and burning the ladder would turn a config error into a pile of
        # permanently failed rows.
        if not _unconfigured_logged:
            print("notifications: mailer is not configured; leaving rows queued "
                  "(further occurrences suppressed until it is configured)",
                  file=sys.stderr)
            _unconfigured_logged = True
        return 0

    if _unconfigured_logged:
        print("notifications: mailer is configured; resuming sending",
              file=sys.stderr)
        _unconfigured_logged = False

    try:
        pool = get_pool(dsn=dsn)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                _reclaim_stale(cur)
                claimed = _claim_due(cur, limit)
            conn.commit()

            for row in claimed:
                # Per-row, so one bad row cannot strand the rest of the batch
                # — and, more importantly, cannot leave itself claimed with
                # attempts unincremented, which used to stall the outbox
                # permanently and invisibly (review finding B2).
                try:
                    attachment, note = _resolve_attachment(row["attachment_ref"])
                    result = mailer.send(
                        to=row["recipient"],
                        subject=row["subject"],
                        body=row["body"],
                        attachment=attachment,
                    )
                except Exception as row_err:
                    # An unexpected error is not retryable in any useful sense,
                    # but it must still advance the ladder so the row ends up
                    # `failed` and visible to an admin rather than looping.
                    print(f"⚠️ notifications: row {row['id']} raised: {row_err}",
                          file=sys.stderr)
                    attachment, note = None, None
                    result = SendResult(
                        ok=False, status_code=0, error=repr(row_err)[:500]
                    )

                try:
                    with conn.cursor() as cur:
                        if result.ok:
                            _mark_sent(cur, row["id"], result.provider_message_id, note)
                        else:
                            _record_failure(cur, row["id"], row["attempts"] + 1, result)
                    conn.commit()
                except Exception as write_err:
                    # The outcome could not be recorded. The row stays
                    # `sending`; the stale sweep reclaims it and now advances
                    # attempts, so it terminates rather than looping.
                    print(f"⚠️ notifications: could not record outcome for "
                          f"row {row['id']}: {write_err}", file=sys.stderr)
                    conn.rollback()

            return len(claimed)
    except Exception as e:
        print(f"⚠️ notifications.drain_once failed: {e}", file=sys.stderr)
        return 0


# ============================================================
# Worker thread (ARCH A2, A12)
# ============================================================

POLL_INTERVAL_SECONDS = 5

_worker_thread = None
_worker_lock = threading.Lock()

# The worker polls every few seconds forever. Without this latch, an
# unconfigured mailer would print the same line on every poll — roughly
# 17,000 lines a day in a production environment whose API key was never
# set, burying anything worth reading. Logged on the way into the state and
# again on the way out, so the transition is still visible.
_unconfigured_logged = False


def is_enabled() -> bool:
    """The operator's kill switch. Defaults to on; set NOTIFICATIONS_ENABLED
    to a falsey value to stop all sending without redeploying code."""
    raw = (os.environ.get("NOTIFICATIONS_ENABLED") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _drain_forever() -> None:
    """The worker loop. Each iteration is wrapped so a transient database or
    provider problem cannot kill the thread — if it died, emails would queue
    silently forever."""
    while True:
        try:
            drain_once()
        except Exception as e:
            print(f"⚠️ notifications worker iteration failed: {e}", file=sys.stderr)
        time.sleep(POLL_INTERVAL_SECONDS)


def start_worker() -> bool:
    """Start the background drainer. Returns True if this call started it.

    Called at main.py import time, so it must never raise: a mail problem
    taking down the whole app would be a far worse outcome than mail not
    being sent. Idempotent — a second call is a no-op.
    """
    global _worker_thread
    try:
        if not is_enabled():
            print("notifications: NOTIFICATIONS_ENABLED is off; worker not started",
                  file=sys.stderr)
            return False

        with _worker_lock:
            if _worker_thread is not None:
                return False
            thread = threading.Thread(
                target=_drain_forever,
                name="notifications-worker",
                daemon=True,
            )
            thread.start()
            _worker_thread = thread
            return True
    except Exception as e:
        print(f"⚠️ notifications.start_worker failed: {e}", file=sys.stderr)
        return False


def _reset_worker_for_tests() -> None:
    """Clear the worker handle. Tests only — the thread itself is a daemon."""
    global _worker_thread, _unconfigured_logged
    with _worker_lock:
        _worker_thread = None
    _unconfigured_logged = False


# ============================================================
# Admin read paths
# ============================================================

# What counts as needing an admin's attention. `sent` rows are included when
# they carry a note, because a confirmation that shipped without its voucher
# PNG is `sent` — and that was invisible everywhere, despite ARCH A6 promising
# it "becomes the admin's manual-follow-up flag" (review finding F8).
_FLAGGED_PREDICATE = (
    "(status IN ('failed', 'skipped') "
    " OR (status = 'sent' AND last_error IS NOT NULL))"
)


def flags_by_voucher(voucher_ids, dsn: Optional[str] = None) -> dict:
    """Map voucher_id -> flag info for every failed or skipped notification.

    One query for the whole page. The /admin dashboard renders up to 50 rows
    and already does per-row filesystem checks for PNG existence; adding a
    query per row on top of that would be the wrong direction.
    """
    ids = [v for v in (voucher_ids or []) if v]
    if not ids:
        return {}

    try:
        pool = get_pool(dsn=dsn)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT ON (voucher_id) "
                    "       voucher_id, id, kind, status, attempts, last_error "
                    "FROM notifications "
                    f"WHERE voucher_id = ANY(%s) AND {_FLAGGED_PREDICATE} "
                    "ORDER BY voucher_id, id DESC",
                    (ids,),
                )
                rows = cur.fetchall()
    except Exception as e:
        # The dashboard must still render if the outbox is unavailable.
        print(f"⚠️ notifications.flags_by_voucher failed: {e}", file=sys.stderr)
        return {}

    return {
        row[0]: {
            "notification_id": row[1],
            "kind": row[2],
            "status": row[3],
            "attempts": row[4],
            "last_error": row[5],
        }
        for row in rows
    }


def get(notification_id: int, dsn: Optional[str] = None) -> Optional[dict]:
    """Read one notification row.

    The resend route needs the row's account_code to re-resolve a recipient
    for a skipped notification (a legacy customer whose address an admin has
    since filled in). This module must not import the repo — ARCH's module
    boundary — so the lookup itself belongs to the caller; this just hands it
    the row.
    """
    try:
        pool = get_pool(dsn=dsn)
        with pool.connection(timeout=ENQUEUE_POOL_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, kind, recipient, account_code, voucher_id, status, "
                    "       attempts, last_error "
                    "FROM notifications WHERE id = %s",
                    (notification_id,),
                )
                row = cur.fetchone()
    except Exception as e:
        print(f"⚠️ notifications.get({notification_id}) failed: {e}", file=sys.stderr)
        return None

    if row is None:
        return None
    cols = ["id", "kind", "recipient", "account_code", "voucher_id",
            "status", "attempts", "last_error"]
    return dict(zip(cols, row))


def list_flagged(limit: int = 200, dsn: Optional[str] = None) -> list:
    """Every failed or skipped notification, newest first."""
    try:
        pool = get_pool(dsn=dsn)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, kind, recipient, account_code, voucher_id, status, "
                    "       attempts, last_error, created_at "
                    "FROM notifications "
                    f"WHERE {_FLAGGED_PREDICATE} "
                    "ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
                cols = ["id", "kind", "recipient", "account_code", "voucher_id",
                        "status", "attempts", "last_error", "created_at"]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        print(f"⚠️ notifications.list_flagged failed: {e}", file=sys.stderr)
        return []
