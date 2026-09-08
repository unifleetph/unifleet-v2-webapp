"""
tests/test_supplier_exports_exclude_deleted.py — supplier-facing export
routes exclude soft-deleted orders (T3, ARCH-delete-order-button).
M-risk area per ARCH: external-facing documents.
"""

import pytest

import main


class RepoStub:
    def __init__(self, vouchers):
        self._vouchers = vouchers

    def list_all_vouchers(self):
        return list(self._vouchers)


def _voucher(voucher_id, status="Unredeemed", deleted=False, **extra):
    row = {c: "" for c in main.VOUCHER_COLUMNS}
    row.update({
        "voucher_id": voucher_id,
        "status": status,
        "station": "Cleanfuel",
    })
    if deleted:
        row["deleted_at"] = "2026-09-02T00:00:00"
    row.update(extra)
    return row


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "s3cret")
    main.app.config.update(TESTING=True)
    return main.app.test_client()


def _login(client):
    client.post("/admin/login", data={"password": "s3cret"})


# ============================================================
# Supplier PDF (supplier_sheet_pdf)
# ============================================================

def test_supplier_pdf_excludes_deleted_orders(client, monkeypatch):
    captured = {}

    def fake_build_supplier_pdf(vouchers, target_station_ids, stations, logo_path):
        captured["vouchers"] = vouchers
        return b"%PDF-fake"

    monkeypatch.setattr(main, "build_supplier_pdf", fake_build_supplier_pdf)
    monkeypatch.setattr(main, "repo", RepoStub([
        _voucher("UF-VISIBLE", status="Unredeemed"),
        _voucher("UF-DELETED", status="Unredeemed", deleted=True),
    ]))
    monkeypatch.setattr(main.price_store, "list_stations", lambda *a, **kw: [])

    r = client.get("/supplier-sheet.pdf")

    assert r.status_code == 200
    ids = {v["voucher_id"] for v in captured["vouchers"]}
    assert ids == {"UF-VISIBLE"}


# ============================================================
# Supplier CSV (export_supplier_csv)
# ============================================================

def test_supplier_csv_excludes_deleted_orders(client, monkeypatch):
    monkeypatch.setattr(main, "repo", RepoStub([
        _voucher("UF-VISIBLE-2", status="Unredeemed"),
        _voucher("UF-DELETED-2", status="Unredeemed", deleted=True),
    ]))

    r = client.get("/export_supplier_csv")

    assert r.status_code == 200
    body = r.data.decode("utf-8")
    assert "UF-VISIBLE-2" in body
    assert "UF-DELETED-2" not in body
