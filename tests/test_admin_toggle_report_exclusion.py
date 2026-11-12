"""
tests/test_admin_toggle_report_exclusion.py — admin "Exclude/Include from
supplier report" toggle route (T1, REQ-supplier-report-exclusion-flag).
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


def _toggle(client, voucher_id):
    return client.post(f"/admin/orders/{voucher_id}/toggle-report-exclusion")


def test_toggling_a_redeemed_order_sets_the_flag(client, fake_repo):
    _login(client)
    fake_repo.add({"voucher_id": "UF-1", "status": "Redeemed",
                    "excluded_from_supplier_report": False})

    resp = _toggle(client, "UF-1")

    assert resp.status_code == 302
    assert len(fake_repo.update_calls) == 1
    updated_id, fields = fake_repo.update_calls[0]
    assert updated_id == "UF-1"
    assert fields.get("excluded_from_supplier_report") is True


def test_toggling_again_reverses_it(client, fake_repo):
    _login(client)
    fake_repo.add({"voucher_id": "UF-2", "status": "Redeemed",
                    "excluded_from_supplier_report": True})

    resp = _toggle(client, "UF-2")

    assert resp.status_code == 302
    updated_id, fields = fake_repo.update_calls[0]
    assert updated_id == "UF-2"
    assert fields.get("excluded_from_supplier_report") is False


def test_toggling_a_non_redeemed_order_is_rejected(client, fake_repo):
    _login(client)
    fake_repo.add({"voucher_id": "UF-3", "status": "Unredeemed",
                    "excluded_from_supplier_report": False})

    resp = _toggle(client, "UF-3")

    assert resp.status_code == 302
    assert fake_repo.update_calls == []


def test_toggling_a_missing_voucher(client, fake_repo):
    _login(client)

    resp = _toggle(client, "UF-DOES-NOT-EXIST")

    assert resp.status_code == 302
    assert fake_repo.update_calls == []


def test_unauthenticated_toggle_is_rejected(client, fake_repo):
    fake_repo.add({"voucher_id": "UF-4", "status": "Redeemed",
                    "excluded_from_supplier_report": False})

    resp = _toggle(client, "UF-4")

    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]
    assert fake_repo.update_calls == []
