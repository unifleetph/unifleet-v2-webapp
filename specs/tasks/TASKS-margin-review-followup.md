# Tasks

## Task T1: Audit trail on global margin update

> **Status:** done
> **Verification:** tdd
> **Effort:** xs
> **Priority:** medium
> **Depends on:** None
> **Satisfies REQs:** R1
> **Footprint slice:** Modified: `main.py:2272-2283` (`admin_margin_update`)
> **High-risk areas touched:** None

### Description

`admin_margin_update` validates and persists the global margin % but never records the change anywhere — ARCH's A8 decision (inherited from the original `ARCH-profit-margin`) committed to logging every change via the existing `audit_log.append_audit`, and that call was never added. This task wires it in: capture the old value before `.set()`, then log old→new on success, including the `reason` string already passed to `margin_store.set()`.

### Test Plan

#### Test File(s)
- `tests/test_admin_pricing_endpoints.py` (existing `admin_margin_update` section, ~line 327 onward)

#### Test Scenarios

##### Margin Update Audit Trail

- **valid update logs an audit entry** — GIVEN `fake_margin_store.value = 10.0` WHEN `POST /admin/margin/update` with `margin_pct=15` succeeds THEN `append_audit` was called once with `action="margin_update"`, `voucher_id=None`, `from_status="10.0"`, `to_status="15.0"`, `note="manual update"` _(verifies R1)_

##### Regression Guard — No Audit Noise on Rejected/Unauthorized Requests

- **rejected update does not log** — GIVEN an invalid payload (over 100, negative, or >2 decimals) WHEN `POST /admin/margin/update` is called THEN `append_audit` is NOT called _(REQ edge case: validation failure must not create a misleading audit row)_
- **unauthenticated request does not log** — GIVEN no admin session WHEN `POST /admin/margin/update` is called THEN it 403s and `append_audit` is NOT called _(guards ARCH backward-regression risk: auth check must still short-circuit before any side effect)_

### Implementation Notes

- **Module(s):** `main.py` only — no change to `margin_store.py` or `audit_log.py`.
- **Pattern reference:** `admin_recipient_delete` / other admin routes in `main.py` (e.g. `append_audit("recipient_delete", None, note=...)`) — same `voucher_id=None`, `note=` shape for a non-voucher action.
- **Key decisions:** A1 (wire `append_audit`, no new history table); developer confirmed during architecture review that `note=reason` should be included (closes ARCH's Open Question in this same task rather than deferring it).
- **Libraries:** none new — `audit_log.append_audit` is already imported in `main.py`.
- **High-risk callouts:** None — this is an additive side-effect on an existing, already-guarded route; `append_audit`'s own try/except means a DB hiccup here can't break the margin update response.

### Scope Boundaries

- Do NOT add a `margin_history` table (ARCH Out of Scope — audit via `audit_log.py` only).
- Do NOT change `admin_margin_update`'s response shape or status codes — only add the audit side-effect.
- Only touch `main.py:2272-2283` — no changes to `margin_store.py`.

### Files Expected

**Modified files:**
- `main.py` (capture old value before `.set()`; add `append_audit` call on success)
- `tests/test_admin_pricing_endpoints.py` (new test + 2 regression-guard tests in the existing margin section)

**Must NOT modify:**
- `margin_store.py` (out of scope — `.set()`'s signature/behavior is unchanged)
- `audit_log.py` (out of scope — using its existing public API as-is)

---

## Task T2: Read global margin once per `/book` GET request

> **Status:** done
> **Verification:** tdd
> **Effort:** s
> **Priority:** medium
> **Depends on:** None
> **Satisfies REQs:** R2
> **Footprint slice:** Modified: `main.py:1367-1376` (`_margin_adjusted_discounts`), `main.py` `book()` GET call sites at `1466` and `1546`
> **High-risk areas touched:** `/book` GET rendering (Areas of Impact: Low — pure read-path optimization, output must stay byte-identical)

### Description

`_margin_adjusted_discounts(fuel_type)` calls `margin_store.get()` internally every time it's invoked. `book()`'s GET path calls it once for `"Biodiesel"` plus once per `FUEL_TYPES` entry — 4 DB round-trips per page view for one global scalar. This task adds an optional `margin_pct` parameter so `book()` can fetch the value once and thread it through, while every other caller (which doesn't have the value handy) keeps working unchanged.

### Test Plan

#### Test File(s)
- `tests/test_book_margin.py` (existing file)

#### Test Scenarios

##### Single Fetch Per Request

- **GET /book calls margin_store.get() exactly once** — GIVEN `margin_store.get` wrapped in a call-counting lambda WHEN `GET /book` is requested THEN the call count is exactly 1, regardless of how many entries `FUEL_TYPES` has _(verifies R2)_

##### Contract — Explicit `margin_pct` Bypasses Internal Fetch

- **passing margin_pct uses it directly** — GIVEN `_margin_adjusted_discounts("Biodiesel", margin_pct=12.25)` is called directly WHEN the discounts are computed THEN `margin_store.get()` is NOT called and the 12.25 value is what's applied _(verifies R2 core contract)_

##### Regression Guard — Back-Compat for Other Callers

- **GET /book output is unchanged** — GIVEN the same fixture setup as the existing `test_get_book_shows_post_margin_discount_for_non_exempt_station` THEN the rendered post-margin discount values per fuel type are identical to before this change _(guards backward-regression risk: `book()` GET's rendered output)_
- **omitting margin_pct still works (back-compat)** — GIVEN `_margin_adjusted_discounts(fuel_type)` is called with no second argument (as `main.py:2463`'s `/api/v1/discounts` route does) WHEN the function runs THEN it calls `margin_store.get()` internally and returns the same result as before this change _(guards backward-regression risk for `main.py:2463`, a touched-but-not-changed call site)_

### Implementation Notes

- **Module(s):** `main.py` only.
- **Pattern reference:** existing per-request value threading in `book()` (e.g. how `price_store` lookups are scoped to the request) — follow the same "fetch once at the top, pass down" shape.
- **Key decisions:** A2 (explicit parameter over a module/request-level cache — no new state/invalidation machinery).
- **Libraries:** none new.
- **High-risk callouts:** `/book` GET rendering is the one Area of Impact this task touches directly — mitigated by the "output is unchanged" regression test above, which pins the exact values the existing suite already asserts.

### Scope Boundaries

- Do NOT change `_margin_adjusted_discounts`'s behavior when `margin_pct` is omitted — must remain fully back-compat for `main.py:2463`.
- Do NOT touch `main.py:1790` (`/book` POST's `margin_pct_at_booking = margin_store.get()`) — that call site is unrelated to the GET-path redundancy this task fixes.
- Only touch the 3 named locations in `main.py` (function signature + 2 call sites inside `book()` GET).

### Files Expected

**Modified files:**
- `main.py` (`_margin_adjusted_discounts` gains optional `margin_pct` param; `book()` GET fetches once and passes it to both call sites)
- `tests/test_book_margin.py` (4 new/updated test scenarios above)

**Must NOT modify:**
- `main.py:1790` (`/book` POST margin fetch — out of scope, different call site)
- `main.py:2463` (`/api/v1/discounts` — touched-but-not-changed; covered by the back-compat regression test)

---

## Task T3: Consolidate `discount_store.py`'s "all"-rows query methods

> **Status:** done
> **Verification:** test-after
> **Effort:** s
> **Priority:** low
> **Depends on:** None
> **Satisfies REQs:** R3
> **Footprint slice:** Modified: `discount_store.py:85-161` (`get_all`, `get_all_with_updated_at`, `get_all_with_exempt`)
> **High-risk areas touched:** `discount_store.py` consumers (Areas of Impact: Low–Medium — internal-only refactor behind unchanged public methods, but the one place a subtle shape mismatch could slip past existing tests)

### Description

`get_all`, `get_all_with_updated_at`, and `get_all_with_exempt` are near-identical copy-pasted SQL + row-mapping, differing only in which columns are projected. This task adds a private `_fetch_all(fuel_type, extra_cols=())` helper that runs one parameterized query, and rewrites the 3 public methods as thin wrappers over it — with their exact existing signatures and return shapes preserved, since 9+ call sites and test files monkeypatch them by name.

### Test Plan

#### Test File(s)
- `tests/test_discount_store.py` (existing file — must pass unchanged)
- `tests/test_admin_pricing_endpoints.py`, `tests/test_book_and_booking_success_copy.py`, `tests/test_book_template_layout.py`, `tests/test_book_fuel_type_data.py`, `tests/test_book_station_filter.py`, `tests/test_book_calculator_inversion.py`, `tests/test_book_pg.py`, `tests/test_api_v1_pricing.py`, `tests/test_admin_display_layout.py` (existing files — must pass unchanged, regression guards only)

#### Test Scenarios

##### Regression Guard — Public Shapes Unchanged

- **full existing `test_discount_store.py` suite passes unchanged** — GIVEN the refactored `discount_store.py` WHEN the existing test suite runs THEN every existing `get_all`/`get_all_with_updated_at`/`get_all_with_exempt` test still passes with no modification _(guards backward-regression risk: return shapes these methods are documented to produce)_
- **full existing dependent suites pass unchanged** — GIVEN the refactored `discount_store.py` WHEN `test_admin_pricing_endpoints.py`, the `test_book_*` files, `test_api_v1_pricing.py`, and `test_admin_display_layout.py` run THEN all pass unchanged (these monkeypatch `get_all_with_exempt`/`get_all_with_updated_at` by name and depend on the exact call signature) _(guards backward-regression risk for every touched-but-not-changed caller)_

##### Consolidation Contract

- **three methods independently return correct, non-cross-contaminated shapes from one shared helper** — GIVEN one seeded station/fuel_type row with a known `value`, `updated_at`, and `margin_exempt` WHEN `get_all`, `get_all_with_updated_at`, and `get_all_with_exempt` are each called for that fuel_type THEN each returns exactly its own documented shape (bare value / value+updated_at / value+margin_exempt respectively) with correct values for that row _(verifies R3 core contract)_

### Implementation Notes

- **Module(s):** `discount_store.py` only — no change to `main.py` or any caller.
- **Pattern reference:** the thin-wrapper-over-private-helper shape already implied by the module's own docstring conventions; no external pattern to mirror since this is new internal structure.
- **Key decisions:** A3 (private `_fetch_all`, public methods unchanged — rejected merging/renaming the public methods specifically to avoid rippling into 9+ call sites).
- **Libraries:** none new — same `psycopg`/`dict_row` usage as today.
- **High-risk callouts:** this is the one M-risk area in the whole ARCH; the consolidation-contract test above specifically guards against the shared helper silently mixing up which `extra_cols` belong to which public method.

### Scope Boundaries

- Do NOT rename, merge, or change the signature of `get_all`, `get_all_with_updated_at`, or `get_all_with_exempt` — public API must stay byte-identical.
- Do NOT touch `get`, `get_with_exempt`, `set`, `set_many`, or `clear_all` — out of scope, only the 3 "all" variants are duplicated.
- Do NOT touch any `main.py` call site — if a call site needs to change, that's a sign this task overstepped scope.

### Files Expected

**Modified files:**
- `discount_store.py` (new private `_fetch_all` helper; 3 public methods rewritten as thin wrappers)
- `tests/test_discount_store.py` (1 new consolidation-contract test added)

**Must NOT modify:**
- `main.py` (out of scope — no caller needs to change)
- `tests/test_admin_pricing_endpoints.py`, `test_book_*.py`, `test_api_v1_pricing.py`, `test_admin_display_layout.py` (touched-but-not-changed — must pass as-is, proving the refactor is invisible to them)

---

## Task T4: Replace tautological margin-retroactivity test

> **Status:** done
> **Verification:** tdd
> **Effort:** xs
> **Priority:** medium
> **Depends on:** None
> **Satisfies REQs:** R4
> **Footprint slice:** Modified: `tests/test_book_margin.py:175-187`
> **High-risk areas touched:** `ops_set_status` non-zero-snapshot path (regression-risk target this task exists to cover)

### Description

`test_margin_changed_after_booking_does_not_retroactively_change_stored_snapshot` re-reads an in-memory dict that nothing in the test actually mutates — it cannot fail even if the real guarantee broke. This task replaces it with a test that drives `ops_set_status`'s real non-zero-`discount_snapshot_php_per_liter` branch (`main.py:998`) after the global margin has changed, and asserts the Approve-flow result still reflects the margin at booking time, not the new one.

### Test Plan

#### Test File(s)
- `tests/test_book_margin.py` (replaces the existing test at lines 175-187)

#### Test Scenarios

##### Retroactivity Guard — Real Non-Zero-Snapshot Path

- **margin change after booking does not alter an Approve-flow result driven from a non-zero snapshot** — GIVEN a voucher with `discount_snapshot_php_per_liter=8.775` (margin A=12.25 already applied to raw 10.0, non-exempt) and `margin_pct_at_booking=12.25` AND `margin_store.get()` monkeypatched to return B=20.0 (simulating a margin change after booking) WHEN `GET /ops/voucher/<id>/status/Unredeemed` is called (exercising `main.py:998`'s non-zero-`snap_disc` branch, which must skip live recompute) THEN the resulting `discount_per_liter` field is `8.775` (reflects A) and `discount_total_php` is computed from `8.775`, not from any value derived from margin B _(verifies R4, replaces the old tautological test)_
- **sanity check the test exercises the intended branch** — the same scenario first asserts the voucher's `discount_snapshot_php_per_liter != 0` before calling the ops route, so a future refactor that accidentally zeroes the snapshot can't make this test silently exercise the wrong (zero-snapshot fallback) branch instead _(guards against the new test itself becoming a false positive)_

### Implementation Notes

- **Module(s):** test-only change — no production code in this task.
- **Pattern reference:** `tests/test_admin_approve_margin.py`'s `RepoStub` + `client.get("/ops/voucher/...")` pattern (specifically `test_fallback_applies_margin_when_snapshot_zero_and_station_not_exempt`), adapted for the non-zero-snapshot branch instead of the zero-snapshot fallback it covers.
- **Key decisions:** A4 (replace, don't add alongside — the old test provides false confidence and should not remain).
- **Libraries:** none new.
- **High-risk callouts:** this task's entire purpose is closing a regression-risk gap (ARCH's Risk & Stress-Test backward section) — the sanity-check scenario above exists specifically to make sure the new test doesn't repeat the old one's mistake in a new form.

### Scope Boundaries

- Do NOT modify `main.py` — this is a test-only fix; if the non-zero-snapshot branch appears broken when writing this test, stop and flag it rather than silently "fixing" production code under this task.
- Do NOT remove or weaken any of the other existing tests in `test_book_margin.py` — only the one named test is replaced.

### Files Expected

**Modified files:**
- `tests/test_book_margin.py` (replace `test_margin_changed_after_booking_does_not_retroactively_change_stored_snapshot` with the new scenario above)

**Must NOT modify:**
- `main.py` (out of scope — test-only task)

---

## Task T5: Repo-layer round-trip test for `margin_pct_at_booking`

> **Status:** done
> **Verification:** tdd
> **Effort:** xs
> **Priority:** low
> **Depends on:** None
> **Satisfies REQs:** R5
> **Footprint slice:** New test content in `tests/test_postgres_repo.py`
> **High-risk areas touched:** None (purely additive — reveals a pre-existing gap if one exists, cannot cause a regression)

### Description

`margin_pct_at_booking` is in `VOUCHER_COLUMNS`, but no existing test exercises the real Postgres repo round-trip for it — `test_book_margin.py`'s `RepoStub` just echoes a dict back, bypassing `db/postgres_repo.py`'s actual column mapping. This task adds a real-`schema_db` test proving the column survives `create_unverified_booking` → `get_voucher`.

### Test Plan

#### Test File(s)
- `tests/test_postgres_repo.py` (existing file, new test added near the other round-trip tests, ~line 488)

#### Test Scenarios

##### Real Column Round-Trip

- **margin_pct_at_booking round-trips through the real repo** — GIVEN a real `schema_db` + `PostgresRepo` WHEN `create_unverified_booking({"driver_name": ..., "vehicle_plate": ..., "margin_pct_at_booking": 12.25})` is called THEN the immediate return value has `margin_pct_at_booking == 12.25`, AND calling `get_voucher(result["voucher_id"])` afterward also returns `margin_pct_at_booking == 12.25` — proving the real INSERT/SELECT column mapping in `db/postgres_repo.py`, not a stub _(verifies R5)_

### Implementation Notes

- **Module(s):** test-only change — no production code in this task.
- **Pattern reference:** `test_create_unverified_booking_with_account_code_round_trips` (`tests/test_postgres_repo.py:488`) — same `schema_db` fixture, same "create then re-fetch" shape.
- **Key decisions:** A5 (real-Postgres test, not an extension of the `RepoStub`-based test — the gap is specifically that the stub bypasses the real mapping).
- **Libraries:** none new — same `psycopg`/`schema_db` fixture already used throughout `test_postgres_repo.py`.
- **High-risk callouts:** None. If this test fails, it reveals a pre-existing production bug (column not actually persisted), not a regression introduced by this task or by T1-T4.

### Scope Boundaries

- Do NOT modify `db/postgres_repo.py` or `models.py` — if the test reveals a real mapping bug, stop and flag it as a separate finding rather than silently patching it under this task.
- Do NOT modify `tests/test_book_margin.py`'s `RepoStub`-based tests — they stay as-is; this task adds a parallel, real-DB test, not a replacement.

### Files Expected

**Modified files:**
- `tests/test_postgres_repo.py` (1 new test added)

**Must NOT modify:**
- `db/postgres_repo.py`, `models.py` (out of scope unless the test reveals a real bug, in which case stop and flag rather than fix silently)
- `tests/test_book_margin.py` (its `RepoStub`-based margin tests are a separate, unrelated layer)
