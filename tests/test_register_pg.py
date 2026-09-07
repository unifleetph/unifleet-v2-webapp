"""
tests/test_register_pg.py — /register dual-write + collision-safe code (CT2).

Stubs the module-level `main.repo` so no Postgres is needed, and redirects
CUSTOMERS_CSV to a temp file for the CSV side of the dual-write.
"""

import re

import pandas as pd
import pytest

import data_paths
import main


FORM = {
    "company_name": "Harrods",
    "contact_name": "Harry",
    "contact_number": "0900-000-0000",
    "email": "harry@example.com",
    "fleet_size": "12",
    "areas": "QC",
}


class RepoStub:
    """Records create_customer_if_absent calls; a scripted collision
    sequence controls whether each call reports the code as already
    taken (returns None) or succeeds (records + returns the row)."""

    def __init__(self, exists_seq=None):
        self.created = []
        self._exists_seq = list(exists_seq or [])
        self.exists_calls = []

    def create_customer_if_absent(self, data):
        code = data.get("account_code")
        self.exists_calls.append(code)
        taken = self._exists_seq.pop(0) if self._exists_seq else False
        if taken:
            return None
        self.created.append(dict(data))
        return dict(data)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(data_paths, "CUSTOMERS_CSV", tmp_path / "customers.csv")
    main.app.config.update(TESTING=True)
    return main.app.test_client()


def _use_repo(monkeypatch, stub):
    monkeypatch.setattr(main, "repo", stub)


# ============================================================
# Register persistence
# ============================================================

def test_writes_to_postgres(client, monkeypatch):
    """POST /register calls repo.create_customer with derived code + fields."""
    stub = RepoStub(exists_seq=[False])
    _use_repo(monkeypatch, stub)

    client.post("/register", data=FORM)

    assert len(stub.created) == 1
    rec = stub.created[0]
    assert rec["account_code"] == "HARR"
    assert rec["company_name"] == "Harrods"
    assert rec["contact_name"] == "Harry"


def test_dual_writes_csv(client, monkeypatch):
    """POST /register also appends the row to customers.csv."""
    stub = RepoStub(exists_seq=[False])
    _use_repo(monkeypatch, stub)

    client.post("/register", data=FORM)

    df = pd.read_csv(data_paths.CUSTOMERS_CSV, dtype=str).fillna("")
    assert (df["account_code"].str.strip().str.upper() == "HARR").any()


# ============================================================
# Collision handling
# ============================================================

def test_collision_generates_unique_code(client, monkeypatch):
    """If the derived code exists, an alternate 4-letter code is used and
    the pre-existing customer is not overwritten."""
    stub = RepoStub(exists_seq=[True, False])
    _use_repo(monkeypatch, stub)

    client.post("/register", data=FORM)

    assert len(stub.created) == 1
    code = stub.created[0]["account_code"]
    assert code != "HARR"
    assert re.fullmatch(r"[A-Z]{4}", code)
    # create_customer_if_absent never succeeded for the pre-existing HARR
    assert all(c["account_code"] != "HARR" for c in stub.created)


def test_exhausted_retries_shows_error(client, monkeypatch):
    """If every attempt collides (10 exhausted), flash an error and
    re-render the form — no customer is created on either backend."""
    stub = RepoStub(exists_seq=[True] * 10)
    _use_repo(monkeypatch, stub)

    resp = client.post("/register", data=FORM)

    assert resp.status_code == 200
    assert b"Could not generate a unique account code" in resp.data
    assert len(stub.created) == 0
    assert not data_paths.CUSTOMERS_CSV.exists()


# ============================================================
# Resilience & regression
# ============================================================

def test_pg_failure_still_writes_csv(client, monkeypatch):
    """If create_customer raises, the CSV append still happens and no 500."""

    class FailRepo(RepoStub):
        def create_customer_if_absent(self, data):
            raise RuntimeError("pg down")

    stub = FailRepo(exists_seq=[False])
    _use_repo(monkeypatch, stub)

    resp = client.post("/register", data=FORM)

    assert resp.status_code in (200, 302)
    df = pd.read_csv(data_paths.CUSTOMERS_CSV, dtype=str).fillna("")
    assert (df["account_code"].str.strip().str.upper() == "HARR").any()


def test_success_redirect_preserved(client, monkeypatch):
    """Valid POST redirects to /register/success?account_code=<code>."""
    stub = RepoStub(exists_seq=[False])
    _use_repo(monkeypatch, stub)

    resp = client.post("/register", data=FORM)

    assert resp.status_code == 302
    assert "/register/success?account_code=HARR" in resp.headers["Location"]


# ============================================================
# ARCH-brief-8 T4 (R9/R10): contact_number validation (new reject path)
# ============================================================

def test_contact_number_under_ten_digits_rejects_registration(client, monkeypatch):
    """New reject path: register() had no server-side validation before
    this — a too-short contact_number must now flash an error and
    re-render the form instead of creating the customer."""
    stub = RepoStub(exists_seq=[False])
    _use_repo(monkeypatch, stub)

    form = dict(FORM, contact_number="091234")
    resp = client.post("/register", data=form)
    body = resp.data.decode("utf-8")

    assert resp.status_code == 200
    assert len(stub.created) == 0
    assert not data_paths.CUSTOMERS_CSV.exists()
    assert "Please enter a valid Contact Number" in body
    # form values (other than the rejected field) are retained on re-render
    assert 'value="091234"' in body
    assert 'value="Harrods"' in body


def test_contact_number_exactly_ten_digits_accepts_registration(client, monkeypatch):
    stub = RepoStub(exists_seq=[False])
    _use_repo(monkeypatch, stub)

    form = dict(FORM, contact_number="0900000000")
    resp = client.post("/register", data=form)

    assert resp.status_code == 302
    assert len(stub.created) == 1


def test_contact_number_existing_fixture_value_unchanged_on_store(client, monkeypatch):
    """Regression guard (ARCH A3): the 11-digit fixture value with dashes
    ("0900-000-0000") passes the new digit-count check (stripping is for
    the count only) and is stored UNCHANGED — no rewrite of contact_number
    on this pre-existing field."""
    stub = RepoStub(exists_seq=[False])
    _use_repo(monkeypatch, stub)

    client.post("/register", data=FORM)

    assert len(stub.created) == 1
    assert stub.created[0]["contact_number"] == "0900-000-0000"


def test_register_country_code_select_renders(client, monkeypatch):
    """Client-side check: /register shows a country-code selector
    defaulting to +63 (R9)."""
    resp = client.get("/register")
    body = resp.data.decode("utf-8")

    assert 'name="mobile_country_code"' in body
    assert '<option value="+63" selected>+63 (Philippines)</option>' in body


# ============================================================
# T5 — email required, account-code email queued
# (ARCH-brief-11-email-notifications)
# ============================================================

@pytest.fixture
def queued(monkeypatch):
    """Capture enqueue calls instead of writing to Postgres."""
    calls = []

    def fake_enqueue(kind, **kwargs):
        calls.append({"kind": kind, **kwargs})
        return len(calls)

    monkeypatch.setattr(main.notifications, "enqueue", fake_enqueue)
    return calls


def test_blank_email_is_rejected_and_creates_no_customer(client, monkeypatch, queued):
    """GIVEN a registration with an empty email WHEN it is submitted THEN the
    form re-renders with an error and no customer is created (verifies R2)."""
    stub = RepoStub()
    _use_repo(monkeypatch, stub)
    form = dict(FORM, email="")

    resp = client.post("/register", data=form)

    assert resp.status_code == 200
    assert stub.created == []
    assert queued == []


def test_malformed_email_is_rejected(client, monkeypatch, queued):
    """GIVEN an email of 'not-an-email' WHEN submitted THEN it is rejected and
    no customer is created (verifies R2, REQ edge case)."""
    stub = RepoStub()
    _use_repo(monkeypatch, stub)

    resp = client.post("/register", data=dict(FORM, email="not-an-email"))

    assert resp.status_code == 200
    assert stub.created == []
    assert queued == []


@pytest.mark.parametrize("bad", ["no-at-sign.com", "two@@at.com", "trailing@", "@leading.com", "spaces in@example.com"])
def test_email_validation_rejects_malformed_addresses(client, monkeypatch, queued, bad):
    stub = RepoStub()
    _use_repo(monkeypatch, stub)

    client.post("/register", data=dict(FORM, email=bad))

    assert stub.created == [], f"{bad!r} should not have been accepted"


def test_rejected_registration_preserves_entered_values(client, monkeypatch, queued):
    """GIVEN a rejected submission WHEN the form re-renders THEN the other
    entered values are still populated, so the customer is not retyping
    everything (verifies R2)."""
    _use_repo(monkeypatch, RepoStub())

    resp = client.post("/register", data=dict(FORM, email=""))
    html = resp.get_data(as_text=True)

    assert "Harrods" in html
    assert "Harry" in html


def test_valid_registration_queues_the_account_code_email(client, monkeypatch, queued):
    """GIVEN a valid submission WHEN the customer is created THEN exactly one
    account_code notification is queued for that address, carrying the real
    account code (verifies R1)."""
    stub = RepoStub()
    _use_repo(monkeypatch, stub)

    resp = client.post("/register", data=FORM)

    assert resp.status_code == 302
    assert len(queued) == 1
    call = queued[0]
    assert call["kind"] == "account_code"
    assert call["recipient"] == "harry@example.com"
    code = stub.created[0]["account_code"]
    assert call["account_code"] == code
    assert code in call["body"]
    assert call["subject"] == "UniFleet Account Code"
    assert call["dedupe_key"] == f"acct:{code}"


def test_the_email_is_queued_only_after_the_customer_is_stored(client, monkeypatch):
    """A code must never be emailed for an account that failed to save, so the
    enqueue happens after both the repo write and the CSV append (verifies R1)."""
    stub = RepoStub()
    _use_repo(monkeypatch, stub)
    seen = {}

    def fake_enqueue(kind, **kwargs):
        seen["created_at_enqueue_time"] = list(stub.created)
        seen["csv_exists"] = data_paths.CUSTOMERS_CSV.exists()
        return 1

    monkeypatch.setattr(main.notifications, "enqueue", fake_enqueue)

    client.post("/register", data=FORM)

    assert len(seen["created_at_enqueue_time"]) == 1
    assert seen["csv_exists"] is True


def test_an_enqueue_failure_does_not_fail_registration(client, monkeypatch):
    """GIVEN the outbox raises WHEN a valid registration is submitted THEN the
    customer is still created and the success redirect still happens
    (verifies R7, ARCH forward stress-test)."""
    stub = RepoStub()
    _use_repo(monkeypatch, stub)

    def boom(kind, **kwargs):
        raise RuntimeError("outbox exploded")

    monkeypatch.setattr(main.notifications, "enqueue", boom)

    resp = client.post("/register", data=FORM)

    assert resp.status_code == 302
    assert len(stub.created) == 1


# ------------------------------------------------------------
# Regression guards
# ------------------------------------------------------------

def test_contact_number_rule_is_unchanged(client, monkeypatch, queued):
    """The pre-existing >=10 digit rule still rejects short numbers
    (guards ARCH-brief-8 R9/R10)."""
    stub = RepoStub()
    _use_repo(monkeypatch, stub)

    resp = client.post("/register", data=dict(FORM, contact_number="0900"))

    assert resp.status_code == 200
    assert stub.created == []


def test_postgres_down_path_still_registers(client, monkeypatch, queued):
    """GIVEN create_customer_if_absent raises WHEN a valid registration is
    submitted THEN the pg_error sentinel path still completes it
    (guards main.py's existing Postgres-down resilience)."""
    class RaisingRepo(RepoStub):
        def create_customer_if_absent(self, data):
            raise RuntimeError("Postgres is down")

    _use_repo(monkeypatch, RaisingRepo())

    resp = client.post("/register", data=FORM)

    assert resp.status_code == 302


def test_email_field_is_marked_required_in_the_form(client):
    """The client-side attribute backs up the server-side rule (verifies R2)."""
    html = client.get("/register").get_data(as_text=True)

    assert re.search(r'name="email"[^>]*required', html) or re.search(
        r'required[^>]*name="email"', html
    )


def test_registration_survives_a_missing_notifications_module(client, monkeypatch):
    """GIVEN the notifications module failed to import WHEN a valid
    registration is submitted THEN it still completes. main.py guards the
    import the way it guards build_supplier_pdf: a mail problem must never
    take down registration (guards ARCH backward-regression risk for
    main.py module import)."""
    stub = RepoStub()
    _use_repo(monkeypatch, stub)
    monkeypatch.setattr(main, "notifications", None)

    resp = client.post("/register", data=FORM)

    assert resp.status_code == 302
    assert len(stub.created) == 1
