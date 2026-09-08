"""
report_recipients.py — the internal distribution list for the nightly
supplier PDF (T10, ARCH-brief-11-email-notifications).

Admin-managed rather than an environment variable: staff join and leave, and
changing who receives the nightly report must not require a redeploy (A13).

Postgres-backed, reached through db.pool like margin_store.py and
audit_log.py rather than through the persistence repo abstraction, since
this is Postgres-only in every environment that matters (A3).

Public API:
  list_all(include_inactive=False) -> list[dict]
  active_emails()                  -> list[str]   (what the cron sends to)
  add(email, label="")             -> dict | None (None on duplicate/invalid)
  set_active(id, active)           -> bool
  delete(id)                       -> bool

An empty list is a legitimate state, not an error: the list ships empty and
the nightly job treats "no recipients" as nothing to do.
"""

import re
import sys
from typing import Optional

from db.pool import get_pool

# Same permissive rule as /register: catch typos, do not adjudicate RFC 5322.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_COLUMNS = ["id", "email", "label", "is_active", "created_at", "updated_at"]


def _normalise(email: str) -> str:
    """Lowercase and strip. Without this, Ops@example.com and ops@example.com
    are two rows and one mailbox gets the report twice."""
    return str(email or "").strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(_normalise(email)))


def list_all(include_inactive: bool = False, dsn: Optional[str] = None,
             strict: bool = False) -> list:
    """Every recipient, newest first. Inactive ones only when asked for.

    `strict=True` re-raises instead of swallowing. The nightly cron needs
    that: swallowing turned an unreachable database into an empty list, so a
    Postgres outage took the "no recipients, nothing to send" branch and
    exited 0 — a green Railway run on the night the report did not go out
    (review finding F12). The web app keeps the swallowing default, where an
    empty admin list beats a stack trace.
    """
    clause = "" if include_inactive else "WHERE is_active "
    try:
        pool = get_pool(dsn=dsn)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, email, label, is_active, created_at, updated_at "
                    f"FROM report_recipients {clause}ORDER BY id DESC"
                )
                return [dict(zip(_COLUMNS, row)) for row in cur.fetchall()]
    except Exception as e:
        if strict:
            raise
        print(f"⚠️ report_recipients.list_all failed: {e}", file=sys.stderr)
        return []


def active_emails(dsn: Optional[str] = None, strict: bool = False) -> list:
    """The addresses the nightly report actually goes to.

    See list_all's note on `strict` — the cron passes it so that "the database
    is unreachable" cannot masquerade as "nobody is on the list".
    """
    return [r["email"] for r in list_all(dsn=dsn, strict=strict)]


def add(email: str, label: str = "", dsn: Optional[str] = None) -> Optional[dict]:
    """Add one recipient. Returns None if the address is malformed or already
    present — both are 'nothing changed' from the admin's point of view, and
    the route turns them into a flash message."""
    addr = _normalise(email)
    if not is_valid_email(addr):
        return None

    try:
        pool = get_pool(dsn=dsn)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO report_recipients (email, label) VALUES (%s, %s) "
                    "ON CONFLICT (email) DO NOTHING "
                    "RETURNING id, email, label, is_active, created_at, updated_at",
                    (addr, str(label or "").strip() or None),
                )
                row = cur.fetchone()
            conn.commit()
        return dict(zip(_COLUMNS, row)) if row else None
    except Exception as e:
        print(f"⚠️ report_recipients.add({addr}) failed: {e}", file=sys.stderr)
        return None


def set_active(recipient_id: int, active: bool, dsn: Optional[str] = None) -> bool:
    """Soft-disable, so someone on leave can be muted without losing the row."""
    try:
        pool = get_pool(dsn=dsn)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE report_recipients SET is_active = %s, updated_at = NOW() "
                    "WHERE id = %s",
                    (bool(active), recipient_id),
                )
                updated = cur.rowcount
            conn.commit()
        return updated > 0
    except Exception as e:
        print(f"⚠️ report_recipients.set_active({recipient_id}) failed: {e}",
              file=sys.stderr)
        return False


def delete(recipient_id: int, dsn: Optional[str] = None) -> bool:
    """Remove a recipient outright."""
    try:
        pool = get_pool(dsn=dsn)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM report_recipients WHERE id = %s", (recipient_id,))
                deleted = cur.rowcount
            conn.commit()
        return deleted > 0
    except Exception as e:
        print(f"⚠️ report_recipients.delete({recipient_id}) failed: {e}", file=sys.stderr)
        return False
