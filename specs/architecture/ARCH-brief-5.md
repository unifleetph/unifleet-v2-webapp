# Architecture: Brief-5 UI & Calculator Updates

> **Date:** 2026-08-02
> **Phase:** 2 of 5 (System Architecture)
> **Requirements source:** specs/requirements/REQ-brief-5.md
> **Type:** feature

## Architecture Summary

Three independent slices land in the existing Flask monolith (`main.py` + Jinja templates), no new services. (1) `booking_success.html` gets a static copy/QR swap — no backend change. (2) The `book.html` calculator flips its input's meaning from "prepaid cash" to "total fuel value"; because the booking pipeline (`main.py`, `db/postgres_repo.py`/CSV repo, `generate_voucher.py`, redemption) all currently derive discount/total from a single stored column (`requested_amount_php`), a new column `requested_total_php` is added so the entered total survives to redemption without downstream code re-deriving a wrong number from the discounted pay amount. (3) `admin_prices.html` consolidates its already-half-AJAX save pattern (price is AJAX today, discount is a full-reload form POST) into one combined AJAX endpoint per fuel type, plus a sticky `<thead>`.

## High-Level Structure

```
Customer flow (item 1, 2):
  book.html (GET/POST /book)
    └─ client JS: total-fuel-amount → discount subtract → pay          [item 2, client-side]
    └─ POST stores BOTH requested_total_php (T) AND requested_amount_php (pay)
  booking_success.html                                                  [item 1, static copy/QR]

  ... later, admin approves booking (Unverified → Unredeemed) ...
  ops_set_status() in main.py
    └─ liters_requested = requested_total_php / snapshot_price          [item 2, fixed formula]
    └─ discount_total   = liters_requested * snapshot_discount
    └─ total_dispensed  = requested_amount_php + discount_total          (= T, self-consistent)
    └─ generate_voucher.py renders PDF from these already-correct fields  [unchanged]

Admin flow (item 3):
  admin_prices.html
    └─ 1 button/fuel-type → fetch → /admin/prices/update (extended)
         validates price AND discount together, saves both or neither
    └─ sticky thead (CSS only)
```

## Tech Choices

| Area                     | Decision                                                        | Alternatives Considered                                   | Rationale |
|---------------------------|------------------------------------------------------------------|-------------------------------------------------------------|-----------|
| Calculator total storage  | New column `requested_total_php`, keep `requested_amount_php` meaning unchanged | (a) Repurpose `requested_amount_php` to hold T; (b) derive T on the fly at redemption from pay + discount | (a) breaks every downstream consumer that reads `requested_amount_php` as "amount charged" (PDF, supplier API, exports); (b) is mathematically lossy — `pay + k·pay ≠ T` when discount is instead derived from `pay` rather than `T` (see REQ Common Mistakes). Storing T directly is the only option that keeps both the charge amount and the delivered value exact. |
| Admin save consolidation  | Extend `/admin/prices/update` (existing JSON/fetch endpoint) to accept an optional `discount_per_liter` and save both atomically | New separate combined endpoint | Price save is already AJAX with the exact request/response shape needed; extending it avoids duplicating the fetch/toast/stale-marking JS scaffolding already built for it in `admin_prices.html`. |
| Sticky header             | CSS `position: sticky` on `<thead><tr>` within an added `overflow: auto` wrapper `<div>` around the `<table>` | JS-based header clone/shadow-table | Table has no existing scroll container (whole page scrolls); per REQ's own flagged Common Mistake, sticky needs a scoped scroll container to behave consistently — wrapping the table is the minimal change that provides one. |

## Patterns & Conventions

- **Repo abstraction (`persistence.py` / `db/postgres_repo.py`)** — both repos build voucher rows from `models.VOUCHER_COLUMNS`; adding a field there is enough for both CSV and Postgres paths to persist it, no repo-code changes needed. Followed as-is.
- **Postgres additive migration pattern** — `db/schema.sql` already has a precedent for adding a nullable column to the live `vouchers` table post-deploy: `ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS fuel_type VARCHAR(30);` (line 112). `requested_total_php` follows the same idempotent, nullable-for-historical-rows pattern.
- **Booking-time snapshots** — price/discount used at redemption are snapshotted at booking time (`price_snapshot_php_per_liter`, `discount_snapshot_php_per_liter`), not re-read live. `requested_total_php` fits this existing snapshot pattern — it's also frozen at booking time, not recomputed later.
- **Not applying:** a full "repurpose `requested_amount_php`" rename — rejected per Tech Choices above; changing an existing column's meaning in-place with no way to distinguish old rows from new rows is riskier than adding one.

## Data Models

### `vouchers` (existing table, one new column)

**Purpose:** central booking/voucher record — unchanged in purpose; gains one field.

**Key fields:**
| Field                  | Type / Constraint                | Notes |
|-------------------------|-----------------------------------|-------|
| `requested_total_php`   | `NUMERIC(12,2)`, nullable         | NEW. The customer-entered "Total Fuel Amount" (T) from the calculator. Null for all pre-existing rows (booked before this ships) — those rows keep using `requested_amount_php` as both charge and delivered-value under the old model, since they were created under it. |
| `requested_amount_php`  | `NUMERIC(12,2)`, nullable (existing) | Unchanged meaning: the amount actually charged/prepaid. Now computed as `T − discount(T)` instead of being the customer's raw entry. |

**Relationships:** unchanged — no new FKs.

**Lifecycle:** `requested_total_php` is set once at booking creation (`POST /book`), alongside `requested_amount_php`, and never modified after. Same lifecycle stage as the existing snapshot fields.

## API Contracts / Interfaces

### `POST /book` (existing, modified)

**Boundary:** HTTP form POST (server-rendered, existing route in `main.py`).

Request field `requested_amount_php` (form input, still named this in `book.html`'s `<input name="requested_amount_php">` per REQ R4's "Total Fuel Amount" label — the **name attribute stays**, only the label text and the value it holds changes semantically) now carries the entered **total** T. Server computes `pay = T − discount(T)` (same per-liter discount formula, applied server-side using the fresh live price + discount at submit time, mirroring what the client showed) and stores both `requested_total_php = T` and `requested_amount_php = pay`.

**Validation added:** if `pay ≤ 0` for the resolved station/discount, reject the booking with a flashed error and re-render `book.html` (same pattern as existing validation failures in this handler) — satisfies R10.

### `/admin/prices/update` (existing, extended)

**Boundary:** JSON/fetch, admin-only (`require_admin`).

| Method/Op | Path/Signature | Purpose | Errors/Returns |
|-----------|-----------------|---------|-----------------|
| POST | `/admin/prices/update?key=` — body `{station_id, fuel_type, price, discount_per_liter}` | Save price AND discount for one station+fuel_type in one call | `{ok:false, error, field: "price"|"discount"}` on validation failure (neither saved); `{ok:true, price_php_per_liter, discount_per_liter, updated_at}` on success |

`discount_per_liter` becomes a required field in the payload (previously price-only). Both `price_store.set_price` (bounds: `0 < price ≤ 200`) and `discount_store.set` (bounds: `0 ≤ discount ≤ 15`, enforced at the route level same as today's `admin_discounts_update`) are validated **before either write happens** — satisfies R15's all-or-nothing requirement.

**Auth requirements:** `require_admin(request)` — unchanged, same as both endpoints it replaces.

### `/api/v1/price_preview` (existing, modified)

**Boundary:** public read-only GET API, mirrors the client-side calculator math.

Currently computes `total_dispensed = amount + discount(amount)` (add-on-top model). Rewritten to match the new calculator direction: given `amount` now means "total fuel amount", returns `pay = amount − discount(amount)` as a new `you_pay_php` field, keeping `requested_amount_php` in the response as the echoed input (renamed meaning, same key, per REQ R6/R7 parity) for backward JSON-shape compatibility with any existing caller that only reads `liters_requested`/`discount_total` (both unchanged formulas — only `total_dispensed`'s meaning and the new `you_pay_php` field change).

## Module Boundaries

No new modules. Existing boundaries hold: `main.py` (routes/orchestration) never imports template logic; `price_store.py`/`discount_store.py` own their respective tables and validation; `models.py` remains the single source of truth for the voucher row shape that both repos key off of.

## Change Footprint

### New files / modules

| Path | Purpose | Pattern reference |
|------|---------|---------------------|
| `static/instapay_qr.png` (or reuse `static/qr_codes/`) | Static InstaPay QR asset for the booking-success page | `static/UniFleet Logo.png` (existing static asset served the same way) |

### Modified files / modules

| Path | What changes here |
|------|----------------------|
| `templates/booking_success.html` | Drop "(InstaPay or PESONet)" from the amount-due line (R1); add QR `<img>` + caption inside the existing (currently-unused) `.payment-qr` CSS block already defined in this file (R2, R3 — no new styling needed, block exists but is unused in markup today). |
| `templates/book.html` | Relabel input to "Total Fuel Amount (in PHP)" (R4); rewrite `updatePreview()` JS to subtract discount from the entered value instead of adding it, reorder/relabel `.cost-row` elements to match image-6 (R5, R6, R7, R8); remove the "How Discounts Work" `<details>` block (R9); add inline validation that hides/blocks the preview and shows an error when computed pay ≤ 0 (R10). |
| `main.py` — `book()` POST handler (~line 1080) | Compute `pay = T − discount(T)` server-side from `request.form['requested_amount_php']` (now holding T) using the same price/discount snapshot already resolved in this handler; store both `requested_total_php` and `requested_amount_php` in the booking row dict; reject with flash + re-render if `pay ≤ 0` (R10). |
| `main.py` — `ops_set_status()` Unredeemed branch (~lines 500-579) | Change `liters_requested`/`discount_total` to derive from `requested_total_php` instead of `requested_amount_php`; `total_dispensed = requested_amount_php + discount_total` formula itself is unchanged (now correctly reconstructs to T). Guard: if `requested_total_php` is null (pre-migration row), fall back to today's formula using `requested_amount_php` as both base and charge (preserves old-row behavior). |
| `main.py` — `api_price_preview()` (~line 1543) | Rewrite to subtract-based model per API Contracts section above. |
| `main.py` — `admin_prices_update()` (~line 1425) | Accept optional `discount_per_liter` in the JSON payload; validate both price and discount before writing either; call `discount_store.set(...)` alongside the existing `price_store.set_price(...)` call; drop `admin_discounts_update()`'s form-POST route once the UI no longer calls it (or leave dead — see Open Questions). |
| `templates/admin_prices.html` | Remove the per-discount `<form>` (lines 103-114); merge discount input into the same cell/button as price; one `.save-price-btn` per fuel type now reads both inputs and posts both fields; wrap `<table>` in a scrollable container and add `position: sticky` to `<thead>` (R11, R12, R13, R14, R15). |
| `models.py` | Add `"requested_total_php"` to `VOUCHER_COLUMNS` (propagates to both repos automatically); add matching column to the legacy SQLite `SCHEMA_SQL` string. |
| `db/schema.sql` | Add `requested_total_php NUMERIC(12,2)` to the `vouchers` `CREATE TABLE` block; add `ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS requested_total_php NUMERIC(12,2);` below the existing `fuel_type` precedent (line 112) so it lands on the already-deployed Railway DB. |

### Deleted / replaced

| Path | Reason |
|------|--------|
| `templates/admin_prices.html` discount `<form>` block | Replaced by the combined save button/JS (R12). |

### Touched but not changed (silent-regression hotspots)

| Path | Why it matters |
|------|--------------------|
| `generate_voucher.py` (PDF text, ~line 150) | Reads `requested_amount_php`/`discount_total`/`total_dispensed` — all three remain correct with no code change here, *provided* `ops_set_status()` is fixed as designed. If that fix is skipped or wrong, this file will silently print an inconsistent voucher with no error. |
| `main.py` supplier API / admin bookings export (~lines 1150-1240) | Read the same three already-persisted fields; correct as a side effect of the `ops_set_status()` fix, not touched directly. |
| `generate_voucher.py` `append_and_generate_vouchers()` / `REQUIRED_COLUMNS` (bulk CSV admin-upload path) | Does not include `requested_total_php` and is not being updated — bulk-uploaded rows keep computing under the old add-on-top formula. Out of scope (legacy admin path, not part of the customer booking flow this REQ touches), but flagged since it's an existing consumer of the same voucher schema that will silently diverge in behavior from the customer-facing flow. |
| `tests/test_book_and_booking_success_copy.py` | Asserts the exact "PESONet" string (line 125) — will fail after R1 and must be updated as part of this change, not left broken. |
| `tests/test_api_v1_pricing.py` | Exercises `api_price_preview` — needs updated expected values once the endpoint's formula changes. |
| `tests/test_admin_pricing_endpoints.py` | Exercises the current price-only `/admin/prices/update` and discount-only `/admin/discounts/update` — needs new/updated cases for the combined payload and all-or-nothing validation. |
| `tests/test_schema.py`, `tests/test_apply.py` | Assert schema/column expectations — will need `requested_total_php` added to whatever column list they check. |

## Areas of Impact

| Area | Impact | Risk (L/M/H) | Why |
|------|--------|:---:|-----|
| Booking money flow (`main.py` book handler + `ops_set_status`) | Core discount/charge arithmetic changes direction | **H** | Directly affects what customers are charged; a formula mistake either overcharges or undercharges real money. |
| Voucher PDF / supplier API / admin exports | No code change, but depends entirely on the Approve-flow fix being correct | **M** | "Touched but not changed" — silent regression risk if the fix is incomplete; no compile-time signal will catch a wrong formula here. |
| Admin price/discount editing (`admin_prices.html`, `/admin/prices/update`) | Endpoint contract gains a field; one old route effectively retired | **L** | Contained to admin-only UI; single internal consumer (the template's own JS). |
| Legacy bulk-CSV voucher import (`generate_voucher.py`) | Diverges from customer-facing flow's semantics for that one path | **L** | Rare/manual admin path, already legacy per `archive/` sibling tooling; not customer-facing. |
| Database schema | Additive column only, no backfill, no data loss | **L** | Follows an established safe pattern (`fuel_type` precedent) already proven on this Railway deployment. |

**Contract changes:** `/api/v1/price_preview` response gains `you_pay_php` and reinterprets `total_dispensed`'s meaning (still same key, different formula) — any external consumer reading `total_dispensed` from this specific endpoint would see different numbers post-deploy. No known external consumer identified in this codebase (it's not referenced by `supplier_api` or any test outside `test_api_v1_pricing.py`), but flagged since it's a public unauthenticated endpoint.

**Cross-cutting ripples:** one additive DB migration (nullable column, safe on both fresh and already-deployed Postgres per the Dockerfile's auto-apply-on-start chain — `db/apply.py` runs before `gunicorn` per `AGENTS.md`). No feature flag needed — this ships as a single atomic behavior change, consistent with how prior Briefs (3, 4) shipped.

## Cross-Cutting Concerns

- **Errors:** Booking-time `pay ≤ 0` rejection reuses the existing flash-message + re-render pattern already used elsewhere in the `book()` handler (e.g., missing station match) — no new error-handling mechanism introduced. Admin combined-save validation errors surface via the existing JSON `{ok:false, error}` shape the price endpoint already returns, extended with a `field` key so the JS can highlight the specific bad input.
- **Logging & metrics:** Existing `print()`-based logging in `main.py` (e.g. the `[BOOK] snapshots:` line) is the current pattern; add one line logging `requested_total_php`/`requested_amount_php` at booking creation for the same debuggability the existing snapshot log line provides.
- **Auth & authz:** No change — `require_admin` gate on admin routes is untouched; `/api/v1/price_preview` and `/book` remain at their existing auth levels (public).
- **Performance & scale:** No new queries in a hot path; one extra column on an existing row write. Sticky-header CSS has no runtime cost.
- **Security:** No new user input beyond what's already accepted (numeric amount/discount, station selection) — same validation boundaries as today.
- **Migrations & rollout:** Single additive, nullable-column migration auto-applies via the existing `db/apply.py` → `gunicorn` Dockerfile chain — no manual migration step, consistent with how this project already ships schema changes.

## Architecture Decisions Log

| # | Decision | Alternatives | Chosen Because | Satisfies REQs |
|---|----------|---------------|------------------|------------------|
| A1 | Add `requested_total_php` column; keep `requested_amount_php`'s existing meaning (amount charged) | Repurpose `requested_amount_php` to hold the total; derive total on the fly at redemption | Only option that keeps both the charge amount and delivered fuel value exact — repurposing breaks 3+ downstream consumers, deriving on the fly is mathematically lossy | R5, R6, R7, R8 |
| A2 | Fix `ops_set_status()` to compute `liters_requested`/`discount_total` from `requested_total_php`, keep `total_dispensed = requested_amount_php + discount_total` formula unchanged | Rewrite the whole Approve-flow calc block | Minimal surgical change — swapping which column feeds the liters formula is sufficient; the existing add-back formula already reconstructs to T once fed the right liters | R5, R6, R7 |
| A3 | Extend `/admin/prices/update` to accept price+discount together, rather than a new endpoint | New combined endpoint; keep 2 endpoints, sequence 2 fetches behind 1 button | Reuses existing fetch/toast/stale-marking JS scaffolding already built around this endpoint; avoids a second round-trip or two endpoints to keep in sync | R12, R13, R14, R15 |
| A4 | All-or-nothing validation: validate price AND discount fully before writing either | Save whichever field passes | REQ decision #5 — keeps price and discount for a fuel type always mutually consistent | R15 |
| A5 | Booking-time `pay ≤ 0` rejected via existing flash + re-render pattern | New client-only validation without server-side enforcement | Server-side enforcement is required since the client calc could be bypassed (direct POST); reusing the existing pattern avoids introducing a new error UX | R10 |
| A6 | Sticky header via CSS `position: sticky` + a new scroll-container wrapper `<div>` | JS-cloned header row | Matches the REQ's own flagged pitfall (sticky needs a scoped scroll container); pure CSS, no JS-sync-drift risk | R11 |
| A7 | Static QR image added to `static/`, referenced via existing `.payment-qr` CSS already present but unused in `booking_success.html` | Generate QR at request time via the `qrcode` library already in `pyproject.toml` | REQ confirmed the QR is static/account-level — generating it at request time for a value that never changes adds no value and a new code path | R2, R3 |

## Risk & Stress-Test Scenarios

### Forward — runtime failure scenarios

| Scenario | How the Design Handles It |
|----------|-------------------------------|
| Customer submits a booking where the resolved discount ≥ entered total (pay would be ≤ 0) | Server-side rejection in the `book()` POST handler before the row is created — booking never persists a ≤0 `requested_amount_php` (R10, A5). |
| Two admins save the same station+fuel-type's price+discount within the same second | Last write wins via the existing `ON CONFLICT ... DO UPDATE` upsert pattern already used by `price_store.set_price` / `discount_store.set` — no new locking introduced, matches REQ decision #6. |
| Deploy ships with the `requested_total_php` migration but `ops_set_status()` fix is somehow skipped | GAP — see Open Questions: nothing currently guards against the Approve-flow formula silently reverting to the old (wrong) computation; only a test catches this, there's no runtime assertion. |
| A pre-existing "Unverified" booking (created before this ships) reaches Approve after deploy, with `requested_total_php` = NULL | Explicit fallback in the modified `ops_set_status()`: if `requested_total_php` is null, use `requested_amount_php` as the liters/discount base (today's formula) — old in-flight bookings settle under the old model, new ones under the new model. |
| `/api/v1/price_preview` called by an unknown external consumer expecting the old add-on-top `total_dispensed` | GAP — no consumer of this endpoint was found in-repo; flagged in Areas of Impact/Contract changes, not fully closable from code alone. |

### Backward — regression risk per touched area

| Touched area | What could regress | How we'd know / mitigation |
|------|------|------|
| `ops_set_status()` Approve-flow formula | Wrong liters/discount_total/total_dispensed for every future voucher — silent, no error thrown, PDF just prints wrong numbers | `test_admin_pricing_endpoints.py`/new booking-flow test should assert `total_dispensed == requested_total_php` (within rounding) for a fresh booking; this identity is the single most important regression check this change introduces |
| `/admin/prices/update` payload shape change | Any other caller of this endpoint besides `admin_prices.html`'s own JS breaks if `discount_per_liter` becomes required | Grep confirmed `admin_prices.html` is the only caller in-repo; kept as optional-with-validation rather than strictly required to avoid breaking any untracked external caller |
| Removing the `admin_discounts_update()` form route from the UI | Bookmarked URLs or the `?key=` query-string workflow mentioned in `admin_prices.html`'s own "Tip" text could reference the old discount-form action directly | Route itself is left in place (not deleted) even though the UI stops using it — no link/bookmark breaks, only the form markup is removed |
| `test_book_and_booking_success_copy.py` | Hardcoded PESONet assertion (line 125) breaks on R1 | Must be updated in the same change — flagged explicitly in Change Footprint |

## Open Questions

- Does anything outside this repo call `/api/v1/price_preview` and depend on `total_dispensed` meaning "add-on-top voucher total"?
  - **Impact if unresolved:** An external consumer could silently start receiving smaller `total_dispensed` values with no error.
  - **Suggested default:** Ship as designed (no in-repo consumer found); add a changelog note for this endpoint specifically.
- Should the now-unused `/admin/discounts/update` route be deleted, or left in place as a dead but harmless legacy route?
  - **Impact if unresolved:** Minor dead-code accumulation; no functional risk either way since it's simply unlinked from the UI.
  - **Suggested default:** Leave it in place for this change; revisit in a cleanup pass.
- Should `generate_voucher.py`'s bulk-CSV admin-upload path (`append_and_generate_vouchers`) eventually gain `requested_total_php` support for consistency?
  - **Impact if unresolved:** That legacy path keeps computing under the old formula indefinitely, diverging from the customer-facing flow.
  - **Suggested default:** Out of scope for this REQ; track as a follow-up if that bulk-upload path is still actively used.

## Out of Scope

- Per-booking dynamic QR generation (REQ scope boundary — static QR only).
- Any change to discount tiers/rates themselves (REQ scope boundary).
- Conflict detection/optimistic locking for concurrent admin edits (REQ scope boundary).
- `generate_voucher.py` bulk-CSV admin-upload path gaining `requested_total_php` awareness (see Open Questions).
- Deleting the now-unused `/admin/discounts/update` route (see Open Questions).

---

# Tasks

## Task T1: Add `requested_total_php` column (schema + models)

> **Status:** done
> **Verification:** checklist
> **Effort:** xs
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** R5, R6, R7 (foundation for)
> **Footprint slice:** Modified: `models.py`, `db/schema.sql`
> **High-risk areas touched:** None directly — enables the H-risk area (booking money flow) in T2

### Description

Add a new nullable column `requested_total_php` to the `vouchers` table so the calculator's entered "total fuel amount" can be stored separately from `requested_amount_php` (which keeps its existing meaning: amount actually charged). Additive-only migration, following the exact precedent already in this file for the `fuel_type` column.

### Verification Checklist

- [ ] `poetry run pytest tests/test_schema.py -k requested_total` — new test asserting `requested_total_php` is present on `vouchers` (extend `test_vouchers_has_all_voucher_columns` coverage, since it already iterates `models.VOUCHER_COLUMNS`) — expected: passes
- [ ] `poetry run pytest tests/test_apply.py -k idempotent` — expected: still passes (additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` doesn't break re-apply on an already-migrated DB)
- [ ] `make test-db` — expected: full suite green, no regressions in `test_schema.py`/`test_apply.py` (both flagged as touched in ARCH's backward-regression table)
- [ ] `make verify` — expected: exits 0 (`scripts/verify_build.py` build-sanity check passes)

### Implementation Notes

- **Module(s):** `models.py` (VOUCHER_COLUMNS, legacy SQLite `SCHEMA_SQL`), `db/schema.sql` (Postgres)
- **Pattern reference:** `db/schema.sql:112` — `ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS fuel_type VARCHAR(30);` is the exact pattern to replicate (nullable, idempotent, historical rows stay NULL)
- **Key decisions:** ARCH A1 — this column is what makes A1 possible; do not repurpose `requested_amount_php` instead
- **Libraries:** none new
- **High-risk callouts:** none — this task is pure schema, the money-logic risk lives in T2

### Scope Boundaries

- Do NOT touch `generate_voucher.py`'s `REQUIRED_COLUMNS` list (bulk-CSV admin-upload path is explicitly out of scope per ARCH Open Questions)
- Do NOT backfill historical rows — nullable is correct, per ARCH's data model section
- Only add the one column; no other schema changes

### Files Expected

**Modified files:**
- `models.py` — add `"requested_total_php"` to `VOUCHER_COLUMNS`; add matching field to legacy SQLite `SCHEMA_SQL` string
- `db/schema.sql` — add `requested_total_php NUMERIC(12,2)` to the `vouchers` `CREATE TABLE` block; add `ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS requested_total_php NUMERIC(12,2);` below the existing `fuel_type` precedent (line 112)

**Must NOT modify:**
- `generate_voucher.py` (`REQUIRED_COLUMNS`, `append_and_generate_vouchers`) — out of scope per ARCH Open Questions

---

## Task T2: Booking money-flow backend (store total + fix Approve-flow formula)

> **Status:** done
> **Verification:** tdd
> **Effort:** m
> **Priority:** critical
> **Depends on:** T1
> **Satisfies REQs:** R5, R6, R7, R10
> **Footprint slice:** Modified: `main.py` — `book()` POST handler (~line 1080), `ops_set_status()` Unredeemed branch (~lines 500-579)
> **High-risk areas touched:** Booking money flow (H) — core discount/charge arithmetic changes direction; Voucher PDF/supplier API/admin exports (M) — depend entirely on this fix being correct

### Description

The calculator's input now represents the total fuel value (T) the customer wants, not the cash they prepay. The `book()` handler must compute `pay = T − discount(T)` server-side and store both `requested_total_php = T` and `requested_amount_php = pay`, rejecting the booking if `pay ≤ 0`. The Approve-flow (`ops_set_status`, triggered when an admin moves a booking to `Unredeemed`) must derive `liters_requested`/`discount_total` from `requested_total_php` instead of `requested_amount_php`, so `total_dispensed` (still `requested_amount_php + discount_total`) reconstructs exactly to T. This is the financially critical core of the whole REQ — a wrong formula either overcharges or undercharges real money, silently.

### Test Plan

#### Test File(s)
- `tests/test_book_calculator_inversion.py` (new — mirrors `test_book_pg.py`'s `RepoStub`/fixture pattern: stub `main.repo`, stub `price_store.list_stations`/`discount_store.get`)

#### Test Scenarios

##### Booking creation stores both values

- **stores requested_total_php and computed requested_amount_php** — GIVEN a valid booking POST with `requested_amount_php=1000` (form field name unchanged, now carrying T), price=76.03, discount=0.55/L WHEN booked THEN the stored row has `requested_total_php == 1000` and `requested_amount_php ≈ 927.66` _(verifies R5, R6, R7)_
- **discount formula matches today's per-liter calc** — GIVEN the same numeric input as today's `dpl`/price WHEN pay is computed THEN the discount rebate amount equals today's rebate for that input (liters = T/price, discount = liters × dpl) _(verifies R6)_

##### Booking rejection on non-positive pay

- **rejects when computed pay ≤ 0** — GIVEN a discount large enough that `T − discount(T) ≤ 0` WHEN booked THEN no booking row is created, a flash error is shown, and `book.html` is re-rendered with the submitted form values retained (same pattern as the existing station-not-found rejection at ~line 1035) _(verifies R10, REQ edge case)_

##### Approve-flow formula (`ops_set_status`)

- **total_dispensed reconstructs to the entered total** — GIVEN a booking with `requested_total_php` set and Unverified status WHEN `ops_set_status(id, 'Unredeemed')` runs THEN `liters_requested = requested_total_php / price`, `discount_total = liters_requested * dpl`, and `total_dispensed == requested_total_php` (within rounding) _(verifies R6, R7 — this is the single most important regression check per ARCH)_

##### Regression guard — pre-migration bookings

- **falls back to old formula when requested_total_php is null** — GIVEN a booking row created before this change (`requested_total_php IS NULL`) WHEN approved THEN the handler falls back to using `requested_amount_php` as both the liters-formula base and the charge amount, exactly as it does today _(guards backward-regression risk for in-flight bookings, per ARCH's forward-stress scenario)_

### Implementation Notes

- **Module(s):** `main.py` (`book()`, `ops_set_status()`)
- **Pattern reference:** existing flash + re-render blocks in `book()` (e.g. the station-match failure at ~line 1035-1053) for the pay≤0 rejection; existing snapshot-capture block (~lines 989-1075) for where to compute `pay`
- **Key decisions:** ARCH A1 (new column, don't repurpose `requested_amount_php`), A2 (surgical formula swap — only change which column feeds liters/discount, keep the add-back formula), A5 (reuse existing flash+re-render error pattern, not a new mechanism)
- **Libraries:** none new
- **High-risk callouts:** this is the H-risk area from ARCH's Areas of Impact table — a mistake here directly changes what customers are charged. The `total_dispensed == requested_total_php` identity test is the load-bearing regression check; it must exist before this task is considered done.

### Scope Boundaries

- Do NOT change discount tiers/rates themselves (REQ out of scope)
- Do NOT touch `generate_voucher.py` — it reads already-computed fields and needs no change if this task's formula is correct
- Do NOT touch supplier API / admin export code paths — they read the same already-persisted fields, correct as a side effect
- Only the two named functions in `main.py`

### Files Expected

**Modified files:**
- `main.py` — `book()` POST handler: compute and store `requested_total_php`/`requested_amount_php`, reject `pay ≤ 0`
- `main.py` — `ops_set_status()` Unredeemed branch: derive liters/discount from `requested_total_php`, with null-fallback

**Must NOT modify:**
- `generate_voucher.py` (touched-but-not-changed — depends on this task's correctness, covered by the regression-guard test above)
- `main.py` supplier API / admin export sections (~lines 1150-1240) — touched-but-not-changed, same dependency

### TDD Sequence

Write the "total_dispensed reconstructs" test first (it defines the contract), then the storage test, then the rejection test, then the null-fallback regression guard.

---

## Task T3: Rewrite `api_price_preview` to the subtract-based model

> **Status:** done
> **Verification:** tdd
> **Effort:** s
> **Priority:** medium
> **Depends on:** None
> **Satisfies REQs:** R5, R7
> **Footprint slice:** Modified: `main.py` — `api_price_preview()` (~line 1543)
> **High-risk areas touched:** None flagged M/H for this endpoint specifically, but it's a public unauthenticated contract change (see ARCH Contract changes)

### Description

This endpoint duplicates the calculator's math for external/preview callers. It currently implements the old add-on-top model (`total_dispensed = amount + discount(amount)`). Rewrite it to match the new subtract-based calculator: add a `you_pay_php` field, keep `liters_requested`/`discount_total` formulas as today (still `amount / price` and `liters × dpl`, since `amount` now means "total"), and change what `total_dispensed` represents accordingly.

### Test Plan

#### Test File(s)
- `tests/test_api_v1_pricing.py` (extend existing `FakePriceStore`/`FakeDiscountStore` fixtures)

#### Test Scenarios

##### New subtract-based response

- **returns you_pay_php** — GIVEN `amount=1000` and a known price/discount WHEN `/api/v1/price_preview` is called THEN the response includes `you_pay_php = amount − discount_total` _(verifies R5)_
- **total_dispensed reflects new semantics** — GIVEN the same inputs THEN `total_dispensed` no longer equals `amount + discount_total` under the old formula's intent — verify it matches the new documented meaning _(verifies R7)_

##### Regression guard — response shape

- **existing fields still present and correctly formed** — GIVEN the same inputs THEN `liters_requested`, `discount_total`, `price_php_per_liter`, `station_id`, `station_name`, `price_is_stale` keys are all still present with their existing formulas unchanged _(guards backward-regression risk — response-shape backward compat, per ARCH Contract changes)_
- **invalid amount/station errors unchanged** — GIVEN a missing station or amount ≤ 0 THEN the existing 400/404 error responses behave identically to today _(regression guard)_

### Implementation Notes

- **Module(s):** `main.py`
- **Pattern reference:** the endpoint's own existing structure (~lines 1543-1610); mirror the same subtract logic being added to `book()` in T2 so both stay consistent, though the two are independently deployable
- **Key decisions:** ARCH's API Contracts section (subtract-based rewrite, `you_pay_php` new field, `requested_amount_php` key kept for backward JSON-shape compat)
- **Libraries:** none new
- **High-risk callouts:** flagged in ARCH as a public unauthenticated contract change with no known in-repo consumer — ship as designed per the Open Questions' suggested default

### Scope Boundaries

- Do NOT add authentication to this endpoint — out of scope, unrelated to this REQ
- Do NOT change the `/api/v1/prices` or `/api/v1/discounts` endpoints — only `price_preview`

### Files Expected

**Modified files:**
- `main.py` — `api_price_preview()` function only

**Must NOT modify:**
- `main.py` — `api_prices_list()`, `api_discounts_list()` (same file, different functions, out of scope)

---

## Task T4: Admin combined price+discount save endpoint

> **Status:** not started
> **Verification:** tdd
> **Effort:** s
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** R12, R15
> **Footprint slice:** Modified: `main.py` — `admin_prices_update()` (~line 1425)
> **High-risk areas touched:** Admin price/discount editing (L) — contained, single internal consumer

### Description

Extend the existing JSON/fetch `/admin/prices/update` endpoint to accept an optional `discount_per_liter` field alongside `price`, validating both before writing either (all-or-nothing per fuel type). This replaces the need for the separate form-POST `/admin/discounts/update` endpoint from the UI's perspective (that route is left in place, unused, per ARCH Open Questions).

### Test Plan

#### Test File(s)
- `tests/test_admin_pricing_endpoints.py` (extend existing `FakePriceStore`/`FakeDiscountStore` and `client`/`_login` fixtures)

#### Test Scenarios

##### Combined save — happy path

- **saves both price and discount** — GIVEN a valid price and a valid discount (0-15 range) WHEN POSTed together to `/admin/prices/update` THEN both `price_store.set_price` and `discount_store.set` are called, and the response is `{ok: true, price_php_per_liter, discount_per_liter, updated_at}` _(verifies R12)_

##### All-or-nothing validation

- **rejects invalid discount, saves neither** — GIVEN a valid price and a discount > 15 WHEN POSTed THEN neither `set_price` nor `discount_store.set` is called, and the response is `{ok: false, error, field: "discount"}` _(verifies R15)_
- **rejects invalid price, saves neither** — GIVEN price ≤ 0 or > 200 and a valid discount WHEN POSTed THEN neither store call happens, response is `{ok: false, error, field: "price"}` _(verifies R15)_

##### Regression guard

- **auth guard unchanged** — GIVEN no admin session WHEN POSTed THEN 403, identical to today's behavior _(guards backward-regression risk)_
- **price-only payload (backward compat)** — GIVEN a payload with only `price` (no `discount_per_liter`) WHEN POSTed THEN price still saves as it does today, discount is left untouched _(regression guard — in case any caller still sends price-only during rollout)_

### Implementation Notes

- **Module(s):** `main.py`
- **Pattern reference:** the endpoint's own existing structure (~lines 1425-1461); `admin_discounts_update()`'s validation bounds (0-15 range check) to replicate here rather than duplicate logic in `discount_store`
- **Key decisions:** ARCH A3 (extend existing endpoint, don't build a new one), A4 (validate both before writing either)
- **Libraries:** none new
- **High-risk callouts:** none M/H — this endpoint's risk is scoped L per ARCH

### Scope Boundaries

- Do NOT delete the `/admin/discounts/update` route (ARCH Open Questions — leave in place, unused)
- Do NOT add conflict detection/locking for concurrent saves (REQ out of scope, decision #6)
- Do NOT change `price_store.set_price` or `discount_store.set` internals — call both, don't modify their bounds/logic

### Files Expected

**Modified files:**
- `main.py` — `admin_prices_update()` function only

**Must NOT modify:**
- `main.py` — `admin_discounts_update()` (left in place per Open Questions, not deleted)
- `price_store.py`, `discount_store.py` — called as-is, no internal changes

---

## Task T5: Admin prices table UI — consolidated save + sticky header

> **Status:** not started
> **Verification:** ui
> **Effort:** s
> **Priority:** high
> **Depends on:** T4
> **Satisfies REQs:** R11, R13, R14
> **Footprint slice:** Modified: `templates/admin_prices.html`; Deleted: the per-discount `<form>` block (lines 103-114)
> **High-risk areas touched:** Admin price/discount editing (L)

### Description

Consolidate the 6 save buttons (2 per fuel type) into 3 (1 per fuel type), each posting both price and discount to T4's extended endpoint via `fetch` (no page reload), with an inline success indicator. Add a sticky header via a scroll-container wrapper so the header stays visible while scrolling the table body.

### Verification Checklist

- [ ] Each station row shows exactly 3 Save buttons (one per fuel type: Biodiesel, Premium, Unleaded), each next to both a price input and a discount input — expected: 3 buttons/row, down from 6
- [ ] Clicking Save with valid price+discount: no page navigation, no scroll-position change, an inline success indicator (checkmark/highlight) appears near the button and clears after a short delay — expected: matches R13/R14
- [ ] Clicking Save with an invalid discount (>15): inline error appears on the discount input, price is NOT saved (verify via reload that price value is unchanged) — expected: matches R15 (already covered server-side in T4; here confirming the UI surfaces it correctly)
- [ ] Scrolling the table body vertically: header row (Brand/Station/Location/fuel-type columns) stays visible and column-aligned at all scroll positions — expected: matches R11
- [ ] Existing stale-badge marking (`markCellStaleIfNeeded`) and toast behavior still function after the button consolidation — expected: no regression (guards backward-regression risk for this file's existing JS)
- [ ] Existing inactive-row styling (`tr.inactive-row`) still renders correctly — expected: no regression

#### Testable Seams
- Render: page loads with 3 buttons/row, correct initial price/discount values populated
- Conditional states: success indicator show/hide, error indicator show/hide
- Handlers: consolidated save button's fetch call, sticky-header CSS applied via the new wrapper

### Implementation Notes

- **Module(s):** `templates/admin_prices.html` (HTML + inline `<style>` + inline `<script>`)
- **Pattern reference:** the existing `.save-price-btn` fetch/toast/stale-marking JS (~lines 199-232) is the scaffold to extend, not replace
- **Key decisions:** ARCH A3 (reuse existing fetch scaffolding), A6 (CSS `position: sticky` + new scroll-container wrapper `<div>`, per REQ's own flagged sticky-without-scroll-container pitfall)
- **Libraries:** none new — vanilla JS, consistent with the rest of this file
- **High-risk callouts:** none M/H

### Scope Boundaries

- Do NOT add conflict detection/locking UI (REQ out of scope)
- Do NOT change the stale-badge (7-day) logic — only the save-button/header structure around it
- Only this one template file

### Files Expected

**Modified files:**
- `templates/admin_prices.html` — merge discount input into price cell/button, wrap table in scroll container with sticky thead, update JS to post both fields

**Must NOT modify:**
- `main.py` (T4's contract is a dependency, not something this task changes)

---

## Task T6: Calculator UI — relabel, invert display logic

> **Status:** not started
> **Verification:** ui
> **Effort:** m
> **Priority:** high
> **Depends on:** T2
> **Satisfies REQs:** R4, R8, R9, R10
> **Footprint slice:** Modified: `templates/book.html`
> **High-risk areas touched:** Booking money flow (H) — this is the customer-facing surface of T2's backend change; a mismatch between this UI's math and T2's server math would silently mislead customers about what they'll pay

### Description

Relabel the input field, rewrite the client-side `updatePreview()` JS to subtract the discount from the entered total (mirroring T2's server formula exactly), reorder/relabel the summary panel to match image-6, remove the "How Discounts Work" info box, and add client-side validation that blocks/errors when computed pay would be ≤ 0. Sequenced after T2 so the reject-path is a real end-to-end guarantee, not just cosmetic.

### Verification Checklist

- [ ] Input label reads "Prepaid Fuel Amount" → "Total Fuel Amount (in PHP)", no "[Discount Applied Later]" suffix — expected: matches R4
- [ ] Entering ₱1,000 at EcoOil-Cainta rates (₱76.03/L, discount yielding ₱72.34) reproduces image-6 exactly: Estimated volume ~13.2L, You save (discount) −₱72.34, You pay (prepaid) ₱927.66 — expected: matches R5-R8 acceptance criteria numbers
- [ ] Summary panel row order/labels match image-6: Station, Live fuel price, Estimated volume, Fuel voucher total, You save (discount), You pay (prepaid) — expected: matches R8
- [ ] "How Discounts Work" `<details>` section is absent from the rendered page — expected: matches R9
- [ ] Entering a total where discount ≥ total: inline error shown, summary panel hidden/not populated with a ≤₱0 value — expected: matches R10, mirrors T2's server-side rejection
- [ ] Switching Fuel Type and Station still repopulates the station dropdown and recalculates the preview correctly — expected: no regression in existing `populateStations()`/`updatePreview()` wiring
- [ ] Existing fuel-type reference tables (`.fuel-table-group` details) still render and remain independently collapsible — expected: no regression

#### Testable Seams
- Render: initial hidden state of `#cost_preview` before a station/amount is chosen
- Conditional states: preview shown/hidden, error shown/hidden
- Handlers: `updatePreview()` recalculation on input/station/fuel-type change

### Implementation Notes

- **Module(s):** `templates/book.html` (inline `<script>` around `updatePreview()`, ~lines 542-582)
- **Pattern reference:** the existing `updatePreview()` function itself — same variables (`liters`, `discount`, `pricing` lookup), inverted final arithmetic
- **Key decisions:** ARCH's calculator formula (T2's server-side mirror) — this task's JS must compute identically: `liters = enteredValue / price`, `discount = liters * dpl`, `pay = enteredValue - discount`
- **Libraries:** none new — vanilla JS, no test harness exists for it in this repo (hence `ui` mode, not `tdd`)
- **High-risk callouts:** H-risk per Areas of Impact — a client-side formula that disagrees with T2's server formula would show customers a number they don't actually get charged. Manually cross-check against T2's test fixture numbers (₱1000 total → ₱927.66 pay) during verification.

### Scope Boundaries

- Do NOT change discount tiers/rates or the fuel-type reference tables' data (REQ out of scope)
- Do NOT touch the driver/vehicle or station-selection form sections below the calculator
- Only the calculator input, its label, the summary panel, and the "How Discounts Work" block

### Files Expected

**Modified files:**
- `templates/book.html` — input label, `updatePreview()` JS, summary panel markup, removal of "How Discounts Work" block

**Must NOT modify:**
- `main.py` (T2 already owns the server-side contract this UI must match)

---

## Task T7: Booking-success payment panel — drop PESONet, add InstaPay QR

> **Status:** not started
> **Verification:** test-after
> **Effort:** xs
> **Priority:** medium
> **Depends on:** None
> **Satisfies REQs:** R1, R2, R3
> **Footprint slice:** Modified: `templates/booking_success.html`; New: static QR asset
> **High-risk areas touched:** None

### Description

Drop the "(InstaPay or PESONet)" parenthetical from the amount-due line and add the static InstaPay QR code + caption, using the `.payment-qr` CSS block already present but unused in this template. No backend change — this is a copy/asset swap.

### Test Plan

#### Test File(s)
- `tests/test_book_and_booking_success_copy.py` (extend existing booking-flow test)

#### Test Scenarios

##### Copy changes

- **PESONet removed** — GIVEN a completed booking WHEN `booking_success.html` renders THEN the response body does not contain "PESONet", and contains "Amount Due: ₱1" exactly (no parenthetical) _(verifies R1)_
- **QR present with caption** — GIVEN the same render THEN the response body contains a QR `<img>` tag referencing the static InstaPay QR asset and the caption "Scan with your banking app (InstaPay) to pay." _(verifies R2)_

##### Regression guard

- **no GoTyme regression** — GIVEN the same render THEN the existing negative assertions still hold: `"GoTyme" not in body`, `"payment_qr.png" not in body`, `'alt="UniFleet GoTyme payment QR code"' not in body` — this repo previously reverted a GoTyme QR attempt; this change must not reintroduce it _(guards backward-regression risk — pre-existing test already encodes this)_
- **unrelated payment-box content unchanged** — GIVEN the same render THEN "Send to INSTAPAY", "000228034271", and "UniFleet" (Account Name) are still present exactly as today _(regression guard)_

#### Verification Checklist (styling — R3)
- [ ] Visual diff of the rendered page shows only text and QR-image changes — expected: heading styles, bold "Account Name"/"Account Number" labels, box layout (`.payment-box`, `.amount-due`) are visually unchanged

### Implementation Notes

- **Module(s):** `templates/booking_success.html`
- **Pattern reference:** the `.payment-qr` CSS block (lines 102-119) already exists in this file's `<style>` unused — use it as-is, do not add new CSS; `static/UniFleet Logo.png` for how static assets are already referenced via `url_for('static', ...)`
- **Key decisions:** ARCH A7 (static asset, not generated at request time)
- **Libraries:** none new
- **High-risk callouts:** none

### Scope Boundaries

- Do NOT generate the QR dynamically (REQ out of scope — static only, per REQ decision #3)
- Do NOT touch the `due_amount` Python variable wiring (pre-existing dead variable, unrelated to this REQ — the displayed "₱1" is and remains hardcoded text)
- Do NOT modify `book.html` in this task (calculator changes are T6's scope)

### Files Expected

**New files:**
- `static/instapay_qr.png` (or `static/qr_codes/instapay_qr.png`) — static QR asset, referenced via `url_for('static', filename=...)`

**Modified files:**
- `templates/booking_success.html` — amount-due text, QR `<img>` + caption markup inside existing `.payment-qr` block

**Must NOT modify:**
- `main.py` `book()` handler's `due_amount`/`PAYMENT_INFO` wiring — out of scope, pre-existing

---

_Status values: `not started` (defined, not picked up) | `in progress` (implementation underway) | `done` (verification evidence produced) | `blocked` (cannot proceed — see notes)._
