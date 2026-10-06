# Architecture: Profit-Margin Code Review Follow-Up

> **Date:** 2026-10-06
> **Phase:** 2 of 5 (System Architecture)
> **Requirements source:** Standalone brief — see Inferred Requirements. (`docs/Brief_change_table_logic.md` described the margin feature, which turned out to already be shipped; this ARCH instead closes the open "Should Address" findings from `specs/reviews/CODE-REVIEW-PIPELINE-profit-margin.md`.)
> **Tasks:** TASKS-margin-review-followup.md
> **Type:** refactor

## Architecture Summary

The global profit-margin feature (`margin_store.py`, T1-T5, commits `0b899f0`..`5b51b6a` + 2 post-review fixes) is fully shipped and in production. Its code review passed with both must-fix items resolved, but left 5 "Should Address" findings open: no audit trail on margin changes, redundant per-request DB reads, duplicated query code in `discount_store.py`, one tautological test, and one untested repo round-trip. This ARCH closes exactly those 5 — no new behavior, no schema change, no new endpoint. Every change is a local edit to an existing function or a new/rewritten test; nothing here alters what margin-related code does from the outside.

## Inferred Requirements (if Mode B / no REQ)

| ID | Inferred Requirement | Source |
|----|------------------------|--------|
| R1 | Every successful change to the global margin % must be recorded in the existing audit log (actor/action/old/new), matching every other admin-write route's pattern. | Review task-completion#2 / code-quality#3 — ARCH-profit-margin's own A8 decision, never implemented. |
| R2 | A single `/book` GET page view must read the global margin value at most once, not once per fuel type. | Review code-quality#2. |
| R3 | `discount_store.py`'s three "all"-rows query methods must share one query implementation; public method names and return shapes must not change (existing callers/tests depend on them). | Review code-quality#1. |
| R4 | There must be an automated test that actually exercises the "booking keeps its margin-at-booking-time value even after the global margin later changes" guarantee via the real non-zero-snapshot code path (`ops_set_status`), not a tautological re-read of an untouched stub. | Review requirement-coverage#1. |
| R5 | `margin_pct_at_booking` must be proven to round-trip through the real Postgres repo layer (`create_unverified_booking` → `get_voucher`), not just an in-memory `RepoStub`. | Review requirement-coverage#2. |

## High-Level Structure

No structural change. This is 5 point-fixes inside the existing margin feature's footprint:

```
main.py
 ├─ admin_margin_update()          [R1: + append_audit call]
 ├─ book() GET                      [R2: fetch margin once, pass down]
 └─ _margin_adjusted_discounts()   [R2: accept optional margin_pct]

discount_store.py
 └─ get_all / get_all_with_updated_at / get_all_with_exempt
      [R3: rewritten to share one private _fetch_all() helper]

tests/test_book_margin.py           [R4: rewritten test]
tests/test_postgres_repo.py         [R5: new test]
```

## Tech Choices

No new tech. Every change reuses an existing, already-adopted mechanism:

| Area | Decision | Alternatives Considered | Rationale |
|------|----------|--------------------------|-----------|
| Audit trail (R1) | Call existing `audit_log.append_audit()` | New `margin_history` table | ARCH-profit-margin's A8 already chose `append_audit`; a history table was explicitly deferred (scope boundary in T1). Just wire the call that was never added. |
| Perf fix (R2) | Pass `margin_pct` as an explicit parameter through `_margin_adjusted_discounts()` | Cache margin value in a module-level/request-level global | Explicit parameter keeps the function pure and testable, matches how `price_store`/other per-request values are already threaded through `book()`; a cache adds invalidation complexity for a single-digit-ms DB read. |
| Query dedup (R3) | Private `_fetch_all(fuel_type, extra_cols=())` helper, public methods as thin wrappers | Merge the 3 public methods into 1 with an optional flag | Tests and call sites (`main.py`, 5+ test files) monkeypatch the 3 methods by their current names; renaming/merging the public surface would ripple needlessly. Internal-only consolidation gets the dedup with zero caller impact. |
| Test fix (R4) | Rewrite the existing test to drive `ops_set_status`'s real non-zero-`snap_disc` path | Add a second, separate test alongside the old one | The old test is tautological (asserts nothing meaningful); keeping it alongside a real one is dead weight. Replace it. |
| Repo round-trip test (R5) | New test in `tests/test_postgres_repo.py`, same pattern as `test_create_unverified_booking_with_account_code_round_trips` | Extend `test_book_margin.py`'s `RepoStub`-based test | The gap is specifically that `RepoStub` bypasses the real column mapping in `db/postgres_repo.py` — the fix has to run against a real `schema_db`, which is what `test_postgres_repo.py` already does for every other voucher column. |

## Patterns & Conventions

- **Thin-wrapper-over-private-helper** — `discount_store.py`'s R3 fix follows the same shape the codebase already uses elsewhere for public-API-stability-over-internal-dedup; no new convention introduced.
- **`append_audit` call-site convention** — every existing admin write route calls it right after a successful mutation, with `voucher_id=None` for non-voucher actions (see `recipient_add`, `customer_email_update`). R1 follows this exactly.
- **`schema_db` fixture for repo-layer tests** — `test_postgres_repo.py`'s existing pattern (live ephemeral Postgres, not a stub) is reused verbatim for R5.

## Data Models

No schema change. No new or modified tables/columns. `margin_settings`, `discounts.margin_exempt`, and `vouchers.margin_pct_at_booking` are all unchanged — this ARCH only adds code paths that read/write them more correctly or test them more thoroughly.

## API Contracts / Interfaces

No contract change. `/admin/margin/update`'s request/response shape is untouched by R1 (the audit call is a side effect, not a response-shape change). `/book` GET's rendered output is unchanged by R2 (same values, fewer DB round-trips to produce them). No new or removed endpoints.

### `_margin_adjusted_discounts` (internal helper, `main.py`)

**Boundary:** internal module function, not externally callable.

| Signature (before) | Signature (after) |
|---|---|
| `_margin_adjusted_discounts(fuel_type)` | `_margin_adjusted_discounts(fuel_type, margin_pct=None)` — when `None`, fetches internally (preserves existing callers at `main.py:2463` and any future caller that doesn't have the value handy). |

## Module Boundaries

Unchanged. `margin_store.py` and `discount_store.py` remain mutually independent (explicitly verified by the original review, "Module Boundaries honored both directions") — none of these 5 fixes introduce a cross-import between them.

## Change Footprint

### New files / modules

None.

### Modified files / modules

| Path | What changes here |
|------|---------------------|
| `main.py:2272-2283` (`admin_margin_update`) | Read `old = margin_store.get()` before calling `.set()`; after a successful `.set()`, call `append_audit("margin_update", None, from_status=str(old), to_status=str(new_margin))`. |
| `main.py:1367-1376` (`_margin_adjusted_discounts`) | Add optional `margin_pct=None` parameter; use it when provided, otherwise call `margin_store.get()` as today (back-compat for the `/api/v1/discounts` call site). |
| `main.py` `book()` GET, call sites at `1466` and `1546` | Fetch `margin_pct = margin_store.get()` once near the top of the GET branch; pass it into both `_margin_adjusted_discounts()` calls instead of letting each call fetch it independently. |
| `discount_store.py:85-161` | Add private `_fetch_all(self, fuel_type, extra_cols=())` running one parameterized query; rewrite `get_all`, `get_all_with_updated_at`, `get_all_with_exempt` to call it with the right `extra_cols` and shape the return dict as each already does. Public method names, signatures, and return shapes unchanged. |
| `tests/test_book_margin.py:175-187` | Replace `test_margin_changed_after_booking_does_not_retroactively_change_stored_snapshot` with a test that: books a voucher with global margin = A (captured into `margin_pct_at_booking` / `discount_snapshot_php_per_liter`), changes the global margin to B, then calls `ops_set_status` (`main.py:938`) on that voucher through its normal non-zero-`snap_disc` path (`main.py:998`), and asserts the resulting discount/total reflects A, not B. |

### New test file content (not a new module, additive test only)

| Path | What's added |
|------|----------------|
| `tests/test_postgres_repo.py` | New `test_create_unverified_booking_with_margin_pct_at_booking_round_trips(schema_db)`, mirroring `test_create_unverified_booking_with_account_code_round_trips` (line ~488): create a booking with `margin_pct_at_booking` set via real `create_unverified_booking`, then `get_voucher`, assert the value round-trips through the actual Postgres column mapping. |

### Deleted / replaced

None — the R4 test is a content replacement inside an existing file, not a file deletion.

### Touched but not changed (silent-regression hotspots)

| Path | Why it matters |
|------|------------------|
| `tests/test_discount_store.py` | Directly tests `get_all`, `get_all_with_updated_at`, `get_all_with_exempt` against a real `schema_db` — must keep passing unchanged through the R3 refactor; this is what proves the thin-wrapper rewrite preserved exact return shapes. |
| `tests/test_admin_pricing_endpoints.py`, `test_book_and_booking_success_copy.py`, `test_book_template_layout.py`, `test_book_fuel_type_data.py`, `test_book_station_filter.py`, `test_book_calculator_inversion.py`, `test_book_pg.py`, `test_api_v1_pricing.py`, `test_admin_display_layout.py` | All monkeypatch/stub `discount_store.get_all_with_exempt` / `get_all_with_updated_at` by name — R3's refactor must not rename or change the call signature these rely on. |
| `main.py:2463` (`/api/v1/discounts`'s `_resolve_fuel_type_param` call) | Calls `_margin_adjusted_discounts(fuel_type)` with one positional arg — must keep working once the function gains the new optional parameter. |
| `main.py:938` `ops_set_status`, specifically the non-zero-`snap_disc` branch | This is the exact path R4's new test must drive for real; if a future change alters this branch's logic without updating this test, that's the regression the test exists to catch. |

## Areas of Impact

| Area | Impact | Risk (L/M/H) | Why |
|------|--------|---------------|-----|
| Admin margin update route | Gains an audit side-effect; no behavior/response change | L | Audit write failures are swallowed by `append_audit`'s own try/except (matches every other call site) — cannot break the route. |
| `/book` GET rendering | Same values, 3 fewer DB round-trips per page view (1 instead of 4) | L | Pure read-path optimization; output is byte-identical, verified by existing `test_book_*` suite. |
| `discount_store.py` consumers | Internal-only refactor behind unchanged public methods | L–M | Low risk given the thin-wrapper approach, but it's the one place a subtle shape mismatch could slip past the existing tests if `extra_cols` handling is off — mitigated by running the existing `test_discount_store.py` suite unchanged. |
| Test suite | 1 test replaced, 1 test added | L | Test-only change; worst case is a new test failing loudly in CI, never a runtime regression. |

**Contract changes:** none. No external API response, event payload, or public type changes.

**Cross-cutting ripples:** none beyond the audit log gaining one new `action` value (`"margin_update"`) — purely additive to `audit_log` table rows, no schema change (the table already stores arbitrary `action` strings for 10+ distinct actions).

## Cross-Cutting Concerns

- **Errors:** R1's `append_audit` call inherits that function's existing swallow-and-log-to-stderr failure policy — an audit write failure still never blocks the margin update response, consistent with every other admin route. No new error paths introduced by R2/R3 (pure refactor of existing, already-guarded code).
- **Logging & metrics:** R1 adds one new `audit_log.action` value (`"margin_update"`) with `from_status`/`to_status` carrying the old/new percentage as strings — queryable the same way `recipient_add`/`delete_order`/etc. already are. No new metrics.
- **Auth / authz:** Unchanged. `admin_margin_update` still gated by `require_admin(request)`; no auth logic touched by any of the 5 fixes.
- **Performance:** R2 directly improves `/book` GET from 4 margin-related DB round-trips to 1 per page view. No other performance impact.
- **Security:** No new input surface, no new validation boundary. R1's `append_audit` call passes only values already validated by `MarginStore._validate()` (0-100, ≤2 decimals) — nothing unsanitized reaches the audit row beyond what the route already handles.
- **Migrations / rollout:** None needed. No schema change. Deploy is a plain code push; fully backward-compatible (the new `margin_pct` parameter on `_margin_adjusted_discounts` defaults to the old behavior).

## Architecture Decisions Log

| # | Decision | Alternatives | Chosen Because | Satisfies REQs |
|---|----------|---------------|------------------|-----------------|
| A1 | Wire `append_audit` into `admin_margin_update`, capturing old value before `.set()` | New `margin_history` table | Matches the feature's own original A8 decision; a history table was explicitly deferred as a T1 scope boundary and nothing has changed to revisit that | R1 |
| A2 | Thread `margin_pct` through `_margin_adjusted_discounts()` as an optional parameter, fetched once in `book()` | Module/request-level cache of the margin value | Simplest fix with zero new state/invalidation concerns; keeps the function's existing default-fetch behavior for other callers | R2 |
| A3 | Consolidate `discount_store.py`'s 3 "all" query variants behind one private `_fetch_all()`, public methods unchanged | Merge/rename the public methods | 9+ call sites and test files monkeypatch the current method names directly; an internal-only consolidation achieves the dedup with zero blast radius | R3 |
| A4 | Replace the tautological retroactivity test with one driving `ops_set_status`'s real non-zero-snapshot path | Add a second test alongside the old one | The old test asserts nothing meaningful (re-reads an untouched stub); keeping it provides false confidence | R4 |
| A5 | Add the margin round-trip test to `tests/test_postgres_repo.py` against real `schema_db`, mirroring the existing account_code round-trip test | Extend the `RepoStub`-based test in `test_book_margin.py` | The gap is specifically that `RepoStub` bypasses the real column mapping — only a real-Postgres test closes it | R5 |

## Risk & Stress-Test Scenarios

### Forward — runtime failure scenarios

| Scenario | How the Design Handles It |
|----------|------------------------------|
| `append_audit`'s DB write fails (pool exhausted) right after a margin update | Swallowed by `append_audit`'s own existing try/except + stderr log (unchanged behavior) — the margin update itself already succeeded and its response is already sent; no new failure mode introduced. |
| `margin_store.get()` call at the top of `book()` GET fails (DB blip) | Unaffected by R2 — this call sits in the same position (top of the GET handler, outside any snapshot-specific try/except) as today's first call; no regression in error handling introduced, since R2 doesn't touch error handling, only call count. |
| `discount_store._fetch_all()`'s single query fails for one of the 3 wrapper methods | Same as today — each public method's existing try/except (if any) still wraps the call; no new exception types introduced since it's the same SQL, just deduplicated. |
| Two admins update the global margin concurrently | Unchanged (out of scope for this ARCH) — last-write-wins on `margin_settings`, same as before these 5 fixes. R1 at least now makes both writes visible in the audit log, improving post-hoc detection of this scenario even though it doesn't prevent it. |

### Backward — regression risk per touched area

| Touched area (from Change Footprint) | What could regress | How we'd know / mitigation |
|----------------------------------------|----------------------|-------------------------------|
| `admin_margin_update` (R1) | Audit call accidentally reads `margin_store.get()` *after* `.set()` instead of before, logging `old == new` | New assertion in the task's test plan: verify `from_status` differs from `to_status` when the value actually changed. |
| `_margin_adjusted_discounts` / `book()` GET (R2) | Passing a stale or wrong `margin_pct` into one of the two call sites, causing one fuel type's discounts to use a different margin than another's within the same page render | Existing `test_book_*` suite already asserts discount values per fuel type; re-run full suite after the change — any divergence between fuel types' margin application would fail those assertions. |
| `discount_store.py` `_fetch_all` (R3) | `extra_cols` wiring subtly changes a column alias or row-mapping key for one of the 3 wrapper methods (e.g. `get_all_with_exempt` stops returning `margin_exempt` as a bool) | `tests/test_discount_store.py`'s existing method-specific tests (e.g. `test_get_all_with_exempt_returns_correct_flags_for_mixed_rows`) run unchanged against the refactored code — any shape drift fails there before it reaches `main.py`. |
| `ops_set_status` non-zero-snapshot path (R4's new test target) | The new test could itself be subtly wrong (e.g. accidentally exercising the zero-snapshot fallback instead) | Task spec must require the test to first assert `discount_snapshot_php_per_liter != 0` on the booked voucher before calling `ops_set_status`, so the test demonstrably exercises the intended branch. |
| `create_unverified_booking` / `get_voucher` (R5's new test target) | None — purely additive; cannot regress existing behavior, only reveal a pre-existing gap if one exists | If the new test fails, that's a real bug in production right now (column not actually persisted) — not a regression caused by this ARCH. |

## Open Questions

- Should the `append_audit("margin_update", ...)` call also record the `reason` parameter that `admin_margin_update` already passes to `margin_store.set()` (currently `"manual update"`, hardcoded)? The review's migration finding #2 noted `reason` is accepted but never persisted anywhere.
  - **Impact if unresolved:** The audit row records old/new value but not a human-readable reason, slightly less rich than the original A8 intent implied.
  - **Suggested default:** Pass it through as `note=reason` on the `append_audit` call — trivial to include while already touching this line; recommend doing it as part of R1 rather than deferring.

## Out of Scope

- The 2 must-fix items from the review (both already fixed in commits `0ba3890`, `36dcfac`) — not touched again here.
- The 4 remaining nice-to-haves: echoing `margin_store.set()`'s persisted value instead of the raw request payload; asserting rendered HTML (not just template context) for the margin input field; a literal ALTER-on-populated-table backfill test; dropping the unused `id="margin-save-feedback"` attribute; fixing the `apply()` signature doc mismatch in the old ARCH. (Reason: user scoped this round to the 5 "Should Address" items only.)
- Per-row/per-station margin overrides, a dedicated `margin_history` table, or any other feature-level change to the margin mechanism itself — this ARCH is cleanup of the existing, already-shipped design, not a redesign.
