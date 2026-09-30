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

import sys
from typing import Optional

from db.pool import get_pool
from email_validation import is_valid_email

_COLUMNS = ["id", "email", "label", "is_active", "created_at", "updated_at"]


# Request-path reads and writes should not sit on the pool's default 30s
# checkout: the admin pages that call these must fail fast rather than hang.
POOL_TIMEOUT_SECONDS = 2


def _run(operation, *, dsn=None, label="", default=None, strict=False):
    """Run `operation(cursor)` with this module's standard policy.

    One place for the pool, the checkout bound, the commit and the swallow —
    the same block used to be repeated at every call site here and in
    notifications.py (review finding F27). `strict` re-raises instead of
    swallowing, which is what the nightly cron needs so that an unreachable
    database cannot masquerade as an empty recipient list (F12).
    """
    try:
        pool = get_pool(dsn=dsn)
        with pool.connection(timeout=POOL_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                result = operation(cur)
            conn.commit()
        return result
    except Exception as e:
        if strict:
            raise
        print(f"⚠️ report_recipients.{label or 'query'} failed: {e}", file=sys.stderr)
        return default


def _normalise(email: str) -> str:
    """Lowercase and strip. Without this, Ops@example.com and ops@example.com
    are two rows and one mailbox gets the report twice."""
    return str(email or "").strip().lower()


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

    def _select(cur):
        cur.execute(
            "SELECT id, email, label, is_active, created_at, updated_at "
            f"FROM report_recipients {clause}ORDER BY id DESC"
        )
        return [dict(zip(_COLUMNS, row)) for row in cur.fetchall()]

    return _run(_select, dsn=dsn, label="list_all", default=[], strict=strict)


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

    def _insert(cur):
        cur.execute(
            "INSERT INTO report_recipients (email, label) VALUES (%s, %s) "
            "ON CONFLICT (email) DO NOTHING "
            "RETURNING id, email, label, is_active, created_at, updated_at",
            (addr, str(label or "").strip() or None),
        )
        row = cur.fetchone()
        return dict(zip(_COLUMNS, row)) if row else None

    return _run(_insert, dsn=dsn, label=f"add({addr})")


def set_active(recipient_id: int, active: bool, dsn: Optional[str] = None) -> bool:
    """Soft-disable, so someone on leave can be muted without losing the row."""
    def _update(cur):
        cur.execute(
            "UPDATE report_recipients SET is_active = %s, updated_at = NOW() "
            "WHERE id = %s",
            (bool(active), recipient_id),
        )
        return cur.rowcount > 0

    return _run(_update, dsn=dsn, label=f"set_active({recipient_id})", default=False)


def delete(recipient_id: int, dsn: Optional[str] = None) -> bool:
    """Remove a recipient outright."""
    def _delete(cur):
        cur.execute("DELETE FROM report_recipients WHERE id = %s", (recipient_id,))
        return cur.rowcount > 0

    return _run(_delete, dsn=dsn, label=f"delete({recipient_id})", default=False)
