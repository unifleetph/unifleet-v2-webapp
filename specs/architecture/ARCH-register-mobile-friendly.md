# Architecture: Register Page Mobile Enhancement

> **Date:** 2026-08-04
> **Phase:** 2 of 5 (System Architecture)
> **Requirements source:** specs/requirements/REQ-register-mobile-friendly.md
> **Type:** feature

## Architecture Summary

Pure presentation-layer fix, no backend/JS involved. Add the viewport meta tag and `.mobile-page` body class (established pattern from Brief-6/ARCH-brief-6) to `register.html` and `register_success.html`, then extend the existing `@media (max-width: 480px)` block in `static/styles.css` with a handful of new selectors covering the two element shapes Brief-6 didn't have: `<textarea>` and a checkbox+label row. Zero new files, zero route/DB changes, additive-only CSS scoped so it cannot reach `admin_login.html` despite shared class names.

## High-Level Structure

```
Browser → GET/POST /register  → main.py:register()        → render_template('register.html')
Browser → GET      /register/success → main.py:register_success() → render_template('register_success.html')
                                                                              │
                                                                              ▼
                                                        <viewport meta> + <body class="mobile-page">
                                                                              │
                                                                              ▼
                                                    static/styles.css @media(max-width:480px) block
                                                    (existing Brief-6 rules + new register-specific rules)
```

No new layers, no new data flow. Both routes already render_template with no logic change needed (confirmed in `main.py:651-709` — pure render, `register()` POST path only writes to CSV/DB via existing unrelated code, untouched here).

## Tech Choices

| Area | Decision | Alternatives Considered | Rationale |
|------|----------|--------------------------|-----------|
| Styling approach | Plain CSS, extend existing `@media(max-width:480px)` block in `static/styles.css` | New separate `@media` block; CSS-in-template | Matches Brief-6 precedent exactly (REQ decision #2); avoids duplicate breakpoint declarations; no build pipeline in this project so plain CSS is the only option anyway |
| Scoping | `.mobile-page` body class, same as Brief-6 | Page-specific classes per element; ID selectors | Proven pattern, already protects `admin_login.html` which shares `.page-title`/`.centered-content` names |
| JS | None | Any JS-based reflow/behavior | Neither template has a `<script>` tag; no interactive behavior needed beyond native browser rotate/resize handling |

## Patterns & Conventions

- **`.mobile-page` scoping** — from ARCH-brief-6; every new mobile rule is prefixed `.mobile-page .selector` so it cannot leak into `admin_login.html`, which shares `.page-title`/`.centered-content` but does not (and will not) carry the `.mobile-page` class.
- **Single shared breakpoint** — `@media (max-width: 480px)`, extended rather than duplicated.
- **No-zoom-block viewport meta** — `width=device-width, initial-scale=1`, explicitly without `maximum-scale`/`user-scalable=no`, per Brief-6's accessibility requirement.
- **Native label-click for checkbox tap target** — relies on existing `<label for="acknowledge">` already toggling the checkbox; no custom JS or wrapper markup needed to satisfy R4.

## Data Models

None. No new/changed entities, no DB schema touched.

## API Contracts / Interfaces

None. No route signatures, request/response shapes, or auth requirements change. `main.py:651` (`/register`, GET/POST) and `main.py:707-709` (`/register/success`, GET) are unmodified.

## Module Boundaries

| Module | Responsibility | Allowed Dependencies |
|--------|------------------|------------------------|
| `templates/register.html` | Form markup, viewport meta, body class | `static/styles.css` (linked), no JS |
| `templates/register_success.html` | Confirmation markup, viewport meta, body class | `static/styles.css` (linked), no JS |
| `static/styles.css` | All new mobile CSS rules | None (pure CSS file, no preprocessor) |

## Change Footprint

### New files / modules

None.

### Modified files / modules

| Path | What changes here |
|------|----------------------|
| `templates/register.html` | Add `<meta name="viewport" content="width=device-width, initial-scale=1">` to `<head>`; add `class="mobile-page"` to `<body>` |
| `templates/register_success.html` | Add same viewport meta tag; add `class="mobile-page"` to `<body>` |
| `static/styles.css` | Extend existing `@media(max-width:480px)` block: (1) add `textarea` to the existing tap-target/font-size selector list (currently `input, select, button, .full-width-input, .btn`); (2) add `.mobile-page .inline-checkbox input[type="checkbox"]` rule — modest padding to grow hit area without visually resizing beyond current `transform:scale(1.1)`; (3) add `.mobile-page .button` rule — min-height 44px, flex-center, for register_success's anchor-styled CTA (not covered by any existing selector) |

### Deleted / replaced

None.

### Touched but not changed (silent-regression hotspots)

| Path | Why it matters |
|------|-------------------|
| `templates/admin_login.html` | Shares `.page-title`/`.centered-content` class names with register.html; confirmed it has no `.mobile-page` body class, so the extended media query cannot reach it regardless of viewport width |
| `main.py:651-709` (`register()`, `register_success()`) | Pure render_template calls; verified no logic depends on template structure changing here |
| `tests/` referencing `/register` | Grepped — no test asserts exact HTML structure/classes of register.html or register_success.html, so additive meta/class changes carry no test-breakage risk |

## Areas of Impact

| Area | Impact | Risk (L/M/H) | Why |
|------|-----------|----------------|-----|
| `static/styles.css` | New selectors appended to existing media block | L | Purely additive, scoped under `.mobile-page`, no existing rule modified |
| `admin_login.html` | None (verified unaffected) | L | No `.mobile-page` class present; shared class names but scoping holds |
| Register → Book customer journey | Both entry points (`/register`, `/book`) now mobile-usable end-to-end | L | Consistent UX improvement, no functional/behavioral change to either flow |

**Contract changes:** None — no API/route/DB contract changes.

**Cross-cutting ripples:** None — no auth, telemetry, migration, or feature-flag surface touched.

## Cross-Cutting Concerns

- **Errors:** No change. Flash/error message rendering (`register.html:118-128`) untouched; inherits the same `.centered-content` width fix as the rest of the page, no dedicated styling (per REQ R5/decision #5).
- **Logging & metrics:** No change.
- **Auth / authz:** No change — `/register` and `/register/success` remain public, unauthenticated routes.
- **Performance:** Negligible — a few additional CSS rules in an already-loaded stylesheet, no new requests, no render-blocking change beyond what Brief-6 already introduced.
- **Security:** No new input handling, no new validation boundary.
- **Migrations / rollout:** None — static asset + template change, deploys via the normal Railway Dockerfile flow, no schema/env changes, trivially revertible (pure CSS/HTML diff).

## Architecture Decisions Log

| # | Decision | Alternatives | Chosen Because | Satisfies REQs |
|---|-----------|----------------|-------------------|-------------------|
| A1 | Reuse Brief-6 pattern exactly: viewport meta, `.mobile-page` scoping, extend existing `@media(max-width:480px)` block | Design a fresh mobile approach for this page | Proven, ships consistency across the register→book journey, no new tradeoffs to re-litigate | R1, R2, R3, N3 |
| A2 | Checkbox tap target via existing label-click + modest input padding, no visual resize | Wrap checkbox in a separately-padded invisible hit-box element | Label already covers most of the 44px area natively; avoids extra markup for marginal gain | R4 |
| A3 | Add `textarea` to the existing shared tap-target/font-size selector list rather than a standalone rule | New dedicated `.mobile-page textarea {...}` block | Less duplication, `textarea` behaves identically to `input`/`select` for this purpose | R2, R3 |
| A4 | New `.mobile-page .button` selector for register_success's CTA anchor | Reuse `.btn` (would require changing the template's class, out of scope) | `.button` class is unique to register_success.html (confirmed via grep), zero collision risk, no template class rename needed | R6 |
| A5 | No JS changes, no route changes | N/A — no JS exists in either template today | Neither template has a `<script>` tag; rotation/resize state preservation (R7) is native browser behavior, verify-only | R7 |
| A6 | `admin_login.html` explicitly out of scope, confirmed unaffected | Add `.mobile-page` to admin_login too "while we're at it" | Not requested, expands footprint, admin_login wasn't part of the customer journey this REQ targets | (Scope Boundary) |

## Risk & Stress-Test Scenarios

### Forward — runtime failure scenarios

| Scenario | How the Design Handles It |
|-------------|-------------------------------|
| Slow network mid-load | CSS is render-blocking as normal; page becomes usable once `styles.css` loads, same non-issue disclosed in Brief-6 — no new risk introduced |
| Two users submitting `/register` simultaneously | Unrelated to this change — backend registration logic is untouched, no new concurrency surface |
| Rollback needed post-deploy | Pure CSS/template diff, no DB/schema/env involved — trivially revertible via a single git revert, no data migration to unwind |

### Backward — regression risk per touched area (brownfield only)

| Touched area | What could regress | How we'd know / mitigation |
|-----------------|------------------------|----------------------------------|
| `templates/admin_login.html` | Media query rules leaking in despite shared class names | Verified no `.mobile-page` class present on its `<body>`; visual screenshot check at 375px pre/post change as a final confirmation during implementation |
| `main.py` register routes | None expected — no logic touched | Full test suite (`make test-db`) run after implementation, same as Brief-6 |
| Existing desktop register/register_success rendering | Visual shift above 480px breakpoint | New rules are entirely inside `@media(max-width:480px)`; desktop-width screenshot comparison during implementation (N3) |

## Open Questions

- Cross-browser verification (Safari iOS, Samsung Internet) may hit the same sandbox limitation (no WebKit) as Brief-6.
  - **Impact if unresolved:** Same disclosed gap pattern — cross-browser claims limited to Chromium-based automated testing.
  - **Suggested default:** Disclose explicitly in task verification evidence, consistent with how Brief-6 handled it; flag as manual/real-device follow-up.

## Out of Scope

- `admin_login.html` mobile treatment (reason: not part of the register→book customer journey targeted by this REQ; separate REQ if needed later)
- Deduping inline `<style>` blocks in register.html/register_success.html into styles.css (reason: same rationale as Brief-6 — risk of leaking into admin_login.html, not requested)
- Backend/route/validation changes to `/register` or `/register/success` (reason: presentation-layer-only fix)

---

# Tasks

## Task T1: Mobile-responsive register form and confirmation page

> **Status:** done — 12/12 verification checklist items evidenced (screenshots + Playwright DOM measurements at `docs/qa/register-brief/`). Two implementation deviations from A2 discovered and resolved (see below), both discussed and approved before proceeding.
> **Verification:** ui
> **Effort:** s
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** R1, R2, R3, R4, R5, R6, R7, N1, N2, N3
> **Footprint slice:** Modified: `templates/register.html`, `templates/register_success.html`, `static/styles.css` (full footprint — single task, no split)
> **High-risk areas touched:** None — all Areas of Impact rows are L risk

### Description

Add the Brief-6 mobile pattern (viewport meta, `.mobile-page` scoping, 480px breakpoint) to `register.html` and `register_success.html` so the fleet-signup form and account-code confirmation page are usable on mobile without pinch-zoom, matching the polish already shipped on `/book`. Extends the existing `@media(max-width:480px)` block in `static/styles.css` with new selectors for `textarea`, the checkbox+label row, and the register_success CTA button — element shapes Brief-6 didn't have.

### Verification Checklist

- **Viewport meta present, no zoom needed** — load `/register` and `/register/success` at 375-430px width; expected: no horizontal scroll, no pinch-zoom required to read/interact with any content _(verifies R1)_
- **Tap targets ≥44px, font-size ≥16px** — Playwright `getComputedStyle`/bounding-box measurement on input, select (none present, skip if absent), textarea, submit button; expected: all ≥44px height, text-entry fields ≥16px font-size _(verifies R2, R3)_
- **Checkbox tap target grows via padding, no visual distortion** — measure `input[type=checkbox]` bounding box pre/post change; expected: hit-area larger than baseline, visible checkbox size not dramatically different from desktop's `transform:scale(1.1)`; click on label text toggles checkbox _(verifies R4)_
- **Flash/error message readable at 375px** — trigger a flash (e.g. duplicate email submission) and screenshot at 375px; expected: message fits within viewport width, no overflow, no dedicated new styling needed beyond inherited page-width fix _(verifies R5)_
- **register_success account code + CTA button tappable, no zoom** — screenshot `/register/success` at 375-430px; expected: account code readable without zoom, `.button` CTA link ≥44px tap target _(verifies R6)_
- **Form state survives rotate/resize** — fill contact_name, areas textarea, tick acknowledge checkbox; resize viewport (portrait→landscape); expected: all three values/state still present after resize _(verifies R7)_
- **Both orientations usable** — screenshot both pages in portrait and landscape at a common phone size (e.g. 390x844 / 844x390); expected: no scroll/zoom needed in either orientation _(verifies N1)_
- **Cross-browser spot-check** — run the above in Chromium; if WebKit available, repeat there; expected: Chromium passes; WebKit/Samsung Internet gap explicitly disclosed if sandbox lacks it (matches Brief-6 precedent) _(verifies N2)_
- **Desktop unchanged** — screenshot both pages at ≥481px width pre- and post-change; expected: pixel-equivalent, no visual diff _(verifies N3)_
- **`admin_login.html` unaffected** — confirm no `.mobile-page` class present on its `<body>`; screenshot at 375px; expected: layout identical to pre-change baseline _(guards ARCH backward-regression risk for `templates/admin_login.html`)_
- **Long free-text input doesn't overflow** — enter a long string into "Company Name" and "Preferred Areas" textarea at 375px; expected: text wraps within the input/textarea bounds, no horizontal overflow or truncation _(REQ edge case)_
- **No test regressions** — `make test-db`; expected: all 347+ tests pass, no failures introduced

#### Testable Seams

None — no JS, no logic branches introduced; this is markup + CSS only, so no unit/component tests apply. All verification is visual/DOM-measurement based, consistent with `ui` mode.

### Implementation Deviations (discovered during build, discussed and approved)

1. **Checkbox tap target (deviates from A2 as originally worded):** the planned "grow via padding" approach doesn't work — Chromium ignores CSS padding for native `<input type="checkbox">` width (confirmed via computed-style/bounding-box measurement: padding had zero effect on width, only `min-height` from the general tap-target rule affected height). Resolved: dropped the padding rule; R4 is satisfied instead by the existing `<label for="acknowledge">` click target, which already covers a ~300×48px tappable region beside the small checkbox — verified via `checkboxLabelClickToggles: true`. No visual change to the checkbox itself, consistent with A2's spirit even though the mechanism differs from what A2 specified.
2. **Landscape breakpoint gap (new finding, scoped fix added):** `@media(max-width:480px)` doesn't fire on most phones in landscape (common landscape widths run 667–926px, past the breakpoint) — confirmed via measurement (CTA button height reverted to un-fixed 43px in a 667×375 viewport before the fix). This gap also exists in the already-shipped `book.html`/`booking_success.html` from Brief-6, undetected by that review; out of scope to fix there now. For this task, added a new `.register-page`-scoped `@media(max-height:480px) and (orientation:landscape)` block in `static/styles.css` (not touching the shared `.mobile-page` block `book.html` relies on) and a new `register-page` body class on both templates. This is a footprint addition beyond the original ARCH plan — flagged and approved by the developer before implementing.

### Implementation Notes

- **Module(s):** `templates/register.html`, `templates/register_success.html` (presentation), `static/styles.css` (styling) — no other module involved.
- **Pattern reference:** `templates/book.html` and `templates/booking_success.html` as they stand after Brief-6 (viewport meta placement, `.mobile-page` body class, `.mobile-page`-prefixed selectors in `static/styles.css`).
- **Key decisions (from Architecture Decisions Log):**
  - A1 — reuse Brief-6 pattern exactly (viewport meta without zoom-blocking attrs, `.mobile-page` scoping, extend existing media block).
  - A2 — checkbox tap target via label-click + modest input padding; do not visually resize the checkbox.
  - A3 — add `textarea` to the existing shared tap-target/font-size selector list rather than a standalone rule.
  - A4 — new `.mobile-page .button` selector for the register_success CTA; `.button` class confirmed unique to `register_success.html`, no collision.
  - A5 — no JS/route changes; R7 is native browser behavior, verify-only.
- **Libraries:** None — plain CSS, no new dependencies.
- **High-risk callouts:** None — all Areas of Impact rows are L risk. The one thing worth double-checking during implementation is the `admin_login.html` regression guard (Touched-but-not-changed), covered by its own checklist item above.

### Scope Boundaries

- Do NOT modify `templates/admin_login.html` (ARCH Out of Scope)
- Do NOT dedupe the inline `<style>` blocks in `register.html`/`register_success.html` into `static/styles.css` (ARCH Out of Scope — same rationale as Brief-6)
- Do NOT change `main.py` routes, validation, or registration business logic (ARCH Out of Scope — presentation-layer-only fix)
- Do NOT add a new `@media` block — extend the existing `@media(max-width:480px)` block only (ARCH A1/A3)
- Only implement the three footprint files listed below — no incidental cleanup elsewhere in `styles.css`

### Files Expected

**New files:** None.

**Modified files:**
- `templates/register.html` (add viewport meta, `class="mobile-page"` on `<body>`)
- `templates/register_success.html` (add viewport meta, `class="mobile-page"` on `<body>`)
- `static/styles.css` (extend existing `@media(max-width:480px)` block: `textarea` added to tap-target selector list; new `.mobile-page .inline-checkbox input[type="checkbox"]` rule; new `.mobile-page .button` rule)

**Must NOT modify:**
- `templates/admin_login.html` (silent-regression hotspot — covered by regression-guard checklist item above)
- `main.py` (out of scope per ARCH — pure render_template routes, no logic change needed)

---

_Status values: `not started` (defined, not picked up) | `in progress` (implementation underway) | `done` (verification evidence produced) | `blocked` (cannot proceed — see notes)._
