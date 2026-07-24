# Architecture: Brief 3 Fixes — Booking Order, Admin Styling, Customer Export

> **Date:** 2026-07-24
> **Phase:** 2 of 5 (System Architecture) — lightweight, plan-architecture skipped per developer call (small, well-understood work)
> **Requirements source:** specs/requirements/REQ-brief-3-fixes.md
> **Type:** bugfix + feature (mixed)

## Architecture Summary

Five small, independent fixes across the Flask monolith: (R1) revert `/book`'s field order so Fuel Type + Station appear before Driver & Vehicle, restoring the pre-fuel-types layout while keeping the fuel-type-filters-station dependency intact; (R2) fix a background-color mismatch on `/admin/stations` and `/admin/prices` vs `/admin`; (R3) add 3 customer-contact columns (Name, Number, Email) to both booking export CSVs, joined at export time via `account_code`; (R4/R5) add an always-visible all-customers/all-drivers table (one row per driver, sourced from booking history) to `/admin/customers`, plus its own CSV export. No schema changes, no new dependencies, no new data models — all work reuses existing `repo.list_customers()` / `repo.list_all_vouchers()` and existing template/route patterns.

## Change Footprint

| Path | What changes here | Task |
|---|---|---|
| `templates/book.html` | Move Fuel Type + Station select block (and the "How to Find Discounts" info block) above the Driver & Vehicle section | T1 |
| `tests/test_book_template_layout.py` | Update/invert `test_driver_vehicle_precedes_station_section`; add new order assertions | T1 |
| `templates/admin_stations.html` | `body` background color → `#f4f4f4` (match `/admin`) | T2 |
| `templates/admin_prices.html` | `body` background color → `#f4f4f4` (match `/admin`) | T2 |
| `main.py` (`admin_bookings_export`, `admin_customer_export`) | Join customer contact info via `repo.list_customers()`, append 3 columns to the exported DataFrame (export-time only, not `VOUCHER_COLUMNS`) | T3 |
| `tests/test_admin_bookings_export.py` (new) | Coverage for both export routes' new columns | T3 |
| `main.py` (`admin_customers`) | Compute all-customers/driver rows once, pass to all 4 existing render branches | T4 |
| `templates/admin_customer_lookup.html` | New table below the search form, always rendered | T4 |
| `tests/test_admin_customers.py` | Extend with all-customers-table scenarios | T4 |
| `main.py` (new route, e.g. `/admin/customers/export_all`) | CSV export of the same all-customers/driver rows | T5 |
| `tests/test_admin_customers.py` | Extend with export-route scenarios | T5 |

**Touched but not changed:** `models.py`'s `VOUCHER_COLUMNS` (T3 does not modify it — new columns are export-time joins only, not part of the persisted voucher shape); `repo.list_customers()` / `repo.list_all_vouchers()` (existing methods, reused as-is by T3/T4/T5); `admin_customer_lookup.html`'s existing detail/picklist/not_found rendering (T4 must not regress).

## Areas of Impact

| Area | Risk | Why |
|---|---|---|
| `/book` field order | L | Presentation-only; form field names/POST contract unchanged, existing booking JS (station-by-fuel-type filter) unaffected — only its visual position moves |
| Admin page styling | L | Single CSS property, two files |
| Booking export CSVs | L | Additive columns only, joined at export time; existing columns/behavior untouched |
| `/admin/customers` route | M | 4 existing render branches (empty query, not_found, picklist, detail) all need the new table wired in — risk is missing one branch, not data correctness |

**Cross-cutting ripples:** none — no auth changes, no schema migration, no new env vars.

## Architecture Decisions Log

| # | Decision | Alternatives | Chosen Because | Satisfies REQs |
|---|---|---|---|---|
| A1 | Restore full original section order (Station/discount/amount/date, then Driver&Vehicle), with Fuel Type inserted immediately before Station | Only move Station, leave Fuel Type where T5 put it | User confirmed pairing Fuel Type with Station preserves the station-filters-by-fuel-type dependency that motivated the original reorder | R1 |
| A2 | New customer-contact columns joined at CSV-export time only, not added to `VOUCHER_COLUMNS` | Add columns to `VOUCHER_COLUMNS` so they persist with every voucher | These are derived/joined data (customer record via `account_code`), not properties of the voucher itself; adding to `VOUCHER_COLUMNS` would touch the persistence layer (CSV + Postgres) for no benefit and risk the same column-duplication bug class seen in prior REQs | R3 |
| A3 | All-customers table rows computed once in `admin_customers()`, passed to all 4 existing render branches | Duplicate the table-building logic per branch | Single source of truth, avoids the 4-branch drift the REQ's R4 acceptance criterion (always visible) explicitly requires | R4 |
| A4 | Driver names sourced from `repo.list_all_vouchers()` grouped by `account_code`, not the `presets` table | Wire up the dormant Postgres `presets` table | `presets` is unused in the current persistence setup (driver presets live in CSV files); confirmed in REQ decisions log | R4 |
| A5 | New dedicated export route for the all-customers table, separate from existing `admin_bookings_export`/`admin_customer_export` | Reuse `admin_bookings_export` with a query param | Different data shape (customer/driver roster vs. booking transactions) — a separate route keeps both contracts simple | R5 |

## Out of Scope

- Any change to booking validation, price/discount logic, or fuel-type filtering behavior (R1 is presentation-only).
- Any other visual/style changes to `/admin/stations` or `/admin/prices` beyond background color.
- Wiring up the dormant `presets` Postgres table.
- Pagination or search/filter controls on the new `/admin/customers` table.

---

# Tasks

## Task T1: `/book` Field Order Revert

> **Status:** done
> **Verification:** test-after
> **Effort:** s
> **Priority:** medium
> **Depends on:** None
> **Satisfies REQs:** R1
> **Footprint slice:** Modified: `templates/book.html`
> **High-risk areas touched:** `/book` field order (L)

### Description

Move the Fuel Type + Station select block (and the "How to Find Discounts" info block that follows it) above the Driver & Vehicle section in `book.html`, restoring the original pre-fuel-types page structure while keeping Fuel Type paired immediately before Station so the existing station-filters-by-fuel-type behavior keeps working.

### Test Plan

#### Test File(s)
- `tests/test_book_template_layout.py`

#### Test Scenarios

##### Field Order

- **fuel_type_and_station_precede_driver_vehicle** — GIVEN the booking form renders WHEN checking element order THEN `id="fuel_type"` and `id="station"` both appear before `id="driver_mode"` _(verifies R1; replaces/inverts the existing `test_driver_vehicle_precedes_station_section`)_
- **fuel_type_immediately_precedes_station** — GIVEN the rendered form WHEN checking element order THEN the Fuel Type select's index is immediately followed by the Station select (pairing preserved) _(verifies R1, ARCH A1)_
- **discount_info_block_precedes_driver_vehicle** — GIVEN the rendered form WHEN checking element order THEN the "How to Find Discounts" heading appears before `id="driver_mode"` _(verifies R1)_

##### Regression Guard

- **fuel_type_select_still_has_three_real_options** — existing test, must still pass unchanged
- **station_dropdown_still_populated_client_side** — GIVEN the rendered form WHEN inspecting the station `<select>` THEN it has no server-rendered `<option>` elements (population stays client-side via `window.__STATION_TABLE__`) _(guards backward-regression risk for the fuel-type-filters-station JS)_

### Implementation Notes

- **Module(s):** `templates/book.html`
- **Pattern reference:** the block being moved is `templates/book.html:313-380ish` (Fuel Type select through the discount info `</div>`); target position is immediately after the `contact_number` input, before the `<!-- Driver & Vehicle -->` comment/`<hr>`
- **Key decisions:** ARCH A1
- **Libraries:** none
- **High-risk callouts:** none — pure template reorder, no JS/route changes

### Scope Boundaries

- Do NOT change any field's `name` attribute, `required` status, or the booking POST handler in `main.py`.
- Do NOT change the client-side station-filter JS logic itself — only its position on the page moves.
- Do NOT add Gasoline/Diesel selection or any new field (out of scope, unrelated to this REQ).

### Files Expected

**Modified files:**
- `templates/book.html` (section reorder only)
- `tests/test_book_template_layout.py` (invert existing order test, add new order assertions)

**Must NOT modify:**
- `main.py` (booking POST handler — no change needed, form contract unchanged)

---

## Task T2: Admin Page Background Color Fix

> **Status:** done
> **Verification:** checklist
> **Effort:** xs
> **Priority:** low
> **Depends on:** None
> **Satisfies REQs:** R2
> **Footprint slice:** Modified: `templates/admin_stations.html`, `templates/admin_prices.html`
> **High-risk areas touched:** None

### Description

Set the `body` background color on `/admin/stations` and `/admin/prices` to `#f4f4f4`, matching `/admin`'s existing background, so all three admin pages look visually consistent.

### Verification Checklist

- **`grep -o "background:[^;]*" templates/admin.html | head -1`** — expected: `background: #f4f4f4` (baseline, confirms target value)
- **`grep -o "background:[^;]*" templates/admin_stations.html | head -1`** — expected (after fix): `background: #f4f4f4` on the `body` rule
- **`grep -o "background:[^;]*" templates/admin_prices.html | head -1`** — expected (after fix): `background: #f4f4f4` on the `body` rule
- **Human-verified:** load `/admin`, `/admin/stations`, `/admin/prices` side by side in a browser — backgrounds visually match
- Regression guard: **diff limited to the `body { ... background: ... }` line in each file** — no other style rule changed

### Implementation Notes

- **Module(s):** `templates/admin_stations.html`, `templates/admin_prices.html`
- **Pattern reference:** `templates/admin.html`'s `body { background: #f4f4f4; }` rule
- **Key decisions:** none beyond REQ decision #3 (background-only, no other style changes)
- **Libraries:** none

### Scope Boundaries

- Do NOT change any other CSS rule, layout, spacing, or color in either file.
- Only the `body` background-color value changes.

### Files Expected

**Modified files:**
- `templates/admin_stations.html` (body background color)
- `templates/admin_prices.html` (body background color)

**Must NOT modify:**
- `templates/admin.html` (reference/source of truth, not the target of the fix)

---

## Task T3: Customer Contact Columns on Booking Export CSVs

> **Status:** done
> **Verification:** tdd
> **Effort:** s
> **Priority:** medium
> **Depends on:** None
> **Satisfies REQs:** R3
> **Footprint slice:** Modified: `main.py` (`admin_bookings_export`, `admin_customer_export`)
> **High-risk areas touched:** Booking export CSVs (L)

### Description

Add Customer Name, Customer Number, and Customer Email columns to both the "Export All Bookings" CSV and the per-customer booking export CSV, populated by joining each booking's `account_code` against `repo.list_customers()` at export time. Bookings with no linked customer get blank cells for these 3 columns.

### Test Plan

#### Test File(s)
- `tests/test_admin_bookings_export.py` (new)

#### Test Scenarios

##### Export All Bookings

- **export_all_bookings_includes_customer_columns** — GIVEN a booking with `account_code` linked to a known customer WHEN GET `/admin/bookings/export` THEN the CSV has Customer Name/Number/Email columns populated with that customer's `contact_name`/`contact_number`/`email` _(verifies R3)_
- **export_all_bookings_blank_for_missing_account_code** — GIVEN a booking with no `account_code` (or one that matches no customer) WHEN exported THEN the 3 new columns are blank, no error _(REQ edge case)_

##### Per-Customer Export

- **customer_export_includes_customer_columns** — GIVEN `admin_customer_export` for a known customer WHEN exported THEN the 3 columns are present and populated with that customer's own contact info _(verifies R3)_

##### Regression Guard

- **existing_voucher_columns_unchanged_in_both_exports** — GIVEN the same fixtures WHEN exported THEN every column from `VOUCHER_COLUMNS` is still present, in order, with unchanged values _(guards backward-regression risk — export contract)_
- **customer_export_still_404s_for_unknown_account_code** — existing behavior, must still pass unchanged

### Implementation Notes

- **Module(s):** `main.py`
- **Pattern reference:** existing `pd.DataFrame(bookings, columns=VOUCHER_COLUMNS).to_csv(...)` calls in both routes
- **Key decisions:** ARCH A2 (join at export time, do not touch `VOUCHER_COLUMNS`)
- **Libraries:** pandas (already a dependency)
- **High-risk callouts:** build the customer lookup dict once via `repo.list_customers()` per request (not per-row `repo.get_customer()` calls) to avoid N+1 lookups

### Scope Boundaries

- Do NOT add these columns to `models.VOUCHER_COLUMNS` (ARCH A2 — export-time join only).
- Do NOT change any existing column's name, order, or value.
- Do NOT touch the CSV/Postgres persistence layer.

### Files Expected

**Modified files:**
- `main.py` (`admin_bookings_export`, `admin_customer_export`)

**New files:**
- `tests/test_admin_bookings_export.py`

**Must NOT modify:**
- `models.py` (`VOUCHER_COLUMNS` — silent-regression hotspot, covered by regression-guard test)

---

## Task T4: All-Customers Table on `/admin/customers`

> **Status:** not started
> **Verification:** test-after
> **Effort:** m
> **Priority:** medium
> **Depends on:** None
> **Satisfies REQs:** R4
> **Footprint slice:** Modified: `main.py` (`admin_customers`), `templates/admin_customer_lookup.html`
> **High-risk areas touched:** `/admin/customers` route (M)

### Description

Add a table below the search box on `/admin/customers`, always visible regardless of search state, listing every customer with one row per distinct driver from that customer's booking history (Customer Name, Number, Email, Driver Name). A customer with no bookings yet still appears, with one row and a blank Driver Name.

### Test Plan

#### Test File(s)
- `tests/test_admin_customers.py`

#### Test Scenarios

##### Always-Visible Table

- **all_customers_table_present_on_empty_query** — GIVEN no search query WHEN GET `/admin/customers` THEN the response contains a table listing all customers _(verifies R4)_
- **all_customers_table_present_on_detail_state** — GIVEN a search that resolves to a single customer WHEN GET THEN the all-customers table still renders below the detail view _(verifies R4, ARCH A3)_
- **all_customers_table_present_on_picklist_state** — GIVEN a fuzzy search returning multiple matches WHEN GET THEN the table still renders _(verifies R4, ARCH A3)_
- **all_customers_table_present_on_not_found_state** — GIVEN a search with zero matches WHEN GET THEN the table still renders _(verifies R4, ARCH A3)_

##### Row Shape

- **customer_with_two_distinct_drivers_gets_two_rows** — GIVEN a customer with 2 distinct `driver_name` values across their vouchers WHEN GET THEN the table has 2 rows for that customer _(verifies R4)_
- **customer_with_zero_bookings_gets_one_blank_driver_row** — GIVEN a customer with no vouchers WHEN GET THEN 1 row appears with a blank Driver Name cell _(REQ edge case)_

##### Regression Guard

- **existing_detail_picklist_not_found_rendering_unchanged** — existing tests in this file for detail/picklist/not_found states must still pass unchanged

### Implementation Notes

- **Module(s):** `main.py`, `templates/admin_customer_lookup.html`
- **Pattern reference:** `admin_customers()`'s existing 4 `render_template` call sites; driver aggregation mirrors the existing per-customer booking filter (`[v for v in repo.list_all_vouchers() if v.account_code == ...]`)
- **Key decisions:** ARCH A3 (compute once, pass to all branches), ARCH A4 (driver names from booking history, not `presets`)
- **Libraries:** none new
- **High-risk callouts:** M-risk per ARCH — 4 existing render branches must ALL receive the new table data; missing one branch is the most likely bug. Test plan covers all 4 explicitly.

### Scope Boundaries

- Do NOT add pagination, search, or filter controls to the new table (out of scope).
- Do NOT wire up the `presets` table (ARCH A4, out of scope).
- Do NOT change the existing search/detail/picklist logic itself — only add the new table alongside it.
- Do NOT add the CSV export button/link (T5's scope) — table only.

### Files Expected

**Modified files:**
- `main.py` (`admin_customers()`)
- `templates/admin_customer_lookup.html` (new table markup)

**Modified test files:**
- `tests/test_admin_customers.py`

**Must NOT modify:**
- `main.py` (`admin_customer_export`, `admin_bookings_export` — T3's scope, already landed)

---

## Task T5: All-Customers Table CSV Export

> **Status:** not started
> **Verification:** tdd
> **Effort:** s
> **Priority:** low
> **Depends on:** T4
> **Satisfies REQs:** R5
> **Footprint slice:** Modified: `main.py` (new route)
> **High-risk areas touched:** None — new, isolated route

### Description

Add a new admin-gated route that exports the same all-customers/driver data T4 renders as a table, as a CSV — one row per driver, same columns, same shape as the on-screen table.

### Test Plan

#### Test File(s)
- `tests/test_admin_customers.py`

#### Test Scenarios

##### Export Shape

- **export_matches_table_shape** — GIVEN the same customer/voucher fixtures as T4's row-shape scenarios WHEN GET the export route THEN the CSV has one row per driver, columns Customer Name/Number/Email/Driver Name, exactly matching T4's table _(verifies R5)_
- **export_includes_zero_booking_customer_as_blank_driver_row** — GIVEN a customer with no vouchers WHEN exported THEN 1 row with blank Driver Name _(REQ edge case, mirrors T4)_

##### Auth

- **export_requires_admin** — GIVEN no session/key WHEN GET THEN redirected to login, consistent with all other admin export routes _(auth regression guard)_

##### Regression Guard

- **existing_export_routes_unaffected** — `admin_customer_export` and `admin_bookings_export` (T3) behavior unchanged by adding this new route

### Implementation Notes

- **Module(s):** `main.py`
- **Pattern reference:** existing `admin_bookings_export`/`admin_customer_export` CSV-download pattern (`pd.DataFrame(...).to_csv(...)`, `send_file(..., as_attachment=True)`)
- **Key decisions:** ARCH A5 (separate route, not a param on an existing export)
- **Libraries:** pandas (already a dependency)
- **High-risk callouts:** none — reuses T4's row-building logic (extract as a shared helper if T4's implementation makes that natural; not required)

### Scope Boundaries

- Do NOT modify `admin_bookings_export` or `admin_customer_export` (T3's scope, already landed).
- Do NOT add any UI beyond the export button/link itself (styling/placement is minimal, matching existing "Export Bookings CSV" button pattern).

### Files Expected

**Modified files:**
- `main.py` (new export route)
- `templates/admin_customer_lookup.html` (add export button/link, if not already added by T4)

**Modified test files:**
- `tests/test_admin_customers.py`

**Must NOT modify:**
- `main.py` (`admin_customer_export`, `admin_bookings_export` — T3's scope, already landed)
