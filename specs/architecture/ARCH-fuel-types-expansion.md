# Architecture: Three Fuel Types Across the Booking Flow

> **Date:** 2026-07-15
> **Phase:** 2 of 5 (System Architecture)
> **Requirements source:** specs/requirements/REQ-fuel-types-expansion.md
> **Type:** feature

## Architecture Summary

Extend fuel pricing/discounts from a single implicit type to three explicit fuel types (Biodiesel, Premium, Unleaded) by adding `fuel_type` as a new key dimension throughout the existing stack — no new services or modules. `prices`/`discounts` Postgres tables move from a `station_id` primary key to a composite `(station_id, fuel_type)` key; `vouchers`, `price_history`, and `discount_history` gain a `fuel_type` column. `price_store.py` and `discount_store.py` functions gain a `fuel_type` parameter, intentionally breaking their previously "unchanged" public API contract. `/book` becomes fuel-type-selection-first (form reordered), with client-side JS filtering the station list against a per-fuel-type dataset embedded server-side — reusing the existing `window.__STATION_TABLE__` pattern. The server always re-validates the selected `(station, fuel_type)` combo has a real price before accepting a booking, independent of client state. Admin prices, admin dashboard, and the supplier PDF surface the new dimension; three previously-unaccounted-for read-only JSON APIs get a backward-compatible `fuel_type` query param.

## High-Level Structure

```
                        ┌─────────────────────────────┐
                        │        main.py (Flask)       │
                        │                              │
  /book (GET/POST) ─────┤  reordered: Driver&Vehicle   │
                        │  (incl. Fuel Type) → Station  │
                        │  → snapshot capture → voucher │
                        │                              │
  /admin ───────────────┤  dashboard + fuel_type column │
  /admin/prices ────────┤  6 price/discount columns     │
  /admin/prices/update ─┤  + fuel_type in payload        │
  /admin/discounts/update┤ + fuel_type in form body       │
  /api/v1/prices ───────┤  + optional fuel_type param     │
  /api/v1/discounts ────┤  (default "Biodiesel")          │
  /api/v1/price_preview ┤                                 │
                        └───────────┬──────────────────┘
                                    │
                 ┌──────────────────┼───────────────────┐
                 ▼                  ▼                    ▼
         price_store.py     discount_store.py       models.py
         (+ fuel_type arg    (+ fuel_type arg      (+ "fuel_type" in
          on every fn)        on every fn)          VOUCHER_COLUMNS)
                 │                  │
                 └────────┬─────────┘
                           ▼
                  Postgres: prices, discounts
                  (composite PK station_id+fuel_type)
                  vouchers, price_history,
                  discount_history (+ fuel_type col)
```

Nothing new is added at the module level — this is a dimension-extension across existing modules. `persistence.py` (CSVRepo/PostgresRepo, used for customers/vouchers) is untouched by the pricing-schema change since `price_store.py`/`discount_store.py` talk to Postgres directly regardless of `PERSISTENCE_BACKEND`; only `VOUCHER_COLUMNS` in `models.py` (shared by both repos) is touched, since that's what governs whether `fuel_type` survives onto a voucher row in either backend.

## Tech Choices

| Area                       | Decision                                                              | Alternatives Considered                                        | Rationale                                                                                   |
|-----------------------------|------------------------------------------------------------------------|------------------------------------------------------------------|------------------------------------------------------------------------------------------------|
| Fuel-type representation    | Short canonical strings (`"Biodiesel"`, `"Premium"`, `"Unleaded"`) stored everywhere; PDF expands to full names at render time only | Separate enum/lookup table; store full names everywhere and shorten for dashboard | Matches existing pattern where `fuel_type` has always been a plain literal string; avoids a lookup table for a fixed 3-value set |
| Schema migration mechanism  | `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for new columns; guarded, rerun-safe block for the `prices`/`discounts` composite-PK change | One-off manual migration script outside `db/apply.py`'s normal boot flow | `db/apply.py` reruns `schema.sql` on every container start — must stay idempotent; this is the first structural (non-additive) change this codebase has made, so it needs explicit rerun-safety, not a bespoke one-time script |
| Station-list filtering (`/book`) | Client-side JS, extending existing `window.__STATION_TABLE__` embed (now keyed by fuel type) | Server round-trip re-render on fuel-type change | Matches the existing client-side cost-preview pattern already in `book.html`; avoids adding a page reload to a flow that's currently single-page-feeling |
| Booking-time price validation | Server re-checks `(station, fuel_type)` has a non-`None` price at submit, independent of client JS state | Trust the client-submitted station/fuel_type pair | Client state (JS-filtered dropdown) can be stale or tampered; explicit `None` check (not falsy/zero) avoids a DB-hiccup 0.0 fallback silently passing validation |
| `upsert_station()` scope    | Narrowed to station identity only (id/brand/name/location); pricing always goes through `set_price(station_id, fuel_type, price)` | Keep `upsert_station` bundling an initial price | Decouples station creation from per-fuel-type pricing, required for REQ-station-management's "bare station creation" flow and for a station to have 0–3 fuel types priced independently |
| Read-only `/api/v1/*` endpoints | Add optional `fuel_type` query param, default `"Biodiesel"` when omitted | Require `fuel_type` on every call (breaking); leave unchanged (broken once `price_store`/`discount_store` require the param) | Preserves backward compatibility for existing external supplier consumers who don't know about fuel types yet |

## Patterns & Conventions

- **Repo abstraction stays intact** — `main.py` never queries `prices`/`discounts`/`vouchers` tables directly; always through `price_store.py` / `discount_store.py` / the `repo` (CSVRepo/PostgresRepo) abstraction. This REQ extends those modules' signatures but does not bypass them.
- **`VOUCHER_COLUMNS` (`models.py`) remains the single canonical column list** both `CSVRepo.create_unverified_booking()` and `PostgresRepo.create_unverified_booking()` build voucher rows from — adding `"fuel_type"` here is the single source-of-truth change that fixes persistence in both backends at once.
- **Idempotent schema pattern, extended, not replaced** — `db/schema.sql`'s existing `CREATE TABLE IF NOT EXISTS` convention is preserved for new tables; the composite-PK change introduces the first `ALTER TABLE` this codebase has needed, following the same "safe to rerun on every boot" principle.
- **Flash-and-rerender error pattern** — new validation (missing price for selected fuel type, invalid `fuel_type` value on admin update) follows the existing `flash(...)` + re-render-with-`form_values` pattern already used for refuel-datetime validation in `/book`.

## Data Models

### `prices` (modified)

**Purpose:** current price per station per fuel type.

| Field                 | Type / Constraint                                  | Notes                                                        |
|------------------------|------------------------------------------------------|----------------------------------------------------------------|
| station_id             | VARCHAR(64), FK → stations(id), part of composite PK | unchanged column, role changes (no longer sole PK)              |
| fuel_type              | VARCHAR(30) NOT NULL, part of composite PK            | one of "Biodiesel" / "Premium" / "Unleaded"                     |
| price_php_per_liter    | NUMERIC(10,4) NOT NULL                                | unchanged                                                       |
| updated_at             | TIMESTAMPTZ NOT NULL DEFAULT NOW()                    | unchanged                                                       |

**Relationships:** many rows per station (0–3, one per fuel type actually priced); FK to `stations`.

**Lifecycle:** row created/updated via `price_store.set_price(station_id, fuel_type, price)`; absence of a row for a given `(station_id, fuel_type)` means that combo is unavailable for booking.

### `discounts` (modified)

Same shape change as `prices`: composite PK `(station_id, fuel_type)`, `discount_per_liter NUMERIC(8,4) NOT NULL`. Absence of a discount row for a priced `(station, fuel_type)` combo means ₱0 discount, not unavailability — distinct from `prices`, where absence means unavailability.

### `vouchers` (modified)

**New field:** `fuel_type VARCHAR(30)` — nullable (existing rows are NULL post-migration since the column never previously existed; new rows always populated). Display layer falls back to `"Diesel"` when NULL.

### `price_history`, `discount_history` (modified)

**New field:** `fuel_type VARCHAR(30)` — nullable, audit-only dimension, not part of any constraint. Old rows NULL (pre-migration history, left as-is, no backfill).

### `presets` (unused table — no change)

Driver/vehicle presets are stored entirely in CSV files (`data/presets/<account_code>.csv`), not the Postgres `presets` table, regardless of `PERSISTENCE_BACKEND`. The preset CSV already has a `fuel_type` column (currently always `"Diesel"`); no schema change needed here — this REQ changes how that value is *used* (default-only, overridable at booking time), not how it's stored.

## API Contracts / Interfaces

### `price_store.py` (module-boundary functions)

**Boundary:** internal module, imported by `main.py`.

| Op                                            | Signature                                                  | Purpose                                            | Errors / Returns                                        |
|------------------------------------------------|--------------------------------------------------------------|-------------------------------------------------------|--------------------------------------------------------------|
| list_stations                                  | `list_stations(fuel_type: str) -> List[Dict]`                 | stations with a price row for this fuel type            | `[]` if none                                                  |
| get_station                                    | `get_station(station_id: str, fuel_type: str) -> Optional[Dict]` | single station's price for this fuel type               | `None` if no price row exists for this combo                 |
| set_price                                      | `set_price(station_id: str, fuel_type: str, new_price: float) -> Dict` | admin price edit                                        | `ValueError` (0 < price ≤ 200), `KeyError` (station not found) |
| upsert_station                                 | `upsert_station(station: Dict) -> Dict`                       | create/update station identity only (no price)          | `ValueError` on missing required identity keys                |

### `discount_store.py` (module-boundary functions)

**Boundary:** internal module, imported by `main.py`.

| Op        | Signature                                                                 | Purpose                          | Errors / Returns                              |
|-----------|------------------------------------------------------------------------------|--------------------------------------|---------------------------------------------------|
| get_all   | `get_all(fuel_type: str) -> Dict[str, float]`                                | display_name → discount, this fuel type | `{}` if none                                       |
| get       | `get(station: str, fuel_type: str) -> Optional[float]`                       | single station's discount               | `None` if not set (means ₱0, per data model above) |
| set       | `set(station: str, fuel_type: str, value, actor, reason)`                    | admin discount edit                     | `DiscountValueError` on invalid value              |
| set_many  | `set_many(updates: List[Dict], actor, reason)`                               | bulk upsert/remove; each update dict carries its own `station` + `fuel_type` | — |

### HTTP routes (`main.py`)

**Boundary:** HTTP, Flask routes.

| Method/Path                     | Purpose                                                  | Auth            | Errors / Returns                                                        |
|-----------------------------------|--------------------------------------------------------------|--------------------|--------------------------------------------------------------------------|
| GET/POST `/book`                 | reordered (Driver&Vehicle incl. Fuel Type, then Station); on POST re-validates `(station, fuel_type)` price exists | public (account-code-gated) | flash + re-render on invalid datetime, missing preset selection, or missing price for selected combo |
| POST `/admin/prices/update`      | JSON body `{station_id, fuel_type, price}`                    | admin session       | 403 forbidden, 404 KeyError, 400 ValueError, 500 server_error             |
| POST `/admin/discounts/update`   | form body `station, fuel_type, discount_per_liter`             | admin session (redirect-to-login) | flash-based errors, same pattern as today                                |
| GET `/api/v1/prices`             | `?fuel_type=` optional, default `"Biodiesel"`                  | none (public read)  | `{"stations": [...]}`                                                    |
| GET `/api/v1/discounts`          | `?fuel_type=` optional, default `"Biodiesel"`                  | none (public read)  | `{"discounts": {...}}`                                                   |
| GET `/api/v1/price_preview`      | `?fuel_type=` optional, default `"Biodiesel"`, plus existing `station`/`amount`/`discount_per_liter` params | none (public read) | `{"ok": false, "error": ...}` on invalid input, unchanged shape otherwise |

**Auth requirements:** unchanged from today — admin routes behind `require_admin`; `/book` and `/api/v1/*` remain public.

## Module Boundaries

| Module / Package     | Responsibility                                                             | Allowed Dependencies                          |
|------------------------|--------------------------------------------------------------------------------|--------------------------------------------------|
| `price_store.py`       | owns all reads/writes to `prices`, `price_history`, station identity (`stations`) | `db/pool.py` only                                |
| `discount_store.py`    | owns all reads/writes to `discounts`, `discount_history`                        | `db/pool.py` only                                |
| `models.py`            | canonical `VOUCHER_COLUMNS` list, schema constants                              | none (pure constants)                            |
| `persistence.py` / `db/postgres_repo.py` | voucher/customer CRUD via `VOUCHER_COLUMNS`-shaped rows              | `models.py`, `db/pool.py`                        |
| `main.py`               | HTTP layer, orchestrates `price_store`/`discount_store`/repo, never queries DB directly for pricing/voucher data | `price_store.py`, `discount_store.py`, `persistence.py`, `models.py` |
| `report_pdf.py`         | renders supplier PDF from voucher rows already containing `fuel_type`           | reads plain dicts only, no DB access             |

## Change Footprint

### New files / modules

_None — this is a pure extension of existing modules; no new files created._

### Modified files / modules

| Path                              | What changes here                                                                                                 |
|-------------------------------------|------------------------------------------------------------------------------------------------------------------|
| `db/schema.sql`                    | `prices`/`discounts`: add `fuel_type` column, migrate PK to composite `(station_id, fuel_type)` behind a rerun-safe guard. `vouchers`, `price_history`, `discount_history`: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS fuel_type VARCHAR(30)`. |
| `db/seed_prices.sql`               | Each `INSERT` row gets `fuel_type='Biodiesel'`; `ON CONFLICT` target becomes `(station_id, fuel_type)`. Premium/Unleaded left unseeded. |
| `models.py`                        | Add `"fuel_type"` to `VOUCHER_COLUMNS`.                                                                            |
| `price_store.py`                   | `list_stations`, `get_station`, `set_price` gain `fuel_type` param; `upsert_station` narrows to identity-only (drops `price_php_per_liter` from required keys). Docstring's "Public API (unchanged)" claim corrected. |
| `discount_store.py`                | `get_all`, `get`, `set`, `set_many` gain `fuel_type` param(s). Same docstring correction.                          |
| `main.py`                          | `/book`: form reorder, new Fuel Type field, per-fuel `window.__STATION_TABLE__`, server-side price re-validation, `fuel_type` persisted on voucher row. "Add New Driver" sub-form: hidden hardcoded input → real 3-option select. `/admin`: dashboard render includes `fuel_type` column with `or "Diesel"` fallback. `admin_prices_update()`, `admin_discounts_update()`: accept `fuel_type` (note: `append_price_history()`'s legacy CSV audit trail is explicitly left unchanged — station-only, no `fuel_type` — redundant with Postgres `price_history`, out of scope). `/api/v1/prices`, `/api/v1/discounts`, `/api/v1/price_preview`: accept optional `fuel_type` query param, default `"Biodiesel"`. |
| `report_pdf.py`                    | `build_supplier_pdf()`: `header` list gains "Fuel Type" (7th column), each `rows.append([...])` gains a fuel-type cell (short→full name expansion), `col_widths` retuned for 7 columns. |
| `templates/book.html`              | Reorder Driver&Vehicle before Station; new Fuel Type `<select>`; hidden `fuel_type` input → real select; 3 stacked collapsible price×discount tables; `window.__STATION_TABLE__` restructured keyed by fuel type; JS filtering logic added. |
| `templates/admin_prices.html`      | 6 new price/discount columns (3 fuel types × 2); readable `Date + 24H` timestamp under both price and discount cells. |
| `templates/admin.html`             | New fuel-type column on the bookings table.                                                                        |

### Deleted / replaced

_None._

### Touched but not changed (silent-regression hotspots)

| Path                                    | Why it matters                                                                                          |
|-------------------------------------------|--------------------------------------------------------------------------------------------------------------|
| `tests/test_book_fuel_field.py`           | Asserts the field is hidden and hardcoded to "Diesel" — directly contradicted, must be rewritten.             |
| `tests/test_book_station_filter.py`       | Asserts discount-gated hiding — directly contradicted by the price-gate reversal, must be rewritten.           |
| `tests/test_book_pg.py`                   | Posts `"fuel_type": "Diesel"` (line 137) — not one of the 3 canonical values, needs updating.                  |
| `tests/test_schema.py`                    | `test_vouchers_has_all_voucher_columns` self-validates every `VOUCHER_COLUMNS` entry has a matching DB column — passes once migration lands, useful canary if migration is incomplete. |
| `tests/test_seeds.py`                     | Cross-checks `price_store._DEFAULT_STATIONS` against seed files — low risk, `_DEFAULT_STATIONS` shape unchanged. |

## Areas of Impact

| Area                              | Impact                                                          | Risk (L/M/H) | Why                                                                                     |
|--------------------------------------|----------------------------------------------------------------------|---------------|----------------------------------------------------------------------------------------------|
| Booking flow (`/book`)               | Form reorder, new field, client-side filtering, server re-validation | H             | Largest behavioral surface change in the REQ; direct customer-facing flow                     |
| Schema migration (`prices`/`discounts` PK) | First-ever `ALTER TABLE` on a live-shape table in this codebase   | H             | `db/apply.py` reruns on every boot; a bad migration blocks every future deploy, not just this one |
| Admin pricing (`/admin/prices`)      | 6 new columns, composite-key edit endpoints                          | M             | Mostly additive UI, but built on the risky schema change                                      |
| External supplier API consumers (`/api/v1/*`) | New optional `fuel_type` param, default preserves old behavior | L             | Backward-compatible by design, but external consumer identity/usage isn't fully known internally |
| Supplier PDF export                  | New column, retuned layout                                           | L             | Pure addition, low complexity                                                                 |
| Admin dashboard                      | New column with documented NULL fallback                             | L             | Pure addition                                                                                  |

**Contract changes:** `price_store.py` and `discount_store.py` public function signatures change (breaking, intentional) — internal-only, no external consumers. `/api/v1/prices`, `/api/v1/discounts`, `/api/v1/price_preview` gain a new optional query param — additive, non-breaking for existing external supplier integration.

**Cross-cutting ripples:** none into auth, telemetry, feature flags, or the build/deploy pipeline beyond the schema-migration step already baked into `db/apply.py`'s existing boot-time execution.

## Cross-Cutting Concerns

- **Errors:** invalid/missing `(station, fuel_type)` combo at booking submit → flash + re-render with form values preserved, matching existing datetime-validation pattern. Admin price/discount update with an unrecognized `fuel_type` value → 400, matching existing invalid-price handling.
- **Logging & metrics:** no new infra; reuse existing `print(f"⚠️ ...")` non-fatal error pattern already used throughout `main.py`.
- **Auth / authz:** unchanged — admin routes stay behind `require_admin`; `/book` and `/api/v1/*` remain public as today.
- **Performance:** `list_stations(fuel_type)` called 3× per `/book` render (once per fuel type) for the JS embed — acceptable at current ~10-station scale; revisit if station count grows to hundreds.
- **Security:** server-side re-validation of `(station, fuel_type)` price existence at submit closes a client-tamper vector; explicit `None` check avoids a 0.0-fallback false-pass during a DB hiccup.
- **Migrations / rollout:** composite-PK migration on `prices`/`discounts` must be tested against a copy of the prod schema before shipping — this is the single highest-risk deployment step in the REQ, since `db/apply.py` executes on every container boot.

## Architecture Decisions Log

| #   | Decision                                                                 | Alternatives                                                     | Chosen Because                                                                                     | Satisfies REQs |
|-----|-------------------------------------------------------------------------|-----------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------|-----------------|
| A1  | Fuel type stored as short canonical strings everywhere; PDF expands to full names at render time | Separate enum/lookup table; store full names everywhere            | Matches existing plain-string pattern; avoids a lookup table for a fixed 3-value set                   | R1, R15         |
| A2  | `prices`/`discounts` PK becomes composite `(station_id, fuel_type)`; new columns on `vouchers`/`price_history`/`discount_history` via idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` | One-off manual migration outside `db/apply.py`'s normal flow          | `db/apply.py` reruns schema.sql on every boot — must stay rerun-safe                                   | R9, R10, R17    |
| A3  | Historical `vouchers.fuel_type` is NULL post-migration; resolved via display-layer fallback (`fuel_type or "Diesel"`), not a data backfill | One-time `UPDATE vouchers SET fuel_type='Diesel' WHERE fuel_type IS NULL` | Avoids a data-mutating migration statement; achieves the same visible behavior, stays idempotent        | R16             |
| A4  | `price_store.upsert_station()` narrowed to station-identity only; pricing always via separate `set_price()` | Keep `upsert_station` bundling an initial price                       | Decouples station creation from per-fuel-type pricing; enables bare station creation (REQ-station-management) | R6 (this REQ), R6 (REQ-station-management) |
| A5  | Station-list filtering on `/book` is client-side JS, extending `window.__STATION_TABLE__` | Server round-trip re-render on fuel-type change                        | Matches existing client-side cost-preview pattern; avoids adding a page reload                          | R7              |
| A6  | Server re-validates `(station, fuel_type)` price existence at booking submit, explicit `None` check | Trust client-submitted station/fuel_type pair                          | Client state can be stale/tampered; explicit `None` check avoids DB-hiccup false-pass                   | R7, R9          |
| A7  | Station availability gate reverses to price-presence-only (was discount>0) | Keep existing discount-gate behavior                                   | User confirmed this behavior reversal is intentional, not an REQ oversight                              | R10             |
| A8  | `/api/v1/prices`, `/api/v1/discounts`, `/api/v1/price_preview` gain optional `fuel_type` param, default `"Biodiesel"` | Require `fuel_type` on every call (breaking); leave unmodified (silently broken) | Preserves backward compatibility for external supplier consumers                                        | Inferred (not in original REQ) |

## Risk & Stress-Test Scenarios

### Forward — runtime failure scenarios

| Scenario                                                              | How the Design Handles It                                                                                   |
|--------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------|
| Two admins edit the same station's Premium price simultaneously          | Existing `ON CONFLICT DO UPDATE` on the composite key — same last-write-wins semantics as today's single-key upserts, no new race introduced |
| Postgres unreachable during `/book` submit                               | Existing `try/except` around snapshot capture degrades gracefully; new price-existence re-validation uses an explicit `None` check (not falsy/zero) so a DB hiccup can't silently pass a booking with no real price |
| Composite-PK migration ships broken                                      | GAP — `db/apply.py` reruns on every boot, so a bad migration blocks every future deploy. Mitigation: test against a prod-schema copy before shipping; guard logic must not corrupt existing single-row-per-station data before new code is ready to read it |
| Station count grows from ~10 to hundreds                                 | `list_stations(fuel_type)` called 3× per `/book` render — acceptable now, revisit query pattern if station count grows significantly |

### Backward — regression risk per touched area

| Touched area (from Change Footprint)     | What could regress                                                     | How we'd know / mitigation                                                     |
|---------------------------------------------|------------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| `main.py` `/book` station-list logic         | Existing "hide stations without discount" behavior disappears (intentional) | `test_book_station_filter.py` rewritten to assert the new price-gate behavior instead |
| `main.py` "Add New Driver" fuel_type field    | Existing hidden/hardcoded "Diesel" contract breaks (intentional)             | `test_book_fuel_field.py` rewritten to assert the new visible 3-option select        |
| `price_store.py`/`discount_store.py` signatures | Any unaccounted-for internal caller passing the old (no-fuel_type) signature | Full-repo grep for all call sites before merging; `/api/v1/*` explicitly covered by A8 |
| `/api/v1/*` external supplier consumers       | Consumer breaks if it doesn't send `fuel_type` and backend defaults wrong    | Default value (`"Biodiesel"`) chosen specifically to match pre-migration behavior     |
| `tests/test_book_pg.py`                       | Posts literal `"Diesel"` as fuel_type — behavior once validation is stricter | Test updated to use a canonical value; decide during implementation whether unknown values are rejected or passed through |

## Open Questions

- Should `/book`'s server-side booking validation **reject** an unrecognized `fuel_type` value outright (strict enum check), or remain permissive (accept any string, as today)?
  - **Impact if unresolved:** a stale client or bad actor could submit a booking with a `fuel_type` value outside the 3 canonical ones; if permissive, this data quietly diverges from the UI's intended 3-value set.
  - **Suggested default:** reject with a flash error if `fuel_type` isn't one of the 3 canonical values — matches the existing strict-validation pattern for other `/book` fields (refuel datetime, preset selection).

- Should `report_pdf.py`'s hardcoded title string (`"UniFleet – Diesel Refuel Vouchers (Offline Version)"`) be updated now that Diesel is one of three fuel types?
  - **Impact if unresolved:** cosmetic staleness only; no functional impact.
  - **Suggested default:** leave as-is per REQ scope boundaries (not in scope for this REQ); revisit in a future copy pass.

## Out of Scope

- Migrating/backfilling existing single price/discount values into the new per-fuel-type model (reason: REQ confirmed all current prices are stale and will be re-entered post-launch).
- Relabeling historical booking records or legacy preset fuel-type values (reason: explicitly preserved as-is / left blank per REQ decisions).
- Migrating driver/vehicle presets off CSV storage onto the unused Postgres `presets` table (reason: pre-existing gap, unrelated to this REQ's scope — presets already function correctly via CSV for the fuel-type default/override behavior needed here).
- Updating `report_pdf.py`'s hardcoded title string (reason: cosmetic, not functionally required; captured as an Open Question instead).
- Station management (add/edit/deactivate stations) — covered separately by REQ-station-management; this REQ only consumes the identity/`upsert_station()` split, doesn't build the management UI.
- Adding `fuel_type` to `main.py`'s legacy `append_price_history()` CSV audit trail (`PRICE_HISTORY_FIELDS`) — redundant with the Postgres `price_history` table (which does get `fuel_type` via T1); left station-only/unchanged per explicit decision during task generation.

---

# Tasks

## Task T1: Schema & Seed Migration for Per-Fuel-Type Pricing

> **Status:** done
> **Verification:** test-after
> **Effort:** m
> **Priority:** critical
> **Depends on:** None
> **Satisfies REQs:** R9, R10, R17
> **Footprint slice:** Modified: `db/schema.sql`, `db/seed_prices.sql`
> **High-risk areas touched:** Schema migration (`prices`/`discounts` PK) — H risk

### Description

Extend `prices` and `discounts` from a 1:1 station keying to a composite `(station_id, fuel_type)` keying, and add a `fuel_type` column to `vouchers`, `price_history`, and `discount_history`. This is the foundation everything else in the fuel-types feature depends on. It's the first non-additive (`ALTER TABLE`) schema change this codebase has made, and `db/apply.py` reruns `schema.sql` on every container boot, so the migration must be safe to run repeatedly and against a database already holding legacy single-row-per-station data.

### Test Plan

#### Test File(s)
- `tests/test_schema.py` (extend existing file, following its `schema_db` fixture pattern)

#### Test Scenarios

##### Composite Key Shape

- **prices has composite PK (station_id, fuel_type)** — GIVEN db/schema.sql applied WHEN inspecting `prices`' PK constraint THEN both `station_id` and `fuel_type` are key columns _(verifies R9)_
- **discounts has composite PK (station_id, fuel_type)** — same shape _(verifies R10)_
- **prices allows multiple fuel_type rows per station** — GIVEN a station WHEN inserting rows for Biodiesel and Premium (same station_id) THEN both insert successfully, no PK violation _(verifies R9 — partial fuel coverage)_

##### New Columns

- **vouchers.fuel_type exists and is nullable** — GIVEN schema applied WHEN inspecting `vouchers` columns THEN `fuel_type` exists, `VARCHAR(30)`, no NOT NULL constraint _(verifies R17 prerequisite)_
- **price_history.fuel_type and discount_history.fuel_type exist, nullable** — audit dimension _(supports R9/R10 auditability)_

##### Seed Data

- **seed_prices.sql seeds fuel_type='Biodiesel' for all 10 default stations** — GIVEN seed applied WHEN querying `prices` THEN each of the 10 stations has exactly one row with `fuel_type='Biodiesel'` and no Premium/Unleaded rows exist _(verifies ARCH: no required migration beyond Biodiesel)_

##### Edge Cases

- **discount row is independent/optional per (station, fuel_type)** — GIVEN a station with a Biodiesel price WHEN no discounts row exists for that (station, Biodiesel) pair THEN no schema constraint prevents this _(verifies R10 edge case: missing discount ≠ unavailable)_
- **station with only 2 of 3 fuel types priced** — GIVEN a station WHEN inserting Biodiesel + Premium rows only THEN no Unleaded row exists and no error occurs _(REQ edge case)_

##### Resilience

- **schema.sql is rerunnable without error after the migration** — GIVEN the migrated schema already applied WHEN `db/apply.py db/schema.sql` is run again THEN it exits 0 and does not duplicate/corrupt `(station_id, fuel_type)` rows _(verifies ARCH forward-stress: composite-PK migration ships broken — GAP mitigation)_
- **migration preserves existing single-price data during the PK transition** — GIVEN a `prices` table already containing legacy single-row-per-station data (pre-migration state, no `fuel_type` column) WHEN the post-migration schema.sql is applied THEN existing rows survive, auto-assigned `fuel_type='Biodiesel'` via the `DEFAULT` clause, not dropped _(verifies ARCH: guard logic must not corrupt existing data)_

##### Regression Guard

- **existing FK relationship tests still pass** — `test_foreign_key[prices-station_id-stations-id]` and `test_foreign_key[discounts-station_id-stations-id]` (existing, parametrized) _(guards backward-regression risk for `db/schema.sql`)_
- **existing apply idempotency/no-data-drop tests still pass** — `test_apply_is_idempotent_against_an_existing_schema`, `test_apply_does_not_drop_existing_data` (existing, in `tests/test_apply.py`) _(guards backward-regression risk for `db/apply.py` + `db/schema.sql`)_

### Implementation Notes

- **Module(s):** `db/schema.sql`, `db/seed_prices.sql` (schema/data layer, no application code)
- **Pattern reference:** existing `CREATE TABLE IF NOT EXISTS` idempotent-script convention already established in `db/schema.sql`
- **Key decisions:** ARCH A2 (composite PK via idempotent, guarded `ALTER TABLE`), A3 (vouchers.fuel_type nullable, no backfill — display-layer fallback handled in a later task)
- **Libraries:** none new — plain SQL via `db/apply.py`'s existing psycopg-based applier
- **High-risk callouts:** this is the H-risk "Schema migration" Area of Impact from ARCH — a bad migration blocks every future deploy since `db/apply.py` runs on every boot. Use `ADD COLUMN fuel_type VARCHAR(30) NOT NULL DEFAULT 'Biodiesel'` before swapping the PK so existing rows survive automatically; guard the PK-swap step (check current PK shape before `DROP CONSTRAINT`/`ADD PRIMARY KEY`) so a second run doesn't error. The existing `_primary_key()` test helper in `tests/test_schema.py` only returns one column (`fetchone()`) — needs a new/adjusted helper to assert a composite PK.

### Scope Boundaries

- Do NOT backfill Premium/Unleaded prices — intentionally left unseeded (ARCH Out of Scope).
- Do NOT change `price_store.py`/`discount_store.py` — that's T2.
- Do NOT touch the unused Postgres `presets` table — out of scope per ARCH.

### Files Expected

**Modified files:**
- `db/schema.sql` (composite PK migration for `prices`/`discounts`; `ADD COLUMN IF NOT EXISTS fuel_type` for `vouchers`, `price_history`, `discount_history`)
- `db/seed_prices.sql` (fuel_type='Biodiesel' seeding, composite `ON CONFLICT`)

**Must NOT modify:**
- `price_store.py`, `discount_store.py` (T2's scope)
- `main.py`, any template (later tasks' scope)

---

## Task T2: `price_store`/`discount_store` Fuel-Type-Aware API

> **Status:** done
> **Verification:** tdd
> **Effort:** m
> **Priority:** critical
> **Depends on:** T1
> **Satisfies REQs:** R7, R9, R10
> **Footprint slice:** Modified: `price_store.py`, `discount_store.py`
> **High-risk areas touched:** None directly (foundational to H-risk booking flow, but this task itself is internal-module-only)

### Description

Add `fuel_type` as a required parameter across every public function in `price_store.py` and `discount_store.py`, since pricing/discounts now key on `(station_id, fuel_type)` instead of `station_id` alone. Deliberately breaks both modules' previously-documented "Public API (unchanged)" contract. `upsert_station()` narrows to station-identity-only, decoupling station creation from pricing (ARCH A4).

### Test Plan

#### Test File(s)
- `tests/test_price_store.py` (new — no existing dedicated test file)
- `tests/test_discount_store.py` (new — no existing dedicated test file)

Both follow the `schema_db`/`postgres_db` fixture pattern already established in `tests/test_schema.py` / `tests/test_apply.py`.

#### Test Scenarios

##### price_store — Per-Fuel-Type Reads

- **list_stations returns only stations priced for the given fuel type** — GIVEN stations with mixed fuel-type price coverage WHEN `list_stations("Premium")` is called THEN only stations with a Premium price row are returned _(verifies R9, R7)_
- **get_station returns None for an unpriced combo** — GIVEN a station with no Unleaded price row WHEN `get_station(id, "Unleaded")` is called THEN `None` is returned _(verifies R7 — availability gate)_

##### price_store — Writes

- **set_price upserts into the composite-key row without affecting other fuel types** — GIVEN a station with an existing Biodiesel price WHEN `set_price(id, "Premium", 65.0)` is called THEN the Premium row is created/updated and the Biodiesel row is unchanged _(verifies R9)_
- **upsert_station creates/updates identity only** — GIVEN a new station dict with no price field WHEN `upsert_station(station)` is called THEN it succeeds without requiring `price_php_per_liter` _(verifies A4)_

##### discount_store — Per-Fuel-Type Reads

- **get_all returns only the given fuel type's discounts** — GIVEN discounts set for multiple fuel types WHEN `get_all("Unleaded")` is called THEN only Unleaded discounts are returned, keyed by display name _(verifies R10)_
- **get returns None when no discount row exists** — GIVEN a priced-but-undiscounted (station, fuel_type) combo WHEN `get(station, fuel_type)` is called THEN `None` is returned (meaning ₱0 downstream, not unavailable) _(verifies R10)_

##### discount_store — Writes

- **set upserts into the composite-key row and appends a history row with fuel_type** — GIVEN a station WHEN `set(station, "Premium", 2.5, actor, reason)` is called THEN the discounts row is upserted and `discount_history` gets a new row including `fuel_type` _(verifies R10)_
- **set_many applies per-update fuel_type atomically** — GIVEN a batch of updates each carrying its own `fuel_type` WHEN `set_many(updates, actor, reason)` is called THEN all upserts/removals and history rows are applied in one transaction _(verifies R10)_

##### Edge Cases

- **a station can go from 0 to 3 fuel types priced independently** — GIVEN a bare station WHEN `set_price` is called 3 times with different fuel types THEN all 3 rows exist independently, no prior row required for any of them _(verifies R9 edge case)_

##### Regression Guard

- **_DEFAULT_STATIONS shape is unchanged** — `tests/test_seeds.py`'s existing cross-check against `price_store._DEFAULT_STATIONS` still passes _(guards backward-regression risk)_
- **clear_all() behavior is unchanged** — GIVEN discounts across multiple fuel types WHEN `clear_all()` is called THEN all discount rows are removed regardless of fuel type (dead code today, no caller depends on fuel-scoped clearing, left as-is) _(guards backward-regression risk for `discount_store.py`)_

### Implementation Notes

- **Module(s):** `price_store.py`, `discount_store.py` (per ARCH Module Boundaries — each owns its own table(s), depends only on `db/pool.py`)
- **Pattern reference:** existing single-key function bodies in both files — same query/upsert shape, extended to a composite key
- **Key decisions:** ARCH A4 (`upsert_station` split from pricing); validation of `fuel_type` values stays permissive at this layer (confirmed) — enum-checking happens at the HTTP boundary in T3/T6
- **Libraries:** none new — same `psycopg`/`db.pool` usage as today
- **High-risk callouts:** none directly; this task's correctness underpins the H-risk booking flow (T3/T4), so its test coverage matters disproportionately even though the module itself is low-risk in isolation

### Scope Boundaries

- Do NOT change `main.py` call sites — that's T3/T4/T6.
- Do NOT add `fuel_type` enum validation at this layer (confirmed permissive).
- Do NOT change `clear_all()` behavior — dead code, left as-is.

### Files Expected

**New files:**
- `tests/test_price_store.py`
- `tests/test_discount_store.py`

**Modified files:**
- `price_store.py` (`list_stations`, `get_station`, `set_price` gain `fuel_type`; `upsert_station` narrows to identity-only)
- `discount_store.py` (`get_all`, `get`, `set`, `set_many` gain `fuel_type`; `discount_history` inserts include `fuel_type`)

**Must NOT modify:**
- `main.py` (T3/T4/T6's scope)
- `db/schema.sql`, `db/seed_prices.sql` (T1's scope, already landed)

---

## Task T3: Booking Persistence & Validation

> **Status:** done
> **Verification:** tdd
> **Effort:** m
> **Priority:** critical
> **Depends on:** T1, T2, T4
> **Satisfies REQs:** R2, R4, R5, R7, R10, R17
> **Footprint slice:** Modified: `models.py`, `main.py` (`/book` POST handler — snapshot capture, price-existence validation, row persistence)
> **High-risk areas touched:** Booking flow (`/book`) — H risk

### Description

Add `fuel_type` to `models.VOUCHER_COLUMNS` (the single fix that makes both CSV and Postgres backends persist it correctly) and, within `/book`'s POST handler, use the fuel-type-aware `price_store`/`discount_store` calls (T2) to capture the booking's price/discount snapshot, reject the booking server-side if the submitted `(station, fuel_type)` combo has no price, and persist the resolved `fuel_type` onto the voucher row. Assumes T4 has resolved which value `fuel_type` holds (booking-level, independent of the driver preset) — this task owns validating and persisting that value, not parsing where it came from.

### Test Plan

#### Test File(s)
- `tests/test_book_pg.py` (extend existing file, using its backend-agnostic `RepoStub` pattern — stubs `main.repo` directly, so no real Postgres/CSV needed)

#### Test Scenarios

##### Persistence

- **booking row includes fuel_type matching the submitted value** — GIVEN a booking POST with `fuel_type=Premium` WHEN the row reaches `create_unverified_booking` THEN `row["fuel_type"] == "Premium"` _(verifies R2, R17)_
- **overridden fuel type persists as submitted, not the preset default** — GIVEN a preset with stored default "Unleaded" WHEN the booking POST submits `fuel_type=Premium` THEN the persisted row has `fuel_type == "Premium"` _(verifies R2, R3)_
- **VOUCHER_COLUMNS includes fuel_type** — GIVEN `models.VOUCHER_COLUMNS` THEN `"fuel_type"` is present _(verifies R17 — single source fixing both backends)_

##### Preset Isolation

- **override does not write back to the preset's stored default** — GIVEN an existing preset with default "Unleaded" WHEN a booking overrides to "Premium" and succeeds THEN re-reading the preset CSV shows the default is still "Unleaded" _(verifies R4)_

##### Server-Side Validation

- **missing price for (station, fuel_type) rejects the booking** — GIVEN a station with no price row for "Unleaded" WHEN a booking POST submits that combo THEN the response flashes an error and re-renders with form values preserved, no voucher created _(verifies R7)_
- **price present but discount absent still succeeds, with ₱0 discount** — GIVEN a priced-but-undiscounted combo WHEN booked THEN the booking succeeds and the discount snapshot is 0 _(verifies R10)_
- **a DB hiccup returning a falsy/zero price is not mistaken for "no price"** — GIVEN `price_store.get_station` mocked to return a price of `0.0` (vs. `None`) WHEN validated THEN behavior differs from the true-missing (`None`) case _(verifies ARCH A6)_

##### Edge Case

- **legacy preset with stored fuel_type="Diesel" has no server-side special-casing** — GIVEN a booking submitted with a blank/missing `fuel_type` (as would happen if the field wasn't filled in) WHEN validated THEN it's rejected the same as any other missing-price case, no silent default to "Diesel" or "Biodiesel" _(verifies R5, R7)_

##### Regression Guard

- **test_booking_save_resolves_account_code updated to mock a real price** — GIVEN the existing test's `"station": "Test Station"` WHEN `price_store` is mocked to return a valid price for the submitted fuel type THEN the test continues to verify `account_code` passthrough without tripping the new price-existence rejection _(guards backward-regression risk for `tests/test_book_pg.py`)_
- **test_vouchers_has_all_voucher_columns still passes** — existing self-validating test in `tests/test_schema.py`, confirms T1's schema change lines up with T3's `VOUCHER_COLUMNS` change

### Implementation Notes

- **Module(s):** `models.py`, `main.py` (per ARCH Module Boundaries)
- **Pattern reference:** existing refuel-datetime validation in `/book` (flash + re-render with `form_values` preserved) is the model for the new price-existence rejection
- **Key decisions:** ARCH A6 (explicit `None` check for price existence, not falsy/zero); R4 (booking override never writes back to the preset's stored default — preset CSV write path stays untouched by this task)
- **Libraries:** none new
- **High-risk callouts:** this task's footprint sits inside the H-risk "Booking flow" Area of Impact — its test coverage is what actually proves the price-gate reversal (ARCH A7) and R7 work correctly, not just that the code compiles

### Scope Boundaries

- Do NOT change how `fuel_type` is parsed from `request.form` or how the driver-preset default is resolved/prefilled — that's T4.
- Do NOT touch `append_price_history()` — explicitly out of scope (legacy CSV audit trail, left unchanged per decision during task generation).
- Do NOT modify preset CSV write logic — override must not mutate the stored default (R4).

### Files Expected

**Modified files:**
- `models.py` (`VOUCHER_COLUMNS` gains `"fuel_type"`; new `FUEL_TYPES = ["Biodiesel", "Premium", "Unleaded"]` constant — no prior shared source of truth for the 3-value list, T4 imports this to iterate per-fuel-type data)
- `main.py` (`/book` POST handler: snapshot capture gains `fuel_type` param; new price-existence check; row persistence includes `fuel_type`)

**Must NOT modify:**
- `price_store.py`, `discount_store.py` (T2's scope, already landed)
- `append_price_history()` in `main.py` (explicitly out of scope)
- Preset CSV write logic (must remain untouched by booking-time overrides)

---

## Task T4: `/book` Route — Fuel Type Field Resolution & Per-Fuel Station Data Prep

> **Status:** done
> **Verification:** test-after
> **Effort:** m
> **Priority:** critical
> **Depends on:** T1, T2
> **Satisfies REQs:** R2, R3, R5, R7, R9
> **Footprint slice:** Modified: `main.py` (`/book` handler — field parsing, default resolution, per-fuel station data prep)
> **High-risk areas touched:** Booking flow (`/book`) — H risk

### Description

Within `/book`'s handler, parse the new independent Fuel Type form field (decoupled from `driver_data`), resolve its default/prefill value from the selected preset (blank for legacy `"Diesel"` presets), and build per-fuel-type station data (calling `price_store.list_stations(fuel_type)` for each of the 3 types) to pass into the template context — replacing the old flat `station_names`/`station_table` variables. This is the data-prep half of the `/book` handler change; T3 owns validating/persisting the resolved value, T5 owns rendering it.

### Test Plan

#### Test File(s)
- `tests/test_book_fuel_field.py` (rewritten — currently asserts the old hidden/hardcoded field)
- `tests/test_book_station_filter.py` (rewritten — currently asserts the discount-gate)

#### Test Scenarios

##### Field Resolution

- **renders a single, independent Fuel Type field** — GIVEN `/book` with a resolved customer WHEN rendered THEN a visible Fuel Type field exists, no longer hidden/hardcoded _(verifies R2)_
- **selecting a preset pre-fills Fuel Type from its stored default** — GIVEN a preset with stored `fuel_type="Unleaded"` WHEN selected THEN the Fuel Type field defaults to "Unleaded" _(verifies R3)_
- **legacy preset with stored fuel_type="Diesel" has no pre-filled default** — GIVEN such a preset WHEN selected THEN the Fuel Type field starts unset _(verifies R5)_
- **submitted value is independent of driver_data** — GIVEN an existing-preset booking WHEN the Fuel Type field is overridden THEN `driver_data` for that request carries no fuel_type from `parts[5]`, and the booking's fuel_type is sourced solely from the top-level field _(verifies R2, R4 — data-flow correctness, complements T3's persistence-side test)_

##### Data Prep

- **per-fuel-type station data contains only correctly-priced stations** — GIVEN mixed fuel-type price coverage across stations WHEN `/book` is rendered THEN each fuel type's dataset (feeding `window.__STATION_TABLE__`) contains only stations priced for that type _(verifies R7, data shape T5's JS consumes)_

##### Edge Cases

- **customer with zero presets, driver_mode=new** — Fuel Type field has no preset to default from, starts unset _(R3 edge case)_
- **station priced for only 1 of 3 fuel types** — appears only in that fuel type's data set, absent from the other two _(R9 edge case, data-prep side)_

##### Regression Guard

- **discount-gate assertions replaced with price-gate assertions** — station with a price but no discount now appears (was hidden); station with zero/no price stays hidden _(guards + intentionally supersedes backward-regression risk — ARCH A7)_
- **hidden-hardcoded-field assertions replaced with visible-field assertions** _(guards + intentionally supersedes backward-regression risk)_

### Implementation Notes

- **Module(s):** `main.py` (`/book` handler)
- **Pattern reference:** existing preset-loading logic (`pd.read_csv(preset_path, ...)`) stays as-is; this task adds fuel-type resolution alongside it, doesn't replace the preset-loading mechanism
- **Key decisions:** ARCH A5 (client-side JS filtering — this task only supplies correctly-shaped per-fuel-type data, doesn't filter it; filtering is T5's JS); R5 (legacy "Diesel" preset shows no default, no auto-mapping to "Biodiesel")
- **Libraries:** none new — uses `models.FUEL_TYPES` (added in T3) to iterate the 3 types
- **High-risk callouts:** part of the H-risk "Booking flow" area — the multiple `render_template` call sites (GET, and 3+ POST validation-failure re-renders) must all receive the same per-fuel-type data shape consistently, or the template (T5) breaks on some paths and not others

### Scope Boundaries

- Do NOT implement the client-side JS filtering logic itself — that's T5.
- Do NOT touch the price-existence validation/rejection or voucher persistence — that's T3.
- Do NOT change the preset CSV read/write mechanism itself, only what data flows through `driver_data`.

### Files Expected

**Modified files:**
- `main.py` (`/book` handler: new top-level `fuel_type` field parsing; default/prefill resolution; `driver_data`'s `'preset'` branch no longer includes `fuel_type`; `'new'` branch's `driver_data['fuel_type']` sourced from the same top-level field; per-fuel-type station data built and passed to every `render_template('book.html', ...)` call site)
- `models.py` (`FUEL_TYPES = ["Biodiesel", "Premium", "Unleaded"]` constant — reassigned here from T3 due to a circular-dependency note in the original task specs: T3 depends on T4, but T4 needs this constant, so it lands with whichever task actually runs first)

**Must NOT modify:**
- The price-existence validation/rejection logic (T3's scope)
- Voucher persistence / `repo.create_unverified_booking()` call (T3's scope)
- `templates/book.html` (T5's scope)

---

## Task T5: `/book` Template & Client-Side JS Filtering

> **Status:** done
> **Verification:** ui
> **Effort:** m
> **Priority:** critical
> **Depends on:** T4
> **Satisfies REQs:** R2, R3, R6, R7, R13
> **Footprint slice:** Modified: `templates/book.html`
> **High-risk areas touched:** Booking flow (`/book`) — H risk

### Description

Reorder `templates/book.html` so Driver & Vehicle (including the new Fuel Type field) renders before Station selection, add the visible 3-option Fuel Type `<select>`, replace the single "white" price×discount table with 3 stacked, independently collapsible tables (one per fuel type), and extend the existing client-side JS (`window.__STATION_TABLE__` cost-preview pattern) so changing Fuel Type re-filters the Station dropdown without a page reload. Pure rendering/UX layer — consumes the data T4 prepares, doesn't validate or persist anything itself.

### Verification Checklist

#### Testable Seams (static HTML assertions, new test file)

- **Driver & Vehicle section precedes Station section in rendered HTML** — expected: form-reorder confirmed via element order in the response body
- **Fuel Type `<select>` renders with exactly 3 options** — expected: Biodiesel, Premium, Unleaded, no others
- **Fuel Type field has an associated `<label>`** — expected: matches existing Station field's a11y pattern
- **3 distinct price×discount tables render, each in a collapsible element** — expected: 3 separate `<details>` (or equivalent) blocks
- **`window.__STATION_TABLE__` is keyed by fuel type** — expected: `{"Biodiesel": [...], "Premium": [...], "Unleaded": [...]}`, not a flat array

#### Human-Verified Checklist

- [ ] Selecting an existing preset visually pre-fills Fuel Type to that preset's stored default — expected: field shows the preset's value
- [ ] Selecting a preset with legacy `"Diesel"` default leaves Fuel Type visually unset — expected: no option pre-selected
- [ ] Changing Fuel Type re-populates the Station `<select>` without a page reload — expected: only stations priced for that fuel type appear, no visible navigation/reload
- [ ] Switching Fuel Type after a Station was already selected that's invalid for the new fuel type — expected: Station selection resets/clears, no stale invalid selection carried forward
- [ ] Each of the 3 price×discount tables independently expands/collapses, no forced sync to the currently-selected fuel type — expected: matches brief's "user can minimize 1-3 fuel tables" note
- [ ] Live cost-preview calculator recalculates correctly for the currently selected station+fuel_type pair — expected: no stale data from a prior selection
- [ ] Mobile/narrow-viewport layout remains usable — expected: no horizontal overflow, tables don't break layout
- [ ] "Add New Driver" mode has no separate/duplicate Fuel Type field — expected: old hidden hardcoded input fully removed, single top-level field is the only one

### Implementation Notes

- **Module(s):** `templates/book.html`
- **Pattern reference:** existing `updatePreview()` IIFE and `window.__STATION_TABLE__` embed (lines ~440-490 today) is the base to extend, not replace
- **Key decisions:** ARCH A5 (client-side JS filtering, no server round-trip); brief's explicit UX note — the 3 stacked tables are independently collapsible, not forced to sync with the current Fuel Type selection
- **Data contract from T4:** expects `window.__STATION_TABLE__` as `{fuel_type: [...]}` and a default/pre-fill fuel-type value in the template context — this task only consumes it, doesn't produce it
- **Libraries:** none new — vanilla JS, matching existing pattern
- **High-risk callouts:** part of the H-risk "Booking flow" area — a station left selected after a Fuel Type change becomes invalid for the new fuel type; JS must clear/reset the Station selection in that case (checklist item), since T3's server-side re-validation is a safety net, not a substitute for correct UX

### Scope Boundaries

- Do NOT change server-side data prep or shape — that's T4; this task only consumes whatever T4 passes into the template context.
- Do NOT change validation/persistence logic — T3's scope.
- Do NOT add fuel-type-specific styling/theming beyond what's needed for the 3-table layout — no gold-plating.

### Files Expected

**New files:**
- `tests/test_book_template_layout.py` (static HTML assertions per Testable Seams above)

**Modified files:**
- `templates/book.html` (form reorder, new Fuel Type select, hidden hardcoded input removed, 3 stacked collapsible tables, restructured `window.__STATION_TABLE__`, JS filtering + cost-preview updates)

**Must NOT modify:**
- `main.py` (T4's scope, already landed)

---

## Task T6: Admin Price/Discount Edit Endpoints

> **Status:** not started
> **Verification:** tdd
> **Effort:** s
> **Priority:** high
> **Depends on:** T2
> **Satisfies REQs:** R11
> **Footprint slice:** Modified: `main.py` (`admin_prices_update`, `admin_discounts_update`)
> **High-risk areas touched:** None — isolated from the H-risk booking flow

### Description

Extend `admin_prices_update()` and `admin_discounts_update()` in `main.py` to accept and validate a `fuel_type` field, threading it through to the T2 store functions. Since T2 keeps `price_store`/`discount_store` permissive on `fuel_type` values, HTTP-boundary validation (rejecting unrecognized values) belongs here.

### Test Plan

#### Test File(s)
- `tests/test_admin_pricing_endpoints.py` (new — no existing dedicated test file for either endpoint)

#### Test Scenarios

##### admin_prices_update

- **valid station_id+fuel_type+price succeeds** — GIVEN a valid payload WHEN posted THEN 200, `price_store.set_price` called with `fuel_type`, response JSON includes the price/updated_at for that fuel type _(verifies R11)_
- **missing/unrecognized fuel_type is rejected** — GIVEN a payload with an invalid `fuel_type` WHEN posted THEN 400, rejected at the HTTP boundary _(HTTP-layer validation, per T2's confirmed permissive-store decision)_
- **unknown station_id returns 404** — existing `KeyError` path, unchanged
- **out-of-range price returns 400** — existing `ValueError` path, now scoped per fuel type

##### admin_discounts_update

- **valid station+fuel_type+discount_per_liter succeeds** — GIVEN a valid form body WHEN posted THEN redirect, flash success, `discount_store.set` called with `fuel_type`
- **missing/unrecognized fuel_type is rejected** — flash error, same validation-boundary rule as the price endpoint
- **out-of-range discount (0-15) is rejected** — existing flash-error handling still applies, now per fuel-type combo

##### Edge Case

- **setting one fuel type's price/discount doesn't affect another's** — GIVEN a station with an existing Biodiesel price WHEN Premium's price is updated THEN Biodiesel's price is unchanged _(HTTP-layer isolation check, mirrors T2's store-layer isolation test)_

##### Regression Guard

- **both endpoints still require admin session** — unauthenticated request still gets 403 (price endpoint) / redirect-to-login (discount endpoint), unchanged from today

### Implementation Notes

- **Module(s):** `main.py`
- **Pattern reference:** existing price-range (`0 < price ≤ 200`) and discount-range (`0 ≤ discount ≤ 15`) validation in both endpoints is the model for the new `fuel_type` validation — same error-response shape per endpoint's existing convention
- **Key decisions:** T2's confirmed boundary — store functions stay permissive, enum validation happens here
- **Libraries:** none new
- **High-risk callouts:** none — additive validation on existing endpoints, isolated from the H-risk booking flow

### Scope Boundaries

- Do NOT change the two endpoints' differing auth/encoding conventions (JSON+403 vs form+redirect-to-login) — pre-existing inconsistency, not this task's concern.
- Do NOT touch `append_price_history()` — out of scope, per the earlier decision during task generation.

### Files Expected

**New files:**
- `tests/test_admin_pricing_endpoints.py`

**Modified files:**
- `main.py` (`admin_prices_update`, `admin_discounts_update` — both gain `fuel_type` param + validation)

**Must NOT modify:**
- `price_store.py`, `discount_store.py` (T2's scope, already landed)
- `append_price_history()` in `main.py`

---

## Task T7: Admin-Facing Display Updates

> **Status:** not started
> **Verification:** ui
> **Effort:** m
> **Priority:** high
> **Depends on:** T6
> **Satisfies REQs:** R11, R12, R14, R16
> **Footprint slice:** Modified: `templates/admin_prices.html`, `templates/admin.html`
> **High-risk areas touched:** None — additive UI on existing admin-only pages

### Description

Expand `admin_prices.html` from a single price/discount column to 6 (3 fuel types × price/discount), each independently editable and save-able, with a readable `Date + 24H` timestamp under both price and discount cells (discount currently has no timestamp display at all). Add a "Fuel Type" column to `admin.html`'s bookings table, with a "Diesel" fallback for pre-migration NULL rows.

### Verification Checklist

#### Testable Seams (static HTML assertions, new test file)

- **admin_prices.html renders 6 price/discount cells per station row** — expected: 3 fuel types × price/discount, each tagged with a `fuel_type` identifier (e.g. `data-fuel-type`) the JS can read
- **readable timestamp under both price and discount cells** — expected: `Date + 24H` formatted string, not raw epoch; discount previously had none at all
- **admin.html has a new Fuel Type column** — expected: new `<th>`/`<td>` pair in the bookings table
- **dashboard fuel-type cell falls back to "Diesel"** — expected: `row['fuel_type'] or 'Diesel'` renders "Diesel" for NULL/missing values

#### Human-Verified Checklist

- [ ] Saving one fuel type's price for a station updates only that cell's displayed value and timestamp, others unchanged — expected: no cross-contamination between fuel-type cells
- [ ] Saving a discount updates its own readable timestamp underneath, independent of the price timestamp — expected: two independent timestamp displays per fuel type
- [ ] Existing "stale" badge logic works correctly per fuel type, not conflated across the 3 — expected: a stale Biodiesel price doesn't mark Premium/Unleaded stale too
- [ ] Dashboard fuel-type column shows correct value for new bookings (Biodiesel/Premium/Unleaded) and "Diesel" for pre-migration historical bookings — expected: matches R16
- [ ] Layout stays usable at "internal-use only" bar — expected: not broken, polish not required per brief

### Implementation Notes

- **Module(s):** `templates/admin_prices.html`, `templates/admin.html`
- **Pattern reference:** existing single-price save-button JS (`document.querySelectorAll('.save-btn')`, lines ~197-228 in `admin_prices.html`) is the base to extend — each of the 6 cells needs its own save handler reading a `fuel_type` attribute from its row/cell
- **Key decisions:** brief's explicit "doesn't have to be perfect / internal-use only" note — layout polish is a low bar; functional correctness (right cell updates on save) matters more
- **Libraries:** none new
- **High-risk callouts:** none — additive UI on existing admin-only pages

### Scope Boundaries

- Do NOT change the admin prices page's overall visual style/theme beyond what's needed to fit 6 columns — no gold-plating on an explicitly "doesn't have to be perfect" page.
- Do NOT add collapsible UX to `admin_prices.html` — that's `/book`-specific (T5), confirmed out of scope here (ARCH decision A-11 / decisions log #11 in the REQ).

### Files Expected

**New files:**
- `tests/test_admin_display_layout.py`

**Modified files:**
- `templates/admin_prices.html` (6 price/discount cells, readable timestamps)
- `templates/admin.html` (new Fuel Type column, "Diesel" fallback)

**Must NOT modify:**
- `main.py` (T6's scope, already landed)

---

## Task T8: Supplier PDF Fuel Column

> **Status:** not started
> **Verification:** test-after
> **Effort:** s
> **Priority:** medium
> **Depends on:** T3
> **Satisfies REQs:** R15, R16
> **Footprint slice:** Modified: `report_pdf.py`
> **High-risk areas touched:** None — low-risk, pure addition

### Description

Add a "Fuel Type" column to `report_pdf.py`'s `build_supplier_pdf()` output, expanding short canonical values ("Premium"/"Unleaded") to their full display names ("Premium Gasoline"/"Unleaded Gasoline") at render time, with "Biodiesel" unchanged and a "Diesel" fallback for legacy NULL rows. Pure addition to an existing hardcoded 6-column table.

### Test Plan

#### Test File(s)
- `tests/test_report_pdf.py` (new — no existing test file for this module)

#### Test Scenarios

##### Column Content

- **header includes Fuel Type as a 7th column** _(verifies R15)_
- **Biodiesel voucher shows "Biodiesel" unchanged** _(verifies R15)_
- **Premium voucher shows "Premium Gasoline" (expanded)** _(verifies R15)_
- **Unleaded voucher shows "Unleaded Gasoline" (expanded)** _(verifies R15)_
- **legacy voucher with fuel_type=None shows "Diesel"** — matches the same display-layer fallback used on the admin dashboard _(verifies R16)_

##### Checklist (PDF-render smoke test, since full content assertion needs a new dependency)

- [ ] `build_supplier_pdf()` returns non-empty, valid PDF bytes and doesn't raise, given vouchers spanning all 3 fuel types plus a legacy NULL one

##### Regression Guard

- **existing 6 columns retain correct values and order** — GIVEN the same voucher data used before this change WHEN rendered THEN Station/Amount/Driver/Plate/Voucher ID/Name-Signature values and order are unchanged, only the new column is inserted

### Implementation Notes

- **Module(s):** `report_pdf.py`
- **Pattern reference:** existing `rows.append([...])` construction (line ~105) and hardcoded `header`/`col_widths` are the base to extend
- **Key decisions:** A1 (short canonical strings stored, expanded to full names only at PDF render time); A3/R16 (legacy NULL → "Diesel" fallback, consistent with the dashboard's fallback)
- **Testability:** recommend extracting the row/header-building logic (name expansion, column order) into a small pure helper testable without invoking reportlab, since full PDF-content assertion would require a new PDF-parsing dependency not currently in the project
- **Libraries:** none new — explicitly avoid adding a PDF-parsing dependency for testing
- **High-risk callouts:** none — low-risk, pure addition

### Scope Boundaries

- Do NOT update the PDF's hardcoded title string ("UniFleet – Diesel Refuel Vouchers") — captured as an Open Question in ARCH, explicitly deferred.
- Do NOT add a new PDF-parsing dependency for testing — use the extracted-helper approach instead.

### Files Expected

**New files:**
- `tests/test_report_pdf.py`

**Modified files:**
- `report_pdf.py` (`header`, row-building, `col_widths`)

**Must NOT modify:**
- `main.py` (already landed by earlier tasks)

---

## Task T9: `/api/v1/*` Backward-Compatible `fuel_type` Param

> **Status:** not started
> **Verification:** tdd
> **Effort:** s
> **Priority:** medium
> **Depends on:** T2
> **Satisfies REQs:** Inferred (not in original REQ — see ARCH A8)
> **Footprint slice:** Modified: `main.py` (`/api/v1/prices`, `/api/v1/discounts`, `/api/v1/price_preview`)
> **High-risk areas touched:** None — additive, backward-compatible by design

### Description

Add an optional `fuel_type` query param to `/api/v1/prices`, `/api/v1/discounts`, and `/api/v1/price_preview`, defaulting to `"Biodiesel"` when omitted or unrecognized — preserving both external supplier and internal preview consumers' existing behavior without requiring them to know about fuel types.

### Test Plan

#### Test File(s)
- `tests/test_api_v1_pricing.py` (new — no existing tests for these endpoints)

#### Test Scenarios

##### Default (Backward-Compat) Behavior

- **GET /api/v1/prices with no fuel_type param defaults to Biodiesel** — response shape unchanged from pre-migration _(verifies A8)_
- **GET /api/v1/discounts with no fuel_type param defaults to Biodiesel** _(verifies A8)_
- **GET /api/v1/price_preview with no fuel_type param defaults to Biodiesel** — existing calculation behavior unchanged _(verifies A8)_

##### Explicit Fuel Type

- **GET /api/v1/prices?fuel_type=Premium returns Premium-scoped station list**
- **GET /api/v1/discounts?fuel_type=Unleaded returns Unleaded-scoped discounts**
- **GET /api/v1/price_preview?fuel_type=Premium computes using Premium's price**

##### Edge Case

- **unrecognized fuel_type value falls back to Biodiesel silently** — GIVEN `?fuel_type=Regular` (not one of the 3 canonical values) WHEN any of the 3 endpoints is called THEN it behaves as if `fuel_type` were omitted (Biodiesel), no 400 error — confirmed deliberately more lenient than T6's admin write-endpoint validation

##### Regression Guard

- **default response for all 3 endpoints exactly matches pre-migration output** — this is the core backward-compat guarantee for existing external supplier and internal preview consumers _(verifies A8)_

### Implementation Notes

- **Module(s):** `main.py`
- **Pattern reference:** existing `/api/v1/price_preview` station-matching logic (exact id/name match, 404 on miss) is unaffected by this change — only the fuel_type layer is new
- **Key decisions:** deliberately more lenient than T6's admin write endpoints — public, read-only, unknown-identity external consumer, so silent fallback beats a hard 400 (confirmed)
- **Libraries:** none new
- **High-risk callouts:** none — additive, backward-compatible by design

### Scope Boundaries

- Do NOT reject unrecognized `fuel_type` values — silent fallback only, unlike T6's stricter admin-endpoint validation.
- Do NOT change any other query param handling on these 3 endpoints.

### Files Expected

**New files:**
- `tests/test_api_v1_pricing.py`

**Modified files:**
- `main.py` (`/api/v1/prices`, `/api/v1/discounts`, `/api/v1/price_preview`)

**Must NOT modify:**
- `price_store.py`, `discount_store.py` (T2's scope, already landed)
