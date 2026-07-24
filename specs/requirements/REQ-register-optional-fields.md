# Requirements: Register Page — Optional Field Labels

> **Date:** 2026-07-14
> **Type:** bugfix
> **Source:** docs/Brief_2.md (item 4)
> **Phase:** 1 of 5 (Requirement Engineering)

## Summary

Update the label text on the `/register` page's three trailing form fields (Company Name, Fleet Size, Preferred Areas) to consistently read "(Optional)" instead of their current mismatched phrasing ("If applicable" / "Select all that apply"). This is a copy-only change — all three fields are already optional in both the HTML form and the server-side handler, so no validation or behavior changes.

## Problem & Motivation

The `/register` form's optional fields currently use inconsistent, non-standard label suffixes ("If applicable", "Select all that apply") that don't clearly signal optionality to users the way a plain "(Optional)" tag would. This creates ambiguity about which fields are truly required. Standardizing on "(Optional)" improves clarity and consistency without changing any underlying logic.

## Users & Consumers

- **Prospective fleet customers filling out `/register`** — clearer signal of which fields they can skip.

## Functional Requirements

| ID  | Requirement                                                                                   | Acceptance Criterion                                                                                   |
|-----|--------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------|
| R1  | `company_name` field label reads "Company Name (Optional)".                                    | Rendered `/register` page shows this exact label text, replacing "Company Name (If applicable)".         |
| R2  | `fleet_size` field label reads "Total Number of Vehicles in Fleet (Optional)".                  | Rendered `/register` page shows this exact label text, replacing "...(If applicable)".                   |
| R3  | `areas` field label reads "Preferred Areas for Driver Re-Fueling (Optional)".                   | Rendered `/register` page shows this exact label text, replacing "...(Select all that apply)".           |
| R4  | No functional/validation change to any field.                                                   | Form still submits successfully with `company_name`, `fleet_size`, and `areas` left blank, exactly as it does today. |

## Non-Functional Requirements

_None identified — copy-only change._

## Behaviors & Domain Rules

**Copy-only, no logic touched:** All three fields (`company_name`, `fleet_size`, `areas`) already lack the `required` HTML attribute, and the server-side `/register` POST handler (`sanitize()` helper) already tolerates empty values for all three — an empty `company_name` simply falls back to a randomly generated account code. This REQ changes only the visible label text.

**Why this rule matters:**
- Confirms to the implementing developer that this is not a validation-relaxation task — the "optional" behavior already exists; only the label wording is inconsistent.

**Common mistakes:**
- Adding a `required` attribute removal "fix" that isn't needed — the fields are not currently required.
- Assuming a 4th field must change (the brief said "last four fields" but the page only has 3 trailing optional fields — confirmed as a miscount in the source brief during requirements review; the acknowledgement checkbox is explicitly excluded and stays required).

## Edge Cases & Failure Modes

| Scenario                                                    | Decision                                          | Rationale                                                                 |
|-----------------------------------------------------------------|------------------------------------------------------|--------------------------------------------------------------------------------|
| Brief says "last four fields" but only 3 trailing fields exist | Treat as brief miscount; scope to the 3 confirmed fields | User confirmed during interview — no 4th field exists on the current page.    |
| Acknowledgement checkbox ("I acknowledge...")                  | Remains required, unchanged                          | User explicitly excluded it from the optional-fields change.                  |
| User submits form with all 3 fields blank                      | Form submits successfully (already true today)      | No new validation logic introduced; behavior preserved as-is.                 |

## Decisions Log

| #   | Decision                                                                 | Alternatives Considered                                    | Chosen Because                                                                 |
|-----|-----------------------------------------------------------------------------|----------------------------------------------------------------|-------------------------------------------------------------------------------------|
| 1   | Scope is the 3 trailing fields (company_name, fleet_size, areas), not 4     | Include acknowledgement checkbox as the "4th" field             | User confirmed brief's "four" is a miscount; only 3 fields match the described pattern. |
| 2   | Acknowledgement checkbox stays required                                     | Make checkbox optional too                                       | User explicitly rejected making the checkbox optional.                             |
| 3   | Treat as copy-only change, no validation/HTML attribute changes             | Also touch `required` attributes / server validation             | Fields are already optional client- and server-side; confirmed via code review.    |

## Scope Boundaries

### In Scope
- Label text changes for `company_name`, `fleet_size`, and `areas` fields in `templates/register.html`.

### Out of Scope
- Any change to form validation, required attributes, or the `/register` POST handler (reason: fields are already optional; no logic change needed).
- Changes to the acknowledgement checkbox (reason: explicitly confirmed to stay required, unchanged).
- Changes to `/register-vehicle` (covered separately by REQ-cleanup-register-vehicle).

## Open Questions

_None — all decisions confirmed by user during interview._

---
_This requirements document is the input for the **plan-architecture** skill._
_Next step: `/plan-architecture from: specs/requirements/REQ-<slug>.md`_
