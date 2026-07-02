# Architecture: Migrate Customer Flow to Postgres

> **Date:** 2026-07-02
> **Phase:** 2 of 5 (System Architecture)
> **Requirements source:** Standalone brief — "migrate customer flow (register + book + validation) to Postgres" — see Inferred Requirements
> **Type:** migration

## Architecture Summary

Today vouchers run through Postgres (`PostgresRepo`, prod `PERSISTENCE_BACKEND=db`)
but the **customer** flow bypasses the repo entirely: `/register` writes
`data/customers.csv` and `/book` reads it, while the Postgres `customers` table is
stale (seeded only by the one-time migration script). This creates a split-brain
where the same running app has two customer sources of truth. This migration adds
three customer methods to the repo layer (`create_customer`, `get_customer`,
`customer_exists`), reroutes `/register` and `/book` to the repo, and unblocks the
`/register-vehicle` validation (T4 in `ARCH-brief-changes.md`). Cutover is a
**dual-write transition**: `/register` writes both Postgres and CSV, `/book` reads
Postgres first with a CSV fallback — so a missed backfill or a transient DB issue
degrades gracefully instead of losing customers. Existing CSV rows are backfilled
once via the existing `scripts/migrate_to_postgres.py`. CSV paths are removed in a
later cleanup, leaving `customers.csv` as a read-only backup.

## Inferred Requirements

| ID  | Inferred Requirement | Source |
|-----|----------------------|--------|
| R1  | Customer records are stored in and read from the Postgres `customers` table (single source of truth). | Brief + prod db-mode split-brain |
| R2  | `/register` persists new customers to Postgres. | Brief ("register") |
| R3  | `/book` resolves the account code from Postgres. | Brief ("book") |
| R4  | Repo exposes a `customer_exists(account_code)` check for `/register-vehicle` validation. | Brief ("validation"); unblocks T4 |
| R5  | Existing `customers.csv` rows are backfilled into Postgres before cutover. | Migration necessity |
| R6  | Deriving an account code that collides with an existing (different) customer must not overwrite that customer. | account_code PK vs CSV dup behavior |
| N1  | Transition is graceful: DB miss/outage does not break booking or lose registrations (dual-write + CSV fallback). | Rollout safety |

## High-Level Structure

Layered: HTTP routes (`main.py`) → repository (`persistence.get_repo` → `PostgresRepo`/`CSVRepo`)
→ Postgres. Customers join vouchers in going through the repo instead of raw pandas CSV.

```
/register POST ─► repo.create_customer(data)  ──► Postgres customers   [R2]
             └──► append customers.csv (dual-write, transition only)   [N1]
             code-gen loop: while repo.customer_exists(code): re-derive [R6]

/book POST ────► repo.get_customer(account_code) ─► Postgres            [R3]
             └── if None: fallback pd.read_csv(customers.csv)  (transition) [N1]
             customer dict ─► book.html (account_code, company_name,
                                         contact_name, contact_number)

/register-vehicle POST ─► repo.customer_exists(account_code)            [R4]
                          (consumed by ARCH-brief-changes T4)

Backfill (one-time, ops): scripts/migrate_to_postgres.py ─► customers   [R5]
```

**Added:** 3 repo methods (PostgresRepo + CSVRepo). **Modified:** `/register`, `/book`.
**Replaced later (cleanup, out of scope here):** CSV write in register, CSV read in book.

## Tech Choices

| Area | Decision | Alternatives Considered | Rationale |
|------|----------|-------------------------|-----------|
| Storage | Postgres `customers` table (existing) | new table; keep CSV | Table already exists in schema; vouchers already use it as FK target |
| Access layer | Extend repo (`PostgresRepo`/`CSVRepo`) | raw SQL in `main.py`; new `customer_store.py` | Matches existing voucher pattern; keeps `main.py` route-thin; parity across backends |
| Cutover | Dual-write transition (write PG+CSV, read PG-first + CSV fallback) | hard cutover | Graceful degradation if backfill missed or DB blips; reversible |
| Backfill | One-time run of `scripts/migrate_to_postgres.py` | new `seed_customers.sql` auto-applied | Script already has idempotent `migrate_customers` upsert; static seed goes stale as new customers register |
| Collision handling | Generate unique code on collision (retry) | upsert/overwrite; reject | Prevents silently overwriting another customer (PK collision); best UX |
| Upsert semantics | `INSERT ... ON CONFLICT (account_code) DO UPDATE` | insert-only | Idempotent; mirrors `migrate_customers` (`scripts/migrate_to_postgres.py:314`) |

## Patterns & Conventions

- **Repository pattern** — customer methods live on the repo, mirror voucher methods (`create_unverified_booking`, `get_voucher`). `main.py` stays route-thin.
- **Pool + `dict_row`** — Postgres reads use `self._pool.connection()` + `conn.cursor(row_factory=dict_row)` (see `postgres_repo.py:176-187`).
- **Upsert idempotency** — reuse the `ON CONFLICT (account_code) DO UPDATE` shape from `migrate_customers`.
- **Type coercion** — `fleet_size` is str in CSV, INTEGER in PG; coerce with the `_nullable_int` pattern (`scripts/migrate_to_postgres.py`).
- **account_code normalization** — upper/stripped everywhere (matches `/book` `main.py:555`, `/register` `main.py:417`).
- **Backend parity** — both `PostgresRepo` and `CSVRepo` implement the 3 methods so CSV-mode dev/tests still work.
- **Not applied:** no ORM (project uses raw psycopg); no separate customer service module.

## Data Models

### Customer (existing table `customers`)

**Purpose:** A registered fleet account; `account_code` is the human-facing key used at booking.

**Key fields:**
| Field | Type / Constraint | Notes |
|-------|-------------------|-------|
| account_code | VARCHAR(16) PK | 4-char upper code derived from company name; unique |
| contact_name | VARCHAR(200) | shown at booking |
| contact_number | VARCHAR(50) | shown at booking |
| email | VARCHAR(200) | |
| company_name | VARCHAR(200) | shown at booking ("Welcome, {company_name}") |
| fleet_size | INTEGER | coerced from CSV string |
| areas | VARCHAR(200) | |
| refuel_locations | VARCHAR(200) | legacy, blank on new register |
| hq_locations | VARCHAR(200) | legacy, blank on new register |
| created_at | TIMESTAMPTZ default NOW() | |

**Relationships:** `vouchers.account_code` → `customers.account_code` (FK, existing).

**Lifecycle:** created at `/register` (or backfill) → read at `/book` and `/register-vehicle` → no update/delete UI in scope (upsert may refresh fields).

## API Contracts / Interfaces

### Repo customer methods (new)

**Boundary:** internal module API — `PostgresRepo` and `CSVRepo`, obtained via `persistence.get_repo`.

| Method/Op | Signature | Purpose | Errors / Returns |
|-----------|-----------|---------|------------------|
| create_customer | `create_customer(data: Dict) -> Dict` | Upsert a customer; returns stored row | raises on DB error; returns dict with all customer fields |
| get_customer | `get_customer(account_code: str) -> Optional[Dict]` | Fetch by PK (upper-normalized) | `None` if not found |
| customer_exists | `customer_exists(account_code: str) -> bool` | Existence check | `False` if not found |

**Returned dict keys** must match the CSV column names consumed by `book.html`:
`account_code, contact_name, contact_number, email, company_name, fleet_size, areas,
refuel_locations, hq_locations` (parity with `rows.iloc[0].to_dict()`).

### HTTP routes (modified)

| Method/Op | Path | Change | Errors / Returns |
|-----------|------|--------|------------------|
| POST | `/register` | dual-write PG + CSV; collision-safe code gen | success → `/register/success?account_code=`; unchanged on error |
| POST | `/book` | `repo.get_customer` PG-first, CSV fallback | not found → re-render form (`customer=None`) |

**Auth requirements:** both public (unchanged).

## Module Boundaries

| Module | Responsibility | Allowed Dependencies |
|--------|----------------|----------------------|
| `main.py` (routes) | HTTP handling; calls repo for customers | flask, persistence, data_paths, pandas (CSV fallback only) |
| `persistence.py` (`CSVRepo`) | CSV-backed customer methods (parity) | pandas, data_paths |
| `db/postgres_repo.py` (`PostgresRepo`) | Postgres customer methods | psycopg, pool |
| `scripts/migrate_to_postgres.py` | one-time backfill (unchanged) | psycopg, csv |

Rule: routes never issue raw customer SQL — they go through the repo. CSV fallback in
`/book` is the one temporary exception, removed in the cleanup task.

## Change Footprint

### New files / modules

| Path | Purpose | Pattern reference |
|------|---------|-------------------|
| `tests/test_customer_repo.py` | Unit tests for CSVRepo customer methods (SQLite/CSV, no PG) | `tests/test_postgres_repo.py` (monkeypatch) |
| `tests/test_customer_repo_integration.py` | Integration tests for PostgresRepo customer methods | `tests/test_postgres_repo_integration.py` (`schema_db`) |

### Modified files / modules

| Path | What changes here |
|------|-------------------|
| `db/postgres_repo.py` | Add `create_customer` (upsert), `get_customer`, `customer_exists`; fleet_size int coercion |
| `persistence.py` (`CSVRepo`) | Add same 3 methods over `customers.csv` for backend parity |
| `main.py:412-448` (`/register`) | Dual-write: call `repo.create_customer(new_row)` AND keep CSV append; wrap code-gen in `while repo.customer_exists(code)` collision loop |
| `main.py:549-648` (`/book`) | Replace `pd.read_csv`+filter with `repo.get_customer(account_code)`; `rows.empty` → `customer is None`; `base`/`rows` → `customer`; keep `pd.read_csv` only as fallback when `get_customer` returns None |

### Deleted / replaced

| Path | Reason |
|------|--------|
| (none this phase) | CSV write in register + CSV read in book are removed in a LATER cleanup task, not here (dual-write transition) |

### Touched but not changed (silent-regression hotspots)

| Path | Why it matters |
|------|----------------|
| `templates/book.html:165-180` | Consumes `customer.account_code/.company_name/.contact_name/.contact_number`; repo dict keys + casing must match old CSV keys exactly |
| `main.py:719` | Voucher save uses resolved `account_code`; depends on `/book` still resolving the customer |
| `main.py:570,587,628,644` | Preset reads keyed by `account_code`; unaffected but co-located in the edited `/book` block |
| `scripts/migrate_to_postgres.py` | Reused unchanged for one-time backfill; behavior must stay idempotent |
| `vouchers.account_code` FK | Customers must exist in PG before vouchers reference them; backfill ordering matters |

## Areas of Impact

| Area | Impact | Risk (L/M/H) | Why |
|------|--------|--------------|-----|
| Customer source of truth | Split-brain removed; PG becomes primary | H | Core data-integrity change; wrong dict shape breaks booking |
| `/register` flow | Dual-write + collision handling | M | New code-gen loop; partial-write risk if PG ok but CSV fails (or vice versa) |
| `/book` flow | Read path rerouted | M | All bookings depend on customer resolution; fallback must be correct |
| account_code uniqueness | New collision semantics | M | Changes long-standing "duplicates allowed" CSV behavior |
| Rollout / backfill | One-time script must run before/at cutover | M | Missed backfill → PG miss (mitigated by CSV fallback) |
| CSVRepo / dev parity | New methods for CSV backend | L | Dev + unit tests only |

**Contract changes:** internal repo interface gains 3 methods (additive). No external
HTTP/API contract changes. `book.html` template contract (customer dict keys) must be
preserved exactly.

**Cross-cutting ripples:**
- **Migration:** one-time `scripts/migrate_to_postgres.py` run against prod (ops step in rollout).
- **Rollout order:** deploy code (dual-write + fallback) is safe even before backfill because `/book` falls back to CSV; backfill then makes PG authoritative.
- **Later cleanup:** a follow-up task removes the CSV write (register) and CSV read (book), leaving `customers.csv` as read-only backup and CSVRepo methods for dev/tests.

## Cross-Cutting Concerns

- **Errors:** repo raises on DB failure. `/register` dual-write — if PG write fails, log + still write CSV (don't lose the signup); surface a flash only on total failure. `/book` — if `get_customer` raises, fall back to CSV read; if both miss, re-render form with `customer=None`.
- **Logging & metrics:** reuse `print`-style logging already in routes; log PG customer write/read failures and fallback activation so we can tell when PG is actually serving vs falling back.
- **Auth / authz:** unchanged — `/register` and `/book` remain public.
- **Performance:** `get_customer`/`customer_exists` are single-PK lookups (indexed PK); negligible. Removes a full-CSV pandas read per booking.
- **Security:** parameterized SQL (psycopg) — no string interpolation. account_code upper/stripped before query. No new secrets.
- **Migrations / rollout:** no schema change (table exists). Sequence: (1) deploy dual-write+fallback code, (2) run backfill script once, (3) verify PG serving, (4) later cleanup removes CSV paths. Rollback: revert code → `/book` CSV read still works (CSV kept current by dual-write).

## Architecture Decisions Log

| # | Decision | Alternatives | Chosen Because | Satisfies REQs |
|---|----------|--------------|----------------|----------------|
| A1 | Extend repo with 3 customer methods | raw SQL in routes; new store module | Matches voucher pattern; route-thin; backend parity | R1, R2, R3, R4 |
| A2 | Dual-write transition (PG+CSV, read PG-first + CSV fallback) | hard cutover | Graceful degradation, reversible, safe if backfill missed/DB blips | N1, R2, R3 |
| A3 | One-time backfill via existing migrate script | new seed_customers.sql | Idempotent upsert already exists; static seed goes stale | R5 |
| A4 | Generate unique code on collision | upsert/overwrite; reject | Never overwrite another customer; best UX | R6 |
| A5 | Upsert `ON CONFLICT (account_code) DO UPDATE` | insert-only | Idempotent; mirrors migrate script | R1, R5 |
| A6 | Keep `customers.csv` (read-only backup after cleanup) | delete file | Reversible; CSV-mode dev/test parity retained | N1 |

## Risk & Stress-Test Scenarios

### Forward — runtime failure scenarios

| Scenario | How the Design Handles It |
|----------|---------------------------|
| Backfill not run before deploy | `/book` `get_customer` returns None → CSV fallback resolves existing customers; new registers write PG directly. No lockout. |
| Postgres down during `/book` | `get_customer` raises → CSV fallback serves the booking (transition safety net). |
| Postgres down during `/register` | PG write fails → CSV append still succeeds; signup not lost; logged for reconciliation. |
| CSV write fails during `/register` | PG write is authoritative; log CSV failure; registration still recorded in PG. |
| Two companies derive the same 4-char code | Collision loop (`while customer_exists`) generates an alternate unique code before insert. |
| Concurrent `/register` race on same derived code | `ON CONFLICT` upsert + unique-code retry; worst case one gets an alternate code. Rare (2 signups same instant, same prefix). See Open Questions. |
| customers table grows large | PK lookups stay O(index); no full scans introduced. |

### Backward — regression risk per touched area

| Touched area | What could regress | How we'd know / mitigation |
|--------------|--------------------|-----------------------------|
| `templates/book.html:165-180` | Missing/renamed customer dict keys blank the welcome + prefill | Integration test asserts `get_customer` returns dict with exact keys `account_code, company_name, contact_name, contact_number`; render test |
| `main.py:549-648` `/book` | `rows.empty` → `customer is None` logic slip; fallback path wrong | Tests: existing account (PG hit), unknown account (None → form), PG-miss-CSV-hit (fallback) |
| `fleet_size` type | str→INTEGER coercion error on register | Unit test: register with numeric + blank fleet_size; assert stored/retrieved value |
| `main.py:719` voucher save | account_code no longer resolved → FK violation | End-to-end book test: resolve customer → save voucher succeeds |
| account_code case | PG query case-sensitivity vs CSV upper | Test: lookup with lower/mixed case resolves same customer |
| `scripts/migrate_to_postgres.py` | Re-run duplicates/overwrites | Idempotency already tested; re-run is upsert |

## Open Questions

- Concurrent-register race producing two rows with different codes for the same company?
  - **Impact if unresolved:** rare duplicate company (different codes).
  - **Suggested default:** accept for now (extremely low volume); revisit if it occurs.
- Should upsert on `/register` refresh existing customer fields, or only insert-if-absent?
  - **Impact if unresolved:** re-registering an existing company could overwrite contact details.
  - **Suggested default:** DO UPDATE (refresh) — matches migrate script; revisit if unwanted.
- When exactly is the CSV-fallback cleanup task scheduled?
  - **Impact if unresolved:** dual-write lingers, mild complexity.
  - **Suggested default:** schedule cleanup one sprint after backfill verified in prod.

## Out of Scope

- Removing CSV write/read paths (separate later cleanup task — dual-write must prove out first).
- Customer edit/delete UI (no existing UI; not requested).
- Migrating presets (`data/presets/*.csv`) to Postgres (separate concern).
- Changing `/book`'s preset read path (still CSV).
- `PERSISTENCE_BACKEND` selection logic (already db in prod).
- T4 `/register-vehicle` route itself (lives in `ARCH-brief-changes.md`; this ARCH only delivers `customer_exists`).

---

# Tasks

> **Generated:** 2026-07-02 (Phase 3 — generate-tasks)
> **Test harness:** pytest. `PostgresRepo` tests use the `schema_db` fixture (real PG,
> `make test-db`); the `customers` table is empty in `schema_db`/`seeded_db`, so
> integration tests own their rows (autouse `TRUNCATE customers CASCADE` for isolation,
> mirroring `clean_vouchers`). `CSVRepo` + route tests monkeypatch `UNIFLEET_DATA_DIR`
> → `tmp_path` and stub the module-level `main.repo`, so they run under `make test`
> (no PG).
> **Ordering:** CT1 first (data layer, unblocks CT2/CT3 and `ARCH-brief-changes.md` T4).
> CT2 and CT3 are independent of each other once CT1 lands.

## Task CT1: Repo customer methods (PostgresRepo + CSVRepo)

> **Status:** done
> **Effort:** m
> **Priority:** critical
> **Depends on:** None
> **Satisfies REQs:** R1, R2, R3, R4, R6 (partial — `customer_exists`), A5
> **Footprint slice:** New: `tests/test_customer_repo.py`, `tests/test_customer_repo_integration.py`; Modified: `db/postgres_repo.py` (+3 methods), `persistence.py` `CSVRepo` (+3 methods)
> **High-risk areas touched:** Customer source of truth (H), CSVRepo/dev parity (L)

### Description

Add three customer methods — `create_customer`, `get_customer`, `customer_exists` —
to both `PostgresRepo` and `CSVRepo` so customers become a first-class repo entity
(like vouchers). This is the data-layer foundation for rerouting `/register` and
`/book`, and it delivers `customer_exists` which unblocks `/register-vehicle`
validation (T4 in `ARCH-brief-changes.md`). No routes change in this task.

### Test Plan

#### Test File(s)
- `tests/test_customer_repo.py` (CSVRepo — monkeypatch `UNIFLEET_DATA_DIR` → `tmp_path`)
- `tests/test_customer_repo_integration.py` (PostgresRepo — `schema_db` fixture; autouse `TRUNCATE customers CASCADE`)

#### Test Scenarios

##### Create & Upsert

- **create_customer inserts** — GIVEN a customer dict WHEN `create_customer` THEN the row is retrievable with all 9 fields (account_code, contact_name, contact_number, email, company_name, fleet_size, areas, refuel_locations, hq_locations) _(verifies R1, R2)_
- **create_customer upserts** — GIVEN an existing account_code WHEN `create_customer` is called again with changed fields THEN there is still exactly one row and the fields are refreshed _(verifies A5)_

##### Read

- **get_customer returns full dict** — GIVEN a stored customer WHEN `get_customer(code)` THEN returns a dict whose keys exactly match the CSV/`book.html` contract (account_code, company_name, contact_name, contact_number, email, fleet_size, areas, refuel_locations, hq_locations) _(verifies R3; guards `book.html` dict-key parity)_
- **get_customer None when absent** — GIVEN an unknown account_code WHEN `get_customer` THEN returns None _(verifies R3)_
- **customer_exists true/false** — GIVEN a stored customer WHEN `customer_exists(code)` THEN True; for an unknown code THEN False _(verifies R4)_

##### Edge / Regression

- **account_code case-insensitive** — GIVEN a customer stored as `ABCD` WHEN looked up via `abcd` / `AbCd` THEN resolves the same customer _(guards backward-regression: case parity with `/book` upper-normalization)_
- **fleet_size coercion** — GIVEN `fleet_size="12"` and `fleet_size=""` WHEN `create_customer` THEN stored/returned as int `12` and `None` respectively, with no error _(guards backward-regression: CSV str → PG INTEGER)_

### Implementation Notes

- **Module(s):** `db/postgres_repo.py` (`PostgresRepo`), `persistence.py` (`CSVRepo`).
- **Pattern reference:** voucher methods `create_unverified_booking` / `get_voucher` in `postgres_repo.py`; pool + `dict_row` at `postgres_repo.py:176-187`; upsert SQL at `scripts/migrate_to_postgres.py:314` (`ON CONFLICT (account_code) DO UPDATE`); `_nullable_int` coercion in the migrate script; CSV read/write idioms in `persistence.py` `CSVRepo`.
- **Key decisions:** A1 (extend repo), A5 (upsert ON CONFLICT DO UPDATE).
- **Libraries:** psycopg (`dict_row`), pandas (CSVRepo), `data_paths`.
- **High-risk callouts:**
  - *Customer source of truth (H):* `get_customer` dict keys + casing MUST match the old `rows.iloc[0].to_dict()` shape or `book.html` prefill silently blanks. The read test asserts exact keys; `/book` render is verified in CT3.
  - account_code normalized upper/stripped on both write and lookup (match `main.py:417,555`).
  - `fleet_size` str→INTEGER coercion is the top silent-failure risk — explicit edge test.

### Scope Boundaries

- Do NOT modify any route in `main.py` (that's CT2/CT3).
- Do NOT add customer update/delete methods (out of scope).
- Do NOT migrate presets (out of scope).
- Only add the 3 methods to both repos + their tests.

### Files Expected

**New files:**
- `tests/test_customer_repo.py` (mirror `tests/test_persistence.py` / `test_data_paths.py`)
- `tests/test_customer_repo_integration.py` (mirror `tests/test_postgres_repo.py`, `schema_db`)

**Modified files:**
- `db/postgres_repo.py` (add `create_customer` upsert, `get_customer`, `customer_exists`; fleet_size int coercion)
- `persistence.py` `CSVRepo` (add same 3 methods over `customers.csv` for parity)

**Must NOT modify:**
- `main.py` routes (untouched this task)
- `scripts/migrate_to_postgres.py` (reused as-is; SQL pattern only)

### TDD Sequence (optional)

1. `create_customer` + `get_customer` (need both to assert inserts).
2. `customer_exists` (thin wrapper over get).
3. Edge tests (case, fleet_size) last.

---

## Task CT2: `/register` dual-write to Postgres + collision-safe code generation

> **Status:** done
> **Effort:** s
> **Priority:** high
> **Depends on:** CT1
> **Satisfies REQs:** R2, R6, N1
> **Footprint slice:** Modified: `main.py:412-448` (`/register` POST)
> **High-risk areas touched:** `/register` flow (M), account_code uniqueness (M)

### Description

Reroute `/register` to persist new customers to Postgres via `repo.create_customer`
while keeping the existing `customers.csv` append (dual-write transition). Make the
4-char account_code generation collision-safe: if the derived code already belongs to
a different customer, generate an alternate unique code instead of overwriting.

### Test Plan

#### Test File(s)
- `tests/test_register_pg.py` (stub module-level `main.repo`; monkeypatch `UNIFLEET_DATA_DIR` → `tmp_path`)

#### Test Scenarios

##### Register Persistence

- **writes to postgres** — GIVEN a valid registration POST WHEN `/register` THEN `repo.create_customer` is called with the derived account_code and all form fields (contact_name, contact_number, email, company_name, fleet_size, areas) _(verifies R2)_
- **dual-writes csv** — GIVEN a valid registration POST WHEN `/register` THEN a matching row is also appended to `customers.csv` _(verifies N1)_

##### Collision Handling

- **collision generates unique code** — GIVEN the derived code already exists (`customer_exists` returns True once, then False) WHEN `/register` THEN an alternate unique code is used AND the pre-existing customer is not overwritten _(verifies R6)_

##### Resilience & Regression

- **pg failure still writes csv** — GIVEN `repo.create_customer` raises WHEN `/register` THEN the CSV append still happens, no 500 is returned, and the signup is not lost _(verifies ARCH forward stress-test)_
- **success redirect preserved** — GIVEN a valid POST WHEN `/register` THEN 302 → `/register/success?account_code=<code>` _(guards existing `/register` behavior)_

### Implementation Notes

- **Module(s):** `main.py` `/register` route.
- **Pattern reference:** existing code-gen at `main.py:415-417`; CSV append at `main.py:435-447`; repo obtained via module-level `repo` (`main.py:116`).
- **Key decisions:** A2 (dual-write), A4 (unique code on collision), A5 (upsert).
- **Libraries:** flask, pandas (CSV side), `persistence` repo.
- **High-risk callouts:**
  - *account_code uniqueness (M):* collision loop uses `repo.customer_exists(code)`; guard against infinite loop (bounded retries). Do NOT rely on upsert to "handle" collisions — that would overwrite another customer (A4).
  - *`/register` flow (M):* dual-write ordering — write PG first, then CSV; if PG fails, still append CSV and log (forward-stress test covers this). Don't 500 on partial failure.

### Scope Boundaries

- Do NOT remove the CSV append (dual-write transition; CSV removal is the later cleanup task, out of scope).
- Do NOT change the `/register` GET form or `register.html`.
- Do NOT add customer edit/delete.
- Only reroute the POST write path + collision-safe code gen.

### Files Expected

**New files:**
- `tests/test_register_pg.py`

**Modified files:**
- `main.py:412-448` (`/register` POST — add `repo.create_customer` dual-write; wrap code-gen in bounded `while repo.customer_exists(code)` loop)

**Must NOT modify:**
- `templates/register.html` (unchanged)
- `db/postgres_repo.py` / `persistence.py` (consumed, delivered by CT1)

---

## Task CT3: `/book` reroute to Postgres (PG-first + CSV fallback)

> **Status:** done
> **Effort:** m
> **Priority:** high
> **Depends on:** CT1
> **Satisfies REQs:** R3, N1
> **Footprint slice:** Modified: `main.py:549-648` (`/book` POST customer resolution)
> **High-risk areas touched:** `/book` flow (M), Customer source of truth (H)

### Description

Reroute `/book` customer resolution to `repo.get_customer(account_code)` (Postgres),
falling back to the existing `customers.csv` read only when the repo returns None or
raises (transition safety net). Collapse the pandas `rows`/`base` handling into a
single `customer` dict passed to the template, preserving the exact keys `book.html`
consumes.

### Test Plan

#### Test File(s)
- `tests/test_book_pg.py` (stub module-level `main.repo`; monkeypatch `UNIFLEET_DATA_DIR` → `tmp_path`)

#### Test Scenarios

##### Customer Resolution

- **resolves via postgres** — GIVEN `repo.get_customer` returns a customer WHEN POST `/book` with that account_code THEN the page renders "Welcome, {company_name}" and prefills the contact field _(verifies R3; guards `book.html:165-180`)_
- **unknown account renders empty form** — GIVEN `get_customer` returns None AND no CSV match WHEN POST `/book` THEN the form re-renders with `customer=None` (asks for account code) _(verifies R3)_

##### Fallback & Resilience

- **csv fallback on pg miss** — GIVEN `get_customer` returns None but the code exists in `customers.csv` WHEN POST `/book` THEN the customer is resolved via the CSV fallback _(verifies N1 forward stress-test)_
- **pg down falls back to csv** — GIVEN `get_customer` raises a DB error WHEN POST `/book` THEN the CSV fallback serves the booking without a 500 _(verifies ARCH forward stress-test)_

##### Regression

- **booking save still resolves account_code** — GIVEN a resolved customer WHEN proceeding to save a booking THEN the voucher save path receives the account_code (no FK break) _(guards backward-regression for `main.py:719`)_

### Implementation Notes

- **Module(s):** `main.py` `/book` route.
- **Pattern reference:** current read+filter at `main.py:551-556`; the four `base = rows.iloc[0].to_dict()` sites (`main.py:569,586,627,644`); template contract at `book.html:165-180`.
- **Key decisions:** A2 (PG-first + CSV fallback).
- **Libraries:** flask, pandas (fallback only), `persistence` repo.
- **High-risk callouts:**
  - *Customer source of truth (H):* the `customer` dict must carry the same keys as the old `rows.iloc[0].to_dict()` or `book.html` prefill blanks. "resolves via postgres" render test asserts this end-to-end.
  - *`/book` flow (M):* `rows.empty` logic becomes `customer is None`; all four former `base`/`rows` sites must funnel through the single resolved `customer`. Preset reads (`main.py:570` etc.) stay CSV-keyed by account_code — do not alter.
  - Wrap `get_customer` in try/except → CSV fallback; never let a DB blip 500 the booking page.

### Scope Boundaries

- Do NOT remove the CSV fallback read (transition; removal is the later cleanup task).
- Do NOT change the preset read path (still CSV, out of scope).
- Do NOT change station/pricing logic in `/book`.
- Only reroute customer resolution + fallback.

### Files Expected

**New files:**
- `tests/test_book_pg.py`

**Modified files:**
- `main.py:549-648` (`/book` POST — `repo.get_customer` PG-first with CSV fallback; `rows`/`base` → single `customer` dict; `rows.empty` → `customer is None`)

**Must NOT modify:**
- `templates/book.html` (silent-regression hotspot — dict-key contract; covered by render test)
- `main.py:719` voucher save (depends on resolved account_code; covered by regression test)
- `main.py:570,587,628,644` preset reads (co-located; keep CSV, account_code-keyed)
- `db/postgres_repo.py` / `persistence.py` (consumed, delivered by CT1)
