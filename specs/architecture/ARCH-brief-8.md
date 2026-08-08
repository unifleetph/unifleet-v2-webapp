# Architecture: Book & Register Page Fixes (Brief-8)

> **Date:** 2026-08-09
> **Phase:** 2 of 5 (System Architecture)
> **Requirements source:** specs/requirements/REQ-brief-8.md
> **Type:** feature (batch of small fixes)

## Architecture Summary

Ten small, independent changes to `templates/book.html`, `templates/register.html`, and `templates/register_success.html`, plus one new persisted field. Nine of the ten are template/copy/attribute edits with matching server-side validation added to the existing `book()` and `register()` handlers in `main.py`, following the app's existing patterns exactly (native HTML5 `required`/`pattern` attributes, flash-message + re-render on server rejection). The one structural change is a new `mobile_number` column on the `vouchers` table, added the same way `fuel_type` and `requested_total_php` were added previously: append to `VOUCHER_COLUMNS` in `models.py` and add an `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `db/schema.sql` — both `persistence.py` (CSV) and `db/postgres_repo.py` (Postgres) derive their column sets from `VOUCHER_COLUMNS`, so no repo-layer code changes are needed. No new services, dependencies, or architectural patterns are introduced.

## High-Level Structure

No structural change to the app. All work lands inside the existing single-file Flask app (`main.py`) and its Jinja templates. Data flow for the two request paths touched:

```
POST /book  → main.py:book()      → validate (existing + new) → repo.create_unverified_booking(row)
                                                                    → persistence.py (CSV) or db/postgres_repo.py (Postgres)
                                                                    → both keyed off models.VOUCHER_COLUMNS

POST /register → main.py:register() → validate (new) → repo.create_customer_if_absent(new_row)
                                                            → customers table / customers.csv (contact_number column, unchanged)
```

`mobile_number` is booking-scoped (new column on `vouchers`), not customer-scoped — it's captured fresh on every `/book` submission, independent of the customer's `contact_number` on the `customers` table.

## Tech Choices

| Area | Decision | Alternatives Considered | Rationale |
|---|---|---|---|
| Client-side validation | Native HTML5 `required`/`pattern`/`min` attributes | JS validation library/framework | Matches 100% of existing form validation in this app (`book.html`, `register.html` have zero custom JS validation today) — no new dependency |
| Server-side validation | Inline checks in `main.py`'s existing route handlers, reusing `_reject_booking()` for `/book` and adding an equivalent flash+re-render path for `/register` | Extract a shared validation module | App is intentionally single-file (`main.py`, ~1200 lines, no blueprints per AGENTS.md); introducing a validation module for 4 new checks is disproportionate |
| New booking field storage | New `mobile_number` column on `vouchers`, added via `VOUCHER_COLUMNS` + `ALTER TABLE IF NOT EXISTS` | Store combined with existing `contact_number`; new side table | Follows the exact precedent already in `db/schema.sql` for `fuel_type`/`requested_total_php`; a side table is unwarranted for one scalar field |
| Country-code selector | Plain `<select>` with a single `+63` option | Third-party intl-tel-input JS library | Scope is Philippines-only per REQ Decision #6; a library would add a new frontend dependency for zero present benefit |
| QR caption fix | Edit the static image asset (`static/instapay_qr.png`) directly | Template/CSS change | Root cause is text baked into the PNG itself, not template markup — confirmed no second caption exists in code |

## Patterns & Conventions

- **`_reject_booking()` flash+re-render pattern** (`main.py:874`) — every new `/book` validation failure (new-driver required fields, wheels≥2, mobile_number) reuses this existing helper instead of duplicating `render_template` calls.
- **`VOUCHER_COLUMNS`-driven schema** (`models.py:11`) — the single source of truth for what a booking row contains; both CSV and Postgres repos derive their column sets from it. Adding `mobile_number` here is sufficient for both backends to persist it.
- **`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`** (`db/schema.sql`) — idempotent, forward-only schema evolution already used twice in this file; the new column follows the same block style with a comment explaining why it's nullable.
- **Not applying:** no new abstraction, module, or shared validation layer — nine of ten items are template-local edits; the tenth (new column) is additive and mechanical.

## Data Models

### `vouchers` (existing table, one new column)

**Purpose:** one row per booking/refuel request.

**New field:**
| Field | Type / Constraint | Notes |
|---|---|---|
| `mobile_number` | `VARCHAR(20)`, nullable | Digits only, leading zero preserved (e.g. `"09123456789"`). Nullable so historical rows don't need backfill (same pattern as `fuel_type`). Required at the application layer for new submissions (R9), not enforced at the DB layer — matches how other app-required fields (`driver_name`, `vehicle_plate`, etc.) are handled today (also nullable columns, required in `main.py`). |

**Lifecycle:** set once at booking creation (`create_unverified_booking`), never updated afterward — same lifecycle as `driver_name`/`vehicle_plate`.

No changes to `customers` table — `contact_number` keeps its existing type/constraints; only its client/server-side validation strictness changes (still stored as-is, per confirmed decision).

## API Contracts / Interfaces

No HTTP contract changes (same routes, same methods, same success/redirect behavior). Only the request-body validation surface changes:

### `POST /book`

| Change | New rejection case | Response |
|---|---|---|
| R6/R7 | `driver_mode=new` and any of `driver_name`, `vehicle_plate`, `truck_make`, `truck_model`, `number_of_wheels` empty, or `number_of_wheels < 2` | Flash error, re-render `book.html` with submitted values retained (existing `_reject_booking` contract) |
| R9/R10 | `mobile_number` empty or fewer than 10 digits | Same as above |

### `POST /register`

| Change | New rejection case | Response |
|---|---|---|
| R9/R10 | `contact_number` fewer than 10 digits (after stripping non-digits for the count only — stored value unchanged) | Flash error, re-render `register.html` with submitted values retained (**new** path — `register()` has no validation/reject branch today) |

## Module Boundaries

No new modules. Existing boundary respected: `main.py` (routes + validation) → `persistence.py` / `db/postgres_repo.py` (storage, selected via `repo` at startup) → `models.py` (shared column schema). Template files own presentation only; no logic moves into them beyond the existing toggle-visibility JS in `book.html`.

## Change Footprint

### New files / modules

None. `static/instapay_qr.png` is edited in place (asset crop), not replaced with a new path.

### Modified files / modules

| Path | What changes here |
|---|---|
| `templates/book.html` | R1: remove `{{ customer.company_name }}` from the Welcome line (line 327). R3: add `<p>Malaking tipid. Mas mahabang biyahe.</p>` directly above the existing "Big savings. Longer trips." paragraph (line 312), no other layout change. R4: add `target="_blank" rel="noopener"` to the Terms & Conditions `<a>` (line 499). R6/R7: add `required` to the 5 new-driver inputs (lines 481–494) and `min="2"` to `number_of_wheels`. R9: add a new Mobile Number field (country-code `<select>` defaulting to `+63`, plus `<input type="tel" name="mobile_number" required pattern="[0-9]{10,}">`) adjacent to the existing `contact_number` field (~line 329), which stays untouched |
| `templates/register.html` | R9: add a country-code `<select>` (single `+63` option) beside the existing `contact_number` input (line 144–145); add `pattern`/client-side digit-count hint to the existing input — no new form field, no schema change |
| `templates/register_success.html` | R2: insert `<p class="code-reminder">Please Screenshot &amp; Save this CODE!</p>` between the page title and the "Thank you for registering..." paragraph, with a new scoped CSS rule matching the highlighted-callout style in the screenshot. R8: adjust this file's own `<style>` block (`.centered-content` padding, heading/body spacing) for cleaner mobile presentation — scoped to this template only |
| `static/instapay_qr.png` | R5: crop the baked-in "Scan with your banking app (InstaPay) to pay." caption text from the bottom of the image, keep the QR code + InstaPay logo artwork only. The real caption (`templates/booking_success.html:149`, `.qr-caption` `<p>`) is unchanged |
| `models.py` | R9: append `"mobile_number"` to `VOUCHER_COLUMNS` (line ~30, alongside the other booking-time fields) |
| `db/schema.sql` | R9: add `ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS mobile_number VARCHAR(20);` after the existing `fuel_type`/`requested_total_php` ALTER blocks, with a comment following their style |
| `main.py` | R6/R7: in `book()`'s `use_new` branch (~line 955), validate the 5 fields non-empty and `number_of_wheels >= 2` before proceeding; reject via `_reject_booking()`. R9/R10: validate `mobile_number` (required, ≥10 digits after stripping non-digit characters for the check) before building `row`; add `'mobile_number': request.form.get('mobile_number')` to the `row` dict (~line 1093). In `register()` (line 651): add digit-count validation for `contact_number` and a new flash+re-render rejection path (doesn't exist today) |

### Deleted / replaced

None.

### Touched but not changed (silent-regression hotspots)

| Path | Why it matters |
|---|---|
| `persistence.py` (`_ensure_cols`, `_read`, `_write`) | Already derives its column set from `VOUCHER_COLUMNS` — will pick up `mobile_number` automatically (blank-defaults for pre-existing CSV rows) with no edit needed, but this is the mechanism the new column depends on |
| `db/postgres_repo.py` (`_VOUCHER_INSERT_COLUMNS`, `append_vouchers`) | Same — automatically includes `mobile_number` in the INSERT once it's in `VOUCHER_COLUMNS`. No edit needed, but confirms the column addition is sufficient without touching this file |
| `main.py:389` `_EXPORT_COLUMNS = VOUCHER_COLUMNS + [...]` | Admin CSV exports automatically gain a `mobile_number` column — additive, but changes the shape of the exported CSV that the UniFleet ops team consumes |
| `tests/test_book_pg.py` (~10 POST fixtures) | Fixtures don't include `mobile_number`; will fail against the new required-field validation until updated — deferred to implementation (confirmed, not a design gap) |
| `tests/test_schema.py:201-206` | Already asserts every `VOUCHER_COLUMNS` entry has a matching Postgres column — will correctly fail if the `ALTER TABLE` step is skipped, acting as a safety net |
| `tests/test_register_pg.py`, `tests/test_register_optional_labels.py` | Use `"0900-000-0000"` as `contact_number` (11 digits after stripping) — passes the new ≥10-digit check with no fixture change expected, since stripping is for the check only and storage stays raw |

## Areas of Impact

| Area | Impact | Risk (L/M/H) | Why |
|---|---|---|---|
| `/book` new-driver submission flow | New required-field + min-wheels validation | Low | Additive validation, existing reject/re-render pattern reused |
| `/book` booking data shape | New `mobile_number` column | Low | Nullable, additive; no consumer assumes a fixed/closed column set |
| `/register` submission flow | First-ever server-side validation on this route | Medium | `register()` currently has zero POST validation; adding a reject path is new code, not just a new check — higher chance of a subtle bug (e.g. wrong redirect, lost form state) than a one-line validation add |
| Existing booking tests (`test_book_pg.py`) | ~10 fixtures need `mobile_number` added | Medium | Known, deferred to implementation per confirmed decision |
| Ops CSV exports / supplier consumers | New `mobile_number` column appears | Low | Additive; no fixed-column-count assumption found in `main.py` export code |
| `static/instapay_qr.png` | Visual asset change | Low | Isolated image edit; only one template references this file |

**Contract changes:** the admin CSV export (`main.py:389` `_EXPORT_COLUMNS`) gains one new column, `mobile_number`. No breaking change (additive), but the UniFleet ops team's downstream CSV consumers will see a new field.

**Cross-cutting ripples:** `db/schema.sql`'s new `ALTER TABLE` runs automatically on every Railway deploy (Dockerfile CMD chains `db/apply.py` → gunicorn) — no manual migration step, consistent with existing rollout mechanics.

## Cross-Cutting Concerns

- **Errors:** all new validation failures surface via the existing `flash(message, "error")` + re-render pattern — no new error-handling mechanism. `/register` gains this pattern for the first time (previously had none), scoped narrowly to the two new checks.
- **Logging & metrics:** no new logging added; matches existing app behavior (only exceptions are printed today, not validation rejections).
- **Auth / authz:** unaffected — both routes remain public/customer-facing, no auth boundary crossed.
- **Performance:** negligible — one extra nullable VARCHAR column, no new queries or joins.
- **Security:** `mobile_number` is stripped to digits-only server-side before storage (defense against injection-style input in a field that flows into CSV exports and internal copy/paste workflows). No secrets involved.
- **Migrations / rollout:** `ALTER TABLE IF NOT EXISTS` is idempotent and auto-applied on deploy; no backward-compat concern since the column is nullable and no existing code reads it yet.

## Architecture Decisions Log

| # | Decision | Alternatives | Chosen Because | Satisfies REQs |
|---|---|---|---|---|
| A1 | New `mobile_number` column on `vouchers`, added via `VOUCHER_COLUMNS` + `ALTER TABLE IF NOT EXISTS` | Side table; merge into `contact_number` | Matches existing precedent exactly (`fuel_type`, `requested_total_php`); both repo layers already derive from `VOUCHER_COLUMNS`, zero repo-code changes needed | R9, R10 |
| A2 | Store `mobile_number` as digits-only local format (leading zero preserved), no separate country-code column | Store merged E.164 (`+639...`); add a `mobile_country_code` column | REQ Decision #5 (leading zero preserved) + single-country scope today makes a second column premature | R9, R10 |
| A3 | `/register`'s existing `contact_number` gets validation only (digit-count check), no stripping/rewrite of the stored value | Apply the same digit-stripping used on the new `mobile_number` field | Confirmed: narrower blast radius, avoids changing behavior of a field that predates this REQ and has existing consumers | R9, R10 |
| A4 | R5 fix is an image-asset crop (`static/instapay_qr.png`), not a template/CSS change | Add/remove a `<p>` element in `booking_success.html` | Confirmed the "small caption" is text baked into the PNG itself — the real, only `<p class="qr-caption">` in code is the "big" caption and is already correctly placed | R5 |
| A5 | New-driver `required` attributes rely on the existing `display:none` toggle to avoid blocking submission in preset mode, no extra JS | Add JS to dynamically set/unset `required` on mode change | Per HTML5 spec, `display:none` form controls are barred from constraint validation — verified safe, avoids unnecessary JS | R6, R7 |
| A6 | Country-code selector is a single-option `<select>` (`+63`), no JS library | Integrate intl-tel-input or similar | Scope is Philippines-only per REQ; a library adds a new frontend dependency for no present benefit | R9 |

## Risk & Stress-Test Scenarios

### Forward — runtime failure scenarios

| Scenario | How the Design Handles It |
|---|---|
| Booking submitted for a pre-existing customer whose past bookings have no `mobile_number` | Column is nullable; historical rows read back blank/NULL, no crash — same as `fuel_type`'s NULL-backfill precedent |
| User disables JS / bypasses browser validation and POSTs directly | Server-side checks in `main.py` (R6/R7/R9/R10) reject independently of client-side `required`/`pattern` attributes |
| Concurrent deploys both running `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` | Idempotent by construction, safe under Railway's deploy mechanics |
| Rollback after this batch ships and something's wrong | New column is nullable and additive — can stay in place unused with zero impact; no destructive rollback required. Template/validation changes are simple reverts |

### Backward — regression risk per touched area

| Touched area | What could regress | How we'd know / mitigation |
|---|---|---|
| `tests/test_book_pg.py` fixtures | Existing booking-creation tests fail once `mobile_number` becomes required | Run `make test-db` after implementation; update fixtures to include a valid `mobile_number` (planned, not a gap) |
| `register()`'s new reject path | First validation logic ever added to this route — risk of losing submitted form values or wrong flash category on error | Mirror `_reject_booking()`'s pattern (retain `form_values`, `flash(..., "error")`) exactly; add a task-level test posting an invalid `contact_number` and asserting the form re-renders with prior values intact |
| `main.py:389` `_EXPORT_COLUMNS` | Ops team's CSV tooling assumes a fixed column layout downstream (outside this codebase) | Out of this team's control to verify; flag in Open Questions |
| `static/instapay_qr.png` crop | Visual regression if crop is imprecise (cuts into QR code itself) | Visual review of the cropped asset before merge; QR must remain scannable |

## Open Questions

- Does any downstream/ops tooling outside this repo (e.g. a spreadsheet import) assume a fixed column count or order in the admin CSV export?
  - **Impact if unresolved:** an additive new column could still break a brittle external import script.
  - **Suggested default:** proceed — the change is additive and matches how `fuel_type` was added previously without incident; flag to the ops team informally when this ships.
- Exact padding/margin values for R8's mobile cleanup are intentionally undefined (REQ Decision #7).
  - **Impact if unresolved:** "done" for R8 is judged by screenshot review, not a fixed spec.
  - **Suggested default:** reuse the spacing scale already present in `static/styles.css`'s `.mobile-page` media query (lines 90–150) rather than inventing new values.

## Out of Scope

- Backfilling `mobile_number` on historical booking rows (reason: REQ explicitly scopes this to new submissions only).
- Any country code beyond `+63` (reason: REQ scope is Philippines-only; selector is structured to allow adding more later without a redesign).
- Restructuring the existing `/book` free-text "Name & Mobile Number" field or `/register`'s `contact_number` storage format (reason: REQ Decision #3 and Decision #A3 above — explicitly left untouched).

---

# Tasks

## Task T1: Copy, layout, and asset fixes (R1, R2, R3, R4, R5, R8)

> **Status:** done
> **Verification:** ui
> **Effort:** s
> **Priority:** medium
> **Depends on:** None
> **Satisfies REQs:** R1, R2, R3, R4, R5, R8
> **Footprint slice:** Modified: `templates/book.html` (Welcome text, Tagalog tagline, Terms link target — no form/validation lines touched); `templates/register_success.html` (code-reminder text + mobile spacing); Modified (asset): `static/instapay_qr.png`
> **High-risk areas touched:** None (all Low risk per ARCH Areas of Impact)

### Description

Six independent, low-risk copy/layout/asset edits with no business logic: drop the broken "Welcome, None" personalization, add a code-reminder line on the registration success page, add a Tagalog tagline on `/book`, make the Terms link open in a new tab, crop the caption baked into the InstaPay QR image, and clean up mobile spacing on the registration success page. None of these touch form submission or validation — safe to do as one visual-verification pass.

### Verification Checklist

- **R1 — Welcome text**: load `/book` with a valid `account_code` for a customer (any `company_name` value, including blank) — expected: page shows "Welcome" only, no ", None" or ", <name>" suffix ever appears.
- **R2 — code reminder**: load `/register/success?account_code=XXXX` — expected: "Please Screenshot & Save this CODE!" renders between the "Registration Complete" heading and the "Thank you for registering..." paragraph, styled as a highlighted callout matching `docs/image-13.png`.
- **R3 — Tagalog tagline**: load `/book` with no `account_code` submitted (pre-registration view) — expected: "Malaking tipid. Mas mahabang biyahe." renders directly above the existing "Big savings. Longer trips." line; diff review confirms no other layout/markup change on the page.
- **R4 — Terms link**: on `/book`'s customer form, click "Terms & Conditions" — expected: opens `/terms` in a new tab (`target="_blank" rel="noopener"`), current tab and any in-progress form input untouched.
- **R5 — QR crop**: inspect `static/instapay_qr.png` — expected: no caption text baked into the bottom of the image; QR code + InstaPay logo art remain intact and scannable; `templates/booking_success.html:149`'s `<p class="qr-caption">` element is unchanged (still present, still the only in-code caption).
- **R8 — mobile spacing**: load `/register/success` at a 375px viewport, before/after screenshot comparison — expected: header/card padding and heading-to-body spacing read as visually consistent and uncramped (subjective, per REQ Decision #7 — no fixed numeric spec).

### Implementation Notes

- **Module(s):** template layer only (`templates/`), one static asset — no `main.py` or repo-layer involvement.
- **Pattern reference:** `templates/register_success.html`'s existing `.account-code` block for the highlighted-callout style pattern (R2); `static/styles.css`'s `.mobile-page` media query (lines 90–150) as the spacing scale to reuse for R8 (ARCH Open Questions suggested default).
- **Key decisions:** ARCH A4 — R5 is confirmed to be an image-asset crop, not a template/CSS change; do not add or remove any `<p>` element in `booking_success.html`.
- **Libraries:** none new. Image crop can use Pillow (already a dependency, used elsewhere in the app for report/voucher generation).
- **High-risk callouts:** none.

### Scope Boundaries

- Do NOT touch `templates/book.html`'s form fields, `required` attributes, or any validation-adjacent markup — that's T2/T4.
- Do NOT restructure `register.html` or `booking_success.html` — R8 is scoped to `register_success.html` only; R5 is scoped to the image asset only.
- Do NOT invent a fixed padding/margin spec for R8 beyond what reads clean at 375px — this is a judgment call, not a pixel target.

### Files Expected

**Modified files:**
- `templates/book.html` (Welcome line ~327; new tagline line above ~312; Terms link `target`/`rel` ~499 — only these three spots)
- `templates/register_success.html` (new code-reminder `<p>` + CSS rule; spacing adjustments in its own `<style>` block)
- `static/instapay_qr.png` (cropped)

**Must NOT modify:**
- `templates/booking_success.html` (silent-regression hotspot — R5's real caption `<p>` must stay exactly as-is; no test needed beyond the visual check above since no code changes here)
- `templates/register.html` (out of scope for T1 — its `contact_number` field is T4's concern)

---

## Task T2: New Driver required fields + wheels minimum (R6, R7)

> **Status:** done
> **Verification:** test-after
> **Effort:** s
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** R6, R7
> **Footprint slice:** Modified: `templates/book.html` (add `required` to 5 new-driver inputs, `min="2"` on `number_of_wheels`); Modified: `main.py` (`book()`'s `use_new` branch — server-side validation before building `driver_data`)
> **High-risk areas touched:** None (Low risk per ARCH Areas of Impact — additive validation reusing the existing `_reject_booking()` pattern)

### Description

Makes all 5 "New Driver" fields on `/book` mandatory (Driver Full Name, Number of Wheels, Vehicle Make, Vehicle Model, Plate Number), with Number of Wheels additionally enforcing a minimum of 2. Enforced both client-side (HTML5 `required`/`min`) and server-side (rejecting the submission via the existing `_reject_booking()` flash+re-render pattern), so bypassing the browser doesn't bypass validation.

### Test Plan

#### Test File(s)
- `tests/test_book_pg.py` (existing conventions — POST fixtures against `/book`)

#### Test Scenarios

##### New Driver Required Fields

- **rejects missing driver_name** — GIVEN `driver_mode=new` and `driver_name` empty (other 4 fields valid) WHEN POST `/book` THEN response is rejected with a flash error and the form re-renders with submitted values retained _(verifies R6)_
- **rejects missing vehicle_plate / truck_make / truck_model / number_of_wheels** — GIVEN `driver_mode=new` and each field empty in turn WHEN POST `/book` THEN rejected the same way _(verifies R6)_

##### Wheels Minimum

- **rejects wheels below minimum** — GIVEN `driver_mode=new`, all fields valid, `number_of_wheels=0` or `1` WHEN POST `/book` THEN rejected _(verifies R7, REQ edge case)_
- **accepts wheels at minimum** — GIVEN `driver_mode=new`, all 5 fields valid, `number_of_wheels=2` WHEN POST `/book` THEN booking is created successfully _(verifies R6, R7 happy path)_

##### Regression Guard

- **preset mode unaffected by new-driver required attrs** — GIVEN `driver_mode=preset` with a valid `driver_select` and the (hidden) new-driver fields left blank WHEN POST `/book` THEN the booking is still accepted, unaffected by the new `required`/`min` attributes on the hidden fields _(guards backward-regression risk: `display:none` constraint-validation exemption per ARCH A5)_

##### Client-Side Attributes

- **new-driver inputs render as required** — GIVEN the rendered `/book` template WHEN the 5 new-driver `<input>` elements are inspected THEN each has a `required` attribute and `number_of_wheels` has `min="2"` _(verifies R6, R7 client-side)_

### Implementation Notes

- **Module(s):** `main.py`'s `book()` route handler; `templates/book.html`'s `#new_driver_fields` block.
- **Pattern reference:** `main.py:874` `_reject_booking()` — reuse directly, do not duplicate the flash+render_template call.
- **Key decisions:** ARCH A5 — `display:none` form controls are exempt from HTML5 constraint validation (verified per spec), so no extra JS is needed to toggle `required` on/off between preset/new modes.
- **Libraries:** none.
- **High-risk callouts:** none — this is additive validation on an existing, well-understood code path.

### Scope Boundaries

- Do NOT add JS-based dynamic `required` toggling — the `display:none` exemption already handles this per ARCH A5.
- Do NOT touch the `driver_select` preset path's own validation (`main.py:946-947`) beyond what's already there.
- Only implement the 5-field-required + wheels≥2 checks — no broader New Driver form redesign.

### Files Expected

**Modified files:**
- `templates/book.html` (`required` on `driver_name`, `vehicle_plate`, `truck_make`, `truck_model`, `number_of_wheels`; `min="2"` on `number_of_wheels` — lines ~481–494)
- `main.py` (validation block added in `book()`'s `use_new` branch, ~line 955, before `driver_data` is built)

**Must NOT modify:**
- `tests/test_book_pg.py`'s existing preset-mode fixtures beyond what's needed for the regression-guard scenario above — don't touch unrelated tests in this file (T4 separately updates the ~10 fixtures that need `mobile_number` added)

---

## Task T3: `mobile_number` column on `vouchers` (R9 storage)

> **Status:** done
> **Verification:** checklist
> **Effort:** xs
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** R9 (storage prerequisite for R9/R10 — see T4)
> **Footprint slice:** Modified: `models.py` (`VOUCHER_COLUMNS`); Modified: `db/schema.sql` (`ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS mobile_number`)
> **High-risk areas touched:** None (Low risk — nullable, additive column; `persistence.py` and `db/postgres_repo.py` derive their column sets from `VOUCHER_COLUMNS` automatically, per ARCH A1)

### Description

Adds the `mobile_number` column that T4's validation logic will write to. Follows the exact precedent already in `db/schema.sql` for `fuel_type` and `requested_total_php`: append the column name to `VOUCHER_COLUMNS` in `models.py`, add a matching idempotent `ALTER TABLE` in `db/schema.sql`. No repo-layer code changes needed — both CSV (`persistence.py`) and Postgres (`db/postgres_repo.py`) paths derive their column sets from `VOUCHER_COLUMNS` dynamically.

### Verification Checklist

- **`VOUCHER_COLUMNS` updated** — inspect `models.py` — expected: `"mobile_number"` present in the list (grouped with the other booking-time fields, e.g. near `driver_name`/`vehicle_plate`).
- **schema.sql updated** — inspect `db/schema.sql` — expected: `ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS mobile_number VARCHAR(20);` present, with a comment following the style of the existing `fuel_type`/`requested_total_php` ALTER blocks explaining why it's nullable.
- **schema applies cleanly** — run `python db/apply.py db/schema.sql db/seed_stations.sql db/seed_prices.sql` against local Postgres — expected: applies with no errors; `psql` `\d vouchers` shows `mobile_number` as `character varying(20)`, nullable.
- **schema test passes** — run `pytest tests/test_schema.py -v` — expected: passes (this test already asserts every `VOUCHER_COLUMNS` entry has a matching Postgres column — see `tests/test_schema.py:201-206`).
- **repo layers unaffected** — run `pytest tests/test_postgres_repo.py tests/test_persistence.py -v` — expected: no new failures, confirming both repos pick up the column automatically with zero code changes (per ARCH A1).

### Implementation Notes

- **Module(s):** `models.py`, `db/schema.sql` only — no `main.py`/repo-layer touches in this task.
- **Pattern reference:** `db/schema.sql`'s existing `ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS fuel_type VARCHAR(30);` and `requested_total_php` blocks (with their explanatory comments) — copy the style exactly.
- **Key decisions:** ARCH A1 (new column via `VOUCHER_COLUMNS` + `ALTER TABLE`, not a side table) and ARCH A2 (digits-only local format, no separate country-code column).
- **Libraries:** none.
- **High-risk callouts:** none.

### Scope Boundaries

- Do NOT write to `mobile_number` anywhere yet — that's T4's job. This task only makes the column exist and confirms both repo layers see it.
- Do NOT add a `mobile_country_code` column (ARCH A2 — explicitly rejected for now, single-country scope).
- Do NOT touch `customers` / `contact_number` — that column already exists, unrelated to this task.

### Files Expected

**Modified files:**
- `models.py` (`VOUCHER_COLUMNS` list — add `"mobile_number"`)
- `db/schema.sql` (new `ALTER TABLE ... ADD COLUMN IF NOT EXISTS mobile_number VARCHAR(20);` block)

**Must NOT modify:**
- `persistence.py` (silent-regression hotspot — must pick up the new column automatically via its existing `VOUCHER_COLUMNS`-driven `_ensure_cols`; covered by the "repo layers unaffected" checklist item, not a code change)
- `db/postgres_repo.py` (same — `_VOUCHER_INSERT_COLUMNS` is derived from `VOUCHER_COLUMNS`, no edit needed)

---

## Task T4: Mobile number field — UI + validation on `/book` and `/register` (R9, R10)

> **Status:** not started
> **Verification:** tdd
> **Effort:** m
> **Priority:** high
> **Depends on:** T3
> **Satisfies REQs:** R9, R10, N1
> **Footprint slice:** Modified: `templates/book.html` (new Mobile Number field + country-code selector, additive to the existing `contact_number` field); Modified: `templates/register.html` (country-code selector added beside existing `contact_number` field); Modified: `main.py` (`book()` — validate + store `mobile_number`; `register()` — new validation/reject path for `contact_number`)
> **High-risk areas touched:** `/register` submission flow (Medium risk per ARCH Areas of Impact — first-ever server-side validation on this route; existing "Touched but not changed" test files: `tests/test_book_pg.py` fixtures need `mobile_number` added)

### Description

Adds a new, required Mobile Number field with a country-code selector (defaulting to Philippines `+63`) to `/book`, storing a clean, digits-only value (leading zero preserved) in the new `mobile_number` column from T3. The existing free-text "Name & Mobile Number" field on `/book` stays completely untouched (ARCH Decision #3 / A3). `/register`'s existing `contact_number` field gets the same selector plus a digit-count validation (≥10 digits after stripping non-digits for the check only) — its stored value is NOT rewritten, since `register()` predates this REQ and has existing consumers (ARCH A3). Both routes get server-side enforcement, since `/register` currently has no POST validation at all — this task adds that reject path for the first time.

### Test Plan

#### Test File(s)
- `tests/test_book_pg.py` (existing conventions)
- `tests/test_register_pg.py` (existing conventions)

#### Test Scenarios

##### Book: Mobile Number Required & Validated

- **rejects missing mobile_number** — GIVEN a valid `/book` submission with `mobile_number` omitted WHEN POST `/book` THEN rejected with a flash error, form values retained _(verifies R9)_
- **rejects mobile_number under 10 digits** — GIVEN `mobile_number="091234"` WHEN POST `/book` THEN rejected _(verifies R10, REQ edge case)_
- **stores digits with leading zero preserved** — GIVEN `mobile_number="09123456789"` WHEN POST `/book` THEN booking created; stored `row.mobile_number == "09123456789"` _(verifies R9, R10)_
- **strips non-digit characters on store** — GIVEN `mobile_number="0912-345-6789"` WHEN POST `/book` THEN booking created; stored value is `"09123456789"` (dashes removed) _(verifies R10, NFR1 clean-data)_

##### Register: Contact Number Validation

- **rejects contact_number under 10 digits** — GIVEN `contact_number` with fewer than 10 digits WHEN POST `/register` THEN rejected with a flash error, form values retained _(verifies R9, R10 — new reject path)_
- **accepts existing 11-digit fixture unchanged** — GIVEN `contact_number="0900-000-0000"` (11 digits after stripping for the count) WHEN POST `/register` THEN accepted; stored `contact_number` is UNCHANGED (`"0900-000-0000"`, dashes kept — no stripping applied to this field) _(guards backward-regression risk per ARCH A3; verifies R9, R10)_

##### Client-Side Selector

- **country-code selector renders on both pages** — GIVEN the rendered `/book` and `/register` templates WHEN inspected THEN each shows a country-code `<select>` defaulting to `"+63"` _(verifies R9 client-side)_

##### Regression Guard

- **update existing test_book_pg.py fixtures** — GIVEN the ~10 existing POST fixtures in `tests/test_book_pg.py` that predate `mobile_number` WHEN updated to include a valid `mobile_number` value THEN all previously-passing tests still pass _(guards backward-regression risk for `tests/test_book_pg.py`, per ARCH)_

### Implementation Notes

- **Module(s):** `templates/book.html`, `templates/register.html`, `main.py`'s `book()` and `register()` handlers.
- **Pattern reference:** `main.py:874` `_reject_booking()` for `/book`; for `/register`, build an equivalent flash+`render_template('register.html', form_values=request.form)` path (doesn't exist today — this is new code, called out as the task's Medium-risk area).
- **Key decisions:** ARCH A2 (digits-only local format, no country-code column), ARCH A3 (`contact_number` validated but not rewritten — stripping applies only to the new `mobile_number` field), ARCH A6 (single-option `+63` `<select>`, no JS library).
- **Libraries:** none new.
- **High-risk callouts:** `/register`'s new reject path is the one genuinely new code pattern in this whole REQ (every other validation reuses `_reject_booking()`). Watch for: correct flash category (`"error"`), submitted values retained on re-render, and the redirect-on-success path (`/register/success?account_code=...`) staying unaffected for the happy path. The regression-guard scenario above (11-digit fixture unchanged) and the reject-path scenario both target this directly.

### Scope Boundaries

- Do NOT modify `/book`'s existing free-text `contact_number` field (its label, required state, or content) — it stays exactly as today (ARCH Decision #3).
- Do NOT strip/rewrite `/register`'s `contact_number` stored value — validation only (ARCH A3).
- Do NOT add support for any country code beyond `+63` (ARCH Out of Scope) — the `<select>` should be structured so adding more later doesn't require a redesign, but only one option ships now.
- Do NOT add a `mobile_country_code` column (T3/ARCH A2 already settled this).

### Files Expected

**Modified files:**
- `templates/book.html` (new Mobile Number field: country-code `<select>` + `<input type="tel" name="mobile_number" required pattern="[0-9]{10,}">`, adjacent to the existing `contact_number` field ~line 329)
- `templates/register.html` (country-code `<select>` added beside the existing `contact_number` input ~line 144–145)
- `main.py` (`book()`: validate + strip `mobile_number`, add to the `row` dict ~line 1093; `register()`: new digit-count validation + reject/re-render path for `contact_number`)
- `tests/test_book_pg.py` (existing fixtures updated to include `mobile_number`)

**Must NOT modify:**
- `templates/book.html`'s existing `contact_number` field/label (silent-regression hotspot — must remain byte-for-byte as today per ARCH Decision #3)
- `models.py`, `db/schema.sql` (owned by T3 — this task only writes to the column T3 creates)
- `main.py:389` `_EXPORT_COLUMNS` (already picks up `mobile_number` automatically — no edit needed; Low risk per ARCH Areas of Impact)

### TDD Sequence

1. `/book` `mobile_number` required + digit-count + leading-zero-preserved + strip-on-store (drives the validation helper's shape).
2. `/register` `contact_number` digit-count validation + new reject path (reuses the digit-count check from step 1, adds the new reject/re-render mechanics).
3. Country-code `<select>` markup on both templates (client-side only, no logic dependency).
4. Update `test_book_pg.py`'s existing fixtures last, once the required-field behavior is locked in.
