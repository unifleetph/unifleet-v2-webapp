# Requirements: Remove /register-vehicle Page

> **Date:** 2026-07-14
> **Type:** refactor
> **Source:** docs/Brief_2.md (item 2)
> **Phase:** 1 of 5 (Requirement Engineering)

## Summary

Remove the `/register-vehicle` page entirely — it was added outside the original product scope and is now redundant, since `/book` already provides an equivalent "Add New Driver" flow that writes to the same vehicle preset data. Only `/register` (customer/company registration) should remain as a registration entry point. The Admin dashboard's "Register Vehicle" button is relabeled "Register" and repointed to `/register`.

## Problem & Motivation

`/register-vehicle` was identified as an unintended addition — not part of the original product design. It duplicates functionality already available inline in the `/book` form's "Add New Driver" path, creating two ways to do the same thing and confusing the intended registration flow (`/register` = company/customer onboarding). Removing it simplifies the product surface and removes a maintenance burden (duplicate form, duplicate validation, duplicate test coverage).

## Users & Consumers

- **Admin dashboard users** — currently click "Register Vehicle" button expecting a driver/vehicle registration flow; will now be routed to `/register`.
- **Anyone with a bookmarked/old `/register-vehicle` link** — will receive a 404 going forward.

## Functional Requirements

| ID  | Requirement                                                                 | Acceptance Criterion                                                                 |
|-----|------------------------------------------------------------------------------|----------------------------------------------------------------------------------------|
| R1  | The `/register-vehicle` route is removed from the application.             | `GET /register-vehicle` and `POST /register-vehicle` both return HTTP 404.            |
| R2  | The `register_vehicle.html` template is deleted.                            | File no longer exists in `templates/`.                                                |
| R3  | The Admin dashboard "Register Vehicle" button is relabeled and repointed.   | Button text reads "Register"; its link (`href`) points to `/register`.                |
| R4  | No other page/template references `/register-vehicle` or old button text.  | Repo-wide search for `register-vehicle` and "Register Vehicle" returns no live template refs (spec/docs history excluded). |
| R5  | Existing vehicle preset data written by the old page remains usable.       | Presets previously saved via `/register-vehicle` still appear/selectable in `/book`'s driver-select flow, unmodified. |

## Non-Functional Requirements

_None identified — this is a removal with no new performance, security, or scale surface._

## Behaviors & Domain Rules

**Removal, not redirect:** Hitting the old URL (GET or POST) results in a plain 404 — no redirect to `/register` or `/book`. This is a deliberate choice to make the removal unambiguous rather than silently reinterpreting old links.

**Preset data is shared infrastructure, not owned by this page:** The vehicle preset CSV (per account code) is written by both the old `/register-vehicle` page and `/book`'s "Add New Driver" path. Deleting `/register-vehicle` must not touch this data or the preset read/write logic used by `/book` — only the now-redundant write path via `/register-vehicle` itself goes away.

**Why these rules matter:**
- Removing the duplicate entry point without breaking `/book`'s preset flow keeps drivers/vehicles previously registered still bookable.
- A hard 404 rather than a redirect avoids masking the fact that a distinct page was removed, which matters for anyone auditing old links/docs.

**Common mistakes:**
- Deleting or altering `data_paths.preset_csv_path` / preset read-write logic in `main.py`, thinking it's exclusive to `/register-vehicle` — it isn't; `/book` depends on it too.
- Only removing the nav link/button on `/admin` without deleting the route, template, and test file (brief explicitly calls this out as insufficient).
- Missing the button *text* change — brief only mentions swapping the `href`, but this REQ also changes the label from "Register Vehicle" to "Register".

## Edge Cases & Failure Modes

| Scenario                                                        | Decision                                   | Rationale                                                              |
|-------------------------------------------------------------------|---------------------------------------------|--------------------------------------------------------------------------|
| User hits old `/register-vehicle` URL (bookmark, external link) | Return 404, no redirect                     | Explicit product decision — page is gone, not moved.                    |
| POST to `/register-vehicle` after removal (e.g. stale form submit) | Same 404 behavior as GET                    | Route no longer exists; no special-casing by HTTP method.               |
| Old test suite still asserts `/register-vehicle` behavior       | `tests/test_register_vehicle.py` deleted/updated so CI doesn't fail | Route removal makes existing assertions invalid.       |
| Preset CSV data written before this change                      | Left untouched, remains readable via `/book` | No data migration needed — `/book` already reads/writes the same file. |

## Decisions Log

| #   | Decision                                                        | Alternatives Considered                        | Chosen Because                                                        |
|-----|-------------------------------------------------------------------|--------------------------------------------------|---------------------------------------------------------------------------|
| 1   | Old URL returns 404 rather than redirecting                     | Redirect to `/register`; redirect to `/book`     | User explicitly chose plain 404 (option a) — cleanest signal that the page is gone. |
| 2   | Admin button text changes from "Register Vehicle" to "Register" | Keep text unchanged, only swap `href` (brief's literal wording) | User confirmed text should also change, for consistency with new destination. |
| 3   | Full deletion of route, template, and test file                 | Just hide/remove the nav link                     | User confirmed brief's intent: only `/register` should exist at all, not merely be unlinked. |

## Scope Boundaries

### In Scope
- Deleting the `/register-vehicle` Flask route in `main.py`.
- Deleting `templates/register_vehicle.html`.
- Deleting or repurposing `tests/test_register_vehicle.py` so the suite passes.
- Updating the Admin dashboard button (`templates/admin.html`) href and label.

### Out of Scope
- Any changes to the `/register` page itself (covered separately by REQ-register-optional-fields).
- Any changes to `/book`'s "Add New Driver" / preset logic (unaffected, must keep working as-is).
- Migrating or altering existing preset CSV data (reason: no migration needed, data format unchanged).
- Updating historical architecture docs (`specs/architecture/ARCH-*.md`) that reference `/register-vehicle` as part of project history (reason: historical record, not live app surface).

## Open Questions

_None — all decisions confirmed by user during interview._

---
_This requirements document is the input for the **plan-architecture** skill._
_Next step: `/plan-architecture from: specs/requirements/REQ-<slug>.md`_
