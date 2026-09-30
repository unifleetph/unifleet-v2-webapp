"""
tests/test_voucher_exclude_deleted.py — unit tests for main._exclude_deleted()
(T3, ARCH-delete-order-button).
"""

import main


def test_exclude_deleted_filters_rows_with_deleted_at_set():
    rows = [
        {"voucher_id": "A", "deleted_at": None},
        {"voucher_id": "B"},  # key entirely absent
        {"voucher_id": "C", "deleted_at": ""},  # CSV-world empty
        {"voucher_id": "D", "deleted_at": "2026-09-02T00:00:00"},
    ]

    result = main._exclude_deleted(rows)

    ids = {r["voucher_id"] for r in result}
    assert ids == {"A", "B", "C"}
