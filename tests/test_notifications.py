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
