"""
tests/test_book_fuel_type_data.py — /book's per-fuel-type data prep and
independent fuel_type field (T4, ARCH-fuel-types-expansion).

The template (templates/book.html) is untouched until T5, so these tests
verify the *data* main.py passes into it via Flask's template_rendered
signal, rather than string-matching rendered HTML.
"""

from contextlib import contextmanager

import pytest
from flask import template_rendered

import data_paths
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
    def __init__(self):
        self.booked = []

    def get_customer(self, code):
        return dict(CUST)

    def customer_exists(self, code):
        return True

    def create_unverified_booking(self, row):
        self.booked.append(dict(row))
        return {"voucher_id": "UF-TEST-00001", **row}


@contextmanager
def captured_templates(app):
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template, context))

    template_rendered.connect(record, app)
    try:
        yield recorded
    finally:
        template_rendered.disconnect(record, app)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub())
    main.app.config.update(TESTING=True)
    return main.app.test_client()


STATIONS_BY_FUEL = {
    "Biodiesel": [{"id": "s1", "name": "Cleanfuel", "price_php_per_liter": 60.0, "updated_at": 0}],
    "Premium": [{"id": "s1", "name": "Cleanfuel", "price_php_per_liter": 65.0, "updated_at": 0}],
    "Unleaded": [],  # not priced for Unleaded at all
}


def _stub_price_store(monkeypatch):
    monkeypatch.setattr(
        main.price_store, "list_stations",
        lambda fuel_type: [dict(s) for s in STATIONS_BY_FUEL.get(fuel_type, [])]
    )
    monkeypatch.setattr(main.discount_store, "get_all", lambda fuel_type: {})


# ============================================================
# Per-fuel-type data prep
# ============================================================

def test_station_table_by_fuel_contains_only_correctly_priced_stations(client, monkeypatch):
    _stub_price_store(monkeypatch)

    with captured_templates(main.app) as templates:
        resp = client.get("/book")

    assert resp.status_code == 200
    _, context = templates[0]
    by_fuel = context["station_table_by_fuel"]
    assert [s["name"] for s in by_fuel["Biodiesel"]] == ["Cleanfuel"]
    assert [s["name"] for s in by_fuel["Premium"]] == ["Cleanfuel"]
    assert by_fuel["Unleaded"] == []


def test_all_three_fuel_types_present_as_keys(client, monkeypatch):
    _stub_price_store(monkeypatch)

    with captured_templates(main.app) as templates:
        client.get("/book")

    _, context = templates[0]
    assert set(context["station_table_by_fuel"].keys()) == {"Biodiesel", "Premium", "Unleaded"}


# ============================================================
# Legacy preset blanking
# ============================================================

def test_legacy_diesel_preset_fuel_type_blanked_for_template(client, monkeypatch, tmp_path):
    _stub_price_store(monkeypatch)
    monkeypatch.setattr(data_paths, "PRESETS_DIR", tmp_path)
    preset_path = data_paths.preset_csv_path("HARR")
    preset_path.write_text(
        "driver_name,vehicle_plate,truck_make,truck_model,number_of_wheels,fuel_type\n"
        "Dave,XYZ-123,Isuzu,NQR,6,Diesel\n"
    )

    with captured_templates(main.app) as templates:
        resp = client.post("/book", data={"account_code": "HARR"})

    assert resp.status_code == 200
    _, context = templates[0]
    assert context["presets"][0]["fuel_type"] == ""


def test_non_legacy_preset_fuel_type_preserved(client, monkeypatch, tmp_path):
    _stub_price_store(monkeypatch)
    monkeypatch.setattr(data_paths, "PRESETS_DIR", tmp_path)
    preset_path = data_paths.preset_csv_path("HARR")
    preset_path.write_text(
        "driver_name,vehicle_plate,truck_make,truck_model,number_of_wheels,fuel_type\n"
        "Dave,XYZ-123,Isuzu,NQR,6,Unleaded\n"
    )

    with captured_templates(main.app) as templates:
        client.post("/book", data={"account_code": "HARR"})

    _, context = templates[0]
    assert context["presets"][0]["fuel_type"] == "Unleaded"


# ============================================================
# Independent fuel_type field (data flow)
# ============================================================

def test_submitted_fuel_type_independent_of_preset_driver_data(client, monkeypatch, tmp_path):
    """Booking with driver_mode=preset carries no fuel_type via
    driver_select's parts[5] into driver_data — the booking's fuel_type
    comes solely from the top-level form field."""
    _stub_price_store(monkeypatch)
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    manila = ZoneInfo("Asia/Manila")
    refuel = (datetime.now(manila) + timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M")

    stub = main.repo
    resp = client.post("/book", data={
        "account_code": "HARR",
        "station": "Cleanfuel",
        "requested_amount_php": "1000",
        "refuel_datetime": refuel,
        "driver_mode": "preset",
        "driver_select": "Dave|XYZ-123|Isuzu|NQR|6|Unleaded",  # preset default: Unleaded
        "fuel_type": "Premium",  # overridden at booking time
        "contact_number": "Harry – 0900-000-0000",
    })

    assert resp.status_code == 200
    assert len(stub.booked) == 1
    assert stub.booked[0]["fuel_type"] == "Premium"


# ============================================================
# Edge case: zero presets
# ============================================================

def test_customer_with_zero_presets_gets_empty_presets_list(client, monkeypatch, tmp_path):
    _stub_price_store(monkeypatch)
    monkeypatch.setattr(data_paths, "PRESETS_DIR", tmp_path)  # no preset file written

    with captured_templates(main.app) as templates:
        client.post("/book", data={"account_code": "HARR"})

    _, context = templates[0]
    assert context["presets"] == []
