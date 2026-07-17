# Architecture: Station Management Dashboard

> **Date:** 2026-07-17
> **Phase:** 2 of 5 (System Architecture)
> **Requirements source:** specs/requirements/REQ-station-management.md
> **Type:** feature

## Architecture Summary

Add a new admin-gated "Manage Stations" page for creating, editing, deactivating, and reactivating fuel stations. Most of the needed groundwork already exists as a side effect of REQ-fuel-types-expansion: `price_store.upsert_station()` is already identity-only (id/brand/name/location, no price), and the `stations.is_active` column already exists in the schema but is never read. This REQ's core work is: (1) generating a unique station id from brand+name, (2) actually enforcing `is_active` at every station-listing call site, and (3) fixing a related gap where bare (unpriced) stations are invisible on the existing admin prices page, undermining the "stations can be created with zero prices" requirement. No schema migration — the flag already exists.

## High-Level Structure

```
                    ┌──────────────────────────────┐
                    │         main.py (Flask)        │
                    │                                 │
GET/POST /admin/stations ─── list (active+inactive) + create form
POST /admin/stations/<id>/edit ─── update brand/name/location
POST /admin/stations/<id>/deactivate ─── set_station_active(id, False)
POST /admin/stations/<id>/reactivate ─── set_station_active(id, True)
GET /admin/prices (T7, modified) ─── now seeds from list_all_stations()
                    │                                 │
                    └────────────┬────────────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │      price_store.py        │
                    │  upsert_station()  (existing, unchanged)
                    │  list_stations(fuel_type, include_inactive=False)  (existing, +1 param)
                    │  list_all_stations(include_inactive=True)  (NEW)
                    │  generate_unique_station_id(brand, name)  (NEW)
                    │  set_station_active(station_id, is_active)  (NEW)
                    └──────────────────────────┘
                                 ▼
                    Postgres: stations.is_active (existing column, now enforced)
```

Nothing new at the schema level. `main.py`'s other 8 `price_store.list_stations()` call sites (booking flow, PDF export, `/api/v1/*`) need zero code changes — the new `include_inactive` param's default (`False`) makes `is_active` take effect there automatically.

## Tech Choices

| Area | Decision | Alternatives Considered | Rationale |
|---|---|---|---|
| Slug generation | Consolidate the slugify logic already duplicated inline (`_slug()`) across several `main.py` route functions into one proper `price_store.generate_unique_station_id()` | Add yet another inline local copy | This REQ is a natural point to stop duplicating it; station-id generation is a `price_store` concern (has the DB access needed for collision checks) |
| `is_active` enforcement mechanism | `list_stations()` gains `include_inactive: bool = False` | A separate `list_active_stations()` function alongside the existing one | Backward-compatible default means all 8 existing call sites need zero changes — the safest possible rollout for a flag that's never been enforced before |
| Bare/unpriced station visibility | New `list_all_stations()` (no price JOIN, no fuel filter) replaces the fuel-type-price-union as the base station list for both Manage Stations and `admin_prices()` | Leave `admin_prices()`'s existing gap as-is, defer the fix to a future REQ | Without this fix, R6 (bare station creation) has no visible outcome — a newly created station would be invisible on the only page that can give it a price |
| Create/edit routing | 2 separate routes/forms | 1 dual-purpose form (id field present-or-blank) | Clearer semantics; `upsert_station()`'s `ON CONFLICT DO UPDATE` already naturally supports both without extra branching in the store layer |
| Concurrent-creation race | Accepted as a known low-risk gap, no locking added | Add a `SELECT ... FOR UPDATE` or retry loop around id generation + insert | Admin-only, low-traffic; `upsert_station()`'s upsert semantics mean a race silently merges rather than crashing — acceptable given the REQ's own low-stakes framing |

## Patterns & Conventions

- **Repo abstraction preserved** — `main.py` never queries `stations` directly; all access through `price_store.py`, matching existing convention.
- **Optional-parameter backward compatibility** — `include_inactive` follows the same pattern already used for `fuel_type` defaults elsewhere in this codebase (additive, safe-default parameters over breaking signature changes where possible).
- **Soft-delete via existing flag** — no new deletion/archival pattern introduced; reuses `stations.is_active`, present in the schema since the original F2.1 migration but never wired up.

## Data Models

### `stations` (no schema change)

Existing table, existing columns (`id`, `legacy_id`, `brand`, `display_name`, `location`, `is_active`, `created_at`, `updated_at`). This REQ is purely about *using* `is_active`, which has existed unused since F2.1.

## API Contracts / Interfaces

### Module-boundary functions (`price_store.py`)

| Op | Signature | Purpose | Errors / Returns |
|---|---|---|---|
| `list_stations` | `list_stations(fuel_type: str, include_inactive: bool = False) -> List[Dict]` | existing, +1 param | unchanged behavior when `include_inactive` omitted |
| `list_all_stations` | `list_all_stations(include_inactive: bool = True) -> List[Dict]` | all stations, no price join, no fuel filter | `[]` if none |
| `generate_unique_station_id` | `generate_unique_station_id(brand: str, name: str) -> str` | slugify + collision-suffix | always returns a unique, never-yet-used id |
| `set_station_active` | `set_station_active(station_id: str, is_active: bool) -> Dict` | flip the flag | `KeyError` if station doesn't exist |
| `upsert_station` | `upsert_station(st: Dict) -> Dict` | existing, unchanged | `ValueError` on missing required identity keys |

### HTTP routes (`main.py`)

| Method/Path | Purpose | Auth | Errors / Returns |
|---|---|---|---|
| `GET /admin/stations` | list (active+inactive) + create form | admin session | — |
| `POST /admin/stations` | create a new station | admin session | flash error + re-render on missing brand/name |
| `POST /admin/stations/<id>/edit` | update brand/name/location | admin session | 404 on unknown id |
| `POST /admin/stations/<id>/deactivate` | soft-delete | admin session | 404 on unknown id |
| `POST /admin/stations/<id>/reactivate` | undo soft-delete | admin session | 404 on unknown id |

**Auth requirements:** all 4 new routes behind existing `require_admin`, same as the rest of `/admin`.

## Module Boundaries

| Module / Package | Responsibility | Allowed Dependencies |
|---|---|---|
| `price_store.py` | owns all station identity/active-status reads/writes, including the new functions | `db.pool` only |
| `main.py` | HTTP layer, orchestrates `price_store`, never queries `stations` directly | `price_store.py` |

## Change Footprint

### New files / modules

| Path | Purpose | Pattern reference |
|---|---|---|
| `templates/admin_stations.html` | Manage Stations page: list + create form + inline edit | `templates/admin_prices.html` (admin-only, table-driven page) |

### Modified files / modules

| Path | What changes here |
|---|---|
| `price_store.py` | `list_stations()` gains `include_inactive` param + `WHERE s.is_active` clause; new `list_all_stations()`, `generate_unique_station_id()`, `set_station_active()` |
| `main.py` | `admin_prices()` (T7's route) rebuilt to seed from `list_all_stations()` instead of the fuel-type price union; 4 new routes for station CRUD/activation |
| `templates/admin_prices.html` | grayed-out row styling + reactivate control for inactive stations |
| `templates/admin.html` | new nav link to Manage Stations |

### Deleted / replaced

_None._

### Touched but not changed (silent-regression hotspots)

| Path | Why it matters |
|---|---|
| 8 other `price_store.list_stations()` call sites in `main.py` (`/book`, PDF export, `supplier_api` fallback, `/api/v1/*`) | New `include_inactive` param defaults to `False` — zero code changes needed, this is the REQ's intended fix taking effect, not a regression |
| `tests/test_price_store.py` | Must confirm `list_stations()` still returns all (`is_active=TRUE`) stations by default |
| `tests/test_admin_display_layout.py` (T7) | `FakePriceStore.list_stations(self, fuel_type)` stub has a single-param signature — breaks with a `TypeError` once real code calls it with `include_inactive=True`; must be updated |
| `tests/test_seeds.py` | Cross-checks `_DEFAULT_STATIONS`, untouched by this REQ |

## Areas of Impact

| Area | Impact | Risk (L/M/H) | Why |
|---|---|---|---|
| `admin_prices()` route rebuild | Touches T7's already-shipped, tested route | M | Must not regress its existing 6-column rendering while fixing the bare-station-visibility gap |
| New Manage Stations page | New route, template, store functions | L | Purely additive, admin-gated |
| `is_active` enforcement rollout | First time this flag actually takes effect anywhere | M | Low risk given admin-only trigger, but touches the booking-flow-adjacent `list_stations()` signature |

**Contract changes:** `list_stations()` gains an optional parameter — backward compatible, no existing caller breaks. No external/public contract changes.

**Cross-cutting ripples:** none into auth, telemetry, feature flags, or the build/deploy pipeline. No schema migration.

## Cross-Cutting Concerns

- **Errors:** slug collision → silent auto-suffix. Missing brand/name on create → flash error, re-render form. Deactivate/reactivate on unknown `station_id` → 404.
- **Logging & metrics:** reuse existing `print(f"⚠️ ...")` non-fatal pattern.
- **Auth / authz:** all new routes behind `require_admin`, unchanged pattern.
- **Performance:** `list_all_stations()` is a plain unfiltered `SELECT`, trivial at current scale (~10-20 stations).
- **Security:** parameterized queries throughout, no new injection surface.
- **Migrations / rollout:** none — `is_active` already exists in the schema, no `db/apply.py` changes needed. Lowest-risk rollout of any REQ in this brief.

## Architecture Decisions Log

| # | Decision | Alternatives | Chosen Because | Satisfies REQs |
|---|---|---|---|---|
| A1 | Reuse `upsert_station()` as-is for both create and edit | New dedicated create/edit functions | Already identity-only from fuel-types-expansion's T2 — no new station-write logic needed | R3, R4, R7 |
| A2 | `generate_unique_station_id()` consolidates the previously-duplicated inline `_slug()` pattern | Add another inline local copy | Natural point to stop duplicating; belongs in `price_store.py` (has DB access for collision checks) | R4, R5 |
| A3 | Soft-delete via plain `UPDATE` on `is_active`, no audit trail | Add a `station_history` audit table | Not requested by the REQ; keep scope tight | R8 |
| A4 | `list_stations()` gains `include_inactive: bool = False` | Separate `list_active_stations()` function | Backward-compatible default — zero changes to the 8 existing call sites | R10 |
| A5 | New `list_all_stations()` (no price join) fixes bare-station invisibility on `admin_prices()` too | Leave the existing gap for a future REQ | Without this, R6 (bare creation) has no usable outcome | R6, R9 |
| A6 | Create/edit as 2 separate routes | 1 dual-purpose form | Clearer semantics; matches `upsert_station()`'s natural insert-or-update shape without extra branching | R3, R7 |
| A7 | Accept the slug-generation-then-insert race as a known low-risk gap | Add locking/retry around id generation | Admin-only, low-traffic; `upsert_station()`'s upsert semantics mean a race silently merges, not crashes | — |

## Risk & Stress-Test Scenarios

### Forward — runtime failure scenarios

| Scenario | How the Design Handles It |
|---|---|
| Two admins deactivate/reactivate the same station simultaneously | Simple `UPDATE`, last-write-wins, matches existing price/discount update pattern — no lock needed |
| Slug collision on creation (two "Petron" entries) | Handled by design — auto-suffix (R5) |
| Admin deactivates a station mid-booking (customer has it open in another tab) | T3's existing server-side price-gate re-validation naturally rejects it once `include_inactive` defaults to `False` — booking correctly blocked. Flash message ("does not have a price set") is a minor UX inaccuracy for this specific case (inactive vs. unpriced) but functionally correct; not required to fix by the REQ — captured as an Open Question |
| Rollback if this ships broken | Trivial — `git revert`, no schema migration to reverse |

### Backward — regression risk per touched area

| Touched area | What could regress | How we'd know / mitigation |
|---|---|---|
| `tests/test_price_store.py` | Existing `list_stations()` tests break if the new param changes default behavior | Must pass unchanged — default preserves old behavior exactly |
| `tests/test_admin_display_layout.py` | `FakePriceStore.list_stations()` stub signature mismatch | Update the stub to accept `include_inactive` (or `**kwargs`) before this REQ's tests run |
| Booking flow, PDF export, `/api/v1/*` | Any accidental behavior change for existing (all currently-active) stations | Unaffected today since every current station is `is_active=TRUE`; only diverges once an admin actually deactivates something for the first time |

## Open Questions

- Should the booking-rejection flash message distinguish "station is deactivated" from "no price set for this fuel type," given both currently produce the same generic message via the shared price-gate check?
  - **Impact if unresolved:** slightly confusing error message for the rare case of a station deactivated between page-load and booking-submit; booking is still correctly blocked either way.
  - **Suggested default:** leave as-is; the shared price-gate mechanism already delivers the correct *behavior* (rejection), just with imprecise wording for one edge case. Revisit only if support tickets show real confusion.

## Out of Scope

- Hard deletion of station rows (reason: soft-delete only, per REQ).
- Requiring price entry at station creation time (reason: REQ confirmed bare creation is valid).
- Per-fuel-type price/discount management itself (reason: REQ-fuel-types-expansion's scope).
- Any changes to `admin_prices.html`'s pricing columns/layout beyond the grayed-out/reactivate addition (reason: REQ-fuel-types-expansion's scope).
- Locking/retry logic for the slug-generation race (reason: accepted low-risk gap, A7).
- Distinguishing "deactivated" from "unpriced" in the booking rejection message (reason: captured as an Open Question, not required).

---

# Tasks

## Task T1: `price_store.py` Station Identity & Activation Functions

> **Status:** done
> **Verification:** tdd
> **Effort:** m
> **Priority:** critical
> **Depends on:** None
> **Satisfies REQs:** R4, R5, R6, R8, R9, R10
> **Footprint slice:** Modified: `price_store.py`
> **High-risk areas touched:** None directly — foundational to T2/T3's booking-flow-adjacent behavior

### Description

Add `is_active` enforcement to `list_stations()` via a new backward-compatible `include_inactive` parameter, plus 3 new functions: `list_all_stations()` (no price JOIN — the fix for bare-station invisibility), `generate_unique_station_id()` (consolidates the previously-duplicated inline slugify pattern), and `set_station_active()`. Foundational — T2/T3 both depend on this landing first.

### Test Plan

#### Test File(s)
- `tests/test_price_store.py` (extend existing file, same `test_station`/`schema_db` fixture pattern)

#### Test Scenarios

##### `is_active` Enforcement

- **list_stations default excludes inactive stations** — GIVEN a priced but inactive station WHEN `list_stations(fuel_type)` is called with no `include_inactive` arg THEN it's excluded _(verifies R10)_
- **list_stations(include_inactive=True) includes inactive stations** — same setup, explicit override

##### `list_all_stations`

- **returns a bare station with zero prices** — GIVEN a station with no price row for any fuel type WHEN `list_all_stations()` is called THEN it still appears _(verifies R6, ARCH A5)_
- **list_all_stations(include_inactive=False) excludes inactive stations** when explicitly asked

##### `generate_unique_station_id`

- **slugifies brand + name** — GIVEN `generate_unique_station_id("Petron", "Makati")` THEN returns a lowercase, underscore-separated id _(verifies R4)_
- **auto-suffixes on collision** — GIVEN an existing station with a given slug WHEN generating again with the same brand/name THEN returns a `-2` suffixed variant _(verifies R5)_

##### `set_station_active`

- **deactivating then reactivating round-trips correctly** — GIVEN `set_station_active(id, False)` WHEN `list_stations(fuel_type)` (default) is called THEN the station is excluded; WHEN `set_station_active(id, True)` is called THEN it reappears _(verifies R8, R9)_
- **unknown station_id raises KeyError**

##### Edge Case

- **sequential collisions get sequential suffixes** — two stations whose brand+name slugify identically get `-2`, `-3` in order _(REQ edge case)_

##### Regression Guard

- **existing list_stations tests (T2, fuel-types-expansion) still pass unchanged** — default behavior preserved for already-active stations
- **tests/test_seeds.py's `_DEFAULT_STATIONS` cross-check still passes**

### Implementation Notes

- **Module(s):** `price_store.py`
- **Pattern reference:** existing `_slug()`/`_norm_dashes()` inline helpers duplicated across `main.py` route functions — same slugify algorithm, moved here properly this time
- **Key decisions:** ARCH A2 (consolidate slugify logic here, not another inline copy), A4 (`include_inactive` backward-compatible default), A5 (`list_all_stations()` fixes the bare-station gap)
- **Libraries:** none new
- **High-risk callouts:** none directly — store-layer only, but its correctness underpins T2/T3's booking-flow-adjacent behavior

### Scope Boundaries

- Do NOT touch `main.py` — that's T2/T3.
- Do NOT add locking/retry around the collision-suffix generation (accepted risk, ARCH A7).
- Do NOT add an audit/history table for activation changes — not requested (ARCH A3).

### Files Expected

**Modified files:**
- `price_store.py` (`list_stations` +param; new `list_all_stations`, `generate_unique_station_id`, `set_station_active`)

**Must NOT modify:**
- `main.py`, any template (later tasks' scope)

---

## Task T2: Station CRUD/Activation Routes

> **Status:** not started
> **Verification:** test-after
> **Effort:** m
> **Priority:** high
> **Depends on:** T1
> **Satisfies REQs:** R1, R3, R4, R6, R7, R8, R9
> **Footprint slice:** Modified: `main.py` (4 new routes)
> **High-risk areas touched:** None — new, isolated route family

### Description

Add 4 new admin-gated routes for creating, editing, deactivating, and reactivating stations, using T1's new `price_store` functions.

### Test Plan

#### Test File(s)
- `tests/test_admin_stations.py` (new)

#### Test Scenarios

##### Auth & Creation

- **GET /admin/stations unauthenticated redirects to login** _(verifies R1)_
- **POST /admin/stations with valid brand/name/location creates a station, no price required** — `price_store.upsert_station` called with a generated id _(verifies R3, R4, R6)_
- **POST /admin/stations with missing brand or name flashes an error and re-renders** _(edge case)_

##### Edit

- **POST /admin/stations/<id>/edit updates brand/name/location** _(verifies R7)_
- **POST /admin/stations/<id>/edit on unknown id returns 404**

##### Deactivate / Reactivate

- **POST /admin/stations/<id>/deactivate calls set_station_active(id, False)** _(verifies R8)_
- **POST /admin/stations/<id>/reactivate calls set_station_active(id, True)** _(verifies R9)_
- **POST deactivate/reactivate on unknown id returns 404**

##### Regression Guard

- **all 4 routes require admin session** — unauthenticated request redirected, consistent with rest of `/admin`

### Implementation Notes

- **Module(s):** `main.py`
- **Pattern reference:** existing `require_admin` gating used throughout `/admin`; `admin_discounts_update`'s flash+redirect error pattern for the create-form validation case
- **Key decisions:** ARCH A1 (`upsert_station()` reused as-is for create/edit), A6 (2 separate routes rather than 1 dual-purpose form)
- **Libraries:** none new
- **High-risk callouts:** none — new, isolated route family

### Scope Boundaries

- Do NOT touch `admin_prices()` — that's T3.
- Do NOT touch templates — that's T4; use minimal/placeholder rendering sufficient for this task's own tests if needed.
- Do NOT add hard-delete — soft-delete only (ARCH A3/Out of Scope).

### Files Expected

**New files:**
- `tests/test_admin_stations.py`

**Modified files:**
- `main.py` (`/admin/stations` GET/POST, `/admin/stations/<id>/edit`, `/admin/stations/<id>/deactivate`, `/admin/stations/<id>/reactivate`)

**Must NOT modify:**
- `price_store.py` (T1's scope, already landed)
- `admin_prices()` in `main.py` (T3's scope)

---

## Task T3: `admin_prices()` Rebuild — Bare & Inactive Station Visibility

> **Status:** not started
> **Verification:** test-after
> **Effort:** s
> **Priority:** high
> **Depends on:** T1
> **Satisfies REQs:** R6, R9
> **Footprint slice:** Modified: `main.py` (`admin_prices()` only)
> **High-risk areas touched:** `admin_prices()` route — M risk (touches T7's already-shipped code)

### Description

Fix `admin_prices()` (T7's route) to seed its station list from T1's `list_all_stations()` instead of the fuel-type price union, so bare (unpriced) and inactive stations both appear — closing the gap where R6 (bare creation) would otherwise have no visible outcome, and satisfying R9's requirement that deactivated stations stay visible on this page too.

### Test Plan

#### Test File(s)
- `tests/test_admin_display_layout.py` (extend; update `FakePriceStore` stub for the new `price_store` surface)

#### Test Scenarios

##### Bare & Inactive Visibility

- **bare station (zero prices) appears with empty/blank price cells** _(verifies R6, ARCH A5)_
- **inactive station appears with is_active=False passed to the template context** _(verifies R9's data-layer half — visual graying is T4's job)_

##### Regression Guard

- **priced, active station's fuel-price/discount data is unchanged from T7's existing behavior**
- **existing T7 tests (6-column rendering, readable timestamps) still pass** once `FakePriceStore` is updated for the new signature

### Implementation Notes

- **Module(s):** `main.py`
- **Pattern reference:** T7's existing per-fuel-type overlay loop (`fuels_by_station` dict) — same shape, different seed source (`list_all_stations()` instead of the price-union)
- **Key decisions:** ARCH A5
- **Libraries:** none new
- **High-risk callouts:** M-risk per ARCH — touches T7's already-shipped, tested route; must not regress existing 6-column rendering for priced/active stations

### Scope Boundaries

- Do NOT touch the 6-column pricing layout itself or its styling — only the underlying station-list seed source. Grayed-out visual treatment is T4's job.
- Do NOT touch the new `/admin/stations` routes — that's T2.

### Files Expected

**Modified files:**
- `main.py` (`admin_prices()`)
- `tests/test_admin_display_layout.py` (`FakePriceStore` stub updated)

**Must NOT modify:**
- `price_store.py` (T1's scope, already landed)
- `/admin/stations` routes (T2's scope)
- `templates/admin_prices.html` styling (T4's scope)

---

## Task T4: Manage Stations Page & Grayed-Out Inactive Styling

> **Status:** not started
> **Verification:** ui
> **Effort:** m
> **Priority:** high
> **Depends on:** T1, T2
> **Satisfies REQs:** R1, R2, R3, R6, R9
> **Footprint slice:** New: `templates/admin_stations.html`. Modified: `templates/admin_prices.html`, `templates/admin.html`
> **High-risk areas touched:** `admin_prices.html` — M risk (T7's already-shipped styling)

### Description

Build "Manage Stations" admin page (list/create/edit/deactivate/reactivate stations via T2's routes), gray-out styling for inactive stations shared between new page and `admin_prices.html`, nav link wired into `admin.html`.

### Verification Checklist

**Human-verified (browser, `?key=` admin gate):**
1. `/admin/stations` lists all stations: id, brand, name, location, active/inactive status
2. Create form (brand/name/location only, bare — no price fields) submits, new station appears in list immediately
3. Edit form pre-fills identity fields, save persists changes
4. Deactivate control flips station to inactive — row stays visible, grayed-out style, control swaps to "Reactivate"
5. Reactivate control flips back, normal styling returns
6. `admin_prices.html`: inactive stations render grayed-out, consistent visual treatment with admin_stations page; price cells still show T3's data (blank for bare stations)
7. `admin.html` nav shows "Manage Stations" link → routes correctly with admin key preserved
8. Regression: `admin_prices.html` active-station 6-column layout, timestamps unchanged from T7
9. a11y basics: labeled buttons, table headers present, no keyboard trap

**Testable seams (automated, `template_rendered` signal):**
- `/admin/stations` context contains full station list incl. inactive ones
- inactive station's context flag reaches template (CSS class/attr presence)
- `admin_prices.html` template receives is_active per station (T3 data already covers this — verify template actually consumes it)

### Implementation Notes

- **Module(s):** templates only, no `main.py`/store changes (T2/T3 already provide routes+data)
- **Pattern reference:** existing `admin_prices.html`/`admin.html` structure and CSS conventions
- **Key decisions:** ARCH — grayed-out + reactivate control, not hidden (soft-delete stays visible)
- **High-risk callouts:** M-risk touching T7's `admin_prices.html` — don't regress active-station rendering

### Scope Boundaries

- Do NOT touch `main.py` routes or `price_store.py` — T1/T2 own those, already landed.
- Do NOT change 6-column pricing data logic — T3's job, already landed. This task is styling/markup only on top of it.

### Files Expected

**New files:**
- `templates/admin_stations.html`

**Modified files:**
- `templates/admin_prices.html` (inactive styling only)
- `templates/admin.html` (nav link)

**Must NOT modify:**
- `main.py`, `price_store.py`
