# Architecture: Booking Confirmation Notice & Admin Station Delete

> **Date:** 2026-08-26
> **Phase:** 2 of 5 (System Architecture)
> **Requirements source:** specs/requirements/REQ-booking-confirmation-note-and-station-delete.md
> **Type:** feature

## Architecture Summary

Two isolated, template/route-level changes on top of the existing single-file Flask app. Part A edits static copy in `booking_success.html` only — no backend change. Part B adds one new Postgres-backed function (`price_store.delete_station`) and one new admin route (`/admin/stations/<id>/delete`) that follow the exact pattern of the existing `deactivate`/`reactivate` routes, plus a booking-history guard implemented at the application layer (not a DB FK) because bookings reference stations by name string, not by the `station_id` FK column.

## High-Level Structure

```
Part A (copy only):
  templates/booking_success.html  →  edited in place, no other layer touched

Part B (delete flow):
  templates/admin_stations.html  --POST--> main.py:/admin/stations/<id>/delete
                                              │
                                              ├─ 1. booking-history check
                                              │     persistence.get_repo().list_all_vouchers()
                                              │     filter by voucher["station"] == station["name"]
                                              │     → match found: flash error, no writes, return
                                              │
                                              └─ 2. price_store.delete_station(id)
                                                    (Postgres transaction)
                                                    delete price_history, discount_history,
                                                    prices, discounts WHERE station_id = id
                                                    → delete FROM stations WHERE id = id
                                                    → flash success
```

Existing `deactivate`/`reactivate`/`edit` routes are untouched; `delete` is additive, following their exact shape (`require_admin` guard, `_admin_stations_back()` redirect, flash-based feedback).

## Tech Choices

| Area                     | Decision                                                        | Alternatives Considered                                  | Rationale                                                                 |
|---------------------------|-------------------------------------------------------------------|--------------------------------------------------------------|--------------------------------------------------------------------------|
| Booking-history check     | Application-level check via `persistence.get_repo().list_all_vouchers()`, matched by station name | Rely on the `station_id` FK constraint in Postgres        | The `/book` flow never populates `station_id` on vouchers — only the denormalized `station` name column is written (main.py:1175). An FK-only guard would silently pass even when bookings exist. Also must work under both `PERSISTENCE_BACKEND=csv` and `=db` since `persistence.py` abstracts that, and price_store's own Postgres tables don't see CSV-backed bookings at all. |
| Station row deletion      | New `price_store.delete_station(id)`, one Postgres transaction cascading child rows then the station row | Add `ON DELETE CASCADE` to the FK definitions in schema.sql | Changing FK cascade behavior is a schema-wide change with broader blast radius than this feature warrants; an explicit application-level cascade in one function keeps the change scoped and auditable. |
| Delete confirmation       | Client-side `confirm()` on the existing form submit, no new modal component | Server-side two-step confirm page | Matches zero other admin-stations interaction patterns in this codebase (none use a confirm modal); a JS confirm is the lowest-footprint option consistent with how destructive-ish admin actions are otherwise unguarded here. |

## Patterns & Conventions

- **Route-per-action, flash-redirect** — every `/admin/stations/*` mutation route (`edit`, `deactivate`, `reactivate`) does its work then calls `_admin_stations_back()` after setting a flash message; `delete` follows this exactly.
- **`require_admin(request)` guard first line of every admin route** — followed for the new route.
- **`price_store` owns all station/price/discount Postgres access; `main.py` never issues SQL directly** — `delete_station` lives in `price_store.py`, not inline in the route.
- **`KeyError` signals "not found" from `price_store` mutators** (`set_station_active` does this today) — `delete_station` raises `KeyError` the same way; the route catches it and calls `abort(404)`, matching `deactivate`/`reactivate`.

## Data Models

### stations (existing, no schema change)

**Purpose:** Identity of a fuel station; `is_active` gates visibility on `/book` and price listings.

**Relationships (existing, unchanged):**
- `prices`, `discounts`, `price_history`, `discount_history` — FK `station_id → stations.id`, no cascade. Every station has at least a `prices` row per priced fuel type from creation, so these always exist and must be cleared before the station row can be deleted.
- `vouchers.station_id` — FK `station_id → stations.id`, declared but **not populated** by the current booking flow (bookings write the denormalized `vouchers.station` name string instead — see main.py:1175, templates/book.html station `<select>` value = station name). This FK is not a reliable delete guard.

**Lifecycle addition:** `active → deactivated → deleted` (new terminal state). Deletion is only reachable from `deactivated`; an active station has no delete affordance (R3).

## API Contracts / Interfaces

### Admin Stations (Flask routes in main.py)

**Boundary:** HTTP, form-POST, session-cookie admin auth (`require_admin`)

| Method/Op | Path                                  | Purpose                                    | Errors / Returns |
|-----------|----------------------------------------|---------------------------------------------|-------------------|
| POST      | `/admin/stations/<station_id>/delete`  | Delete a deactivated station with no booking history | 404 if `station_id` unknown; redirect + flash "Cannot delete: station has existing bookings." if a voucher name-matches the station; redirect + flash success otherwise |

**Auth requirements:** Same as every other `/admin/stations/*` route — `require_admin(request)`, redirect to `admin_login` if not authenticated.

### price_store.py (module-internal API addition)

| Op                          | Signature                          | Purpose                                                     | Errors / Returns |
|------------------------------|--------------------------------------|----------------------------------------------------------------|-------------------|
| `delete_station`             | `delete_station(station_id: str) -> None` | Deletes a station's price/discount rows then the station row, in one transaction | Raises `KeyError` if `station_id` doesn't exist |

## Module Boundaries

| Module              | Responsibility                                              | Allowed Dependencies                     |
|----------------------|--------------------------------------------------------------|--------------------------------------------|
| `main.py`            | HTTP route, admin-auth guard, booking-history check, flash messaging | `price_store`, `persistence` (unchanged boundary) |
| `price_store.py`     | All station/price/discount Postgres reads and writes         | Postgres connection pool (unchanged boundary) |
| `persistence.py`     | Booking (voucher) read/write, backend-agnostic (CSV or DB)   | No new dependency — `main.py` calls its existing `get_repo().list_all_vouchers()` |

Rule preserved: the booking-history check reads through `persistence.py`'s existing repo abstraction rather than `price_store` or raw SQL reaching into `vouchers` — keeps station and booking storage concerns separate, as they are today.

## Change Footprint

### New files / modules

None — no new files. `delete_station` is a new function inside the existing `price_store.py`; the delete route is a new function inside the existing `main.py`.

### Modified files / modules

| Path                                 | What changes here                                                                                     |
|----------------------------------------|----------------------------------------------------------------------------------------------------------|
| `price_store.py`                      | Add `delete_station(station_id)` — transaction: delete `price_history`, `discount_history`, `prices`, `discounts` rows for `station_id`, then delete the `stations` row; raise `KeyError` if not found. |
| `main.py`                              | Add `POST /admin/stations/<station_id>/delete` route, placed directly above/below the existing `reactivate` route: `require_admin` guard → booking-name check via `persistence.get_repo().list_all_vouchers()` → block-with-flash or call `price_store.delete_station()` → flash + `_admin_stations_back()`. |
| `templates/admin_stations.html`        | Inside the existing `{% if not s.is_active %}` (deactivated-row) block, add a delete `<form>`/button styled like the existing deactivate/reactivate buttons, with a `confirm()` prompt on submit. |
| `templates/booking_success.html`       | Replace the `<p class="success-note">Your refuel request is now pending payment...</p>` paragraph with an info-box element (icon + the three-line note text from R1), matching the reference screenshot's style. Add a new element between the QR `<img>` and the existing `<p class="qr-caption">` reading "FREE BDO Transfer using BDO App", styled as a badge/button per the screenshot; the existing `qr-caption` paragraph is kept unchanged below it. |

### Deleted / replaced

None.

### Touched but not changed (silent-regression hotspots)

| Path                                          | Why it matters                                                                                     |
|--------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| `tests/test_admin_stations.py`                   | Uses a `fake_price_store` fixture standing in for `price_store` in route tests — will need a `delete_station` stub added for new route tests to run (flagged for Phase 3, not implemented here). |
| `tests/test_book_and_booking_success_copy.py`    | Asserts `"Scan with your banking app (InstaPay) to pay."` is present in `booking_success.html` body — this line is kept, not removed, so the existing assertion keeps passing; new assertions for the two new notes are additive (Phase 3/4 work). |

## Areas of Impact

| Area                          | Impact                                                              | Risk (L/M/H) | Why                                                                 |
|---------------------------------|--------------------------------------------------------------------|--------------|--------------------------------------------------------------------|
| `/admin/stations` page          | New destructive action available to admins                          | M            | Irreversible data removal; mitigated by deactivated-only gating + confirm dialog + booking-history guard |
| Booking confirmation page       | Copy-only change, no logic                                          | L            | Static template text/styling, no route or data change |
| Postgres `stations`/`prices`/`discounts`/`price_history`/`discount_history` tables | New delete path touching 5 tables in one transaction | M | First code path that ever deletes rows from these tables (previously insert/update only) — verify transaction rollback on partial failure |

**Contract changes:** None — no existing HTTP response shape, admin route, or public API changes; the new route is purely additive.

**Cross-cutting ripples:** None on auth (reuses `require_admin`), telemetry, feature flags, or build pipeline. No schema migration needed — no new tables/columns, only new application-level DML against existing tables.

## Cross-Cutting Concerns

- **Errors:** Delete route catches `KeyError` from `price_store.delete_station` → `abort(404)`, matching `deactivate`/`reactivate`. Any other exception (e.g. Postgres connection failure) is caught and flashed as `f"Failed to delete station: {e}"`, matching the existing `try/except Exception` pattern in the create/edit routes — the transaction rolls back automatically, station row is untouched.
- **Logging & metrics:** No new logging infra — consistent with the rest of `/admin/stations`, which relies on flash messages only, not structured logs.
- **Auth / authz:** `require_admin(request)` at the top of the new route, identical to every other `/admin/stations/*` route. No new auth surface.
- **Performance:** Deletes are scoped to a single `station_id` across indexed FK columns (`idx_price_history_station_id` etc.) — O(rows for that station), not a table scan, regardless of table size.
- **Security:** `station_id` comes from the URL path and is only ever used in parameterized queries (matching existing `price_store` conventions) — no injection surface introduced.
- **Migrations / rollout:** No schema change, no data migration. Safe to deploy directly; no backward-compat concern since this is a net-new route and a net-new function.

## Architecture Decisions Log

| #   | Decision                                                                 | Alternatives                                             | Chosen Because                                                                 | Satisfies REQs |
|-----|----------------------------------------------------------------------------|----------------------------------------------------------|---------------------------------------------------------------------------------|----------------|
| A1  | Booking-history guard checks `voucher["station"] == station["name"]` via `persistence.get_repo()`, not the `station_id` FK | FK-constraint-only guard | `station_id` is unpopulated by the booking flow; FK alone would let deletion proceed even with active booking history | R6 |
| A2  | `delete_station` cascades `price_history`/`discount_history`/`prices`/`discounts` deletion for that station inside the same transaction as the station-row delete | Add `ON DELETE CASCADE` to schema; block delete entirely if any price/discount row exists | Every station always has these rows by design — blocking on them would make delete never work; changing FK cascade in schema.sql is a wider-blast-radius change than this feature needs | R5 |
| A3  | Delete button rendered only for `not s.is_active` rows in `admin_stations.html` | Always-visible button, disabled for active stations | Matches REQ R3 exactly; keeps active-station rows visually unchanged | R3 |
| A4  | Confirmation via inline JS `confirm()` on form submit, no new modal | Server-rendered two-step confirm page | Lowest-footprint option; no existing confirm-modal pattern in this codebase to extend | R4 |
| A5  | Top note (R1) replaces the existing `success-note` paragraph; QR note (R2) is a new element added before the existing `qr-caption`, which is kept | Add R1 note alongside old paragraph; replace `qr-caption` entirely | Confirmed with developer: screenshot shows one info box (not two), but two elements around the QR (badge + existing caption) | R1, R2 |

## Risk & Stress-Test Scenarios

### Forward — runtime failure scenarios

| Scenario                                                                 | How the Design Handles It                                                                                          |
|-----------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|
| Postgres connection drops mid-delete-transaction                            | Transaction rolls back automatically (no partial cascade); route catches the exception, flashes failure, station row untouched. |
| Admin double-clicks delete, two requests race on the same `station_id`      | Second request's `DELETE FROM stations WHERE id = ...` affects 0 rows / `price_store` raises `KeyError` → route returns 404, matching existing `deactivate`/`reactivate` double-submit behavior. |
| A booking is created for this station between the history-check and the delete call | Not reachable: `price_store.list_stations()` (used to populate the `/book` dropdown) excludes inactive stations by default, and delete is only offered on already-deactivated stations — no new booking can target it in this window. |
| Station has thousands of `price_history` rows (long-lived, frequently repriced station) | Delete is scoped by indexed `station_id` FK columns — bounded to that station's rows, not a full-table scan. |

### Backward — regression risk per touched area

| Touched area                          | What could regress                                                          | How we'd know / mitigation                                                     |
|-----------------------------------------|------------------------------------------------------------------------------|----------------------------------------------------------------------------------|
| `admin_stations.html` deactivated-row block | New delete button/form breaks existing deactivate/reactivate button layout or styling | Existing tests asserting inactive-row rendering (`test_get_admin_stations_renders_inactive_row_css_class`) plus visual check in Phase 4 |
| `booking_success.html`                  | Removing the old `success-note` paragraph could break a hidden assertion elsewhere | Grepped `tests/` for `success-note` / the old copy string — no matches found; existing `qr-caption` and InstaPay assertions are unaffected since those lines are kept |
| `price_store.py`                        | New `delete_station` accidentally deletes rows for the wrong station if `station_id` param isn't parameterized correctly | Follow existing parameterized-query pattern used by every other function in this file (e.g. `set_station_active`) |

## Open Questions

- Exact wording of the "cannot delete" flash message.
  - **Impact if unresolved:** Minor copy variance.
  - **Suggested default:** `"Cannot delete: station has existing bookings."` (from the REQ's suggested default).

## Out of Scope

- Station deactivation flow itself (reason: already exists, unrelated to this change).
- Bulk/multi-station delete (reason: not requested).
- Schema changes to FK cascade behavior (reason: broader blast radius than this feature warrants; handled at application level instead — see A2).

---

# Tasks

## Task T1: Booking confirmation page notes

> **Status:** done
> **Verification:** test-after
> **Effort:** xs
> **Priority:** medium
> **Depends on:** None
> **Satisfies REQs:** R1, R2
> **Footprint slice:** Modified: `templates/booking_success.html`
> **High-risk areas touched:** None — "Booking confirmation page" area of impact is rated L (copy-only, no logic)

### Description

Replace the current pending-payment paragraph on the booking confirmation page with an info-box note reassuring the customer their transaction is being processed, and add a new note between the QR code and its existing caption pointing out the free BDO transfer option. Both texts and their placement come from REQ R1/R2 and the reference screenshot (`docs/Screenshot from 2026-08-26 15-49-04.png`).

### Test Plan

#### Test File(s)
- `tests/test_book_and_booking_success_copy.py`

#### Test Scenarios

##### Booking Confirmation Notes

- **top note replaces old paragraph** — GIVEN the booking confirmation page renders WHEN the body is inspected THEN it contains "Thank you for ordering from UniFleet." and "We are working to confirm your transaction." and "We'll get back to you as soon as we can." AND does NOT contain "Your refuel request is now pending payment" _(verifies R1)_
- **QR note text present** — GIVEN the page renders THEN body contains "FREE BDO Transfer using BDO App" _(verifies R2)_
- **QR note placed between QR image and existing caption** — GIVEN the rendered HTML THEN "FREE BDO Transfer using BDO App" appears before "Scan with your banking app (InstaPay) to pay." in document order _(verifies R2 placement)_

##### Regression Guard

- **existing InstaPay payment copy untouched** — GIVEN the page renders THEN body still contains "Scan with your banking app (InstaPay) to pay.", "Send to INSTAPAY", "000228034271" _(guards backward-regression risk for `templates/booking_success.html` — existing assertions in this file)_
- **existing PESONet/GoTyme absence untouched** — GIVEN the page renders THEN body still excludes "PESONet", "GoTyme", "payment_qr.png" _(guards backward-regression risk)_

### Implementation Notes

- **Module(s):** `templates/booking_success.html` only — no route or backend change (per ARCH High-Level Structure, Part A is copy-only).
- **Pattern reference:** existing `.payment-instructions` / `.payment-qr` / `.qr-caption` blocks and inline `<style>` in the same file — extend that style block rather than introducing a new CSS file.
- **Key decisions:** A5 — top note *replaces* the old `success-note` paragraph; QR note is a *new* element added before the existing `qr-caption`, which is kept unchanged.
- **Libraries:** None — plain Jinja/HTML/CSS, no new dependency.

### Scope Boundaries

- Do NOT touch the `payment-instructions` box (amount due, INSTAPAY account details) — unchanged per ARCH.
- Do NOT remove or reword the existing `qr-caption` paragraph — it stays, the new note goes above it.
- Only implement the two note additions described in R1/R2 — no other visual changes to this page.

### Files Expected

**Modified files:**
- `templates/booking_success.html` (replace `success-note` paragraph with info-box; add new note element between QR image and `qr-caption`)

**Must NOT modify:**
- `main.py` (booking_success route) — out of scope, no logic change needed for this task

---

## Task T2: Station delete — backend logic

> **Status:** not started
> **Verification:** tdd
> **Effort:** m
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** R3, R5, R6
> **Footprint slice:** Modified: `price_store.py` (add `delete_station`); Modified: `main.py` (add `POST /admin/stations/<station_id>/delete`)
> **High-risk areas touched:** "Postgres `stations`/`prices`/`discounts`/`price_history`/`discount_history` tables" (M) — first delete path against these tables; addressed by the atomicity test below. "/admin/stations page" (M) — new destructive admin action; addressed by the booking-guard and 404 tests below.

### Description

Add `price_store.delete_station(station_id)`, which deletes a station's `price_history`/`discount_history`/`prices`/`discounts` rows and then the `stations` row itself in one transaction, raising `KeyError` if the station doesn't exist. Add the `POST /admin/stations/<station_id>/delete` route in `main.py`, which checks for existing booking history (by matching `voucher["station"]` against the station's name via `main.repo.list_all_vouchers()`) before calling `delete_station`, blocking with a flash message if any match is found.

### Test Plan

#### Test File(s)
- `tests/test_price_store.py`
- `tests/test_admin_stations.py`

#### Test Scenarios

##### price_store.delete_station

- **removes a bare station** — GIVEN `test_station` with zero price rows WHEN `delete_station(test_station)` is called THEN it no longer appears in `list_all_stations(include_inactive=True)` _(verifies R5)_
- **cascades prices + price_history** — GIVEN `test_station` with `set_price()` called once (creates a `prices` row and a `price_history` row) WHEN `delete_station` is called THEN no exception is raised AND a direct query shows zero `prices`/`price_history` rows for that station_id _(verifies R5, A2)_
- **cascades discounts + discount_history** — GIVEN `test_station` with a `discounts` row and a `discount_history` row inserted directly via SQL WHEN `delete_station` is called THEN both tables have zero rows for that station_id afterward _(verifies R5, A2)_
- **unknown id raises KeyError** — GIVEN a station id that does not exist WHEN `delete_station` is called THEN `KeyError` is raised, matching the `set_station_active` convention _(supports R3's 404 path)_
- **atomic rollback on partial failure** — GIVEN `test_station` with price rows AND a `vouchers` row whose `station_id` FK still points at it (simulating a stray reference) WHEN `delete_station` is called THEN it raises AND the station, its prices, and its price_history rows are all still present afterward — no partial cascade _(forward-stress: transaction atomicity)_

##### POST /admin/stations/<id>/delete

- **delete succeeds, no booking history** — GIVEN a deactivated station in `FakePriceStore` and `main.repo` stubbed with no vouchers matching its name WHEN `POST /admin/stations/<id>/delete` THEN `delete_station` is called with that id and a success flash is set _(verifies R5)_
- **delete blocked, matching voucher name** — GIVEN `main.repo` stubbed with a voucher whose `station` field exactly matches the target station's name WHEN `POST /admin/stations/<id>/delete` THEN `delete_station` is NOT called, and the flash is "Cannot delete: station has existing bookings." _(verifies R6, exact case-sensitive match)_
- **unknown station id returns 404** — matches the existing `deactivate`/`reactivate` 404 convention
- **delete requires admin** — add `("post", "/admin/stations/some_id/delete")` to the existing `test_all_station_routes_require_admin` parametrization _(regression guard + new coverage)_
- **preserves `?key=` on redirect** — matches the existing `preserves_key_query_param` tests for `create`/`edit`/`deactivate`/`reactivate`
- **`delete_station` exception is flashed, not a 500** — GIVEN `delete_station` raises a generic `Exception` WHEN `POST /admin/stations/<id>/delete` THEN the response flashes `"Failed to delete station: ..."` and redirects, matching the existing create/edit store-error-flash convention

### Implementation Notes

- **Module(s):** `price_store.py` owns all station/price/discount Postgres access; `main.py` owns the HTTP route and the booking-history check via `persistence`'s `main.repo` singleton (module-level `repo = get_repo(PERSISTENCE_BACKEND)` at `main.py:127`).
- **Pattern reference:** `price_store.set_station_active` (transaction shape, `KeyError` convention); `admin_stations_deactivate`/`admin_stations_reactivate` routes (guard → try/except → flash → `_admin_stations_back()`).
- **Key decisions:** A1 (booking guard by name via `persistence`, not the `station_id` FK), A2 (cascade delete in one transaction, not a schema `ON DELETE CASCADE` change).
- **Libraries:** `psycopg` (already used throughout `price_store.py`) — no new dependency.
- **High-risk callouts:** the atomicity test above is the direct mitigation for the M-risk "first delete path against these tables" area — it proves a partial cascade can't silently corrupt state. The booking-guard tests are the direct mitigation for the M-risk "new destructive admin action" area.

### Scope Boundaries

- Do NOT add `ON DELETE CASCADE` to `db/schema.sql` — cascade is handled at the application level in `delete_station` (A2).
- Do NOT check the `vouchers.station_id` FK column for the booking-history guard — it is not populated by the current booking flow (A1).
- Do NOT implement bulk/multi-station delete — out of scope per ARCH.
- Only add the one new route and one new `price_store` function — no changes to `edit`/`deactivate`/`reactivate`.

### Files Expected

**Modified files:**
- `price_store.py` (add `delete_station(station_id)`)
- `main.py` (add `POST /admin/stations/<station_id>/delete` route)

**Must NOT modify:**
- `db/schema.sql` (no FK/cascade change — out of scope per A2)
- `templates/admin_stations.html` (owned by T3)

### TDD Sequence

1. `price_store.delete_station` scenarios first (bare station → cascades → KeyError → atomicity) — the route depends on this function existing and behaving correctly.
2. Route scenarios against `FakePriceStore`/`main.repo` stub second.

---

## Task T3: Admin stations delete button

> **Status:** not started
> **Verification:** ui
> **Effort:** s
> **Priority:** medium
> **Depends on:** T2 (route must exist for the button's action and for end-to-end manual verification)
> **Satisfies REQs:** R3, R4
> **Footprint slice:** Modified: `templates/admin_stations.html`
> **High-risk areas touched:** "/admin/stations page" (M) — mitigated here by deactivated-only gating and confirm-dialog checklist items (the destructive-action risk itself is primarily mitigated by T2's server-side guard).

### Description

Add a delete button to the deactivated-station row in `/admin/stations`, following the existing `Deactivate`/`Reactivate` form pattern exactly, with a client-side confirm prompt before the POST fires.

#### Testable Seams
- Conditional rendering (delete form present only for `not s.is_active` rows)
- Form action URL construction (including `?key=` passthrough)

### Verification Checklist

#### Component tests (`tests/test_admin_stations.py`, via `captured_templates`)
- **delete button rendered only for inactive stations** — expected: given a station list with one active + one inactive station, the delete form/button appears only in the inactive row's rendered HTML, never the active row's
- **delete form posts to correct URL with key param** — expected: given `?key=testkey`, the inactive row's delete form `action` is `/admin/stations/<id>/delete?key=testkey`

#### Human-verified checklist (manual, evidence attached in Phase 4)
- [ ] Delete button visually matches existing Deactivate/Reactivate button sizing/spacing (side-by-side in the Actions column) — expected: consistent styling, no layout shift
- [ ] Clicking Delete shows a browser `confirm()` prompt before any request fires — expected: prompt appears, no network request yet
- [ ] Cancelling the confirm prompt leaves the page unchanged — expected: no request sent, row still present
- [ ] Confirming on a station with no bookings removes the row and shows the success flash — expected: row gone, flash visible
- [ ] Confirming on a station with existing bookings does NOT remove the row and shows the blocked-delete flash — expected: row still present, "Cannot delete: station has existing bookings." flash visible
- [ ] Active stations show no delete button anywhere in the list — expected: visually confirmed across the full station list

### Implementation Notes

- **Module(s):** `templates/admin_stations.html` only.
- **Pattern reference:** the existing `{% if s.is_active %} ... Deactivate ... {% else %} ... Reactivate ... {% endif %}` block (lines ~91-99) — add the delete form inside the `{% else %}` branch, alongside `Reactivate`.
- **Key decisions:** A3 (button only on `not s.is_active` rows), A4 (inline JS `confirm()`, no new modal component).
- **Libraries:** None — plain `onsubmit="return confirm(...)"` or equivalent, no new JS dependency.

### Scope Boundaries

- Do NOT add a server-rendered confirm page — A4 specifies inline JS confirm only.
- Do NOT change the `Save`/`Deactivate`/`Reactivate` forms or buttons — additive only.
- Do NOT show or enable the delete button for active stations, even disabled — A3 says no button at all for active rows.

### Files Expected

**Modified files:**
- `templates/admin_stations.html` (add delete form/button inside the deactivated-row branch, with confirm prompt)

**Must NOT modify:**
- `main.py`, `price_store.py` (owned by T2)
