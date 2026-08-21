# Requirements: Mobile UI Enhancement for /book Page

> **Date:** 2026-08-03
> **Type:** feature (UX/responsive)
> **Source:** docs/Brief-6.md
> **Phase:** 1 of 5 (Requirement Engineering)

## Summary

The `/book` page and its full flow — booking form, station map, price/discount tables, booking summary, payment step, and confirmation page — currently render as a desktop-width layout squeezed into mobile viewports, forcing users to pinch-zoom to read text or tap controls. This REQ makes the entire booking flow usable and visually polished on common mobile phones (375–430px) in both portrait and landscape, without requiring zoom.

## Problem & Motivation

Internal/QA review (no confirmed customer complaint yet) found the booking flow hard to use on mobile — text too small, controls cramped, requiring pinch-zoom to read or tap anything. Trigger: caught during internal testing, and mobile traffic share has been growing, so the team wants to get ahead of it proactively rather than wait for customer complaints. If not fixed, mobile users likely have a harder time completing bookings, risking drop-off as mobile share grows.

## Users & Consumers

- Customers booking a refuel on their phone — need to read prices, fill the form, view the map, and pay without zooming or fighting cramped controls.
- Internal/QA team — need a mobile experience they can sign off on before treating mobile as a first-class supported path.

## Functional Requirements

| ID  | Requirement                                                                 | Acceptance Criterion                                                                                   |
|-----|------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------|
| R1  | Page renders at readable/tappable size by default on mobile, no pinch-zoom required to read text or use controls | On a 375–430px viewport, all text is legible and all controls usable without any pinch or double-tap zoom gesture |
| R2  | Station map is shown collapsible on mobile, expanded by default              | Map renders expanded on page load on mobile; a visible toggle lets the user collapse it to save scroll space |
| R3  | Price/discount tables reflow into stacked label:value cards on mobile        | On mobile viewport, each table row renders as its own card with labeled fields, no horizontal scrolling required |
| R4  | Payment QR code maintains a minimum scannable size on mobile                 | QR code renders at ≥200px on its shortest side regardless of viewport width, remains scannable by another device's camera |
| R5  | Date/time picker uses the native mobile OS picker, styled/sized to fit the mobile layout | Tapping the date/time field opens the native mobile date/time picker; the trigger input/button meets the R6 tap-target size and its label doesn't overflow or clip |
| R6  | All interactive controls (buttons, inputs, dropdowns, toggles) meet a minimum tap-target size | Every tappable element measures at least 44x44px on mobile viewports |
| R7  | Form data (all entered fields, selections, in-progress state) is preserved when the user rotates the device or resizes the viewport mid-booking | Rotating from portrait to landscape (or resizing) at any step retains all previously entered values and current step, only the layout reflows |
| R8  | Booking form, summary, payment, and confirmation pages are all responsive and usable on mobile | Each of the four areas independently passes manual test on target devices/browsers (R-Devices) without requiring zoom or exhibiting cramped/clipped layout |
| R9  | Slow/spotty mobile network shows a loading state for map and price data while keeping the rest of the form usable | On throttled 3G, map and price sections show a visible loading indicator; the rest of the form (e.g., name/mobile fields) remains interactive while those sections load |

## Non-Functional Requirements

| ID  | Requirement                                                       | Acceptance Criterion                                                                 |
|-----|---------------------------------------------------------------------|-----------------------------------------------------------------------------------------|
| N1  | Layout supports both portrait and landscape mobile orientation      | Manual test on target devices confirms usable layout (no clipped/overlapping elements) in both orientations |
| N2  | Verified on target mobile browsers                                  | Manual pass on Mobile Safari (iOS), Chrome (Android), and Samsung Internet (Android) confirms R1–R9 |
| N3  | Desktop layout/behavior unchanged                                    | Desktop viewport rendering and functionality are visually and behaviorally identical to pre-change baseline |

## Behaviors & Domain Rules

**Booking form:** Name/mobile, fuel type, station selector, total fuel amount, refuel date/time, driver/vehicle preset selection — all fields must be legible and tappable at mobile width without zoom.

**Station map:** Embedded map is a large element on a small screen. On mobile it becomes collapsible (starts expanded per R2) so it doesn't force excessive scrolling for users who don't need it, but isn't hidden by default since users do need to check station locations.

**Price/discount tables:** Desktop wide tables ("Biodiesel — Live Pricing & Discounts", etc.) must never require horizontal scrolling on mobile — they restructure into stacked cards (R3), one card per row, each field labeled.

**Payment/confirmation:** The InstaPay QR code is the only way a customer pays — it must stay scannable (R4) regardless of how the rest of the page scales down.

**Why these rules matter:**
- Collapsible-but-expanded map (R2) balances "user needs to see it" against "don't force endless scrolling on a small screen."
- QR minimum size (R4) exists because a QR that scales down proportionally with a narrow phone screen may become too small/dense for a camera to resolve — this is a hard floor, not a proportional scale.
- Tap-target minimum (R6) prevents mis-taps on adjacent small buttons, a common mobile complaint.
- Data preservation on rotate (R7) prevents users losing form progress — a rotate/resize is a layout event, not a navigation event, and must not be treated as a reset.

**Common mistakes:**
- A developer's first attempt often makes the QR code scale down with the rest of the page (flex/percentage width) — this breaks scannability at narrow widths; it needs an explicit min-size floor instead.
- Reflowing tables into cards purely with CSS `overflow-x: scroll` looks like a fix but doesn't meet R3 — the requirement is stacked cards, not scrollable tables.
- Naively re-rendering the form component on orientation change (rather than just reflowing CSS) can wipe React/JS state — must confirm state layer survives resize, not just that CSS reflows correctly.

## Edge Cases & Failure Modes

| Scenario                                                                 | Decision                                                                 | Rationale                                                                 |
|---------------------------------------------------------------------------|-----------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| User rotates phone or resizes browser mid-form-fill                       | Preserve all entered data; only layout reflows                              | Rotation is a common real-world action; losing progress would be a regression |
| Slow/spotty mobile network while map or price data loads                  | Show loading state/spinner for those sections; rest of form stays usable    | Prevents a frozen/blank page perception on 3G or poor connectivity            |
| Landscape orientation on mobile                                           | Must be fully supported, not just portrait                                  | User explicitly requires both orientations in scope                          |
| QR code on very narrow viewport (e.g., 375px)                             | Enforce ≥200px minimum QR size even if it requires more vertical space or overflow beyond a purely proportional layout | A QR too small to scan blocks payment entirely — functional blocker over cosmetic fit |
| Native date/time picker rendering differences across iOS/Android/Samsung  | Rely on OS-native picker (no custom widget); only the trigger control is styled | Avoids reinventing picker UX and cross-browser inconsistency risk explicitly ruled out of scope |
| Tablet-width viewport (~768px)                                            | Not an explicit target; falls back to whichever of phone/desktop layout is closer, not separately verified | User explicitly scoped this REQ to phones only (375–430px)                    |

## Decisions Log

| #   | Decision                                                                 | Alternatives Considered                                  | Chosen Because                                                             |
|-----|-----------------------------------------------------------------------------|---------------------------------------------------------------|----------------------------------------------------------------------------------|
| 1   | Bundle booking form, summary, payment, and confirmation into one REQ         | Split into separate REQs per page                              | Same root cause (viewport/zoom), same page flow, one sprint-sized effort         |
| 2   | "Done" = no pinch-zoom needed AND visual polish matching desktop, not just technical usability | Bare minimum "technically usable" only                          | Team wants mobile treated as a first-class, intentionally designed experience, not an afterthought |
| 3   | Target only common phone widths (375–430px); tablet not explicitly verified | Explicit separate tablet breakpoint                             | Keeps scope sprint-sized; tablet can fall back to nearest existing layout        |
| 4   | "Auto-zoom" ask in the brief is interpreted as fixing the root cause (proper viewport meta + ≥16px input font-size) rather than literally zooming the page in on load | Literal auto-zoom-in behavior on page load                     | Root-cause fix is the standard, correct solution; literal auto-zoom is not a real browser behavior users want |
| 5   | Map is collapsible on mobile, expanded by default                           | Always-expanded (no toggle); collapsed by default              | Balances visibility (user needs to check station location) against scroll fatigue |
| 6   | Price/discount tables become stacked cards on mobile                        | Keep table with horizontal scroll                               | Avoids horizontal scrolling, a common mobile UX complaint                        |
| 7   | QR code gets an explicit minimum scannable size floor                       | Pure proportional scaling with rest of page                     | Prevents QR becoming too small to scan on narrow phones, which would block payment |
| 8   | Both portrait and landscape orientations required                          | Portrait-only                                                    | User explicitly wants both verified, not just portrait                          |
| 9   | Tap targets minimum 44x44px (Apple HIG)                                    | 48x48px (Material Design)                                       | User chose Apple HIG as the standard for this REQ                                |
| 10  | Form data preserved across rotate/resize                                    | No explicit guarantee, rely on default browser behavior         | Prevents user frustration/data loss from a routine action like rotating a phone |
| 11  | Native OS date/time picker retained, just styled/sized correctly            | Build a custom in-page picker                                    | Avoids extra complexity of custom widget across all mobile browsers; native picker already functional |
| 12  | Verify on Mobile Safari, Chrome (Android), and Samsung Internet             | Safari + Chrome only                                             | Samsung Internet has meaningful share in some markets; user wants it covered explicitly |
| 13  | Loading state required for map/price data on slow network; rest of form stays usable | No explicit slow-network requirement                            | Prevents page feeling frozen/broken on 3G or poor connectivity                    |

## Scope Boundaries

### In Scope
- `/book` page: booking form, station map, price/discount tables, booking summary, payment step (InstaPay QR), confirmation page — full flow, mobile responsive.
- Portrait and landscape orientation on mobile.
- Tap target sizing, native date/time picker styling, QR minimum scannable size.
- Loading states for map/price data on slow networks.
- Verification on Mobile Safari (iOS), Chrome (Android), Samsung Internet (Android).
- Preserving form state across rotation/resize.

### Out of Scope
- Explicit tablet-specific breakpoint/testing (reason: scoped to phone widths only per decision #3; tablet falls back to nearest layout).
- Custom-built date/time picker widget (reason: native OS picker retained per decision #11).
- Desktop layout changes (reason: this REQ is mobile-only; desktop must remain unchanged per N3).
- Any other pages outside `/book` flow (reason: brief and interview scoped strictly to the booking flow).

## Open Questions

- Exact minimum QR pixel size (200px used as a working number) — should be confirmed against the actual InstaPay QR image's error-correction density before implementation.
  - **Impact if unresolved:** architecture phase might pick a size that's technically "big enough" per this REQ but still fails to scan in practice for a specific QR encoding.
  - **Suggested default:** 200px minimum side, validated with an actual scan test during implementation.
- No specific customer complaint data exists yet — mobile traffic growth was cited but not quantified.
  - **Impact if unresolved:** hard to prioritize this against other work with concrete numbers.
  - **Suggested default:** proceed on internal QA/proactive rationale as agreed; revisit with analytics if reprioritization is needed later.

---
_This requirements document is the input for the **plan-architecture** skill._
_Next step: `/plan-architecture from: specs/requirements/REQ-brief-6.md`_
