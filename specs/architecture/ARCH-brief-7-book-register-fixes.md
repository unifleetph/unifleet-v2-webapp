# Architecture: Brief-7 — Book/Register Copy Fixes, Terms & Conditions, Amount Due Bug

> **Date:** 2026-08-04
> **Phase:** 2 of 5 (System Architecture)
> **Requirements source:** specs/requirements/REQ-brief-7-book-register-fixes.md
> **Type:** feature

## Architecture Summary

Five small, mostly independent changes: a new `GET /terms` route serving a hand-authored HTML page (no new dependency — the project has no markdown-rendering library, and this is a single static legal document), a required Terms-agreement checkbox added to `/book`'s existing booking form (mirroring `register.html`'s proven `required`-checkbox pattern, client-only validation, no new server logic), several copy edits to `/book` and `/register`, and a real bug fix: `booking_success.html` currently hardcodes `Amount Due: ₱1` as a literal string, and separately the value Flask *does* pass in re-reads the raw pre-discount form field instead of the already-correct post-discount amount (`computed_pay_php`) computed a few lines earlier in `main.py`. The fix wires the correct value through and displays it with the same `{:,.2f}` currency-formatting pattern already used in `admin.html`.

## High-Level Structure

```
GET /terms  →  main.py:terms() (new)  →  render_template('terms.html')  (new, static content)

POST /book  →  main.py:book()  →  computed_pay_php (already computed, main.py:1080)
                                        │
                                        ▼ (fixed: was re-reading raw form field instead)
                                   due_amount = computed_pay_php
                                        │
                                        ▼
                          render_template('booking_success.html', due_amount=...)
                                        │
                                        ▼
                    {{ '{:,.2f}'.format(due_amount|float) }}  (fixed: was literal "₱1")

GET/POST /book (form)  →  book.html  →  copy edits (button text, link text, section
                                          removals/additions) + new required Terms
                                          checkbox (client-only validation, no new
                                          server logic — same as register.html's
                                          existing acknowledge checkbox)

GET /register  →  register.html  →  one new copy line under existing heading
```

No new layers, no new data flow shape — everything rides the existing Flask render_template pattern already used throughout `main.py`.

## Tech Choices

| Area | Decision | Alternatives Considered | Rationale |
|------|----------|--------------------------|-----------|
| Terms content rendering | Hand-authored HTML in `templates/terms.html`, content transcribed from `docs/UnifleetTermsConditions.md` | Add a markdown-rendering dependency (e.g. `markdown`/`mistune`) and render the .md file at request time | No build pipeline, no existing markdown dependency; every other page in this app is hand-authored HTML — adding a dependency for one static legal document is disproportionate. Developer confirmed. |
| Terms checkbox validation | Client-only native HTML `required`, no server-side check | Server-side validation rejecting POST if checkbox missing | Mirrors `register.html`'s existing `acknowledge` checkbox exactly — confirmed via grep that no server-side check exists for it today. Consistent precedent, no new validation logic. |
| Currency formatting | Reuse `admin.html`'s existing `₱{{ '{:,.2f}'.format(x|float) }}` Jinja pattern | New Python currency-formatting helper function | Already proven in this codebase (`admin.html:363`), zero new code needed, no risk of drift from a second formatting implementation |

## Patterns & Conventions

- **Hand-authored template pages** — every page in this app (register, book, admin, redeem, etc.) is a standalone Jinja template with its own local `<style>` block; `terms.html` follows the same shape (header, page-title, centered-content).
- **Client-only checkbox validation** — established by `register.html`'s `acknowledge` checkbox; `/book`'s new Terms checkbox follows the identical mechanism.
- **`.mobile-page` responsive scoping (Brief-6)** — `terms.html` gets the viewport meta + `.mobile-page` body class from the start, consistent with every other customer-facing page shipped since Brief-6, so it doesn't need a follow-up mobile-fix REQ later.

## Data Models

None. No new/changed entities, no DB schema touched. `due_amount` is a request-scoped float, not persisted differently than it already is (the persisted voucher row already stores the correct `computed_pay_php` value under `requested_amount_php` — only the confirmation-page *display* variable was wrong).

## API Contracts / Interfaces

### `main.py` (Flask routes)

| Method/Op | Path | Purpose | Errors / Returns |
|-----------|------|---------|----------------------|
| GET | `/terms` (new) | Render the Terms & Conditions page | 200, renders `terms.html` — no error paths, static content |
| POST | `/book` (modified) | Existing booking submission; `due_amount` now sourced from `computed_pay_php` instead of the raw form field | Unchanged error/redirect behavior — only the value assigned to `due_amount` changes, not the response shape or status codes |

**Auth requirements:** `/terms` is public, unauthenticated — same as every other customer-facing route (`/book`, `/register`).

## Module Boundaries

| Module | Responsibility | Allowed Dependencies |
|--------|------------------|------------------------|
| `templates/terms.html` (new) | Static Terms & Conditions content | None — no JS, no server data beyond the page shell |
| `templates/book.html` | Booking form markup + copy | `static/styles.css` (linked), local `<style>` block (new `.inline-checkbox` rule) |
| `templates/booking_success.html` | Confirmation page markup | `due_amount` passed in from `main.py` |
| `templates/register.html` | Registration form markup + copy | Local `<style>` block only |
| `main.py` | Routes, `computed_pay_php` calculation (unchanged), `due_amount` assignment (fixed) | No new imports needed |

## Change Footprint

### New files / modules

| Path | Purpose | Pattern reference |
|------|------------|------------------------|
| `templates/terms.html` | Terms & Conditions page content | `templates/register.html` (page shell: header, page-title, centered-content, local `<style>` block, `.mobile-page` body class) |

### Modified files / modules

| Path | What changes here |
|------|----------------------|
| `main.py` | New `@app.route('/terms')` GET handler rendering `terms.html`; in `book()`'s POST handler, change `due_amount = request.form.get('requested_amount_php')` (main.py:1135, wrong — raw pre-discount value) to `due_amount = computed_pay_php` (main.py:1080, already correct) |
| `templates/book.html` | Submit button text → "Submit Booking & Start Payment"; account-code link text → ">>> Register Vehicle To Get 4-Letter Account CODE <<<"; remove "How It Works" heading only (keep 3 numbered steps); remove Tagalog tagline line only (keep English); add Facebook page link (new tab); add required Terms-agreement checkbox with inline link to `/terms`, positioned directly above the Submit Booking button; add `.inline-checkbox` CSS rule to the local `<style>` block (copied from `register.html`'s existing rule) |
| `templates/booking_success.html` | Replace hardcoded `Amount Due: ₱1` with `Amount Due: ₱{{ '{:,.2f}'.format(due_amount|float) }}` |
| `templates/register.html` | Add `<p>Sign-up to get access to our fuel discounts.</p>` directly under the existing `<strong>Save up to ₱1,000...</strong>` heading, inside the same `.info-box` section |
| `tests/test_book_and_booking_success_copy.py` | Update the assertion at line 128 from `"Amount Due: ₱1" in body` to `"Amount Due: ₱10,000.00" in body` — the test's stubbed discount is 0, so with `requested_amount_php=10000`, `computed_pay_php` = 10000.0, formatted as `₱10,000.00` |

### Deleted / replaced

None — the "How It Works" heading and Tagalog tagline are template-content removals within `book.html` (captured above), not separate file deletions.

### Touched but not changed (silent-regression hotspots)

| Path | Why it matters |
|------|-------------------|
| `docs/UnifleetTermsConditions.md` | Source of truth for `terms.html`'s content, transcribed once — not read at runtime, so a future edit to the .md file won't automatically appear on `/terms` (see Open Questions) |
| `tests/test_book_pg.py`, `tests/test_book_calculator_inversion.py` | POST to `/book` directly via Flask's test client, bypassing browser-side HTML validation entirely — confirmed the new required Terms checkbox introduces zero risk here, since `required` is enforced client-side only and these tests never go through a browser |
| `templates/admin.html` | Source of the currency-formatting pattern being copied (`'{:,.2f}'.format(x|float)`) — not modified, just referenced as a pattern |

## Areas of Impact

| Area | Impact | Risk (L/M/H) | Why |
|------|-----------|----------------|-----|
| `main.py` `book()` POST handler | `due_amount` now correctly reflects `computed_pay_php` | L | Purely additive correction — `computed_pay_php` was already computed and validated (rejected if ≤0) before this line; just changes which variable feeds the display |
| `tests/test_book_and_booking_success_copy.py` | One assertion updated to match corrected behavior | L | Expected, intentional test update — the old assertion locked in the bug |
| `/book` form | New required field (Terms checkbox) | L | Client-only validation, no backend impact; existing automated tests confirmed unaffected |
| `/terms` (new route) | New public page | L | Fully additive, no existing route/behavior touched |

**Contract changes:** None — no API/route signature changes visible to any external consumer; `/book`'s response shape and status codes are unchanged.

**Cross-cutting ripples:** None — no auth, telemetry, migration, or feature-flag surface touched.

## Cross-Cutting Concerns

- **Errors:** No new error paths. The Terms checkbox is client-validated only (`required` attribute), consistent with `register.html`'s existing precedent — if a non-browser client (e.g. `curl`, the automated tests) omits it, the request still succeeds exactly as it does today, since no server-side check is being added.
- **Logging & metrics:** No change.
- **Auth / authz:** `/terms` is public, unauthenticated, matching `/book` and `/register`.
- **Performance:** Negligible — one new static page, a handful of copy edits, one corrected variable assignment.
- **Security:** No new input handling, no new validation boundary. The Terms checkbox doesn't introduce any new user-controlled data reaching the server (its presence/absence in form data is simply not checked, same as `acknowledge` today).
- **Migrations / rollout:** None — template/route/copy changes only, no schema/env changes, deploys via the normal Railway Dockerfile flow, trivially revertible (pure diff, no data migration).

## Architecture Decisions Log

| # | Decision | Alternatives | Chosen Because | Satisfies REQs |
|---|-----------|----------------|-------------------|-------------------|
| A1 | Terms content is hand-authored HTML in a new `terms.html`, no markdown dependency | Add a markdown-rendering library and render `docs/UnifleetTermsConditions.md` at request time | No build pipeline or markdown dependency exists; every other page in this app is hand-authored; disproportionate to add a dependency for one static document | R1 |
| A2 | Terms checkbox uses client-only native `required` validation, no server-side check | Add server-side rejection if checkbox field missing from POST | Mirrors `register.html`'s existing `acknowledge` checkbox exactly (confirmed no server check exists for it); consistent precedent, zero new validation logic | R3 |
| A3 | R2 (plain Terms link below Submit button) is satisfied by R3's inline checkbox link instead of a separate link element | Two separate links: one below the button (literal brief wording), one inline in the checkbox label above it | Developer's explicit call — a second identical link is redundant once the checkbox's inline link exists right above the button | R2, R3 |
| A4 | Amount Due fix changes both what value is passed (`main.py`: use `computed_pay_php` not the raw form re-read) and how it's displayed (`booking_success.html`: format via Jinja, not a literal string) | Fix only the template's hardcoded string, leaving `due_amount`'s source unchanged | Both parts are broken independently — fixing only one leaves the other bug in place: fixing only the template would still show the wrong (pre-discount) number; fixing only the source without touching the template literal wouldn't change anything visible at all | R5 |
| A5 | Currency formatting reuses `admin.html`'s existing `'{:,.2f}'.format(x|float)` Jinja pattern | New Python helper function for currency formatting | Already proven in this codebase, zero new code, no risk of a second formatting implementation drifting from the first | R5 |
| A6 | "How It Works" heading removed, its 3 numbered steps kept | Remove the entire block (heading + steps) | Developer correction during REQ phase — steps still provide useful guidance | R8 |
| A7 | Only the Tagalog tagline is removed; the English tagline stays | Remove both taglines | Brief/mockup only marks the Tagalog line for removal | R9 |
| A8 | `terms.html` ships with the Brief-6 mobile pattern (viewport meta, `.mobile-page` class) from the start | Ship desktop-only, add mobile support in a later REQ | Every customer-facing page shipped since Brief-6 already has this; adding it now avoids a guaranteed follow-up mobile-fix task for a brand-new page | N2 |

## Risk & Stress-Test Scenarios

### Forward — runtime failure scenarios

| Scenario | How the Design Handles It |
|-------------|-------------------------------|
| Slow network mid-booking | No new risk — `/terms` and the copy edits add no new network calls or dependencies; same render_template pattern as every existing page |
| Two users submitting `/book` simultaneously | Unrelated to this change — `computed_pay_php` calculation and booking creation logic are untouched; only which variable is read into `due_amount` for display changes |
| Rollback needed post-deploy | Pure template/route/copy diff, no DB/schema/env involved — trivially revertible via a single git revert |

### Backward — regression risk per touched area (brownfield only)

| Touched area | What could regress | How we'd know / mitigation |
|-----------------|------------------------|----------------------------------|
| `tests/test_book_and_booking_success_copy.py:128` | Currently asserts the bug (`"Amount Due: ₱1"`) — will fail once the fix lands unless updated in the same task | Updated in this task's footprint to assert `"Amount Due: ₱10,000.00"`, matching the test's stubbed 0-discount scenario |
| `tests/test_book_pg.py`, `tests/test_book_calculator_inversion.py` | New required Terms checkbox could theoretically break form-POST tests if validation were server-side | Confirmed both files POST directly via Flask's test client, bypassing browser HTML validation entirely — client-only `required` cannot affect them. Full suite run after implementation confirms this holds. |
| `main.py` `book()` — `computed_pay_php` → `due_amount` | Any other code path reading the old `due_amount` variable in an unexpected way | Grepped — `due_amount` is only ever assigned once and passed straight into `render_template`; no other reader exists |

## Open Questions

- `terms.html`'s content is a one-time transcription of `docs/UnifleetTermsConditions.md`, not a live read of that file — a future edit to the .md source won't automatically appear on `/terms`.
  - **Impact if unresolved:** Content could drift between the source document and the live page if the .md file is edited later without a corresponding template update.
  - **Suggested default:** Acceptable for this REQ (legal text changes infrequently); if live-sync becomes a real need later, that's a separate REQ (likely the markdown-dependency alternative from A1).

## Out of Scope

- Adding a markdown-rendering dependency or any CMS-like mechanism for Terms content (reason: disproportionate for one static document, per A1)
- Server-side validation of the Terms checkbox (reason: mirrors existing `register.html` precedent, per A2)
- Any change to the discount/pricing calculation logic itself (reason: `computed_pay_php`'s calculation is already correct; this REQ only fixes how that value reaches the display)
- Mobile-specific redesign beyond applying the already-established Brief-6 pattern to the new `terms.html` page (reason: N2 is regression-guard only, not a redesign)

---

# Tasks

## Task T1: Fix Amount Due bug on booking confirmation page

> **Status:** done — all 4 test scenarios pass (`tests/test_book_and_booking_success_copy.py`, 6 tests total in file), full suite 349 passed (347 + 2 new). Regression guard written and confirmed against baseline before the fix landed.
> **Verification:** test-after
> **Effort:** s
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** R5
> **Footprint slice:** Modified: `main.py` (due_amount source fix only, not the new `/terms` route), `templates/booking_success.html`, `tests/test_book_and_booking_success_copy.py`
> **High-risk areas touched:** None — Areas of Impact rows for this slice are all L risk

### Description

`booking_success.html` currently hardcodes `Amount Due: ₱1` as a literal string, and separately the value Flask does pass in (`due_amount`) re-reads the raw pre-discount form field instead of `computed_pay_php`, which is already correctly computed a few lines earlier in `main.py`'s `book()` handler. This task fixes both: the source of `due_amount` and the template's display of it, formatted with the same `'{:,.2f}'` pattern already used in `admin.html`.

### Test Plan

#### Test File(s)
- `tests/test_book_and_booking_success_copy.py`

#### Test Scenarios

##### Amount Due Display

- **shows corrected amount due, not the old hardcoded literal** — GIVEN a booking submitted with `requested_amount_php=10000` and a stubbed 0 discount (existing test setup) WHEN the confirmation page renders THEN the body contains `"Amount Due: ₱10,000.00"` and does NOT contain the old literal `"Amount Due: ₱1"` _(verifies R5, updates existing test)_
- **shows post-discount amount, not the raw total** — GIVEN a station with price ₱60/L and discount ₱5/L, a booking submitted with `requested_amount_php=1200` (total fuel amount) WHEN the confirmation page renders THEN the body contains `"Amount Due: ₱1,100.00"` (1200 total − 100 discount) and does NOT contain `"₱1,200.00"` _(verifies R5's core requirement — proves the discount is actually subtracted, not just that the literal is gone)_

##### Regression Guard

- **zero/negative computed pay still rejected before reaching confirmation page** — GIVEN a discount that would make `computed_pay_php ≤ 0` WHEN the booking is submitted THEN the response redirects with an error and never renders `booking_success.html` _(guards ARCH backward-regression risk — this existing `main.py` logic sits adjacent to the fixed line but must stay unchanged)_

[All scenarios pulled from REQ R5's acceptance criterion and ARCH's backward-regression risk table for `main.py`'s `book()` POST handler.]

### Implementation Notes

- **Module(s):** `main.py` (`book()` POST handler — change `due_amount = request.form.get('requested_amount_php')` to `due_amount = computed_pay_php`), `templates/booking_success.html` (display logic).
- **Pattern reference:** `templates/admin.html:363` — `₱{{ '{:,.2f}'.format(_req|float) }}` is the exact currency-formatting pattern to reuse.
- **Key decisions (from Architecture Decisions Log):**
  - A4 — both the value source (`main.py`) and the display (`booking_success.html`) must be fixed together; fixing only one leaves the bug partially in place.
  - A5 — reuse `admin.html`'s existing Jinja formatting pattern, no new Python helper.
- **Libraries:** None.
- **High-risk callouts:** None — all L risk. The one thing to double check: `computed_pay_php` (main.py:1080) is already validated `> 0` before this task's changed line runs (main.py:1082-1087) — do not duplicate that check, it already exists upstream and is out of this task's footprint.

### Scope Boundaries

- Do NOT touch the `/terms` route or any `book.html`/`register.html` copy — that's T2's footprint
- Do NOT modify the `computed_pay_php` calculation itself (main.py:1078-1080) — it's already correct; only the line that assigns `due_amount` changes
- Do NOT add new server-side validation for the computed-pay-≤0 case — it already exists and is out of scope (REQ Edge Cases table)
- Only implement the three footprint files listed below

### Files Expected

**New files:** None.

**Modified files:**
- `main.py` (`due_amount = computed_pay_php` instead of re-reading the raw form field)
- `templates/booking_success.html` (`Amount Due: ₱{{ '{:,.2f}'.format(due_amount|float) }}` instead of the hardcoded literal)
- `tests/test_book_and_booking_success_copy.py` (update the existing assertion; add the new post-discount test scenario)

**Must NOT modify:**
- `main.py`'s `computed_pay_php` calculation (main.py:1068-1087) — already correct, out of this task's footprint
- `tests/test_book_pg.py`, `tests/test_book_calculator_inversion.py` (Touched-but-not-changed per ARCH — confirmed unaffected since they don't assert on `due_amount` display)

---

## Task T2: Terms & Conditions page and book/register copy updates

> **Status:** not started
> **Verification:** ui
> **Effort:** m
> **Priority:** medium
> **Depends on:** None
> **Satisfies REQs:** R1, R2, R3, R4, R6, R7, R8, R9, R10, N2
> **Footprint slice:** New: `templates/terms.html`; Modified: `main.py` (new `/terms` route only, not the due_amount fix), `templates/book.html`, `templates/register.html`
> **High-risk areas touched:** None — Areas of Impact rows for this slice are all L risk

### Description

Adds a new Terms & Conditions page (hand-authored HTML, no markdown dependency, mirrors the existing page-shell pattern) and a required Terms-agreement checkbox on `/book`'s booking form, plus a batch of copy edits across `/book` and `/register`: Submit button text, account-code link text, removing the "How It Works" heading (keeping its 3 steps), removing the Tagalog tagline (keeping the English one), adding a Facebook page link, and adding one new onboarding line to `/register`.

### Verification Checklist

- **`/terms` renders Terms content** — load `/terms`; expected: page shows content matching `docs/UnifleetTermsConditions.md`'s sections (Acceptance, UniFleet Credits & Fuel Vouchers, etc.) _(verifies R1)_
- **Terms checkbox required, correctly positioned** — on `/book`'s booking form, locate the checkbox directly above the Submit Booking button; expected: unchecked submission is blocked by native browser validation; the checkbox's inline link navigates to `/terms` in the same tab _(verifies R2, R3)_
- **Submit button text** — expected: reads "Submit Booking & Start Payment" _(verifies R4)_
- **Register page copy** — expected: "Sign-up to get access to our fuel discounts." appears as its own line directly under the existing "Save up to ₱1,000..." heading _(verifies R6)_
- **Book page account-code link text** — expected: reads ">>> Register Vehicle To Get 4-Letter Account CODE <<<" _(verifies R7)_
- **"How It Works" heading removed, steps intact** — expected: the heading text is gone; the 3 numbered steps ("Register Vehicle (link above)", "Enter Details", "Get CODE") are still present, unchanged _(verifies R8)_
- **Tagalog tagline removed, English kept** — expected: "Malaking tipid. Mas mahabang biyahe." is gone; "Big savings. Longer trips." is still present _(verifies R9)_
- **Facebook link** — expected: "Follow our Facebook page: Fuel Discounts by UniFleet" link present, correct URL (`https://www.facebook.com/people/Fuel-Discounts-by-UniFleet/61589914800072/`), `target="_blank"` opens in a new tab _(verifies R10)_
- **Mobile layout intact** — `terms.html` has viewport meta + `.mobile-page` body class, no pinch-zoom needed at 375px; `book.html`/`register.html` mobile layout unregressed by the new checkbox/copy (screenshot at 375px) _(verifies N2)_
- **Desktop unchanged elsewhere** — spot-check `book.html`/`register.html` at desktop width outside the touched sections, no unintended visual diff

#### Testable Seams

None — no JS added, no logic branches; this is markup + copy + one new static page. All verification is visual/DOM-inspection based, consistent with `ui` mode. (The Terms checkbox's `required` attribute is a testable DOM property, but its pass/fail is judged by the human-verified checklist item above, not a separate automated test — same precedent as `register.html`'s existing checkbox.)

### Implementation Notes

- **Module(s):** `templates/terms.html` (new), `templates/book.html`, `templates/register.html` (presentation), `main.py` (`/terms` route only).
- **Pattern reference:** `templates/register.html` for the page shell (header, page-title, centered-content, local `<style>` block, `.mobile-page` body class) and its existing `.inline-checkbox`/`acknowledge` checkbox markup — copy that pattern into `book.html`'s local `<style>` block and form.
- **Key decisions (from Architecture Decisions Log):**
  - A1 — hand-authored HTML in `terms.html`, no markdown dependency.
  - A2 — Terms checkbox is client-only `required`, no server-side validation to add.
  - A3 — only one Terms link exists (the checkbox's inline link) — do not add a second separate link below the Submit button.
  - A6 — remove only the "How It Works" heading, keep its 3 steps.
  - A7 — remove only the Tagalog tagline, keep the English one.
  - A8 — `terms.html` ships with viewport meta + `.mobile-page` class from the start.
- **Libraries:** None — no new dependencies (per A1).
- **High-risk callouts:** None — all L risk. Double-check `book.html`'s new `.inline-checkbox` CSS doesn't collide with anything already in that template's local `<style>` block (it currently has none, confirmed via grep during architecture phase).

### Scope Boundaries

- Do NOT add a markdown-rendering dependency (ARCH Out of Scope, A1)
- Do NOT add server-side validation for the Terms checkbox (ARCH Out of Scope, A2)
- Do NOT add a second Terms link separate from the checkbox's inline link (A3)
- Do NOT remove the 3 numbered "How It Works" steps, only the heading (A6)
- Do NOT remove the English "Big savings. Longer trips." tagline (A7)
- Do NOT touch `main.py`'s `due_amount`/`computed_pay_php` logic — that's T1's footprint
- Only implement the four footprint files listed below

### Files Expected

**New files:**
- `templates/terms.html` (Terms & Conditions content, mirrors `templates/register.html`'s page-shell pattern)

**Modified files:**
- `main.py` (new `@app.route('/terms')` GET handler only)
- `templates/book.html` (Submit button text, account-code link text, "How It Works" heading removal, Tagalog tagline removal, Facebook link, Terms checkbox + inline link, `.inline-checkbox` CSS in local `<style>` block)
- `templates/register.html` (new onboarding line under existing heading)

**Must NOT modify:**
- `main.py`'s `due_amount`/`computed_pay_php` logic (T1's footprint, not this task's)
- `docs/UnifleetTermsConditions.md` (source content only, read once during implementation to transcribe, not modified or read at runtime)

---

_Status values: `not started` (defined, not picked up) | `in progress` (implementation underway) | `done` (verification evidence produced) | `blocked` (cannot proceed — see notes)._
