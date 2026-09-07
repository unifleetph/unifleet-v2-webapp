"""
tests/test_notifications.py — notifications.py's outbox (T3/T4,
ARCH-brief-11-email-notifications).

The outbox is Postgres-only (ARCH A3), reached through db.pool like
audit_log.py and margin_store.py rather than through the persistence repo
abstraction. These tests use the session-scoped `schema_db` fixture and
reset the pool singleton around each test, following test_margin_store.py.

The three customer emails carry client-approved copy from
docs/Brief-11_email.md. It is asserted literally, not by substring: the
brief is the contract, and a "small" wording drift is a client-facing
regression nobody would catch by eye.
"""

import psycopg
import pytest

import mailer
import notifications


@pytest.fixture(autouse=True)
def _use_test_dsn(schema_db):
    import db.pool as pool_module

    pool_module.reset_pool()
    yield
    pool_module.reset_pool()


@pytest.fixture(autouse=True)
def _clean_notifications(schema_db):
    """schema_db is session-scoped, so rows would leak between tests and the
    UNIQUE dedupe_key would poison later inserts."""
    def _truncate():
        with psycopg.connect(schema_db) as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE notifications")
            conn.commit()

    _truncate()
    yield
    _truncate()


@pytest.fixture
def customer(schema_db):
    """A real customers row. notifications.account_code is a FK, so an
    enqueue for a customer who does not exist is correctly rejected — in
    production the customer was created moments earlier."""
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO customers (account_code, contact_name, email) "
                "VALUES (%s, %s, %s) ON CONFLICT (account_code) DO NOTHING",
                ("HARR", "Harriet Fleet", "driver@example.com"),
            )
        conn.commit()
    return "HARR"


def _rows(schema_db, **where):
    """Read notification rows back as dicts, newest first."""
    clause = ""
    params = []
    if where:
        clause = "WHERE " + " AND ".join(f"{k} = %s" for k in where)
        params = list(where.values())
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, kind, recipient, account_code, voucher_id, status, "
                "       attempts, last_error, dedupe_key, voucher_fingerprint, "
                "       attachment_ref, subject, body, next_attempt_at <= NOW() "
                f"FROM notifications {clause} ORDER BY id DESC",
                params,
            )
            cols = [
                "id", "kind", "recipient", "account_code", "voucher_id", "status",
                "attempts", "last_error", "dedupe_key", "voucher_fingerprint",
                "attachment_ref", "subject", "body", "due_now",
            ]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


# ============================================================
# Email copy — verbatim from docs/Brief-11_email.md
# ============================================================

def test_account_code_copy_matches_the_brief():
    """GIVEN account code HARR WHEN the account-code email is rendered THEN
    the subject and body match the brief with [XXXX] substituted
    (verifies R1)."""
    subject, body = notifications.render_account_code("HARR")

    assert subject == "UniFleet Account Code"
    assert body == (
        "Hi,\n"
        "Thank you for registering with UniFleet!\n"
        "Your Account Code is:\n"
        "HARR\n"
        "Please keep this code for your future UniFleet bookings and transactions.\n"
        "Thank you,\n"
        "UniFleet"
    )


def test_booking_received_copy_matches_the_brief():
    """The received email must say the booking is NOT yet confirmed and tell
    the customer to wait before going to the station (verifies R3)."""
    subject, body = notifications.render_booking_received()

    assert subject == "Booking Request Received - UniFleet"
    assert body == (
        "Hi,\n"
        "We’ve received your UniFleet booking request.\n"
        "Your request is currently being reviewed and is not yet confirmed. "
        "You will receive another email once your booking has been confirmed.\n"
        "Please wait for our Confirmation and Fuel Voucher email before "
        "proceeding to the station.\n"
        "Thank you,\n"
        "UniFleet"
    )


def test_booking_confirmed_copy_matches_the_brief():
    """The confirmed email lists the three redemption requirements and the
    48-hour reminder (verifies R4)."""
    subject, body = notifications.render_booking_confirmed()

    assert subject == "Booking Confirmed - UniFleet"
    assert body == (
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


def test_account_code_is_the_only_interpolated_value():
    """GIVEN any render call WHEN the body is inspected THEN nothing but the
    account code is substituted — no name, no booking data. Keeps the
    injection surface at zero and satisfies N4."""
    _, body_a = notifications.render_account_code("AAAA")
    _, body_b = notifications.render_account_code("ZZZZ")

    assert body_a.replace("AAAA", "<CODE>") == body_b.replace("ZZZZ", "<CODE>")
    assert "{" not in body_a and "}" not in body_a


# ============================================================
# Dedupe keys (ARCH A7)
# ============================================================
# The UNIQUE constraint on dedupe_key is the idempotency mechanism. Key
# shape is therefore a contract, not an implementation detail: get it wrong
# and either duplicates escape or legitimate re-sends are swallowed.

def test_dedupe_key_shapes_per_kind():
    """Each kind's key follows the shape ARCH's data model specifies
    (verifies N3)."""
    assert notifications.dedupe_account_code("HARR") == "acct:HARR"
    assert notifications.dedupe_booking_received("V123") == "booked:V123"
    assert (
        notifications.dedupe_booking_confirmed("V123", "abc123")
        == "confirmed:V123:abc123"
    )
    assert (
        notifications.dedupe_daily_report("2026-09-07", "ops@example.com")
        == "daily:2026-09-07:ops@example.com"
    )


def test_confirmed_dedupe_key_changes_with_the_fingerprint():
    """R10's whole mechanism: an unchanged voucher collides on the existing
    key and sends nothing, a changed one produces a new key and a new email."""
    same = notifications.dedupe_booking_confirmed("V123", "fingerprint_one")
    changed = notifications.dedupe_booking_confirmed("V123", "fingerprint_two")

    assert same != changed


# ============================================================
# Enqueue
# ============================================================

def test_enqueue_writes_a_queued_row(schema_db, customer):
    """GIVEN a valid recipient WHEN enqueue is called THEN one row exists,
    queued, with zero attempts, due now, and the rendered copy stored."""
    subject, body = notifications.render_account_code("HARR")

    new_id = notifications.enqueue(
        "account_code",
        recipient="driver@example.com",
        subject=subject,
        body=body,
        dedupe_key=notifications.dedupe_account_code("HARR"),
        account_code="HARR",
        dsn=schema_db,
    )

    assert new_id is not None
    rows = _rows(schema_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "account_code"
    assert row["recipient"] == "driver@example.com"
    assert row["status"] == "queued"
    assert row["attempts"] == 0
    assert row["due_now"] is True
    assert row["subject"] == subject
    assert row["body"] == body
    assert row["account_code"] == "HARR"


def test_enqueue_returns_none_on_a_dedupe_collision(schema_db):
    """GIVEN a row already exists for a key WHEN enqueue is called with the
    same key THEN it returns None and no second row is created
    (verifies A7, R10)."""
    def _enqueue():
        return notifications.enqueue(
            "booking_confirmed",
            recipient="driver@example.com",
            subject="Booking Confirmed - UniFleet",
            body="body",
            dedupe_key="confirmed:V123:samefingerprint",
            voucher_id=None,
            dsn=schema_db,
        )

    first = _enqueue()
    second = _enqueue()

    assert first is not None
    assert second is None
    assert len(_rows(schema_db)) == 1


def test_enqueue_stores_the_attachment_reference_not_the_bytes(schema_db):
    """A11: the row carries a reference; the worker resolves the current file
    at send time, so a customer never receives a stale voucher."""
    notifications.enqueue(
        "booking_confirmed",
        recipient="driver@example.com",
        subject="s",
        body="b",
        dedupe_key="confirmed:V999:fp",
        attachment_ref="voucher_png:V999",
        fingerprint="fp",
        dsn=schema_db,
    )

    row = _rows(schema_db)[0]
    assert row["attachment_ref"] == "voucher_png:V999"
    assert row["voucher_fingerprint"] == "fp"


# ============================================================
# Missing recipient (REQ R6)
# ============================================================

def test_enqueue_skipped_writes_a_terminal_row(schema_db, customer):
    """GIVEN a customer with no email WHEN enqueue_skipped is called THEN one
    terminal row exists with a NULL recipient and the reason recorded, which
    is what the admin 'no email' flag reads (verifies R6)."""
    notifications.enqueue_skipped(
        "booking_received",
        account_code="HARR",
        reason="customer has no email on file",
        dsn=schema_db,
    )

    rows = _rows(schema_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "skipped"
    assert row["recipient"] is None
    assert "no email" in row["last_error"]
    assert row["kind"] == "booking_received"


def test_enqueue_skipped_does_not_collide_across_bookings(schema_db):
    """Two different bookings can both be skipped — the skipped rows must not
    dedupe against each other."""
    notifications.enqueue_skipped(
        "booking_received", voucher_id=None, reason="no email", dsn=schema_db
    )
    notifications.enqueue_skipped(
        "booking_received", voucher_id=None, reason="no email", dsn=schema_db
    )

    assert len(_rows(schema_db)) == 2


# ============================================================
# Resilience (ARCH forward stress-test)
# ============================================================

def test_enqueue_swallows_a_database_failure():
    """GIVEN the database is unreachable WHEN enqueue is called THEN it
    returns None and does not raise. This is the normal path in local dev
    under PERSISTENCE_BACKEND=csv with no DATABASE_URL, and it is what keeps
    a Postgres outage from failing a registration."""
    result = notifications.enqueue(
        "account_code",
        recipient="driver@example.com",
        subject="s",
        body="b",
        dedupe_key="acct:UNREACHABLE",
        dsn="postgresql://nobody:nobody@127.0.0.1:1/nonexistent?connect_timeout=1",
    )

    assert result is None


def test_enqueue_skipped_swallows_a_database_failure():
    """Same guarantee on the skip path — it runs inside /book."""
    notifications.enqueue_skipped(
        "booking_received",
        reason="no email",
        dsn="postgresql://nobody:nobody@127.0.0.1:1/nonexistent?connect_timeout=1",
    )


# ============================================================
# T4 — worker, retry ladder, requeue
# ============================================================

@pytest.fixture
def sent_ok(monkeypatch):
    """Mailer that always succeeds; records what it was asked to send."""
    calls = []

    def fake_send(to, subject, body, attachment=None):
        calls.append({"to": to, "subject": subject, "body": body, "attachment": attachment})
        return mailer.SendResult(ok=True, status_code=200, provider_message_id="msg_ok")

    monkeypatch.setattr(notifications.mailer, "send", fake_send)
    monkeypatch.setattr(notifications.mailer, "is_configured", lambda: True)
    return calls


def _queue_one(schema_db, **overrides):
    """Enqueue a plain queued row and return its id."""
    kwargs = {
        "recipient": "driver@example.com",
        "subject": "s",
        "body": "b",
        "dedupe_key": f"test:{overrides.pop('key', 'default')}",
        "dsn": schema_db,
    }
    kwargs.update(overrides)
    return notifications.enqueue("account_code", **kwargs)


def _row(schema_db, row_id):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, attempts, last_error, last_status_code, "
                "       provider_message_id, sent_at, recipient, "
                "       next_attempt_at > NOW() "
                "FROM notifications WHERE id = %s",
                (row_id,),
            )
            cols = ["status", "attempts", "last_error", "last_status_code",
                    "provider_message_id", "sent_at", "recipient", "scheduled_ahead"]
            return dict(zip(cols, cur.fetchone()))


def test_drain_claims_only_due_queued_rows(schema_db, sent_ok):
    """GIVEN a due queued row, a future queued row and a sent row WHEN
    drain_once runs THEN only the due one is sent."""
    due = _queue_one(schema_db, key="due")
    future = _queue_one(schema_db, key="future")
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE notifications SET next_attempt_at = NOW() + interval '1 hour' "
                "WHERE id = %s", (future,),
            )
            cur.execute("UPDATE notifications SET status = 'sent' WHERE id = %s", (due,))
        conn.commit()

    also_due = _queue_one(schema_db, key="alsodue")
    processed = notifications.drain_once(dsn=schema_db)

    assert processed == 1
    assert len(sent_ok) == 1
    assert _row(schema_db, also_due)["status"] == "sent"
    assert _row(schema_db, future)["status"] == "queued"


def test_drain_marks_the_row_sent_with_the_provider_id(schema_db, sent_ok):
    """GIVEN the mailer succeeds WHEN a row is drained THEN it is sent, with
    sent_at and the provider's message id recorded."""
    row_id = _queue_one(schema_db, key="success")

    notifications.drain_once(dsn=schema_db)

    row = _row(schema_db, row_id)
    assert row["status"] == "sent"
    assert row["provider_message_id"] == "msg_ok"
    assert row["sent_at"] is not None
    assert row["last_error"] is None


# ------------------------------------------------------------
# Retry ladder
# ------------------------------------------------------------

@pytest.fixture
def sends_failing(monkeypatch):
    """Mailer whose result the test controls."""
    state = {"result": mailer.SendResult(ok=False, status_code=503, error="unavailable")}
    calls = []

    def fake_send(to, subject, body, attachment=None):
        calls.append(to)
        return state["result"]

    monkeypatch.setattr(notifications.mailer, "send", fake_send)
    monkeypatch.setattr(notifications.mailer, "is_configured", lambda: True)
    return state, calls


def test_a_5xx_returns_the_row_to_the_queue_with_backoff(schema_db, sends_failing):
    """GIVEN the provider returns 503 WHEN the row is drained THEN it goes back
    to queued with attempts incremented and next_attempt_at pushed into the
    future (verifies ARCH forward stress-test: Resend unreachable 30s)."""
    row_id = _queue_one(schema_db, key="fivehundred")

    notifications.drain_once(dsn=schema_db)

    row = _row(schema_db, row_id)
    assert row["status"] == "queued"
    assert row["attempts"] == 1
    assert row["scheduled_ahead"] is True
    assert row["last_status_code"] == 503
    assert "unavailable" in row["last_error"]


def test_the_backoff_ladder_lengthens_with_each_attempt(schema_db, sends_failing):
    """The delays follow ARCH's ladder: 1s, 5s, 30s, 2m, 10m."""
    assert notifications.RETRY_BACKOFF_SECONDS == [1, 5, 30, 120, 600]


def test_a_4xx_fails_immediately_without_retrying(schema_db, sends_failing):
    """GIVEN the provider returns 422 for a bad address WHEN the row is drained
    THEN it goes straight to failed on the first attempt — a permanently
    rejected address will not improve with retries (verifies REQ edge case)."""
    state, _ = sends_failing
    state["result"] = mailer.SendResult(ok=False, status_code=422, error="Invalid `to`")
    row_id = _queue_one(schema_db, key="fourtwentytwo")

    notifications.drain_once(dsn=schema_db)

    row = _row(schema_db, row_id)
    assert row["status"] == "failed"
    assert row["attempts"] == 1
    assert row["last_status_code"] == 422


def test_the_ladder_terminates_after_five_attempts(schema_db, sends_failing):
    """GIVEN a row that has already failed four times WHEN the fifth attempt
    fails with a 5xx THEN it becomes failed, with the provider's message kept
    for the admin flag (verifies R7, N5)."""
    row_id = _queue_one(schema_db, key="exhausted")
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE notifications SET attempts = 4 WHERE id = %s", (row_id,))
        conn.commit()

    notifications.drain_once(dsn=schema_db)

    row = _row(schema_db, row_id)
    assert row["status"] == "failed"
    assert row["attempts"] == 5
    assert row["last_error"]


def test_a_transport_failure_is_retried_like_a_5xx(schema_db, sends_failing):
    """status_code 0 means we never got a response — retryable, not permanent."""
    state, _ = sends_failing
    state["result"] = mailer.SendResult(ok=False, status_code=0, error="timed out")
    row_id = _queue_one(schema_db, key="transport")

    notifications.drain_once(dsn=schema_db)

    assert _row(schema_db, row_id)["status"] == "queued"


# ------------------------------------------------------------
# Attachments (ARCH A11)
# ------------------------------------------------------------

def test_the_attachment_is_resolved_at_send_time(schema_db, sent_ok, tmp_path, monkeypatch):
    """GIVEN a row referencing a voucher PNG WHEN it is drained THEN the file's
    CURRENT bytes are attached — not a copy taken at enqueue time, so a
    regenerated voucher is never sent stale (verifies A11, R4)."""
    png = tmp_path / "V777_Official.png"
    png.write_bytes(b"\x89PNG current bytes")
    monkeypatch.setattr(
        notifications.data_paths, "official_qr_png_path", lambda vid: png
    )
    _queue_one(schema_db, key="withattach", attachment_ref="voucher_png:V777")

    notifications.drain_once(dsn=schema_db)

    attachment = sent_ok[0]["attachment"]
    assert attachment is not None
    filename, content, mimetype = attachment
    assert content == b"\x89PNG current bytes"
    assert mimetype == "image/png"
    assert "V777" in filename


def test_a_missing_attachment_still_sends_and_flags(schema_db, sent_ok, tmp_path, monkeypatch):
    """GIVEN the referenced PNG does not exist WHEN the row is drained THEN the
    email goes out without it and the row records attachment_missing for
    manual follow-up (verifies A6 and the residual REQ decision #9 case)."""
    monkeypatch.setattr(
        notifications.data_paths,
        "official_qr_png_path",
        lambda vid: tmp_path / "does_not_exist.png",
    )
    row_id = _queue_one(schema_db, key="missingattach", attachment_ref="voucher_png:V888")

    notifications.drain_once(dsn=schema_db)

    row = _row(schema_db, row_id)
    assert row["status"] == "sent"
    assert sent_ok[0]["attachment"] is None
    assert "attachment_missing" in row["last_error"]


# ------------------------------------------------------------
# Recovery
# ------------------------------------------------------------

def test_stale_sending_rows_are_reclaimed(schema_db, sent_ok):
    """GIVEN a row stuck in 'sending' for more than five minutes — the app was
    redeployed mid-send — WHEN the worker runs THEN it returns to the queue
    and gets delivered (verifies ARCH forward stress-test)."""
    row_id = _queue_one(schema_db, key="stale")
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE notifications SET status = 'sending', "
                "       next_attempt_at = NOW() - interval '10 minutes' "
                "WHERE id = %s", (row_id,),
            )
        conn.commit()

    notifications.drain_once(dsn=schema_db)

    assert _row(schema_db, row_id)["status"] == "sent"


def test_a_recently_claimed_row_is_left_alone(schema_db, sent_ok):
    """A row claimed seconds ago is mid-flight, not stale — reclaiming it would
    manufacture the duplicate the sweep exists to bound."""
    row_id = _queue_one(schema_db, key="inflight")
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE notifications SET status = 'sending', next_attempt_at = NOW() "
                "WHERE id = %s", (row_id,),
            )
        conn.commit()

    notifications.drain_once(dsn=schema_db)

    assert _row(schema_db, row_id)["status"] == "sending"
    assert sent_ok == []


def test_requeue_resets_a_failed_row(schema_db):
    """GIVEN a failed row WHEN an admin hits Resend THEN it returns to queued
    with attempts cleared, ready for a fresh ladder (verifies R8)."""
    row_id = _queue_one(schema_db, key="requeue")
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE notifications SET status = 'failed', attempts = 5, "
                "       last_error = 'gave up' WHERE id = %s", (row_id,),
            )
        conn.commit()

    assert notifications.requeue(row_id, dsn=schema_db) is True

    row = _row(schema_db, row_id)
    assert row["status"] == "queued"
    assert row["attempts"] == 0


def test_requeue_is_idempotent(schema_db):
    """GIVEN a row already queued WHEN Resend is clicked again THEN nothing
    changes and no duplicate row appears (verifies REQ edge case)."""
    row_id = _queue_one(schema_db, key="doubleclick")

    notifications.requeue(row_id, dsn=schema_db)
    notifications.requeue(row_id, dsn=schema_db)

    assert len(_rows(schema_db)) == 1
    assert _row(schema_db, row_id)["status"] == "queued"


def test_requeue_can_repoint_a_skipped_row_at_a_new_address(schema_db, customer):
    """GIVEN a skipped row with no recipient WHEN an admin adds the customer's
    email and hits Resend THEN the row queues against that address — the
    recovery path for legacy emailless customers (verifies R8 with R9)."""
    notifications.enqueue_skipped(
        "booking_received", account_code="HARR", reason="no email", dsn=schema_db
    )
    row_id = _rows(schema_db)[0]["id"]

    assert notifications.requeue(row_id, recipient="new@example.com", dsn=schema_db) is True

    row = _row(schema_db, row_id)
    assert row["status"] == "queued"
    assert row["recipient"] == "new@example.com"


def test_requeue_returns_false_for_an_unknown_id(schema_db):
    assert notifications.requeue(999999, dsn=schema_db) is False


# ------------------------------------------------------------
# Gating and configuration
# ------------------------------------------------------------

def test_an_unconfigured_mailer_leaves_rows_queued(schema_db, monkeypatch):
    """GIVEN RESEND_API_KEY is missing WHEN drain_once runs THEN rows stay
    queued with attempts untouched, rather than burning the retry ladder on a
    misconfiguration no retry can fix (verifies ARCH forward stress-test)."""
    monkeypatch.setattr(notifications.mailer, "is_configured", lambda: False)
    sent = []
    monkeypatch.setattr(
        notifications.mailer, "send",
        lambda **kw: sent.append(kw) or mailer.SendResult(ok=True),
    )
    row_id = _queue_one(schema_db, key="unconfigured")

    processed = notifications.drain_once(dsn=schema_db)

    row = _row(schema_db, row_id)
    assert processed == 0
    assert sent == []
    assert row["status"] == "queued"
    assert row["attempts"] == 0


def test_start_worker_respects_the_feature_flag(monkeypatch):
    """GIVEN NOTIFICATIONS_ENABLED is false WHEN start_worker is called THEN no
    thread starts — the operator's kill switch (verifies A12)."""
    monkeypatch.setenv("NOTIFICATIONS_ENABLED", "false")
    notifications._reset_worker_for_tests()

    started = notifications.start_worker()

    assert started is False


def test_start_worker_is_idempotent(monkeypatch):
    """Called at main.py import; a reload must not spawn a second drainer."""
    monkeypatch.setenv("NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setattr(notifications, "_drain_forever", lambda: None)
    notifications._reset_worker_for_tests()

    first = notifications.start_worker()
    second = notifications.start_worker()

    assert first is True
    assert second is False
    notifications._reset_worker_for_tests()


def test_start_worker_never_raises(monkeypatch):
    """It runs at module import in main.py. If it raised, a mail problem would
    take down the entire app (guards ARCH backward-regression risk)."""
    monkeypatch.setenv("NOTIFICATIONS_ENABLED", "true")
    notifications._reset_worker_for_tests()

    def boom(*a, **kw):
        raise RuntimeError("cannot start threads")

    monkeypatch.setattr(notifications.threading, "Thread", boom)

    assert notifications.start_worker() is False
    notifications._reset_worker_for_tests()


# ------------------------------------------------------------
# Admin read paths
# ------------------------------------------------------------

@pytest.fixture
def voucher(schema_db):
    """A real vouchers row — notifications.voucher_id is a FK."""
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO vouchers (voucher_id, status) VALUES (%s, 'Unredeemed') "
                "ON CONFLICT (voucher_id) DO NOTHING",
                ("V777",),
            )
        conn.commit()
    return "V777"


def test_flags_by_voucher_maps_flagged_rows_to_their_voucher(schema_db, voucher):
    """GIVEN a failed notification for a voucher WHEN the dashboard asks for
    flags THEN the map carries that voucher's status and error (verifies R7)."""
    row_id = _queue_one(schema_db, key="flagged", voucher_id=voucher)
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE notifications SET status = 'failed', "
                "       last_error = 'provider said no' WHERE id = %s", (row_id,),
            )
        conn.commit()

    flags = notifications.flags_by_voucher([voucher, "V000"], dsn=schema_db)

    assert set(flags) == {voucher}
    assert flags[voucher]["status"] == "failed"
    assert flags[voucher]["last_error"] == "provider said no"
    assert flags[voucher]["notification_id"] == row_id


def test_flags_by_voucher_excludes_sent_rows(schema_db, voucher):
    """A delivered email is not a flag — it must not light up the dashboard."""
    row_id = _queue_one(schema_db, key="delivered", voucher_id=voucher)
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE notifications SET status='sent' WHERE id=%s", (row_id,))
        conn.commit()

    assert notifications.flags_by_voucher([voucher], dsn=schema_db) == {}


def test_flags_by_voucher_issues_one_query_for_many_ids(schema_db, voucher):
    """GIVEN 50 voucher ids WHEN flags are looked up THEN a single statement
    runs, not one per id — this executes on every /admin render."""
    executed = []

    real_get_pool = notifications.get_pool

    class _SpyCursor:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, params=None):
            executed.append(sql)
            return self._inner.execute(sql, params)

        def fetchall(self):
            return self._inner.fetchall()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _SpyConn:
        def __init__(self, inner):
            self._inner = inner

        def cursor(self):
            return _SpyCursor(self._inner.cursor().__enter__())

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _SpyPool:
        def __init__(self, inner):
            self._inner = inner

        def connection(self, *a, **kw):
            import contextlib

            @contextlib.contextmanager
            def _cm():
                with self._inner.connection(*a, **kw) as conn:
                    yield _SpyConn(conn)

            return _cm()

    def spy_pool(*a, **kw):
        return _SpyPool(real_get_pool(*a, **kw))

    import unittest.mock

    with unittest.mock.patch.object(notifications, "get_pool", spy_pool):
        notifications.flags_by_voucher([f"V{i:03d}" for i in range(50)], dsn=schema_db)

    assert len(executed) == 1, f"expected 1 query, got {len(executed)}"


def test_flags_by_voucher_returns_empty_for_no_ids(schema_db):
    """The dashboard with no vouchers must not build a malformed IN () query."""
    assert notifications.flags_by_voucher([], dsn=schema_db) == {}


def test_list_flagged_returns_only_failed_and_skipped(schema_db):
    """GIVEN a mix of statuses WHEN list_flagged is called THEN sent and queued
    rows are excluded (verifies R7)."""
    queued = _queue_one(schema_db, key="stillqueued")
    failed = _queue_one(schema_db, key="isfailed")
    sent = _queue_one(schema_db, key="issent")
    notifications.enqueue_skipped("booking_received", reason="no email", dsn=schema_db)

    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE notifications SET status='failed' WHERE id=%s", (failed,))
            cur.execute("UPDATE notifications SET status='sent' WHERE id=%s", (sent,))
        conn.commit()

    flagged = notifications.list_flagged(dsn=schema_db)
    statuses = {row["status"] for row in flagged}

    assert statuses == {"failed", "skipped"}
    assert len(flagged) == 2
    assert queued not in [row["id"] for row in flagged]


def test_the_daily_report_pdf_is_attached_from_disk(schema_db, sent_ok, tmp_path, monkeypatch):
    """GIVEN a daily_report row referencing a date WHEN it is drained THEN the
    PDF written by scripts/send_daily_report.py is attached (verifies R11)."""
    pdf = tmp_path / "UniFleet_Supplier_Sheet_2026-09-07.pdf"
    pdf.write_bytes(b"%PDF-nightly report")
    monkeypatch.setattr(
        notifications.data_paths, "daily_report_pdf_path", lambda date: pdf
    )
    _queue_one(schema_db, key="daily", attachment_ref="daily_pdf:2026-09-07")

    notifications.drain_once(dsn=schema_db)

    filename, content, mimetype = sent_ok[0]["attachment"]
    assert content == b"%PDF-nightly report"
    assert mimetype == "application/pdf"
    assert "2026-09-07" in filename


def test_a_missing_daily_report_pdf_still_sends_and_flags(schema_db, sent_ok, tmp_path, monkeypatch):
    """The report email is still worth sending without its attachment, and the
    flag tells an admin to follow up."""
    monkeypatch.setattr(
        notifications.data_paths,
        "daily_report_pdf_path",
        lambda date: tmp_path / "missing.pdf",
    )
    row_id = _queue_one(schema_db, key="dailymissing", attachment_ref="daily_pdf:2026-09-07")

    notifications.drain_once(dsn=schema_db)

    row = _row(schema_db, row_id)
    assert row["status"] == "sent"
    assert sent_ok[0]["attachment"] is None
    assert "attachment_missing" in row["last_error"]
