"""
tests/test_mailer.py — mailer.py's Resend HTTP client (T2,
ARCH-brief-11-email-notifications).

mailer.py is the only vendor-aware file in the codebase. These tests mock
`requests.post` — the boundary — and assert on what mailer sends and how it
classifies what comes back. Nothing here touches the network or the database.

The classification matters downstream: the outbox worker (T4) retries 5xx and
transport failures on a backoff ladder, but fails 4xx immediately, because a
permanently rejected address will not improve with retries.
"""

import pytest

import mailer


@pytest.fixture(autouse=True)
def _mail_env(monkeypatch):
    """A configured mailer, unless a test overrides it."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key_abc123")
    monkeypatch.setenv("MAIL_FROM", "UniFleet <support@unifleet.example>")
    monkeypatch.delenv("MAIL_REPLY_TO", raising=False)


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


def test_send_posts_expected_payload_and_auth_header(monkeypatch):
    """GIVEN a configured mailer WHEN send() is called THEN it POSTs to Resend
    with from/to/subject/body and a Bearer auth header (verifies R15)."""
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        captured["timeout"] = kwargs.get("timeout")
        return _FakeResponse(200, {"id": "msg_1"})

    monkeypatch.setattr(mailer.requests, "post", fake_post)

    mailer.send(
        to="driver@example.com",
        subject="UniFleet Account Code",
        body="Your Account Code is: HARR",
    )

    assert "resend.com" in captured["url"]
    assert captured["json"]["from"] == "UniFleet <support@unifleet.example>"
    assert captured["json"]["to"] == ["driver@example.com"]
    assert captured["json"]["subject"] == "UniFleet Account Code"
    assert "HARR" in captured["json"]["text"]
    assert captured["headers"]["Authorization"] == "Bearer re_test_key_abc123"
    assert captured["timeout"] == 10


def test_send_includes_reply_to_when_configured(monkeypatch):
    """GIVEN MAIL_REPLY_TO is set WHEN an email is sent THEN the payload
    carries that reply-to address (verifies R15)."""
    monkeypatch.setenv("MAIL_REPLY_TO", "ops@unifleet.example")
    captured = {}

    def fake_post(url, **kwargs):
        captured["json"] = kwargs.get("json")
        return _FakeResponse(200, {"id": "msg_1"})

    monkeypatch.setattr(mailer.requests, "post", fake_post)
    mailer.send(to="driver@example.com", subject="s", body="b")

    assert captured["json"]["reply_to"] == "ops@unifleet.example"


def test_send_omits_reply_to_when_not_configured(monkeypatch):
    """An unset MAIL_REPLY_TO must not send an empty reply_to field."""
    captured = {}

    def fake_post(url, **kwargs):
        captured["json"] = kwargs.get("json")
        return _FakeResponse(200, {"id": "msg_1"})

    monkeypatch.setattr(mailer.requests, "post", fake_post)
    mailer.send(to="driver@example.com", subject="s", body="b")

    assert "reply_to" not in captured["json"]


def test_send_base64_encodes_the_attachment(monkeypatch):
    """GIVEN an attachment of (filename, bytes, mimetype) WHEN send() is called
    THEN attachments[] carries the base64 content and the filename
    (verifies R4, R11)."""
    import base64

    captured = {}

    def fake_post(url, **kwargs):
        captured["json"] = kwargs.get("json")
        return _FakeResponse(200, {"id": "msg_1"})

    monkeypatch.setattr(mailer.requests, "post", fake_post)
    mailer.send(
        to="driver@example.com",
        subject="Booking Confirmed - UniFleet",
        body="voucher attached",
        attachment=("V123_Official.png", b"\x89PNG fake bytes", "image/png"),
    )

    attachments = captured["json"]["attachments"]
    assert len(attachments) == 1
    assert attachments[0]["filename"] == "V123_Official.png"
    assert base64.b64decode(attachments[0]["content"]) == b"\x89PNG fake bytes"


def test_send_omits_attachments_when_none_given(monkeypatch):
    """Most emails carry no attachment; the field must not appear empty."""
    captured = {}

    def fake_post(url, **kwargs):
        captured["json"] = kwargs.get("json")
        return _FakeResponse(200, {"id": "msg_1"})

    monkeypatch.setattr(mailer.requests, "post", fake_post)
    mailer.send(to="driver@example.com", subject="s", body="b")

    assert "attachments" not in captured["json"]


# ============================================================
# Result classification
# ============================================================
# The outbox worker (T4) branches on these: ok=True is terminal success,
# a 4xx is a permanent failure that must not be retried, and 5xx / 0 go
# back on the retry ladder.

def test_send_returns_provider_message_id_on_success(monkeypatch):
    """GIVEN Resend returns 200 with an id WHEN send() is called THEN the
    result is ok with provider_message_id populated."""
    monkeypatch.setattr(
        mailer.requests, "post",
        lambda url, **kw: _FakeResponse(200, {"id": "msg_abc123"}),
    )

    result = mailer.send(to="driver@example.com", subject="s", body="b")

    assert result.ok is True
    assert result.provider_message_id == "msg_abc123"
    assert result.status_code == 200
    assert result.error is None


def test_send_treats_4xx_as_a_permanent_failure(monkeypatch):
    """GIVEN Resend returns 422 for an invalid address WHEN send() is called
    THEN ok is False, the real status code comes back, and the provider's
    message is in error (verifies REQ edge case: an address the provider
    rejects permanently)."""
    monkeypatch.setattr(
        mailer.requests, "post",
        lambda url, **kw: _FakeResponse(
            422,
            {"message": "Invalid `to` field", "name": "validation_error"},
            text='{"message": "Invalid `to` field"}',
        ),
    )

    result = mailer.send(to="not-a-real-address", subject="s", body="b")

    assert result.ok is False
    assert result.status_code == 422
    assert "Invalid `to` field" in result.error
    assert result.provider_message_id is None


def test_send_treats_5xx_as_a_retryable_failure(monkeypatch):
    """GIVEN Resend returns 503 WHEN send() is called THEN ok is False with
    the real status code, so the worker puts it back on the retry ladder."""
    monkeypatch.setattr(
        mailer.requests, "post",
        lambda url, **kw: _FakeResponse(503, {}, text="service unavailable"),
    )

    result = mailer.send(to="driver@example.com", subject="s", body="b")

    assert result.ok is False
    assert result.status_code == 503
    assert result.error


def test_send_treats_transport_failure_as_status_zero(monkeypatch):
    """GIVEN the HTTP call raises a timeout WHEN send() is called THEN the
    result is ok=False with status_code 0 and send() does not raise
    (verifies ARCH forward stress-test: Resend unreachable for 30s)."""
    def boom(url, **kw):
        raise mailer.requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(mailer.requests, "post", boom)

    result = mailer.send(to="driver@example.com", subject="s", body="b")

    assert result.ok is False
    assert result.status_code == 0
    assert "timed out" in result.error


def test_send_treats_connection_error_as_status_zero(monkeypatch):
    """A refused connection is the same class of failure as a timeout."""
    def boom(url, **kw):
        raise mailer.requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(mailer.requests, "post", boom)

    result = mailer.send(to="driver@example.com", subject="s", body="b")

    assert result.ok is False
    assert result.status_code == 0


def test_send_does_not_raise_on_an_unexpected_error(monkeypatch):
    """Nothing reaches the worker as an exception — an unforeseen failure
    inside the client must still come back as a SendResult."""
    def boom(url, **kw):
        raise ValueError("something unforeseen")

    monkeypatch.setattr(mailer.requests, "post", boom)

    result = mailer.send(to="driver@example.com", subject="s", body="b")

    assert result.ok is False
    assert result.status_code == 0


# ============================================================
# Configuration
# ============================================================

def test_is_configured_is_false_without_an_api_key(monkeypatch):
    """GIVEN RESEND_API_KEY is missing WHEN is_configured() is called THEN it
    returns False, so the worker leaves rows queued instead of burning the
    retry ladder (verifies ARCH forward stress-test)."""
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert mailer.is_configured() is False


def test_is_configured_is_false_without_a_from_address(monkeypatch):
    monkeypatch.delenv("MAIL_FROM", raising=False)
    assert mailer.is_configured() is False


def test_is_configured_is_true_when_both_are_set():
    assert mailer.is_configured() is True


def test_is_configured_is_false_for_blank_values(monkeypatch):
    """An empty env var is as unconfigured as a missing one."""
    monkeypatch.setenv("RESEND_API_KEY", "   ")
    assert mailer.is_configured() is False


# ============================================================
# Security
# ============================================================

def test_api_key_never_appears_in_the_result(monkeypatch):
    """GIVEN any failure path WHEN the result is inspected THEN the API key is
    absent from it. The worker writes result.error straight into
    notifications.last_error, which admins read in the UI (verifies N2)."""
    key = "re_test_key_abc123"

    def leaky_post(url, **kw):
        # A client that echoed the request back would be the realistic way
        # for a key to end up in an error string.
        raise RuntimeError(f"failed calling {url} with headers {kw.get('headers')}")

    monkeypatch.setattr(mailer.requests, "post", leaky_post)

    result = mailer.send(to="driver@example.com", subject="s", body="b")

    assert result.ok is False
    assert key not in (result.error or "")


def test_api_key_never_appears_in_a_provider_error_body(monkeypatch):
    """Same guarantee when the provider itself echoes the credential back."""
    key = "re_test_key_abc123"
    monkeypatch.setattr(
        mailer.requests, "post",
        lambda url, **kw: _FakeResponse(
            401, {"message": f"invalid key {key}"}, text=f"invalid key {key}"
        ),
    )

    result = mailer.send(to="driver@example.com", subject="s", body="b")

    assert result.ok is False
    assert key not in (result.error or "")


# ============================================================
# B3 — send() must never raise, including on bad arguments
# ============================================================
# The worker's loop depends on this promise. An escape from here stranded the
# whole claimed batch in `sending` with attempts unincremented, where the
# stale sweep requeued it forever and no admin query could see it.

def test_a_malformed_attachment_tuple_does_not_raise(monkeypatch):
    """GIVEN an attachment that is not a 3-tuple WHEN send() is called THEN it
    returns a retryable failure instead of raising ValueError."""
    monkeypatch.setattr(
        mailer.requests, "post", lambda url, **kw: _FakeResponse(200, {"id": "m"})
    )

    result = mailer.send(
        to="driver@example.com", subject="s", body="b",
        attachment=("only-two", b"parts"),
    )

    assert result.ok is False
    assert result.status_code == 0
    assert "ValueError" in result.error


def test_a_non_bytes_attachment_body_does_not_raise(monkeypatch):
    """b64encode raises TypeError on a str payload."""
    monkeypatch.setattr(
        mailer.requests, "post", lambda url, **kw: _FakeResponse(200, {"id": "m"})
    )

    result = mailer.send(
        to="driver@example.com", subject="s", body="b",
        attachment=("v.png", "not-bytes", "image/png"),
    )

    assert result.ok is False
    assert result.status_code == 0
    assert "TypeError" in result.error


def test_the_api_key_is_redacted_from_an_argument_error(monkeypatch):
    """The new failure path must respect N2 like every other one."""
    monkeypatch.setattr(
        mailer.requests, "post", lambda url, **kw: _FakeResponse(200, {"id": "m"})
    )

    result = mailer.send(
        to="driver@example.com", subject="s", body="b",
        attachment=("v.png", "not-bytes", "image/png"),
    )

    assert "re_test_key_abc123" not in (result.error or "")
