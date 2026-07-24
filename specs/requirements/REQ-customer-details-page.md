# Requirements: Customer Details Page

> **Date:** 2026-07-14
> **Type:** feature
> **Source:** docs/Brief_2.md (item 1)
> **Phase:** 1 of 5 (Requirement Engineering)

## Summary

Add a new admin-only page for viewing a single customer's full registration details and booking transaction history, reachable via a new button/link on `/admin`. Admins can find a customer by exact account code or fuzzy name search, view their `/register`-page details plus a booking history table, export that customer's bookings as CSV, and separately export all customers' bookings from `/admin`. Today no such page exists — customer data is write-only (captured at `/register`) with no way to look it back up.

## Problem & Motivation

Customer data captured at `/register` is currently invisible after submission — there's no admin-facing way to look up a customer's contact details or see what they've booked over time. This blocks basic account support (e.g. "what's this customer's phone number", "what has account XYZ booked") and any auditing of registered fleets. Building this page also surfaces a data-integrity gap: in CSV persistence mode, booking rows don't retain the `account_code` link back to the customer at all, so this REQ also closes that gap.

## Users & Consumers

- **Admin dashboard users** — need to look up a customer's contact info and booking history for support/auditing.

## Functional Requirements

| ID  | Requirement                                                                                          | Acceptance Criterion                                                                                                    |
|-----|-----------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------|
| R1  | New page is gated by the same admin session check as the rest of `/admin`.                               | Unauthenticated request to the new page redirects to admin login, same as `/admin` does today.                              |
| R2  | New button/link added to `/admin` linking to the new page.                                                | `/admin` renders a visible link/button to the customer-details page.                                                         |
| R3  | Search supports exact `account_code` match (case-insensitive).                                            | Searching a valid account code (any case) navigates directly to that customer's detail view.                                |
| R4  | Search supports fuzzy match against `contact_name` or `company_name`.                                     | Searching a partial name string returns customers whose `contact_name` or `company_name` contains the search term.          |
| R5  | Multiple fuzzy matches show a results list before the detail view.                                        | A search matching 2+ customers renders a list (name, account code, company) the admin can click into.                        |
| R6  | Exact account-code match or a single fuzzy match goes straight to the detail view (no intermediate list). | Searching an exact account code, or a fuzzy term matching exactly one customer, opens that customer's detail view directly.  |
| R7  | No-match search shows an inline "not found" message.                                                      | Searching a term matching zero customers renders a not-found message on the same page; no 500/crash.                        |
| R8  | Detail view shows all `/register` fields for the customer.                                                | Page displays account_code, contact_name, contact_number, email, company_name, fleet_size, and areas.                       |
| R9  | Detail view shows a booking transaction history table for that customer.                                  | Table lists that customer's bookings with voucher_id, station, fuel amount, status, and date columns.                       |
| R10 | Detail view offers a CSV export scoped to that customer's bookings only.                                  | Clicking export downloads a CSV containing only the currently-viewed customer's booking rows.                               |
| R11 | `/admin` dashboard gets a separate control to export **all** customers' bookings as one CSV.               | A new export button on `/admin` downloads a CSV covering every customer's bookings, independent of the details page.        |
| R12 | CSV persistence mode is fixed to retain `account_code` on new bookings going forward.                     | A booking created while `PERSISTENCE_BACKEND=csv` is active has a non-empty `account_code` in the resulting CSV row.        |

## Non-Functional Requirements

_None identified beyond existing admin-auth requirements (R1), which are inherited from the current `/admin` gating, not new infrastructure._

## Behaviors & Domain Rules

**Search resolves to exactly one of three outcomes:** (a) direct-to-detail (exact account code, or a fuzzy match with exactly one result), (b) a picklist (fuzzy match with 2+ results), or (c) not-found. No other states.

**Booking history is customer-scoped by `account_code`, matching the existing uniqueness rule:** `account_code` is already the unique key customers are looked up by (`repo.customer_exists` / `repo.get_customer`), so booking history joins on it directly — no new identity concept introduced.

**CSV/Postgres parity gap, now closed going forward:** `VOUCHER_COLUMNS` (`models.py`) previously had no `account_code` field, so `CSVRepo.create_unverified_booking()` silently dropped it even though the caller (`main.py` `/book` handler) always passes it. `PostgresRepo` already retains it via a dedicated FK column. This REQ adds `account_code` to the CSV schema so both backends behave the same *from this point forward*.

**No historical backfill:** Booking rows written under CSV mode *before* this fix have no `account_code` and cannot be retroactively linked to a customer. They will not appear in any customer's booking history. This is a deliberate scope cut, not an oversight.

**Why these rules matter:**
- Fixing the CSV gap prevents the customer-details feature from silently working in prod (Postgres) but breaking in local/dev (CSV) — a bug that would only surface late.
- The direct-to-detail shortcut avoids extra clicks for the common case (admin already knows the exact account code).

**Common mistakes:**
- Backfilling old CSV rows with a best-effort match (e.g. by phone number) — explicitly out of scope; don't attempt it.
- Treating account-code search as fuzzy — it must remain an exact (case-insensitive) match, distinct from the name search.
- Conflating the per-customer CSV export (R10, on the details page) with the global all-customers export (R11, on `/admin`) — they are two separate controls in two separate places.
- Adding `fuel_type` to `VOUCHER_COLUMNS` while touching this schema — that's part of REQ-fuel-types-expansion, not this REQ; don't scope-creep it in here.

## Edge Cases & Failure Modes

| Scenario                                                              | Decision                                                          | Rationale                                                                 |
|----------------------------------------------------------------------|---------------------------------------------------------------------|--------------------------------------------------------------------------|
| Search term matches zero customers                                   | Inline "not found" message, page does not crash                    | Confirmed by user; standard graceful-empty-state handling.               |
| Search term (fuzzy) matches exactly 1 customer                       | Go straight to detail view, skip the picklist                      | Avoids unnecessary extra click for the common single-match case.         |
| Search term (fuzzy) matches 2+ customers                             | Show a picklist (name, account code, company) before detail view   | User confirmed; lets admin disambiguate.                                 |
| Exact account code search                                            | Always goes straight to detail view (account_code is unique)       | account_code is already enforced unique at registration.                 |
| Customer exists but has zero bookings                                | Detail view renders with an empty booking history table, not an error | Customer record and booking history are independent; absence of one shouldn't block the other. |
| Booking exists in CSV mode from before this fix (no account_code)    | Not shown in any customer's history; not retroactively linked      | User confirmed: only new bookings going forward need to carry the link.  |
| Admin not logged in, hits the new page directly                      | Redirected to admin login, same as `/admin`                        | Reuses existing `require_admin` gate — no new auth model needed.         |
| Global CSV export triggered with zero customers/bookings in the system | Downloads a CSV with headers only, no error                       | Consistent empty-state handling, matches per-customer export behavior.   |

## Decisions Log

| #   | Decision                                                                  | Alternatives Considered                                               | Chosen Because                                                                 |
|-----|------------------------------------------------------------------------------|---------------------------------------------------------------------------|--------------------------------------------------------------------------------|
| 1   | Single page with search-by-account-code (later expanded to also support fuzzy name search) | Separate list page + drill-down detail page                            | User chose the single-page search approach as simpler for admin's lookup use case. |
| 2   | Booking transaction history is in scope for this REQ                        | Defer history to a follow-up REQ                                       | User confirmed both customer details and history are needed now.               |
| 3   | Fix CSV backend's missing `account_code` link as part of this REQ           | Ship customer-details page knowing history is Postgres-only            | User explicitly chose to fix CSV mode too, for backend parity.                 |
| 4   | No backfill of historical CSV booking rows                                  | Best-effort backfill matching by phone/other fields                    | User confirmed only new bookings going forward need the fix.                   |
| 5   | Fuzzy search covers both `contact_name` and `company_name`                  | Name-only or company-only fuzzy search                                 | User confirmed both fields should be searchable.                               |
| 6   | Multiple fuzzy matches show a picklist before detail view                   | Show all matches inline on one page; error on ambiguous search         | User confirmed a picklist UX.                                                  |
| 7   | Global "all customers" CSV export lives on `/admin`, not the details page   | Put global export on the customer-details page alongside per-customer export | User confirmed it belongs on the dashboard, separate from per-customer view.   |
| 8   | Entry-point button label left to dev/design discretion                      | Prescribe exact button copy now                                        | User had no preference; not a product-critical decision.                       |

## Scope Boundaries

### In Scope
- New admin-gated customer-details page with account-code + fuzzy name search.
- Picklist UI for multi-match fuzzy search results.
- Customer detail view showing all `/register` fields.
- Per-customer booking history table and CSV export.
- New button/link on `/admin` to reach the page.
- New global "all customers' bookings" CSV export control on `/admin`.
- Adding `account_code` to `VOUCHER_COLUMNS` / CSV schema so CSV-mode bookings retain the link going forward.

### Out of Scope
- Backfilling `account_code` onto historical CSV booking rows written before this fix (reason: user confirmed only new bookings need it).
- Editing customer details from this page (reason: brief and interview scoped this as view-only).
- Adding `fuel_type` to the voucher schema (reason: belongs to REQ-fuel-types-expansion, being scoped separately).
- Prescribing exact button/link copy on `/admin` (reason: left to dev/design discretion).

## Open Questions

_None — all decisions confirmed by user during interview._

---
_This requirements document is the input for the **plan-architecture** skill._
_Next step: `/plan-architecture from: specs/requirements/REQ-<slug>.md`_
