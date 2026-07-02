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
    """Records create_customer calls; customer_exists follows a scripted
    sequence, then defaults to False."""

    def __init__(self, exists_seq=None):
        self.created = []
        self._exists_seq = list(exists_seq or [])
        self.exists_calls = []

    def customer_exists(self, code):
        self.exists_calls.append(code)
        if self._exists_seq:
            return self._exists_seq.pop(0)
        return False

    def create_customer(self, data):
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
    # create_customer never called against the pre-existing HARR
    assert all(c["account_code"] != "HARR" for c in stub.created)


# ============================================================
# Resilience & regression
# ============================================================

def test_pg_failure_still_writes_csv(client, monkeypatch):
    """If create_customer raises, the CSV append still happens and no 500."""

    class FailRepo(RepoStub):
        def create_customer(self, data):
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
