# Architecture: Archive Stations Instead of Deleting Them

> **Date:** 2026-11-10
> **Phase:** 2 of 5 (System Architecture)
> **Requirements source:** specs/requirements/REQ-station-archive-instead-of-delete.md
> **Tasks:** TASKS-station-archive-instead-of-delete.md
> **Type:** refactor

## Architecture Summary

A new nullable `archived_at` column on `stations`, independent of the existing `is_active` flag, reached only from the deactivated state. The current hard-delete route is repurposed into an archive route (no more `DELETE FROM stations`), a new unarchive route reverses it, and every existing station-read path gains an unconditional "archived is invisible" filter — regardless of whatever `include_inactive` flag that path already has. A dedicated view (same template, `?view=archived` query param) is the only place archived stations are ever listed again. The hard-delete function (`price_store.delete_station`) stays in the codebase, unused, per the developer's call — nothing calls it after this change.

## High-Level Structure

```
Admin dashboard (admin_stations.html)
  ├─ Active list (default)              ├─ Archived list (?view=archived)
  │   "Archive" button (was "Delete")   │   "Un-archive" button only
  │   POST .../archive                  │   POST .../unarchive
  │        │                            │        │
  │        ▼                            │        ▼
  │  main.py::admin_stations_archive    │  main.py::admin_stations_unarchive
  │   - must be deactivated first       │   - must currently be archived
  │   - no longer checks bookings       │
  │        │                            │        │
  │        ▼                            │        ▼
  └─ price_store.archive_station()  ────┴──  price_store.unarchive_station()
         sets archived_at = NOW()            sets archived_at = NULL
         (is_active untouched, stays FALSE)  (is_active untouched, stays FALSE
                                               -- station lands back on
                                               "deactivated", not "active")

Every existing read path (list_stations, list_all_stations, get_station):
  gains an unconditional "AND archived_at IS NULL" -- archived stations
  are invisible everywhere except list_archived_stations() (new), which
  only the ?view=archived branch calls.
```

## Tech Choices

| Area | Decision | Alternatives Considered | Rationale |
|------|----------|---------------------------|-----------|
| Storage | New nullable `archived_at TIMESTAMPTZ` column, independent of `is_active` | A single `status` enum column (active/deactivated/archived) replacing `is_active` | A separate column preserves `is_active`'s existing meaning and every query that already reads it; an enum migration would touch every `is_active` call site for no behavioral gain |
| Archived-view mechanism | Same template + route, `?view=archived` query param | New dedicated template/route | Developer's explicit call: smaller footprint, the row rendering difference (no Edit form, one button) is a straightforward Jinja branch |
| Route naming | Rename `/admin/stations/<id>/delete` → `/admin/stations/<id>/archive` | Keep `/delete` path, change only behavior | Developer's explicit call: URL should describe what the action does; internal-only form action, no external consumer |
| Dead code | Leave `price_store.delete_station()` and its 5 tests in place, unused | Remove the function and its tests entirely | Developer's explicit call |

## Patterns & Conventions

- **`UPDATE ... RETURNING` for state flips** — `archive_station`/`unarchive_station` mirror `set_station_active`'s existing shape exactly (single UPDATE, `RETURNING` the updated row, `KeyError` if no row matched).
- **Route checks business rules, store function does the mechanical write** — same division of responsibility as every existing `price_store`/`main.py` pair (e.g. `admin_stations_delete` checked `is_active`/bookings before calling the dumb `delete_station`); `admin_stations_archive` keeps checking "must be deactivated first" itself, `archive_station` just writes.
- **Unconditional archived-filter, not a toggle** — unlike `is_active` (which has an `include_inactive` escape hatch used by the pricing page's grayed-out display), archived stations get no such escape hatch in the normal read paths. The only way to see one is the dedicated `list_archived_stations()`, called only from the `?view=archived` branch.
- **Not applied:** no new ORM/migration tool, no soft-delete library — this follows the project's existing raw-SQL-in-`price_store.py` convention exactly.

## Data Models

### `stations.archived_at`

**Purpose:** marks a station as permanently removed from normal admin/booking views while preserving its row (and every price/discount/history row still referencing it) intact.

**Key fields:**
| Field | Type / Constraint | Notes |
|-------|----------------------|-------|
| `archived_at` | `TIMESTAMPTZ`, nullable | `NULL` = not archived (default for every existing and new row); a timestamp = archived, and records *when* |

**Relationships:** none new — no FK, no new table. Existing `prices`, `discounts`, `price_history`, `discount_history`, and `vouchers.station_id` all keep referencing the same never-deleted `stations.id`.

**Lifecycle:** `NULL` at creation → admin deactivates (`is_active: TRUE → FALSE`, `archived_at` untouched) → admin archives (`archived_at: NULL → NOW()`, `is_active` untouched, stays `FALSE`) → admin un-archives (`archived_at: NOW() → NULL`, station is now simply "deactivated" again, `is_active` still `FALSE`) → admin reactivates separately if wanted (`is_active: FALSE → TRUE`, existing route, unchanged).

## API Contracts / Interfaces

### Admin station routes (`main.py`)

**Boundary:** HTTP, admin-session/key-gated (`require_admin`, same as every sibling `/admin/stations/...` route).

| Method/Op | Path | Purpose | Errors / Returns |
|-----------|------|---------|----------------------|
| POST (renamed from `.../delete`) | `/admin/stations/<station_id>/archive` | Archives a deactivated station, regardless of booking history | 404 if station not found; flash error + redirect if `is_active` is `TRUE`; flash success + redirect on success |
| POST (new) | `/admin/stations/<station_id>/unarchive` | Returns an archived station to the deactivated state | 404 if the station isn't currently archived; flash success + redirect on success |
| GET (extended) | `/admin/stations?view=archived` | Lists archived stations instead of active/deactivated ones | Same route as today, branches on the query param |

**Auth requirements:** admin only, identical to every sibling `/admin/stations/...` route.

### Internal store functions (`price_store.py`, not externally callable)

| Function | Signature | Purpose |
|----------|-----------|---------|
| `archive_station` | `(station_id: str) -> Dict` | Sets `archived_at = NOW()`; raises `KeyError` if the station doesn't exist. Does not check `is_active` — that's the route's job, mirroring the existing `delete_station`/route split. |
| `unarchive_station` | `(station_id: str) -> Dict` | Sets `archived_at = NULL`; raises `KeyError` if the station doesn't exist. |
| `list_archived_stations` | `() -> List[Dict]` | Returns only stations where `archived_at IS NOT NULL`. The only read path that can see an archived station. |

## Module Boundaries

Unchanged. `price_store.py` remains the sole owner of all `stations`/`prices`/`discounts` SQL; `main.py` routes keep doing business-rule checks (deactivated-first, archived-only-for-unarchive) before calling the store's mechanical write functions, exactly as today's `admin_stations_delete`/`admin_stations_deactivate` already do.

## Change Footprint

### New files / modules

None.

### Modified files / modules

| Path | What changes here |
|------|----------------------|
| `db/schema.sql` | Add `archived_at TIMESTAMPTZ` (nullable) to the `stations` `CREATE TABLE IF NOT EXISTS` block, plus a mirrored `ALTER TABLE stations ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;` (same dual-declaration convention as every prior column addition in this schema) |
| `price_store.py` | New `archive_station`, `unarchive_station`, `list_archived_stations` (near `set_station_active`, ~line 365). `list_stations` (~line 160), `list_all_stations` (~line 202), `get_station` (~line 220) each gain an unconditional `AND archived_at IS NULL` / `AND s.archived_at IS NULL` clause. `delete_station` (~line 384) left untouched, now unreferenced |
| `main.py` | `admin_stations_delete` (~line 2301) renamed to `admin_stations_archive`, route path changed to `.../archive`, the `_station_has_bookings` check (currently ~line 2314) removed; new `admin_stations_unarchive` route added beside it; the `admin_stations` list route (~line 2214-2240) reads `request.args.get('view')` and calls `price_store.list_archived_stations()` instead of `list_all_stations()` when it equals `"archived"`; both new/renamed routes gain an `append_audit` call (mirroring `recipient_delete`'s `voucher_id=None`, `note=` shape) |
| `templates/admin_stations.html` | "Delete" button (~line 99-101) becomes "Archive", action URL updated; new "View Archived Stations" / "Back to Active Stations" link near the page header; when rendering the archived view, each row shows id/brand/name/location plus a single "Un-archive" button — no Edit form, no Deactivate/Reactivate |
| `tests/test_admin_stations.py` | 6 of the 7 existing delete-route tests (lines ~226-305) renamed/adapted to the new `/archive` path and `archive_station` mock; `test_post_admin_stations_delete_blocked_matching_voucher_name` is **removed** (archiving now succeeds regardless of booking history — the opposite of the old assertion); new tests added for the unarchive route and the `?view=archived` listing |

### Deleted / replaced

None — `price_store.delete_station` is left in place per the developer's call, just no longer called.

### Touched but not changed (silent-regression hotspots)

| Path | Why it matters |
|------|------------------|
| `main.py::_station_has_bookings` | Still used by the unrelated rename-guard inside `admin_stations_edit` (~line 2265) — must keep working exactly as today; only its call inside the archive route goes away |
| `price_store.py::delete_station` + `tests/test_price_store.py:260-326` (its 5 existing tests) | Deliberately left as unreferenced dead code — must not be touched, removed, or "cleaned up" as part of this work |
| `templates/admin_prices.html` | Benefits from the new archived-filter in `list_stations`/`list_all_stations` with zero template change of its own — an archived station simply stops appearing on the pricing page; the existing grayed-out *deactivated* station rendering must be unaffected |
| `tests/test_price_store.py` (existing `is_active`/`list_stations`/`list_all_stations` tests) | Must keep passing unchanged, proving the new archived-filter is additive and doesn't alter deactivated-station visibility |

## Areas of Impact

| Area | Impact | Risk (L/M/H) | Why |
|------|--------|---------------|-----|
| Admin station management | Delete button becomes Archive; new Unarchive flow; new Archived view | L | Internal-only admin UI change, no external contract |
| Pricing page (`admin_prices.html`) | Archived stations silently stop appearing | L | Purely narrowing filter; a station that's not archived is completely unaffected |
| Booking flow (`/book`) | None expected | L | `list_stations()`'s existing `include_inactive=False` default already excludes anything not fully active; archived stations were already deactivated (and thus already hidden) before being archived |
| Database schema | One new nullable column | L | Additive, idempotent (`IF NOT EXISTS` convention), no backfill, no FK, no index needed (station counts are small; app-level filtering, not a hot query path) |

**Contract changes:** the `/admin/stations/<id>/delete` URL is removed (renamed to `/archive`) — internal-only, referenced solely from `admin_stations.html`'s own form, no external consumer.

**Cross-cutting ripples:** none beyond the one new column and the two new audit-log action values (`station_archive`, `station_unarchive`). No auth changes, no new env vars, no feature flag, no deploy-order constraint — the `ALTER TABLE ... IF NOT EXISTS` runs on every container start via the existing `db/apply.py` convention.

## Cross-Cutting Concerns

- **Errors:** both new/renamed routes follow the existing station-route precedent exactly — flash message + redirect for expected failures (not found, wrong state). *(Code-review correction, 2026-11-10: this actually matches the old `admin_stations_delete`'s/`admin_stations_edit`'s broader shape — pre-check, then `except KeyError`, then a catch-all `except Exception` that flashes instead of 500ing — not `admin_stations_deactivate`'s/`admin_stations_reactivate`'s narrower `KeyError`-only handling. That asymmetry between the four station-lifecycle routes predates this change and isn't drift introduced here, but the original wording above overstated which sibling it matches.)*
- **Logging & metrics:** two new `audit_log` action values, `station_archive` and `station_unarchive`, each with `voucher_id=None` and a `note` naming the station — matching the REQ's explicit decision that this (unlike REQ1's lighter toggle) deserves an audit trail.
- **Auth / authz:** `require_admin`, identical check and mechanism to every other `/admin/stations/...` route.
- **Performance:** negligible — one additional boolean-ish column check per existing station query, one additional single-row `UPDATE` per archive/unarchive action. Station counts are small (dozens, not thousands); no new index needed.
- **Security:** no new input surface beyond the existing `<station_id>` path parameter pattern; no injection risk (parameterized queries throughout, matching every other `price_store.py` function).
- **Migrations / rollout:** additive nullable column, `IF NOT EXISTS` on both the `CREATE TABLE` and `ALTER TABLE` lines — applies cleanly on every existing and fresh database with zero backfill, exactly like every prior schema addition in this project.

## Architecture Decisions Log

| # | Decision | Alternatives | Chosen Because | Satisfies REQs |
|---|----------|---------------|------------------|-----------------|
| A1 | New nullable `archived_at` column, independent of `is_active` | Replace `is_active` with a 3-state `status` enum | Preserves every existing `is_active` call site and its meaning; an enum migration would touch far more code for the same behavior | R1, R2, R3 |
| A2 | Archive/unarchive reached only from the deactivated state; archiving never changes `is_active`, un-archiving always lands back on "deactivated" | Un-archive could restore directly to whatever state preceded archiving | Developer's explicit call: forces the same deliberate reactivation step every other deactivated station goes through | R2, R5 |
| A3 | Archived-view via same template + `?view=archived` query param | New dedicated template/route | Developer's explicit call: smaller footprint for a straightforward conditional row-rendering difference | R4 |
| A4 | Route renamed `/delete` → `/archive` | Keep `/delete` path, just change behavior | Developer's explicit call: URL should describe what the action does; internal-only, no external consumer | — (naming clarity, no REQ-ID) |
| A5 | `price_store.delete_station()` and its tests left in place, unreferenced | Remove the function and its 5 tests entirely | Developer's explicit call | — |
| A6 | Archiving drops the booking-history check entirely (no longer calls `_station_has_bookings`) | Keep the check as a no-op safeguard | R2 explicitly requires archiving to succeed regardless of booking history; keeping a check that can never block anything is dead logic | R2 |
| A7 | Archive/unarchive write an audit log entry | Skip logging (as REQ1's lighter toggle does) | REQ's explicit decision — more consequential than REQ1's flag (this is effectively delete/restore of a station), warrants the same audit trail as `delete_order` | R6 |

## Risk & Stress-Test Scenarios

### Forward — runtime failure scenarios

| Scenario | How the Design Handles It |
|----------|------------------------------|
| A customer has `/book` open with the station's dropdown already rendered, and an admin archives it mid-session | No crash — `price_store` queries are live, not cached; the customer's already-rendered page is unaffected, and the next page load simply won't show the archived station. Submitting a booking against it would persist the station *name* as always (denormalized, no FK check at booking time), same as today for a deactivated station. |
| Two admins archive the same station simultaneously | Both `UPDATE ... RETURNING` calls succeed independently (no uniqueness constraint on `archived_at`); the later one's timestamp simply wins. No corruption, no error — acceptable for an admin-only, low-traffic action. |
| DB connection drops mid-archive (pool exhaustion) | Follows the existing station-route precedent: the route's own try/except around the `price_store` call (already present in `admin_stations_delete` today) catches it and flashes an error rather than 500ing — no new failure mode introduced. |
| `archive_station` called on a station that's already archived (double-click) | `archived_at` is simply set to `NOW()` again — idempotent in effect (still archived), no error. The route's own "must be deactivated first" check still passes (archiving never changed `is_active`), so this is a harmless no-op re-archive, not a rejection. |

### Backward — regression risk per touched area

| Touched area (from Change Footprint) | What could regress | How we'd know / mitigation |
|---------------------------------------|-----------------------|-------------------------------|
| `price_store.list_stations` / `list_all_stations` / `get_station` | The new unconditional `archived_at IS NULL` clause could accidentally also exclude deactivated-but-not-archived stations if written wrong, breaking the pricing page's existing grayed-out display | Existing `tests/test_price_store.py` tests for deactivated-station visibility (via `include_inactive=True`) must keep passing unchanged — they'd fail immediately if the new clause over-filtered |
| `main.py::admin_stations` (list route) | The new `?view=archived` branch could accidentally become the default (hiding active stations when no query param is present) | New test asserting the *default* (no `view` param) still shows `list_all_stations()`'s normal output, alongside the new archived-view test |
| `main.py::admin_stations_archive` (renamed route) | Removing the `_station_has_bookings` check correctly changes behavior (archiving now succeeds with bookings) — but the "must be deactivated first" check must survive the rename intact | The adapted test suite explicitly keeps `test_post_admin_stations_delete_blocked_when_station_active`'s equivalent (renamed, not removed) — only the booking-blocked test is removed, per A6 |
| `price_store.delete_station` (touched-but-not-changed) | None expected — it's simply unreferenced now | Its 5 existing tests continue to pass unchanged, proving the function itself still works even though nothing calls it |

## Open Questions

None — every decision needed to implement this was resolved during REQ and this architecture session.

## Out of Scope

- Any schema change to `vouchers.station_id` or its foreign key (per REQ — no longer needed, since station rows are never removed).
- Removing `price_store.delete_station()` or its tests (per developer's explicit call, A5).
- Bulk archive/un-archive actions (not raised in the brief).
- Any change to the existing active/deactivated lifecycle, its reactivation flow, or the `_station_has_bookings` rename-guard in `admin_stations_edit`.
