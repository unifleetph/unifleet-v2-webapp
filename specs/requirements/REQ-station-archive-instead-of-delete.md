# Requirements: Archive Stations Instead of Deleting Them

> **Date:** 2026-11-09
> **Type:** bugfix
> **Source:** docs/Bugfix-1.md (bug 2 of 2), verbal gap-fill interview
> **Phase:** 1 of 5 (Requirement Engineering)

## Summary

Deleting a station today permanently removes its database row, which is blocked whenever the station has existing orders (a real foreign-key relationship, not just a guard) — so a station with any booking history can never be removed, period. This replaces station deletion entirely with a reversible "archive" action: the station's row is never removed, it's hidden from the normal stations list and the pricing page, and an admin can un-archive it later with everything fully editable again.

## Problem & Motivation

Admins can't delete a station that has existing orders associated with it, because the system (and the database itself) must keep those historical orders intact and readable. The current delete action is an all-or-nothing permanent removal, which is exactly why it's blocked — there's no way today to just get a station out of the way without that risk. The business wants stations to be removable from the day-to-day admin/booking experience without ever losing history: "never delete, always archive/hide."

## Users & Consumers

- **Admin (internal staff)** — needs to remove a station from active use (regardless of whether it has booking history), and needs the option to bring it back later if that turns out to be premature.

## Functional Requirements

| ID | Requirement | Acceptance Criterion |
|----|-------------|------------------------|
| R1 | Admin can archive a deactivated station. | Archiving a station that is currently deactivated succeeds. |
| R2 | A station must be deactivated before it can be archived (same precondition the current delete action already enforces). | Attempting to archive an active (non-deactivated) station is rejected; the station's state is unchanged. |
| R3 | Archiving replaces deletion entirely — the station's database row is never removed, regardless of whether it has existing orders. | After archiving, the station's row still exists; all existing orders, prices, and discounts referencing it are unaffected. |
| R4 | Archived stations are hidden from the normal stations list and from the pricing page. | Neither the stations list nor the pricing page shows an archived station by default. |
| R5 | Admin can view archived stations via a separate view, and can un-archive one from there. | A dedicated "Archived" view lists archived stations; un-archiving one returns it to the deactivated state (not directly to active), with every field fully editable again exactly as any other deactivated station. |
| R6 | Both archiving and un-archiving are logged. | Each action records the actor, the station, and the state change (deactivated→archived or archived→deactivated), matching how `delete_order` is already logged today. |

## Non-Functional Requirements

None identified — this is a low-traffic admin action with no performance, security, or scale dimension beyond what the existing station-management flows already have.

## Behaviors & Domain Rules

**Relationship to the existing active/deactivated states:**
- "Deactivated" is unchanged: the station stays visible (grayed out) in both the stations list and the pricing page, and can be reactivated directly.
- "Archived" is a new, further state reached only from "deactivated." An archived station disappears from both lists entirely — it is not just grayed out, it's gone from the normal views — but its row and all history remain intact.
- Un-archiving returns a station to "deactivated," not directly to "active" — the admin must explicitly reactivate it afterward, same as any other deactivated station. Once un-archived, the station is fully editable again; archiving does not lock or freeze any of its fields.

**Why this rule matters:**
- The two-step precondition (must be deactivated first) is preserved rather than loosened, so archiving doesn't become a way to silently yank a station out of active rotation without the admin having first made that deliberate deactivation call.
- Making the row permanent (never deleted) is what removes the foreign-key problem entirely — there's no longer any scenario where "this station has orders" blocks the action, because nothing is ever removed.

**Common mistakes:**
- Treating "archived" as just another visual state like "deactivated" (grayed out but still listed) — it must be hidden from the normal views, only reachable through the dedicated Archived view.
- Restoring an un-archived station directly to "active" instead of "deactivated" — it must land back in the same state the admin would reach by creating/reactivating normally.

## Edge Cases & Failure Modes

| Scenario | Decision | Rationale |
|----------|----------|-----------|
| Admin archives a station that has existing orders | Succeeds — this is the entire point of the change | The DB row is never removed, so the foreign-key concern that blocks today's delete no longer applies |
| Admin tries to archive an active (not-yet-deactivated) station | Rejected, no state change | Matches R2 — precondition preserved from today's delete flow |
| Admin tries to archive an already-archived station | No-op, no error | Idempotent action, not destructive |
| Admin tries to un-archive a station that isn't archived | Rejected, no state change | Mirrors R2's precondition in reverse |
| Station is archived, then its name/fuel-type/price data is looked up by an existing order | No change — historical data reads are unaffected | Matches R3 — nothing about existing orders changes |

## Decisions Log

| # | Decision | Alternatives Considered | Chosen Because |
|---|----------|---------------------------|------------------|
| 1 | Archive, never hard-delete | Allow real row deletion with `ON DELETE SET NULL` on `vouchers.station_id` | Business preference stated directly: never delete, always archive/hide, to preserve history without a schema change or FK-null risk |
| 2 | Archive replaces hard-delete entirely, even for zero-booking stations | Archive only kicks in when a station has bookings; zero-booking stations keep today's hard delete | One consistent behavior is simpler to reason about and matches the stated general preference, rather than two different delete paths depending on booking history |
| 3 | Un-archiving is possible (not one-way) | One-way archive, re-create the station fresh if needed again | Avoids losing a station's existing configuration (pricing, discount history, id) just because it was temporarily archived |
| 4 | Un-archived station returns to "deactivated," not "active" | Return directly to its prior active state | Forces the same deliberate reactivation step every other deactivated station goes through — no silent reappearance in active booking flows |
| 5 | Archiving/un-archiving requires logging | Skip logging (as REQ1's lighter-weight toggle does) | This is effectively a delete/restore of a station — more consequential than REQ1's report-exclusion flag, warrants the same audit trail as `delete_order` |

## Scope Boundaries

### In Scope
- A new "archived" state for stations, reachable only from "deactivated."
- Removing the ability to hard-delete a station's row entirely (this route always archives now).
- A separate admin view listing archived stations, with an un-archive action.
- Audit logging of both archive and un-archive.

### Out of Scope
- Any change to the existing active/deactivated lifecycle or its reactivation flow.
- Bulk archive/un-archive actions (not raised in this brief).
- Any schema change to `vouchers.station_id` or its foreign key (no longer needed, since rows are never removed).

## Open Questions

- None — scope, reversibility, and the precondition were all resolved in this session.

---
_This requirements document is the input for the **plan-architecture** skill._
_Next step: `/plan-architecture from: specs/requirements/REQ-station-archive-instead-of-delete.md`_
