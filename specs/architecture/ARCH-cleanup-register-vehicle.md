# Architecture: Delete `/register-vehicle`

> **Date:** 2026-07-16
> **Phase:** 2 of 5 (System Architecture) — lightweight, `plan-architecture` skipped per its own "work under ~half a day" rule
> **Requirements source:** specs/requirements/REQ-cleanup-register-vehicle.md
> **Type:** refactor

## Architecture Summary

Straightforward deletion: remove the `/register-vehicle` Flask route, its template, and its dedicated test file, then repoint and relabel the Admin dashboard's button to `/register`. No new design — the route is redundant with `/book`'s existing "Add New Driver" flow, which shares the same underlying vehicle-preset CSV write path.

## Out of Scope

- `/book`'s preset read/write logic — shared infrastructure, untouched.
- Historical `specs/architecture/ARCH-*.md` docs referencing `/register-vehicle` — historical record, not live app surface.
- Any change to preset data itself — no migration needed.

---

# Tasks

## Task T1: Delete `/register-vehicle`, Fix Admin Nav

> **Status:** done
> **Verification:** test-after
> **Effort:** xs
> **Priority:** medium
> **Depends on:** None
> **Satisfies REQs:** R1, R2, R3, R4, R5
> **Footprint slice:** Modified: `main.py`, `templates/admin.html`; Deleted: `templates/register_vehicle.html`, `tests/test_register_vehicle.py`
> **High-risk areas touched:** None

### Description

`/register-vehicle` was added outside the original product scope and duplicates `/book`'s "Add New Driver" flow (both write to the same vehicle preset CSV). Remove the route, template, and its dedicated test file entirely; repoint and relabel the Admin dashboard's button to `/register`.

### Test Plan

#### Test File(s)
- `tests/test_register_vehicle.py` — deleted (no repurposable logic remains; `/book`'s own preset-write path already has coverage in `tests/test_book_pg.py`)
- New assertions added to an appropriate existing test file (e.g. `tests/test_admin_auth.py` or a new small file) for the 404 checks

#### Test Scenarios

##### Removal

- **GET /register-vehicle returns 404** — GIVEN the route is deleted WHEN requested THEN 404 _(verifies R1)_
- **POST /register-vehicle returns 404** — same, regardless of HTTP method _(verifies R1, edge case)_
- **templates/register_vehicle.html no longer exists** — file-existence check _(verifies R2)_

##### Admin Nav

- **admin.html button href points to /register** — GIVEN the dashboard renders THEN the button's `href` is `/register`, not `/register-vehicle` _(verifies R3)_
- **admin.html button text reads "Register"** — not "Register Vehicle" _(verifies R3)_
- **no other template/page references /register-vehicle or old text** — repo-wide search excluding historical spec docs _(verifies R4)_

##### Regression Guard

- **existing vehicle presets remain usable via /book** — GIVEN a preset written before this change WHEN `/book`'s preset-select flow loads THEN it still appears and works _(verifies R5 — the shared preset CSV write path must survive this deletion untouched)_
- **tests/test_book_pg.py's existing preset-related tests still pass** — confirms the shared infrastructure wasn't touched

### Implementation Notes

- **Key decision:** hard 404 on the old URL, no redirect — deliberate (REQ Decision #1), not an oversight
- **Pattern reference:** none needed — pure removal
- **High-risk callouts:** none; only shared-infrastructure risk is the preset CSV path, covered by the regression guard above

### Scope Boundaries

- Do NOT touch `/book`'s preset read/write logic — shared infrastructure, out of scope.
- Do NOT update historical `specs/architecture/ARCH-*.md` docs that reference `/register-vehicle` — historical record, not live app surface (REQ Out of Scope).

### Files Expected

**Modified files:**
- `main.py` (delete the `/register-vehicle` route, `main.py:597-637`)
- `templates/admin.html` (button href + text)

**Deleted:**
- `templates/register_vehicle.html`
- `tests/test_register_vehicle.py`

**Must NOT modify:**
- `/book`'s preset read/write logic in `main.py` (shared infrastructure)
