"""
tests/test_admin_approve_margin.py — Approve-flow (ops_set_status) live
discount fallback must apply margin too (T3, REQ-profit-margin).

Regresses the specific gap identified in ARCH-profit-margin: when a
voucher's discount_snapshot_php_per_liter is exactly 0.0 (missing/never
captured), ops_set_status() falls back to a *live* discount_store lookup
— that fallback must run the same margin transform as everywhere else,
or it silently leaks the raw supplier discount for this one edge case.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

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
    def __init__(self, voucher=None):
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


@pytest.fixture
def client(monkeypatch):
    main.app.config.update(TESTING=True)
    monkeypatch.setattr(main, "generate_assets_for_row", lambda row: None)
    return main.app.test_client()


def _voucher(discount_snapshot=0.0):
    return {
        "voucher_id": "UF-TEST-MARGIN-1",
        "station": "EcoOil - Cainta",
        "requested_amount_php": 927.66,
        "requested_total_php": 1000.0,
        "price_snapshot_php_per_liter": 76.03,
        "price_snapshot_updated_at": 0,
        "discount_snapshot_php_per_liter": discount_snapshot,
        "discount_snapshot_captured_at": 0,
        "status": "Unverified",
    }


def test_fallback_applies_margin_when_snapshot_zero_and_station_not_exempt(client, monkeypatch):
    stub = RepoStub(voucher=_voucher(discount_snapshot=0.0))
    monkeypatch.setattr(main, "repo", stub)
    monkeypatch.setattr(main.margin_store, "get", lambda: 12.25)
    monkeypatch.setattr(
        main.discount_store, "get_with_exempt",
        lambda station, fuel_type: {"value": 5.50, "margin_exempt": False}
    )

    resp = client.get("/ops/voucher/UF-TEST-MARGIN-1/status/Unredeemed")

    assert resp.status_code == 302
    fields = stub.updated_fields
    assert fields is not None

    expected_dpl = MarginStore.apply(5.50, 12.25, exempt=False)
    liters_requested = round(1000.0 / 76.03, 2)
    expected_discount_total = round(liters_requested * expected_dpl, 2)

    assert fields["discount_per_liter"] == pytest.approx(expected_dpl, abs=0.0001)
    assert fields["discount_total_php"] == pytest.approx(expected_discount_total, abs=0.01)
    # Regression check: the raw (pre-margin) discount must NOT be what
    # was used — this is exactly the leak the fix closes.
    raw_discount_total = round(liters_requested * 5.50, 2)
    assert fields["discount_total_php"] != pytest.approx(raw_discount_total, abs=0.01)


def test_fallback_returns_raw_for_exempt_station_zero_snapshot(client, monkeypatch):
    stub = RepoStub(voucher=_voucher(discount_snapshot=0.0))
    monkeypatch.setattr(main, "repo", stub)
    monkeypatch.setattr(main.margin_store, "get", lambda: 12.25)
    monkeypatch.setattr(
        main.discount_store, "get_with_exempt",
        lambda station, fuel_type: {"value": 5.50, "margin_exempt": True}
    )

    resp = client.get("/ops/voucher/UF-TEST-MARGIN-1/status/Unredeemed")

    assert resp.status_code == 302
    fields = stub.updated_fields
    assert fields is not None

    liters_requested = round(1000.0 / 76.03, 2)
    expected_discount_total = round(liters_requested * 5.50, 2)

    assert fields["discount_per_liter"] == pytest.approx(5.50, abs=0.0001)
    assert fields["discount_total_php"] == pytest.approx(expected_discount_total, abs=0.01)


# ============================================================
# T7 — confirmed email with the voucher attached
# (ARCH-brief-11-email-notifications)
# ============================================================

def _approve_voucher(**overrides):
    """A voucher row as the approve flow actually sees one — the file's
    pre-existing _voucher() helper predates account_code, which the
    notification path needs to resolve a recipient."""
    row = dict(_voucher(), account_code="HARR")
    row.update(overrides)
    return row


class _ApproveRepo(RepoStub):
    """RepoStub that records set_status calls and serves a customer."""

    def __init__(self, voucher=None, customer=None):
        super().__init__(voucher=voucher)
        self.statuses = []
        self._customer = customer if customer is not None else dict(
            CUST, email="driver@example.com"
        )

    def set_status(self, voucher_id, status, ts):
        self.statuses.append(status)

    def get_customer(self, code):
        return self._customer


@pytest.fixture
def approve_mail(monkeypatch, tmp_path):
    """Capture enqueue/enqueue_skipped and point voucher PNGs at tmp_path."""
    sent, skipped = [], []
    monkeypatch.setattr(
        main.notifications, "enqueue",
        lambda kind, **kw: sent.append({"kind": kind, **kw}) or len(sent),
    )
    monkeypatch.setattr(
        main.notifications, "enqueue_skipped",
        lambda kind, **kw: skipped.append({"kind": kind, **kw}),
    )
    png = tmp_path / "UF-TEST-MARGIN-1_Official.png"
    png.write_bytes(b"\x89PNG voucher one")
    monkeypatch.setattr(
        main.data_paths, "official_qr_png_path", lambda vid: tmp_path / f"{vid}_Official.png"
    )
    return sent, skipped, png


def _approve(client):
    return client.get("/ops/voucher/UF-TEST-MARGIN-1/status/Unredeemed")


def _stub_pricing(monkeypatch):
    monkeypatch.setattr(main.margin_store, "get", lambda: 0.0)
    monkeypatch.setattr(
        main.discount_store, "get_with_exempt",
        lambda station, fuel_type: {"value": 5.50, "margin_exempt": False},
    )


def test_approval_queues_the_confirmed_email_with_the_voucher(client, monkeypatch, approve_mail):
    """GIVEN an Unverified voucher WHEN an admin approves it THEN one
    booking_confirmed notification is queued, referencing the voucher PNG
    (verifies R4)."""
    sent, skipped, _ = approve_mail
    stub = _ApproveRepo(voucher=_approve_voucher())
    monkeypatch.setattr(main, "repo", stub)
    _stub_pricing(monkeypatch)

    resp = _approve(client)

    assert resp.status_code == 302
    assert len(sent) == 1
    assert sent[0]["kind"] == "booking_confirmed"
    assert sent[0]["recipient"] == "driver@example.com"
    assert sent[0]["subject"] == "Booking Confirmed - UniFleet"
    assert sent[0]["attachment_ref"] == "voucher_png:UF-TEST-MARGIN-1"
    assert sent[0]["voucher_id"] == "UF-TEST-MARGIN-1"


def test_the_email_is_queued_after_the_status_flip(client, monkeypatch, approve_mail):
    """A11/A6: the enqueue must follow set_status('Unredeemed'), so a mail
    problem can never leave an approved voucher unapproved."""
    sent, _, _ = approve_mail
    stub = _ApproveRepo(voucher=_approve_voucher())
    monkeypatch.setattr(main, "repo", stub)
    _stub_pricing(monkeypatch)
    order = []

    original_set_status = stub.set_status

    def tracking_set_status(voucher_id, status, ts):
        order.append(f"status:{status}")
        return original_set_status(voucher_id, status, ts)

    monkeypatch.setattr(stub, "set_status", tracking_set_status)
    monkeypatch.setattr(
        main.notifications, "enqueue",
        lambda kind, **kw: order.append("enqueue") or sent.append(kw) or 1,
    )

    _approve(client)

    assert order == ["status:Unredeemed", "enqueue"]


def test_an_unchanged_reapproval_sends_nothing_new(client, monkeypatch, approve_mail):
    """GIVEN a voucher confirmed once WHEN it is re-approved with no data
    change THEN the fingerprint is identical, so the dedupe key collides and
    no second email is sent (verifies R10)."""
    sent, _, _ = approve_mail
    stub = _ApproveRepo(voucher=_approve_voucher())
    monkeypatch.setattr(main, "repo", stub)
    _stub_pricing(monkeypatch)

    _approve(client)
    first_key = sent[0]["dedupe_key"]
    _approve(client)
    second_key = sent[1]["dedupe_key"]

    assert first_key == second_key, (
        "an unchanged re-approval must reuse the dedupe key so the DB "
        "constraint suppresses the duplicate"
    )


def test_a_changed_amount_produces_a_new_dedupe_key(client, monkeypatch, approve_mail):
    """GIVEN a confirmed voucher whose amount then changes WHEN it is
    re-approved THEN the fingerprint differs and a fresh email goes out
    (verifies R10)."""
    sent, _, _ = approve_mail
    _stub_pricing(monkeypatch)

    monkeypatch.setattr(main, "repo", _ApproveRepo(voucher=_approve_voucher()))
    _approve(client)

    changed = _approve_voucher(requested_total_php=2000.0)
    monkeypatch.setattr(main, "repo", _ApproveRepo(voucher=changed))
    _approve(client)

    assert sent[0]["dedupe_key"] != sent[1]["dedupe_key"]


def test_a_changed_station_produces_a_new_dedupe_key(client, monkeypatch, approve_mail):
    sent, _, _ = approve_mail
    _stub_pricing(monkeypatch)

    monkeypatch.setattr(main, "repo", _ApproveRepo(voucher=_approve_voucher()))
    _approve(client)

    changed = _approve_voucher(station="Petron - Ortigas")
    monkeypatch.setattr(main, "repo", _ApproveRepo(voucher=changed))
    _approve(client)

    assert sent[0]["dedupe_key"] != sent[1]["dedupe_key"]


def test_a_regenerated_png_produces_a_new_dedupe_key(client, monkeypatch, approve_mail):
    """A8: the fingerprint covers the image bytes, so a voucher regenerated
    with identical numbers still reaches the customer (verifies R10)."""
    sent, _, png = approve_mail
    _stub_pricing(monkeypatch)
    monkeypatch.setattr(main, "repo", _ApproveRepo(voucher=_approve_voucher()))

    _approve(client)
    png.write_bytes(b"\x89PNG regenerated with different pixels")
    _approve(client)

    assert sent[0]["dedupe_key"] != sent[1]["dedupe_key"]


def test_an_emailless_customer_is_flagged_not_sent(client, monkeypatch, approve_mail):
    """A legacy customer with no address gets a skipped row, and the approval
    still completes (verifies R6)."""
    sent, skipped, _ = approve_mail
    stub = _ApproveRepo(voucher=_approve_voucher(), customer=dict(CUST, email=""))
    monkeypatch.setattr(main, "repo", stub)
    _stub_pricing(monkeypatch)

    resp = _approve(client)

    assert resp.status_code == 302
    assert stub.statuses == ["Unredeemed"]
    assert sent == []
    assert len(skipped) == 1


# ------------------------------------------------------------
# Regression guards
# ------------------------------------------------------------

def test_asset_failure_still_aborts_approval_and_sends_nothing(client, monkeypatch, approve_mail):
    """GIVEN generate_assets_for_row raises WHEN approval is attempted THEN it
    500s, the status stays Unverified and nothing is queued — the pre-existing
    abort is untouched (verifies A6; guards main.py's approve flow)."""
    sent, skipped, _ = approve_mail
    stub = _ApproveRepo(voucher=_approve_voucher())
    monkeypatch.setattr(main, "repo", stub)
    _stub_pricing(monkeypatch)

    def boom(row):
        raise RuntimeError("wkhtmltoimage exploded")

    monkeypatch.setattr(main, "generate_assets_for_row", boom)

    resp = _approve(client)

    assert resp.status_code == 500
    assert stub.statuses == []
    assert sent == []
    assert skipped == []


def test_an_enqueue_failure_cannot_leave_the_voucher_unapproved(client, monkeypatch, approve_mail):
    """GIVEN the outbox raises WHEN approval runs THEN the voucher is still
    Unredeemed and the admin still gets the normal redirect (verifies R7)."""
    stub = _ApproveRepo(voucher=_approve_voucher())
    monkeypatch.setattr(main, "repo", stub)
    _stub_pricing(monkeypatch)
    monkeypatch.setattr(
        main.notifications, "enqueue",
        lambda kind, **kw: (_ for _ in ()).throw(RuntimeError("outbox exploded")),
    )

    resp = _approve(client)

    assert resp.status_code == 302
    assert stub.statuses == ["Unredeemed"]


def test_the_redeemed_transition_sends_nothing(client, monkeypatch, approve_mail):
    """Only approval notifies. Scanning the voucher at the station does not
    (verifies the REQ's scope boundary)."""
    sent, skipped, _ = approve_mail
    stub = _ApproveRepo(voucher=_approve_voucher(status="Unredeemed"))
    monkeypatch.setattr(main, "repo", stub)
    _stub_pricing(monkeypatch)

    resp = client.get("/ops/voucher/UF-TEST-MARGIN-1/status/Redeemed")

    assert resp.status_code == 302
    assert sent == []
    assert skipped == []
