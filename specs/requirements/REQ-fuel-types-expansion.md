# Requirements: Three Fuel Types Across the Booking Flow

> **Date:** 2026-07-15
> **Type:** feature
> **Source:** docs/Brief_2.md (item 3)
> **Phase:** 1 of 5 (Requirement Engineering)

## Summary

Expand the booking flow from a single implicit fuel type ("Diesel", currently hardcoded) to three explicit, user-selectable fuel types: **Biodiesel** (the existing type, renamed), **Premium Gasoline**, and **Unleaded Gasoline**. Every station gets its own price and discount per fuel type (a station may support only some). The `/book` form gains an independent, overridable Fuel Type field defaulted from the driver's preset. Admin prices, admin dashboard, and the Supplier PDF export all surface fuel type accordingly.

## Problem & Motivation

The product only supports one implicit fuel type today, hardcoded as "Diesel" throughout the codebase (a hidden form field, single price/discount per station, no fuel column anywhere). The business needs to support fleets that use different fuel types — including drivers who switch fuel types between bookings (e.g. a driver who normally books Unleaded but occasionally books Premium) — which requires fuel type to be a per-booking choice, not a fixed vehicle/driver attribute.

## Users & Consumers

- **Customers booking fuel (`/book`)** — need to select which of the 3 fuel types they're booking, per booking, with a sensible starting default.
- **Admins managing prices (`/admin/prices`)** — need to set/view price and discount per station per fuel type.
- **Admins reviewing bookings (`/admin` dashboard)** — need to see which fuel type each booking used.
- **Suppliers receiving the PDF export** — need to see fuel type per voucher to fulfill the correct fuel.

## Functional Requirements

| ID   | Requirement                                                                                                   | Acceptance Criterion                                                                                                    |
|------|--------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------|
| R1   | Three canonical fuel types exist system-wide: Biodiesel, Premium Gasoline, Unleaded Gasoline.                     | All surfaces (admin prices, `/book`, admin dashboard, PDF) draw from this same 3-value set; no other fuel type value is selectable. |
| R2   | `/book` has an independent Fuel Type field, decoupled from the driver/vehicle preset.                             | The submitted booking's `fuel_type` reflects whatever was selected in this field at submit time, not the preset's stored default. |
| R3   | Fuel Type field pre-fills from the driver preset's stored default (or the "Add New Driver" sub-form's chosen value) but remains user-overridable. | Selecting a preset with default "Unleaded" pre-fills "Unleaded"; user can change to "Premium Gasoline" and submit successfully with that value recorded. |
| R4   | Overriding the Fuel Type field for one booking does not change the preset's stored default.                       | After booking with an overridden fuel type, re-opening `/book` and selecting the same preset again shows the original stored default, not the override. |
| R5   | Legacy presets with literal stored value `"Diesel"` show no pre-filled default.                                    | Selecting a preset whose stored `fuel_type` is `"Diesel"` leaves the Fuel Type field unset; the driver must explicitly choose one. |
| R6   | `/book` form order is flipped: Driver & Vehicle (incl. Fuel Type) section now precedes the Station section.        | Rendered `/book` page shows driver/preset/fuel-type fields before the station selector, reversed from current order.         |
| R7   | Station dropdown filters to only stations that have a price configured for the currently-selected fuel type.      | Changing the Fuel Type selection updates the Station dropdown's available options to match.                                  |
| R8   | Stations with no price configured for any fuel type are hidden entirely from the Station dropdown.                | A station with all 3 fuel prices unset does not appear in `/book`'s station list regardless of selected fuel type.           |
| R9   | Each station can have an independent price per fuel type (nullable — a station may not support all 3).            | Price data model supports storing 0–3 fuel-type prices per station independently.                                            |
| R10  | Each station can have an independent discount per fuel type; missing discount defaults to ₱0, not unavailability. | A station with a price but no discount for a fuel type is still bookable, with ₱0 discount applied.                          |
| R11  | Admin prices page (`/admin/prices`) shows price and discount for all 3 fuel types per station.                    | Table renders 6 new columns (Biodiesel/Premium/Unleaded × Price/Discount) alongside existing Brand/Station/Location.         |
| R12  | Admin prices page shows a human-readable "updated at" timestamp under both price and discount cell values.        | Each price and discount cell displays a `Date + 24H time` formatted timestamp beneath the value, replacing/adding to the current raw-epoch "Updated" column. |
| R13  | `/book`'s live price×discount reference table is replicated per fuel type (3 tables), stacked vertically and independently collapsible. | Page renders 3 distinct price×discount tables (one per fuel type), each with its own expand/collapse control.                |
| R14  | Admin dashboard bookings table gets a fuel type column.                                                            | Each booking row displays its fuel type value (Biodiesel/Premium/Unleaded).                                                  |
| R15  | Supplier PDF export gets a fuel type column using full display names.                                              | PDF rows show "Biodiesel", "Premium Gasoline", or "Unleaded Gasoline" per voucher.                                            |
| R16  | Bookings made before this feature ships retain their literal historical `"Diesel"` fuel type label everywhere.    | Pre-existing booking rows display "Diesel" (unchanged) in the admin dashboard and PDF export, not relabeled to Biodiesel.     |
| R17  | Booking-level `fuel_type` is reliably persisted in both CSV and Postgres backends.                                 | A booking created under `PERSISTENCE_BACKEND=csv` has a correct, non-empty `fuel_type` value in the resulting record, matching Postgres-mode behavior. |

## Non-Functional Requirements

_None identified beyond existing per-request auth/session handling already in place for `/admin` and `/book`._

## Behaviors & Domain Rules

**Fuel type is a booking-time decision, not a vehicle attribute:** Historically `fuel_type` lived on the driver/vehicle preset (a truck's "fixed" fuel type). This REQ moves the source of truth to the booking itself — the preset value becomes only a *default suggestion*, because real fleets switch fuel types between bookings on the same vehicle.

**Availability is price-gated, not discount-gated:** A station "supports" a fuel type if and only if it has a price set for it. Discount is a separate, optional layer on top — its absence never blocks booking, it just means ₱0 rebate.

**Form field ordering has a data dependency:** Station options depend on the selected fuel type (R7), which is why Driver & Vehicle (containing Fuel Type) must render before Station in the form (R6) — without this order, the station list can't be correctly filtered on first render.

**No retroactive relabeling of history:** Both booking records (R16) and stale/blank preset defaults (R5) intentionally preserve or blank out old "Diesel" values rather than auto-mapping them to "Biodiesel" — history stays as recorded; only forward-looking defaults get the new 3-way model.

**Why these rules matter:**
- Decoupling fuel type from the vehicle preset directly serves the business case that motivated this REQ (drivers switching fuel types booking-to-booking).
- Preserving literal "Diesel" in historical records avoids silently rewriting past transactions that a supplier or auditor might reference.

**Common mistakes:**
- Wiring the Fuel Type field to overwrite the preset's stored default on submit — it must not (R4).
- Auto-mapping legacy `"Diesel"` values to `"Biodiesel"` anywhere except conceptually (as the "existing" type in R1's naming) — actual data (presets, historical bookings) keeps `"Diesel"` literal or blank, never auto-converted (R5, R16).
- Treating a missing discount as "station doesn't support this fuel type" — only a missing *price* gates availability (R10).
- Forgetting to also add a migration/backfill task — explicitly not required; existing single price/discount values are considered stale and will be re-entered by admins after this ships.
- Reusing this schema change to also fix the `account_code` CSV gap (that's REQ-customer-details-page's concern) or vice versa — keep the two CSV-parity fixes independent even though they're the same class of bug.

## Edge Cases & Failure Modes

| Scenario                                                                                   | Decision                                                                 | Rationale                                                                                     |
|------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------|
| Station has price for Biodiesel and Premium but not Unleaded                                   | Station appears in dropdown when Biodiesel/Premium selected; hidden when Unleaded selected | Price presence is the availability gate (R7).                                                  |
| Station has a price but no discount for a fuel type                                            | Bookable, discount treated as ₱0                                          | Discount absence never blocks booking (R10).                                                    |
| Station has zero fuel types priced (newly added, unconfigured)                                 | Hidden entirely from `/book`'s station dropdown, regardless of fuel type selected | Confirmed by user (R8) — an entirely unpriced station shouldn't be selectable at all.           |
| Driver preset's stored fuel type is legacy `"Diesel"`                                          | Fuel Type field shows no default; driver must pick explicitly              | Confirmed by user (R5) — legacy value doesn't map to any of the new 3 options.                  |
| Driver overrides Fuel Type for a single booking (preset says Unleaded, books Premium instead)  | Booking records Premium; preset's stored default remains Unleaded          | Confirmed by user — override is booking-scoped only, doesn't mutate the stored default (R4).    |
| Existing booking record from before this feature shipped                                       | Displays literal "Diesel" in dashboard/PDF, unchanged                      | Confirmed by user (R16) — no retroactive relabeling of history.                                 |
| Admin hasn't yet re-entered any prices after this ships (fresh/blank price table)              | All stations effectively hidden from `/book` until at least one fuel type is priced per station | Consistent with R8; expected transitional state post-deploy, not an error condition.            |
| Booking created under CSV persistence backend                                                  | `fuel_type` is correctly persisted, same as Postgres mode                  | Confirmed in scope (R17) — same parity-gap pattern as `account_code` fix in REQ-customer-details-page. |

## Decisions Log

| #   | Decision                                                                                     | Alternatives Considered                                                       | Chosen Because                                                                                     |
|-----|---------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| 1   | Canonical fuel types are exactly Biodiesel / Premium Gasoline / Unleaded Gasoline, used everywhere | Brief's literal "Gasoline and Diesel" (2 categories) phrasing taken at face value    | Brief was internally inconsistent; user confirmed the 3-type reading matches dashboard/PDF sub-bullets. |
| 2   | Fuel Type is booking-level, decoupled from driver/vehicle preset                                  | Keep fuel type as a fixed vehicle attribute (status quo)                            | User's business case: drivers regularly switch fuel type booking-to-booking on the same vehicle.    |
| 3   | Preset's stored fuel type acts only as an overridable default, never mutated by a booking's override | Booking override also updates the stored preset default                            | User confirmed override should be booking-scoped only.                                              |
| 4   | Legacy `"Diesel"` presets show no pre-filled default                                              | Auto-map legacy "Diesel" preset value to "Biodiesel" for pre-fill purposes           | User explicitly chose blank-until-explicit-choice over auto-mapping.                                 |
| 5   | `/book` form reordered: Driver & Vehicle before Station                                           | Keep current order, filter station list via JS regardless of field position          | User confirmed the straightforward reorder to resolve the data-dependency ordering conflict.         |
| 6   | Availability gated by price presence only; missing discount defaults to ₱0                        | Missing discount also blocks booking; discount required alongside price              | User confirmed: station should still be bookable with ₱0 discount if only price is set.              |
| 7   | No required migration of existing single price/discount data                                     | Backfill existing value into Biodiesel for every station                            | User: all current prices considered stale/wrong, will be re-entered fresh after this ships.          |
| 8   | Historical bookings keep literal "Diesel" label, unchanged                                        | Relabel historical bookings to "Biodiesel" for consistency                          | User confirmed history should not be retroactively rewritten.                                        |
| 9   | Admin prices page updated-at timestamp fix (readable Date+24H) extended to discount cells too      | Scope the readability fix to price cells only, as originally reported                | User asked for the same fix to apply to the new discount cells.                                      |
| 10  | `fuel_type` CSV/Postgres persistence parity fix is in scope for this REQ                          | Treat as a separate follow-up REQ                                                    | User confirmed in scope, same class of gap as the `account_code` fix in REQ-customer-details-page.  |
| 11  | Admin prices page stays a flat table (6 new columns), no collapsible UX                           | Add collapsible sections to admin prices page to match `/book`'s pattern             | User confirmed collapse behavior is specific to `/book`'s 3 stacked reference tables, not needed here. |

## Scope Boundaries

### In Scope
- Per-station, per-fuel-type price and discount data model (Biodiesel/Premium/Unleaded).
- `/book`: new independent Fuel Type field, form reorder, station-list filtering by fuel type, 3 stacked collapsible price×discount reference tables.
- Admin prices page: 6 new price/discount columns, readable updated-at timestamps under both price and discount cells.
- Admin dashboard: new fuel type column on bookings.
- Supplier PDF export: new fuel type column with full display names.
- CSV/Postgres parity fix for booking-level `fuel_type` persistence.

### Out of Scope
- Migrating/backfilling existing single price/discount values into the new per-fuel-type model (reason: user confirmed all current prices are stale and will be re-entered post-launch).
- Relabeling historical booking records or legacy preset fuel-type values (reason: explicitly preserved as-is / left blank per user decisions).
- The `account_code` CSV/Postgres parity fix (reason: covered separately in REQ-customer-details-page, kept independent despite being the same class of bug).
- Any change to `/register-vehicle` (reason: that page is being deleted entirely per REQ-cleanup-register-vehicle).

## Open Questions

_None — all decisions confirmed by user during interview._

---
_This requirements document is the input for the **plan-architecture** skill._
_Next step: `/plan-architecture from: specs/requirements/REQ-<slug>.md`_
