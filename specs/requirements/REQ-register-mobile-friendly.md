# Requirements: Register Page Mobile Enhancement

> **Date:** 2026-08-04
> **Type:** feature
> **Source:** verbal brief ("This register page and its content make mobile friendly as /book page")
> **Phase:** 1 of 5 (Requirement Engineering)

## Summary

`/register` (fleet signup form) and `/register/success` (account-code confirmation page) need mobile-friendly treatment matching the work already done on `/book` and `/book/success` in Brief-6. Same root cause: no viewport meta tag, form/content laid out at desktop width, forcing pinch-zoom on phones (375-430px).

## Problem & Motivation

Same complaint as Brief-6: internal/QA feedback that pages require pinch-zoom to use on mobile. `/register` is the entry point to the whole customer flow (fleet owner signs up → gets 4-letter account code → uses it on `/book`) — if it's unusable on mobile, customers can't even reach the booking page Brief-6 just fixed. Fixing it closes that gap and brings the two pages in the primary user journey (register → book) to the same mobile-quality bar.

## Users & Consumers

- Fleet owners/contact persons signing up for the discount program on a mobile phone
- Same customers immediately after signup, viewing their account code on `/register/success` before moving to `/book`

## Functional Requirements

| ID  | Requirement                                                                 | Acceptance Criterion                                                                 |
|-----|------------------------------------------------------------------------------|----------------------------------------------------------------------------------------|
| R1  | Pages usable on mobile without pinch-zoom                                    | Viewport meta present on both pages; content reflows to fit 375-430px width without horizontal scroll or manual zoom |
| R2  | All form fields (text, tel, email, number, textarea, checkbox) sized as proper tap targets | Inputs/textarea/checkbox/submit button measure ≥44x44px tappable area on mobile |
| R3  | Text inputs/textarea don't trigger iOS auto-zoom on focus                    | All text-entry fields render at ≥16px font-size on mobile |
| R4  | Checkbox + acknowledgement text row remains side-by-side (not stacked) on mobile, with checkbox individually tappable | Checkbox retains ≥44px tappable area (via padding, not visual checkbox scale) while text wraps beside it as on desktop |
| R5  | Flash/error messages (e.g. duplicate email) remain readable, no explicit new styling required | Flash message `<li>` inherits page-width/padding fixes; no separate requirement beyond that |
| R6  | Account-code confirmation page (`/register/success`) and its CTA button ("View Discounts & Book Refuel Now") are mobile-usable | Account code and CTA button readable and tappable (≥44px) without zoom at 375-430px |
| R7  | Form field values (text, textarea, checkbox state) survive device rotation / viewport resize mid-entry | Rotating the device or resizing the viewport after partially filling the form leaves all entered values and checkbox state intact |

## Non-Functional Requirements

| ID  | Requirement                                                        | Acceptance Criterion                                                        |
|-----|----------------------------------------------------------------------|-------------------------------------------------------------------------------|
| N1  | Both portrait and landscape mobile orientations supported            | No pinch-zoom or horizontal scroll needed in either orientation at 375-430px |
| N2  | Verified on Safari iOS, Chrome Android, Samsung Internet              | Manual/visual check on each browser (or disclosed as a gap if unavailable, consistent with Brief-6) |
| N3  | Desktop layout (>480px) stays pixel-identical to current behavior     | No visual diff on desktop-width screenshots before/after |

## Behaviors & Domain Rules

**Reused pattern from Brief-6 (`/book`):**
- Viewport meta tag `width=device-width, initial-scale=1` added to `<head>` of both templates — must NOT include `maximum-scale` or `user-scalable=no` (keeps user-initiated zoom available for accessibility).
- New mobile CSS scoped under a `.mobile-page` body class and a single `@media (max-width: 480px)` breakpoint, added to `static/styles.css` — existing base rules are not deduped or modified.
- 44x44px minimum tap target (Apple HIG) applied to buttons, inputs, textarea, and the checkbox's tappable area; `font-size: 16px` on text-entry fields to prevent iOS auto-zoom.

**Why these rules matter:**
- `register.html` and `register_success.html` each carry their own inline `<style>` block using the *same* class names (`.page-title`, `.centered-content`) as `book.html`/`booking_success.html`/`admin_login.html`. The `.mobile-page` scoping convention exists specifically so new rules in the shared `styles.css` file don't leak across pages that happen to reuse those class names — this must hold here too.
- Register's checkbox is a new interactive-control shape Brief-6 didn't have (Brief-6 had no checkboxes). Native checkboxes can't be visually enlarged to 44px without looking broken, so the tappable area must be grown via padding/margin around the input rather than by scaling the checkbox itself.

**Common mistakes:**
- Reflexively deduping the inline `<style>` blocks in register.html/register_success.html into styles.css "for consistency" — out of scope, same rationale as Brief-6 (risk of leaking into admin_login.html which shares class names but wasn't audited for this change).
- Stacking the checkbox above the label text by default — explicitly decided against; keep side-by-side, only grow the tap target.

## Edge Cases & Failure Modes

| Scenario                                                        | Decision                                                                 | Rationale                                                                 |
|-------------------------------------------------------------------|----------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| Very long company name or "Preferred Areas" free-text entry     | Text wraps normally within the input/textarea; no truncation              | Textarea already supports multi-line; no new handling needed |
| Duplicate-email flash error appears on a narrow viewport         | Inherits the same `.centered-content`/`.section` width fix as the rest of the page; no dedicated styling | Consistent with R5 — no separate requirement, avoids scope creep |
| Device rotated mid-form-fill (some fields entered, checkbox ticked) | All entered values and checkbox state preserved after rotation           | Same as Brief-6 R7 — native browser behavior, verify-only, no code change expected |
| User reaches `/register/success` then rotates device             | Account code and CTA button remain visible and tappable in both orientations | Covered by R6/N1 |

## Decisions Log

| #   | Decision                                                                 | Alternatives Considered                                              | Chosen Because                                                                 |
|-----|------------------------------------------------------------------------------|---------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| 1   | Bundle `/register` and `/register/success` into one REQ                     | Register form only, defer success page to a separate REQ                 | Mirrors how book.html + booking_success.html were bundled in Brief-6; both are small, related, low-risk |
| 2   | Reuse the exact Brief-6 mobile pattern (viewport meta, `.mobile-page` scoping, 480px breakpoint, 44px tap targets) | Design a fresh approach for this page                                    | Consistency across the app, proven pattern, avoids re-litigating already-settled tradeoffs |
| 3   | Textarea gets the same tap-target/font-size treatment as other inputs, nothing special | Custom textarea sizing/behavior                                          | No stated need for anything beyond the standard input treatment |
| 4   | Checkbox + label stays side-by-side on mobile; only the checkbox's tappable area grows | Stack checkbox above label text on mobile                                | Preserves existing visual layout; avoids unnecessary redesign for a single row |
| 5   | Flash/error messages get no dedicated mobile styling                        | Add explicit font-size/padding rules for flash `<li>` elements           | They already inherit the page-width fix; no evidence of a specific readability problem beyond that |
| 6   | Form-state-survives-rotation requirement (R7) carried over from Brief-6's R7 | Skip this requirement for register                                       | Same risk profile as the book form — no reason mobile browsers would behave differently here |
| 7   | N1-N3 (both orientations, three-browser matrix, desktop-unchanged) carried over unchanged from Brief-6 | Set a different/lighter bar for this smaller page                        | Consistency with the established bar; register is equally customer-facing |

## Scope Boundaries

### In Scope
- `templates/register.html`
- `templates/register_success.html`
- New rules in `static/styles.css`, scoped under `.mobile-page` and the existing `@media (max-width: 480px)` block established in Brief-6 (extended, not duplicated)

### Out of Scope
- `templates/admin_login.html` (reason: shares class names but is not part of the customer-facing register/book journey; must remain unaffected — same guardrail as Brief-6)
- Deduping or restructuring the inline `<style>` blocks inside register.html/register_success.html (reason: out of scope, same rationale as Brief-6's decision not to touch base rules)
- Backend/route changes to `/register` or `/register/success` (reason: this is a presentation-layer-only fix, same as Brief-6)
- New validation, new fields, or changes to the registration business logic (reason: not requested, would expand scope beyond a sprint-sized mobile fix)

## Open Questions

- Physical device scan/tap testing and Safari iOS/Samsung Internet verification may hit the same sandbox limitation (no WebKit available) encountered in Brief-6.
  - **Impact if unresolved:** Same disclosed gap pattern as Brief-6 — cross-browser claims limited to what Chromium-based automated testing can verify.
  - **Suggested default:** Disclose explicitly in the architecture/task verification evidence rather than claiming full coverage, consistent with how Brief-6 handled it.

---
_This requirements document is the input for the **plan-architecture** skill._
_Next step: `/plan-architecture from: specs/requirements/REQ-register-mobile-friendly.md`_
