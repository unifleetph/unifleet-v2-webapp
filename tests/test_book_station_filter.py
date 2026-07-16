"""
tests/test_book_station_filter.py — /book hides stations with no discount.

Stations whose current discount is 0 / missing must not appear in the
station dropdown or the Live Pricing & Discounts table.
"""

import pytest

import main


CUST = {
    "account_code": "HARR",
    "company_name": "Harrods",
    "contact_name": "Harry",
    "contact_number": "0900-000-0000",
    "email": "",
    "fleet_size": 0,
    "areas": "",
    "refuel_locations": "",
    "hq_locations": "",
}

STATIONS = [
    {"id": "disc", "name": "EcoOil - EDSA Mandaluyong", "brand": "EcoOil",
     "price_php_per_liter": 60.0, "updated_at": 0},
    {"id": "nodisc", "name": "EcoOil - Bulacan", "brand": "EcoOil",
     "price_php_per_liter": 0.0, "updated_at": 0},
    {"id": "zero", "name": "Cleanfuel - Valenzuela", "brand": "Cleanfuel",
     "price_php_per_liter": 60.0, "updated_at": 0},
]


class RepoStub:
    def get_customer(self, code):
        return dict(CUST)

    def customer_exists(self, code):
        return True


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub())
    # TEMP (T2 bridge, F3.1): lambdas accept-and-ignore a fuel_type arg
    # since main.py's call sites now pass "Biodiesel" positionally.
    # T4/T5 will rewrite this whole file's assertions for the price-gate
    # behavior reversal (ARCH A7) — this is only a signature-compat patch.
    monkeypatch.setattr(main.price_store, "list_stations",
                        lambda *a, **kw: [dict(s) for s in STATIONS])
    # Only EDSA Mandaluyong has a positive discount; Cleanfuel is 0.
    monkeypatch.setattr(main.discount_store, "get_all",
                        lambda *a, **kw: {"EcoOil - EDSA Mandaluyong": 2.0,
                                           "Cleanfuel - Valenzuela": 0.0})
    main.app.config.update(TESTING=True)
    return main.app.test_client()


def test_hides_stations_without_discount(client):
    r = client.post("/book", data={"account_code": "HARR"})
    assert r.status_code == 200
    assert b"EcoOil - EDSA Mandaluyong" in r.data      # has discount -> shown
    assert b"EcoOil - Bulacan" not in r.data           # no discount -> hidden
    assert b"Cleanfuel - Valenzuela" not in r.data     # zero discount -> hidden


def test_discounted_station_present_in_dropdown(client):
    r = client.post("/book", data={"account_code": "HARR"})
    assert b'<option value="EcoOil - EDSA Mandaluyong">' in r.data
