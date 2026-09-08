"""
tests/test_admin_delete_order.py — admin "Delete Order" soft-delete route
(T2, ARCH-delete-order-button).
"""

import pytest

import main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "s3cret")
    monkeypatch.setattr(main, "ADMIN_KEY", "testkey")
    main.app.config.update(TESTING=True)
    return main.app.test_client()


def _login(client):
    client.post("/admin/login", data={"password": "s3cret"})


class FakeRepo:
    def __init__(self, vouchers=None):
        self._vouchers = {v["voucher_id"]: dict(v) for v in (vouchers or [])}
        self.update_calls = []

    def add(self, voucher):
        self._vouchers[voucher["voucher_id"]] = dict(voucher)

    def get_voucher(self, voucher_id):
        v = self._vouchers.get(voucher_id)
        return dict(v) if v else None

    def update_voucher_fields(self, voucher_id, fields):
        if voucher_id not in self._vouchers:
            raise KeyError(f"voucher not found: {voucher_id}")
        self.update_calls.append((voucher_id, dict(fields)))
        self._vouchers[voucher_id].update(fields)


@pytest.fixture
def fake_repo(monkeypatch):
    repo = FakeRepo()
    monkeypatch.setattr(main, "repo", repo)
    return repo


@pytest.fixture
def audit_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "append_audit", lambda *a, **kw: calls.append((a, kw)))
    return calls


# ============================================================
# Status gate
# ============================================================

def test_post_delete_order_blocks_redeemed(client, fake_repo, audit_calls):
    _login(client)
    fake_repo.add({"voucher_id": "UF-1", "status": "Redeemed"})

    resp = client.post("/admin/orders/UF-1/delete")

    assert resp.status_code == 302
    assert fake_repo.update_calls == []
    assert audit_calls == []


# ============================================================
# Soft-delete happy path
# ============================================================

def test_post_delete_order_soft_deletes_non_redeemed(client, fake_repo, audit_calls):
    _login(client)
    fake_repo.add({"voucher_id": "UF-2", "status": "Unredeemed"})

    resp = client.post("/admin/orders/UF-2/delete")

    assert resp.status_code == 302
    assert len(fake_repo.update_calls) == 1
    updated_id, fields = fake_repo.update_calls[0]
    assert updated_id == "UF-2"
    assert fields.get("deleted_at") is not None


def test_post_delete_order_removes_png_files(client, fake_repo, audit_calls, monkeypatch):
    _login(client)
    fake_repo.add({"voucher_id": "UF-3", "status": "Unverified"})

    removed = []
    monkeypatch.setattr(main.os.path, "exists", lambda p: True)
    monkeypatch.setattr(main.os, "remove", lambda p: removed.append(p))

    client.post("/admin/orders/UF-3/delete")

    assert len(removed) == 2


# ============================================================
# Regression guard — delete_png() unaffected by helper extraction
# ============================================================

def test_delete_png_still_removes_both_files(client, monkeypatch):
    removed = []
    monkeypatch.setattr(main.os.path, "exists", lambda p: True)
    monkeypatch.setattr(main.os, "remove", lambda p: removed.append(p))

    resp = client.post("/delete_png/UF-4")

    assert resp.status_code == 302
    assert len(removed) == 2


# ============================================================
# Audit log
# ============================================================

def test_post_delete_order_writes_one_audit_entry(client, fake_repo, audit_calls):
    _login(client)
    fake_repo.add({"voucher_id": "UF-5", "status": "Unredeemed"})

    client.post("/admin/orders/UF-5/delete")

    assert len(audit_calls) == 1
    args, kwargs = audit_calls[0]
    assert args[0] == "delete_order"
    assert args[1] == "UF-5"
    assert kwargs.get("to_status") == "Deleted" or (len(args) > 3 and args[3] == "Deleted")


# ============================================================
# Missing voucher
# ============================================================

def test_post_delete_order_missing_voucher_no_500(client, fake_repo, audit_calls):
    _login(client)

    resp = client.post("/admin/orders/UF-DOES-NOT-EXIST/delete")

    assert resp.status_code == 302
    assert fake_repo.update_calls == []
    assert audit_calls == []


# ============================================================
# Auth
# ============================================================

def test_post_delete_order_requires_admin_auth(client, fake_repo, audit_calls):
    fake_repo.add({"voucher_id": "UF-6", "status": "Unredeemed"})

    resp = client.post("/admin/orders/UF-6/delete")

    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]
    assert fake_repo.update_calls == []
    assert audit_calls == []
