# Architecture: Customer Details Page

> **Date:** 2026-07-16
> **Phase:** 2 of 5 (System Architecture)
> **Requirements source:** specs/requirements/REQ-customer-details-page.md
> **Type:** feature

## Architecture Summary

Add a single new admin-gated route, `/admin/customers`, that resolves a search query (`?q=`) to one of three server-rendered states — not-found, direct detail view, or a picklist of fuzzy matches — using exact `account_code` lookup first and `rapidfuzz`-powered fuzzy matching against `contact_name`/`company_name` as a fallback. Booking history and both CSV exports (per-customer and global) reuse existing patterns (`list_all_vouchers()` + Python-side filtering, `DataFrame.to_csv` → `send_file`) rather than introducing new abstractions. The one non-trivial technical piece: fixing the CSV-mode gap where `account_code` is silently dropped from booking rows requires two files to change together — `models.VOUCHER_COLUMNS` gains it, and `db/postgres_repo.py`'s `_FK_COLUMNS` must lose it, or Postgres's INSERT statement breaks with a duplicate-column error.

## High-Level Structure

```
                    ┌───────────────────────────────┐
                    │         main.py (Flask)         │
                    │                                  │
GET /admin/customers├── search resolution:              │
  ?q=<term>          │   1. exact account_code match     │
                    │   2. fuzzy match (rapidfuzz) on    │
                    │      contact_name/company_name     │
                    │   → not-found / detail / picklist  │
                    │                                  │
GET /admin/customers/export?account_code=  ── per-customer CSV
GET /admin/bookings/export                  ── global CSV
                    │                                  │
                    └───────────┬──────────────────────┘
                                ▼
                    ┌───────────────────────┐
                    │   repo (CSVRepo /       │
                    │   PostgresRepo)         │
                    │  + list_customers() NEW │
                    │  (existing: get_customer,│
                    │   list_all_vouchers)     │
                    └───────────────────────┘
```

Nothing new at the module level — this REQ extends `main.py` (routes), `persistence.py`/`db/postgres_repo.py` (one new method each), `models.py` (one column addition), and one new template. No new services, no schema migration (the `vouchers.account_code` column already exists in Postgres).

## Tech Choices

| Area | Decision | Alternatives Considered | Rationale |
|---|---|---|---|
| Fuzzy matching | `rapidfuzz.process.extract` against a combined `contact_name`/`company_name` field, score-cutoff ~60/100 | New fuzzy-matching library; exact-substring matching only | `rapidfuzz` is already a project dependency, unused anywhere — first use here; substring-only would miss typos/reordering |
| Booking history data access | Reuse `list_all_vouchers()` + Python-side filter by `account_code` | New account_code-filtered repo method (`list_vouchers_by_account`) | Matches the existing supplier-export convention (fetch-all-then-filter/transform in Python); avoids growing the repo interface for an internal admin tool at modest data volume |
| CSV export mechanism | Reuse exact existing pattern (`pd.DataFrame(rows).to_csv(path)` → `send_file(path, as_attachment=True)`, `main.py:1039-1041`) | `Response` with in-memory CSV buffer (no temp file) | Matches established project convention; no reason to diverge for two new exports |
| `account_code` CSV persistence fix | Add to `VOUCHER_COLUMNS`; remove from `db/postgres_repo.py`'s `_FK_COLUMNS` in the same change | Leave `_FK_COLUMNS` untouched, accept the duplicate-column risk | The duplicate-column INSERT would break booking creation in Postgres — not optional, must be paired |

## Patterns & Conventions

- **All route logic lives in `main.py`** — no new modules, matching `AGENTS.md`'s documented "Flask app with all routes in one file" convention.
- **Repo abstraction preserved** — `main.py` never queries `customers`/`vouchers` tables directly; new `list_customers()` added to both `CSVRepo` and `PostgresRepo`, mirroring the existing `list_all_vouchers()` shape in each.
- **Fetch-all-then-filter/transform in Python**, not new filtered SQL/pandas queries — consistent with the existing supplier CSV export's approach.
- **CSV export via temp file + `send_file`**, not streamed in-memory — matches the one existing export pattern in the codebase exactly.

## Data Models

### `vouchers` (no schema change — column already exists)

`account_code VARCHAR(16) REFERENCES customers(account_code)` already exists in `db/schema.sql`. The only change is in Python: `models.VOUCHER_COLUMNS` gains `"account_code"` so both `CSVRepo` and `PostgresRepo` correctly overlay it from caller-supplied booking data, and `db/postgres_repo.py`'s `_FK_COLUMNS` tuple drops `"account_code"` (becomes `("station_id",)` only) to avoid a duplicate column reference in the generated INSERT statement.

### `customers` (no schema change)

Existing table, existing columns (`account_code`, `contact_name`, `contact_number`, `email`, `company_name`, `fleet_size`, `areas`, `refuel_locations`, `hq_locations`) — this REQ only adds a new read method (`list_customers()`), no new fields.

## API Contracts / Interfaces

### HTTP routes (`main.py`)

**Boundary:** HTTP, Flask routes.

| Method/Path | Purpose | Auth | Errors / Returns |
|---|---|---|---|
| GET `/admin/customers?q=<term>` | search resolution → not-found / detail view / picklist | admin session | renders `admin_customer_lookup.html` in one of 3 states; empty `q` shows the search form only |
| GET `/admin/customers/export?account_code=<code>` | per-customer booking history CSV | admin session | 404 if `account_code` doesn't resolve to a real customer; CSV (headers only if zero bookings) otherwise |
| GET `/admin/bookings/export` | all customers' bookings CSV | admin session | CSV (headers only if zero bookings) |

### Module-boundary functions

**Boundary:** internal module, imported by `main.py`.

| Op | Signature | Purpose | Errors / Returns |
|---|---|---|---|
| `CSVRepo.list_customers` | `list_customers() -> List[Dict]` | all customers, CSV-backed | `[]` if none |
| `PostgresRepo.list_customers` | `list_customers() -> List[Dict]` | all customers, `SELECT * FROM customers` | `[]` if none |

**Auth requirements:** unchanged — all 3 new routes behind `require_admin`, same as `/admin`/`/admin/prices`.

## Module Boundaries

| Module / Package | Responsibility | Allowed Dependencies |
|---|---|---|
| `persistence.py` / `db/postgres_repo.py` | owns all customer/voucher CRUD, including new `list_customers()` | `models.py`, `db/pool.py` |
| `models.py` | canonical `VOUCHER_COLUMNS` list | none (pure constants) |
| `main.py` | HTTP layer, orchestrates repo + `rapidfuzz`, never queries DB directly | `persistence.py`, `models.py`, `rapidfuzz` |

## Change Footprint

### New files / modules

| Path | Purpose | Pattern reference |
|---|---|---|
| `templates/admin_customer_lookup.html` | search + 3-state render + booking history + export button | `templates/admin_prices.html` (admin-only, table-driven page) |

### Modified files / modules

| Path | What changes here |
|---|---|
| `models.py` | `VOUCHER_COLUMNS` gains `"account_code"` |
| `db/postgres_repo.py` | `_FK_COLUMNS` loses `"account_code"` (paired with the above — must ship together); new `list_customers()` method |
| `persistence.py` | `CSVRepo` gains `list_customers()` |
| `main.py` | new routes: `/admin/customers`, `/admin/customers/export`, `/admin/bookings/export` |
| `templates/admin.html` | new link to `/admin/customers`; new "Export All Bookings" button |

### Deleted / replaced

_None._

### Touched but not changed (silent-regression hotspots)

| Path | Why it matters |
|---|---|
| `tests/test_postgres_repo.py` (`create_unverified_booking` tests, real schema via `schema_db` fixture) | Exercises the exact `append_vouchers()`/`_VOUCHER_INSERT_COLUMNS` SQL path the `_FK_COLUMNS` fix touches — must keep passing, serves as the automatic tripwire for a mispaired deploy |
| `tests/test_postgres_repo_integration.py` | Same — live-Postgres integration coverage of `create_unverified_booking` |
| `tests/test_customer_repo.py`, `tests/test_customer_repo_integration.py` | Existing customer CRUD tests, must keep passing unchanged; good pattern reference for new `list_customers()` tests |
| `tests/test_schema.py`'s `test_vouchers_has_all_voucher_columns` | Self-validating, passes automatically once `VOUCHER_COLUMNS` includes `account_code` — no migration needed since the column already exists |

## Areas of Impact

| Area | Impact | Risk (L/M/H) | Why |
|---|---|---|---|
| Booking creation (`create_unverified_booking`, both backends) | `_FK_COLUMNS`/`VOUCHER_COLUMNS` pairing touches the shared INSERT-column-list mechanism | M | A mispaired deploy (one file changed, not the other) breaks *all* bookings in Postgres mode, not just this feature — isolated but sharp risk |
| New admin customer-lookup page | New route, template, repo method | L | Purely additive, admin-gated, no existing behavior changes |
| CSV exports | Reuses existing pattern exactly | L | Low novelty, proven pattern already in production |

**Contract changes:** none external — `list_customers()` is a new addition to an internal repo interface, no existing method signatures change.

**Cross-cutting ripples:** none into auth, telemetry, feature flags, or the build/deploy pipeline. The `VOUCHER_COLUMNS`/`_FK_COLUMNS` pairing must land in the same deploy — that's the one rollout constraint.

## Cross-Cutting Concerns

- **Errors:** no-match search → inline message, no crash. Export with zero bookings → CSV with headers only. Per-customer export with an unknown `account_code` → 404, not a blank 200 CSV.
- **Logging & metrics:** no new infra — existing `print(f"⚠️ ...")` pattern for non-fatal failures.
- **Auth / authz:** unchanged — all 3 new routes behind `require_admin`.
- **Performance:** `list_customers()` loads the full customer set into memory for fuzzy matching — acceptable at current scale (dozens/hundreds); revisit if customer count grows into the thousands.
- **Security:** exports contain PII (contact name/phone/email) — already admin-gated, no new public exposure. Search query `q=` rendered back into the page is Jinja-auto-escaped, no XSS concern.
- **Migrations / rollout:** `VOUCHER_COLUMNS`/`_FK_COLUMNS` must ship as one atomic change — the standout rollout risk. No database schema migration required (column already exists).

## Architecture Decisions Log

| # | Decision | Alternatives | Chosen Because | Satisfies REQs |
|---|---|---|---|---|
| A1 | Single route, 3 server-side render states (not-found/detail/picklist) | Separate list + detail pages | Matches REQ's confirmed single-page decision | R3, R5, R6, R7 |
| A2 | Exact `account_code` match first, `rapidfuzz` fallback on `contact_name`/`company_name` | Fuzzy-only; exact-only | Matches REQ's confirmed dual-search decision | R3, R4 |
| A3 | Booking history via `list_all_vouchers()` + Python filter, no new filtered repo method | New `list_vouchers_by_account()` method | Matches existing supplier-export convention; avoids repo interface growth for modest data volume | R9 |
| A4 | Both CSV exports reuse the exact existing `main.py:1039-1041` pattern | New in-memory CSV streaming | Matches established project convention | R10, R11 |
| A5 | `VOUCHER_COLUMNS` gains `account_code`; `_FK_COLUMNS` loses it, shipped together | Leave `_FK_COLUMNS` untouched | Avoids the duplicate-column INSERT bug that would break all Postgres-mode bookings | R12 |
| A6 | Per-customer export with unknown `account_code` returns 404 | Return a blank 200 CSV | Avoids a misleading "successful" empty export for a bogus code | New (not explicit in REQ, closes an edge case) |

## Risk & Stress-Test Scenarios

### Forward — runtime failure scenarios

| Scenario | How the Design Handles It |
|---|---|
| Deploy lands `VOUCHER_COLUMNS` change without the `_FK_COLUMNS` fix (or vice versa) | GAP if shipped separately — every booking creation breaks in Postgres mode. Mitigation: both edits land in the same task/commit; `test_postgres_repo.py`'s existing `create_unverified_booking` tests are the automatic tripwire |
| Fuzzy search against a customer base with many similar company names | Handled via the picklist UX; no gap, though the 60/100 threshold may need tuning after real usage |
| Two admins hitting the global export simultaneously | No shared mutable state (read-only export), no race condition possible |

### Backward — regression risk per touched area

| Touched area | What could regress | How we'd know / mitigation |
|---|---|---|
| `tests/test_postgres_repo.py` / `test_postgres_repo_integration.py` | `create_unverified_booking` breaks due to the `_FK_COLUMNS`/`VOUCHER_COLUMNS` pairing | Existing tests must keep passing — primary regression guard |
| `tests/test_customer_repo.py` / `test_customer_repo_integration.py` | Existing `create_customer`/`get_customer`/`customer_exists` behavior | Unchanged by this REQ; `list_customers()` is purely additive |

## Open Questions

_None — all decisions confirmed by the developer during this session._

## Out of Scope

- Editing customer details from this page (reason: REQ scoped this as view-only).
- Adding `fuel_type` to the voucher schema (reason: belongs to REQ-fuel-types-expansion).
- Prescribing exact button/link copy on `/admin` (reason: left to dev/design discretion per REQ).
- Backfilling `account_code` onto historical CSV booking rows written before this fix (reason: REQ confirmed only new bookings going forward need it).

---

# Tasks

## Task T1: `account_code` CSV Persistence Fix

> **Status:** done
> **Verification:** tdd
> **Effort:** s
> **Priority:** critical
> **Depends on:** None
> **Satisfies REQs:** R12
> **Footprint slice:** Modified: `models.py`, `db/postgres_repo.py` (`_FK_COLUMNS` only)
> **High-risk areas touched:** Booking creation (`create_unverified_booking`, both backends) — M risk

### Description

Add `"account_code"` to `models.VOUCHER_COLUMNS` (fixes the CSV-mode gap where it's silently dropped from booking rows) and remove `"account_code"` from `db/postgres_repo.py`'s `_FK_COLUMNS` tuple in the same change — required together, since leaving both would make `_VOUCHER_INSERT_COLUMNS` list `account_code` twice, breaking Postgres's INSERT with a duplicate-column error for every booking. This is the highest-risk, most isolated piece of the REQ and should land and prove safe before anything else depends on it.

### Test Plan

#### Test File(s)
- `tests/test_postgres_repo.py` (extend existing `create_unverified_booking` test block, `schema_db` fixture pattern)

#### Test Scenarios

##### Schema/Constant Shape

- **VOUCHER_COLUMNS includes account_code** — GIVEN `models.VOUCHER_COLUMNS` THEN `"account_code"` is present _(verifies R12)_
- **_FK_COLUMNS no longer includes account_code** — GIVEN `db/postgres_repo._FK_COLUMNS` THEN it equals `("station_id",)` only — guards directly against the duplicate-column regression

##### Persistence

- **create_unverified_booking with account_code succeeds and round-trips** — GIVEN `create_unverified_booking({..., "account_code": "HARR"})` WHEN called THEN no SQL error occurs and `account_code` is retrievable via `get_voucher` matching the input _(verifies R12, extends the existing minimal-data test pattern at `tests/test_postgres_repo.py:378-397`)_

##### Edge Case

- **create_unverified_booking without account_code still succeeds** — GIVEN a booking dict with no `account_code` key WHEN created THEN it succeeds with a NULL `account_code` (nullable FK), no crash

##### Regression Guard

- **existing create_unverified_booking tests still pass** — `test_create_unverified_booking_with_minimal_data`, `test_create_unverified_booking_returns_persisted_row`, `test_create_unverified_booking_respects_provided_voucher_id`, `test_create_unverified_booking_refuel_datetime_fills_expected_refill_date`, `test_create_unverified_booking_does_not_overwrite_provided_dates` (all existing, `tests/test_postgres_repo.py`) _(guards backward-regression risk — the automatic tripwire per ARCH's stress-test scenario)_
- **existing live-Postgres integration tests still pass** — `tests/test_postgres_repo_integration.py`'s `create_unverified_booking` coverage _(guards backward-regression risk)_

### Implementation Notes

- **Module(s):** `models.py`, `db/postgres_repo.py`
- **Pattern reference:** existing `create_unverified_booking` tests (`tests/test_postgres_repo.py:378-397`) — same `schema_db` fixture, same assert-via-`get_voucher`-round-trip style
- **Key decisions:** ARCH A5 — both file changes are one atomic unit, not separable across deploys
- **Libraries:** none new
- **High-risk callouts:** this is the M-risk "Booking creation" Area of Impact from ARCH — the existing `create_unverified_booking` test suite (both `test_postgres_repo.py` and `test_postgres_repo_integration.py`) is the load-bearing proof this didn't break anything

### Scope Boundaries

- Do NOT touch `CSVRepo`'s `create_unverified_booking` logic itself — adding `account_code` to `VOUCHER_COLUMNS` is sufficient, since `CSVRepo` already overlays any key present in `VOUCHER_COLUMNS` from caller data (no special-casing needed there, unlike Postgres's FK passthrough).
- Do NOT add `list_customers()` here — that's T2.
- Do NOT backfill historical CSV rows — out of scope per REQ.

### Files Expected

**Modified files:**
- `models.py` (`VOUCHER_COLUMNS` gains `"account_code"`)
- `db/postgres_repo.py` (`_FK_COLUMNS` loses `"account_code"`)

**Must NOT modify:**
- `persistence.py` (T2's scope)
- `main.py`, any template (later tasks' scope)

---

## Task T2: `list_customers()` on Both Repos

> **Status:** done
> **Verification:** tdd
> **Effort:** s
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** supports R3-R7 (search functionality in T3)
> **Footprint slice:** Modified: `persistence.py`, `db/postgres_repo.py` (new `list_customers()` method each)
> **High-risk areas touched:** None — pure addition, no shared write path touched

### Description

Add `list_customers()` to `CSVRepo` and `PostgresRepo` — neither has it today. Required for the fuzzy-search feature in T3, which needs to load the full customer set to match against. Mirrors existing patterns exactly: `CSVRepo._read_customers()` already loads the full DataFrame; `PostgresRepo.get_customer()` shows the query pattern to extend to an unfiltered `SELECT`.

### Test Plan

#### Test File(s)
- `tests/test_customer_repo.py` (extend)
- `tests/test_customer_repo_integration.py` (extend)

#### Test Scenarios

##### Behavior

- **CSVRepo.list_customers returns all customers with fleet_size coerced** — GIVEN a populated customers CSV WHEN `list_customers()` is called THEN all rows are returned as dicts with `fleet_size` as int, matching `get_customer`'s existing coercion
- **PostgresRepo.list_customers returns all customers** — GIVEN a populated `customers` table WHEN `list_customers()` is called THEN all rows are returned via `SELECT * FROM customers`
- **empty customer set returns empty list** — GIVEN no customers exist WHEN `list_customers()` is called on either repo THEN `[]` is returned, not an error

##### Edge Case

- **customers with blank/missing optional fields still appear** — GIVEN customers with blank `fleet_size`/`areas`/etc. WHEN listed THEN they appear without crashing, matching `get_customer`'s existing tolerant handling

##### Regression Guard

- **existing create_customer/get_customer/customer_exists tests still pass** — both `tests/test_customer_repo.py` and `tests/test_customer_repo_integration.py` _(guards backward-regression risk — `list_customers()` is purely additive to these files)_

### Implementation Notes

- **Module(s):** `persistence.py` (`CSVRepo`), `db/postgres_repo.py` (`PostgresRepo`)
- **Pattern reference:** `CSVRepo._read_customers()` (`persistence.py:234-242`), `PostgresRepo.get_customer()` (`db/postgres_repo.py:461-470`)
- **Key decisions:** none from ARCH Decisions Log constrain this beyond mirroring existing method shapes
- **Libraries:** none new
- **High-risk callouts:** none

### Scope Boundaries

- Do NOT add filtering/pagination params — REQ scope is "load all, filter in Python" (ARCH A3's sibling decision for customers, consistent with the booking-history approach).
- Do NOT touch `get_customer`/`create_customer`/`customer_exists` — unrelated to this task.

### Files Expected

**Modified files:**
- `persistence.py` (`CSVRepo.list_customers()`)
- `db/postgres_repo.py` (`PostgresRepo.list_customers()`)

**Must NOT modify:**
- `models.py`, `_FK_COLUMNS` (T1's scope, already landed)
- `main.py`, any template (later tasks' scope)

---

## Task T3: `/admin/customers` Route Logic

> **Status:** done
> **Verification:** test-after
> **Effort:** m
> **Priority:** high
> **Depends on:** T1, T2
> **Satisfies REQs:** R1, R3, R4, R5, R6, R7, R8, R9, R10, R11
> **Footprint slice:** Modified: `main.py` (3 new routes)
> **High-risk areas touched:** None directly, but depends on T1's fix for correct booking history

### Description

Implement the 3 new routes in `main.py`: `/admin/customers` (search resolution across exact/fuzzy/not-found/picklist states), `/admin/customers/export` (per-customer booking history CSV), and `/admin/bookings/export` (global CSV). Uses T2's `list_customers()` for fuzzy matching (`rapidfuzz`, first use in this codebase) and the existing `list_all_vouchers()` + Python-side filter pattern for booking history.

### Test Plan

#### Test File(s)
- `tests/test_admin_customers.py` (new)

#### Test Scenarios

##### Auth & Search Resolution

- **unauthenticated request redirects to login** — GIVEN no admin session WHEN `/admin/customers` is requested THEN redirected to admin login _(verifies R1)_
- **exact account_code search goes direct to detail view** — GIVEN `?q=HARR` (valid account code) WHEN requested THEN the detail view renders directly, no picklist _(verifies R3, R6)_
- **fuzzy search with exactly 1 match goes direct to detail view** — GIVEN a name fragment matching exactly one customer WHEN requested THEN the detail view renders directly _(verifies R4, R6)_
- **fuzzy search with 2+ matches renders a picklist** — GIVEN a name fragment matching multiple customers WHEN requested THEN a picklist renders, each entry linkable back to an exact-account_code resolution _(verifies R4, R5)_
- **zero matches renders inline not-found** — GIVEN a search term matching no customers WHEN requested THEN an inline "not found" message renders, response is 200, no crash _(verifies R7)_

##### Detail View Content

- **detail view includes all /register fields** — GIVEN a resolved customer WHEN the detail view renders THEN account_code, contact_name, contact_number, email, company_name, fleet_size, and areas all appear _(verifies R8)_
- **detail view's booking history is scoped to that customer** — GIVEN a customer with bookings WHEN the detail view renders THEN only that customer's `account_code`-matching bookings appear in the history table _(verifies R9)_

##### Exports

- **per-customer export returns only that customer's bookings** — GIVEN `/admin/customers/export?account_code=HARR` WHEN requested THEN the CSV contains only HARR's bookings _(verifies R10)_
- **per-customer export with unknown account_code returns 404** — GIVEN an account_code that doesn't resolve to a real customer WHEN exported THEN 404, not a blank CSV _(verifies ARCH A6)_
- **global export covers all customers' bookings** — GIVEN `/admin/bookings/export` WHEN requested THEN the CSV contains bookings across all customers _(verifies R11)_

##### Edge Cases

- **customer with zero bookings renders an empty history table** — GIVEN a customer with no bookings WHEN the detail view renders THEN the history table is empty, not an error _(REQ edge case)_
- **global export with zero bookings anywhere returns headers-only CSV** — GIVEN no bookings exist WHEN exported THEN the CSV has headers but no rows, no error _(REQ edge case)_

### Implementation Notes

- **Module(s):** `main.py`
- **Pattern reference:** `require_admin(request)` gating (`main.py:206-209`) used throughout; existing supplier CSV export (`main.py:1039-1041`) for both new exports
- **Key decisions:** ARCH A1 (single route, 3 render states), A2 (exact-first, fuzzy-fallback search order), A3 (booking history via `list_all_vouchers()` + Python filter, no new filtered repo method), A4 (CSV exports follow `main.py:1039-1041`'s exact pattern), A6 (unknown `account_code` on per-customer export → 404)
- **Libraries:** `rapidfuzz` (already a dependency, first use in this codebase)
- **High-risk callouts:** none directly — additive, but booking history correctness depends on T1's fix actually landing

### Scope Boundaries

- Do NOT implement the polished template itself — that's T4; this task can render a minimal/placeholder template sufficient for its own tests, refined in T4.
- Do NOT add a new filtered repo method for booking history — reuse `list_all_vouchers()` + Python filter (ARCH A3).
- Do NOT allow editing customer details — view-only, per REQ scope.

### Files Expected

**New files:**
- `tests/test_admin_customers.py`
- `templates/admin_customer_lookup.html` (minimal placeholder — bare structure for the 3 states + booking history, sufficient for T3's own route tests; substantially rewritten/polished by T4 — footprint amendment agreed during T3 implementation, since testing the routes end-to-end needs a renderable template)

**Modified files:**
- `main.py` (`/admin/customers`, `/admin/customers/export`, `/admin/bookings/export`)

**Must NOT modify:**
- `models.py`, `db/postgres_repo.py`'s `_FK_COLUMNS` (T1's scope, already landed)
- `persistence.py`, `db/postgres_repo.py`'s `list_customers()` (T2's scope, already landed)

---

## Task T4: Customer Lookup Template & Admin Nav

> **Status:** done
> **Verification:** ui
> **Effort:** m
> **Priority:** high
> **Depends on:** T3
> **Satisfies REQs:** R2, R5, R6, R7, R8, R9, R10, R11
> **Footprint slice:** New: `templates/admin_customer_lookup.html`; Modified: `templates/admin.html`
> **High-risk areas touched:** None — pure UI layer

### Description

Build `templates/admin_customer_lookup.html` — the search form, and 3 conditional render states (not-found/detail/picklist) that T3's route logic feeds. Add nav entry points on `templates/admin.html`: a link to `/admin/customers` and an "Export All Bookings" link to `/admin/bookings/export`, both following the existing `<a href="..." target="_blank">` nav-link pattern.

### Verification Checklist

#### Testable Seams (static HTML assertions, extending `tests/test_admin_customers.py`)

- **admin.html has a new link to /admin/customers** — expected: matches existing `<a href="..." target="_blank">` pattern (`templates/admin.html:247` is the model)
- **admin.html has a new "Export All Bookings" link** — expected: links to `/admin/bookings/export`
- **search form renders with a single input and submit control** — expected: one text input, one submit button/control
- **picklist state renders name/account code/company per entry** — expected: each entry links to `?q=<account_code>`
- **detail view renders all 7 customer fields, labeled** — expected: account_code, contact_name, contact_number, email, company_name, fleet_size, areas
- **booking history table renders a header row even when empty** — expected: no missing-table error state

#### Human-Verified Checklist

- [ ] Searching an exact account code shows the detail view immediately — expected: no picklist detour
- [ ] Searching a fuzzy name with multiple matches shows a clickable picklist, clicking an entry opens that customer's detail view — expected: working navigation loop
- [ ] Searching a term with no matches shows a clear "not found" message, no broken layout — expected: graceful empty state
- [ ] Per-customer export button downloads a CSV scoped to the currently-viewed customer — expected: correct file contents
- [ ] Global export button on `/admin` downloads a CSV covering all customers — expected: correct file contents
- [ ] Page is usable at "internal admin tool" polish level — expected: functional, not necessarily refined

### Implementation Notes

- **Module(s):** `templates/admin_customer_lookup.html`, `templates/admin.html`
- **Pattern reference:** `templates/admin.html:247` for nav links; `templates/admin_prices.html` for general admin-page structure/styling level
- **Key decisions:** REQ's confirmed single-page-with-3-states design; button/link copy explicitly left to dev/design discretion (REQ Out of Scope)
- **Libraries:** none new
- **High-risk callouts:** none — pure UI layer

### Scope Boundaries

- Do NOT prescribe exact button/link copy — REQ leaves this to dev/design discretion.
- Do NOT allow editing customer details from this template — view-only.
- Do NOT add styling/polish beyond making the 3 states clearly distinguishable — no gold-plating on an internal admin tool.

### Files Expected

**New files:**
- `templates/admin_customer_lookup.html`

**Modified files:**
- `templates/admin.html` (new nav link, new export link)
- `tests/test_admin_customers.py` (testable-seams assertions added)

**Must NOT modify:**
- `main.py` (T3's scope, already landed)
