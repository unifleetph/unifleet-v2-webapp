"""
tests/test_book_fuel_field.py — Fuel Type field is a real, visible select
(T5, ARCH-fuel-types-expansion).

Supersedes the pre-F3.1 version of this file, which asserted the field
was hidden and hardcoded to "Diesel" — the opposite of what's now true.
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
    monkeypatch.setattr(main.price_store, "list_stations", lambda fuel_type: [])
    monkeypatch.setattr(main.discount_store, "get_all", lambda fuel_type: {})
    main.app.config.update(TESTING=True)
    return main.app.test_client()


def _booking_form(client):
    return client.post("/book", data={"account_code": "HARR"})


def test_fuel_type_field_is_a_visible_select(client):
    """The old hidden hardcoded input is gone; fuel_type is now a real,
    visible <select>."""
    resp = _booking_form(client)
    assert resp.status_code == 200
    body = resp.data
    assert b'<input type="hidden" name="fuel_type" value="Diesel">' not in body
    assert b'<select name="fuel_type" id="fuel_type"' in body
    assert b"Fuel Type" in body


def test_fuel_type_no_longer_hardcoded_to_diesel(client):
    """"Diesel" is not one of the 3 canonical options offered."""
    resp = _booking_form(client)
    assert b'value="Diesel"' not in resp.data
