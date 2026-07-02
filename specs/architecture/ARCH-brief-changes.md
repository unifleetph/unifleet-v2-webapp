# Architecture: Brief Changes (Sajid Task Tracker)

> **Date:** 2026-07-02
> **Phase:** 2 of 5 (System Architecture)
> **Requirements source:** Standalone brief — `docs/Brief_changes.md` — see Inferred Requirements
> **Type:** feature + infrastructure (mixed)

## Architecture Summary

Five loosely-related changes to the UniFleet v2 Flask monolith. Three are in-repo
code changes: (2) redirect the site root to the Booking page instead of the admin
dashboard, (3) add real password/session authentication to the admin dashboard,
(4) add a standalone "Register Vehicle" page reachable from the booking page, and
(10) hide the fixed "Diesel" Fuel Type field on the Add-New-Driver form while still
submitting `Diesel`. Two are infrastructure/DNS tasks captured as a runbook rather
than code: (1) a Railway staging environment, and (2-apex) making the bare
`unifleet.asia` domain resolve to the site. All code changes land in the single-file
`main.py`, existing Jinja2 templates, and two new templates. No database schema
changes. Backward compatibility with the existing `?key=` admin flow is preserved.

## Inferred Requirements

| ID  | Inferred Requirement | Source |
|-----|----------------------|--------|
| R1  | Configure a Railway staging/test environment separate from production, for testing changes before prod deploy. | Brief item 1 |
| R2  | Visiting the site root directs users to the Booking page, not the admin dashboard. | Brief item 2 |
| R3  | Bare apex domain `unifleet.asia` (not only `www.unifleet.asia`) resolves to the site. | Brief item 2 |
| R4  | Admin dashboard requires a password/authentication to prevent unauthorized access. | Brief item 3 |
| R5  | Booking page offers a "Register Vehicle" action for users who need to register a vehicle before booking. | Brief item 4 |
| R6  | Fuel Type field ("Diesel") on the Add-New-Driver form is hidden, without breaking downstream functionality; fuel selection will later happen on the Booking page. | Brief item 10 |

## High-Level Structure

Single-process Flask monolith (`main.py`), server-rendered Jinja2, Postgres on
Railway. No blueprints. Changes slot into the existing flat-route structure.

```
Browser
  │
  ├─ GET /                → redirect /book              [R2, was /form]
  │
  ├─ GET /book            → book.html                   [R5: + Register Vehicle link]
  │                                                     [R6: fuel field hidden]
  ├─ GET/POST /register-vehicle → register_vehicle.html [R5: NEW]
  │        POST → append preset CSV (dedup by plate)
  │
  ├─ GET/POST /admin/login  → admin_login.html          [R4: NEW]
  │        POST → check ADMIN_PASSWORD → session['admin']=True
  ├─ GET /admin/logout      → clear session             [R4: NEW]
  └─ /admin/*  guarded by require_admin(session OR key) [R4: MODIFIED]

Infra (runbook, no code):
  Railway staging service off `dev` branch, own DATABASE_URL   [R1]
  DNS: apex unifleet.asia → Railway (A/ALIAS)                  [R3]
```

## Tech Choices

| Area | Decision | Alternatives Considered | Rationale |
|------|----------|-------------------------|-----------|
| Admin auth | Flask server-side session cookie via `session` + login page | HTTP Basic Auth; keep `?key=` only | Best UX, supports logout, no creds in URL; session cookie auto-sent on same-origin fetch so existing admin JS keeps working |
| Session secret | `SECRET_KEY` env var, random per-process fallback in dev | Hardcoded key (current) | Sessions require a stable, non-public secret; hardcoded `"your_secret_key_here"` is insecure |
| Admin password | `ADMIN_PASSWORD` env var | Reuse `ADMIN_KEY` as password | Separates human login secret from the API/bookmark key; both can coexist |
| Register-vehicle storage | Reuse per-customer preset CSV (`data/presets/<code>_presets.csv`) | New DB table; new CSV | Presets already store exactly these fields and the write pattern already exists at `main.py:752-761` |
| Fuel field hide | `type="hidden"` input, value `Diesel` | Remove field entirely | Preserves the POST contract (`main.py:607`) and downstream `Diesel` assumptions with minimal risk |
| Infra items | Runbook doc, not code | Full IaC / rw.txt env matrix | Railway service creation + DNS are console/registrar actions; low value to encode in repo now |

## Patterns & Conventions

- **Single-file routes** — from AGENTS.md; all new routes go flat on `app` in `main.py`, no blueprints.
- **Guard helper pattern** — mirror existing `_check_admin_key` (`main.py:192-194`); add sibling `require_admin`.
- **CSV preset write** — reuse dedup-by-plate + append idiom (`main.py:752-761`), `utf-8-sig` encoding.
- **`data_paths` registry** — all file paths via `data_paths.preset_csv_path()`; never hardcode.
- **Redirect idiom** — `redirect("/book")` matches existing `redirect("/form")` at `main.py:201`.
- **Not applied:** no new auth library (Flask-Login etc.) — the built-in `session` is enough for one admin role.

## Data Models

No new database entities. No schema change.

### Preset (existing, reused by R5)

**Purpose:** Per-customer saved driver+vehicle, used to prefill bookings.

**Storage:** `data/presets/<account_code>_presets.csv` (`data_paths.preset_csv_path`).

**Key fields:**
| Field | Type | Notes |
|-------|------|-------|
| driver_name | str | |
| vehicle_plate | str | dedup key (upper, stripped) |
| truck_make | str | |
| truck_model | str | |
| number_of_wheels | str/int | |
| fuel_type | str | defaults `Diesel` |

**Lifecycle:** created on first booking with a new plate (`main.py:759-761`), or — new — via `/register-vehicle`. No update/delete path.

### Admin session (new, R4)

**Purpose:** Marks an authenticated admin browser session.

**Storage:** Flask signed session cookie. Key: `session['admin'] = True`. Cleared on `/admin/logout`.

## API Contracts / Interfaces

### main.py routes

**Boundary:** HTTP (Flask), server-rendered HTML + redirects.

| Method/Op | Path / Signature | Purpose | Errors / Returns |
|-----------|------------------|---------|------------------|
| GET | `/` | Redirect to booking | 302 → `/book` |
| GET | `/admin/login` | Login form | 200 `admin_login.html` |
| POST | `/admin/login` | Verify password | ok → set session, 302 → `/admin/prices`; bad → 200 form + flash error |
| GET | `/admin/logout` | End session | 302 → `/admin/login` |
| GET | `/register-vehicle` | Vehicle registration form | 200 `register_vehicle.html` |
| POST | `/register-vehicle` | Save preset | ok → success/redirect; missing fields → 200 form + flash |
| GET | `/admin/prices` | Admin UI (MODIFIED guard) | 403/redirect if not admin |
| POST | `/admin/prices/update` | (MODIFIED guard) | 403 JSON if not admin |
| POST | `/admin/discounts/update` | (MODIFIED guard) | 403/redirect if not admin |

**Auth requirements:** `/admin/*` (except `/admin/login`) require `require_admin(req)` →
`session.get('admin')` OR valid admin key. `/register-vehicle` is public (like `/register`).

### Helper: require_admin

```
require_admin(req) -> bool
  returns session.get('admin') is True  OR  _check_admin_key(req)
```

## Module Boundaries

| Module | Responsibility | Allowed Dependencies |
|--------|----------------|----------------------|
| `main.py` | All routes, guards, redirects | flask, data_paths, price_store, discount_store, persistence, pandas |
| `data_paths.py` | Path registry (unchanged) | stdlib |
| `templates/` | HTML rendering (2 new files) | Jinja2 context from main.py |

## Change Footprint

### New files / modules

| Path | Purpose | Pattern reference |
|------|---------|-------------------|
| `templates/admin_login.html` | Admin password login form | mirror simple form in `register.html` |
| `templates/register_vehicle.html` | Standalone vehicle/driver registration form | mirror `#new_driver_fields` block in `book.html:287-314` + `register.html` layout |
| `docs/runbook-staging-and-dns.md` | Railway staging + apex DNS steps | mirror `docs/runbook.md` |

### Modified files / modules

| Path | What changes here |
|------|-------------------|
| `main.py:200-201` | `home()` returns `redirect("/book")` instead of `/form` (R2) |
| `main.py:102` | Replace hardcoded `app.secret_key` with `os.environ.get("SECRET_KEY", <random>)` (R4) |
| `main.py:105` | `ADMIN_KEY` — remove weak default (require env or keep only for back-compat); add `ADMIN_PASSWORD` env read (R4) |
| `main.py:192-194` | Add `require_admin(req)` helper next to `_check_admin_key` (R4) |
| `main.py` (new routes) | Add `/admin/login` (GET/POST), `/admin/logout` (GET), `/register-vehicle` (GET/POST) (R4, R5) |
| `main.py:936, 945, 988` | Swap `_check_admin_key(request)` → `require_admin(request)` in the 3 admin routes (R4) |
| `templates/book.html:296-304` | Fuel Type input → `type="hidden"`, remove its `<label>` (R6) |
| `templates/book.html` (booking form area) | Add "Register Vehicle" link/button → `/register-vehicle` (R5) |
| `AGENTS.md` / `.env.example` | Document new `SECRET_KEY`, `ADMIN_PASSWORD` env vars |

### Deleted / replaced

| Path | Reason |
|------|--------|
| `main.py:102` hardcoded secret | Replaced by env-driven `SECRET_KEY` |

### Touched but not changed (silent-regression hotspots)

| Path | Why it matters |
|------|----------------|
| `templates/admin_prices.html:101,151,206` | Reads `?key=` for form/fetch. Keeps working only because (a) `require_admin` retains key fallback AND (b) session cookie auto-sends on same-origin fetch. Verify both admin update calls still succeed after login-without-key. |
| `main.py:607` | Booking POST reads `fuel_type`; hidden input must still submit `Diesel` |
| `main.py:752-761` | Preset write logic that `/register-vehicle` reuses; ensure shared behavior stays consistent (dedup by plate) |
| Any bookmarked `/admin/prices?key=...` URLs | Must still authenticate via key fallback |
| 107 existing tests (`tests/`) | Tests hitting `/` now get 302→`/book` not `/form`; admin-route tests may need session/key setup |

## Areas of Impact

| Area | Impact | Risk (L/M/H) | Why |
|------|--------|--------------|-----|
| Admin auth flow | New login gate; guard logic changes on 3 routes | M | Auth regressions lock out admins or leave a hole if fallback misconfigured |
| Booking page UI | New link + hidden field | L | Additive; POST contract unchanged |
| Preset storage | New writer via `/register-vehicle` | L | Reuses proven pattern; no schema change |
| Root routing | Landing page changes | L | Single redirect; but changes first impression for all traffic |
| Existing tests | `/` and admin tests | M | May fail until updated for new redirect + auth |
| Railway infra | New staging service, env vars | M | Console/DNS work; misconfigured `DATABASE_URL` could point staging at prod DB |
| DNS apex | Registrar record | L | Standard A/ALIAS; propagation delay only |

**Contract changes:** No external API contract changes. `/` response changes from
302→`/form` to 302→`/book` (affects only implicit expectations, e.g. tests, monitors).

**Cross-cutting ripples:**
- **Secrets:** two new env vars (`SECRET_KEY`, `ADMIN_PASSWORD`) must be set in prod
  AND the new staging env, else sessions break / login unusable.
- **Sessions:** first real use of Flask `session` — requires a stable `SECRET_KEY`
  across gunicorn restarts (single worker today, so in-memory not an issue, but a
  changing secret invalidates all sessions on redeploy).
- **Deploy:** staging service auto-applies schema on start (Dockerfile CMD) — same as prod.

## Cross-Cutting Concerns

- **Errors:** login failure → re-render form with flash (no info leak on whether user/pass wrong). Missing register-vehicle fields → flash + re-render. Preset write failure → flash, do not 500.
- **Logging & metrics:** reuse existing `print`/`append_audit` style. Consider auditing admin login success/failure via `append_audit` (optional, note in Open Questions).
- **Auth / authz:** single admin role. Check at route entry via `require_admin`. `/register-vehicle` intentionally public (matches `/register`).
- **Performance:** negligible — one extra redirect, one CSV append. No new queries.
- **Security:** `ADMIN_PASSWORD` compared with constant-time compare (`hmac.compare_digest`) recommended. `SECRET_KEY` must be strong + secret. Remove default admin creds. Session cookie: rely on Flask defaults; set `SESSION_COOKIE_HTTPONLY` (default on); consider `SESSION_COOKIE_SECURE=True` behind HTTPS.
- **Migrations / rollout:** no DB migration. Rollout order: set env vars FIRST (`SECRET_KEY`, `ADMIN_PASSWORD`) THEN deploy code, or admins get locked out. Backward-compat: `?key=` fallback means no flag-day.

## Architecture Decisions Log

| # | Decision | Alternatives | Chosen Because | Satisfies REQs |
|---|----------|--------------|----------------|----------------|
| A1 | `/` redirects to `/book` | keep `/form`; landing splash page | Booking is the user entry point | R2 |
| A2 | Session login + key fallback guard | session-only; Basic Auth; key-only | Real human auth with zero regression to existing `?key=` JS/bookmarks | R4 |
| A3 | `SECRET_KEY`/`ADMIN_PASSWORD` env, drop weak defaults | reuse hardcoded/ADMIN_KEY | Sessions need stable secret; eliminate default credentials | R4 |
| A4 | New `/register-vehicle` reusing preset CSV write | link to `/register`; on-page anchor; new DB table | Matches "register a vehicle before booking" without a booking, reuses proven storage | R5 |
| A5 | Fuel Type → hidden input (value `Diesel`) | remove field; disable | Hides UI while preserving POST contract and downstream Diesel assumptions | R6 |
| A6 | Infra (staging, apex DNS) as runbook | full IaC in repo | Console/registrar actions; low value to encode now | R1, R3 |

## Risk & Stress-Test Scenarios

### Forward — runtime failure scenarios

| Scenario | How the Design Handles It |
|----------|---------------------------|
| `SECRET_KEY` unset in prod | Random per-process fallback → sessions still work per-process but invalidate on restart. GAP for multi-worker; single worker today. See Open Questions. |
| `ADMIN_PASSWORD` unset | Login POST can never succeed → key fallback still allows `?key=` access. Admin not fully locked out; document as required env. |
| Admin logs in, then uses price/discount update | Session cookie auto-sent on same-origin fetch → `require_admin` session branch passes even with empty `?key=`. Verified by design. |
| Legacy user hits `/admin/prices?key=...` after change | Key fallback in `require_admin` authenticates → still works. |
| `/register-vehicle` POST with duplicate plate | Reused dedup-by-plate logic skips append → idempotent, no dupes. |
| Staging `DATABASE_URL` accidentally points at prod | Operational risk — runbook must call out distinct DB. Mitigation in runbook checklist. |

### Backward — regression risk per touched area

| Touched area | What could regress | How we'd know / mitigation |
|--------------|--------------------|-----------------------------|
| `admin_prices.html` (`?key=` JS) | Price/discount updates 403 after session-only login | require_admin keeps key fallback + cookie auto-send; manual test: log in, update a price with no `?key=` in URL |
| `main.py:607` fuel_type read | Booking submits blank/no fuel_type | Keep `name="fuel_type"` on hidden input with `value="Diesel"`; test a new-driver booking |
| `/` redirect | Tests/monitors expecting `/form` | Update tests to expect 302→`/book`; check UptimeRobot target still `/healthz` not `/` |
| Preset write (`main.py:752-761`) | Divergent behavior between booking-time and `/register-vehicle` writes | Extract or mirror exact logic; assert same columns/dedup |
| 3 admin routes guard swap | One route left on old guard → inconsistent access | Grep all `_check_admin_key(request)` call sites; swap all three |

## Open Questions

- Drop `?key=` entirely once login is adopted?
  - **Impact if unresolved:** dual auth path lingers; slightly larger attack surface.
  - **Suggested default:** keep fallback now, remove in a later cleanup once bookmarks migrated.
- Should `/register-vehicle` validate `account_code` against `customers.csv` before saving?
  - **Impact if unresolved:** presets could be created for non-existent accounts (orphan CSVs).
  - **Suggested default:** validate account_code exists; flash error if not.
- Audit admin login attempts via `append_audit`?
  - **Impact if unresolved:** no trail of admin logins.
  - **Suggested default:** log success + failure; low effort.
- Multi-worker `SECRET_KEY`: enforce required env (fail fast) vs random fallback?
  - **Impact if unresolved:** if workers scale >1, random fallback breaks sessions across workers.
  - **Suggested default:** require `SECRET_KEY` in prod (fail fast), random only when `FLASK_DEBUG`.

## Out of Scope

- Gasoline vs Diesel selection on the Booking page (brief item 10 notes this is future work).
- Multi-role / per-user admin accounts (single shared admin password only).
- Full IaC for Railway (staging created via console per runbook).
- Editing/deleting existing presets/vehicles.
- CSRF protection on admin/login forms (not currently present anywhere; separate hardening task).

---

# Tasks

> **Generated:** 2026-07-02 (Phase 3 — generate-tasks)
> **Test harness:** pytest + Flask `test_client()`. New route tests import `main`
> with the default CSV backend (`PERSISTENCE_BACKEND=csv`) so they run under
> `make test` (no Postgres). Data-writing tests monkeypatch `UNIFLEET_DATA_DIR`
> to `tmp_path` (pattern from `tests/test_data_paths.py`).

## Task T1: Redirect site root to Booking page

> **Status:** done
> **Effort:** xs
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** R2
> **Footprint slice:** Modified: `main.py:200-201` (`home()` redirect target)
> **High-risk areas touched:** Root routing (L)

### Description

The site root `/` currently 302-redirects to `/form` (the admin dashboard). Change
it to redirect to `/book` so users land on the Booking page. Single-line change to
the `home()` view.

### Test Plan

#### Test File(s)
- `tests/test_root_redirect.py`

#### Test Scenarios

##### Root Redirect

- **redirects root to booking** — GIVEN the app WHEN GET `/` THEN status is 302 and `Location` ends with `/book` _(verifies R2)_

##### Regression Guard

- **booking page still reachable** — GIVEN the app WHEN GET `/book` THEN status is 200 _(guards ARCH backward-regression: `/` redirect change must not affect the book route)_

### Implementation Notes

- **Module(s):** `main.py` (Home / Dashboard section).
- **Pattern reference:** existing `redirect("/form")` at `main.py:201` — mirror idiom with `redirect("/book")`.
- **Key decisions:** A1 (`/` → `/book`).
- **Libraries:** flask.

### Scope Boundaries

- Do NOT change `/form` route itself (dashboard stays reachable directly).
- Do NOT add a landing/splash page (out of scope).
- Only change the redirect target in `home()`.

### Files Expected

**New files:**
- `tests/test_root_redirect.py`

**Modified files:**
- `main.py:200-201` (`home()` returns `redirect("/book")` instead of `/form`)

**Must NOT modify:**
- `main.py` `/form` route (still serves the dashboard directly)

---

## Task T2: Hide Fuel Type field on Add-New-Driver form

> **Status:** done
> **Effort:** xs
> **Priority:** medium
> **Depends on:** None
> **Satisfies REQs:** R6
> **Footprint slice:** Modified: `templates/book.html:296-304` (Fuel Type input + label)
> **High-risk areas touched:** Booking page UI (L)

### Description

The Add-New-Driver section of the booking form shows a readonly "Diesel" Fuel Type
input. Hide it from the UI while still submitting `fuel_type=Diesel`, so the booking
POST handler (`main.py:607`) and all downstream Diesel assumptions are unaffected.
Gasoline/Diesel selection on the Booking page is future work (out of scope).

### Test Plan

#### Test File(s)
- `tests/test_book_fuel_field.py`

#### Test Scenarios

##### Fuel Field Visibility

- **fuel type field is hidden** — GIVEN the app WHEN GET `/book` THEN the rendered body contains an input `name="fuel_type"` with `type="hidden"` and no visible `<label for="fuel_type">` _(verifies R6)_

##### Regression Guard (POST contract)

- **fuel type still submits Diesel** — GIVEN the rendered `/book` page WHEN inspecting the fuel_type input THEN its `value` is `Diesel` (so `main.py:607` still receives `Diesel`) _(guards ARCH backward-regression for `main.py:607`)_

### Implementation Notes

- **Module(s):** `templates/book.html`.
- **Pattern reference:** the input block at `book.html:296-304`.
- **Key decisions:** A5 (hidden input, value `Diesel`).
- **High-risk callouts:** the field name must stay `fuel_type` and value `Diesel`; removing the field entirely would break `main.py:607` (`request.form.get('fuel_type')`). Regression test covers this.

### Scope Boundaries

- Do NOT add Gasoline/Diesel selection anywhere (future work, out of scope).
- Do NOT touch the preset dropdown option that also carries `fuel_type` (`book.html:279`).
- Do NOT change `main.py` booking POST handling.
- Only convert the visible input+label to a hidden input.

### Files Expected

**New files:**
- `tests/test_book_fuel_field.py`

**Modified files:**
- `templates/book.html:296-304` (Fuel Type input → `type="hidden"`, remove its `<label>`)

**Must NOT modify:**
- `main.py:607` (silent-regression hotspot — covered by regression-guard test)
- `templates/book.html:279` (preset option `fuel_type`, out of scope)

---

## Task T3: Admin dashboard session authentication

> **Status:** done
> **Effort:** m
> **Priority:** critical
> **Depends on:** None
> **Satisfies REQs:** R4
> **Footprint slice:** New: `templates/admin_login.html`; Modified: `main.py:102` (secret_key), `main.py:105` (ADMIN_KEY default + ADMIN_PASSWORD), `main.py:192-194` (add `require_admin`), new `/admin/login` + `/admin/logout` routes, `main.py:936,945,988` (guard swap)
> **High-risk areas touched:** Admin auth flow (M), Existing tests (M)

### Description

Add real password authentication to the admin dashboard via a Flask session. A new
`/admin/login` page verifies `ADMIN_PASSWORD`, sets `session['admin']=True`, and
`/admin/logout` clears it. A new `require_admin` guard (session OR existing admin
key) replaces `_check_admin_key` on the three admin routes so logged-in users and
legacy `?key=` bookmarks both work. Replace the hardcoded `app.secret_key` with a
`SECRET_KEY` env var (random fallback in dev) and remove weak credential defaults.

### Test Plan

#### Test File(s)
- `tests/test_admin_auth.py`

#### Test Scenarios

##### Admin Login

- **login page renders** — GIVEN the app WHEN GET `/admin/login` THEN status is 200 and body has a password field _(verifies R4)_
- **correct password grants session** — GIVEN `ADMIN_PASSWORD` set WHEN POST `/admin/login` with the correct password THEN 302 → `/admin/prices` AND a follow-up GET `/admin/prices` returns 200 _(verifies R4)_
- **wrong password rejected** — GIVEN `ADMIN_PASSWORD` set WHEN POST `/admin/login` with a wrong password THEN no admin session is set, an error is flashed, and GET `/admin/prices` still redirects to `/admin/login` _(verifies R4)_

##### Access Control

- **unauthenticated admin redirected to login** — GIVEN no session and no key WHEN GET `/admin/prices` THEN 302 → `/admin/login` _(verifies R4)_
- **logout clears session** — GIVEN a logged-in admin session WHEN GET `/admin/logout` THEN subsequent GET `/admin/prices` redirects to `/admin/login` _(verifies R4)_

##### Regression Guard (legacy key)

- **legacy key fallback still works** — GIVEN `ADMIN_KEY` set WHEN GET `/admin/prices?key=<ADMIN_KEY>` THEN status is 200 without any session _(guards ARCH backward-regression for `admin_prices.html` `?key=` flow)_

### Implementation Notes

- **Module(s):** `main.py` (config block + Admin section), `templates/`.
- **Pattern reference:** `_check_admin_key` at `main.py:192-194`; simple form layout from `templates/register.html`; flash usage already present in `admin_discounts_update`.
- **Key decisions:** A2 (session + key fallback), A3 (`SECRET_KEY`/`ADMIN_PASSWORD` env, drop weak defaults).
- **Libraries:** flask (`session`); `hmac.compare_digest` for constant-time password compare (per ARCH Security).
- **High-risk callouts:**
  - *Admin auth flow (M):* `require_admin` = `session.get('admin')` OR `_check_admin_key(req)`. Must be applied to ALL three routes (`main.py:936, 945, 988`) — grep every `_check_admin_key(request)` call site. `/admin/prices/update` returns JSON 403 (keep shape); `/admin/prices` and `/admin/discounts/update` redirect to login. Regression test covers key fallback; access-control tests cover session path.
  - *Rollout:* `SECRET_KEY` + `ADMIN_PASSWORD` must be set before deploy or admins lock out (ARCH cross-cutting). Tests set them via monkeypatch/env.
  - *Existing tests (M):* any test asserting old `/` or admin behavior may need updating — none currently import `main`, so low collision risk.
- **Security:** do not leak which of user/pass was wrong; set session cookie via Flask defaults (HTTPONLY on).

### Scope Boundaries

- Do NOT add multi-user or per-user admin accounts (single shared password — out of scope).
- Do NOT add CSRF protection (separate hardening task, out of scope).
- Do NOT remove the `?key=` fallback in this task (deferred cleanup — Open Question A).
- Only add login/logout + guard swap + secret/env config.

### Files Expected

**New files:**
- `templates/admin_login.html` (password form; mirror `register.html` layout)
- `tests/test_admin_auth.py`

**Modified files:**
- `main.py:102` (secret_key from `SECRET_KEY` env, random dev fallback)
- `main.py:105` (drop weak `ADMIN_KEY` default; add `ADMIN_PASSWORD` env read)
- `main.py:192-194` (add `require_admin(req)` helper)
- `main.py` (new `/admin/login` GET/POST, `/admin/logout` GET routes)
- `main.py:936, 945, 988` (swap `_check_admin_key(request)` → `require_admin(request)`)
- `AGENTS.md` / `.env.example` (document `SECRET_KEY`, `ADMIN_PASSWORD`)

**Must NOT modify:**
- `templates/admin_prices.html` (silent-regression hotspot — `?key=` JS keeps working via key fallback + session-cookie auto-send; covered by regression-guard test)
- `main.py` API auth for `/supplier-api/*` (out of scope)

### TDD Sequence (optional)

1. Add `SECRET_KEY` config + `require_admin` helper (no route uses it yet).
2. Add `/admin/login` + `/admin/logout`.
3. Swap guards on the three admin routes.
4. Run access-control + regression tests last (they depend on all pieces).

---

## Task T4: Register Vehicle page

> **Status:** done
> **Effort:** m
> **Priority:** high
> **Depends on:** `ARCH-customers-postgres.md` (delivers `repo.customer_exists`) — DONE (CT1)
> **Satisfies REQs:** R5
> **Footprint slice:** New: `templates/register_vehicle.html`, `/register-vehicle` routes; Modified: `book.html` (add link)
> **High-risk areas touched:** Preset storage (L), Booking page UI (L), Customer source of truth (H)

### Description

Add a standalone "Register Vehicle" page (`GET/POST /register-vehicle`) reachable
from the Booking page. It takes an account code plus driver/vehicle details and
saves a preset to `data/presets/<code>_presets.csv`, reusing the exact dedup-by-plate
+ append pattern already used at booking time (`main.py:752-761`). The account code
is validated against the **Postgres `customers` table** via a new repo read method
(`repo.customer_exists(account_code)`) before saving.

> **⚠ BLOCKED — cross-feature dependency.** Validating against Postgres requires the
> customer flow to actually live in Postgres. Today `/register` writes `customers.csv`
> and `/book` reads `customers.csv`; the Postgres `customers` table is stale (seeded
> only by the one-time `scripts/migrate_to_postgres.py`), and no repo method reads
> customers. Per developer decision (2026-07-02), the **entire customer flow migrates
> to Postgres** — this is its own architecture item:
>
> - New repo methods: `create_customer()`, `customer_exists()`, `get_customer()`
> - Reroute `/register` → write Postgres; `/book` → read Postgres
> - Backfill `customers.csv` → Postgres; decide fate of `customers.csv`
> - Reckon with `PERSISTENCE_BACKEND` default `csv` (`main.py:115`) — confirm prod backend
>
> Run `/plan-architecture for: migrate customer flow (register + book + validation) to
> Postgres` to produce `ARCH-customers-postgres.md`, generate its tasks, implement it,
> THEN unblock T4. Once `repo.customer_exists()` exists, T4 is a thin consumer of it.

### Test Plan

#### Test File(s)
- `tests/test_register_vehicle.py` (monkeypatch `UNIFLEET_DATA_DIR` → `tmp_path`; mock/stub `repo.customer_exists` so tests stay backend-agnostic and run under `make test`)

#### Test Scenarios

##### Register Vehicle Form

- **form renders** — GIVEN the app WHEN GET `/register-vehicle` THEN status is 200 and body has account_code + driver_name + vehicle_plate + truck_make + truck_model + number_of_wheels fields _(verifies R5)_

##### Save Preset

- **saves preset for valid account** — GIVEN `repo.customer_exists(code)` returns True WHEN POST `/register-vehicle` with full driver/vehicle data THEN a row is written to `<code>_presets.csv` containing all 6 preset fields (driver_name, vehicle_plate, truck_make, truck_model, number_of_wheels, fuel_type) _(verifies R5)_

##### Validation & Edge Cases

- **rejects unknown account_code** — GIVEN `repo.customer_exists(code)` returns False WHEN POST `/register-vehicle` THEN an error is flashed and NO preset file/row is written _(REQ edge — validate account_code against Postgres per developer decision)_
- **rejects missing required fields** — GIVEN a valid account_code but blank driver_name/plate WHEN POST THEN an error is flashed and no bad row is written _(REQ edge)_

##### Regression Guard (dedup)

- **dedup by plate** — GIVEN a preset with plate `ABC123` already exists WHEN POST `/register-vehicle` with the same plate THEN the CSV still has exactly one row for that plate _(guards ARCH backward-regression for `main.py:752-761` shared preset-write behavior)_

### Implementation Notes

- **Module(s):** `main.py` (new routes near `/register`), `templates/`.
- **Pattern reference:** preset write dedup+append at `main.py:752-761`; form layout from `#new_driver_fields` (`book.html:287-314`) + `register.html`.
- **Key decisions:** A4 (new `/register-vehicle` reusing preset CSV). Account validation uses Postgres via `repo.customer_exists()` (developer decision 2026-07-02, supersedes ARCH Open Question B).
- **Libraries:** flask, pandas, `data_paths.preset_csv_path`, `persistence.get_repo`.
- **High-risk callouts:**
  - *Customer source of truth (H):* validation depends on `repo.customer_exists()` existing and the Postgres `customers` table being the live source — delivered by the separate customer-flow-to-Postgres feature. T4 stays **blocked** until that lands. Do not fall back to `customers.csv` (would reintroduce the split-brain this decision removes).
  - *Preset storage (L):* reuse the exact dedup key (`vehicle_plate` upper/stripped) and column set from `main.py:752-761` so booking-time and register-time writes stay consistent. Consider extracting a shared helper; regression test enforces dedup.
- **fuel_type:** default to `Diesel` on save (consistent with R6 / `main.py:607`).

### Scope Boundaries

- Do NOT add edit/delete of existing presets/vehicles (out of scope).
- Do NOT gate `/register-vehicle` behind admin auth (public, like `/register`).
- Do NOT add Gasoline selection (future work).
- Only implement the form, account validation, and preset write.

### Files Expected

**New files:**
- `templates/register_vehicle.html` (mirror `#new_driver_fields` + `register.html`)
- `main.py` `/register-vehicle` GET/POST routes
- `tests/test_register_vehicle.py`

**Modified files:**
- `templates/book.html` (add "Register Vehicle" link/button → `/register-vehicle`)

**Depends on (delivered by customer-flow-to-Postgres feature, not this task):**
- `repo.customer_exists(account_code)` — new repo read method against Postgres `customers`

**Must NOT modify:**
- `main.py:752-761` (silent-regression hotspot — shared preset-write behavior; covered by dedup regression test. If extracted to a helper, booking flow must use the same helper.)

---

## Task T5: Staging environment & apex domain runbook

> **Status:** done
> **Effort:** xs
> **Priority:** medium
> **Depends on:** None
> **Satisfies REQs:** R1, R3
> **Footprint slice:** New: `docs/runbook-staging-and-dns.md`
> **High-risk areas touched:** Railway infra (M), DNS apex (L)

### Description

Infrastructure/DNS work with no repo code. Capture a step-by-step runbook for (1)
creating a Railway staging service off the `dev` branch with its own `DATABASE_URL`
and mirrored env vars, and (3) adding a DNS record so the apex `unifleet.asia`
resolves to the site (in addition to `www`). Doc only — no tests.

### Test Plan

Doc-only task — no automated tests. Verification is manual per the runbook's own
checklist (staging URL responds on `/healthz`; `unifleet.asia` resolves).

### Implementation Notes

- **Module(s):** none (documentation).
- **Pattern reference:** `docs/runbook.md`.
- **Key decisions:** A6 (infra as runbook).
- **High-risk callouts:**
  - *Railway infra (M):* staging must use a **distinct** `DATABASE_URL` — the runbook checklist must explicitly warn against pointing staging at the prod DB. Include the full env-var set: `DATABASE_URL`, `SECRET_KEY`, `ADMIN_PASSWORD`, `ADMIN_KEY`, `SUPPLIER_API_TOKEN`, `PORT`.
  - Schema auto-applies on start (Dockerfile CMD) — same as prod; note this.
- **Content to include:** Railway service creation off `dev`, env-var matrix (prod vs staging), branch→env mapping, apex DNS record (A/ALIAS) alongside existing `www`, and a manual verification checklist.

### Scope Boundaries

- Do NOT encode infra as IaC / `rw.txt` env matrix (out of scope — console-driven).
- Do NOT change production DNS for `www` (already works).
- Only produce the runbook document.

### Files Expected

**New files:**
- `docs/runbook-staging-and-dns.md`

**Must NOT modify:**
- `rw.txt`, `Dockerfile` (no code changes for infra this round)
