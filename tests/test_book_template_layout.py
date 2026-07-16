"""
tests/test_book_template_layout.py — /book template static structure
(T5, ARCH-fuel-types-expansion): form reorder, Fuel Type select, 3
collapsible price tables, per-fuel-type window.__STATION_TABLE__.
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
    resp = client.post("/book", data={"account_code": "HARR"})
    assert resp.status_code == 200
    return resp.data.decode("utf-8")


def test_driver_vehicle_precedes_station_section(client):
    body = _booking_form(client)
    driver_idx = body.find('id="driver_mode"')
    station_idx = body.find('id="station"')
    assert driver_idx != -1 and station_idx != -1
    assert driver_idx < station_idx


def test_fuel_type_select_has_exactly_three_real_options(client):
    body = _booking_form(client)
    select_match = re.search(r'<select name="fuel_type".*?</select>', body, re.DOTALL)
    assert select_match, "fuel_type select not found"
    select_html = select_match.group(0)
    values = re.findall(r'<option value="([^"]+)"', select_html)
    assert set(values) == {"Biodiesel", "Premium", "Unleaded"}


def test_fuel_type_field_has_a_label(client):
    body = _booking_form(client)
    assert '<label for="fuel_type">' in body


def test_three_collapsible_fuel_tables_render(client):
    body = _booking_form(client)
    groups = re.findall(r'<details class="fuel-table-group" data-fuel-type="([^"]+)"', body)
    assert groups == ["Biodiesel", "Premium", "Unleaded"]


def test_station_table_embed_keyed_by_fuel_type(client, monkeypatch):
    monkeypatch.setattr(
        main.price_store, "list_stations",
        lambda fuel_type: [{"id": "s1", "name": "Cleanfuel", "price_php_per_liter": 60.0, "updated_at": 0}]
        if fuel_type == "Biodiesel" else []
    )
    body = _booking_form(client)
    m = re.search(r"window\.__STATION_TABLE__ = (\{.*?\});", body, re.DOTALL)
    assert m, "window.__STATION_TABLE__ not found"
    data = json.loads(m.group(1))
    assert set(data.keys()) == {"Biodiesel", "Premium", "Unleaded"}
    assert data["Biodiesel"][0]["name"] == "Cleanfuel"
    assert data["Premium"] == []


def test_no_separate_fuel_type_field_in_new_driver_block(client):
    body = _booking_form(client)
    new_driver_start = body.find('id="new_driver_fields"')
    new_driver_end = body.find('</div>', body.find('vehicle_plate', new_driver_start))
    new_driver_html = body[new_driver_start:new_driver_end]
    assert 'name="fuel_type"' not in new_driver_html
    assert 'value="Diesel"' not in body
