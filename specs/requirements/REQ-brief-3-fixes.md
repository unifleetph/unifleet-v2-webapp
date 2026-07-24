# Requirements: Brief 3 — Booking Page Order, Admin Styling, Customer Export

> **Date:** 2026-07-24
> **Type:** bugfix + feature
> **Source:** docs/Brief-3.md
> **Phase:** 1 of 5 (Requirement Engineering)

## Summary

Four small, related fixes reported from the staging environment: (1) restore the `/book` page's original section order, which shifted when fuel-type selection was introduced; (2) fix a background-color mismatch on the Manage Stations admin page; (3) add customer contact columns to the booking export CSVs; (4) add a full customer/driver listing table with CSV export to the Customer Lookup admin page. None of these touch core booking or pricing logic — they're presentation and data-export fixes.

## Problem & Motivation

Staging feedback flagged that the `/book` page's field order feels wrong compared to what admins are used to, that the Manage Stations page (shipped in the prior sprint) visually doesn't match the rest of the admin area, and that two existing admin workflows (bulk booking export, customer lookup) are missing customer contact/driver data that support and ops staff need without cross-referencing multiple exports by hand.

## Users & Consumers

- **Customers booking fuel** — see the `/book` form in its restored field order.
- **Admin dashboard users** — see consistent styling across all `/admin/*` pages; get customer contact data directly in booking exports; get a full customer/driver table on the Customer Lookup page.

## Functional Requirements

| ID  | Requirement | Acceptance Criterion |
|-----|-------------|----------------------|
| R1  | On `/book`, the Station + Fuel Type selection block appears above the Driver & Vehicle section (restoring the pre-fuel-type page order, with Fuel Type kept paired with Station since the station list filters by it). | Loading `/book` shows Station/Fuel Type fields before Driver & Vehicle fields; selecting a different fuel type still re-filters the station list exactly as it does today; booking submission and validation behavior is otherwise unchanged. |
| R2  | The Manage Stations page (`/admin/stations`) and Admin Prices page (`/admin/prices`) use the same page background color as the main Admin dashboard (`/admin`). | Visually inspecting all three pages side by side shows the same background color; no other visual elements change. |
| R3  | The "Export All Bookings" CSV and the per-customer booking export CSV each gain 3 new columns: Customer Name, Customer Number, Customer Email, populated from the customer record linked to each booking's account code. | Downloading either export shows the 3 new columns with the correct customer's contact info per row; a booking with no linked customer account shows blank cells for these 3 columns, not an error. |
| R4  | The Customer Lookup page (`/admin/customers`) gains a table, always visible below the existing search box regardless of search state, listing every customer with one row per distinct driver from that customer's booking history (Customer Name, Number, Email, Driver Name per row). A customer with no booking history yet still appears, with a blank driver name. | Loading `/admin/customers` (with or without an active search) shows the table below the search box; a customer with 2 distinct drivers in their booking history appears as 2 rows; a customer with zero bookings appears as 1 row with a blank Driver Name cell. |
| R5  | The Customer Lookup page's new table has an "Export to CSV" button that downloads the same data shown in the table (one row per driver, same columns). | Clicking the export button downloads a CSV whose rows and columns exactly match what's on screen at that time. |

## Non-Functional Requirements

_None identified beyond existing admin-auth requirements, inherited from `/admin`'s current gating (all 4 items touch only already-admin-gated pages, or the public `/book` page's visual layout)._

## Behaviors & Domain Rules

**R1 is a layout change only, not a data/validation change:** the station-list-filters-by-fuel-type behavior introduced when fuel types shipped must be preserved exactly — only the visual position of that Station+Fuel Type block relative to Driver & Vehicle moves. No new fields, no new validation rules.

**R3/R4 both depend on the existing `account_code` link between bookings and customers**, and R4 additionally depends on `driver_name` already stored per booking (vouchers) rather than the driver-preset table, which is not the live data source in this system (driver/vehicle presets are stored in CSV files, not Postgres, regardless of which persistence backend is active).

**Why these rules matter:**
- Preserving the fuel-type-filters-station behavior in R1 avoids silently reintroducing the exact problem that motivated moving the fields in the first place.
- Sourcing R4's driver names from booking history rather than the unused presets table means the table reflects who has actually booked, not a stale or empty preset list.

**Common mistakes:**
- Treating R1 as a literal full revert (moving Fuel Type away from Station) — this would break the existing filter-by-fuel-type dropdown behavior.
- Reading driver names from the `presets` table expecting it to be populated — it isn't, for any persistence backend currently in use.
- Forgetting that a booking or a customer can have no linked counterpart (a booking with no account_code, or a customer with no bookings yet) — both must degrade to blank cells, not errors.

## Edge Cases & Failure Modes

| Scenario | Decision | Rationale |
|----------|----------|-----------|
| A booking has no `account_code` (legacy or anonymous booking) | Customer Name/Number/Email columns are blank for that row in both exports (R3) | Confirmed by user; matches how other optional fields already degrade in these exports. |
| A customer has zero bookings yet | Still appears in R4's table, as one row with a blank Driver Name | Confirmed by user; keeps the table a complete customer roster, not just an activity log. |
| A customer has multiple distinct drivers in their booking history | Each distinct driver gets its own row (denormalized, not comma-joined) | Confirmed by user; keeps CSV export machine-readable (one entity per row) rather than requiring downstream parsing of a joined string. |
| Admin has an active search on `/admin/customers` when the new table loads | Table remains visible below the search box regardless of search state | Confirmed by user; matches the brief's literal wording ("below the SEARCH function"), simplest behavior. |

## Decisions Log

| # | Decision | Alternatives Considered | Chosen Because |
|---|----------|--------------------------|-----------------|
| 1 | Combine all 4 brief items into a single REQ | Split into 2 or 4 separate REQs | User confirmed the combined scope is small enough for one sprint. |
| 2 | R1: keep Fuel Type paired with Station when reordering, rather than a literal revert to the pre-fuel-types field order | Move Station back to its old position and Fuel Type elsewhere, station list unfiltered until fuel type is later chosen | User confirmed; a literal revert would reintroduce the exact filtering problem that motivated the original reorder. |
| 3 | R2: background-color fix only, no other styling changes | Broader visual pass on `/admin/stations` | User confirmed "other style changes" in the brief was fully covered by the background fix alone. |
| 4 | R2: apply the fix to both `/admin/stations` and `/admin/prices`, not just the page named in the brief | Fix only `/admin/stations` as literally stated | User confirmed; both pages share the same underlying style gap against `/admin`. |
| 5 | R3: add the 3 new columns to both the "Export All Bookings" and per-customer export routes | Add only to "Export All Bookings" as literally stated in the brief | User confirmed both exports should be consistent. |
| 6 | R4: source driver names from booking history (`vouchers.driver_name`), not the `presets` table | Populate/wire up the dormant `presets` Postgres table first | User confirmed; presets table is unused in the current persistence setup, out of scope here. |
| 7 | R4: one row per distinct driver (denormalized), not one row per customer with drivers comma-joined | Comma-joined drivers in a single cell per customer | User confirmed one-row-per-driver, for both the on-screen table and its CSV export. |
| 8 | R4: table always visible below search, regardless of search state | Hide the table while a search is active | User confirmed always-visible is simpler and matches the brief's literal placement. |

## Scope Boundaries

### In Scope
- `/book` page section reorder (R1).
- Background-color fix on `/admin/stations` and `/admin/prices` (R2).
- 3 new customer-contact columns on both booking export CSVs (R3).
- New all-customers/all-drivers table + CSV export on `/admin/customers` (R4, R5).

### Out of Scope
- Any change to booking validation, price/discount logic, or fuel-type filtering behavior itself (reason: R1 is presentation-only).
- Any other visual/style changes to `/admin/stations` or `/admin/prices` beyond background color (reason: user confirmed no further specifics).
- Wiring up the dormant `presets` Postgres table (reason: R4 uses booking history instead; out of scope for this REQ).
- Pagination or search/filter controls on the new `/admin/customers` table (reason: not requested; revisit if the customer list grows large enough to matter).

## Open Questions

_None — all decisions confirmed by user during interview._

---
_This requirements document is the input for the **plan-architecture** skill._
_Next step: `/plan-architecture from: specs/requirements/REQ-brief-3-fixes.md`_
