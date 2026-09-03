# Review Report

## Metadata

| Field | Value |
|-------|-------|
| **Review Mode** | Pipeline: ARCH-profit-margin |
| **Target** | specs/architecture/ARCH-profit-margin.md |
| **Date** | 2026-09-03 |
| **Tech Stack** | Python 3.11 / Flask, Postgres via psycopg3, pytest, Jinja2 + vanilla JS, Docker/docker-compose |
| **Checks Run** | task-completion, code-quality, security, error-handling, database-patterns, migration, requirement-coverage |
| **Checks Skipped** | test-coverage (developer drove every RED/GREEN cycle directly this session), performance (simple percentage math, no algorithmic complexity), documentation (internal admin tooling), config-dependencies (no new deps/env vars), typescript-strictness/react-patterns/express-patterns/async-patterns/runtime-behavior (none apply — Flask sync app, vanilla inline JS), accessibility (single label+input+button addition, eyeballed under code-quality instead) |
| **Files Changed** | 18 (commits `0b899f0`..`5b51b6a`, T1-T5) |
| **Lines Changed** | +1340 / -35 |

## Review Process

- [x] Preflight checks passed
- [x] Diff gathered (18 files, ~1375 lines, scoped to `9a6b5f9..HEAD`)
- [x] Tech stack detected: Python/Flask/Postgres
- [x] Context read (CLAUDE.md, AGENTS.md, REQ-profit-margin.md, ARCH-profit-margin.md, TASKS-profit-margin.md)
- [x] Triage proposed and developer confirmed
- [x] 7 checks dispatched: task-completion, code-quality, security, error-handling, database-patterns, migration, requirement-coverage
- [x] Results collected and deduplicated
- [x] Report compiled
- [x] Verdict determined
- [x] Report saved to specs/reviews/

## Verdict: ❌ FAIL

The core mechanism is well-built: the grandfather flag is structurally un-flippable (insert-only write, verified independently by 3 checks), the Postgres `DEFAULT TRUE` backfill semantics are correct, margin math is validated and tested, and the single-request booking flow correctly collapses R7/R8 into "read once, never re-derive." Auth and injection surfaces are clean. But two issues are real must-fix items: a pre-existing public endpoint (`/api/v1/discounts`) still leaks the raw, un-margin-adjusted discount — a genuine R6 gap that both the REQ and ARCH missed — and a new DB call in the booking POST path sits outside the existing degrade-gracefully error handling, so a transient DB hiccup can 500 an entire booking instead of falling back to ₱0 like every sibling lookup in that function does.

### Finding Counts

| Category | 🔴 | 🟠 | 🟡 | 💭 | ⚠️ |
|----------|-----|-----|-----|-----|-----|
| task-completion | 0 | 1 | 0 | 1 | 0 |
| code-quality | 0 | 0 | 2 | 2 | 0 |
| security | 0 | 0 | 0 | 2 | 0 |
| error-handling | 0 | 1 | 0 | 0 | 2 |
| database-patterns | 0 | 0 | 0 | 0 | 0 |
| migration | 0 | 0 | 0 | 3 | 0 |
| requirement-coverage | 0 | 0 | 2 | 2 | 0 |
| **Total** | **0** | **2** | **4** | **10** | **2** |

*(Findings merged where two checks caught the same issue — see notes inline.)*

---

## task-completion

**REQs:** 7/8 fully verified, 1 gap (R6).

| REQ | Status | Evidence |
|---|---|---|
| R1 | ✅ | `templates/admin_prices.html:96-107` single global field; `main.py:1541-1544` passes one `margin_pct` |
| R2 | ✅ | `margin_store.py:38-47`; `tests/test_margin_store.py:54-55` |
| R3 | ✅ | `margin_store.py:79-88` validation; `tests/test_margin_store.py:84-98` |
| R4 | ✅ | insert branches write `margin_exempt=FALSE`; `main.py:846-855`; `tests/test_discount_store.py`, `tests/test_book_margin.py` |
| R5 | ✅ | `ON CONFLICT DO UPDATE` never assigns `margin_exempt`; `tests/test_discount_store.py::test_repeated_edits_never_flip_margin_exempt` |
| R6 | ❌ | see Finding #1 below |
| R7 | ✅ | `vouchers.margin_pct_at_booking` written once at POST `/book`; `tests/test_book_margin.py::test_post_book_stamps_margin_pct_at_booking_regardless_of_exempt_status` |
| R8 | ✅ | margin read once per request, never re-derived (though see requirement-coverage's note on test strength) |

Full suite run live (Postgres in Docker): **470 passed**. Change Footprint followed for all 5 tasks; no Scope Boundary violations (`generate_voucher.py`/`report_pdf.py` untouched, no per-station margin inputs, no `margin_history` table added).

### Findings

| # | Severity | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| 1 | 🟠 High (**✅ Fixed**) | `main.py` | 1852-1856 | `/api/v1/discounts` is a public, unauthenticated GET that calls `discount_store.get_all()` directly — the raw, pre-margin, non-grandfather-aware value. Confirmed by the pre-existing test `test_api_v1_pricing.py::test_discounts_defaults_to_biodiesel` (asserts the raw value comes back unchanged). Neither REQ, ARCH, nor T1-T5 mention this route; ARCH's "Contract changes: none externally" reasoning only covered `/api/v1/prices`. | Fixed in commit `0ba3890` — routed through `_margin_adjusted_discounts()` (main.py:846), same as `/book`. 2 new tests added (`test_discounts_returns_post_margin_value_for_non_exempt_station`, `test_discounts_returns_raw_value_for_exempt_station`); full suite 472 passed. |

| 2 | 💭 Low | `main.py` | 1668-1680 | ARCH decision A8 commits to recording margin changes via `audit_log.py`. `admin_margin_update` never calls `append_audit`. *(Merged with code-quality Finding #3 below — same issue, code-quality rates it Medium given A8 is a written commitment with no history-table backstop.)* | See merged recommendation under code-quality #3. |

---

## code-quality

### Findings

| # | Severity | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| 1 | 🟡 Medium | `discount_store.py` | 85-161 | `get_all`, `get_all_with_updated_at`, and the new `get_all_with_exempt` (plus `get`/`get_with_exempt`) are near-identical copy-pasted SQL + row-mapping, differing only in the projected column. Now at 3 "all" variants and 2 "single" variants. | Consolidate into one parameterized internal fetch (e.g. `_fetch_all(fuel_type, extra_cols=())`) that the public methods thin-wrap. |
| 2 | 🟡 Medium | `main.py` | 846-857, 956-968 | `_margin_adjusted_discounts()` calls `margin_store.get()` internally; `book()`'s GET path invokes it once for `"Biodiesel"` plus once per `FUEL_TYPES` entry — 4 DB round-trips per `/book` page view for one global scalar. | Read `margin_store.get()` once at the top of `book()` and thread it through as a parameter instead of re-fetching per fuel type. |
| 3 | 🟡 Medium (merged with task-completion #2) | `main.py` | 1678-1690 | ARCH A8 explicitly picked `audit_log.py` *because* it would capture actor/old/new/timestamp for margin changes — precisely because no `margin_history` table exists as a backstop (unlike `discount_history` for discounts). `admin_margin_update` never calls `append_audit`, so margin changes currently leave **zero** audit trail anywhere in the system. | Add `append_audit("margin_update", None, old_value=..., new_value=new_margin)` mirroring `ops_set_status`'s existing calls. |
| 4 | 💭 Low | `main.py` | 1682-1685 | Success response echoes `float(new_margin)` (the re-parsed raw payload) rather than the value `margin_store.set()` actually persisted. Harmless today since `_validate()` only bounds-checks, but a latent trap if validation ever transforms the value. | Have `MarginStore.set()` return the stored value (or call `.get()` after `.set()`) and echo that, mirroring `admin_prices_update`'s pattern. |

**Observations (non-blocking):** unused `id="margin-save-feedback"` attribute in the template (JS selects via `.save-feedback` class, matching the per-row pattern); ARCH's documented `apply(raw, exempt)` signature in one table doesn't match the actual `apply(raw, margin_pct, exempt)` — doc wording gap only.

**Positive notes:** `margin_store.py` and `discount_store.py` correctly have zero dependency on each other (Module Boundaries honored both directions); `admin_margin_update` is a fully separate `@app.route`/`def` block, correctly avoiding the documented route-decorator gotcha; schema changes exactly replicate the existing dual `CREATE TABLE` + `ALTER TABLE ADD COLUMN IF NOT EXISTS` idempotency convention; template accessibility (label association, tab order) is correct and reuses existing conventions rather than inventing new ones.

---

## security

**Result:** ✅ No blocking findings.

Auth on the new route (`require_admin`, identical pattern to sibling routes), all SQL parameterized throughout `margin_store.py`/`discount_store.py`, and — the specific R6-adjacent check requested — confirmed no raw discount or margin percentage reaches any customer-facing output (`/book` page, `window.__STATION_TABLE__`, booking-success page). The `/api/v1/discounts` gap above is a task-completion/contract finding, not flagged again here since it's outside this diff's authored code.

**Observations (non-blocking, pre-existing conventions, not introduced by this diff):** `admin_margin_update`'s response echoes the raw input rather than the validated value (same as code-quality #4); no CSRF token on the new endpoint, matching every other admin write route in this codebase.

---

## error-handling

### Findings

| # | Severity | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| 1 | 🟠 High | `main.py` | 1201 | `margin_pct_at_booking = margin_store.get()` in the `/book` POST handler sits **outside** the `try/except` block wrapping the rest of the discount-snapshot logic. Every sibling lookup in this function (price snapshot, discount snapshot) degrades to a safe default and logs on failure, per the function's own stated design ("absence here just means ₱0 discount, never blocks the booking"). This one call breaks that invariant: a transient DB error (pool exhaustion, dropped connection) propagates unhandled and 500s the entire booking request — untested by `test_book_margin.py`. | Move the call inside the adjacent `try` block (default to `0.0` on failure, same as `dpl_snapshot`), or give it its own `try/except Exception as _e: print("⚠️ margin lookup error:", _e); margin_pct_at_booking = 0.0`. |

**Manual checks (⚠️):**
- `ops_set_status`'s Approve-flow fallback (`main.py` ~593-600) uses a bare `except Exception: pass` with no logging around the new `get_with_exempt`/`margin_store.get()` calls — traced and confirmed safe (fails closed, `dpl` stays `0.0`, cannot leak raw discount), but now guards financial logic with zero observability. Pre-existing pattern in that exact block, not introduced by this diff — worth a log line but not a blocking finding.
- `admin_margin_update`'s generic `except Exception` also logs nothing before returning `"server_error"` — mirrors the pre-existing `admin_prices_update` handler exactly, flagged only as an observation given the route now guards a revenue-affecting setting.

---

## database-patterns

**Result:** ✅ No findings.** Connection/cursor lifecycle, transaction boundaries, and the insert-only `margin_exempt` write path (verified independently here, in code-quality, and in migration — three checks agree this is correctly implemented) are all sound. No missing indexes; `margin_settings` is a 1-row table, `discounts.margin_exempt` rides the existing composite-PK lookup.

---

## migration

**Result:** No blocking findings. Explicitly verified: `ALTER TABLE ... ADD COLUMN ... DEFAULT TRUE` is a metadata-only operation in Postgres 11+ — existing `discounts` rows read `margin_exempt = TRUE` immediately and permanently on next deploy, no table rewrite, no manual backfill needed. Also explicitly verified: no breaking change to CSV/Postgres voucher persistence, no external supplier-CSV contract change (that export builds from a fully hardcoded column dict, untouched by this diff), no existing JSON/template-context shape changed.

### Findings

| # | Severity | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| 1 | 💭 Low | `tests/test_discount_store.py` | 228-236 | The backfill test inserts a row omitting `margin_exempt` on a schema that *already has* the column — equivalent to, but not a literal replay of, the real production event (ALTER on a table with pre-existing rows). | Optional: add one test that builds the table without the column, populates it, then runs the literal ALTER and asserts `TRUE` comes back. Given this is the entire mechanism R5 depends on in prod, cheap insurance. |
| 2 | 💭 Low | `margin_store.py` | 44-58 | `set()` accepts a `reason` parameter that is never persisted anywhere (no `margin_history` table, unlike `discount_history`). Same theme as the A8 audit-log gap above. | Not a regression (margin never had history before this feature) — but same fix (`append_audit`) closes this too. |
| 3 | ⚠️ Manual | `main.py` | 1675 | No CSRF token on the new JSON POST endpoint. | Confirm whether this app has any CSRF convention for admin POSTs at all; if none exists elsewhere (confirmed: it doesn't), this is consistent, not a new gap. |

---

## requirement-coverage

**Criteria:** 5/8 fully covered, 3 weak, 0 uncovered (static analysis only, not executed).

### Findings

| # | Severity | File | Line | Issue | Recommendation |
|---|---|---|---|---|---|
| 1 | 🟡 Medium | `tests/test_book_margin.py` | 175-187 | `test_margin_changed_after_booking_does_not_retroactively_change_stored_snapshot` is tautological: it re-reads the same in-memory stub dict after an unrelated monkeypatch, which cannot fail even if the real "never re-derive" guarantee broke elsewhere. The actual guarantee lives in `main.py:585` (`ops_set_status` reading `snap_disc` directly when non-zero) — that path is untested for the *non-zero-snapshot* case with a since-changed margin; only the zero-snapshot fallback is covered (`test_admin_approve_margin.py`). This is exactly the REQ edge-case row "Historical booking queried after global margin has since changed." | Add a test that books with margin A, changes the global margin to B, then drives `ops_set_status`'s normal (non-zero-snapshot) path and asserts the result still reflects A. |
| 2 | 🟡 Medium | `models.py` / repo layer | — | `margin_pct_at_booking` added to `VOUCHER_COLUMNS`, but no test exercises the real Postgres repo round-trip for it — `test_book_margin.py`'s `RepoStub` just echoes the dict back, bypassing `db/postgres_repo.py`'s actual column mapping. A misspelled/dropped column in the real INSERT list would not be caught. | Add a repo-layer test (SQLite-monkeypatch or live-Postgres, matching `test_postgres_repo.py`'s existing pattern) confirming the column round-trips through `create_unverified_booking`/`get_voucher`. |
| 3 | 💭 Low | `tests/test_admin_pricing_endpoints.py` | 399 | `test_admin_prices_context_includes_current_margin` asserts the template *context variable*, never the rendered HTML — a regression that dropped the field from the template entirely would not fail this test. | Cheap addition: also assert `b'id="margin-input"'` (or the formatted value) appears in `r.data`. |
| 4 | 💭 Low | `generate_voucher.py`, `report_pdf.py` | — | REQ R6 explicitly names "receipt" and "Supplier PDF-adjacent customer views" as surfaces margin must never leak into. These files are untouched (per ARCH, justified as pure consumers of already-adjusted fields) but have zero regression test locking that assumption in place. | Consider a lightweight test confirming the generated receipt/PDF reads the frozen snapshot rather than recomputing live. |

---

## Manual Checks Required

- [ ] Confirm whether `/api/v1/discounts`'s consumers are customer-facing (R6 gap) or an internal/supplier surface intentionally left raw (like `report_pdf.py`, per A7) — this determines whether Finding task-completion#1 is a real bug or a documentation gap.
- [ ] Confirm this app has no CSRF convention for admin POSTs anywhere (traced: it doesn't) — the new `/admin/margin/update` route is consistent with that, not a new gap, but worth a project-wide sign-off if that ever changes.

## Prioritized Action Items

### Must Fix (🔴 Critical / 🟠 High)
- ~~**task-completion #1** — `/api/v1/discounts` leaks raw discount~~ — ✅ **Fixed in commit `0ba3890`**.
- **error-handling #1** — `margin_store.get()` in `/book` POST must degrade gracefully like its sibling lookups, not 500 the booking on a DB blip. **Still open.**

### Should Address (🟡 Medium)
- Wire `append_audit` into `admin_margin_update` (closes both the A8 gap and the unused `reason` parameter).
- Read `margin_store.get()` once per `/book` request instead of 4 times.
- Consolidate `discount_store.py`'s 3 near-duplicate "all" query variants.
- Replace the tautological R7/R8 retroactivity test with one that actually drives the non-zero-snapshot Approve-flow path after a margin change.
- Add a real repo-layer round-trip test for `margin_pct_at_booking`.

### Nice to Have (💭 Low)
- Echo `margin_store.set()`'s persisted value instead of the raw request payload in `admin_margin_update`'s response.
- Assert rendered HTML (not just template context) in the admin_prices margin test.
- Literal ALTER-on-populated-table backfill test.
- Drop the unused `id="margin-save-feedback"` attribute.
- Fix ARCH's `apply()` signature doc mismatch.
- Lightweight regression test for `generate_voucher.py`/`report_pdf.py` reading frozen (not live) discount values.

---
*Generated by Review — 2026-09-03*
