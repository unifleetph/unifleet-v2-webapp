# Architecture: Exclude Redeemed Orders from Supplier Report

> **Date:** 2026-11-09
> **Phase:** 2 of 5 (System Architecture)
> **Requirements source:** specs/requirements/REQ-supplier-report-exclusion-flag.md
> **Tasks:** TASKS-supplier-report-exclusion-flag.md
> **Type:** feature

## Architecture Summary

A single new boolean column on `vouchers`, settable only on Redeemed orders by one admin-gated toggle route, read by exactly two existing filter functions — `daily_report.py::_is_live()` (the daily Supplier PDF) and a new `main.py::_exclude_from_supplier_report()` helper used only in `export_supplier_csv` (the Supplier CSV). Nothing else in the system reads this column. The toggle flips the flag; it never writes to the audit log, per REQ decision 4. No schema change touches any other table, no new module, no new dependency — this mirrors the existing `deleted_at` soft-delete pattern almost exactly, just scoped narrower (two exports instead of every read path) and reversible.

## High-Level Structure

```
Admin dashboard (admin.html)
  └─ Redeemed row → new toggle button/form
       └─ POST /admin/orders/<id>/toggle-report-exclusion  (main.py)
              └─ flips vouchers.excluded_from_supplier_report via
                 repo.update_voucher_fields() (existing, generic)

Daily Supplier PDF                      Supplier CSV export
  daily_report.py::live_orders()          main.py::export_supplier_csv()
    └─ _is_live() now also checks           └─ now also runs
       excluded_from_supplier_report            _exclude_from_supplier_report()
```

Both export paths already run their own independent exclusion filter (`_is_live()` for the PDF, `_exclude_deleted()` for the CSV) — this adds one more AND-condition to each, in place, with no shared cross-module helper. `daily_report.py` and `main.py` stay decoupled exactly as they are today.

## Tech Choices

| Area | Decision | Alternatives Considered | Rationale |
|------|----------|---------------------------|-----------|
| Storage | New column on existing `vouchers` table | New side table (`supplier_report_exclusions`) keyed by voucher_id | A side table adds a join for every read path that needs the flag (two of them); a column is a single-row lookup already in hand, matches `deleted_at`'s precedent exactly |
| Toggle shape | One route that flips the current value | Two one-directional routes (`/exclude`, `/include`) | Decided with developer: fewer routes, matches the symmetric reversible nature of R1/R4 |
| Column wiring | Left out of `models.py` `VOUCHER_COLUMNS`; relies on the column's own `DEFAULT FALSE` | Add it to `VOUCHER_COLUMNS` like `deleted_at`/`margin_pct_at_booking` | Decided with developer: this flag is only ever set by a later admin action, never at booking time — no reason to thread it through `create_unverified_booking`'s insert-row shaping |

## Patterns & Conventions

- **Dual schema declaration** (`CREATE TABLE IF NOT EXISTS` column + `ALTER TABLE ADD COLUMN IF NOT EXISTS`) — the project's existing convention for every voucher column added after the original table, applied here exactly as `deleted_at` was.
- **Narrow filter helper mirroring `_exclude_deleted`** — `main.py` already has a one-line list-comprehension helper for the deleted-row case; the new `_exclude_from_supplier_report` follows the identical shape, same file, same style.
- **Flash + redirect error handling** — `admin_orders_delete`'s existing pattern (flash an error, 302 back to `/admin`, no try/except around the repo write) is followed exactly by the new toggle route, including its lack of one — consistent with the existing precedent rather than a new standard.
- **Not applied:** no audit logging (REQ decision 4, explicitly skipped, unlike `delete_order`'s `append_audit` call).

## Data Models

### `vouchers.excluded_from_supplier_report`

**Purpose:** marks a Redeemed order as omitted from the two supplier-facing exports, without affecting anything else about the order.

**Key fields:**
| Field | Type / Constraint | Notes |
|-------|----------------------|-------|
| `excluded_from_supplier_report` | `BOOLEAN NOT NULL DEFAULT FALSE` | Every existing and new row defaults to `FALSE` (included); no backfill needed |

**Relationships:** none — a plain column on `vouchers`, no FK, no new table.

**Lifecycle:** `FALSE` at creation (via the column default, not through `VOUCHER_COLUMNS`) → admin can flip to `TRUE` only while `status == "Redeemed"` → admin can flip back to `FALSE` at any time → never touched by any other code path (booking creation, status transitions, soft-delete).

## API Contracts / Interfaces

### Admin toggle route (`main.py`)

**Boundary:** HTTP, admin-session/key-gated (same `require_admin` as every other admin route).

| Method/Op | Path | Purpose | Errors / Returns |
|-----------|------|---------|----------------------|
| POST | `/admin/orders/<voucher_id>/toggle-report-exclusion` | Flips `excluded_from_supplier_report` for one voucher | 404 if voucher not found (flash + redirect, same as `admin_orders_delete`); flash error + 302 if `status != "Redeemed"`; flash success + 302 on success; unhandled DB error → 500 (same as `admin_orders_delete` today, no try/except) |

**Auth requirements:** admin only (`require_admin`), identical to every sibling `/admin/orders/...` route.

### Internal filter functions (not externally callable)

| Function | Signature | Purpose |
|----------|-----------|---------|
| `main.py::_exclude_from_supplier_report` | `(vouchers: list[dict]) -> list[dict]` | Filters out rows where the flag is `True`; used only by `export_supplier_csv` |
| `daily_report.py::_is_live` | `(order: dict) -> bool` | Gains one more `and`-condition on the flag; used only by `live_orders()` |

## Module Boundaries

Unchanged. `main.py` and `daily_report.py` remain independent — each gets its own filter addition, no shared helper, no new cross-module import. `db/postgres_repo.py` is untouched: `update_voucher_fields` and the `SELECT *`-based list/get methods already handle an arbitrary new column with zero code change.

## Change Footprint

### New files / modules

None.

### Modified files / modules

| Path | What changes here |
|------|----------------------|
| `db/schema.sql` | Add `excluded_from_supplier_report BOOLEAN NOT NULL DEFAULT FALSE` to the `vouchers` `CREATE TABLE IF NOT EXISTS` block (near `deleted_at`, line ~103), plus a mirrored `ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS excluded_from_supplier_report BOOLEAN NOT NULL DEFAULT FALSE;` (near line ~130, same convention as `deleted_at`'s own ALTER line) |
| `main.py` | New route `admin_orders_toggle_report_exclusion` (near `admin_orders_delete`, ~line 814): `require_admin` check, 404 if voucher missing, reject if `status != "Redeemed"`, else read current flag + flip it via `repo.update_voucher_fields`, flash, redirect. New helper `_exclude_from_supplier_report(vouchers)` (near `_exclude_deleted`, ~line 341). `export_supplier_csv` (~line 2024) gains the new filter alongside its existing `_exclude_deleted` call |
| `daily_report.py` | `_is_live()` (~line 60) gains `and not order.get("excluded_from_supplier_report")` |
| `templates/admin.html` | In the Redeemed-row branch (~line 500-505, beside the existing disabled "Delete Order" button), add the new toggle button/form — label reflects current state ("Exclude from supplier report" / "Include in supplier report") |

### Deleted / replaced

None.

### Touched but not changed (silent-regression hotspots)

| Path | Why it matters |
|------|------------------|
| `models.py` (`VOUCHER_COLUMNS`) | Deliberately NOT updated — the new column relies on its own DB default for every insert path; any task must not add it here |
| `db/postgres_repo.py` (`update_voucher_fields`, `list_all_vouchers`, `list_recent_vouchers`, `get_voucher`) | All already generic / `SELECT *` — must keep working unchanged, proving the new column needs zero repo-layer code |
| `tests/test_supplier_exports_exclude_deleted.py` | Locks in today's `deleted_at`-only exclusion behavior for both exports — must keep passing exactly as-is, proving the new filter is additive (AND), not a replacement |
| `tests/test_daily_report.py` | Locks in `_is_live()`'s current behavior — same reasoning |
| `tests/test_admin_delete_order.py` | A different route (`admin_orders_delete`) — must be completely unaffected by the new toggle route existing alongside it |
| `tests/test_admin_display_layout.py` (`test_admin_dashboard_has_delete_order_column`) | Only asserts the `<th>Delete Order</th>` header exists — the new button must not require a new column/header, confirmed no test asserts the disabled-button's exact markup |

## Areas of Impact

| Area | Impact | Risk (L/M/H) | Why |
|------|--------|---------------|-----|
| Daily Supplier PDF (`daily_report.py`) | One more AND-condition in the single gate function | L | Purely narrowing; every previously-included, non-excluded order stays included |
| Supplier CSV export (`main.py::export_supplier_csv`) | Same, new helper call added | L | Same reasoning — additive filter only |
| Admin dashboard UI | One new button in an existing conditional branch | L | No new column/table, no layout restructure, confirmed no test locks the exact disabled-button markup |
| Database schema | One new nullable-safe column with a safe default | L | Additive, idempotent (`IF NOT EXISTS` convention), zero backfill, no FK, no index needed (filtered in app code, not queried by this column directly) |

**Contract changes:** none. No external API response shape changes (the toggle route is a new, internal admin-only endpoint; the two export formats gain no new column of their own — they simply have fewer rows when a flag is set).

**Cross-cutting ripples:** none beyond the one new column. No auth changes, no new env vars, no feature flag, no deploy-order constraint (the `ALTER TABLE ... IF NOT EXISTS` runs on every container start per the existing `db/apply.py` convention, same as every other schema change in this project).

## Cross-Cutting Concerns

- **Errors:** the toggle route follows `admin_orders_delete`'s existing precedent exactly — flash message + 302 redirect for expected failures (voucher not found, wrong status), and an unhandled exception still 500s (no try/except), matching that route's current behavior rather than introducing a new standard.
- **Logging & metrics:** none. Explicitly decided against (REQ decision 4) — no audit log entry, no new log lines beyond whatever Flask/the app already emits for any request.
- **Auth / authz:** `require_admin`, identical check and mechanism to every other `/admin/orders/...` route.
- **Performance:** negligible — one additional boolean column read (already fetched via `SELECT *`), one additional in-memory list comprehension per export generation, one additional single-row `UPDATE` per toggle click. No new query, no new index.
- **Security:** no new input surface beyond the existing `<voucher_id>` path parameter pattern already used by `admin_orders_delete`; no injection risk (parameterized `update_voucher_fields`, same as every other voucher write).
- **Migrations / rollout:** additive column, `DEFAULT FALSE`, idempotent `IF NOT EXISTS` on both the `CREATE TABLE` and the `ALTER TABLE` lines — applies cleanly on every existing and fresh database with zero backfill step, exactly like every prior voucher-column addition in this schema.

## Architecture Decisions Log

| # | Decision | Alternatives | Chosen Because | Satisfies REQs |
|---|----------|---------------|------------------|-----------------|
| A1 | New column on `vouchers`, not a side table | Separate `supplier_report_exclusions` table | A column is a single lookup already present in every fetched row; a side table forces a join into both export paths for one boolean | R1, R2, R3 |
| A2 | Single toggle route that flips current state | Two one-directional routes | Fewer routes, matches the reversible symmetric nature of the requirement; developer's explicit call | R1, R4 |
| A3 | Column left out of `models.py` `VOUCHER_COLUMNS` | Add it there like `deleted_at` | Only ever set by a later admin action, never at booking time; keeps the booking-creation code path untouched; developer's explicit call | R1 |
| A4 | Filter added as a new AND-condition in each existing gate function (`_is_live`, a new CSV-only helper) | One shared cross-module predicate function | `main.py` and `daily_report.py` are already independent by design; a shared helper would be the only cross-module coupling introduced by this feature, for a one-line check | R2, R3 |
| A5 | No audit logging on toggle | Log like `delete_order` | Explicit REQ decision — low-stakes, reversible, not worth the audit noise | R6 |
| A6 | Error handling mirrors `admin_orders_delete`'s existing pattern exactly (flash+redirect, no try/except around the write) | Wrap the new route in try/except for a friendlier 500 | Consistency with the sibling route it sits beside; introducing new error-handling rigor here but not there would be an inconsistent precedent, out of scope for this bugfix | R1, R4, R5 |

## Risk & Stress-Test Scenarios

### Forward — runtime failure scenarios

| Scenario | How the Design Handles It |
|----------|------------------------------|
| Two admins click the toggle on the same Redeemed order at the same time | Last-write-wins (plain `UPDATE`, no optimistic lock) — acceptable per REQ's own "low-stakes, no audit" framing; worst case is a double-flip landing back where it started, never a corrupted state |
| DB connection drops mid-toggle (pool exhaustion) | Route has no try/except (matching `admin_orders_delete`'s existing behavior) — request 500s, voucher's flag is unchanged (the `UPDATE` either fully applies or not at all); not a new failure mode, same precedent as the sibling delete route already in production |
| Daily report tick runs while an admin is mid-toggle on some other order | No interaction — `_is_live()` reads the flag at report-generation time; a toggle either lands before or after that read, both are correct, there's no partial-write window since it's one column |
| `export_supplier_csv` called with zero vouchers after the new filter removes everything | Already handled by the existing `if not rows: return "<h2>No vouchers to export.</h2>", 200` right after `_exclude_deleted` — the new filter just runs alongside it, same early-return path |

### Backward — regression risk per touched area

| Touched area (from Change Footprint) | What could regress | How we'd know / mitigation |
|---------------------------------------|-----------------------|-------------------------------|
| `daily_report.py::_is_live()` | Could accidentally exclude a non-flagged order if the new condition is wired as OR instead of AND, or inverted | `tests/test_daily_report.py`'s existing suite must keep passing unchanged, plus new tests asserting a non-excluded Redeemed order still appears |
| `main.py::export_supplier_csv` | Same inversion risk for the new helper | `tests/test_supplier_exports_exclude_deleted.py` must keep passing unchanged, plus new tests for the exclusion flag specifically |
| `templates/admin.html` Redeemed-row branch | Markup change could break the existing disabled "Delete Order" button's rendering for Redeemed rows | No test currently locks that exact markup (confirmed above) — new tests should assert both the existing disabled button AND the new toggle button render together for a Redeemed row |
| `main.py::admin_orders_delete` (sibling route, not modified) | None expected — different route entirely, but worth confirming nothing in the new route's code accidentally shares mutable state with it | `tests/test_admin_delete_order.py` must keep passing unchanged with zero modification |

## Open Questions

None — every decision needed to implement this was resolved during REQ and this architecture session.

## Out of Scope

- Any change to `deleted_at`'s existing behavior or meaning (per REQ scope boundary).
- Audit logging of the toggle (per REQ decision 4).
- Extending the flag to any non-Redeemed status (per REQ R5).
- Bulk exclude/include actions (not raised in the brief).
- Any change to `/supplier-sheet.pdf` (the separate, Unredeemed-only manual route) or `/api/v1/discounts`-style JSON endpoints — REQ scope is explicitly the daily PDF and the CSV export only.
