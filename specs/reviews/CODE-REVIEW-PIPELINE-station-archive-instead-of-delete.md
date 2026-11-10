# Review Report

## Metadata

| Field | Value |
|-------|-------|
| **Review Mode** | Pipeline: ARCH-station-archive-instead-of-delete |
| **Target** | specs/architecture/ARCH-station-archive-instead-of-delete.md |
| **Date** | 2026-11-10 |
| **Tech Stack** | Python 3.11 / Flask, Postgres via psycopg3 (raw SQL, no ORM), pytest, Jinja2 |
| **Checks Run** | task-completion, code-quality, database-patterns, error-handling, requirement-coverage |
| **Checks Skipped** | security (no new input surface beyond the existing `<id>` path pattern, same admin auth), test-coverage (tdd/ui modes, every red/green + real click-through already observed), performance (trivial single-row ops), accessibility/react-patterns/express-patterns/typescript-strictness/async-patterns/runtime-behavior (wrong stack/no applicable files), documentation (internal admin tooling, no public API), config-dependencies (no new deps/env vars), migration (internal-only route rename, no external contract) |
| **Files Changed** | 6 code files (`db/schema.sql`, `main.py`, `price_store.py`, `templates/admin_stations.html`, `tests/test_admin_stations.py`, `tests/test_price_store.py`) + 3 spec docs |
| **Lines Changed** | +867 / -45 |

## Review Process

- [x] Preflight checks passed
- [x] Diff gathered (9 files, 867+/-45 lines, commits `36104c1`+`c1564f8`)
- [x] Tech stack detected: Python/Flask/Postgres/pytest
- [x] Context read (ARCH, REQ, TASKS)
- [x] Triage proposed and developer confirmed
- [x] 5 checks dispatched: task-completion, code-quality, database-patterns, error-handling, requirement-coverage
- [x] Results collected and deduplicated
- [x] Report compiled
- [x] Verdict determined
- [x] Report saved to specs/reviews/

## Verdict: ❌ FAIL

The core design is sound and cleanly implemented — the archived-filter is correctly unconditional across all three read paths, module boundaries are respected, and the UI was verified against the real running app. But two independent checks (task-completion and requirement-coverage) converged on the same real bug: re-archiving an already-archived station contradicts a decision the REQ explicitly documents, and a separate check (database-patterns) found a second real correctness gap in the write paths. Both are fixable in isolation without touching the rest of the design.

### Finding Counts

| Category | 🔴 | 🟠 | 🟡 | 💭 | ⚠️ |
|----------|-----|-----|-----|-----|-----|
| task-completion | 0 | 1 | 0 | 0 | 0 |
| code-quality | 0 | 0 | 0 | 1 | 0 |
| database-patterns | 0 | 0 | 1 | 1 | 0 |
| error-handling | 0 | 0 | 1 | 1 | 2 |
| requirement-coverage | 0 | 0 | 3 | 0 | 0 |
| **Total** | **0** | **1** | **5** | **3** | **2** |

*(Finding #1 below merges task-completion's and requirement-coverage's independent reports of the same root cause.)*

---

## task-completion

**REQs:** 5/6 fully verified, 1 partially verified with a gap.

| REQ | Status | Evidence |
|---|---|---|
| R1 | ✅ | `test_archive_station_sets_archived_at`; `test_post_admin_stations_archive_succeeds_no_booking_history` |
| R2 | ⚠️ | Precondition + booking-history behavior both verified — see Finding #1 for the already-archived gap |
| R3 | ✅ (code inspection) | `archive_station`/`unarchive_station` are pure `UPDATE...RETURNING`; no `DELETE FROM stations` path remains reachable |
| R4 | ✅ | All 3 read paths + archived-view route each have a dedicated test |
| R5 | ✅ | `test_unarchive_station_clears_archived_at`; `test_archived_view_renders_unarchive_only_no_edit_form`; real click-through narrated in commit `c1564f8` |
| R6 | ✅ (lighter shape, per ARCH not REQ's literal wording) | ARCH's Cross-Cutting Concerns and TASKS explicitly prescribe `recipient_delete`'s shape (`note=`, no `from_status`/`to_status`) over REQ's looser "matching `delete_order`" wording — ARCH overrides, not a gap |

**Verification Plan (T1 = tdd):** 19/19 listed scenarios present and assert correctly. The REQ's "already-archived → no-op" edge case was never translated into a Test Plan item at all — a spec-level gap in TASKS, not just an implementation gap.

**Verification Plan (T2 = ui):** 4/4 testable seams present. Human Checklist (3 items) has no screenshots, but commit `c1564f8`'s message narrates the exact three redirects/flashes/states in order — accepted as developer-claim evidence.

**Change Footprint Adherence:** all rows accounted for — ✅ every Modified entry present in the diff, ✅ every Touched-but-not-changed entry (`_station_has_bookings`, `delete_station`+its 5 tests, `admin_prices.html`) confirmed untouched.

**Scope:** ✅ respected — both tasks' Must-NOT-modify lists honored; no `confirm()` added to Un-archive per T2's explicit boundary.

**ARCH Decisions:** A1–A5, A7 followed exactly. A6 followed correctly in letter (booking check fully removed) but ARCH's own Risk & Stress-Test table claims re-archiving an already-archived station is "idempotent in effect, no error" — the actual route behavior disagrees (see Finding #1).

### Findings

| # | Severity | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| 1 | 🟠 High | `main.py` | 2315 (`admin_stations_archive`) | Re-archiving an already-archived station 404s instead of the no-op success the REQ's Edge Cases table (and ARCH's own "double-click" stress-test scenario) explicitly calls for. Root cause: the existence check uses `price_store.list_all_stations()`, which this same task made unconditionally exclude archived stations — so the lookup can never find an already-archived id to re-archive. No test covers either direction; the scenario was never added to TASKS' Test Plan at all. | Look the station up via a method that can still see archived rows (e.g. fall back to `price_store.list_archived_stations()` when not found in the active list) before deciding to 404, so a second archive call redirects with a success/no-op flash instead of 404ing. Add a regression test. |

---

## code-quality

**Result:** ✅ No findings. Naming, complexity (all new functions well under 40 lines, no nesting >3 levels), layer boundaries (`price_store.py` owns 100% of the new SQL; `main.py` routes only do business-rule checks before calling the store), error handling, and the Jinja branching in `admin_stations.html` are all clean and consistent with this codebase's established patterns.

**Observation (Low confidence, not a standalone finding):** `archive_station`/`unarchive_station` are structurally identical single `UPDATE...RETURNING` calls differing only in the SET clause — the codebase's existing convention for a two-state flip (`set_station_active(id, bool)`) suggests one function taking a bool might fit better. Not flagged as a finding: only 2 call sites (below the project's "3+ repetitions" DRY threshold), and ARCH's Patterns & Conventions section explicitly records mirroring `set_station_active`'s shape as the developer's own design choice.

---

## database-patterns

**Result:** 1 Medium finding, 1 Low observation. All SQL parameterized, no injection risk, no missing-index concern (small table, documented app-level filtering), no transaction gaps (single-statement UPDATEs need none).

### Findings

| # | Severity | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| 2 | 🟡 Medium | `price_store.py` | 373–389 (`set_station_active`), 489 (`_station_exists`, via `set_price`) | Write paths don't check `archived_at` — only the three read paths this task touched do. `admin_stations_reactivate` calls `set_station_active(id, True)` directly with no pre-check (unlike archive/unarchive's explicit lookup), so it can flip `is_active=TRUE` on an archived row while `archived_at` stays set — a combination ARCH's own note says should never happen, and it breaks R5's "unarchive always lands on deactivated" guarantee if that station is later unarchived (it would already be active). `set_price` has the analogous gap for price writes. Reachable via direct POST even though the normal UI doesn't expose it (the archived view only shows Un-archive, never Reactivate/Edit). | Add `AND archived_at IS NULL` to `set_station_active`'s UPDATE `WHERE` clause and to `_station_exists`'s check (or `set_price`'s own guard), raising `KeyError`/a clear error instead of silently mutating an archived row. |
| 3 | 💭 Low | `price_store.py` | 340–346 (`upsert_station`'s post-write SELECT) | Doesn't filter `archived_at`, so it would return data for an archived station if ever reached with one. Not currently reachable: `admin_stations_edit`'s own pre-check already 404s before this is called for an archived id. Latent gap only. | Low priority; consider filtering for defense-in-depth consistency, or leave as-is given the only current caller already gates on it. |

---

## error-handling

**Result:** 1 Medium finding (documentation-accuracy, not a code bug), 1 Low (dead-code), 2 manual-check observations matching pre-existing project convention.

### Findings

| # | Severity | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| 4 | 🟡 Medium | `main.py` | 2283–2356 vs ARCH Cross-Cutting Concerns (~line 140) | ARCH claims the new routes' error handling is "consistent with `admin_stations_delete`'s/`admin_stations_deactivate`'s current shape" — this is only half true. `admin_stations_deactivate`/`admin_stations_reactivate` only catch `KeyError`; any other exception propagates to a 500. The new/renamed `admin_stations_archive`/`admin_stations_unarchive` additionally catch a broad `except Exception` and flash gracefully — they actually match `admin_stations_edit`'s/the old `delete`'s broader shape, not `deactivate`/`reactivate`'s narrower one. This isn't a regression from this diff, but it means three of the four station-lifecycle routes now degrade gracefully on a DB failure and one (`deactivate`/`reactivate`) doesn't — and ARCH's stated precedent is inaccurate about which sibling it actually matches. | Either note in ARCH that `deactivate`/`reactivate` are the pre-existing exception (not drift from this diff), or align them with the same broad `except Exception` in a follow-up for full consistency across all four lifecycle routes. |
| 5 | 💭 Low | `main.py` | 2315-2329, 2342-2352 | The pre-existence check (`list_all_stations()`/`list_archived_stations()` → `abort(404)`) already confirms the row's state immediately before the `try` block, making the subsequent `except KeyError` around `archive_station`/`unarchive_station` effectively unreachable in normal execution (nothing else removes a row post-change, hard-delete is dead per A5). Harmless defensive duplication inherited from the old `delete` route's shape. | No action needed. |

**Manual checks (⚠️):**
- No server-side logging on the `except Exception` branches — a DB failure is only surfaced via flash, nothing logged for diagnosis. Matches the existing project convention exactly (`admin_stations_edit`/old `admin_stations_delete` do the same); ARCH's own risk table treats this as acceptable. Not new.
- Raw exception text flashed to the admin UI (`f"Failed to archive station: {e}"`) could leak internal details. Admin-only surface, identical to every sibling route's existing pattern. Not new.

---

## requirement-coverage

**Criteria:** 5/11 covered (R1, R2, R4 strong; see task-completion for R2's overlap with Finding #1) · 3 weak (R3, R5, R6) · 2 edge-case gaps uncovered.

### Findings

| # | Severity | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| — | 🟡 Medium | `tests/test_admin_stations.py` (archive-with-bookings test) | — | R3's "orders/prices/discounts unaffected" half has no assertion anywhere — the existing test only proves the booking check was removed, not that related rows survive intact post-archive. | Add an assertion (even a direct DB read in the real-schema_db test) confirming a pre-existing price/discount row for the archived station is still present and unchanged. |
| — | 🟡 Medium | `tests/test_admin_stations.py` (unarchive tests) | — | R5's "fully editable again exactly as any other deactivated station" is never asserted — no test un-archives then exercises the edit route against the same station. | Add a test: unarchive → POST to `/edit` → assert the rename/price update succeeds. |
| — | 🟡 Medium | `tests/test_admin_stations.py` (audit-call tests) | — | R6 coverage only asserts `len(calls) == 1`, never inspects the call's arguments — so it can't catch that the actual call (`note=f"station_id=..."`, no `from_status`/`to_status`) departs from `delete_order`'s shape the REQ cites as the model (ARCH explicitly overrides this wording, per task-completion above, but the test as written couldn't have caught it either way). | Assert the captured call's args/kwargs explicitly, not just the count. |

**Edge cases:**

| Edge case | Status |
|---|---|
| Archive w/ existing orders succeeds | ✅ Covered |
| Archive an active station rejected | ✅ Covered |
| Archive an already-archived station → no-op | 🔴 Uncovered — see Finding #1 (same root cause) |
| Unarchive a non-archived station rejected | ✅ Covered |
| Archived station's data still readable by an existing order | 🔴 Uncovered — no test exercises this |

**Orphan tests/files:** none — every new test traces to a T1/T2 scenario and every changed file is named in the Footprint.

---

## Manual Checks Required

- [ ] Confirm whether `deactivate`/`reactivate`'s narrower error handling (Finding #4) is an intentional asymmetry worth documenting, or should be aligned in a follow-up.
- [ ] Decide whether Finding #2 (write-path archived-bypass) is worth closing now or tracking separately — it's reachable only via direct POST, not through the UI, but it's a real state-integrity gap.

## Prioritized Action Items

### Must Fix (🔴 Critical / 🟠 High)
- **Finding #1** — re-archiving an already-archived station 404s instead of no-op, contradicting a documented REQ decision and ARCH's own stress-test claim. Fix the existence lookup; add the missing test.

### Should Address (🟡 Medium)
- **Finding #2** — `set_station_active`/`set_price`/`_station_exists` don't check `archived_at`; a direct POST to `/reactivate` or a price edit can silently corrupt an archived station's state.
- **Finding #4** — ARCH's sibling-consistency claim is only half accurate; either document the asymmetry or align `deactivate`/`reactivate`'s error handling.
- Requirement-coverage's 3 weak-assertion findings (R3, R5, R6) — tests exist but don't verify the full claim; worth strengthening before the next change touches this area.

### Nice to Have (💭 Low)
- **Finding #3** — `upsert_station`'s post-write SELECT doesn't filter `archived_at` (latent, not currently reachable).
- **Finding #5** — dead-code `KeyError` catch in the new routes (harmless, inherited shape).
- code-quality's observation on `archive_station`/`unarchive_station`'s structural duplication vs. the `set_station_active(bool)` convention.

---
*Generated by Review — 2026-11-10*
