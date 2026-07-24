"""
tests/test_admin_bookings_export.py — customer contact columns on
admin_bookings_export() and admin_customer_export() (T3,
ARCH-brief-3-fixes).
"""

import pandas as pd
import pytest

import main


CUSTOMER = {
    "account_code": "HARR",
    "contact_name": "Harry",
    "contact_number": "0900-000-0000",
    "email": "harry@example.com",
    "company_name": "Harrods",
    "fleet_size": 12,
    "areas": "QC",
}


class RepoStub:
    def __init__(self, customers=None, vouchers=None):
        self._customers = {c["account_code"]: c for c in (customers or [])}
        self._vouchers = vouchers or []

    def get_customer(self, account_code):
        return self._customers.get(str(account_code or "").strip().upper())

    def list_customers(self):
        return list(self._customers.values())

    def list_all_vouchers(self):
        return list(self._vouchers)


def _booking(voucher_id, account_code):
    row = {c: "" for c in main.VOUCHER_COLUMNS}
    row["voucher_id"] = voucher_id
    row["account_code"] = account_code
    return row


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "s3cret")
    main.app.config.update(TESTING=True)
    return main.app.test_client()


def _login(client):
    client.post("/admin/login", data={"password": "s3cret"})


# ============================================================
# Export All Bookings
# ============================================================

def test_export_all_bookings_includes_customer_columns(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(
        customers=[CUSTOMER],
        vouchers=[_booking("UF-1", "HARR")],
    ))
    _login(client)

    r = client.get("/admin/bookings/export")

    assert r.status_code == 200
    df = pd.read_csv(pd.io.common.BytesIO(r.data))
    assert df.loc[0, "Customer Name"] == "Harry"
    assert df.loc[0, "Customer Number"] == "0900-000-0000"
    assert df.loc[0, "Customer Email"] == "harry@example.com"


def test_export_all_bookings_blank_for_missing_account_code(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(
        customers=[CUSTOMER],
        vouchers=[_booking("UF-2", "")],
    ))
    _login(client)

    r = client.get("/admin/bookings/export")

    assert r.status_code == 200
    df = pd.read_csv(pd.io.common.BytesIO(r.data))
    assert pd.isna(df.loc[0, "Customer Name"]) or df.loc[0, "Customer Name"] == ""
    assert pd.isna(df.loc[0, "Customer Number"]) or df.loc[0, "Customer Number"] == ""
    assert pd.isna(df.loc[0, "Customer Email"]) or df.loc[0, "Customer Email"] == ""


# ============================================================
# Per-Customer Export
# ============================================================

def test_customer_export_includes_customer_columns(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(
        customers=[CUSTOMER],
        vouchers=[_booking("UF-3", "HARR")],
    ))
    _login(client)

    r = client.get("/admin/customers/export?account_code=HARR")

    assert r.status_code == 200
    df = pd.read_csv(pd.io.common.BytesIO(r.data))
    assert df.loc[0, "Customer Name"] == "Harry"
    assert df.loc[0, "Customer Number"] == "0900-000-0000"
    assert df.loc[0, "Customer Email"] == "harry@example.com"


# ============================================================
# Regression Guard
# ============================================================

def test_existing_voucher_columns_unchanged_in_both_exports(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(
        customers=[CUSTOMER],
        vouchers=[_booking("UF-4", "HARR")],
    ))
    _login(client)

    r_all = client.get("/admin/bookings/export")
    df_all = pd.read_csv(pd.io.common.BytesIO(r_all.data))
    for col in main.VOUCHER_COLUMNS:
        assert col in df_all.columns

    r_customer = client.get("/admin/customers/export?account_code=HARR")
    df_customer = pd.read_csv(pd.io.common.BytesIO(r_customer.data))
    for col in main.VOUCHER_COLUMNS:
        assert col in df_customer.columns


def test_customer_export_still_404s_for_unknown_account_code(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customers=[], vouchers=[]))
    _login(client)

    r = client.get("/admin/customers/export?account_code=NOPE")

    assert r.status_code == 404
