# Requirements: Station Management Dashboard

> **Date:** 2026-07-15
> **Type:** feature
> **Source:** docs/Brief_2.md (item 5)
> **Phase:** 1 of 5 (Requirement Engineering)

## Summary

Add a new admin-gated "Manage Stations" page for adding, editing, and deactivating fuel stations — closing the gap where station management currently requires direct database/SQL access. Stations are soft-deleted (deactivated, not hard-removed) to preserve historical booking/price integrity, and stay visible (grayed out) with a reactivate control rather than disappearing.

## Problem & Motivation

There is currently no way to add or remove a station without direct database access — `price_store.upsert_station()` exists in code but is not exposed through any route or UI, and there is no removal function at all. As the business needs to manage its station list going forward (adding new supply partners, retiring old ones), this needs to be self-service for admins rather than requiring a developer/DB operator each time.

## Users & Consumers

- **Admin dashboard users** — need to add new stations and deactivate/reactivate existing ones without touching the database directly.

## Functional Requirements

| ID  | Requirement                                                                                          | Acceptance Criterion                                                                                              |
|-----|-----------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------|
| R1  | New "Manage Stations" page, gated by the same admin session check as the rest of `/admin`.                | Unauthenticated request redirects to admin login, consistent with `/admin`.                                       |
| R2  | New button/link on `/admin` (or `admin_prices.html`) leading to the Manage Stations page.                 | A visible link/button navigates to the new page.                                                                   |
| R3  | Admin can add a new station by entering brand, display name, and location.                                | Submitting the add-station form creates a new row in `stations`, visible on the Manage Stations page.             |
| R4  | New station's internal `id` is auto-generated (slugified from brand + name), not manually entered.        | Creating a station does not require the admin to type an id; the generated id is unique and derived from the inputs. |
| R5  | Slug collisions are resolved by auto-appending a numeric suffix.                                           | Creating a station whose slug matches an existing station's id succeeds, with the new row getting a suffixed id (e.g. `-2`). |
| R6  | New stations can be created with zero fuel-type prices set.                                                | Station creation succeeds without requiring any price input; the station then follows the "hidden from `/book` until priced" rule from REQ-fuel-types-expansion. |
| R7  | Admin can edit an existing station's brand, display name, and location.                                   | Editing and saving updates the station's row; changes are reflected on Manage Stations and `admin_prices.html`.    |
| R8  | Admin can deactivate a station (soft-delete via `is_active`), not hard-delete it.                         | Deactivating a station sets `is_active = FALSE`; the row and its historical price/booking references remain in the database, unmodified. |
| R9  | Deactivated stations remain visible (grayed out) on both Manage Stations and `admin_prices.html`, with a reactivate control. | A deactivated station still appears in both pages' listings, visually marked inactive, with a control to set `is_active = TRUE` again. |
| R10 | Deactivated stations are excluded from `/book`'s station dropdown and any customer-facing station listing. | A deactivated station does not appear as a selectable option on `/book`, regardless of fuel type or price data.    |

## Non-Functional Requirements

_None identified beyond existing admin-auth requirements (R1), inherited from `/admin`'s current gating._

## Behaviors & Domain Rules

**Soft-delete, not hard-delete:** `is_active` already exists as an unused column on the `stations` table. Deactivation flips this flag; nothing referencing the station (price rows, price history, past booking/voucher records) is touched or removed. This preserves the integrity of historical data that may reference the station by id.

**`is_active` must actually be enforced at read time:** Today, `price_store.list_stations()` has no `is_active` filter anywhere it's called (`/admin` dashboard, `/book`'s station list). This REQ requires wiring that filter into every station-listing call site used for booking/pricing selection — otherwise deactivation is cosmetic only and doesn't actually remove the station from customer-facing flows (R10).

**Auto-generated ids favor predictability over admin effort:** Ids are derived from brand + name rather than manually typed, matching the existing `_DEFAULT_STATIONS` id pattern (e.g. `cleanfuel_valenzuela`). Collisions are resolved automatically (suffix) rather than blocking the admin, since station creation should be low-friction.

**Why these rules matter:**
- Enforcing `is_active` everywhere is the single most important integration point — without it, this whole feature is UI-only theater; deactivated stations would keep appearing to customers.
- Soft-delete protects historical vouchers/bookings from broken references, which matters for support/audit lookups (ties to REQ-customer-details-page's booking history feature).

**Common mistakes:**
- Implementing deactivation only in the UI/route layer without updating `price_store.list_stations()` (or its callers) to filter `is_active = TRUE` for customer-facing contexts.
- Hard-deleting the station row on "remove," breaking FK references from `prices`, `price_history`, and past booking records.
- Requiring at least one fuel-type price at station creation — explicitly not required (R6); a bare station is a valid, if temporarily unbookable, state.

## Edge Cases & Failure Modes

| Scenario                                                                 | Decision                                                          | Rationale                                                                 |
|-------------------------------------------------------------------------|----------------------------------------------------------------------|------------------------------------------------------------------------------|
| Two stations would generate the same slug (e.g. two "Petron" entries)   | Auto-append a numeric suffix (`-2`, `-3`, ...) to keep ids unique     | User confirmed auto-suffix over rejecting the submission.                    |
| New station created with no prices set                                  | Creation succeeds; station is hidden from `/book` until priced       | User confirmed zero-price creation is allowed; matches REQ-fuel-types-expansion's availability rule. |
| Admin deactivates a station that has active/unredeemed vouchers tied to it | Deactivation proceeds; historical/pending vouchers and their station reference remain intact | Soft-delete never touches existing price/booking rows.                       |
| Admin reactivates a previously deactivated station                      | Station reappears in `/book`'s dropdown (subject to still having at least one fuel-type price) | Reactivation simply flips `is_active` back; availability still follows the price-gating rule from REQ-fuel-types-expansion. |
| Admin edits brand/name of a station after it's been referenced by past bookings | Edit succeeds; historical booking records are unaffected (they don't store brand/name redundantly, only station reference) | Editing station metadata doesn't rewrite historical records.                 |

## Decisions Log

| #   | Decision                                                                 | Alternatives Considered                                       | Chosen Because                                                                 |
|-----|-------------------------------------------------------------------------|--------------------------------------------------------------------|---------------------------------------------------------------------------------|
| 1   | Build a real admin dashboard page (not docs-only or a CLI script)       | Docs-only runbook; a CLI script like `scripts/migrate_to_postgres.py` | User chose full dashboard scope for this REQ.                                  |
| 2   | New dedicated "Manage Stations" page, separate from `admin_prices.html` | Fold station add/edit/deactivate controls into `admin_prices.html`   | User confirmed a separate, purpose-built page rather than overloading the prices table. |
| 3   | Soft-delete via existing `is_active` flag                               | Hard delete the station row                                        | User confirmed soft-delete, to avoid breaking historical FK references.        |
| 4   | Deactivated stations stay visible (grayed out) with a reactivate control | Hide deactivated stations entirely from both admin pages            | User confirmed visibility/reversibility over full disappearance.               |
| 5   | Station id auto-generated from brand + name                             | Admin manually enters the id                                       | User confirmed auto-generation for consistency and lower admin effort.         |
| 6   | Slug collisions resolved by auto-appending a suffix                     | Reject the submission and require a manual adjustment              | User confirmed auto-suffix over blocking the admin.                            |
| 7   | New stations may be created with zero prices                            | Require at least one fuel-type price at creation                   | User confirmed bare creation is allowed; pricing can follow later.             |

## Scope Boundaries

### In Scope
- New "Manage Stations" admin page: add, edit (brand/name/location), deactivate, reactivate.
- New nav entry point from `/admin` (or `admin_prices.html`) to the new page.
- Auto-generated, collision-safe station id slugs.
- Wiring `is_active` filtering into every station-listing call site used for customer-facing booking flows.
- Grayed-out visibility + reactivate control for deactivated stations on both Manage Stations and `admin_prices.html`.

### Out of Scope
- Hard deletion of station rows (reason: soft-delete only, to protect historical references).
- Requiring price entry at station creation time (reason: user confirmed bare creation is valid).
- Per-fuel-type price/discount management itself (reason: covered by REQ-fuel-types-expansion; this REQ only manages station identity/metadata and active status).
- Any changes to `admin_prices.html`'s pricing columns/layout (reason: that's REQ-fuel-types-expansion's scope).

## Open Questions

_None — all decisions confirmed by user during interview._

---
_This requirements document is the input for the **plan-architecture** skill._
_Next step: `/plan-architecture from: specs/requirements/REQ-<slug>.md`_
