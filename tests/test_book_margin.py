"""
tests/test_book_margin.py — /book margin application (T3, REQ-profit-margin).

Covers R4/R5/R6 (customer only ever sees post-margin discount, except
for grandfathered/margin_exempt rows) and R7/R8 (the margin % live at
the single POST /book moment — this app has no separate checkout-start
vs. confirm step — is frozen onto the voucher and never re-derived).
"""

import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import data_paths
import main
from margin_store import MarginStore


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
    def __init__(self, customer=None):
        self._customer = customer
        self.booked = []

    def get_customer(self, code):
        return self._customer

    def customer_exists(self, code):
        return self._customer is not None

    def create_unverified_booking(self, row):
        self.booked.append(dict(row))
        return {"voucher_id": "UF-TEST-00001", **row}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(data_paths, "CUSTOMERS_CSV", tmp_path / "customers.csv")
    monkeypatch.setattr(data_paths, "PRESETS_DIR", tmp_path)
    main.app.config.update(TESTING=True)
    return tmp_path


@pytest.fixture
def client(env):
    return main.app.test_client()


def _valid_refuel():
    manila = ZoneInfo("Asia/Manila")
    return (datetime.now(manila) + timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M")


def _station_table_by_fuel(resp_data):
    m = re.search(r"window\.__STATION_TABLE__ = (\{.*?\});", resp_data.decode("utf-8"), re.DOTALL)
    assert m, "window.__STATION_TABLE__ not found"
    return json.loads(m.group(1))


def _stub_station(monkeypatch, margin_pct, exempt, raw_discount=10.0, name="EcoOil - Cainta", price=76.03):
    monkeypatch.setattr(main, "repo", RepoStub(customer=dict(CUST)))
    monkeypatch.setattr(
        main.price_store, "list_stations",
        lambda fuel_type: [{"id": "ecooil-cainta", "name": name, "price_php_per_liter": price, "updated_at": 0}]
        if fuel_type == "Biodiesel" else []
    )
    monkeypatch.setattr(main.margin_store, "get", lambda: margin_pct)
    monkeypatch.setattr(
        main.discount_store, "get_all_with_exempt",
        lambda fuel_type: {name: {"value": raw_discount, "margin_exempt": exempt}} if fuel_type == "Biodiesel" else {}
    )
    monkeypatch.setattr(
        main.discount_store, "get_with_exempt",
        lambda station, fuel_type: {"value": raw_discount, "margin_exempt": exempt} if fuel_type == "Biodiesel" else None
    )


# ============================================================
# Booking Display (GET /book)
# ============================================================

def test_get_book_shows_post_margin_discount_for_non_exempt_station(client, monkeypatch):
    _stub_station(monkeypatch, margin_pct=12.25, exempt=False, raw_discount=10.0)
    resp = client.post("/book", data={"account_code": "HARR"})
    assert resp.status_code == 200
    table = _station_table_by_fuel(resp.data)["Biodiesel"]
    row = next(r for r in table if r["name"] == "EcoOil - Cainta")
    # station_table_by_fuel formats for display (f"{value:.2f}"), so the
    # exact 8.775 renders as "8.78" — the underlying math is covered
    # precisely by the POST-snapshot test below and by margin_store's
    # own unit tests.
    assert float(row["discount_per_liter"]) == pytest.approx(8.78, abs=0.001)


def test_get_book_shows_raw_discount_for_exempt_station(client, monkeypatch):
    _stub_station(monkeypatch, margin_pct=12.25, exempt=True, raw_discount=10.0)
    resp = client.post("/book", data={"account_code": "HARR"})
    assert resp.status_code == 200
    table = _station_table_by_fuel(resp.data)["Biodiesel"]
    row = next(r for r in table if r["name"] == "EcoOil - Cainta")
    assert float(row["discount_per_liter"]) == pytest.approx(10.0, abs=0.001)


def test_get_book_zero_margin_is_noop_for_non_exempt_station(client, monkeypatch):
    _stub_station(monkeypatch, margin_pct=0, exempt=False, raw_discount=10.0)
    resp = client.post("/book", data={"account_code": "HARR"})
    assert resp.status_code == 200
    table = _station_table_by_fuel(resp.data)["Biodiesel"]
    row = next(r for r in table if r["name"] == "EcoOil - Cainta")
    assert float(row["discount_per_liter"]) == pytest.approx(10.0, abs=0.001)


def test_book_request_calls_margin_store_get_exactly_once(client, monkeypatch):
    """Code-review finding: the old code called margin_store.get() once
    for the flat "Biodiesel" table plus once per FUEL_TYPES entry inside
    _margin_adjusted_discounts — 1 + len(FUEL_TYPES) round-trips for one
    global scalar. A single request must now fetch it exactly once."""
    _stub_station(monkeypatch, margin_pct=12.25, exempt=False, raw_discount=10.0)
    calls = []

    def counting_get():
        calls.append(1)
        return 12.25

    monkeypatch.setattr(main.margin_store, "get", counting_get)
    resp = client.post("/book", data={"account_code": "HARR"})
    assert resp.status_code == 200
    assert len(calls) == 1


# ============================================================
# _margin_adjusted_discounts contract (R2, code-review finding)
# ============================================================

def test_margin_adjusted_discounts_uses_explicit_margin_pct_without_fetching(monkeypatch):
    """Passing margin_pct in directly must use it, and must NOT call
    margin_store.get() internally."""
    monkeypatch.setattr(
        main.discount_store, "get_all_with_exempt",
        lambda fuel_type: {"Test Station": {"value": 10.0, "margin_exempt": False}}
    )
    calls = []
    monkeypatch.setattr(main.margin_store, "get", lambda: calls.append(1) or 999.0)

    result = main._margin_adjusted_discounts("Biodiesel", margin_pct=12.25)

    assert calls == []
    assert result["Test Station"] == pytest.approx(8.775, abs=0.0001)


def test_margin_adjusted_discounts_without_margin_pct_still_fetches_internally(monkeypatch):
    """Back-compat: a caller that omits margin_pct (e.g. /api/v1/discounts,
    main.py's _resolve_fuel_type_param call site) must keep working exactly
    as before this change — fetching the margin itself."""
    monkeypatch.setattr(
        main.discount_store, "get_all_with_exempt",
        lambda fuel_type: {"Test Station": {"value": 10.0, "margin_exempt": False}}
    )
    calls = []

    def counting_get():
        calls.append(1)
        return 12.25

    monkeypatch.setattr(main.margin_store, "get", counting_get)

    result = main._margin_adjusted_discounts("Biodiesel")

    assert len(calls) == 1
    assert result["Test Station"] == pytest.approx(8.775, abs=0.0001)


# ============================================================
# Booking Snapshot (POST /book)
# ============================================================

def _post_booking(client, monkeypatch, margin_pct, exempt, raw_discount=10.0):
    _stub_station(monkeypatch, margin_pct=margin_pct, exempt=exempt, raw_discount=raw_discount)
    resp = client.post("/book", data={
        "account_code": "HARR",
        "station": "EcoOil - Cainta",
        "requested_amount_php": "1000",
        "refuel_datetime": _valid_refuel(),
        "driver_mode": "new",
        "driver_name": "Dave",
        "vehicle_plate": "XYZ-123",
        "truck_make": "Isuzu",
        "truck_model": "NQR",
        "number_of_wheels": "6",
        "fuel_type": "Biodiesel",
        "contact_number": "Harry – 0900-000-0000",
        "mobile_number": "09123456789",
    })
    assert resp.status_code == 200
    assert len(main.repo.booked) == 1
    return main.repo.booked[0]


def test_post_book_snapshot_stores_post_margin_discount_for_non_exempt_station(client, monkeypatch):
    booked = _post_booking(client, monkeypatch, margin_pct=12.25, exempt=False, raw_discount=10.0)
    assert booked["discount_snapshot_php_per_liter"] == pytest.approx(8.775, abs=0.001)


def test_post_book_snapshot_stores_raw_discount_for_exempt_station(client, monkeypatch):
    booked = _post_booking(client, monkeypatch, margin_pct=12.25, exempt=True, raw_discount=10.0)
    assert booked["discount_snapshot_php_per_liter"] == pytest.approx(10.0, abs=0.001)


def test_post_book_stamps_margin_pct_at_booking_regardless_of_exempt_status(client, monkeypatch):
    """Developer decision (A6): the live global margin is recorded on
    every booking, even a grandfathered/exempt one whose discount wasn't
    actually reduced — the field means 'policy at the time', not 'margin
    applied to this row'."""
    booked = _post_booking(client, monkeypatch, margin_pct=12.25, exempt=True, raw_discount=10.0)
    assert booked["margin_pct_at_booking"] == pytest.approx(12.25, abs=0.001)
    # Confirms the exempt row's discount itself was NOT reduced, even
    # though margin_pct_at_booking was still recorded.
    assert booked["discount_snapshot_php_per_liter"] == pytest.approx(10.0, abs=0.001)


class _ApproveFlowRepoStub:
    """Minimal repo stub for driving ops_set_status's real Approve flow
    (main.py:938), distinct from RepoStub above which only models the
    POST /book snapshot step."""

    def __init__(self, voucher):
        self._voucher = voucher
        self.updated_fields = None

    def get_voucher(self, voucher_id):
        if self.updated_fields:
            merged = dict(self._voucher)
            merged.update(self.updated_fields)
            return merged
        return self._voucher

    def update_voucher_fields(self, voucher_id, fields):
        self.updated_fields = fields

    def set_status(self, voucher_id, status, ts):
        pass


def test_margin_changed_after_booking_does_not_retroactively_change_approved_result(client, monkeypatch):
    """Replaces a tautological test that re-read an untouched in-memory
    dict and could not fail even if the real guarantee broke. This test
    drives the actual non-zero-snap_disc branch of ops_set_status
    (main.py:998) -- the one place the "never re-derive" guarantee
    actually lives -- after the global margin has changed."""
    # Booking-time margin A = 12.25 already applied to raw 10.0, frozen
    # into the snapshot the way POST /book's own snapshot logic does.
    frozen_dpl = MarginStore.apply(10.0, 12.25, exempt=False)
    voucher = {
        "voucher_id": "UF-TEST-RETRO-1",
        "station": "EcoOil - Cainta",
        "requested_amount_php": 1000.0,
        "requested_total_php": 1000.0,
        "price_snapshot_php_per_liter": 76.03,
        "price_snapshot_updated_at": 0,
        "discount_snapshot_php_per_liter": frozen_dpl,
        "discount_snapshot_captured_at": 0,
        "margin_pct_at_booking": 12.25,
        "status": "Unverified",
    }
    # Sanity: the scenario must exercise the non-zero-snapshot branch,
    # not the zero-snapshot live-fallback one covered by
    # test_admin_approve_margin.py.
    assert voucher["discount_snapshot_php_per_liter"] != 0

    stub = _ApproveFlowRepoStub(voucher)
    monkeypatch.setattr(main, "repo", stub)
    monkeypatch.setattr(main, "generate_assets_for_row", lambda row: None)

    # Simulate the admin raising the global margin to B after booking.
    monkeypatch.setattr(main.margin_store, "get", lambda: 20.0)
    # If the non-zero-snapshot branch ever started re-deriving from the
    # live discount instead of trusting the snapshot, this stub would
    # make that visible: a different raw value than what produced A.
    monkeypatch.setattr(
        main.discount_store, "get_with_exempt",
        lambda station, fuel_type: {"value": 10.0, "margin_exempt": False}
    )

    resp = client.get(f"/ops/voucher/{voucher['voucher_id']}/status/Unredeemed")

    assert resp.status_code == 302
    fields = stub.updated_fields
    assert fields is not None
    # Reflects margin A (frozen at booking), never margin B.
    assert fields["discount_per_liter"] == pytest.approx(frozen_dpl, abs=0.0001)
    liters_requested = round(1000.0 / 76.03, 2)
    expected_discount_total = round(liters_requested * frozen_dpl, 2)
    assert fields["discount_total_php"] == pytest.approx(expected_discount_total, abs=0.01)
    # Explicitly rule out a result derived from margin B instead of A.
    wrong_dpl_from_b = MarginStore.apply(10.0, 20.0, exempt=False)
    assert fields["discount_per_liter"] != pytest.approx(wrong_dpl_from_b, abs=0.0001)


def test_post_book_degrades_gracefully_when_margin_lookup_fails(client, monkeypatch):
    """Code-review finding: margin_store.get() must fail the same way
    every sibling lookup in this function does — degrade to a safe
    default and keep the booking working, never 500 the whole request
    (matches the file's own "never blocks the booking" invariant)."""
    _stub_station(monkeypatch, margin_pct=12.25, exempt=False, raw_discount=10.0)

    def _raise():
        raise RuntimeError("pool exhausted")

    monkeypatch.setattr(main.margin_store, "get", _raise)

    resp = client.post("/book", data={
        "account_code": "HARR",
        "station": "EcoOil - Cainta",
        "requested_amount_php": "1000",
        "refuel_datetime": _valid_refuel(),
        "driver_mode": "new",
        "driver_name": "Dave",
        "vehicle_plate": "XYZ-123",
        "truck_make": "Isuzu",
        "truck_model": "NQR",
        "number_of_wheels": "6",
        "fuel_type": "Biodiesel",
        "contact_number": "Harry – 0900-000-0000",
        "mobile_number": "09123456789",
    })

    assert resp.status_code == 200
    assert len(main.repo.booked) == 1
    booked = main.repo.booked[0]
    assert booked["margin_pct_at_booking"] == 0.0
    assert booked["discount_snapshot_php_per_liter"] == 0.0
