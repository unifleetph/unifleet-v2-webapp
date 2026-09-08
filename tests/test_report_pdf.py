"""
tests/test_report_pdf.py — Supplier PDF fuel-type column (T8,
ARCH-fuel-types-expansion). Tests the pure row/name-expansion helpers
directly (no reportlab dependency) plus a smoke test of the real
build_supplier_pdf() call.
"""

from report_pdf import _fuel_type_display, _build_supplier_row, build_supplier_pdf, FAQ_ENTRIES


# ============================================================
# Column content — pure helpers
# ============================================================

def test_biodiesel_unchanged():
    assert _fuel_type_display("Biodiesel") == "Biodiesel"


def test_premium_expands_to_full_name():
    assert _fuel_type_display("Premium") == "Premium Gasoline"


def test_unleaded_expands_to_full_name():
    assert _fuel_type_display("Unleaded") == "Unleaded Gasoline"


def test_none_falls_back_to_diesel():
    assert _fuel_type_display(None) == "Diesel"


def test_blank_string_falls_back_to_diesel():
    assert _fuel_type_display("") == "Diesel"


def test_build_supplier_row_includes_fuel_type_column():
    row = _build_supplier_row({
        "station": "Cleanfuel", "driver_name": "Dave", "vehicle_plate": "XYZ-123",
        "voucher_id": "UF-1", "fuel_type": "Premium", "requested_amount_php": 1000,
    })
    assert row[4] == "Premium Gasoline"


def test_build_supplier_row_legacy_none_shows_diesel():
    row = _build_supplier_row({
        "station": "Cleanfuel", "driver_name": "Dave", "vehicle_plate": "XYZ-123",
        "voucher_id": "UF-1", "fuel_type": None, "requested_amount_php": 1000,
    })
    assert row[4] == "Diesel"


# ============================================================
# PDF-render smoke test
# ============================================================

def test_build_supplier_pdf_smoke_test_all_fuel_types():
    vouchers = [
        {"station": "Cleanfuel", "driver_name": "A", "vehicle_plate": "AAA-1",
         "voucher_id": "UF-1", "fuel_type": "Biodiesel", "requested_amount_php": 1000},
        {"station": "Cleanfuel", "driver_name": "B", "vehicle_plate": "BBB-2",
         "voucher_id": "UF-2", "fuel_type": "Premium", "requested_amount_php": 1000},
        {"station": "Cleanfuel", "driver_name": "C", "vehicle_plate": "CCC-3",
         "voucher_id": "UF-3", "fuel_type": "Unleaded", "requested_amount_php": 1000},
        {"station": "Cleanfuel", "driver_name": "D", "vehicle_plate": "DDD-4",
         "voucher_id": "UF-4", "fuel_type": None, "requested_amount_php": 1000},
    ]
    stations = [{"id": "s1", "name": "Cleanfuel"}]

    pdf_bytes = build_supplier_pdf(vouchers=vouchers, target_station_ids=[], stations=stations)

    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 0
    assert pdf_bytes.startswith(b"%PDF")


# ============================================================
# Regression guard — existing 6 columns unchanged
# ============================================================

def test_existing_columns_retain_values_and_order():
    row = _build_supplier_row({
        "station": "Cleanfuel", "driver_name": "Dave", "vehicle_plate": "XYZ-123",
        "voucher_id": "UF-1", "fuel_type": "Biodiesel", "requested_amount_php": 1000,
    })
    assert row[0] == "Cleanfuel"       # Station
    assert row[1] == "1,000.00"        # Amount
    assert row[2] == "Dave"            # Driver
    assert row[3] == "XYZ-123"         # Plate
    # row[4] is the new Fuel Type column
    assert row[5] == "UF-1"            # Voucher ID
    assert row[6] == ""                # Name/Signature


# ============================================================
# FAQ section — redemption-window entry (REQ-brief-11-faq-redemption-window)
# ============================================================

def test_faq_has_four_entries_redemption_window_appended_last():
    assert len(FAQ_ENTRIES) == 4
    question, answer = FAQ_ENTRIES[3]
    assert question.startswith("Q: How long do I have to redeem it?")
    assert "48 hrs unredeemed will go back to UniFleet wallet" in answer


def test_faq_redemption_window_question_is_bilingual():
    question, _ = FAQ_ENTRIES[3]
    assert "How long do I have to redeem it?" in question
    assert "Gaano katagal bago mag-expire ang voucher?" in question


def test_faq_redemption_window_answer_is_bilingual():
    _, answer = FAQ_ENTRIES[3]
    assert "48 hrs unredeemed will go back to UniFleet wallet" in answer
    assert "Kung hindi ma-redeem sa loob ng 48 oras, ang halaga ay babalik sa UniFleet wallet." in answer


def test_faq_existing_three_entries_unchanged():
    """Regression guard: appending the new entry must not disturb the
    existing 3 FAQ entries' content or order."""
    assert FAQ_ENTRIES[0][0].startswith("Q: How do I redeem a voucher?")
    assert FAQ_ENTRIES[1][0].startswith("Q: What if a driver goes to the wrong station?")
    assert FAQ_ENTRIES[2][0].startswith("Q: Who do I contact if there’s an issue?")
