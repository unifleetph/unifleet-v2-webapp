# Requirements: Brief-7 — Book/Register Copy Fixes, Terms & Conditions, Amount Due Bug

> **Date:** 2026-08-04
> **Type:** feature
> **Source:** docs/Brief-7.md (5 items, with reference images: image-8.png, image-9.png, image-10.png, `Screenshot from 2026-08-04 18-21-45.png`, and docs/UnifleetTermsConditions.md)
> **Phase:** 1 of 5 (Requirement Engineering)

## Summary

Five small, independent changes bundled from Brief-7: add a Terms & Conditions page with a required agreement checkbox on `/book`, change the Submit button's copy, fix a bug where the booking confirmation page shows a hardcoded "₱1" instead of the real amount due, add a line of onboarding copy to `/register`, and clean up/update several lines of copy on `/book`. All items touch presentation layer only except the Amount Due fix, which also corrects which value flows into that display.

## Problem & Motivation

Internal/QA review of the live `/book`, booking confirmation, and `/register` pages surfaced several rough edges: no way for customers to review terms before booking, unclear button copy, outdated/incorrect onboarding copy, and — most importantly — the confirmation page telling every customer they owe exactly ₱1 regardless of what they actually booked, which is a real, customer-facing correctness bug (customers could underpay or be confused about what to send via InstaPay).

## Users & Consumers

- Fleet contact persons/drivers booking a refuel via `/book`, who need to see Terms before submitting and see the correct amount due afterward
- New signups on `/register`, who see the added onboarding line
- UniFleet ops, who currently risk under/over-payment confusion from the wrong Amount Due display

## Functional Requirements

| ID | Requirement | Acceptance Criterion |
|----|---------------|--------------------------|
| R1 | A Terms & Conditions page exists in-app, showing the content from `docs/UnifleetTermsConditions.md` | Navigating to the Terms page renders that document's content as a readable page |
| R2 | `/book` has a link to the Terms & Conditions page, positioned under the Submit Booking button area, opening in the same tab | Link is visible near the Submit Booking button; clicking it navigates to the Terms page in the same tab |
| R3 | `/book`'s booking form has a required "I agree to the Terms & Conditions" checkbox (with an inline link to the Terms page), positioned directly above the Submit Booking button | Submitting the booking form with the checkbox unchecked is blocked by the browser (native required-field behavior), same as the existing pattern on `/register`'s acknowledge checkbox |
| R4 | The Submit Booking button's text changes | Button reads "Submit Booking & Start Payment" instead of "Submit Booking" |
| R5 | The booking confirmation page shows the real, correct Amount Due — the post-discount amount the customer actually owes | Amount Due on the confirmation page equals the same post-discount figure the live calculator preview on `/book` shows as "Amount to Pay" for that booking, formatted as ₱X,XXX.XX (thousands separator, 2 decimals) — never a hardcoded or unrelated value |
| R6 | `/register` has a new line of onboarding copy | The text "Sign-up to get access to our fuel discounts." appears as its own line directly under the existing heading "Save up to ₱1,000 on every refill with UniFleet's fuel discount program!" |
| R7 | `/book`'s account-code call-to-action link text changes | Link text reads ">>> Register Vehicle To Get 4-Letter Account CODE <<<" |
| R8 | `/book`'s "How It Works" heading is removed, its 3 numbered steps stay | The heading text "How It Works" no longer appears; the ordered list ("Register Vehicle (link above)", "Enter Details", "Get CODE") still appears, unchanged and unlabeled by that heading |
| R9 | `/book`'s Tagalog tagline is removed, English tagline stays | "Malaking tipid. Mas mahabang biyahe." no longer appears; "Big savings. Longer trips." still appears |
| R10 | `/book` has a Facebook page link, opening in a new tab | A "Follow our Facebook page: Fuel Discounts by UniFleet" link appears on `/book`, linking to `https://www.facebook.com/people/Fuel-Discounts-by-UniFleet/61589914800072/`, opening in a new browser tab |

## Non-Functional Requirements

| ID | Requirement | Acceptance Criterion |
|----|---------------|--------------------------|
| N1 | No regression to existing booking flow (form submission, calculator preview, voucher creation) | Full existing test suite passes; a booking can still be submitted end-to-end with the new required checkbox checked |
| N2 | Desktop and mobile layouts (per Brief-6 mobile pattern already shipped) remain intact for all touched pages | No visual regression on `/book`, `/register`, booking confirmation at both desktop and mobile widths |

## Behaviors & Domain Rules

**Amount Due bug — root cause (for context, not prescribing the fix):** the confirmation page template currently has the literal text "₱1" hardcoded, so it never reflects any actual booking data. Separately, the value Flask *does* pass to that page re-reads the raw pre-discount form field rather than the already-correct post-discount amount computed earlier in the booking flow — the same post-discount figure the customer already saw in the live calculator preview before submitting. The fix needs to both display the passed value AND make sure the right value is being passed.

**Why this matters:** underpayment (customer sends too little because they don't know their discount) creates support overhead reconciling payments and delays voucher activation. Overpayment isn't collected back cleanly either. Showing "₱1" to everyone is a visible, embarrassing bug that undermines trust in the payment instructions right at the moment a customer is about to send money.

**Terms checkbox mirrors an existing pattern:** `/register` already has a required "I acknowledge..." checkbox using native HTML `required`. The new `/book` Terms checkbox follows the identical mechanism — no new validation logic, no JS, just the same browser-native pattern already proven in this app.

**Common mistakes:**
- Fixing only the template's hardcoded "₱1" without also fixing what value is actually being passed in (the passed value itself is currently wrong too — see root cause above).
- Removing the whole "How It Works" block including its numbered steps, when only the heading itself should go.
- Removing both taglines instead of just the Tagalog one.

## Edge Cases & Failure Modes

| Scenario | Decision | Rationale |
|---|---|---|
| Booking form submitted with the new Terms checkbox unchecked | Browser blocks submission natively (same as register's existing checkbox) | Simplest, proven, no new server-side validation needed |
| Computed pay amount would be ≤ ₱0 after discount | Out of scope for this REQ — `main.py` already rejects the booking before it's created in this case, so the confirmation page never renders with a zero/negative amount | Pre-existing upstream guard already covers this; confirmation page display logic doesn't need its own defensive check |
| Terms & Conditions document (`docs/UnifleetTermsConditions.md`) is updated later | Out of scope for this REQ — whatever mechanism serves the content should reflect the current file content, not a frozen copy, but the update workflow itself isn't part of this REQ | Avoids scope creep into a CMS-like requirement not asked for |
| Facebook link clicked from a booking in progress | Opens in a new tab, so the in-progress booking form state is preserved in the original tab | Matches decision to open external links in new tabs, consistent with not losing form progress |

## Decisions Log

| # | Decision | Alternatives Considered | Chosen Because |
|---|---|---|---|
| 1 | Amount Due shows the post-discount amount, not the raw pre-discount fuel total | Show the raw amount the customer typed in | Post-discount is what the customer actually owes and sends via InstaPay; matches the "Amount to Pay" the calculator preview already shows them pre-submission — showing something different at the confirmation step would be inconsistent and confusing |
| 2 | Terms & Conditions renders as a page in-app, opens in the same tab | Open in a new tab; link directly to the raw markdown file | Same-tab keeps the flow simple for a single reference document; user's explicit choice over the initially-recommended new-tab option |
| 3 | Add a required "I agree to Terms & Conditions" checkbox, not just an informational link | Just the link, no checkbox (as Brief-7 literally states) | User expanded scope beyond the literal brief — wants an actual agreement gate before submission, not just a passive link |
| 4 | Terms checkbox uses native HTML `required`, mirroring `/register`'s existing acknowledge checkbox | Custom JS validation with a specific error message | Proven, simple, already-established pattern in this codebase; no new validation logic needed |
| 5 | Facebook link opens in a new tab | Same tab | Standard practice for external links; avoids losing in-progress booking form state |
| 6 | "How It Works" heading removed, its 3 numbered steps kept | Remove the whole block (heading + steps) | User correction — steps still provide useful guidance, only the heading itself was flagged as redundant/removable |
| 7 | Tagalog tagline removed, English tagline kept | Remove both taglines | Brief and mockup only mark the Tagalog line as struck through; English line has no such mark |
| 8 | Amount Due formatted as ₱X,XXX.XX (thousands separator, 2 decimals) | Simple ₱X with no formatting | Matches the existing `peso()` formatting already used in the `/book` live calculator preview — consistency across the flow |

## Scope Boundaries

### In Scope
- New Terms & Conditions page (content sourced from `docs/UnifleetTermsConditions.md`)
- `/book`: Terms link + required agreement checkbox, Submit button text change, account-code link text change, "How It Works" heading removal, Tagalog tagline removal, Facebook link addition
- Booking confirmation page: Amount Due display fix (both the template and the value flowing into it)
- `/register`: one line of new onboarding copy

### Out of Scope
- Any change to the actual discount/pricing calculation logic itself (reason: the calculation is already correct — `main.py` already computes the right post-discount value; this REQ only fixes how/whether that value reaches the confirmation page)
- A content-management workflow for updating Terms & Conditions text after this REQ ships (reason: not requested; whatever serves the content today just needs to reflect the current file)
- Mobile-specific redesign of any touched page beyond what Brief-6's existing mobile pattern already covers (reason: Brief-6 already shipped mobile support for `/book`/`/register`; this REQ is copy/bug-fix only, N2 just guards against regression)
- Any change to voucher creation, payment confirmation, or redemption logic (reason: unrelated to the 5 items in Brief-7)

## Open Questions

- Exact rendering approach for the Terms & Conditions page (new Flask route reading the markdown file vs. a static HTML page vs. some other mechanism) is a Phase 2 (architecture) decision, not captured here.
  - **Impact if unresolved:** None for this REQ — behavior (R1/R2) is defined independent of implementation.
  - **Suggested default:** Simplest option that keeps the page's content in sync with `docs/UnifleetTermsConditions.md` without manual duplication.

---
_This requirements document is the input for the **plan-architecture** skill._
_Next step: `/plan-architecture from: specs/requirements/REQ-brief-7-book-register-fixes.md`_
