# Review Report

## Metadata

| Field | Value |
|-------|-------|
| **Review Mode** | Pipeline: ARCH-brief-7-book-register-fixes |
| **Target** | specs/architecture/ARCH-brief-7-book-register-fixes.md |
| **Date** | 2026-08-04 |
| **Tech Stack** | Flask (Python 3.11), Jinja2 templates, plain CSS, no build pipeline / no JS framework |
| **Checks Run** | task-completion, code-quality, accessibility, migration |
| **Checks Skipped** | test-coverage (T1 followed test-after discipline with a regression guard confirmed against baseline before the fix), security/performance/error-handling/documentation/config-dependencies/database-patterns/async-patterns/react-patterns/express-patterns/typescript-strictness/runtime-behavior (no auth/DB changes, no new dependencies, no async/DB/JS-framework code in diff) |
| **Files Changed** | 6 (`main.py`, `templates/booking_success.html`, `templates/book.html`, `templates/register.html`, `templates/terms.html`, `tests/test_book_and_booking_success_copy.py`) |
| **Lines Changed** | Commits `7a0a0e4` (T1) + `9d7af8d` (T2) |

## Review Process

- [x] Preflight checks passed
- [x] Diff gathered (6 files across 2 commits)
- [x] Tech stack detected: Flask / Jinja2 / plain CSS
- [x] Context read (CLAUDE.md, AGENTS.md, linked REQ-brief-7-book-register-fixes.md)
- [x] Triage proposed and developer confirmed
- [x] 4 checks dispatched: task-completion, code-quality, accessibility, migration
- [x] Results collected and deduplicated
- [x] Report compiled
- [x] Verdict determined
- [x] Report saved to specs/reviews/

## Verdict: ✅ PASS (post-fix)

**Update 2026-08-04:** All 4 addressable findings fixed. Both Status lines corrected (T1: "3 scenarios" not "4"; T2: describes the full 4-assertion extent of the test-file deviation, not just "one assertion"). The Facebook link now carries a visually-hidden "(opens in new tab)" span for WCAG 3.2.5 conformance, verified live. The site-wide missing-`<h1>` observation was left as-is per the report's own recommendation — it's a pre-existing pattern across every page, not something scoped to this REQ. Full test suite re-run after fixes: 349 passed, no regressions.

Original findings below are kept for the record.

No must-fix findings across any check. Both tasks are complete, correctly scoped, and every REQ (R1-R10, N1-N2) traces to a real diff and passing evidence — 349/349 tests pass, all 7 T2 evidence screenshots confirm what their checklist claims. The Amount Due fix is a genuine correctness improvement that also closes a pre-existing mismatch between what customers were shown and what was actually persisted/charged (confirmed by the migration check). Remaining items are two Low-severity documentation-accuracy nits in the task Status lines and two Manual/Low accessibility notes that are pre-existing site-wide patterns, not regressions — safe to complete at your discretion or leave as-is.

### Finding Counts

| Category | 🔴 | 🟠 | 🟡 | 💭 | ⚠️ |
|----------|-----|-----|-----|-----|-----|
| task-completion | 0 | 0 | 0 | 2 | 0 |
| code-quality | 0 | 0 | 0 | 0 | 0 |
| accessibility | 0 | 0 | 0 | 1 | 1 |
| migration | 0 | 0 | 0 | 0 | 0 |
| **Total** | **0** | **0** | **0** | **3** | **1** |

## task-completion

### Findings Table

| # | Severity | File | Line | Issue | Recommendation |
|---|----------|------|------|-------|----------------|
| 1 | 💭 Low | `specs/architecture/ARCH-brief-7-book-register-fixes.md` (T1 Status line) | — | Status line says "4 test scenarios" but the Test Plan documents 3 (2 under Amount Due Display + 1 Regression Guard). No coverage gap — cosmetic miscount only. | Correct the wording to "3 scenarios" if the doc is revisited. |
| 2 | 💭 Low | `specs/architecture/ARCH-brief-7-book-register-fixes.md` (T2 Status line), `tests/test_book_and_booking_success_copy.py` | `test_book_pre_registration_prompt_updated` | T2's Status line describes "one assertion updated" in T1's test file, but the actual diff touches 4 assertion lines: the required Tagalog-removal flip, plus a tightened link-text assertion and two new Facebook-link assertions that weren't strictly required to fix the break. The extra changes are on-topic (R7/R10 coverage, same task's own REQs) and additive, not scope creep into unrelated territory — but the Status line undersells what actually changed. | Update the Status line to describe the full extent (4 assertion lines touched, 3 of which are R7/R9/R10 coverage additions beyond the minimum fix) so future readers aren't surprised by the diff size. |

### REQ Traceability

| REQ | Status | Notes |
|-----|--------|-------|
| R1 | ✅ Verified | `terms.html` new page, screenshot confirms all 9 sections |
| R2 | ✅ Verified (via A3) | Resolved by R3's inline link per developer-approved ARCH decision A3 |
| R3 | ✅ Verified | Required checkbox positioned directly above Submit button |
| R4 | ✅ Verified | Button text updated |
| R5 | ✅ Verified | New test asserts post-discount amount (₱1,100.00), not raw total |
| R6 | ✅ Verified | New onboarding line present under heading |
| R7 | ✅ Verified | `>>> ... <<<` link text confirmed in screenshot + diff |
| R8 | ✅ Verified | "How It Works" heading removed, 3 steps kept |
| R9 | ✅ Verified | Tagalog line removed, English kept |
| R10 | ✅ Verified | Facebook link present, correct URL, `target="_blank" rel="noopener"` |
| N1 | ✅ Verified | Full suite: 349/349 passed |
| N2 | ✅ Verified | Mobile screenshots at 375px for all touched pages; `terms.html` ships with viewport meta + `.mobile-page` from day one |

### Change Footprint & Scope Boundaries

✅ Respected — every ARCH footprint row present in the diff exactly as described. `computed_pay_php`'s calculation (main.py:1068-1087) confirmed untouched by T1; the ≤0 rejection guard confirmed still upstream of the fixed line. No markdown dependency added, no server-side Terms validation added, no second Terms link (A3 honored). The one deviation (T2 touching T1's test file) was pre-approved by the developer during implementation and is captured as Finding #2 above for documentation accuracy, not scope violation — the file isn't literally named in T2's Must-NOT-modify list (only `main.py`'s due_amount logic and the `.md` source are), so this is footprint drift into a sibling task's just-landed file, not a hard boundary breach.

### Coverage Checklist
- [x] `specs/requirements/REQ-brief-7-book-register-fixes.md` — R1-R10/N1-N2 traced against implementation
- [x] `specs/architecture/ARCH-brief-7-book-register-fixes.md` — Change Footprint ✅, Scope Boundaries ✅, Decisions A1-A8 all followed
- [x] `main.py`, `templates/booking_success.html`, `templates/book.html`, `templates/register.html`, `templates/terms.html` — reviewed in full
- [x] `tests/test_book_and_booking_success_copy.py` — extent of T2's deviation verified via diff → Finding #2

## code-quality

**Result:** ✅ No findings.

### Coverage Checklist
- [x] `main.py` — `/terms` route placement doesn't trigger the CLAUDE.md-documented decorator-rebinding gotcha; `due_amount = computed_pay_php` has a single assignment, single reader, no scope/shadowing risk
- [x] `templates/booking_success.html` — currency formatting matches `admin.html`'s existing pattern exactly
- [x] `templates/terms.html` — transcription fidelity vs `docs/UnifleetTermsConditions.md` confirmed: all 9 sections present in order, 48-hour redemption clause, no-cash-value clause, and legal carve-out all intact, no dropped/altered clauses
- [x] `templates/book.html` — `.inline-checkbox` CSS duplication is reasonable (matches this project's established per-page local `<style>`-block convention); HTML entities (`&gt;`/`&lt;`/`&amp;`) correctly escaped; Facebook link has `target="_blank" rel="noopener"`
- [x] `templates/register.html` — trivial one-line addition, no issues
- [x] `tests/test_book_and_booking_success_copy.py` — new test scenarios are meaningful and correctly scoped

**Observations (non-blocking):** `terms.html`'s content-sync gap (no live read of the .md source) is already an accepted Open Question in the ARCH doc, not a new finding. Minor comment-drift between `book.html`'s and `register.html`'s near-identical `.inline-checkbox` CSS comments — cosmetic only.

## accessibility

### Findings Table

| # | Severity | File | Line | Issue | WCAG | Recommendation |
|---|----------|------|------|-------|------|----------------|
| 1 | ⚠️ Manual | `templates/book.html` | Facebook link, ~line 137 | New `target="_blank"` link has no visible/announced warning that it opens in a new tab. `rel="noopener"` is correctly present. This is the first `target="_blank"` link in the app, so there's no established project convention either way, and REQ/ARCH explicitly chose new-tab behavior intentionally (avoids losing in-progress booking form state). | 3.2.5 | Low-stakes single link, not a blocker. Consider a visually-hidden "(opens in new tab)" span if full 3.2.5 conformance becomes a priority later. |
| 2 | 💭 Low | `templates/terms.html` | — | Page has no `<h1>` — content headings start at `<h2>`, with the visible title living in a `<div class="page-title">`. Confirmed this is **not a new pattern**: `book.html` and `register.html` both use the identical convention with zero `<h1>` anywhere in the app — `terms.html` correctly mirrors the established (if imperfect) page-shell pattern per ARCH A8. | 1.3.1/2.4.6 | Not specific to this diff — would be a site-wide accessibility pass, not a fix scoped to this REQ. |

**Confirmed non-issues:** checkbox `<label for>`/`id` association correct and mirrors `register.html`'s existing pattern exactly; checkbox's exclusion from the tap-target min-height rule is a byte-for-byte copy of an already-reviewed rule from `register.html`'s prior Brief; no JS added in either commit, so keyboard operability of the checkbox and both new links is fully native; viewport meta on `terms.html` present and correctly omits zoom-blocking attributes; no new hardcoded colors, all existing ones pass WCAG AA.

### Coverage Checklist
- [x] `templates/terms.html` — heading hierarchy ⚠️ → Finding #2 (pre-existing pattern), semantic lists ✅, viewport/zoom ✅, link text ✅, color contrast ✅, no JS ✅
- [x] `templates/book.html` — checkbox label association ✅, link text ✅, keyboard operability ✅, new-tab link ⚠️ → Finding #1, color contrast ✅
- [x] `templates/register.html` — new copy line, no accessibility impact ✅

## migration

**Result:** ✅ No findings. Confirmed `computed_pay_php` (T1's fixed `due_amount` source) is identical to what's already persisted as the voucher's charged amount (`row['requested_amount_php']`, main.py:1096) — the fix closes a pre-existing display/persistence mismatch rather than introducing one. Confirmed no server-side check on the new `agree_terms` field — non-browser/API clients submitting without it are unaffected, so the booking API contract is unchanged. No route collisions, no destructive changes, no env/schema changes in either commit.

### Coverage Checklist
- [x] `main.py` (T1) — `due_amount` traced to persisted value, confirms bugfix not new divergence
- [x] `main.py` (T2) — `/terms` route collision-checked, `agree_terms` confirmed unenforced server-side
- [x] `templates/book.html` — new required checkbox is client-only, no `novalidate` bypass concern
- [x] `templates/register.html`, `templates/terms.html` — purely additive
- [x] `tests/test_book_and_booking_success_copy.py` — assertion update matches an approved, documented copy change, not a silent weakening

## Manual Checks Required

- [ ] Consider a visually-hidden "(opens in new tab)" indicator on the Facebook link for full WCAG 3.2.5 conformance (accessibility Finding #1) — optional, low-stakes
- [ ] Physical/manual smoke test of the full booking flow with the new Terms checkbox on a real mobile device, if not already done outside this session

## Prioritized Action Items

### Must Fix (🔴 Critical / 🟠 High)
None.

### Should Address (🟡 Medium)
None.

### Nice to Have (💭 Low / ⚠️ Manual)
- Correct the T1/T2 Status-line wording to accurately describe test-scenario counts and the full extent of the T2 test-file deviation (task-completion Findings #1, #2)
- Optional: visually-hidden new-tab indicator on the Facebook link (accessibility Finding #1)
- Site-wide (out of scope for this REQ): add `<h1>` elements across all pages using the `page-title` div convention (accessibility Finding #2)

---
*Generated by Review — 2026-08-04*
