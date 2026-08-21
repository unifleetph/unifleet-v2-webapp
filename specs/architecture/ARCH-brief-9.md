# Architecture: Brief 9 — Customer Lookup Driver Info, QR Redeem Bug, PDF/Text Fixes, Map Link, Link Color Bug

> **Date:** 2026-08-13
> **Phase:** 2 of 5 (System Architecture)
> **Requirements source:** specs/requirements/REQ-brief-9.md
> **Type:** feature + bugfix (mixed batch)

## Architecture Summary

Six independent, small changes against the existing Flask single-file app (`main.py`), its Jinja templates, and `generate_voucher.py`. No new modules, no data model changes, no new dependencies. R1–R3 is a template-only addition (data already available on the voucher row). R4–R5 is a one-line default-value fix in `generate_voucher.py` plus a Railway env var correction (infra, outside this repo's code). R6–R8 are literal string edits in `report_pdf.py` and `register_success.html`. R9 adds a link snippet to `book.html`. R10 is a single shared-CSS rule addition in `static/styles.css` that already loads on 8 of 9 templates.

## High-Level Structure

No architectural shape change — this is six point-fixes layered onto the existing structure:

```
templates/admin_customer_lookup.html   → add 2 columns (R1-R3)
generate_voucher.py                     → fix BASE_URL fallback value (R4-R5)
[Railway staging env vars]              → set BASE_URL correctly (R4-R5, infra)
report_pdf.py                           → 2 string edits (R6-R7)
templates/register_success.html         → 1 string edit (R8)
templates/book.html                     → add linked text under map (R9)
static/styles.css                       → add a:link/:visited/:hover/:active rule (R10)
```

Data flow is unchanged in all cases — R1 surfaces existing fields already present in `repo.list_all_vouchers()` rows; nothing new is computed, stored, or queried.

## Tech Choices

No new technology. All changes use existing stack: Flask/Jinja templates, ReportLab-based `report_pdf.py`, plain CSS in `static/styles.css`, `qrcode` lib in `generate_voucher.py`.

| Area | Decision | Alternatives Considered | Rationale |
|------|----------|--------------------------|-----------|
| Link color fix scope | Add rule to `static/styles.css` (shared file) | Per-template inline style on `book.html` only | `styles.css` already loads on book.html, register.html, and 6 others — one shared rule is a true site-wide fix without touching every template |
| QR fallback fix | Update hardcoded fallback URL string in `generate_voucher.py` | Raise/fail at import if `BASE_URL` unset | User chose the lighter fix — keep silent-fallback pattern but point it at a live URL instead of a dead one |

## Patterns & Conventions

- **No base template exists** — each template in `templates/` is self-contained (own `<head>`/`<style>` or links `static/styles.css`). Followed as-is; not introducing a base template for this batch.
- **Route decorator gotcha (CLAUDE.md)** — no new routes/handlers added in this batch, so not applicable, but noted for implementers touching `main.py`.
- **`PERSISTENCE_BACKEND` gotcha (CLAUDE.md)** — not touched; R1 reads through the existing `repo` abstraction already in use by `admin_customers()`.

## Data Models

No data model changes. R1 uses existing fields already on the voucher row (`models.py` `VOUCHER_COLUMNS`): `driver_name`, `mobile_number`. No schema, no migration.

## API Contracts / Interfaces

No HTTP contract changes. No new routes, no changed request/response shapes, no changed status codes. `/redeem/<voucher_id>` (main.py:460-465) behavior is unchanged — the fix is upstream, in what URL the QR encodes, not in the endpoint itself.

## Module Boundaries

Unchanged. Template layer stays presentation-only (Jinja formatting of already-computed fields); `generate_voucher.py` stays the sole owner of QR/voucher-asset generation; `report_pdf.py` stays the sole owner of PDF layout.

## Change Footprint

### New files / modules

None — no new files for this batch.

### Modified files / modules

| Path | What changes here |
|------|---------------------|
| `templates/admin_customer_lookup.html` | Add "Driver Name" + "Phone Number" `<th>`/`<td>` to the per-customer Booking History table (around lines 72-94); render `{{ b.driver_name or '—' }}` / `{{ b.mobile_number or '—' }}`; wrap the `<table>` in a container with `overflow-x: auto` |
| `generate_voucher.py` | Line 33: replace hardcoded fallback default (dead Replit URL) with the correct current staging URL (`https://unifleet-v2-webapp-staging.up.railway.app`) |
| `report_pdf.py` | Line 156: title string → `"UniFleet – Unredeemed Fuel Vouchers (PDF Version)"`; line 174: header list entry `"Voucher ID (Unredeemed)"` → `"Voucher ID"` |
| `templates/register_success.html` | Line 104: `"Thank you for registering your fleet with UniFleet."` → `"Thank you for registering with UniFleet."` |
| `templates/book.html` | After the map `<details class="map-collapsible">…</details>` block (ends line 402), insert a bold `<a>` linking to the Google Maps URL, `target="_blank" rel="noopener"`, text "View Larger Stations Map" |
| `static/styles.css` | Add `a:link, a:visited, a:hover, a:active { color: #0054a6; }` (brand blue already used throughout `book.html`) |

### Deleted / replaced

None.

### Touched but not changed (silent-regression hotspots)

| Path | Why it matters |
|------|------------------|
| `data/exports/all_customers_bookings.csv` / `_EXPORT_COLUMNS` (main.py:389) | Confirms `driver_name`/`mobile_number` are the correct field names R1 must reuse — not modified, just the reference this design relies on staying consistent |
| Railway staging service env vars (outside repo) | `BASE_URL` must actually be set correctly there — a code-only fix to the fallback string does not help if the env var itself is still unset or wrong on staging; this is an infra action alongside the code change |
| Any other template linking `static/styles.css` (admin.html, terms.html, booking_success.html, admin_login.html, register.html, redeem.html, voucher_image.html) | R10's rule applies to all of them too — intentional (site-wide fix), but means visual review should spot-check at least one other page besides /Book |

## Areas of Impact

| Area | Impact | Risk (L/M/H) | Why |
|------|--------|--------------|-----|
| Customer Lookup admin page | New columns + horizontal scroll on Booking History table | L | Additive, template-only, no data/route change |
| Voucher QR generation | Fallback URL changed; real fix depends on Railway env var also being corrected | M | Code fix alone is incomplete without the matching infra env var change; if env var is skipped, bug persists on staging even after code fix is deployed |
| Supplier PDF export | Two string changes only | L | No layout/data changes, pure text |
| Register success flow | One string change | L | No layout/data changes |
| /Book page | New link element added | L | Additive, no existing content removed |
| Site-wide link styling (`styles.css`) | All pages sharing `styles.css` get explicit link-state colors | M | Broad blast radius (8 templates) — low complexity change but visually touches every page with links; worth a quick visual pass on 2-3 pages, not just /Book |

**Contract changes:** None — no API/response/event shape changes anywhere in this batch.

**Cross-cutting ripples:** Railway env var config (`BASE_URL` on staging) is a deploy-time/infra action that must happen alongside the code deploy for R4/R5 to actually resolve — flagged explicitly so it isn't missed as "just a code fix."

## Cross-Cutting Concerns

- **Errors:** No new error paths. `/redeem/<id>` 404 behavior (main.py:460-465) is unchanged — once QR encodes the right URL, existing lookup/error handling already works correctly.
- **Logging & metrics:** No new logging added. Optionally, `generate_voucher.py` could print/log the resolved `BASE_URL` at generation time for future debuggability — not required by REQ, noted as a nice-to-have, not a task.
- **Auth / authz:** No auth changes. Customer Lookup remains behind `require_admin` (main.py:314) — unchanged.
- **Performance:** R1 adds two more fields to already-loaded row dicts rendered in an existing loop — negligible cost, satisfies REQ's N1 (no perceptible slowdown) by construction (no new query, no new computation).
- **Security:** No new user input surfaces. R1 renders existing server-side data (no new injection surface). No secrets touched.
- **Migrations / rollout:** No DB migration. R4/R5 rollout requires two coordinated actions: (1) deploy the code fix (new fallback string) and (2) set `BASE_URL` correctly in Railway staging env vars — order doesn't matter functionally since the env var, if set, always wins over the fallback, but both must be done for the fix to be verified per REQ's R4 acceptance criterion.

## Architecture Decisions Log

| # | Decision | Alternatives | Chosen Because | Satisfies REQs |
|---|----------|---------------|------------------|-----------------|
| A1 | R1 reuses existing `driver_name`/`mobile_number` fields already on voucher rows — no backend/query change | Add a new joined query or dedicated endpoint for lookup page | Fields already present on every row returned by `repo.list_all_vouchers()`; zero backend risk | R1, R2, R3 |
| A2 | QR fix = update the hardcoded fallback URL string only (not fail-fast on missing env var) | Raise/log error at import time if `BASE_URL` unset in non-dev environments | User's explicit choice — keeps current low-friction deploy behavior, accepts residual risk that a future missing env var silently falls back again | R4, R5 |
| A3 | Link color fix lands in shared `static/styles.css`, defining all four link states (`:link`, `:visited`, `:hover`, `:active`) | Inline style override on /Book only | Genuinely site-wide per REQ decision #5; explicit states avoid relying on browser defaults anywhere | R10 |
| A4 | No new base/shared template introduced despite templates being self-contained | Introduce a Jinja base template now | Out of scope for a small fix batch; would expand blast radius far beyond what REQ asked for | (scope boundary) |

## Risk & Stress-Test Scenarios

### Forward — runtime failure scenarios

| Scenario | How the Design Handles It |
|----------|------------------------------|
| Booking row has `driver_name`/`mobile_number` as `NaN`/missing after a CSV read (pandas may yield float NaN, not empty string, for missing cells) | Template must use a Jinja check robust to `None`/NaN/empty string, not just falsy-string — flagged for generate-tasks to test explicitly, since `{{ b.driver_name or '—' }}` handles `None`/`''` but a stray `NaN` (pandas artifact) could render as literal "nan" text. Task should verify against a row with a genuinely missing value in a real exported CSV, not just an empty string. |
| `BASE_URL` still unset on staging after this deploy (env var change missed) | Fallback now points at a real staging URL instead of a dead one, so even in the worst case new vouchers resolve correctly — but if ops forgets to set the env var, the code fix alone still fully covers R4's acceptance criterion since the fallback IS the staging URL |
| Two admins open Customer Lookup for the same customer concurrently while a booking is being redeemed | No write path in this batch — R1 is read-only, no concurrency concern introduced |
| `styles.css` rule increases specificity conflict with a template's own inline `color:` on an `<a>` (e.g., any future inline-styled link) | Current audit found zero existing inline link-color overrides in any template — rule is additive/safe today; any future inline override would need `!important` or higher specificity to win, which is expected CSS behavior, not a gap |

### Backward — regression risk per touched area (brownfield only)

| Touched area (from Change Footprint) | What could regress | How we'd know / mitigation |
|----------------------------------------|------------------------|---------------------------------|
| `templates/admin_customer_lookup.html` | Existing 5-column Booking History table layout shifts/breaks for admins used to current view | Manual visual check on staging Customer Lookup page before/after; horizontal scroll is additive, existing columns keep their widths |
| `static/styles.css` (site-wide) | Some other page currently relies on a link rendering in a non-blue default color (unlikely given audit found no existing `a`/`:visited` rules anywhere, but should be verified) | Quick visual pass on 2-3 other templates that load `styles.css` (e.g. register.html, terms.html) after the change, not just /Book |
| `generate_voucher.py` fallback URL | None — fallback is only used when `BASE_URL` env var is absent; already-generated voucher PNGs are not regenerated by this change | No action needed; only affects vouchers generated after this deploy |
| `report_pdf.py` | Any downstream process (supplier-facing) parsing the PDF title/header text programmatically would break on the wording change | Not currently the case per codebase search (PDF is consumed visually by suppliers, not parsed) — flagged as assumption, not verified against an actual supplier integration |

## Open Questions

- Is `BASE_URL` currently set (correctly or incorrectly) in Railway's staging service env vars today, or entirely absent? This determines whether R4/R5's Railway-side action is "add" or "correct."
  - **Impact if unresolved:** Code fix (new fallback URL) still resolves the bug even if unresolved, since the fallback now points at the right place — but leaves the underlying "silent fallback" pattern unaddressed for future environments (e.g. prod).
  - **Suggested default:** Assume absent; task in Phase 3 should include verifying/setting it in Railway before closing R4/R5.

## Out of Scope

- Failing fast / erroring when `BASE_URL` is unset in non-dev environments (reason: user explicitly chose the lighter fallback-value-only fix).
- Retroactive regeneration of already-issued voucher PNGs with the old dead URL (reason: REQ explicitly scoped to newly generated vouchers only).
- Introducing a shared Jinja base template (reason: far beyond this batch's scope; each template stays self-contained as today).
- Any redesign of Customer Lookup beyond the two new columns (reason: not requested).

---

# Tasks

## Task T1: Add Driver Name + Phone Number columns to Customer Lookup booking history

> **Status:** done
> **Verification:** test-after
> **Effort:** s
> **Priority:** medium
> **Depends on:** None
> **Satisfies REQs:** R1, R2, R3
> **Footprint slice:** Modified: `templates/admin_customer_lookup.html`
> **High-risk areas touched:** None

### Description

Add "Driver Name" and "Phone Number" columns to the per-customer Booking History table on the Customer Lookup admin page, reusing `driver_name`/`mobile_number` fields already present on every booking row (same fields Export All Bookings already exposes). No backend change — `admin_customers()` already passes full voucher dicts as `bookings`.

### Test Plan

#### Test File(s)
- `tests/test_admin_customers.py`

#### Test Scenarios

##### Booking History — Driver Columns

- **test_detail_view_shows_driver_name_and_phone_columns** — GIVEN a customer with a booking that has `driver_name` and `mobile_number` set, WHEN `/admin/customers?q=<code>` is fetched, THEN the response contains "Driver Name" and "Phone Number" header text and the corresponding values in the row _(verifies R1)_
- **test_booking_table_has_scroll_container** — GIVEN the detail view renders, THEN the booking table is wrapped in an element with horizontal-scroll styling (e.g. `overflow-x: auto` in an inline style or class) _(verifies R2)_

##### Booking History — Missing Driver Info

- **test_missing_driver_info_renders_em_dash** — GIVEN a booking with blank/missing `driver_name` and `mobile_number`, WHEN the detail view renders, THEN both cells show "—" (not blank, not "None", not "nan") _(verifies R3, REQ edge case)_

##### Regression Guard

- **test_detail_view_booking_history_scoped_to_customer** (existing test, run unmodified) — confirms adding columns doesn't break existing customer-scoping of the booking list _(guards backward-regression risk for `templates/admin_customer_lookup.html`)_
- **test_customer_with_zero_bookings_renders_empty_history** (existing test, run unmodified) — confirms empty-state message still renders correctly with new column headers present _(guards backward-regression risk)_

### Implementation Notes

- **Module(s):** Presentation/template layer only — `admin_customers()` route (main.py:312-360) is unchanged.
- **Pattern reference:** `templates/admin_customer_lookup.html` lines 72-94 (existing Booking History table markup); `_EXPORT_COLUMNS` (main.py:389) confirms `driver_name`/`mobile_number` are the correct field names.
- **Key decisions:** A1 — reuse existing fields, no backend/query change (ARCH Decisions Log).
- **Libraries:** None new.
- **High-risk callouts:** None — additive template-only change.

### Scope Boundaries

- Do NOT modify `admin_customers()` route logic or `repo.list_all_vouchers()` — data is already correct.
- Do NOT redesign the Customer Lookup page beyond the two new columns (ARCH Out of Scope).
- Only touch the Booking History table markup (lines ~72-94); leave the "All Customers" table (lines ~100-121) untouched.

### Files Expected

**Modified files:**
- `templates/admin_customer_lookup.html` (add Driver Name + Phone Number columns, scroll wrapper, "—" fallback)

**Must NOT modify:**
- `main.py` `admin_customers()` route — data already correct, no change needed
- `data/exports/all_customers_bookings.csv` / `_EXPORT_COLUMNS` — reference only, confirms field names

---

## Task T2: Fix voucher QR fallback URL

> **Status:** done
> **Verification:** tdd
> **Effort:** xs
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** R4, R5
> **Footprint slice:** Modified: `generate_voucher.py` (line 33, `BASE_URL` fallback default)
> **High-risk areas touched:** Voucher QR generation (Medium risk — code fix alone is incomplete without matching Railway `BASE_URL` env var correction; see Open Questions)

### Description

`generate_voucher.py` encodes `{BASE_URL}/redeem/{voucher_id}` into every voucher QR. When the `BASE_URL` env var is unset, it silently falls back to a hardcoded, dead Replit dev URL — this is the confirmed root cause of scanned QR codes returning "Voucher ID not found" (the QR points at an unrelated old instance, not staging). Fix: update the hardcoded fallback string to the correct current staging URL.

### Test Plan

#### Test File(s)
- `tests/test_generate_voucher.py` (new file — no existing test file for this module)

#### Test Scenarios

##### QR URL Resolution

- **test_base_url_fallback_is_staging_url** — GIVEN `BASE_URL` env var is unset (monkeypatch `os.environ` to remove it before reimporting/reading the module constant), WHEN `generate_voucher.BASE_URL` is read, THEN it equals `https://unifleet-v2-webapp-staging.up.railway.app` (not the old Replit URL) _(verifies R4, R5)_
- **test_qr_content_uses_resolved_base_url** — GIVEN the fallback is active, WHEN `generate_qr_image` builds `qr_content` for a voucher, THEN it equals `https://unifleet-v2-webapp-staging.up.railway.app/redeem/<voucher_id>` _(verifies R4)_

##### Regression Guard

- **test_base_url_env_var_still_takes_precedence** — GIVEN `BASE_URL` env var is explicitly set to a custom value, WHEN the module resolves `BASE_URL`, THEN the env var value wins, not the fallback _(guards existing override behavior — same pattern DBRepo/CSVRepo selection relies on)_

### Implementation Notes

- **Module(s):** `generate_voucher.py` only — no route or persistence-layer change.
- **Pattern reference:** `generate_voucher.py:29-34` (existing `os.environ.get("BASE_URL", <fallback>)` pattern) — keep the same pattern, only change the fallback literal.
- **Key decisions:** A2 — fix the fallback value only, do not add fail-fast validation on missing env var (ARCH Decisions Log, user's explicit choice).
- **Libraries:** None new.
- **High-risk callouts:** Medium risk per ARCH Areas of Impact — this code fix must be paired with verifying/setting `BASE_URL` correctly in Railway staging env vars (infra action, outside this repo). Flag in PR description; do not consider R4/R5 done until verified end-to-end on staging (per REQ R4 acceptance criterion: scan resolves correctly on staging, not just unit-tested).
- Since `BASE_URL` is read once at module import time (module-level constant, generate_voucher.py:31-34), tests must reload the module (or use `importlib.reload`) after patching the env var, not just monkeypatch after import.

### Scope Boundaries

- Do NOT add fail-fast/raise-on-missing-env-var behavior (ARCH Out of Scope — user chose the lighter fix).
- Do NOT regenerate or fix already-issued voucher PNGs (ARCH Out of Scope).
- Only change the fallback URL literal — do not refactor the surrounding `BASE_URL` resolution pattern.

### Files Expected

**New files:**
- `tests/test_generate_voucher.py` (new test file, following the `monkeypatch` + Flask-test-client-adjacent conventions used in `tests/test_admin_customers.py`)

**Modified files:**
- `generate_voucher.py` (line 33: replace dead Replit fallback URL with `https://unifleet-v2-webapp-staging.up.railway.app`)

**Must NOT modify:**
- `main.py` (`/redeem/<voucher_id>` route, main.py:460-465) — endpoint behavior is already correct, do not touch

---

## Task T3: Supplier PDF wording changes

> **Status:** done
> **Verification:** checklist
> **Effort:** xs
> **Priority:** low
> **Depends on:** None
> **Satisfies REQs:** R6, R7
> **Footprint slice:** Modified: `report_pdf.py` (line 156 title, line 174 header list)
> **High-risk areas touched:** None

### Description

Two literal string edits in the Supplier PDF export: the title changes from "Diesel Refuel Vouchers (Offline Version)" to "Unredeemed Fuel Vouchers (PDF Version)", and the header row's "Voucher ID (Unredeemed)" loses the word "Unredeemed", becoming "Voucher ID". No layout, column, or data changes. No PDF-text-extraction library exists in this repo and adding one is out of scope (ARCH Tech Choices — no new technology), so verification is source-level plus the existing smoke test.

### Verification Checklist

- **Title string updated** — `grep -n "Unredeemed Fuel Vouchers (PDF Version)" report_pdf.py` → expected: 1 match near line 156 _(verifies R6)_
- **Old title string removed** — `grep -c "Diesel Refuel Vouchers (Offline Version)" report_pdf.py` → expected: 0 _(verifies R6)_
- **Header "Unredeemed" removed** — `grep -c "Voucher ID (Unredeemed)" report_pdf.py` → expected: 0 _(verifies R7)_
- **Header updated** — `grep -n '"Voucher ID"' report_pdf.py` → expected: 1 match in the header list near line 174 _(verifies R7)_
- **Existing PDF logic unaffected** — `pytest tests/test_report_pdf.py -v` → expected: all tests pass unmodified, including `test_build_supplier_pdf_smoke_test_all_fuel_types` and `test_existing_columns_retain_values_and_order` _(guards backward-regression risk for `report_pdf.py`)_

### Implementation Notes

- **Module(s):** `report_pdf.py` — PDF layout module only.
- **Pattern reference:** `report_pdf.py:156` (`_draw_paragraph(c, "UniFleet – Diesel Refuel Vouchers (Offline Version)", ...)`), `report_pdf.py:174` (`header = [..., "Voucher ID (Unredeemed)", ...]`).
- **Key decisions:** None beyond the literal text change specified in REQ R6/R7.
- **Libraries:** None new — explicitly no PDF-text-extraction dependency added (ARCH Tech Choices).
- **High-risk callouts:** None.

### Scope Boundaries

- Do NOT change PDF layout, column order, or any other text on the Supplier PDF.
- Do NOT add a PDF-text-extraction dependency for testing — checklist/grep verification is sufficient for a two-string change.
- Only touch lines 156 and 174 in `report_pdf.py`.

### Files Expected

**Modified files:**
- `report_pdf.py` (line 156 title string, line 174 header list entry)

**Must NOT modify:**
- Any other line in `report_pdf.py` (regression-guarded by existing `tests/test_report_pdf.py` suite)

---

## Task T4: Update /register/success thank-you text

> **Status:** done
> **Verification:** test-after
> **Effort:** xs
> **Priority:** low
> **Depends on:** None
> **Satisfies REQs:** R8
> **Footprint slice:** Modified: `templates/register_success.html` (line 104)
> **High-risk areas touched:** None

### Description

First sentence on the account registration success page changes from "Thank you for registering your fleet with UniFleet." to "Thank you for registering with UniFleet." No other text on the page changes.

### Test Plan

#### Test File(s)
- `tests/test_register_success_text.py` (new file — no existing test covers this page's rendered text)

#### Test Scenarios

##### Success Page Text

- **test_success_page_shows_updated_thank_you_text** — GIVEN `/register/success` is requested, WHEN the page renders, THEN the response contains "Thank you for registering with UniFleet." _(verifies R8)_
- **test_success_page_no_longer_shows_old_text** — GIVEN the same request, THEN the response does NOT contain "your fleet" _(verifies R8)_

##### Regression Guard

- **test_success_page_still_renders_account_code** — GIVEN `/register/success?account_code=TEST`, THEN the response still renders successfully (status 200) with the rest of the page content intact (guards against the text edit accidentally breaking the surrounding template block) _(guards backward-regression risk for `templates/register_success.html`)_

### Implementation Notes

- **Module(s):** Template layer only — `register_success()` route (main.py:716-718) is unchanged.
- **Pattern reference:** `templates/register_success.html:104`.
- **Key decisions:** None beyond REQ R8's exact wording.
- **Libraries:** None new.
- **High-risk callouts:** None.

### Scope Boundaries

- Do NOT change any text on the page other than the first sentence (REQ R8 — "rest of the message untouched").
- Only touch line 104 in `templates/register_success.html`.

### Files Expected

**New files:**
- `tests/test_register_success_text.py`

**Modified files:**
- `templates/register_success.html` (line 104)

**Must NOT modify:**
- `main.py` `register_success()` route — no logic change needed

---

## Task T5: Add "View Larger Stations Map" link to /Book page

> **Status:** done
> **Verification:** ui
> **Effort:** xs
> **Priority:** low
> **Depends on:** None
> **Satisfies REQs:** R9
> **Footprint slice:** Modified: `templates/book.html` (insert link after map `<details>` block, ends line 402)
> **High-risk areas touched:** None

### Description

Add a bold, linked "View Larger Stations Map" text directly under the existing embedded stations map on `/Book`, opening the full Google Maps view in a new tab. Purely additive — no existing content changes.

### Verification Checklist

- **Link visible and positioned correctly** — visit `/book` (staging or local), confirm bold linked text "View Larger Stations Map" appears directly below the map section — expected: visible, does not disrupt existing "How to Find Discounts" or Driver & Vehicle sections below it
- **Link target correct** — click the link — expected: opens `https://www.google.com/maps/d/u/0/viewer?mid=1YTyyRvSx1Fan8bbrnRsMBZuIDbrVC7Y&femb=1` in a new browser tab
- **Existing map still functional** — confirm the embedded map `<iframe>` and its collapsible `<details>` toggle still work as before — expected: no regression to `templates/book.html:400-402`

#### Testable Seams

- **test_book_page_has_stations_map_link** (test-after, `tests/test_book_page.py` or existing book-page test file if one exists) — GET `/book`, response contains `href="https://www.google.com/maps/d/u/0/viewer?mid=1YTyyRvSx1Fan8bbrnRsMBZuIDbrVC7Y&femb=1"` and `target="_blank"` _(verifies R9)_

### Implementation Notes

- **Module(s):** Template layer only — no route change.
- **Pattern reference:** `templates/book.html:325` (existing pattern for an external `target="_blank" rel="noopener"` link — Facebook link) — follow the same `rel="noopener"` convention.
- **Key decisions:** None beyond REQ decision #7 (opens in new tab).
- **Libraries:** None new.
- **High-risk callouts:** None.

### Scope Boundaries

- Do NOT modify the existing embedded map `<iframe>` or `<details class="map-collapsible">` block — insert after it only.
- Do NOT restyle other sections of `/Book`.

### Files Expected

**Modified files:**
- `templates/book.html` (insert new link element after line 402)

**Must NOT modify:**
- `templates/book.html:400-402` (existing map `<details>`/`<iframe>` block — insert-after only, do not alter)

---

## Task T6: Fix site-wide link color (blue, not purple)

> **Status:** done
> **Verification:** ui
> **Effort:** xs
> **Priority:** medium
> **Depends on:** None
> **Satisfies REQs:** R10
> **Footprint slice:** Modified: `static/styles.css` (add `a:link`/`a:visited`/`a:hover`/`a:active` rule)
> **High-risk areas touched:** Site-wide link styling (Medium risk — `styles.css` loads on 8 templates; broad blast radius even though the change itself is simple)

### Description

No template currently defines an `a`/`:visited` CSS rule anywhere in the codebase — links render with the browser's default `:visited` purple once clicked. Add one shared rule to `static/styles.css` (already loaded by `book.html`, `register.html`, and 6 other templates) defining all four link states in the site's existing brand blue (`#0054a6`, already used extensively in `book.html`), fixing the bug genuinely site-wide in one file.

### Verification Checklist

- **Book page links stay blue after visiting** — on `/book`, click the "Register" link, navigate back, confirm the link renders blue (`#0054a6`), not purple — expected: no purple state, matches unvisited color
- **Facebook link stays blue** — same check for the Facebook link on `/book` (line 325) — expected: blue in both unvisited and visited state
- **No regression on other pages loading styles.css** — spot-check at least one other page (e.g. `/register` or `/terms`) that has links — expected: links render in the intended blue, no unexpected color change introduced by the new shared rule
- **Hover/active states consistent** — hover and click-and-hold on a link — expected: consistent blue-family styling, no unstyled/default-browser flash

### Testable Seams

None — CSS cascade and `:visited` state are not meaningfully unit-testable via the Flask test client (no browser rendering in this test suite); this is a pure visual/CSS verification per ARCH Cross-Cutting Concerns.

### Implementation Notes

- **Module(s):** `static/styles.css` — shared stylesheet, no template logic changes.
- **Pattern reference:** `#0054a6` — the brand blue already used at `templates/book.html:24,83,123,150,172,215`.
- **Key decisions:** A3 — land the fix in shared `styles.css`, define all four link states explicitly rather than relying on browser defaults (ARCH Decisions Log).
- **Libraries:** None new.
- **High-risk callouts:** Medium risk (Areas of Impact) — rule applies to all 8 templates loading `styles.css`. Audit confirmed zero existing inline `color:` overrides on any `<a>` tag in any template, so the change is additive/safe, but the verification checklist explicitly includes a spot-check on a second page beyond `/book` to catch anything the audit missed.

### Scope Boundaries

- Do NOT add per-template inline overrides — the fix must be the single shared rule in `styles.css` (REQ decision #5 — site-wide, not /Book-only).
- Do NOT restyle buttons, nav elements, or any non-link element while touching this file.
- Only add the new `a:link, a:visited, a:hover, a:active` rule — do not modify other existing rules in `styles.css`.

### Files Expected

**Modified files:**
- `static/styles.css` (add link-state color rule)

**Must NOT modify:**
- Any individual template's inline `<style>` block — the fix is centralized in `styles.css` only
- Other existing rules within `static/styles.css`
