# Requirements: Booking Confirmation Notice & Admin Station Delete

> **Date:** 2026-08-26
> **Type:** feature
> **Source:** docs/Brief-10.md + docs/Screenshot from 2026-08-26 15-49-04.png
> **Phase:** 1 of 5 (Requirement Engineering)

## Summary

Two small independent UI changes bundled into one sprint slice: (1) the booking confirmation page gains a reassurance note near the top and a clarified note under the payment QR code, and (2) the admin stations page gains a delete button so deactivated stations can be permanently removed from the list.

## Problem & Motivation

Customers landing on the booking confirmation page after submitting a booking currently have no explicit reassurance that their transaction is being processed, which drives avoidable "did this go through?" follow-up. Separately, deactivated stations accumulate in the admin stations list with no way to remove them, cluttering the admin view over time.

## Users & Consumers

- Customer who just submitted a booking — needs reassurance their transaction is being worked on and clear payment instructions.
- Admin managing stations at `/admin/stations` — needs to clear out deactivated stations that are no longer needed.

## Functional Requirements

| ID  | Requirement                                                                 | Acceptance Criterion                                                                                     |
|-----|------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------|
| R1  | Booking confirmation page shows a note: "Thank you for ordering from UniFleet. We are working to confirm your transaction. We'll get back to you as soon as we can." | Visiting the booking confirmation page after a booking shows this exact text in a note box below the "Booking Submitted" heading, matching the reference screenshot layout. |
| R2  | Booking confirmation page shows a note under the payment QR code reading "FREE BDO Transfer using BDO App" | The text "FREE BDO Transfer using BDO App" appears directly under the QR code image on the confirmation page. |
| R3  | Admin stations page shows a delete button only on stations currently marked deactivated | On `/admin/stations`, active stations show no delete button; deactivated stations show one. |
| R4  | Clicking delete prompts the admin to confirm before removing the station | Clicking the delete button opens a confirmation prompt; the station is not removed unless the admin confirms. |
| R5  | Confirming delete on a station with no booking history removes it from the list | After confirming, the station row disappears from `/admin/stations` and a success message is shown. |
| R6  | Confirming delete on a station that has any booking history is blocked | After confirming, the station is NOT removed; an inline error message (e.g. "Cannot delete: station has existing bookings") is shown and the station remains in the list. |

## Non-Functional Requirements

None identified beyond existing admin auth requirements (delete action must remain behind existing `/admin` authentication — no new access control needed).

## Behaviors & Domain Rules

**Booking confirmation notes (R1, R2):**
- The top note and QR note are two separate, independently placed pieces of text — not one combined note.
- QR note wording follows the reference screenshot ("FREE BDO Transfer using BDO App"), not the literal wording originally drafted in the brief ("FREE BDO transfer from BDO account") — the screenshot is the source of truth for this feature.

**Station delete (R3–R6):**
- Delete is only ever available for deactivated stations — an active station can never be deleted directly; it must be deactivated first (deactivation flow is out of scope here, assumed to already exist).
- A station with any booking history is protected from deletion to avoid orphaning historical booking records that reference it.

**Why these rules matter:**
- Blocking delete on stations with booking history prevents broken references in booking/reporting history (e.g. a past booking pointing to a station that no longer exists).
- Confirmation dialog prevents accidental irreversible deletion by an admin.

**Common mistakes:**
- Making the delete button available for active stations (should be deactivated-only).
- Hard-deleting a station without checking for existing booking references first.
- Using the brief's literal QR note wording instead of the screenshot's wording.

## Edge Cases & Failure Modes

| Scenario                                                              | Decision                                                        | Rationale                                                        |
|------------------------------------------------------------------------|------------------------------------------------------------------|---------------------------------------------------------------------|
| Admin clicks delete on a deactivated station with zero bookings ever   | Delete succeeds, row removed, success message shown             | No data integrity risk                                              |
| Admin clicks delete on a deactivated station with past bookings        | Delete blocked, inline error shown, station stays in list       | Preserves referential integrity of historical booking data          |
| Admin opens confirm dialog then cancels                                | No change; station remains, no delete attempted                 | Confirmation must be a real gate, not a formality                   |
| Station is reactivated (no longer deactivated) after page load but before delete click | Out of scope for this REQ — assume existing admin page refresh/reload behavior governs this; not a new requirement | Reactivation-during-view race is an existing admin-page concern, not unique to delete |

## Decisions Log

| #   | Decision                                                                 | Alternatives Considered                                     | Chosen Because                                                        |
|-----|----------------------------------------------------------------------------|------------------------------------------------------------------|----------------------------------------------------------------------------|
| 1   | QR note text uses screenshot wording ("FREE BDO Transfer using BDO App") over brief's literal wording | Use brief's literal text; use both texts in different spots | Screenshot represents the finalized visual design; brief text likely an earlier draft |
| 2   | Bundle both changes into one REQ                                          | Split into two REQs                                              | Both changes are small enough to fit comfortably in one sprint slice        |
| 3   | Delete blocked when station has any booking history                       | Hard delete regardless; soft delete/hide instead                 | Avoids orphaning booking records that reference the station                |
| 4   | Confirmation dialog required before delete                                | No confirmation, immediate delete                                | Delete is irreversible; prevents accidental admin clicks                   |
| 5   | Delete button shown only on deactivated stations (not shown/disabled on active) | Always show button, disabled for active stations           | Matches brief wording; keeps active-station rows uncluttered               |

## Scope Boundaries

### In Scope
- Adding the two note texts to the booking confirmation page.
- Adding a delete button, confirm dialog, and success/blocked-error handling to `/admin/stations` for deactivated stations.

### Out of Scope
- Station deactivation flow itself (reason: assumed to already exist; not part of this brief).
- Bulk delete of multiple stations at once (reason: not requested).
- Any change to active-station display or behavior beyond hiding the delete button (reason: not requested).

## Open Questions

- Exact wording/format of the "Cannot delete" inline error message is not pinned down.
  - **Impact if unresolved:** Minor wording variance in the error message.
  - **Suggested default:** "Cannot delete: this station has existing bookings."

---
_This requirements document is the input for the **plan-architecture** skill._
_Next step: `/plan-architecture from: specs/requirements/REQ-booking-confirmation-note-and-station-delete.md`_
