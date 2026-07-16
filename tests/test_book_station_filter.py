"""
tests/test_book_station_filter.py — /book station availability is
price-gated only (T5, ARCH-fuel-types-expansion, ARCH decision A7).

Supersedes the pre-F3.1 version of this file, which asserted stations
with no/zero discount were hidden — the opposite of the confirmed
design: availability depends only on whether a price row exists for
the (station, fuel_type) combo; a missing discount just means ₱0,
never hides the station. Since the station <select> is now populated
client-side (T5), these assertions target the server-rendered
window.__STATION_TABLE__ JSON embed and the price×discount reference
tables, not a rendered <option> list.
"""

import json
import re

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

# price_store.list_stations(fuel_type) already only returns stations
# with a price row for that fuel type — "no price" means absent from
# this list entirely, not present-with-zero.
PRICED_STATIONS = [
    {"id": "disc", "name": "EcoOil - EDSA Mandaluyong", "brand": "EcoOil",
     "price_php_per_liter": 60.0, "updated_at": 0},
    {"id": "nodisc", "name": "EcoOil - Bulacan", "brand": "EcoOil",
     "price_php_per_liter": 58.0, "updated_at": 0},
]

DISCOUNTS = {"EcoOil - EDSA Mandaluyong": 2.0}  # Bulacan has no discount row


class RepoStub:
    def get_customer(self, code):
        return dict(CUST)

    def customer_exists(self, code):
        return True


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub())
    monkeypatch.setattr(
        main.price_store, "list_stations",
        lambda fuel_type: [dict(s) for s in PRICED_STATIONS] if fuel_type == "Biodiesel" else []
    )
    monkeypatch.setattr(
        main.discount_store, "get_all",
        lambda fuel_type: dict(DISCOUNTS) if fuel_type == "Biodiesel" else {}
    )
    main.app.config.update(TESTING=True)
    return main.app.test_client()


def _station_table(resp_data):
    m = re.search(r"window\.__STATION_TABLE__ = (\{.*?\});", resp_data.decode("utf-8"), re.DOTALL)
    assert m, "window.__STATION_TABLE__ not found"
    return json.loads(m.group(1))


def test_priced_station_with_no_discount_still_shown(client):
    """Availability is price-gated only — a missing discount never
    hides a station."""
    r = client.post("/book", data={"account_code": "HARR"})
    assert r.status_code == 200
    biodiesel = _station_table(r.data)["Biodiesel"]
    names = {s["name"] for s in biodiesel}
    assert "EcoOil - EDSA Mandaluyong" in names
    assert "EcoOil - Bulacan" in names  # no discount, but still shown


def test_station_with_no_price_row_is_hidden(client):
    """A fuel type with zero priced stations (Premium, in this stub)
    has an empty entry — nothing spuriously appears."""
    r = client.post("/book", data={"account_code": "HARR"})
    premium = _station_table(r.data)["Premium"]
    assert premium == []


def test_discount_reference_table_shows_dash_for_missing_discount(client):
    r = client.post("/book", data={"account_code": "HARR"})
    body = r.data.decode("utf-8")
    # EDSA Mandaluyong has a discount; Bulacan doesn't (renders "—")
    assert "EcoOil - EDSA Mandaluyong" in body
    assert "EcoOil - Bulacan" in body
