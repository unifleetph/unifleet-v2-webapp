# Tasks

## Task T1: Margin storage — `margin_settings` schema + `margin_store.py`

> **Status:** done
> **Verification:** tdd
> **Effort:** s
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** R1, R2, R3
> **Footprint slice:** New: `margin_store.py`, `tests/test_margin_store.py`; Modified: `db/schema.sql` (new `margin_settings` table + `vouchers.margin_pct_at_booking` column — pulled forward from T3 since `models.py`'s `VOUCHER_COLUMNS` addition in this same task requires the column to exist), `models.py` (`VOUCHER_COLUMNS` gains `margin_pct_at_booking`)
> **High-risk areas touched:** None

### Description

Adds the single global profit-margin setting the admin edits, plus the pure `apply()` transform every downstream consumer will call. This is the foundation task — nothing else in the feature can be built until the margin value has somewhere to live and a testable, DB-free math function to apply it.

### Test Plan

#### Test File(s)
- `tests/test_margin_store.py` (new, mirrors `tests/test_discount_store.py`'s `schema_db` + dsn-reset fixture pattern)

#### Test Scenarios

##### Margin CRUD

- **defaults to 0% when never set** — GIVEN a fresh `schema_db` WHEN `MarginStore(dsn=schema_db).get()` is called THEN returns `0.0` _(verifies R2)_
- **set then get round-trips at 2-decimal precision** — GIVEN margin set to `12.25` WHEN read back THEN returns `12.25` exactly _(verifies R3)_
- **set() records actor/reason and updated_at** — GIVEN a `.set(12.25, actor="admin", reason="quarterly review")` call WHEN the row is read directly via psycopg THEN `updated_by == "admin"` and `updated_at` is populated _(supports A8 audit trail)_
- **accepts boundary values 0 and 100** — GIVEN `.set(0)` and `.set(100)` THEN both succeed without error

##### Margin Validation

- **rejects 3rd decimal place** — GIVEN `.set(12.255)` THEN raises `MarginValueError` _(verifies R3)_
- **rejects out-of-range values** — GIVEN `.set(-1)` and `.set(101)` THEN both raise `MarginValueError` _(REQ edge case: sane percentage bound)_
- **rejects non-numeric input** — GIVEN `.set("abc")` THEN raises `MarginValueError` _(REQ edge case)_

##### Apply Transform

- **apply() passthrough when exempt** — GIVEN `raw=10.0, margin=12.25, exempt=True` WHEN `apply()` called THEN returns `10.0` unchanged _(verifies R5)_
- **apply() reduces when not exempt** — GIVEN `raw=10.0, margin=12.25, exempt=False` WHEN `apply()` called THEN returns `round(10.0 * (1 - 0.1225), 4) == 8.775` _(verifies R4)_
- **apply() at 0% margin is a no-op regardless of exempt flag** — GIVEN `margin=0` WHEN `apply()` called with `exempt=False` THEN returns `raw` unchanged _(verifies R2, supports zero-risk rollout)_

##### Regression Guard

- **schema migration is idempotent** — GIVEN `db/apply.py` run twice against `schema_db` THEN no error, and `margin_settings` still has exactly 1 row seeded to `margin_pct = 0` _(guards backward-regression risk: forward-only migration convention)_

### Implementation Notes

- **Module(s):** `margin_store.py` — new, owns `margin_settings` CRUD + `apply()`. No dependency on `discount_store.py` (Module Boundaries).
- **Pattern reference:** `discount_store.py` — constructor accepts `dsn=` override for tests, uses `db.pool.get_pool()`, same `_now_iso()`-style Manila-timezone timestamp helper if needed.
- **Key decisions:** A1 (singleton table, not env var/KV table), A2 (own module, not folded into `discount_store.py`), rounding matches `DiscountStore.VALUE_PRECISION_DECIMALS = 4` for the *output* of `apply()`, while the margin value itself is validated/stored to 2 decimals (distinct precision, per R3).
- **Libraries:** `psycopg` (already a project dependency), `db.pool.get_pool`.
- **High-risk callouts:** None — this task has no runtime wiring yet, pure new addition.

### Scope Boundaries

- Do NOT wire `margin_store` into `main.py` yet — that's T3/T4.
- Do NOT add a `margin_history` table (A8 — audit via existing `audit_log.py` instead, deferred to whichever task wires the admin route).
- Only implement the table, the module, and the `VOUCHER_COLUMNS` addition.

### Files Expected

**New files:**
- `margin_store.py` — `MarginStore` class (`get`, `set`, `apply`), `MarginValueError` exception; mirrors `discount_store.py`
- `tests/test_margin_store.py`

**Modified files:**
- `db/schema.sql` — add `CREATE TABLE IF NOT EXISTS margin_settings (id SMALLINT PRIMARY KEY DEFAULT 1, margin_pct NUMERIC(5,2) NOT NULL DEFAULT 0, updated_at TIMESTAMPTZ, updated_by TEXT);` + seed `INSERT INTO margin_settings (id, margin_pct) VALUES (1, 0) ON CONFLICT DO NOTHING;` + `ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS margin_pct_at_booking NUMERIC(5,2);` (moved forward from T3's original footprint slice, since this task's `models.py` change requires the column to exist for `test_schema.py`/`postgres_repo` to pass)
- `models.py` — add `"margin_pct_at_booking"` to `VOUCHER_COLUMNS`

**Must NOT modify:**
- `discount_store.py` (owned by T2)
- `main.py` (owned by T3/T4)

---

## Task T2: Grandfather-flag wiring in `discount_store.py`

> **Status:** done
> **Verification:** tdd
> **Effort:** s
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** R4, R5
> **Footprint slice:** Modified: `discount_store.py` (`.set()`/`.set_many()` insert branch; new exempt-aware read method), `db/schema.sql` (`discounts.margin_exempt` column — same cross-task gap as T1 hit with `vouchers.margin_pct_at_booking`: the column must exist in the same task that starts reading/writing it)
> **High-risk areas touched:** None

### Description

Adds the permanent per-row grandfather marker: a brand-new `(station_id, fuel_type)` discount row is tagged `margin_exempt=FALSE` at creation, while every pre-existing row (and every subsequent edit to it) stays `margin_exempt=TRUE` forever. This is what lets T3 tell, at read time, whether to run a discount through the margin transform.

### Test Plan

#### Test File(s)
- `tests/test_discount_store.py` (extend existing file, same `schema_db` fixture)

#### Test Scenarios

##### Grandfather Flag on Write

- **new `.set()` insert marks `margin_exempt=FALSE`** — GIVEN no existing row for `(station, fuel_type)` WHEN `.set()` creates it THEN a direct psycopg query on `discounts` shows `margin_exempt=FALSE` _(verifies R4)_
- **existing-row `.set()` update leaves `margin_exempt` untouched** — GIVEN a row pre-seeded `margin_exempt=TRUE` WHEN `.set()` updates its value THEN `margin_exempt` is still `TRUE` _(verifies R5)_
- **repeated edits never flip the flag** — GIVEN 3 successive `.set()` calls on the same row THEN `margin_exempt` is unchanged across all three reads _(verifies R5 permanence)_
- **`set_many()` new-row insert marks `margin_exempt=FALSE`** — bulk path, same assertion as the single-set case _(verifies R4)_
- **`set_many()` existing-row update leaves flag untouched** — bulk path, mirrors single-set case _(verifies R5)_
- **column default backfills pre-existing-style rows as exempt** — GIVEN a row inserted via raw SQL with `margin_exempt` omitted WHEN queried THEN it defaults to `TRUE` _(simulates the migration backfill for rows that existed before ship, verifies R5)_

##### Exempt-Aware Reads

- **new read method returns correct flags for mixed rows** — GIVEN one exempt and one non-exempt row for the same fuel type WHEN the new exempt-aware `get_all`/`get` method is called THEN each entry correctly reports its own `margin_exempt` value _(supports T3's per-row lookup)_

##### Regression Guard

- **`clear_all()` still deletes both exempt and non-exempt rows** — GIVEN a mix of exempt/non-exempt rows WHEN `clear_all()` runs THEN all are removed regardless of flag _(guards backward-regression risk for `discount_store.py`'s existing `clear_all()`)_

### Implementation Notes

- **Module(s):** `discount_store.py` only (Module Boundaries — no dependency on `margin_store.py`).
- **Pattern reference:** existing `.set()`/`.set_many()` methods already distinguish "row exists" (`ON CONFLICT DO UPDATE`) vs. "row is new" via the `old_row`/`old_val` lookup already present — extend that same branch rather than adding new queries.
- **Key decisions:** A3 — `DEFAULT TRUE` on the column handles pre-existing-row backfill automatically at migration time; the INSERT branch explicitly passes `FALSE` only for genuinely new rows; the `ON CONFLICT DO UPDATE` clause must never list `margin_exempt` as a column it sets.
- **Libraries:** none new.
- **High-risk callouts:** None flagged M/H for this file in ARCH, but get this exactly right — an inverted default would silently un-grandfather every historical row on ship.

### Scope Boundaries

- Do NOT change `clear_all()`'s behavior beyond confirming it still works — it's explicitly dead code (no callers) per the module's own docstring.
- Do NOT touch `main.py` call sites yet — that's T3/T4.
- Only implement the column-aware insert/update branching and the new exempt-aware read method.

### Files Expected

**Modified files:**
- `discount_store.py` — `.set()`, `.set_many()` insert branches write `margin_exempt=FALSE`; new method (or extended return shape on `get_all`) surfaces `margin_exempt` per row

**Must NOT modify:**
- `margin_store.py` (owned by T1)
- `main.py` (owned by T3/T4)

---

## Task T3: `/book` margin application + Approve-flow snapshot fix

> **Status:** done
> **Verification:** test-after
> **Effort:** m
> **Priority:** critical
> **Depends on:** T1, T2
> **Satisfies REQs:** R4, R5, R6, R7, R8
> **Footprint slice:** Modified: `main.py` (`book()` GET ~858-950, `book()` POST ~1172-1234, Approve-flow live-fallback branch ~584-600). Note: `vouchers.margin_pct_at_booking` column already exists as of T1 (moved there to keep `models.py`'s `VOUCHER_COLUMNS` change from breaking schema tests) — this task only writes to it, no schema change needed here.
> **High-risk areas touched:** `/book` GET+POST (M — core margin logic on the booking hot path); Admin Approve flow (M — the live-fallback branch is easy to miss and would silently leak raw discount)

### Description

Wires the margin transform into the two moments a customer ever sees a discount — the station picker/calculator on `/book` GET, and the discount snapshot frozen at `/book` POST — and patches the one existing code path (the Approve-flow's zero-snapshot live fallback) that would otherwise bypass margin entirely. Also stamps `margin_pct_at_booking` on every new voucher at snapshot time, regardless of whether the row was margin-exempt.

### Test Plan

#### Test File(s)
- `tests/test_book_pg.py` (extend — booking POST, snapshot assertions)
- `tests/test_book_fuel_type_data.py` (extend — GET station-table shape, or a new `tests/test_book_margin_display.py` if cleaner)
- `tests/test_admin_approve_margin.py` (new — Approve-flow fallback fix; check first whether an existing approve/verify test module already owns this route and extend that instead)

Monkeypatch pattern for all three: `monkeypatch.setattr(main, "discount_store", Fake...)`, `monkeypatch.setattr(main, "margin_store", Fake...)`, mirroring `tests/test_admin_pricing_endpoints.py`'s `FakePriceStore`/`FakeDiscountStore` fixtures.

#### Test Scenarios

##### Booking Display (GET /book)

- **shows post-margin discount for a non-exempt station** — GIVEN margin=12.25%, station discount=10.0, `margin_exempt=False` WHEN `/book` GET renders THEN `station_table` and `station_table_by_fuel` show `8.775`, not `10.0` _(verifies R4, R6)_
- **shows raw discount for an exempt station** — GIVEN the same margin setting WHEN the station is `margin_exempt=True` THEN the table shows `10.0` unchanged _(verifies R5)_
- **0% margin is a no-op for non-exempt stations** — GIVEN margin=0 THEN the displayed value equals the raw discount _(verifies R2)_

##### Booking Snapshot (POST /book)

- **snapshot stores post-margin discount for a non-exempt station** — GIVEN the same setup WHEN a booking is submitted THEN the created voucher's `discount_snapshot_php_per_liter` is the post-margin value, not raw _(verifies R4, R6)_
- **snapshot stores raw discount for an exempt station** — mirrors the above for the exempt case _(verifies R5)_
- **`margin_pct_at_booking` is stamped with the live global margin regardless of exempt status** — GIVEN margin=12.25% WHEN booking against an *exempt* station THEN `margin_pct_at_booking == 12.25` even though the discount itself wasn't reduced _(verifies R7, per developer decision A6)_
- **margin changed after booking never retroactively changes the stored snapshot** — GIVEN a booking created under margin=12.25% WHEN the global margin is later changed to 20% and the same voucher is re-read THEN `discount_snapshot_php_per_liter` and `margin_pct_at_booking` are both unchanged from their original values _(verifies R7, R8)_

##### Approve-Flow Fallback Fix

- **fallback branch applies margin when snapshot is exactly 0 and station is non-exempt** — GIVEN a voucher with `discount_snapshot_php_per_liter == 0.0` (simulating a missing/never-captured snapshot) and the station's *current live* discount is non-zero and `margin_exempt=False` WHEN the Approve action runs its live-fallback lookup THEN the resulting `discount_total` reflects the margin-adjusted value, not the raw live discount _(this is the specific backward-regression risk identified in ARCH — do not skip)_
- **fallback branch returns raw for an exempt station's zero-snapshot case** — same fallback path, `margin_exempt=True` THEN the raw live discount is used unmodified _(verifies R5 holds even in the fallback path)_

##### Regression Guard

- **existing exempt/0%-margin booking scenarios in `test_book_calculator_inversion.py` and `test_book_pg.py` are unaffected** — run those files' existing assertions on discount/pay math against the post-change code and confirm they still pass unchanged _(guards backward-regression risk for those files, per ARCH's Backward stress-test row)_

### Implementation Notes

- **Module(s):** `main.py` only — orchestrates by calling both `discount_store` (raw value + exempt flag) and `margin_store` (`get()` + `apply()`), per Module Boundaries.
- **Pattern reference:** the existing station-normalization helpers (`_norm_dashes`, `_slug`) and snapshot-capture block (lines ~1104-1234) already establish the shape to extend — do not introduce a parallel lookup path.
- **Key decisions:** A4 (stored discount never modified — margin applied only at these two read/capture points), A6 (`margin_pct_at_booking` always recorded, exempt or not).
- **Libraries:** none new.
- **High-risk callouts:** the Approve-flow fallback branch (~587-593) is the single easiest place to regress silently — a missed fix here means margin looks correct for 99% of bookings but leaks raw discount whenever a snapshot is legitimately `0.0`. The dedicated fallback test scenarios above exist specifically to catch this.

### Scope Boundaries

- Do NOT modify `generate_voucher.py` or `report_pdf.py` — confirmed in ARCH (A7, developer decision) that both remain pure consumers of the already-adjusted voucher fields.
- Do NOT add a separate "raw discount" field for the supplier PDF — out of scope per A7.
- Do NOT touch the admin margin-setting route or template — that's T4/T5.
- Only implement the two `/book` call-site changes and the Approve-flow fallback fix.

### Files Expected

**Modified files:**
- `main.py` — `book()` GET path (~858-950): apply margin before building `station_table`/`station_table_by_fuel`; `book()` POST path (~1172-1234): apply margin to `dpl_snapshot`, stamp `margin_pct_at_booking`; Approve-flow (~584-600): fallback branch applies margin

**Must NOT modify:**
- `generate_voucher.py` (silent-regression hotspot — pure consumer, covered by the snapshot-correctness tests above rather than its own changes)
- `report_pdf.py` (confirmed no divergence needed, A7)
- `main.py`'s `admin_prices_update()` (~1636-1736) — already confirmed in ARCH to correctly route through T2's insert branch; not this task's concern

### TDD Sequence

Write the Approve-flow fallback tests first (highest-risk, easiest to get wrong), then the POST-snapshot tests, then the GET-display tests — the display logic is the simplest and best left last as a sanity check once the underlying apply-and-store logic is proven.

---

## Task T4: `admin_margin_update` route + `admin_prices()` context wiring

> **Status:** done
> **Verification:** tdd
> **Effort:** s
> **Priority:** high
> **Depends on:** T1
> **Satisfies REQs:** R1, R3
> **Footprint slice:** Modified: `main.py` (`admin_prices()` ~1463-1511, new `admin_margin_update` route near ~1636)
> **High-risk areas touched:** None

### Description

Adds the admin-facing HTTP surface for reading and writing the global margin: a new authenticated POST endpoint to save it, and a context addition to the existing `admin_prices()` GET handler so the template (T5) has the current value to render.

### Test Plan

#### Test File(s)
- `tests/test_admin_pricing_endpoints.py` (extend, same `FakeXStore` + `client`/`_login` fixture pattern)

#### Test Scenarios

##### Margin Update Endpoint

- **valid margin update succeeds** — GIVEN admin logged in, POST `margin_pct=12.25` WHEN handled THEN response is 200 JSON `{ok: true, margin_pct: 12.25}` and the fake margin store's `.set()` was called with `12.25` _(verifies R1, R3)_
- **rejects more than 2 decimal places** — GIVEN `margin_pct=12.255` THEN 400 with a field-tagged error, same shape as `admin_prices_update`'s validation errors _(verifies R3)_
- **rejects out-of-range values** — GIVEN `margin_pct=-1` and `margin_pct=101` THEN both return 400 _(REQ edge case)_
- **unauthenticated request is rejected** — GIVEN no admin session WHEN POST THEN the same auth-failure behavior as `admin_prices_update` (403 JSON, confirm exact status/shape against that route) _(NFR: admin-auth parity)_

##### Admin Prices Context

- **`admin_prices()` GET includes the current margin value** — GIVEN the fake margin store returns `5.5` WHEN `/admin/prices` is rendered THEN the template is invoked with that value in context (assert via a monkeypatched `render_template` capture or by checking the value appears in the response body) _(verifies R1)_

### Implementation Notes

- **Module(s):** `main.py` only.
- **Pattern reference:** `admin_prices_update()` (~1636-1736) — mirror its request-validation-then-write structure and its JSON error response shape (`{"ok": False, "error": ..., "field": ...}`).
- **Key decisions:** reuses `require_admin(request)` exactly as every other `/admin/*` write route (NFR: same admin auth as rest of `admin_prices`).
- **Libraries:** none new.
- **High-risk callouts:** None.

### Scope Boundaries

- Do NOT build a `margin_history` table or audit UI here — margin-change audit logging (if wired in this task) goes through the existing `audit_log.py`, not a new table (A8).
- Do NOT touch `templates/admin_prices.html` — that's T5.
- Only implement the route and the `admin_prices()` context addition.

### Files Expected

**Modified files:**
- `main.py` — `admin_prices()` (~1463-1511): pass `margin_store.get()` into template context; new route near ~1636: `admin_margin_update`, admin-gated, validates and calls `margin_store.set()`

**Must NOT modify:**
- `templates/admin_prices.html` (owned by T5)
- `discount_store.py`, `margin_store.py` (owned by T1/T2, consumed read-only here)

---

## Task T5: Admin margin UI field

> **Status:** not started
> **Verification:** ui
> **Effort:** xs
> **Priority:** medium
> **Depends on:** T4
> **Satisfies REQs:** R1, R3
> **Footprint slice:** Modified: `templates/admin_prices.html`
> **High-risk areas touched:** None

### Description

Adds the single global "% Profit Margin" input and save control to the admin prices page, wired to T4's endpoint. Purely visual/interaction work — the underlying logic and validation are already covered by T4's tests.

### Verification Checklist

- **Field renders once, not per-row** — expected: a single "% Profit Margin" input appears once on `/admin/prices`, visually separate from the per-station price/discount inputs — confirms R1's "single value governs every row" at the UI level
- **Field pre-populates on load** — expected: with margin already set to e.g. `5.5`, the input shows `5.5` on page load, not blank or `0`
- **Invalid input (>2 decimals) surfaces the T4 error inline** — expected: entering `12.255` and saving shows an inline error/toast matching the existing `admin_prices_update` error-display convention, value is not saved
- **Valid input saves and confirms** — expected: entering `12.25` and saving shows a success toast; reloading the page shows `12.25` still in the field
- **Reuses existing visual language** — expected: save button uses the existing `.btn` class, feedback uses the existing `#toast` element — no new CSS component introduced
- **Auth gate unchanged** — expected: an unauthenticated visit to `/admin/prices` still redirects to login exactly as it does today; no new bypass introduced by the added markup
- **Layout holds at narrow viewport** — expected: at mobile width, the new field doesn't break or overlap the existing pricing table layout

#### Testable Seams

- Template renders `0` (not a crash or blank) when `margin_store.get()` returns an unexpected `None` — worth a quick component-level check even though this is a `ui` task, since it's a one-line defensive assertion

### Implementation Notes

- **Module(s):** `templates/admin_prices.html` only.
- **Pattern reference:** existing `.btn`, `#toast`, `.row` styles already defined in this file's `<style>` block (lines ~9-84 per ARCH's file scan) — no new component classes needed.
- **Key decisions:** A1 (single global field, not per-row) is the one design constraint that most affects layout — do not place it inside the per-station table.
- **Libraries:** none new.
- **High-risk callouts:** None.

### Scope Boundaries

- Do NOT add per-station or per-fuel-type margin inputs — out of scope per REQ Scope Boundaries ("Per-station or per-fuel-type margin variation").
- Do NOT add a margin-history/audit-trail UI — out of scope per REQ Scope Boundaries ("Admin-facing reporting/dashboard of margin revenue over time").
- Only implement the single global input, its pre-population, and its save/error/success interaction wired to T4's endpoint.

### Files Expected

**Modified files:**
- `templates/admin_prices.html` — new global margin input + save control

**Must NOT modify:**
- `main.py` (owned by T4)
