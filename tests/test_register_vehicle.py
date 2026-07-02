"""
tests/test_register_vehicle.py — /register-vehicle page (T4, ARCH-brief-changes).

Stubs main.repo.customer_exists (no Postgres) and redirects PRESETS_DIR to
a temp dir. Validates the standalone vehicle-registration flow: form render,
account validation against the customer store, required-field checks, preset
write, and dedup-by-plate parity with the booking-time preset write.
"""

import os

import pandas as pd
import pytest

import data_paths
import main


VEHICLE_FORM = {
    "account_code": "HARR",
    "driver_name": "Dave",
    "vehicle_plate": "XYZ-123",
    "truck_make": "Isuzu",
    "truck_model": "NQR",
    "number_of_wheels": "6",
}


class RepoStub:
    def __init__(self, exists=True):
        self._exists = exists
        self.exists_calls = []

    def customer_exists(self, code):
        self.exists_calls.append(code)
        return self._exists


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(data_paths, "PRESETS_DIR", tmp_path)
    main.app.config.update(TESTING=True)
    return tmp_path


@pytest.fixture
def client(env):
    return main.app.test_client()


def _preset_path(env, code="HARR"):
    return env / f"{code}_presets.csv"


# ============================================================
# Form
# ============================================================

def test_form_renders(client, monkeypatch):
    """GET /register-vehicle renders the driver/vehicle form."""
    monkeypatch.setattr(main, "repo", RepoStub())
    resp = client.get("/register-vehicle")
    assert resp.status_code == 200
    for field in [b"account_code", b"driver_name", b"vehicle_plate",
                  b"truck_make", b"truck_model", b"number_of_wheels"]:
        assert field in resp.data


# ============================================================
# Save
# ============================================================

def test_saves_preset_for_valid_account(client, env, monkeypatch):
    """Valid account -> preset row with all 6 fields written."""
    monkeypatch.setattr(main, "repo", RepoStub(exists=True))
    client.post("/register-vehicle", data=dict(VEHICLE_FORM))

    path = _preset_path(env)
    assert os.path.isfile(path)
    df = pd.read_csv(path, encoding="utf-8-sig").fillna("")
    assert len(df) == 1
    row = df.iloc[0]
    for col in ["driver_name", "vehicle_plate", "truck_make", "truck_model",
                "number_of_wheels", "fuel_type"]:
        assert col in df.columns
    assert str(row["driver_name"]) == "Dave"
    assert str(row["vehicle_plate"]) == "XYZ-123"
    assert str(row["fuel_type"]) == "Diesel"


# ============================================================
# Validation & edge cases
# ============================================================

def test_rejects_unknown_account_code(client, env, monkeypatch):
    """Unknown account_code -> flash, no preset written."""
    monkeypatch.setattr(main, "repo", RepoStub(exists=False))
    resp = client.post("/register-vehicle", data=dict(VEHICLE_FORM))
    assert resp.status_code in (200, 302)
    assert not os.path.isfile(_preset_path(env))


def test_rejects_missing_required_fields(client, env, monkeypatch):
    """Valid account but blank driver/plate -> flash, no preset written."""
    monkeypatch.setattr(main, "repo", RepoStub(exists=True))
    bad = {**VEHICLE_FORM, "driver_name": "", "vehicle_plate": ""}
    resp = client.post("/register-vehicle", data=bad)
    assert resp.status_code in (200, 302)
    assert not os.path.isfile(_preset_path(env))


# ============================================================
# Regression — dedup by plate (parity with main.py:752-761)
# ============================================================

def test_dedup_by_plate(client, env, monkeypatch):
    """Registering the same plate twice yields a single preset row."""
    monkeypatch.setattr(main, "repo", RepoStub(exists=True))
    client.post("/register-vehicle", data=dict(VEHICLE_FORM))
    client.post("/register-vehicle", data=dict(VEHICLE_FORM))

    df = pd.read_csv(_preset_path(env), encoding="utf-8-sig").fillna("")
    plate_rows = df["vehicle_plate"].astype(str).str.strip().str.upper() == "XYZ-123"
    assert plate_rows.sum() == 1
