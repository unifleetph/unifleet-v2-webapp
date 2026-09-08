"""
tests/test_redeem_excludes_deleted.py — a soft-deleted order cannot be
redeemed via /redeem/<voucher_id> (code-review fix, ARCH-delete-order-button).
"""

import pytest

import main


class FakeRepo:
    def __init__(self, vouchers=None):
        self._vouchers = {v["voucher_id"]: dict(v) for v in (vouchers or [])}
        self.set_status_calls = []

    def get_voucher(self, voucher_id):
        v = self._vouchers.get(voucher_id)
        return dict(v) if v else None

    def set_status(self, voucher_id, new_status, redemption_timestamp):
        self.set_status_calls.append((voucher_id, new_status, redemption_timestamp))


@pytest.fixture
def client():
    main.app.config.update(TESTING=True)
    return main.app.test_client()


def _deleted_voucher(voucher_id, status="Unredeemed"):
    return {
        "voucher_id": voucher_id, "status": status,
        "deleted_at": "2026-09-02T00:00:00",
    }


def test_get_redeem_page_404s_for_deleted_order(client, monkeypatch):
    monkeypatch.setattr(main, "repo", FakeRepo([_deleted_voucher("UF-1")]))

    resp = client.get("/redeem/UF-1")

    assert resp.status_code == 404


def test_post_redeem_404s_for_deleted_order(client, monkeypatch):
    repo = FakeRepo([_deleted_voucher("UF-2")])
    monkeypatch.setattr(main, "repo", repo)

    resp = client.post("/redeem/UF-2")

    assert resp.status_code == 404
    assert repo.set_status_calls == []


def test_get_redeem_page_still_works_for_non_deleted_order(client, monkeypatch):
    monkeypatch.setattr(main, "repo", FakeRepo([
        {"voucher_id": "UF-3", "status": "Unredeemed"},
    ]))

    resp = client.get("/redeem/UF-3")

    assert resp.status_code == 200


# ============================================================
# /ops/voucher/<id>/status/<status> (Verify action)
# ============================================================

def test_ops_set_status_404s_for_deleted_order(client, monkeypatch):
    monkeypatch.setattr(main, "OPS_TOKEN", "")
    repo = FakeRepo([_deleted_voucher("UF-4", status="Unverified")])
    monkeypatch.setattr(main, "repo", repo)

    resp = client.get("/ops/voucher/UF-4/status/Unredeemed")

    assert resp.status_code == 404
    assert repo.set_status_calls == []


def test_ops_set_status_still_works_for_non_deleted_order(client, monkeypatch):
    monkeypatch.setattr(main, "OPS_TOKEN", "")
    monkeypatch.setattr(main, "repo", FakeRepo([
        {"voucher_id": "UF-5", "status": "Unverified"},
    ]))

    resp = client.get("/ops/voucher/UF-5/status/Redeemed")

    assert resp.status_code != 404
