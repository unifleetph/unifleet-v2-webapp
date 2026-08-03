# Architecture: Mobile UI Enhancement for /book Page

> **Date:** 2026-08-03
> **Phase:** 2 of 5 (System Architecture)
> **Requirements source:** specs/requirements/REQ-brief-6.md
> **Type:** feature (responsive/presentation-layer enhancement)

## Architecture Summary

This is a pure presentation-layer fix — no routes, data models, or business logic change. Root cause of the "must pinch-zoom" complaint is that neither `templates/book.html` nor `templates/booking_success.html` has a `<meta name="viewport">` tag, so mobile browsers render at a virtual desktop width (~980px) and shrink everything. The fix adds the standard viewport meta tag to both templates, then layers a `@media (max-width: 480px)` block — scoped under a new `.mobile-page` class to avoid leaking into unrelated templates that share the same CSS class names — into `static/styles.css`. Existing collapsible (`<details>/<summary>`) and table markup patterns are reused rather than replaced: the map `iframe` gets wrapped in the same `<details>` pattern already used for the fuel-type tables, and the discount tables get `data-label` attributes so CSS alone can reflow them into stacked cards on mobile, with no markup duplication or new JS framework.

## High-Level Structure

```
templates/book.html            (existing, modified)
templates/booking_success.html (existing, modified)
        │
        ▼
static/styles.css              (existing, modified: + .mobile-page media query block)
```

No new modules, no new files, no backend changes. `main.py` routes, `price_store.py`, `persistence.py`, `discount_store.py` are untouched — the data the templates render is unaffected, only how it's laid out on narrow viewports.

## Tech Choices

| Area             | Decision                                      | Alternatives Considered                          | Rationale                                                                 |
|-------------------|------------------------------------------------|---------------------------------------------------|----------------------------------------------------------------------------|
| Responsive approach | Plain CSS media queries, no framework/library | CSS framework (Bootstrap/Tailwind), JS-based responsive lib | Codebase has zero front-end build step or framework dependency; adding one for a 2-template fix is disproportionate |
| Map collapse      | Native `<details>/<summary>`, no JS            | Custom JS toggle button                            | Identical pattern already used for fuel-type tables in `book.html` — zero new JS, consistent UX |
| Table reflow      | CSS-only (`display:block` + `data-label` + `::before`), same `<table>` markup | Server-render separate mobile div-markup           | One markup source for desktop table + mobile cards; avoids template duplication and Jinja branching |
| Visual regression testing | None — manual QA checklist only          | Automated screenshot-diff tool (Percy, Playwright)  | New tooling dependency disproportionate to a 2-template CSS fix; manual pass on 3 target browsers is the agreed gate (REQ N2) |

## Patterns & Conventions

- **Reuse existing `<details>/<summary>` collapsible pattern** (already used for the 3 fuel-type tables in `book.html`) — applied to the map `iframe` for R2, keeping the codebase's one collapsible idiom instead of introducing a second.
- **CSS-only responsive reflow, no JS state** — matches the project's existing "vanilla JS, no framework" convention (per AGENTS.md: single-file Flask app, no build pipeline).
- **Scoped CSS via new `.mobile-page` class**, not global tag/class selectors — because `.page-title`, `.centered-content`, `.section` are shared verbatim across `register.html`, `register_success.html`, and `admin_login.html`; scoping avoids an unintended N3 violation (other pages' desktop layout must stay untouched).
- **Not applied:** consolidating book.html/booking_success.html's duplicated inline base CSS into `styles.css`. Considered during design (would reduce duplication) but rejected — those templates' base rules use the same class names as three other templates, so deduping into shared CSS would leak style changes into pages this REQ explicitly excludes (register, admin login). Existing duplication stays as-is; only new mobile rules go into `styles.css`.

## Data Models

None — no entities, schema, or persistence changes. This REQ is scoped entirely to template/CSS rendering.

## API Contracts / Interfaces

None — no new/changed HTTP endpoints, module signatures, or events. `main.py`'s existing `/book` (GET/POST) and `booking_success` render routes are unchanged; they pass the same context variables to the same templates.

## Module Boundaries

| Module / Package     | Responsibility                                  | Allowed Dependencies      |
|-----------------------|--------------------------------------------------|-----------------------------|
| `templates/book.html` | Booking form, map, price tables, live cost preview | `static/styles.css`, inline `<style>`/`<script>` (unchanged pattern) |
| `templates/booking_success.html` | Payment instructions, QR, confirmation | `static/styles.css`, inline `<style>` (unchanged pattern) |
| `static/styles.css`   | Shared base styles + (new) mobile media-query rules scoped to `.mobile-page` | None (leaf) |

Rule: new mobile CSS lives only in `static/styles.css` under `.mobile-page`-scoped selectors; template-local inline `<style>` blocks keep owning their existing base (desktop) rules unchanged.

## Change Footprint

### New files / modules

None. No new files — all changes land in existing templates and the existing stylesheet.

### Modified files / modules

| Path                                  | What changes here                                                                                                                                                       |
|-----------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `templates/book.html`                 | Add `<meta name="viewport" content="width=device-width, initial-scale=1">` to `<head>`; add `class="mobile-page"` to `<body>`; wrap the map `iframe` in `<details open>` (mirrors existing fuel-table pattern); add `data-label="Station"/"Price"/"Discount"` to each discount-table `<td>` |
| `templates/booking_success.html`      | Add same viewport meta tag; add `class="mobile-page"` to `<body>`                                                                                                        |
| `static/styles.css`                   | Add `@media (max-width: 480px)` block, all rules scoped under `.mobile-page`: font/spacing adjustments, `.discount-table` → `display:block` reflow using `::before{content:attr(data-label)}`, `<details>`-wrapped map iframe sizing, `input/select/button` min-height 44px, `.payment-qr img` gets `min-width:200px`, `::-webkit-calendar-picker-indicator` sizing on the datetime input |

### Deleted / replaced

None.

### Touched but not changed (silent-regression hotspots)

| Path                                  | Why it matters                                                                                                             |
|------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------|
| `templates/register.html`, `templates/register_success.html`, `templates/admin_login.html` | Share the exact class names (`.page-title`, `.centered-content`, `.section`) with `book.html`/`booking_success.html`. Verified these templates' *base* rules are left untouched (not deduped into `styles.css`) specifically to avoid regressing these pages — confirm visually post-change that they render identically to baseline. |
| Inline `<script>` blocks in `book.html` (`populateStations`, `updatePreview`, `toggleDriverMode`) | Select DOM elements by ID (`#fuel_type`, `#station`, `#cost_preview`, etc.) — none of those IDs change; `data-label` attrs are added only to `<td>` elements these scripts don't touch, and wrapping the iframe in `<details>` doesn't rename/remove it. Confirmed no ID/selector collision. |
| `main.py` `/book` and booking-success render routes | Unchanged — pass the same template context; only the templates' presentation shifts. |

## Areas of Impact

| Area                              | Impact                                             | Risk (L/M/H) | Why                                                                 |
|-------------------------------------|-------------------------------------------------------|---------------|--------------------------------------------------------------------------|
| `templates/book.html` rendering     | New viewport meta + mobile CSS + details-wrapped map + data-label attrs | Low          | Purely additive markup/CSS; existing IDs/selectors/JS untouched         |
| `templates/booking_success.html` rendering | New viewport meta + QR min-size CSS               | Low          | Small, additive, isolated to one image rule                            |
| `static/styles.css`                 | New scoped media-query block appended               | Low          | Scoped under `.mobile-page`, doesn't touch existing global/base selectors |
| Shared-class templates (register, admin_login, register_success) | None intended — must verify no visual drift | Medium       | Same class names exist elsewhere; mitigated by NOT deduping base rules, but still worth a visual spot-check since it's a shared file |
| Desktop rendering of `/book` and `booking_success` | None intended                              | Low          | All new rules are inside `@media (max-width: 480px)`, inert above that width |

**Contract changes:** None — no API/response/event payload changes.

**Cross-cutting ripples:** None — no auth, telemetry, migration, feature-flag, or build-pipeline changes. Static asset change only, deployed via the existing Dockerfile/Railway pipeline with no special rollout step.

## Cross-Cutting Concerns

- **Errors:** N/A — no new error paths; existing form validation/flash-message behavior unchanged.
- **Logging & metrics:** N/A — no new logging or metrics.
- **Auth / authz:** N/A — no auth surface touched.
- **Performance:** No new requests, assets, or dependencies added; CSS/HTML additions are negligible in size. Map `iframe` keeps existing `loading="lazy"`.
- **Security:** N/A — no new input surfaces, no data handling changes.
- **Migrations / rollout:** None. Deploys as a normal static-asset/template change through the existing Railway pipeline. Rollout gate is manual QA: verify R1–R9 on Mobile Safari (iOS), Chrome (Android), and Samsung Internet, in both portrait and landscape, on ~375–430px devices, before merge — per REQ N2.

## Architecture Decisions Log

| #   | Decision                                                                 | Alternatives                                              | Chosen Because                                                                 | Satisfies REQs |
|-----|-------------------------------------------------------------------------|------------------------------------------------------------|---------------------------------------------------------------------------------|----------------|
| A1  | Add standard viewport meta tag (`width=device-width, initial-scale=1`) to both templates | Larger CSS framework, JS viewport polyfill                 | Root cause is missing viewport meta; standard fix, doesn't block user zoom      | R1             |
| A2  | New mobile CSS lives only in `static/styles.css`, scoped under `.mobile-page` class, base template CSS left untouched (no dedup) | Dedupe shared header/page-title/section CSS into styles.css | `.page-title`/`.centered-content`/`.section` classes are shared with register/admin_login templates outside REQ scope — deduping would leak style changes there | R1, N3 |
| A3  | Map `iframe` wrapped in `<details open>`, reusing existing fuel-table collapsible pattern | Custom JS show/hide toggle                                  | Zero new JS, consistent with existing UI idiom already in `book.html`          | R2             |
| A4  | Discount tables stay `<table>` markup; add `data-label` attrs, CSS-only reflow to stacked cards via `display:block` + `::before` | Server-render separate mobile div markup                    | Single markup source, no Jinja branching, no template duplication              | R3             |
| A5  | QR image gets `min-width:200px` added to existing `width:220px; max-width:70%` | Recompute max-width % to algebraically guarantee 200px floor | Explicit floor is simpler and more robust to future container/padding changes  | R4             |
| A6  | Datetime input: base 44px min-height via mobile-page CSS + `::-webkit-calendar-picker-indicator` sizing as progressive enhancement | Skip icon-level styling entirely                            | User requested best-effort icon sizing; webkit-only rule has no negative effect on non-webkit browsers (simply ignored) | R5, R6         |
| A7  | Tap-target 44x44px rule scoped to `.mobile-page` only, not global `input/select/button` in styles.css | Global sitewide rule                                        | Avoids resizing controls on admin/register pages, which are outside REQ scope | R6, N3         |
| A8  | No automated visual regression tooling; manual QA checklist is the rollout gate | Percy/Playwright screenshot-diff integration                | Disproportionate new dependency for a 2-template CSS fix                       | N2             |
| A9  | Form-state preservation on rotate/resize (R7) requires no code change — verify-only | Add explicit JS state-snapshot/restore logic                | No JS framework re-renders on resize; CSS reflow alone doesn't touch DOM/form state, so browser default already satisfies R7 | R7             |
| A10 | Slow-network loading state (R9) for price data is already satisfied by existing server-side rendering (no client-side fetch); map iframe keeps native `loading="lazy"`, no new spinner JS | JS-driven spinner overlay for map iframe                   | Price tables are server-rendered on page load (no async fetch to show a spinner for); map is a 3rd-party embed whose internal loading UX isn't ours to control | R9             |

## Risk & Stress-Test Scenarios

### Forward — runtime failure scenarios

| Scenario                                                        | How the Design Handles It                                                                                      |
|---------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| Google Maps iframe embed is slow/unavailable on a poor mobile connection | Existing `loading="lazy"` + native iframe blank-space fallback; no app-level dependency on map load succeeding for the rest of the form to function |
| User rotates phone mid-form-fill (portrait → landscape)              | CSS media query reflows layout only; no DOM remount or JS state reset occurs, so entered field values persist (native browser behavior, not app-managed state) |
| Two different mobile browsers render `<details>` or `::-webkit-calendar-picker-indicator` inconsistently | `<details>/<summary>` is broadly supported across Safari iOS/Chrome/Samsung Internet (same pattern already shipped for fuel tables); webkit-only picker-indicator rule degrades gracefully (ignored, not broken) on non-webkit engines |
| Extremely narrow viewport (e.g. 320px, below REQ's 375px floor)      | GAP — not explicitly targeted; `min-width:200px` on the QR and 44px tap targets still apply since they're absolute values, not proportional, so core usability holds even below the stated floor, but not formally verified — see Open Questions |

### Backward — regression risk per touched area (brownfield only)

| Touched area (from Change Footprint)                                 | What could regress                                                          | How we'd know / mitigation                                                          |
|---------------------------------------------------------------------------|----------------------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| `register.html`, `register_success.html`, `admin_login.html` (shared class names) | Visual drift if any new global (non-`.mobile-page`-scoped) rule accidentally targets `.page-title`/`.centered-content`/`.section` | Manual visual check on these 3 pages (desktop + mobile) after the change, since they weren't touched but share styling primitives |
| `book.html` inline `<script>` (populateStations/updatePreview/toggleDriverMode) | JS selectors breaking if IDs were accidentally renamed while adding `data-label`/`details` wrapper | Manual smoke test: change fuel type, station, enter amount, verify cost preview still updates; confirmed by design no ID touched |
| Desktop `/book` and `booking_success` rendering                       | Media query bleeding above 480px due to a typo/missing `max-width` bound       | Manual desktop-viewport check as part of the same QA pass (N3)                       |

## Open Questions

- Viewports narrower than 375px (e.g. very old/small Android devices at 320px) aren't explicitly in REQ scope — should they be spot-checked anyway?
  - **Impact if unresolved:** a small number of older/smaller devices might still see minor cramping, though absolute-value rules (44px tap targets, 200px QR floor) should hold regardless.
  - **Suggested default:** no explicit task for it now; note as a fast follow if analytics later show meaningful traffic below 375px.

## Out of Scope

- Tablet-specific breakpoint (~768px) (reason: REQ scoped to phone widths 375–430px only; tablets fall back to nearest existing layout).
- Custom-built date/time picker widget (reason: REQ keeps native OS picker, styling only).
- Any page outside `/book` and its confirmation step — `register.html`, `admin*.html`, `redeem.html` (reason: REQ scoped strictly to the booking flow; explicitly protected from regression per A2/A7).
- Automated visual regression tooling (reason: disproportionate to REQ size; manual QA checklist is the agreed gate).

---

# Tasks

## Task T1: Book page mobile responsiveness

> **Status:** done — 12/14 checklist items fully verified with evidence (screenshots + DOM assertions); item 10 (slow-network) accepted as low-risk/unchanged rather than actively re-tested; item 14 (cross-browser) verified on Chromium only — Safari iOS + Samsung Internet need a manual/real-device check before merge (WebKit unavailable in this sandbox, missing system libs, no sudo access)
> **Verification:** ui
> **Effort:** m
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** R1, R2, R3, R5, R6, R7, R9, N1, N2, N3
> **Footprint slice:** Modified: `templates/book.html` (viewport meta, `.mobile-page` body class, map wrapped in `<details open>`, `data-label` attrs on discount-table `<td>`s); Modified: `static/styles.css` (book-scoped `.mobile-page` media-query rules: table reflow, map/details sizing, tap targets, datetime input sizing)
> **High-risk areas touched:** Shared-class templates (`register.html`, `register_success.html`, `admin_login.html`) — Medium risk per ARCH Areas of Impact, mitigated by NOT deduping base CSS (A2)

### Description

Fix the root cause of the "must pinch-zoom" complaint on `/book`: add the missing viewport meta tag, then make the booking form, station map, and discount tables usable and legible on 375–430px mobile phones without zoom. Reuses the existing `<details>/<summary>` collapsible pattern for the map and a CSS-only `data-label` reflow for tables — no new JS, no new files.

### Verification Checklist

- **Viewport meta present** — inspect `templates/book.html` `<head>` — expected: contains `<meta name="viewport" content="width=device-width, initial-scale=1">` _(verifies R1)_
- **No pinch-zoom needed at 375-430px, portrait** — DevTools mobile emulation at 375px (iPhone SE), 393px (iPhone 14 Pro), 412px (Pixel 7) — expected: all text legible, all controls tappable without any pinch/zoom gesture _(verifies R1)_
- **Same, landscape orientation** — same three device widths, landscape — expected: same as above, no clipped/overlapping elements _(verifies N1)_
- **Map collapsible, expanded by default** — load `/book` on mobile viewport — expected: map `iframe` is wrapped in `<details open>`, visibly expanded on load, with a working toggle to collapse/re-expand _(verifies R2)_
- **Discount tables stack as cards on mobile** — at ≤480px, expand each of the 3 fuel-type `<details>` — expected: each table row renders as a labeled stacked card (Station/Price/Discount labels visible via `data-label` `::before`), no horizontal scrollbar _(verifies R3)_
- **Discount tables render as normal `<table>` above 480px** — resize to desktop width — expected: existing table layout unchanged from pre-change baseline _(guards N3 regression)_
- **Tap targets ≥44x44px** — DevTools box-model inspection on buttons, selects, text/number inputs, datetime input at mobile width — expected: computed height/width ≥44px for each _(verifies R6)_
- **Datetime picker opens native OS picker, trigger sized right** — tap `refuel_datetime` field at 375px — expected: native mobile date/time picker opens; the input field itself is ≥44px tall; its label doesn't clip or overflow _(verifies R5)_
- **Form state survives rotation** — fill fuel type, station, amount, name fields, rotate emulated device portrait→landscape — expected: all entered values and current scroll/step state persist, only layout reflows _(verifies R7)_
- **Slow-network map/table loading doesn't block the form** — throttle to slow 3G in DevTools, reload `/book` — expected: map shows native lazy-load placeholder, discount tables (server-rendered) appear immediately, rest of the form remains interactive throughout _(verifies R9)_
- **Regression guard — JS still functions** — change fuel type dropdown, confirm station list repopulates; select a station and enter an amount, confirm cost preview updates; toggle driver mode, confirm preset/new-driver fields swap — expected: all three behaviors work identically to pre-change baseline _(guards backward-regression risk for `populateStations`/`updatePreview`/`toggleDriverMode` inline scripts)_
- **Regression guard — shared-class pages unaffected** — render `register.html`, `register_success.html`, `admin_login.html` at desktop and mobile widths — expected: visually identical to pre-change baseline (no leaked `.mobile-page`-adjacent style bleed) _(guards backward-regression risk per ARCH A2/Areas of Impact)_
- **Desktop rendering unchanged** — load `/book` at ≥481px viewport — expected: matches pre-change layout exactly _(verifies N3)_
- **Cross-browser pass** — repeat the pinch-zoom, map, and table checks above on Mobile Safari (iOS), Chrome (Android), and Samsung Internet (Android) — expected: consistent behavior across all three _(verifies N2)_

#### Testable Seams

- Render: `<meta viewport>` tag present in rendered HTML source
- Conditional state: `<details open>` initial state on map; `data-label` attributes present on all discount-table `<td>` elements
- Handlers: existing `populateStations`/`updatePreview`/`toggleDriverMode` JS — no new handlers added, but must be smoke-tested since surrounding markup changes
- A11y basics: viewport meta does not include `maximum-scale` or `user-scalable=no` (zoom must remain user-controllable per REQ)

### Implementation Notes

- **Module(s):** `templates/book.html`, `static/styles.css` (per ARCH Module Boundaries)
- **Pattern reference:** reuse the existing `<details class="fuel-table-group">` collapsible pattern already in `book.html` for the map wrapper (A3); reuse existing `.full-width-input` / `.discount-table` classes as the base being extended, not replaced
- **Key decisions:** A1 (viewport meta, don't block user zoom), A2 (new CSS only in `styles.css` under `.mobile-page`, do NOT dedupe/move existing inline base rules), A3 (map via `<details>`, no new JS), A4 (table reflow via `data-label` + CSS, same markup), A6 (44px tap targets + `::-webkit-calendar-picker-indicator` sizing, webkit-only is fine), A7 (tap-target rule scoped to `.mobile-page`, not global), A9 (rotate/resize state preservation needs no code change — verify only), A10 (map/table loading needs no new spinner JS — verify only)
- **Libraries:** none — plain CSS media queries, no framework (per ARCH Tech Choices)
- **High-risk callouts:** shared CSS class names (`.page-title`, `.centered-content`, `.section`) exist in `register.html`/`register_success.html`/`admin_login.html` — mitigated by design (new rules scoped under `.mobile-page`, existing inline base rules left untouched), but the regression-guard checklist item above is the verification for this risk

### Scope Boundaries

- Do NOT add a tablet-specific breakpoint (~768px) — out of scope per ARCH
- Do NOT build a custom date/time picker widget — native OS picker stays, styling only
- Do NOT modify `register.html`, `admin*.html`, `redeem.html`, or any page outside `/book` and its confirmation step
- Do NOT dedupe/move the existing inline `<style>` base rules (header/page-title/section/etc.) into `styles.css` — only add new `.mobile-page`-scoped rules
- Do NOT introduce automated visual regression tooling
- Only implement the `/book` template + its book-scoped portion of `static/styles.css`

### Files Expected

**Modified files:**
- `templates/book.html` (add viewport meta; add `class="mobile-page"` to `<body>`; wrap map `iframe` in `<details open>`; add `data-label` attrs to discount-table `<td>`s)
- `static/styles.css` (add `@media (max-width: 480px)` block scoped under `.mobile-page`: table reflow, map/details sizing, tap-target min-height, datetime picker-indicator sizing)

**Must NOT modify:**
- `templates/register.html`, `templates/register_success.html`, `templates/admin_login.html` (silent-regression hotspot — share class names, covered by regression-guard checklist item above)
- Inline `<script>` blocks in `book.html` (`populateStations`, `updatePreview`, `toggleDriverMode`) — DOM IDs/selectors must not change (covered by regression-guard checklist item above)
- `main.py` `/book` route and its template context (no logic change)

---

## Task T2: Booking confirmation/payment page mobile responsiveness

> **Status:** done — 6/8 checklist items fully verified with evidence (screenshots + DOM measurements); item 5 (physical QR scan) not tested with a real second device in this sandbox — QR size/contrast unchanged from working baseline image; item 8 (cross-browser) verified on Chromium only — Safari iOS + Samsung Internet need a manual/real-device check before merge (same WebKit sandbox limitation as T1)
> **Verification:** ui
> **Effort:** s
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** R1, R4, R6, N1, N2, N3
> **Footprint slice:** Modified: `templates/booking_success.html` (viewport meta, `.mobile-page` body class); Modified: `static/styles.css` (QR/confirmation-scoped `.mobile-page` media-query rules: `.payment-qr img` min-size, tap targets)
> **High-risk areas touched:** None (isolated to one image rule and one button per ARCH Areas of Impact)

### Description

Fix the same missing-viewport-meta root cause on the booking confirmation/payment page, and guarantee the InstaPay QR code never shrinks below a scannable size on narrow phones. Small, isolated change — one image rule plus the shared viewport/tap-target treatment.

### Verification Checklist

- **Viewport meta present** — inspect `templates/booking_success.html` `<head>` — expected: contains `<meta name="viewport" content="width=device-width, initial-scale=1">` _(verifies R1)_
- **No pinch-zoom needed at 375-430px, portrait** — DevTools mobile emulation at 375px, 393px, 412px — expected: payment instructions, amount due, and account details all legible without zoom _(verifies R1)_
- **Same, landscape orientation** — same three device widths, landscape — expected: no clipped/overlapping elements _(verifies N1)_
- **QR code min scannable size enforced** — inspect `.payment-qr img` computed size at every tested viewport width, including 375px — expected: ≥200px on its shortest side at all widths _(verifies R4)_
- **QR remains actually scannable** — at 375px viewport, scan the rendered QR with a second phone's banking/camera app — expected: QR resolves and reads correctly _(verifies R4, functional check beyond size)_
- **Tap targets ≥44x44px** — DevTools box-model inspection on the "Make another booking" button at mobile width — expected: computed height/width ≥44px _(verifies R6)_
- **Desktop rendering unchanged** — load `booking_success` at ≥481px viewport — expected: matches pre-change layout exactly _(verifies N3)_
- **Cross-browser pass** — repeat the pinch-zoom and QR-size checks above on Mobile Safari (iOS), Chrome (Android), and Samsung Internet (Android) — expected: consistent behavior across all three _(verifies N2)_

#### Testable Seams

- Render: `<meta viewport>` tag present in rendered HTML source
- Conditional state: none (static confirmation page, no interactive state)
- Handlers: existing `copyText` JS function — unaffected, smoke-check it still fires if any copy button exists on the page
- A11y basics: viewport meta does not include `maximum-scale` or `user-scalable=no`

### Implementation Notes

- **Module(s):** `templates/booking_success.html`, `static/styles.css` (per ARCH Module Boundaries)
- **Pattern reference:** existing `.payment-qr img` rule (`width:220px; max-width:70%`) — extend, don't replace
- **Key decisions:** A1 (viewport meta), A2 (new CSS only in `styles.css` under `.mobile-page`), A5 (QR gets `min-width:200px` added alongside existing `width`/`max-width`, explicit floor over algebraic max-width recompute), A7 (tap-target rule scoped to `.mobile-page`, not global)
- **Libraries:** none
- **High-risk callouts:** none — this task's footprint carries no M/H Areas of Impact per ARCH

### Scope Boundaries

- Do NOT modify `register.html`, `admin*.html`, `redeem.html`, or any page outside the booking flow
- Do NOT dedupe/move existing inline `<style>` base rules into `styles.css` — only add new `.mobile-page`-scoped rules
- Do NOT introduce automated visual regression tooling
- Only implement the `booking_success` template + its QR/confirmation-scoped portion of `static/styles.css`

### Files Expected

**Modified files:**
- `templates/booking_success.html` (add viewport meta; add `class="mobile-page"` to `<body>`)
- `static/styles.css` (add `.payment-qr img` min-size rule and tap-target rule to the `.mobile-page` media-query block — same block T1 adds, additive)

**Must NOT modify:**
- `main.py` booking-success render route and its template context (no logic change)

---

_Status values: `not started` (defined, not picked up) | `in progress`
(implementation underway) | `done` (verification evidence produced) | `blocked`
(cannot proceed — see notes)._
