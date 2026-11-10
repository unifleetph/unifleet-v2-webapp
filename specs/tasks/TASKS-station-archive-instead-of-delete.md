# Tasks

## Task T1: Backend — archive/unarchive, filters, routes

> **Status:** done
> **Verification:** tdd
> **Effort:** m
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** R1, R2, R3, R4, R5, R6
> **Footprint slice:** Modified: `db/schema.sql` (new `stations.archived_at` column, dual declaration), `price_store.py` (new `archive_station`, `unarchive_station`, `list_archived_stations`; `list_stations`, `list_all_stations`, `get_station` gain the unconditional archived filter), `main.py` (rename `admin_stations_delete` → `admin_stations_archive`, drop `_station_has_bookings` check; new `admin_stations_unarchive` route; `admin_stations` list route reads `?view=archived`; both new/renamed routes gain `append_audit`)
> **High-risk areas touched:** None (all Areas of Impact rated Low)

### Description

Adds the `archived_at` column and the two store functions that flip it, wires the admin routes (archive replaces delete entirely, a new unarchive route reverses it, the stations list honors `?view=archived`), and makes every existing station-read path unconditionally blind to archived stations regardless of their `include_inactive` flag. This is the entire backend — T2 (the UI button/link changes) cannot be built or verified until these routes and filters exist.

### Test Plan

#### Test File(s)
- `tests/test_price_store.py` (existing file, pattern: `test_station`/`schema_db` fixtures, mirrors the existing `delete_station`/`set_station_active` test sections)
- `tests/test_admin_stations.py` (existing file, pattern: `FakePriceStore`/`fake_price_store` fixture, adapts the existing delete-route test section)

#### Test Scenarios

##### Price-Store Layer — Archive/Unarchive

- **archive_station sets archived_at and returns the updated row** — GIVEN a deactivated `test_station` WHEN `price_store.archive_station(test_station)` is called THEN the returned row has a non-null `archived_at`, and `is_active` is unchanged (still `False`) _(verifies R1, R2)_
- **archive_station on an unknown id raises KeyError** — GIVEN no station with that id exists WHEN `archive_station` is called THEN `KeyError` is raised, mirroring `delete_station`'s/`set_station_active`'s existing behavior _(REQ edge case)_
- **unarchive_station clears archived_at, is_active stays False** — GIVEN an archived `test_station` WHEN `price_store.unarchive_station(test_station)` is called THEN `archived_at` is `None` in the returned row and `is_active` is still `False` (lands on "deactivated," not "active") _(verifies R5)_
- **unarchive_station on an unknown id raises KeyError** — same shape as the archive case _(REQ edge case)_

##### Price-Store Layer — Visibility

- **list_archived_stations returns only archived stations** — GIVEN one archived and one non-archived station WHEN `list_archived_stations()` is called THEN only the archived one is returned _(verifies R4)_
- **list_all_stations excludes archived even with include_inactive=True** — GIVEN an archived `test_station` WHEN `list_all_stations(include_inactive=True)` is called THEN it is not in the result, proving the archived filter is unconditional (no escape hatch) _(verifies R4, core contract)_
- **list_stations excludes archived even with include_inactive=True** — same shape, for `list_stations(fuel_type, include_inactive=True)` _(verifies R4)_
- **get_station returns None for an archived station** — GIVEN an archived `test_station` with a price row WHEN `get_station(test_station, fuel_type)` is called THEN it returns `None` _(verifies R4 consistency across all read paths)_

##### Regression Guard — Deactivated-but-not-Archived Stations Unaffected

- **a merely-deactivated station still appears with include_inactive=True** — GIVEN a deactivated (not archived) `test_station` WHEN `list_all_stations(include_inactive=True)` and `list_stations(fuel_type, include_inactive=True)` are called THEN it IS included in both — proving the new archived filter doesn't over-exclude and the pricing page's existing grayed-out display is unaffected _(guards backward-regression risk for `price_store.py`'s existing deactivated-station visibility)_

##### Admin Routes — Archive

- **archiving a deactivated station succeeds** — GIVEN a deactivated station WHEN `POST /admin/stations/<id>/archive` is called THEN it's a 302 redirect, a success flash is shown, and `archive_station` was called once _(verifies R1)_
- **archiving an active station is rejected** — GIVEN an active station WHEN the route is called THEN it redirects with an error flash and `archive_station` is NOT called _(verifies R2's precondition)_
- **archiving succeeds even when the station has existing bookings** — GIVEN a deactivated station AND a voucher referencing its name WHEN the route is called THEN it still succeeds (no booking check) — replaces the old delete-route's now-inverted `test_post_admin_stations_delete_blocked_matching_voucher_name` _(verifies R2's core behavior change)_
- **archiving an unknown station id 404s** — mirrors the old delete route's existing behavior _(REQ edge case)_
- **a store error during archive flashes instead of 500ing** — mirrors the old delete route's existing `test_post_admin_stations_delete_store_error_flashes_instead_of_500` _(guards backward-regression risk: error handling shape preserved through the rename)_

##### Admin Routes — Unarchive

- **unarchiving an archived station succeeds** — GIVEN an archived station WHEN `POST /admin/stations/<id>/unarchive` is called THEN it's a 302 redirect, success flash shown, `unarchive_station` called once _(verifies R5)_
- **unarchiving a non-archived or unknown station 404s and does not call unarchive_station** — GIVEN a station that exists but isn't archived (or doesn't exist at all) WHEN the route is called THEN it 404s and the store function is never invoked _(REQ edge case, mirrors R2's precondition symmetry)_

##### Admin Routes — Archived View

- **?view=archived lists only archived stations** — GIVEN one archived and one active station WHEN `GET /admin/stations?view=archived` is called THEN the response contains only the archived one _(verifies R4)_
- **no view param still shows the normal list** — GIVEN the same two stations WHEN `GET /admin/stations` (no query param) is called THEN the response is unaffected, same as before this task _(guards backward-regression risk for the existing default listing)_

##### Audit Trail

- **archiving writes one audit entry** — GIVEN a successful archive WHEN the route completes THEN `append_audit` was called once with an action naming the station and the new state _(verifies R6)_
- **unarchiving writes one audit entry** — same shape, for the reverse action _(verifies R6)_

### Implementation Notes

- **Module(s):** `price_store.py` owns all `stations` SQL (unchanged module boundary); `main.py` routes keep doing business-rule checks before calling the store's mechanical write functions, exactly as today's `admin_stations_delete`/`admin_stations_deactivate` already do.
- **Pattern reference:** `price_store.py::set_station_active` (~line 365) for the `UPDATE ... RETURNING` shape of `archive_station`/`unarchive_station`. `main.py::admin_recipient_delete`'s `append_audit` call shape (`voucher_id=None`, `note=`) for the two new audit calls.
- **Key decisions:** A1 (new nullable column, independent of `is_active`), A2 (un-archive always lands on "deactivated," never directly "active"), A4 (route renamed to `/archive`), A6 (booking check dropped entirely from this route — do not keep it as a no-op), A7 (audit logging required, unlike REQ1's lighter toggle).
- **Libraries:** none new — same `psycopg`/`dict_row` usage as every other `price_store.py` function.
- **High-risk callouts:** None — every Area of Impact for this task is rated Low.

### Scope Boundaries

- Do NOT touch `price_store.delete_station` or its 5 existing tests (`tests/test_price_store.py:260-326`) — left in place, unreferenced, per ARCH decision A5.
- Do NOT touch `main.py::_station_has_bookings` itself — only remove its call site inside the archive route; it's still used by `admin_stations_edit`'s rename guard.
- Do NOT add a schema change to `vouchers.station_id` or its foreign key — out of scope per REQ.
- Do NOT implement the template changes — that's T2's scope, not this task's.
- Only implement the column, the three new/modified store functions, the archived-filter additions, and the three route changes (archive rename, new unarchive, `?view=archived`).

### Files Expected

**Modified files:** _(from ARCH "Modified files / modules")_
- `db/schema.sql` (new `archived_at TIMESTAMPTZ` column, dual declaration)
- `price_store.py` (new `archive_station`, `unarchive_station`, `list_archived_stations`; archived filter added to `list_stations`, `list_all_stations`, `get_station`)
- `main.py` (`admin_stations_delete` renamed to `admin_stations_archive`, path changed, booking check removed; new `admin_stations_unarchive`; `admin_stations` list route reads `?view=archived`; both gain `append_audit`)
- `tests/test_price_store.py` (new scenarios above)
- `tests/test_admin_stations.py` (route-level scenarios above; `FakePriceStore` gains `archive_calls`/`unarchive_calls`/`list_archived_stations`, and `list_all_stations` gains an unconditional archived-exclusion matching the real store's A1 contract)

**Must NOT modify:**
- `price_store.py::delete_station` and `tests/test_price_store.py:260-326` (silent-regression hotspot — left as dead code per A5)
- `main.py::_station_has_bookings` (still used elsewhere — only its call site in the archive route is removed)
- `templates/admin_stations.html` (T2's scope, not this task's)

---

## Task T2: Admin UI — Archive button, Un-archive button, archived view

> **Status:** not started
> **Verification:** ui
> **Effort:** s
> **Priority:** medium
> **Depends on:** T1 (needs the real `/archive`, `/unarchive`, and `?view=archived` routes to point its forms/links at)
> **Satisfies REQs:** R1, R4, R5
> **Footprint slice:** Modified: `templates/admin_stations.html` ("Delete" button → "Archive", ~line 99-101; new archived-view link; archived-row rendering)
> **High-risk areas touched:** None (Low risk)

### Description

Renames the "Delete" button to "Archive" in the default stations list, adds a "View Archived Stations" link that switches the same page to the archived listing (via T1's `?view=archived`), and renders archived rows with just identity fields plus a single "Un-archive" button — no Edit form, no Deactivate/Reactivate. Plain HTML forms, no JavaScript, matching every other action in this template.

### Verification Checklist

#### Testable Seams
(component/render tests, Flask test client + HTML assertion — new section in `tests/test_admin_stations.py`, same style as T1's route tests)

- **Default view, deactivated row** — render `/admin/stations` with one deactivated station — expected: the old "Delete" button/form is gone, replaced by a form posting to `/admin/stations/<id>/archive` with a button reading "Archive"; a "View Archived Stations" link is present on the page
- **Default view, active row** — same page with an active station — expected: unchanged from today (Deactivate button only, no Archive/Delete form at all, matching the existing `{% if s.is_active %}` branch)
- **Archived view (`?view=archived`)** — render `/admin/stations?view=archived` with one archived station — expected: the row shows only id/brand/name/location plus a form posting to `/admin/stations/<id>/unarchive` with a button reading "Un-archive"; no Edit `<input>` fields, no Deactivate/Reactivate button; a "Back to Active Stations" link is present
- **Archived view, empty** — render the same URL with zero archived stations — expected: an empty table body, no error, no exception

#### Human Checklist
(one real click-through against the running app, evidence = screenshot or terminal confirmation of each redirect/flash)

- **Archive** — deactivate a real station, then click "Archive" — expected: redirects, success flash shown, station no longer appears in the default list
- **View Archived Stations** — click the link — expected: the just-archived station appears there with an "Un-archive" button, nothing else
- **Un-archive** — click "Un-archive" — expected: redirects, success flash shown, station reappears in the default list in the deactivated (grayed-out) state, exactly as any other deactivated station

### Implementation Notes

- **Module(s):** `templates/admin_stations.html` only — no Python/route changes (T1 already provides every route this task's forms/links point at).
- **Pattern reference:** the existing `{% if s.is_active %}` / Deactivate-Reactivate-Delete branch at `templates/admin_stations.html:82-102` — the archive/unarchive forms follow the identical `<form method="POST" action="...">` + `<button type="submit">` shape, same `?key=` query-param preservation convention used throughout this template.
- **Key decisions:** A3 (same template + `?view=archived` query param, not a separate template/route).
- **Libraries:** none — plain HTML forms, no JS.
- **High-risk callouts:** None.

### Scope Boundaries

- Do NOT render an Edit form, Deactivate, or Reactivate button for archived rows — per REQ R4/R5, archived stations are only ever un-archived from this view, nothing else.
- Do NOT add a confirm() dialog to the Un-archive button (not specified by REQ; keep parity with the lower-friction REQ1 precedent unless the developer asks otherwise at implementation time).
- Only implement the template change — the routes are T1's scope, already complete by the time this task starts.

### Files Expected

**Modified files:** _(from ARCH "Modified files / modules")_
- `templates/admin_stations.html` (Delete → Archive rename; archived-view link; archived-row rendering)
- `tests/test_admin_stations.py` (the 4 render-seam scenarios above, new section)

**Must NOT modify:**
- `price_store.py`, `main.py`, `db/schema.sql` — T1's scope, already complete
