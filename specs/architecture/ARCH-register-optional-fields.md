# Architecture: Register Page — Optional Field Labels

> **Date:** 2026-07-16
> **Phase:** 2 of 5 (System Architecture) — lightweight, `plan-architecture` skipped per its own "work under ~half a day" rule
> **Requirements source:** specs/requirements/REQ-register-optional-fields.md
> **Type:** bugfix

## Architecture Summary

Pure copy change in `templates/register.html`: relabel 3 trailing form field labels to consistently read "(Optional)". No logic, validation, or backend changes — confirmed during the REQ interview that all 3 fields are already optional both client- and server-side.

## Out of Scope

- Any change to `/register`'s POST handler or validation logic (fields already optional).
- The acknowledgement checkbox (stays required, unchanged).
- `/register-vehicle` (deleted separately, unrelated REQ).

---

# Tasks

## Task T1: Relabel Optional Fields on `/register`

> **Status:** done
> **Verification:** tdd
> **Effort:** xs
> **Priority:** low
> **Depends on:** None
> **Satisfies REQs:** R1, R2, R3, R4
> **Footprint slice:** Modified: `templates/register.html`
> **High-risk areas touched:** None

### Description

Update the label text on 3 trailing `/register` form fields (`company_name`, `fleet_size`, `areas`) to consistently read "(Optional)", replacing the current mismatched phrasing ("If applicable" / "Select all that apply"). Copy-only — no validation or behavior change.

### Test Plan

#### Test File(s)
- New: `tests/test_register_optional_labels.py`

#### Test Scenarios

##### Label Text

- **company_name label reads "Company Name (Optional)"** — GIVEN `/register` renders THEN the label text matches exactly, replacing "(If applicable)" _(verifies R1)_
- **fleet_size label reads "Total Number of Vehicles in Fleet (Optional)"** — same _(verifies R2)_
- **areas label reads "Preferred Areas for Driver Re-Fueling (Optional)"** — replacing "(Select all that apply)" _(verifies R3)_

##### Regression Guard

- **form still submits successfully with all 3 fields blank** — GIVEN a POST to `/register` with `company_name`, `fleet_size`, `areas` omitted WHEN submitted THEN it still succeeds (redirects to `/register/success`), confirming no validation/behavior change was introduced _(verifies R4)_

### Implementation Notes

- **Key decision:** copy-only — do not add/remove `required` attributes or touch the POST handler; fields are already optional both client- and server-side (confirmed during REQ interview)
- **Pattern reference:** none needed — direct label text edit

### Scope Boundaries

- Do NOT touch `/register`'s POST handler or validation logic.
- Do NOT touch the acknowledgement checkbox.
- Do NOT touch `/register-vehicle` (separate REQ, already deleted).

### Files Expected

**New files:**
- `tests/test_register_optional_labels.py`

**Modified files:**
- `templates/register.html` (3 label text changes)

**Must NOT modify:**
- `main.py`'s `/register` POST handler
