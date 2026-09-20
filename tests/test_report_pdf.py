"""
tests/test_report_pdf.py — Supplier PDF fuel-type column (T8,
ARCH-fuel-types-expansion). Tests the pure row/name-expansion helpers
directly (no reportlab dependency) plus a smoke test of the real
build_supplier_pdf() call.
"""

import base64
import re
import zlib

from report_pdf import (
    _build_supplier_row,
    _empty_message,
    _fuel_type_display,
    _report_title,
    _supplier_header,
    _table_data,
    build_supplier_pdf,
    FAQ_ENTRIES,
)


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


# ============================================================
# Daily report options (T1, ARCH-midnight-supplier-report)
#
# Opt-in extras for the midnight email: a Status column, a "No orders" state
# and a "Report for <date>" label. Every default-call test below pins the
# on-demand /supplier-sheet.pdf output so it cannot drift (A12).
# ============================================================

def _pdf_text(pdf_bytes: bytes) -> str:
    """Text drawn on the pages of a reportlab PDF. reportlab writes each page
    stream as ASCII85 + Flate, so undo both; no PDF-parsing dependency."""
    chunks = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", pdf_bytes, re.S):
        data = m.group(1).strip()
        try:
            if data.endswith(b"~>"):
                data = base64.a85decode(data[:-2], adobe=False)
            chunks.append(zlib.decompress(data))
        except Exception:
            continue
    text = b"\n".join(chunks).decode("latin-1")
    # Undo PDF string escaping: \( \) \\ and octal codes such as \226 (en dash).
    text = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)
    return re.sub(r"\\([()\\])", r"\1", text)


_STATIONS = [{"id": "s1", "name": "Cleanfuel"}]


def _voucher(**over):
    v = {"station": "Cleanfuel", "driver_name": "Dave", "vehicle_plate": "XYZ-123",
         "voucher_id": "UF-1", "fuel_type": "Biodiesel", "requested_amount_php": 1000,
         "status": "Unredeemed"}
    v.update(over)
    return v


# --- Status column ---

def test_status_column_sits_after_voucher_id_and_before_signature():
    header = _supplier_header(include_status=True)
    assert header[5] == "Voucher ID"
    assert header[6] == "Status"
    assert header[7] == "Name / Signature"
    row = _build_supplier_row(_voucher(), include_status=True)
    assert len(row) == len(header) == 8
    assert row[5] == "UF-1"
    assert row[6] == "Unredeemed"
    assert row[7] == ""


def test_each_status_is_shown_on_its_own_row():
    for status in ("Unverified", "Unredeemed", "Redeemed"):
        row = _build_supplier_row(_voucher(status=status), include_status=True)
        assert row[6] == status


def test_blank_or_missing_status_shows_unverified():
    assert _build_supplier_row(_voucher(status=""), include_status=True)[6] == "Unverified"
    assert _build_supplier_row(_voucher(status=None), include_status=True)[6] == "Unverified"
    no_status = _voucher()
    del no_status["status"]
    assert _build_supplier_row(no_status, include_status=True)[6] == "Unverified"


def test_unverified_order_with_no_amount_builds_with_a_blank_amount_cell():
    bare = {"station": "Cleanfuel", "voucher_id": "UF-9", "status": "Unverified"}
    row = _build_supplier_row(bare, include_status=True)
    assert row[1] == ""
    assert row[5] == "UF-9"


def test_a_real_zero_amount_is_not_blanked():
    row = _build_supplier_row(_voucher(requested_amount_php=0), include_status=True)
    assert row[1] == "0.00"


# --- Empty state and labelling ---

def test_empty_daily_report_says_no_orders_and_drops_the_dash_row():
    header = _supplier_header(include_status=True)
    data = _table_data([], header, report_date="2026-09-20")
    assert data == [header]  # no dash-only placeholder row
    assert _empty_message("2026-09-20") == "No orders for 2026-09-20"

    pdf = build_supplier_pdf(
        vouchers=[], target_station_ids=[], stations=_STATIONS,
        include_status=True, report_date="2026-09-20",
    )
    text = _pdf_text(pdf)
    assert "No orders for 2026-09-20" in text
    assert "\x97" not in text.split("Frequently Asked Questions")[0]  # no em-dash placeholder row


def test_non_empty_daily_report_has_no_empty_message():
    pdf = build_supplier_pdf(
        vouchers=[_voucher()], target_station_ids=[], stations=_STATIONS,
        include_status=True, report_date="2026-09-20",
    )
    assert "No orders for" not in _pdf_text(pdf)


def test_daily_title_carries_the_report_date_not_the_unredeemed_title():
    title = _report_title("2026-09-20")
    assert "Report for 2026-09-20" in title
    assert "Unredeemed Fuel Vouchers" not in title

    pdf = build_supplier_pdf(
        vouchers=[_voucher()], target_station_ids=[], stations=_STATIONS,
        include_status=True, report_date="2026-09-20",
    )
    text = _pdf_text(pdf)
    assert "Report for 2026-09-20" in text
    assert "Unredeemed Fuel Vouchers" not in text


def test_daily_pdf_with_mixed_statuses_is_a_valid_pdf():
    vouchers = [
        _voucher(voucher_id="UF-1", status="Unverified"),
        _voucher(voucher_id="UF-2", status="Unredeemed"),
        _voucher(voucher_id="UF-3", status="Redeemed"),
        {"station": "Cleanfuel", "voucher_id": "UF-4", "status": ""},
    ]
    pdf = build_supplier_pdf(
        vouchers=vouchers, target_station_ids=[], stations=_STATIONS,
        include_status=True, report_date="2026-09-20",
    )
    assert isinstance(pdf, bytes) and pdf.startswith(b"%PDF")
    text = _pdf_text(pdf)
    for vid in ("UF-1", "UF-2", "UF-3", "UF-4"):
        assert vid in text
    for status in ("Unverified", "Unredeemed", "Redeemed"):
        assert status in text


# --- Regression guard: the default call is what /supplier-sheet.pdf uses ---

def test_default_header_rows_and_title_are_unchanged():
    assert _supplier_header() == [
        "Station (Expected)", "Amount (PHP)", "Driver name", "Plate",
        "Fuel Type", "Voucher ID", "Name / Signature",
    ]
    row = _build_supplier_row(_voucher())
    assert len(row) == 7
    assert row == ["Cleanfuel", "1,000.00", "Dave", "XYZ-123", "Biodiesel", "UF-1", ""]
    # A missing amount still renders 0.00 by default; only the daily report blanks it.
    assert _build_supplier_row({"voucher_id": "UF-9"})[1] == "0.00"
    assert _report_title() == "UniFleet – Unredeemed Fuel Vouchers (PDF Version)"
    # The default empty sheet keeps its placeholder row.
    header = _supplier_header()
    assert _table_data([], header) == [header, ["—"] * 7]


def test_default_pdf_has_no_status_column_and_the_old_title():
    pdf = build_supplier_pdf(vouchers=[_voucher()], target_station_ids=[], stations=_STATIONS)
    text = _pdf_text(pdf)
    assert "UniFleet \x96 Unredeemed Fuel Vouchers (PDF Version)" in text
    assert "Report for" not in text
    assert "Status" not in text
    assert "No orders for" not in text
