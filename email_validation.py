"""
email_validation.py — one definition of "looks like an email address".

Extracted because the same regex was defined twice, in main.py and in
report_recipients.py, and the admin recipient flow ran both in sequence: main
validated the form, then report_recipients.add re-validated the same string.
Two copies of a rule that must agree is a drift waiting to happen (review
finding F28).

Deliberately permissive: one @, no spaces, a dot in the domain. Catching typos
is the goal. RFC-complete validation rejects addresses that genuinely work,
and every one of those is a lost registration.
"""

import re

# `\Z` rather than `$`: `$` also matches before a trailing newline, so
# "a@b.c\n" would validate. Every caller strips first, so this is belt and
# braces rather than a live bug.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+\Z")


def is_valid_email(value: str) -> bool:
    """True if `value` looks like an address worth trying to send to."""
    return bool(_EMAIL_RE.match((value or "").strip()))
