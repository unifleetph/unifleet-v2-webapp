"""
mailer.py — Resend HTTP client (T2, ARCH-brief-11-email-notifications).

The only vendor-aware file in the codebase. Everything else enqueues work
into the outbox (notifications.py) and lets the worker call send() from
here, so swapping providers is a change to this file alone.

Public API:
  send(to, subject, body, attachment=None) -> SendResult
  is_configured() -> bool

Configuration (environment):
  RESEND_API_KEY   API key. Sending is disabled unless set.
  MAIL_FROM        From header — the monitored support address (REQ R15).
  MAIL_REPLY_TO    Optional Reply-To override.
"""

import base64
import os
from dataclasses import dataclass
from typing import Optional

import requests

RESEND_ENDPOINT = "https://api.resend.com/emails"
REQUEST_TIMEOUT_SECONDS = 10


@dataclass
class SendResult:
    """The outcome of one send attempt.

    `status_code` is the provider's HTTP status, or 0 when the request never
    got a response (timeout, DNS, connection refused). The outbox worker
    branches on it: 4xx is permanent and must not be retried, while 5xx and 0
    go back on the retry ladder.
    """
    ok: bool
    status_code: int = 0
    provider_message_id: Optional[str] = None
    error: Optional[str] = None


def _api_key() -> str:
    return (os.environ.get("RESEND_API_KEY") or "").strip()


def _mail_from() -> str:
    return (os.environ.get("MAIL_FROM") or "").strip()


def _reply_to() -> str:
    return (os.environ.get("MAIL_REPLY_TO") or "").strip()


def _redact(text: str) -> str:
    """Strip the API key out of anything we hand back. Error strings are
    written to notifications.last_error and rendered to admins, so a client
    that echoes the request — or a provider that echoes the credential — must
    not be able to leak it there (N2)."""
    key = _api_key()
    if key and text:
        return text.replace(key, "***")
    return text


def is_configured() -> bool:
    """True when both the API key and the From address are present. The app
    boots fine without them — sending is simply disabled, and the worker says
    so rather than failing rows."""
    return bool(_api_key() and _mail_from())


def send(to: str, subject: str, body: str, attachment=None) -> SendResult:
    """POST one email to Resend. Never raises — every failure comes back as
    a SendResult with ok=False, so the caller can classify it.

    Payload construction is inside the try as well as the request: a malformed
    attachment tuple raises ValueError on unpacking, and a non-bytes body
    raises TypeError in b64encode. Those are programming errors, but this
    function promises never to raise and the worker's loop relies on that
    promise — an escape from here used to strand a whole batch of claimed rows
    in `sending` forever (review finding B3).
    """
    try:
        payload = {
            "from": _mail_from(),
            "to": [to],
            "subject": subject,
            "text": body,
        }

        reply_to = _reply_to()
        if reply_to:
            payload["reply_to"] = reply_to

        if attachment is not None:
            filename, content, mimetype = attachment
            payload["attachments"] = [{
                "filename": filename,
                "content": base64.b64encode(content).decode("ascii"),
                "content_type": mimetype,
            }]

        headers = {
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        }

        response = requests.post(
            RESEND_ENDPOINT,
            json=payload,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        # Timeout, DNS failure, connection refused, a malformed attachment, or
        # anything else that kept us from getting a response at all.
        # status_code 0 tells the worker this is retryable, unlike a 4xx the
        # provider chose to send.
        return SendResult(ok=False, status_code=0, error=_redact(repr(exc))[:500])

    return _classify(response)


def _classify(response) -> SendResult:
    """Turn a provider response into a SendResult. 2xx is success; anything
    else is a failure carrying the provider's own message, so the worker can
    record why and the admin can read it off the flag."""
    status = response.status_code
    if 200 <= status < 300:
        return SendResult(
            ok=True,
            status_code=status,
            provider_message_id=(_safe_json(response) or {}).get("id"),
        )
    # Redact before truncating: a key straddling the 500-char cut would
    # otherwise survive as an unredactable fragment.
    return SendResult(
        ok=False, status_code=status, error=_redact(_error_text(response))[:500]
    )


def _safe_json(response):
    """Resend returns JSON, but a proxy or gateway error may not."""
    try:
        return response.json()
    except Exception:
        return None


def _error_text(response) -> str:
    payload = _safe_json(response)
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error")
        if message:
            return str(message)
    return getattr(response, "text", "") or f"HTTP {response.status_code}"
