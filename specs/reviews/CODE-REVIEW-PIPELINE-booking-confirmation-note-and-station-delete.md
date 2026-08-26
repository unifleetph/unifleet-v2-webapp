# Review Report

## Metadata

| Field | Value |
|-------|-------|
| **Review Mode** | Pipeline: ARCH-booking-confirmation-note-and-station-delete |
| **Target** | specs/architecture/ARCH-booking-confirmation-note-and-station-delete.md |
| **Date** | 2026-08-26 |
| **Tech Stack** | Python 3.11, Flask (single-file app), psycopg3 + psycopg_pool (Postgres), Jinja2, pytest |
| **Checks Run** | task-completion, code-quality, security, database-patterns, error-handling |
| **Checks Skipped** | test-coverage (all red/green observed live during tdd/test-after implementation), performance (trivial CRUD scale, indexed FK-scoped deletes), documentation (no public API surface), accessibility (no new interactive pattern beyond existing button styling), react/express/typescript/async-patterns/runtime-behavior/migration/config-dependencies (not this stack or no relevant changes) |
| **Files Changed** | 7 code/test files (+ 2 spec docs) |
| **Lines Changed** | +758 / -3 |

## Review Process

- [x] Preflight checks passed
- [x] Diff gathered (commits `f17e8df`..`85d167e`, 7 code/test files, 758 lines)
- [x] Tech stack detected: Python/Flask/psycopg3/Postgres/Jinja2/pytest
- [x] Context read (CLAUDE.md, AGENTS.md, linked REQ, ARCH with embedded task specs)
- [x] Triage proposed and developer confirmed
- [x] 5 checks dispatched: task-completion, code-quality, security, database-patterns, error-handling
- [x] Results collected and deduplicated
- [x] Report compiled
- [x] Verdict determined
- [x] Report saved to specs/reviews/

## Verdict: ✅ PASS (post-fix)

Copy change (T1) and the delete transaction's atomicity (T2 backend) were solid from the start — cascade delete is correctly scoped, parameterized, and proven atomic under a forced FK failure by a real test against live Postgres. The original submission had a real ❌ FAIL: the booking-history guard behind R6 had two independent bypasses (confirmed by three separate checks), and R3's "deactivated-only" rule was enforced only in the template. All three findings (rename-bypass, missing server-side is_active check, KeyError-vs-generic-except) have been fixed in a follow-up commit, each covered by a new test, with the full suite green (408/408). See `specs/architecture/ARCH-booking-confirmation-note-and-station-delete.md`'s Post-Review Addendum for details.

### Re-review

| # | Original Finding | Status | Notes |
|---|-----------------|--------|-------|
| 1 | Security — booking-history guard bypassed by station rename (🟠 High) | ✅ Resolved | `admin_stations_edit` now blocks renaming a station that has booking history under its current name, via shared `_station_has_bookings()` helper. Residual: stations renamed *before* this fix are not retroactively protected (documented, accepted). |
| 2 | Database Patterns — R3 "deactivated-only" enforced only in template (🟡 Medium) | ✅ Resolved | `admin_stations_delete` now checks `existing["is_active"]` server-side and blocks with a flash before any deletion logic runs. |
| 3 | Task Completion / Error Handling — `KeyError` not caught explicitly (🟡 Medium) | ✅ Resolved | `admin_stations_delete` now has `except KeyError: abort(404)` before the generic `except Exception`, matching `deactivate`/`reactivate`. |

**Verification:** `tests/test_admin_stations.py` gained `test_post_admin_stations_delete_blocked_when_station_active`, `test_post_admin_stations_delete_unknown_id_race_returns_404`, `test_post_admin_stations_edit_rename_blocked_when_station_has_bookings`, and `test_post_admin_stations_edit_rename_allowed_when_no_bookings`. `make test-db` — 408/408 passed.

**Scope note:** the rename-guard fix touches `admin_stations_edit`, which was outside T1-T3's original Change Footprint — necessary to close the root cause rather than just the symptom in `admin_stations_delete`. Documented in the ARCH doc's Post-Review Addendum.

### Finding Counts

| Category | 🔴 | 🟠 | 🟡 | 💭 | ⚠️ |
|----------|-----|-----|-----|-----|-----|
| task-completion | 0 | 0 | 1 | 0 | 1 |
| code-quality | 0 | 0 | 0 | 0 | 0 |
| security | 0 | 1 | 0 | 1 | 0 |
| database-patterns | 0 | 0 | 1 | 0 | 0 |
| error-handling | 0 | 0 | 1 | 1 | 0 |
| **Total (deduplicated)** | **0** | **1** | **2** | **1** | **1** |

## Task Completion

**REQs:** 6/6 implemented; R6 has a design-level gap (see Finding #1 below), the rest verified clean.

| REQ | Status | Evidence |
|-----|--------|----------|
| R1 | ✅ Verified | `templates/booking_success.html` info-box with exact text; `test_book_and_booking_success_copy.py` asserts all three sentences present, old copy absent |
| R2 | ✅ Verified | `.bdo-note` "FREE BDO Transfer using BDO App" placed between QR image and kept `qr-caption`; document-order assertion passes |
| R3 | ⚠️ Partially verified | Template gates the button correctly (`test_get_admin_stations_delete_button_only_for_inactive_stations` passes), but the **route itself never checks `is_active`** — see Finding #2 |
| R4 | ✅ Verified | `confirm()` prompt wired on the form; checklist item, not unit-testable — see Manual Checks |
| R5 | ✅ Verified | `price_store.delete_station` cascades correctly; 5 scenarios pass live against Postgres |
| R6 | ⚠️ Verified against tests, but the guard is bypassable in real use | Booking-name match blocks delete in the tested scenario, but see Finding #1 |

**Verification method:** `make test-db` run against the working tree at `85d167e` — 404/404 passed, live Postgres.

**Change Footprint adherence:** ✅ Clean — every Footprint row (New/Modified/Touched-not-changed/Must-NOT-modify) matches the diff exactly; no unlisted files touched (`git diff --stat` confirms only the 7 expected files + 2 new spec docs).

**Architecture Decisions:** A1–A5 followed in code as documented. A1's chosen approach (name-match via `repo.list_all_vouchers()`) is exactly where Finding #1 originates — the decision itself was reasonable given the constraint that `station_id` isn't populated by the booking flow, but its interaction with the pre-existing station-rename feature wasn't accounted for.

### Findings

| # | Severity | File | Line | Issue | Recommendation |
|---|----------|------|------|-------|----------------|
| 1 | 🟡 Medium | `main.py` | ~1560 | `delete_station`'s documented `KeyError` contract isn't caught explicitly in `admin_stations_delete`, unlike the sibling `deactivate`/`reactivate` routes (`except KeyError: abort(404)`). A `KeyError` from a race (station deleted between the route's existence check and the `delete_station()` call) falls into the generic `except Exception` and surfaces as a flash instead of a 404 — diverging from the ARCH doc's own stated behavior ("matching `deactivate`/`reactivate`"). | Add `except KeyError: abort(404)` before the generic `except Exception` in `admin_stations_delete`. |

### Manual Checks Required

- [ ] T3's 6-item human-verified checklist (button styling parity, confirm-prompt gating, cancel-leaves-page-unchanged, success delete, blocked delete, active-stations-no-button) is still unchecked `[ ]` in the ARCH doc. The implementer verified these functionally via `curl` against the real running app (documented in the conversation) but the checklist itself was never formally checked off or given attached evidence in the doc — the `confirm()` dialog specifically was explicitly flagged as unverified in a real browser this session.

---

## Security

**Files reviewed:** `main.py` (`admin_stations_delete`), `price_store.py` (`delete_station`), `templates/admin_stations.html`

### Findings

| # | Severity | File | Line | Issue | Recommendation |
|---|----------|------|------|-------|----------------|
| 1 | 🟠 High | `main.py` | ~1556-1558 | The booking-history guard compares `voucher.station` (a **name snapshot** captured at booking time) against the station's **current** `name`. `admin_stations_edit` lets an admin rename a station's `name` at any time with no propagation to historical vouchers. Once renamed, old vouchers no longer match, `has_bookings` evaluates `False`, and `delete_station()` proceeds — permanently destroying a station that has real booking history, exactly the scenario R6 exists to prevent. No malicious intent required: rename then delete are both independently ordinary admin actions. The match is also case-sensitive exact-equality, while the existing booking lookup elsewhere in `main.py` (line ~539) is case-insensitive — a casing drift alone also defeats the guard. | Match on an immutable key (e.g. `station_id` if ever populated on vouchers), or block/warn on rename while the station has voucher history, or otherwise decouple the guard from the mutable `name` field. |

### Observations (not standalone findings)

- Auth (`require_admin`), SQL parameterization, and transaction atomicity (existence check + all 5 deletes in one connection/transaction, no TOCTOU window) are all correct.
- Raw exception text in the failure-path flash message (`f"Failed to delete station: {e}"`) is a pre-existing pattern already present in `admin_stations_edit`/`admin_stations` create — not a new exposure from this diff.
- No CSRF tokens anywhere in this app's admin POST forms — pre-existing, app-wide, not a new gap.

---

## Database Patterns

**Result:** No Critical/High/Medium findings on transaction/query correctness — atomicity, delete ordering, and parameterization all verified correct (including tracing into `psycopg_pool`/`psycopg` source to confirm implicit rollback-on-exception is real and matches the file's existing convention). One informational item is escalated to a standalone finding below because it's a direct REQ violation risk, not just a style note.

### Findings

| # | Severity | File | Line | Issue | Recommendation |
|---|----------|------|------|-------|----------------|
| 2 | 🟡 Medium | `main.py` | `admin_stations_delete` | The "deactivated stations only" rule (R3: "an active station can never be deleted directly") is enforced **only by the template** hiding the button. The route itself never checks `existing["is_active"]` before proceeding — a direct `POST /admin/stations/<id>/delete` against an active station succeeds as long as no matching voucher name exists. Same trust boundary as the rest of `/admin/stations` (admin-only), but it's a direct contradiction of an explicit REQ line, not just defense-in-depth. | Add an explicit `if existing.get("is_active"): flash(...); return _admin_stations_back()` (or similar) guard in the route, mirroring how the button visibility rule is expressed server-side. |

### Verified correct (no issue)

- Transaction atomicity confirmed by tracing `psycopg_pool.ConnectionPool.connection()` → `psycopg.Connection.__exit__`: rollback-on-exception is real, and the new `test_delete_station_rolls_back_entirely_on_partial_failure` exercises it against a genuine FK violation.
- Delete ordering across `price_history`/`discount_history`/`prices`/`discounts` doesn't matter for FK correctness (none reference each other) as long as all four precede the `stations` delete — which they do.
- The "load all vouchers into memory" approach for the booking check is a deliberate, documented tradeoff (ARCH line 89, ties to A1) to keep the check behind the backend-agnostic `persistence.py` abstraction — reasonable at this app's scale, not a defect.

---

## Error Handling & Observability

**Files reviewed:** `main.py` (`admin_stations_delete`), `price_store.py` (`delete_station`)

Findings deduplicated into Task Completion's Finding #1 above (same root cause: `KeyError` not caught explicitly, unlike sibling routes).

### Observations (not standalone findings)

- `f"Failed to delete station: {e}"` interpolates raw exception text into an admin-facing flash — traced and confirmed this exact pattern already exists at 3 other call sites in `main.py` predating this diff. Not a new exposure; flagging only as a pre-existing, repo-wide item if the team ever wants to tighten it.
- No explicit `try/except/rollback` in `delete_station` is intentional and correct — relies on the connection pool's context-manager rollback, same idiom as every other mutator in `price_store.py`.

---

## Code Quality & Conventions

**Result:** ✅ No findings. New code closely follows established idioms: `delete_station` mirrors `set_station_active`'s transaction/`KeyError` shape and reuses the existing `_station_exists` helper; the route mirrors the sibling `deactivate`/`reactivate`/`edit` routes' guard → lookup → try/except → flash → redirect shape; templates reuse existing `.btn-danger` and `?key=` query-param patterns; new tests follow the existing `FakePriceStore`/`captured_templates` fixtures. `main.py`'s growing length (now ~1930 lines) isn't flagged — `AGENTS.md`/`CLAUDE.md` explicitly mandate the single-file-app convention.

## Prioritized Action Items

### Must Fix (🔴 Critical / 🟠 High)
- **[Security #1]** Booking-history guard is defeated by a routine station rename — fix before merge, since it silently reintroduces the exact data-loss scenario R6 was written to prevent.

### Should Address (🟡 Medium)
- **[Database Patterns #2]** Add a server-side `is_active` check in `admin_stations_delete` — currently R3's "active stations can never be deleted directly" is enforced only by hiding the button in the template.
- **[Task Completion / Error Handling #1]** Catch `KeyError` explicitly and `abort(404)` in `admin_stations_delete`, matching the sibling `deactivate`/`reactivate` routes and the ARCH doc's documented behavior.

### Nice to Have (💭 Low)
- Consider tightening the pre-existing raw-exception-in-flash pattern (`f"Failed to delete station: {e}"` and its 3 siblings) repo-wide — not introduced by this change, not blocking.

## Manual Checks Required

- [ ] Formally check off T3's 6-item human-verified checklist in the ARCH doc (or attach evidence) — the `confirm()` dialog specifically was never confirmed in a real browser this session (only via `curl`, which bypasses JS).

---
*Generated by Review — 2026-08-26*
