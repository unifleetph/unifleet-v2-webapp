"""
tests/test_admin_customers.py — /admin/customers search/detail/picklist,
per-customer and global booking-history CSV exports (T3, T4;
ARCH-customer-details-page).

Stubs main.repo directly (same pattern as test_book_pg.py's RepoStub) so
no real Postgres/CSV is needed.
"""

import pytest

import main


HARR = {
    "account_code": "HARR",
    "contact_name": "Harry",
    "contact_number": "0900-000-0000",
    "email": "harry@example.com",
    "company_name": "Harrods",
    "fleet_size": 12,
    "areas": "QC",
}

ABCD = {
    "account_code": "ABCD",
    "contact_name": "Harriet",
    "contact_number": "0900-111-1111",
    "email": "harriet@example.com",
    "company_name": "Harriet Trading",
    "fleet_size": 3,
    "areas": "Cavite",
}


class RepoStub:
    def __init__(self, customers=None, vouchers=None):
        self._customers = {c["account_code"]: c for c in (customers or [])}
        self._vouchers = vouchers or []

    def get_customer(self, account_code):
        return self._customers.get(str(account_code or "").strip().upper())

    def customer_exists(self, account_code):
        return self.get_customer(account_code) is not None

    def list_customers(self):
        return list(self._customers.values())

    def list_all_vouchers(self):
        return list(self._vouchers)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "s3cret")
    monkeypatch.setattr(main, "ADMIN_KEY", "testkey")
    main.app.config.update(TESTING=True)
    return main.app.test_client()


def _login(client):
    client.post("/admin/login", data={"password": "s3cret"})


# ============================================================
# Auth gating
# ============================================================

def test_unauthenticated_redirected_to_login(client):
    r = client.get("/admin/customers")
    assert r.status_code == 302
    assert "/admin/login" in r.headers["Location"]


# ============================================================
# Search resolution
# ============================================================

def test_exact_account_code_goes_direct_to_detail(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customers=[HARR]))
    _login(client)

    r = client.get("/admin/customers?q=HARR")

    assert r.status_code == 200
    assert b"Harrods" in r.data
    assert b"harry@example.com" in r.data


def test_detail_view_shows_all_seven_register_fields(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customers=[HARR]))
    _login(client)

    r = client.get("/admin/customers?q=HARR")

    for expected in (
        b"HARR", b"Harry", b"0900-000-0000", b"harry@example.com",
        b"Harrods", b"12", b"QC",
    ):
        assert expected in r.data


def test_fuzzy_single_match_goes_direct_to_detail(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customers=[HARR]))
    _login(client)

    r = client.get("/admin/customers?q=Harrods")

    assert r.status_code == 200
    assert b"harry@example.com" in r.data


def test_fuzzy_multiple_matches_renders_picklist(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customers=[HARR, ABCD]))
    _login(client)

    r = client.get("/admin/customers?q=Harri")

    assert r.status_code == 200
    assert b"HARR" in r.data
    assert b"ABCD" in r.data
    # the picklist entries themselves (not the all-customers table below,
    # T4 ARCH-brief-3-fixes) still don't show full email inline
    body = r.data.decode("utf-8")
    picklist_block = body[body.index('<ul class="picklist">'):body.index("</ul>")]
    assert "harry@example.com" not in picklist_block
    assert "harriet@example.com" not in picklist_block


def test_no_match_renders_not_found(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customers=[HARR]))
    _login(client)

    r = client.get("/admin/customers?q=Zephyrine")

    assert r.status_code == 200
    assert b"No customer found" in r.data


# ============================================================
# Booking history scoping
# ============================================================

VOUCHERS = [
    {"voucher_id": "UF-1", "account_code": "HARR", "station": "Cleanfuel", "status": "Unverified"},
    {"voucher_id": "UF-2", "account_code": "ABCD", "station": "Seaoil", "status": "Unverified"},
]


def test_detail_view_booking_history_scoped_to_customer(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customers=[HARR, ABCD], vouchers=VOUCHERS))
    _login(client)

    r = client.get("/admin/customers?q=HARR")

    assert b"UF-1" in r.data
    assert b"UF-2" not in r.data


def test_customer_with_zero_bookings_renders_empty_history(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customers=[HARR], vouchers=[]))
    _login(client)

    r = client.get("/admin/customers?q=HARR")

    assert r.status_code == 200
    assert b"UF-1" not in r.data


# ============================================================
# Per-customer export
# ============================================================

def test_customer_export_returns_only_that_customers_bookings(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customers=[HARR, ABCD], vouchers=VOUCHERS))
    _login(client)

    r = client.get("/admin/customers/export?account_code=HARR")

    assert r.status_code == 200
    body = r.data.decode("utf-8-sig")
    assert "UF-1" in body
    assert "UF-2" not in body


def test_customer_export_unknown_account_code_returns_404(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customers=[HARR], vouchers=VOUCHERS))
    _login(client)

    r = client.get("/admin/customers/export?account_code=ZZZZ")

    assert r.status_code == 404


# ============================================================
# Global export
# ============================================================

def test_global_export_covers_all_customers_bookings(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customers=[HARR, ABCD], vouchers=VOUCHERS))
    _login(client)

    r = client.get("/admin/bookings/export")

    assert r.status_code == 200
    body = r.data.decode("utf-8-sig")
    assert "UF-1" in body
    assert "UF-2" in body


def test_global_export_zero_bookings_returns_headers_only(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customers=[HARR], vouchers=[]))
    _login(client)

    r = client.get("/admin/bookings/export")

    assert r.status_code == 200


# ============================================================
# T4 — template & nav testable seams
# ============================================================

def test_admin_dashboard_has_customer_lookup_link(client):
    _login(client)
    r = client.get("/admin")
    assert b'href="/admin/customers"' in r.data


def test_admin_dashboard_has_export_all_bookings_link(client):
    _login(client)
    r = client.get("/admin")
    assert b'href="/admin/bookings/export"' in r.data


def test_search_form_has_single_input_and_submit(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub())
    _login(client)
    r = client.get("/admin/customers")
    assert r.data.count(b'<input type="text" name="q"') == 1
    assert b'<button' in r.data and b'type="submit"' in r.data


def test_picklist_entries_link_to_exact_account_code(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customers=[HARR, ABCD]))
    _login(client)
    r = client.get("/admin/customers?q=Harri")
    assert b'href="/admin/customers?q=HARR"' in r.data
    assert b'href="/admin/customers?q=ABCD"' in r.data


def test_booking_history_table_has_header_row_when_empty(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customers=[HARR], vouchers=[]))
    _login(client)
    r = client.get("/admin/customers?q=HARR")
    assert b"<th>Voucher ID</th>" in r.data
    assert b"No bookings yet" in r.data


# ============================================================
# All-Customers Table (T4, ARCH-brief-3-fixes)
# ============================================================

def _voucher(account_code, driver_name):
    return {"account_code": account_code, "driver_name": driver_name}


def test_all_customers_table_present_on_empty_query(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customers=[HARR], vouchers=[]))
    _login(client)
    r = client.get("/admin/customers")
    assert b'id="all-customers-table"' in r.data
    assert b"Harry" in r.data


def test_all_customers_table_present_on_detail_state(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customers=[HARR], vouchers=[]))
    _login(client)
    r = client.get("/admin/customers?q=HARR")
    assert b'id="all-customers-table"' in r.data


def test_all_customers_table_present_on_picklist_state(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customers=[HARR, ABCD]))
    _login(client)
    r = client.get("/admin/customers?q=Harri")
    assert b'id="all-customers-table"' in r.data


def test_all_customers_table_present_on_not_found_state(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customers=[HARR]))
    _login(client)
    r = client.get("/admin/customers?q=ZZZZ")
    assert b'id="all-customers-table"' in r.data


def test_customer_with_two_distinct_drivers_gets_two_rows(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(
        customers=[HARR],
        vouchers=[_voucher("HARR", "Alice"), _voucher("HARR", "Bob")],
    ))
    _login(client)
    r = client.get("/admin/customers")
    body = r.data.decode("utf-8")
    table = body[body.index('id="all-customers-table"'):]
    assert table.count("Alice") == 1
    assert table.count("Bob") == 1


def test_customer_with_zero_bookings_gets_one_blank_driver_row(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub(customers=[HARR], vouchers=[]))
    _login(client)
    r = client.get("/admin/customers")
    body = r.data.decode("utf-8")
    table = body[body.index('id="all-customers-table"'):]
    assert "Harry" in table
    row_start = table.index("Harry")
    row_end = table.index("</tr>", row_start)
    assert "<td></td>" in table[row_start:row_end] or "<td> </td>" in table[row_start:row_end]
