# Tasks

## Task T1: Backend — exclusion flag, toggle route, export filters

> **Status:** done
> **Verification:** tdd
> **Effort:** s
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** R1, R2, R3, R4, R5, R6
> **Footprint slice:** Modified: `db/schema.sql` (new `vouchers.excluded_from_supplier_report` column, dual declaration), `main.py` (new `admin_orders_toggle_report_exclusion` route, new `_exclude_from_supplier_report` helper, `export_supplier_csv` filter wiring), `daily_report.py` (`_is_live` gains the exclusion check)
> **High-risk areas touched:** None (all Areas of Impact rated Low)

### Description

Adds the new `excluded_from_supplier_report` column, the admin-gated toggle route that flips it (restricted to Redeemed orders), and wires the flag into both supplier-facing exports (`daily_report.py`'s daily PDF and `main.py`'s CSV export). This is the entire backend — nothing in T2 (the admin UI button) can be built or verified until this route and the export filters exist.

### Test Plan

#### Test File(s)
- `tests/test_schema.py` (existing file, new test added near `TIMESTAMP_COLUMNS`/`test_vouchers_has_all_voucher_columns`)
- `tests/test_admin_toggle_report_exclusion.py` (new, pattern: `tests/test_admin_delete_order.py`'s `FakeRepo`/`_login`/`client` fixtures)
- `tests/test_daily_report.py` (existing file, extend `test_the_send_time_builder_returns_a_dated_pdf_with_redeemed_orders`)
- `tests/test_supplier_exports_exclude_deleted.py` (existing file, new test added to the Supplier CSV section)

#### Test Scenarios

##### Schema

- **excluded_from_supplier_report column exists with the right type and default** — GIVEN a fresh `schema_db` WHEN its columns are inspected (via `_columns()`, the existing helper in `test_schema.py`) THEN `excluded_from_supplier_report` is `boolean` with a default of `false`, independent of `VOUCHER_COLUMNS` (which deliberately excludes it per ARCH decision A3) _(verifies R1's storage prerequisite)_

##### Toggle Route

- **toggling a Redeemed order sets the flag** — GIVEN a voucher with `status="Redeemed"` and the flag unset WHEN `POST /admin/orders/<id>/toggle-report-exclusion` is called (logged in) THEN the response is a 302 redirect, a success flash is shown, and the stored flag is now `True` _(verifies R1)_
- **toggling again reverses it** — GIVEN the same voucher now flagged `True` WHEN the route is called again THEN the flag flips back to `False` _(verifies R4)_
- **toggling a non-Redeemed order is rejected** — GIVEN a voucher with `status="Unredeemed"` (or any other non-Redeemed status) WHEN the route is called THEN the response is a 302 redirect with an error flash, and the flag is unchanged (no `update_voucher_fields` call made) _(verifies R5)_
- **toggling a missing voucher** — GIVEN no voucher exists for the given id WHEN the route is called THEN it redirects with an error flash, same shape as `admin_orders_delete`'s existing missing-voucher handling, and no update call is made _(REQ edge case, mirrors existing sibling-route behavior)_
- **unauthenticated request is rejected** — GIVEN no admin session WHEN the route is called THEN it redirects to `/admin/login` and no update call is made _(guards backward-regression risk: auth gate must short-circuit before any write, same as every sibling admin route)_

##### Daily Supplier PDF

- **excluded Redeemed order is omitted from the daily PDF, a non-excluded one still appears** — GIVEN the existing `RepoStub`'s voucher list plus one additional Redeemed voucher with `excluded_from_supplier_report=True` WHEN `main._build_daily_report_pdf` runs THEN the excluded voucher's id does not appear in the PDF text, while the existing non-excluded Redeemed voucher (`V3`) still does _(verifies R2, extends the existing test rather than duplicating its setup)_

##### Supplier CSV Export

- **excluded Redeemed order is omitted from the CSV, a non-excluded one still appears** — GIVEN two Redeemed vouchers, one with the flag set and one without, WHEN `GET /export_supplier_csv` is called THEN the CSV body contains the non-excluded voucher's id and does not contain the excluded one's id _(verifies R3)_

##### Regression Guard

- **existing deleted_at exclusion is unaffected** — the existing `test_supplier_pdf_excludes_deleted_orders` and `test_supplier_csv_excludes_deleted_orders` tests in `tests/test_supplier_exports_exclude_deleted.py` pass unmodified, proving the new filter is additive (AND) rather than a replacement _(guards backward-regression risk for `main.py::export_supplier_csv` and the existing `_exclude_deleted` path)_
- **existing daily-report behavior for non-excluded orders is unaffected** — the rest of `tests/test_daily_report.py`'s suite passes unmodified _(guards backward-regression risk for `daily_report.py::_is_live`)_
- **the sibling delete-order route is unaffected** — `tests/test_admin_delete_order.py` passes unmodified with zero changes, proving the new toggle route shares no mutable state with `admin_orders_delete` _(guards backward-regression risk for `main.py::admin_orders_delete`)_

### Implementation Notes

- **Module(s):** `main.py` and `daily_report.py` stay independent — no shared helper between them (ARCH Module Boundaries); each gets its own filter addition.
- **Pattern reference:** `main.py::admin_orders_delete` (~line 814) for the toggle route's auth check, 404/status-rejection flash+redirect shape, and lack of try/except around the write (ARCH decision A6 — consistency with this sibling route, not a new error-handling standard). `main.py::_exclude_deleted` (~line 341) for the new `_exclude_from_supplier_report` helper's shape. `db/schema.sql`'s `deleted_at` column (line ~103 CREATE, ~130 ALTER) for the dual-declaration convention.
- **Key decisions:** A1 (column, not a side table), A2 (single toggle route, not two one-directional routes), A3 (column left out of `models.py` `VOUCHER_COLUMNS` — do not add it there), A4 (filter added independently to each of `_is_live` and the new CSV helper, no shared cross-module predicate), A5 (no audit logging — do not call `append_audit`), A6 (error handling mirrors `admin_orders_delete` exactly).
- **Libraries:** none new — `repo.update_voucher_fields` (existing, generic) and the existing `psycopg`/schema-apply machinery handle everything.
- **High-risk callouts:** None — every Area of Impact for this task is rated Low.

### Scope Boundaries

- Do NOT add `excluded_from_supplier_report` to `models.py`'s `VOUCHER_COLUMNS` (ARCH decision A3).
- Do NOT add an `append_audit` call to the toggle route (ARCH decision A5 / REQ decision 4).
- Do NOT touch `/supplier-sheet.pdf` (`supplier_sheet_pdf`) — a separate, Unredeemed-only manual route explicitly out of scope.
- Do NOT touch `/api/v1/discounts` or any other JSON API route — out of scope per REQ.
- Do NOT add a try/except around the toggle route's write beyond what `admin_orders_delete` already has — consistency with the existing sibling route (A6), not new error-handling rigor.
- Only implement the column, the toggle route, and the two export filter additions — the admin UI button is T2's scope, not this task's.

### Files Expected

**Modified files:** _(from ARCH "Modified files / modules")_
- `db/schema.sql` (new `excluded_from_supplier_report BOOLEAN NOT NULL DEFAULT FALSE` column, dual `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN IF NOT EXISTS` declaration)
- `main.py` (new `admin_orders_toggle_report_exclusion` route; new `_exclude_from_supplier_report` helper; `export_supplier_csv` gains the new filter call)
- `daily_report.py` (`_is_live` gains the exclusion check)
- `tests/test_schema.py`, `tests/test_daily_report.py`, `tests/test_supplier_exports_exclude_deleted.py` (extended with new scenarios above)

**New files:**
- `tests/test_admin_toggle_report_exclusion.py` (new toggle-route test file, pattern: `tests/test_admin_delete_order.py`)

**Must NOT modify:** _(from ARCH "Touched but not changed")_
- `models.py` (`VOUCHER_COLUMNS`) — silent-regression hotspot; must stay unchanged per A3
- `db/postgres_repo.py` — already generic/`SELECT *`, needs zero change; modifying it would signal scope drift
- `templates/admin.html` — T2's scope, not this task's

---

## Task T2: Admin UI — toggle button on Redeemed rows

> **Status:** not started
> **Verification:** ui
> **Effort:** xs
> **Priority:** medium
> **Depends on:** T1 (needs the real toggle route to post to)
> **Satisfies REQs:** R1, R4, R5, R6
> **Footprint slice:** Modified: `templates/admin.html` (Redeemed-row branch, ~line 500-507)
> **High-risk areas touched:** None (Low risk — confirmed no existing test locks the exact disabled-button markup this change sits beside)

### Description

Adds the toggle button to the admin dashboard, inside the existing Redeemed-row branch that already shows the disabled "Delete Order" button. The button's label reflects the order's current exclusion state and posts to T1's toggle route — no JavaScript needed, same plain-`<form>`-POST pattern as every other action in this table.

### Verification Checklist

#### Testable Seams
(component/render tests, Flask test client + HTML assertion — mirrors `tests/test_admin_display_layout.py`'s existing style)

- **Redeemed row, flag unset** — render `/admin` with a Redeemed voucher row where `excluded_from_supplier_report` is `False`/absent — expected: response HTML contains the existing disabled `Delete Order` button unchanged, AND a new `<form>` posting to `/admin/orders/<id>/toggle-report-exclusion` whose submit button reads **"Exclude from supplier report"**
- **Redeemed row, flag set** — same row with `excluded_from_supplier_report=True` — expected: the same toggle form is present, submit button now reads **"Include in supplier report"**
- **Non-Redeemed row** — a row with `status="Unredeemed"` (or any other non-Redeemed status) — expected: no toggle form/button rendered at all; only the existing enabled `Delete Order` form appears, unchanged from today

#### Human Checklist
(one real click-through against the running app, evidence = screenshot or terminal confirmation of the redirect/flash)

- **Exclude click** — on a real Redeemed order's row, click "Exclude from supplier report" — expected: page redirects back to `/admin`, a success flash is shown, and that row's button now reads "Include in supplier report"
- **Include click (reverse)** — click the same button again — expected: it flips back to "Exclude from supplier report", success flash shown

### Implementation Notes

- **Module(s):** `templates/admin.html` only — no Python/route changes (T1 already provides the route).
- **Pattern reference:** the existing Redeemed-row branch at `templates/admin.html:500-507` — the new form sits alongside the existing disabled `Delete Order` button, in the same `<td>`, same `<form method="POST">` + `<button type="submit">` shape as every other action in this table (no new `<th>`/column needed).
- **Key decisions:** per the generate-tasks conversation — no `confirm()` dialog on this button (unlike Delete Order's), since the action is reversible and low-stakes (matches ARCH's own framing of the toggle as "low-stakes, no audit").
- **Libraries:** none — plain HTML form, no JS.
- **High-risk callouts:** None.

### Scope Boundaries

- Do NOT add a new `<th>`/table column — the button lives inside the existing "Delete Order" cell for Redeemed rows.
- Do NOT add a `confirm()` dialog — explicitly decided against for this reversible, low-stakes action.
- Do NOT render the toggle button/form for any non-Redeemed row.
- Only implement the template change — the route and filters are T1's scope, already done by the time this task starts.

### Files Expected

**Modified files:** _(from ARCH "Modified files / modules")_
- `templates/admin.html` (Redeemed-row branch gains the toggle form/button)
- A test file covering the three render seams above (new or extending `tests/test_admin_display_layout.py`, developer's call at implementation time — either location follows an existing project pattern)

**Must NOT modify:**
- `main.py`, `daily_report.py`, `db/schema.sql` — T1's scope, already complete
