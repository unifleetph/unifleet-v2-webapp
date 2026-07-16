"""
tests/test_register_optional_labels.py — /register's 3 trailing fields
relabeled "(Optional)" (T1, ARCH-register-optional-fields).
"""

import pytest

import data_paths
import main


FORM = {
    "company_name": "Harrods",
    "contact_name": "Harry",
    "contact_number": "0900-000-0000",
    "email": "harry@example.com",
    "fleet_size": "12",
    "areas": "QC",
    "acknowledge": "on",
}


class RepoStub:
    def customer_exists(self, code):
        return False

    def create_customer(self, data):
        return dict(data)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(data_paths, "CUSTOMERS_CSV", tmp_path / "customers.csv")
    monkeypatch.setattr(main, "repo", RepoStub())
    main.app.config.update(TESTING=True)
    return main.app.test_client()


def test_company_name_label_reads_optional(client):
    r = client.get("/register")
    assert b"Company Name (Optional)" in r.data
    assert b"Company Name (If applicable)" not in r.data


def test_fleet_size_label_reads_optional(client):
    r = client.get("/register")
    assert b"Total Number of Vehicles in Fleet (Optional)" in r.data
    assert b"Total Number of Vehicles in Fleet (If applicable)" not in r.data


def test_areas_label_reads_optional(client):
    r = client.get("/register")
    assert b"Preferred Areas for Driver Re-Fueling (Optional)" in r.data
    assert b"(Select all that apply)" not in r.data


def test_register_still_succeeds_with_optional_fields_blank(client):
    form = dict(FORM)
    form["company_name"] = ""
    form["fleet_size"] = ""
    form["areas"] = ""

    r = client.post("/register", data=form)

    assert r.status_code == 302
    assert "/register/success" in r.headers["Location"]
