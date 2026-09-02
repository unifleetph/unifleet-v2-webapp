# Tasks

## Task T1: Add `deleted_at` column to vouchers (schema + models)

> **Status:** done
> **Verification:** checklist
> **Effort:** xs
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** R4, R8 (foundation for)
> **Footprint slice:** Modified: `models.py`, `db/schema.sql`, `db/postgres_repo.py`
> **High-risk areas touched:** None directly — enables the M-risk area (supplier-facing exports) in T3

### Description

Add a nullable `deleted_at` TIMESTAMPTZ column to the `vouchers` table so orders can be soft-deleted (row retained, hidden from view) instead of hard-deleted. Additive-only migration, following the exact precedent already in `db/schema.sql` for `fuel_type`/`requested_total_php`.

### Verification Checklist

- [ ] `models.py` `VOUCHER_COLUMNS` includes `"deleted_at"` — expected: present in the list
- [ ] `db/schema.sql` `vouchers` `CREATE TABLE` block includes `deleted_at TIMESTAMPTZ`, plus `ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;` below the existing `fuel_type`/`requested_total_php` precedents — expected: both present
- [ ] `db/postgres_repo.py` `_TIMESTAMPTZ_COLUMNS` includes `"deleted_at"` — expected: present, so `update_voucher_fields` normalizes it correctly on write
- [ ] `tests/test_schema.py` `TIMESTAMP_COLUMNS` list includes `"deleted_at"` — expected: added
- [ ] `poetry run pytest tests/test_schema.py -v` — expected: all pass, including `test_vouchers_has_all_voucher_columns` and `test_vouchers_timestamp_columns_are_timestamptz` covering the new column
- [ ] `poetry run pytest tests/test_apply.py -k idempotent` — expected: still passes (additive `ADD COLUMN IF NOT EXISTS` doesn't break re-apply on an already-migrated DB)
- [ ] `make test-db` — expected: full suite green, no regressions
- [ ] `make verify` — expected: exits 0

### Implementation Notes

- **Module(s):** `models.py` (`VOUCHER_COLUMNS`), `db/schema.sql` (Postgres), `db/postgres_repo.py` (`_TIMESTAMPTZ_COLUMNS`)
- **Pattern reference:** `db/schema.sql` — the existing `fuel_type`/`requested_total_php` `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` entries are the exact pattern to replicate (nullable, idempotent, historical rows stay NULL)
- **Key decisions:** ARCH A1 — nullable TIMESTAMPTZ, not a boolean flag; `NULL` = visible, non-null = soft-deleted
- **Libraries:** none new
- **High-risk callouts:** none — this task is pure schema; the soft-delete logic and exclusion filtering live in T2/T3

### Scope Boundaries

- Do NOT add any delete route or filtering logic (T2/T3's scope)
- Do NOT backfill historical rows — nullable is correct; all pre-existing rows get `deleted_at = NULL`
- Only add the one column; no other schema changes

### Files Expected

**Modified files:**
- `models.py` — add `"deleted_at"` to `VOUCHER_COLUMNS`
- `db/schema.sql` — add column to `CREATE TABLE` block + `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
- `db/postgres_repo.py` — add `"deleted_at"` to `_TIMESTAMPTZ_COLUMNS`
- `tests/test_schema.py` — add `"deleted_at"` to `TIMESTAMP_COLUMNS`

**Must NOT modify:**
- `main.py`, `persistence.py` (CSVRepo needs no change — it derives columns dynamically from `VOUCHER_COLUMNS`)

---

## Task T2: Delete-order backend route

> **Status:** done
> **Verification:** tdd
> **Effort:** s
> **Priority:** high
> **Depends on:** T1
> **Satisfies REQs:** R4, R5, R6, R7
> **Footprint slice:** Modified: `main.py` — extract `_delete_voucher_pngs()` helper from `delete_png()`, new `admin_orders_delete()` route
> **High-risk areas touched:** None M/H directly — this route's own risk is scoped L per ARCH; T3's exclusion filtering carries the M-risk area

### Description

New admin-only `POST /admin/orders/<voucher_id>/delete` route that soft-deletes an order: blocks the delete if status is "Redeemed", otherwise sets `deleted_at`, removes the two QR PNG files (via a helper extracted from the existing `delete_png()` route so both share the same cleanup logic), and writes one `audit_log` entry. Missing voucher_id flashes and redirects instead of erroring.

### Test Plan

#### Test File(s)
- `tests/test_admin_delete_order.py` (new — mirrors `tests/test_admin_stations.py`'s `client`/`_login`/`FakeRepo` fixture pattern, `FakeRepo` extended with `get_voucher`/`update_voucher_fields`)

#### Test Scenarios

##### Soft-delete happy path

- **soft-deletes a non-Redeemed order** — GIVEN a voucher with status "Unredeemed" WHEN POST `/admin/orders/<id>/delete` THEN `repo.update_voucher_fields` is called with `deleted_at` set, and the response redirects to `/admin` _(verifies R4)_
- **deletes both PNG files** — GIVEN the voucher's PNGs exist on disk WHEN deleted THEN both are removed via `_delete_voucher_pngs` _(verifies R4)_
- **writes one audit_log entry** — GIVEN a successful delete THEN `append_audit("delete_order", voucher_id, from_status=<status>, to_status="Deleted")` is called exactly once _(verifies R4)_

##### Status gate

- **blocks deletion of a Redeemed order** — GIVEN status "Redeemed" WHEN POST THEN `update_voucher_fields` is NOT called, a flash error is shown, and the row is unchanged _(verifies R5)_

##### Missing voucher

- **missing voucher flashes and redirects, no 500** — GIVEN an unknown voucher_id WHEN POST THEN a flash message is shown and the response redirects to `/admin`, no unhandled exception _(verifies R7)_

##### Auth

- **requires admin auth** — GIVEN no admin session WHEN POST THEN redirect to `admin_login`, no delete performed _(matches existing admin-route convention)_

##### Regression guard — `delete_png()` unchanged

- **existing Delete PNGs behavior unaffected by helper extraction** — GIVEN the existing `/delete_png/<id>` test scenarios THEN they still pass unmodified after `_delete_voucher_pngs()` is extracted _(guards backward-regression risk for `main.py` `delete_png()`)_

### Implementation Notes

- **Module(s):** `main.py`
- **Pattern reference:** `admin_stations_delete()` (`main.py:1568`) for the guard-check → flash → redirect shape; `delete_png()` (`main.py:449`) for the PNG-removal loop being extracted
- **Key decisions:** ARCH A1 (`deleted_at` timestamp), A2 (nested route naming), A4 (shared PNG-delete helper), A5 (server-side status re-check), A6 (disabled button is T4's concern, not this task's — this task must still reject an active POST for Redeemed regardless of what the UI renders)
- **Libraries:** none new
- **High-risk callouts:** none M/H for this task specifically

### Scope Boundaries

- Do NOT add bulk/multi-select delete (REQ out of scope)
- Do NOT add restore/undo (REQ out of scope)
- Do NOT touch the exclusion-filtering read paths (T3's scope) — this task only performs the write
- Only the new route + the extracted helper in `main.py`

### Files Expected

**Modified files:**
- `main.py` — extract `_delete_voucher_pngs(voucher_id)` from `delete_png()`; add `admin_orders_delete(voucher_id)` route

**Must NOT modify:**
- `templates/admin.html` (T4's scope)
- Read-path filtering (`admin()`, `supplier_sheet_pdf()`, etc. — T3's scope)

### TDD Sequence

Write the Redeemed-blocked test first (defines the contract's core constraint), then the happy-path soft-delete test, then PNG-cleanup and audit-log tests, then the missing-voucher and auth tests.

---

## Task T3: Deleted-order exclusion across read paths

> **Status:** done
> **Verification:** tdd
> **Effort:** s
> **Priority:** high
> **Depends on:** T1 (not T2 — only needs `deleted_at` to exist, not the delete route itself)
> **Satisfies REQs:** R8
> **Footprint slice:** Modified: `main.py` — new `_exclude_deleted()` helper, applied at `admin()`, `supplier_sheet_pdf()`, `export_supplier_csv()`, `admin_customer_lookup()`, `admin_customer_export()`, `admin_bookings_export()`
> **High-risk areas touched:** Supplier-facing exports (Supplier PDF, supplier CSV) — **M** risk per ARCH Areas of Impact; an incorrect filter could leak a deleted order to a supplier or wrongly hide a legitimate one

### Description

A single `_exclude_deleted(vouchers)` helper filters out soft-deleted orders (`deleted_at` set), applied consistently at every read path that shows orders to an admin, customer, or supplier — the admin dashboard table, Supplier PDF export, supplier CSV export, customer lookup, per-customer export, and all-bookings export. Centralizing the rule in one helper avoids it silently diverging across call sites.

### Test Plan

#### Test File(s)
- `tests/test_voucher_exclude_deleted.py` (new — unit tests for the helper itself)
- `tests/test_admin_bookings_export.py` (extend — customer/bookings export exclusion)
- `tests/test_admin_customers.py` (extend — customer lookup exclusion)
- `tests/test_report_pdf.py` or a new `tests/test_supplier_sheet_pdf_route.py` (Supplier PDF route-level exclusion — mock `build_supplier_pdf` to capture its `vouchers` argument)
- new test module or extension covering `export_supplier_csv()` exclusion

#### Test Scenarios

##### `_exclude_deleted()` unit behavior

- **filters out rows with a set deleted_at** — GIVEN rows with `deleted_at=None`, unset key, `""` (CSV-world empty), and a real timestamp WHEN filtered THEN only the non-deleted rows remain _(verifies R8; handles both Postgres `NULL` and CSV `""` as "not deleted")_

##### Admin dashboard

- **admin() table excludes deleted orders** — GIVEN a soft-deleted voucher among recent vouchers WHEN `/admin` renders THEN its voucher_id is absent from the rendered table _(verifies R8 for the admin-table surface named in REQ R8)_

##### Supplier-facing exports (M-risk)

- **supplier_sheet_pdf() excludes deleted orders** — GIVEN a soft-deleted Unredeemed voucher WHEN `/supplier-sheet.pdf` builds THEN the `vouchers` list passed to `build_supplier_pdf` does not include it _(verifies R8, the REQ's named Supplier PDF surface)_
- **export_supplier_csv() excludes deleted orders** — GIVEN the same setup WHEN `/export_supplier_csv` runs THEN the output rows don't include the deleted voucher_id _(verifies consistency decision — Decisions Log #4)_

##### Customer-facing exports (consistency decision)

- **admin_bookings_export() excludes deleted orders** — GIVEN a soft-deleted voucher WHEN `/admin/bookings/export` runs THEN it's absent from the CSV _(verifies Decisions Log #4)_
- **admin_customer_export() excludes deleted orders** — same, for `/admin/customers/export` _(verifies Decisions Log #4)_
- **admin_customer_lookup() excludes deleted orders** — GIVEN a customer with a soft-deleted booking WHEN looked up THEN it's absent from the detail bookings list _(verifies Decisions Log #4)_

##### Regression guard

- **non-deleted rows unaffected at every site** — GIVEN a mix of deleted and non-deleted vouchers THEN all non-deleted rows still appear at all 6 read sites, unchanged from current behavior _(guards backward-regression risk across all touched read paths)_

### Implementation Notes

- **Module(s):** `main.py`
- **Pattern reference:** the existing per-route filter comprehensions already in these functions (e.g. `status == 'Unredeemed'` filter in `supplier_sheet_pdf()`, account-code filter in `admin_customer_lookup`/`admin_customer_export`) — `_exclude_deleted()` composes with these, doesn't replace them
- **Key decisions:** ARCH A3 — one shared helper applied at all 6 sites, not inline repetition; developer explicitly chose consistency over REQ's narrower literal 2-surface scope
- **Libraries:** none new
- **High-risk callouts:** Supplier PDF / supplier CSV are the M-risk area per ARCH — these are external-facing documents; test both the "deleted row absent" and "non-deleted rows still present" directions to catch either an under- or over-aggressive filter

### Scope Boundaries

- Do NOT apply `_exclude_deleted()` to `_station_has_bookings()` (`main.py:1509`) — deleted orders must still count as booking history for the station-delete guard, per ARCH's explicit "touched but not changed" note
- Do NOT change the CSV export column set (`_EXPORT_COLUMNS`) — only which rows appear changes
- Do NOT add a runtime safeguard/lint ensuring all future voucher-read call sites apply the filter (ARCH Open Question, explicitly deferred)

### Files Expected

**Modified files:**
- `main.py` — add `_exclude_deleted()` helper; apply at `admin()`, `supplier_sheet_pdf()`, `export_supplier_csv()`, `admin_customer_lookup()`, `admin_customer_export()`, `admin_bookings_export()`
- `tests/test_admin_bookings_export.py`, `tests/test_admin_customers.py` — extended with exclusion cases

**New files:**
- `tests/test_voucher_exclude_deleted.py` — unit tests for the helper

**Must NOT modify:**
- `main.py` `_station_has_bookings()` (`main.py:1509`) — deliberately excluded from this filter
- `main.py` `admin_orders_delete()` route itself (T2's scope, this task only reads)

---

## Task T4: Admin UI — Delete Order column, scroll, confirm, flash toasts

> **Status:** not started
> **Verification:** ui
> **Effort:** m
> **Priority:** high
> **Depends on:** T2 (route must exist to POST to)
> **Satisfies REQs:** R1, R2, R3, R6
> **Footprint slice:** Modified: `templates/admin.html` (scroll wrapper, new column/button, confirm dialog, disabled-for-Redeemed, toast-bridge flash block)
> **High-risk areas touched:** Admin dashboard — **L** risk per ARCH (internal-only UI, single consumer)

### Description

Add a "Delete Order" column to the far right of the admin table without resizing existing columns (wrap the table in a horizontal-scroll container), give it a visually distinct button style from "Delete PNGs", gate it with a confirm dialog, disable it for Redeemed rows, and add the admin table's first-ever flash-message rendering (toast-bridge pattern copied from `admin_stations.html`) so R5/R7's flash messages from T2 are actually visible.

### Verification Checklist

- [ ] "Delete Order" column renders as the last column, after "Delete PNGs"; existing columns' widths/order unchanged — expected: matches R1
- [ ] Table wrapped in a horizontal-scroll container; on a narrow viewport, existing columns render at full width and a scrollbar appears, scrolling reveals Delete Order — expected: matches R2
- [ ] Delete Order button visually distinct from Delete PNGs (different color/style — amber/outline vs. solid red) — expected: matches R3
- [ ] Clicking Delete Order (non-Redeemed row) triggers a `confirm()` dialog; Cancel sends no request, row unchanged — expected: matches R6
- [ ] Confirming sends the POST to T2's route, page redirects to `/admin`, deleted row no longer appears — expected: matches R4 end-to-end
- [ ] Redeemed-status rows show a disabled Delete Order button (no active form/POST) — expected: matches R5/ARCH A6
- [ ] Flash messages (success + error) render as toasts via the new toast-bridge block — expected: makes R5/R7's flash messages visible (admin.html had none before this task)
- [ ] Existing "Delete PNGs" button/behavior unchanged — expected: no regression from the T2 helper extraction being wired into this template
- [ ] Existing search filter (`filterTable()`) still works with the new column present — expected: no regression

#### Testable Seams
- Render: column presence/order, scroll-container wrapper present, initial button state per row status
- Conditional states: disabled vs. active Delete Order button by status; toast shown/hidden
- Handlers: confirm-dialog gate before POST submission, toast-bridge JSON parse/display on page load

### Implementation Notes

- **Module(s):** `templates/admin.html` (HTML + inline `<style>`)
- **Pattern reference:** `templates/admin_stations.html`'s toast-bridge block (`<script id="flash-data">` + toast JS) for the flash rendering; the existing `.delete-button`/Delete PNGs `<form>` (`admin.html:381-389`) as the structural pattern for the new column's form + confirm dialog
- **Key decisions:** ARCH A6 (disabled button for Redeemed, no dead-end POST), A8 (scroll wrapper, not column squeezing)
- **Libraries:** none new — vanilla JS/CSS, consistent with the rest of this file
- **High-risk callouts:** none M/H — this task's risk is scoped L per ARCH

### Scope Boundaries

- Do NOT add bulk/multi-select UI (REQ out of scope)
- Do NOT add a restore/undo button or UI (REQ out of scope)
- Do NOT change the existing "Delete PNGs" column's own behavior — only add styling/structure alongside it, and only to distinguish it visually (REQ R3 explicitly limits this to visual differentiation)
- Do NOT add a sticky header (not requested in this REQ — that was a different brief's ask, do not conflate)
- Only this one template file

### Files Expected

**Modified files:**
- `templates/admin.html` — scroll-container wrapper around `<table>`; new "Delete Order" `<th>`/`<td>` with distinct button class, confirm dialog, disabled state for Redeemed rows; toast-bridge flash block copied from `admin_stations.html`

**Must NOT modify:**
- `main.py` (T2/T3 already own the backend contracts this UI calls)
- `templates/admin_stations.html` (source of the pattern being copied, not itself changed)

---

_Status values: `not started` (defined, not picked up) | `in progress` (implementation underway) | `done` (verification evidence produced) | `blocked` (cannot proceed — see notes)._
