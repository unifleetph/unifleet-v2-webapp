"""
tests/test_book_fuel_field.py — Fuel Type field hidden on Add-New-Driver (T2).

The Add-New-Driver block renders inside book.html's `{% if customer %}`
section, so a customer must be resolved first (stub main.repo.get_customer).
"""

import pytest

import main


CUST = {
    "account_code": "HARR",
    "company_name": "Harrods",
    "contact_name": "Harry",
    "contact_number": "0900-000-0000",
    "email": "",
    "fleet_size": 12,
    "areas": "",
    "refuel_locations": "",
    "hq_locations": "",
}


class RepoStub:
    def get_customer(self, code):
        return dict(CUST)

    def customer_exists(self, code):
        return True


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub())
    main.app.config.update(TESTING=True)
    return main.app.test_client()


def _booking_form(client):
    return client.post("/book", data={"account_code": "HARR"})


def test_fuel_type_field_is_hidden(client):
    """The fuel_type input is type=hidden and has no visible label."""
    resp = _booking_form(client)
    assert resp.status_code == 200
    body = resp.data
    # the fuel_type input itself is hidden (not a readonly text input)
    assert b'<input type="hidden" name="fuel_type" value="Diesel">' in body
    assert b'readonly' not in body
    # visible "Fuel Type" label is gone
    assert b"Fuel Type" not in body


def test_fuel_type_still_submits_diesel(client):
    """The hidden fuel_type input still carries value Diesel (POST contract)."""
    resp = _booking_form(client)
    assert b'value="Diesel"' in resp.data
