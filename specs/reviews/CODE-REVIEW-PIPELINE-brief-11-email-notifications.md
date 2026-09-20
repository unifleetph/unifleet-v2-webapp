# Review Report

## Metadata

| Field | Value |
|-------|-------|
| **Review Mode** | Pipeline — ARCH-brief-11-email-notifications |
| **Target** | specs/architecture/ARCH-brief-11-email-notifications.md (commits `6703e66..0e5f346`, branch `dev`) |
| **Date** | 2026-09-08 |
| **Tech Stack** | Python 3.11, Flask (single-file `main.py`), Jinja2, psycopg3 + psycopg_pool, PostgreSQL 16, Poetry, pytest, Docker/Railway |
| **Checks Run** | task-completion, requirement-coverage, code-quality, security, error-handling, database-patterns, config-dependencies, migration, test-coverage |
| **Checks Skipped** | typescript-strictness, react-patterns, express-patterns, runtime-behavior, async-patterns (JS/TS-specific — no such code); performance (no complex algorithms; the one hot path is an indexed bulk query); documentation (AGENTS.md and `.env.example` updated, no public API surface); accessibility (admin-only internal pages) |
| **Files Changed** | 34 |
| **Lines Changed** | +6,387 / -9 |

## Review Process

- [x] Preflight checks passed (git repo confirmed, default branch `main`, ARCH + TASKS + REQ all present, modern separate-TASKS-file path)
- [x] Diff gathered (34 files, 6,396 lines; ~1,468 production Python, 3,440 tests, 1,126 TASKS doc)
- [x] Tech stack detected: Python 3.11 / Flask / Jinja2 / psycopg3 / PostgreSQL / Poetry / pytest / Docker / Railway
- [x] Context read (REQ, ARCH, TASKS, CLAUDE.md, AGENTS.md)
- [x] Triage proposed and developer confirmed (9 run, 8 skipped)
- [x] 9 checks dispatched in parallel
- [x] Results collected and deduplicated (30 distinct issues from ~90 raw findings)
- [x] Six high-impact findings independently verified by execution against the running stack
- [x] Report compiled
- [x] Verdict determined
- [x] Report saved to specs/reviews/

## Verdict: ❌ FAIL

The engineering underneath this change is sound — the outbox design is right, the module boundaries hold, the copy is byte-faithful to the client brief, secret redaction is careful and correctly ordered, and the process record (two scope amendments, one deliberately-unrun checklist item, one honestly-narrowed REQ decision) is unusually disciplined. 660 tests pass.

It fails anyway, because **three of the feature's headline promises do not work in production**, and the test suite says otherwise. The legacy-customer recovery flow (R6 → R9 → R8) requeues an email with no recipient. The nightly report is scheduled through a Railway config key that does not exist. And a single unexpected exception in the drain loop stalls the entire outbox permanently and invisibly. Each was verified by execution, not inference.

The common thread is worth naming: every one of these gaps sits in a seam *between* tested units. Each unit is tested; the joins between them are not, and the tests that appear to cover the joins pass arguments no production caller ever passes.

### Finding Counts

| Category | 🔴 | 🟠 | 🟡 | 💭 | ⚠️ |
|----------|-----|-----|-----|-----|-----|
| task-completion | 0 | 3 | 3 | 3 | 1 |
| requirement-coverage | 3 | 4 | 0 | 3 | 1 |
| code-quality | 0 | 1 | 12 | 6 | 0 |
| security | 0 | 1 | 2 | 3 | 0 |
| error-handling | 1 | 5 | 5 | 0 | 0 |
| database-patterns | 0 | 2 | 7 | 0 | 0 |
| config-dependencies | 0 | 2 | 5 | 2 | 2 |
| migration | 0 | 3 | 5 | 1 | 1 |
| test-coverage | 0 | 2 | 9 | 4 | 0 |
| **Total (deduplicated)** | **2** | **10** | **12** | **6** | **3** |

---

## The Blocking Set

These six are the reason for the verdict. Each was verified by running it.

### B1 🔴 The legacy-customer recovery flow does not work — R6 → R9 → R8

**`templates/admin.html:435` · `main.py:477` · `notifications.py:381-387`**

The Resend form posts no `recipient`, so the route always calls `requeue(id, recipient=None)`, and that branch leaves the row's recipient untouched. For a `skipped` row it is NULL. Nothing re-reads the customer record.

Verified end to end against the dev database — legacy customer with no email → booking creates a `skipped` row → admin fills in the address (T8) → admin clicks Resend (T9):

```
after admin adds email and clicks Resend -> status='queued' recipient=None
```

The worker will then call `mailer.send(to=None)`. This is the entire justification for T8 existing, and R6's "skip and flag" is only useful because this recovery exists. `test_requeue_can_repoint_a_skipped_row_at_a_new_address` passes `recipient=` explicitly — an API path no production caller uses.

**Fix:** re-resolve from `repo.get_customer(row.account_code)` in the resend route when the row has no recipient, and add a route-level test that asserts the requeued row carries the new address.

### B2 🔴 One bad row stalls the outbox permanently and invisibly

**`notifications.py:425-443` · `notifications.py:350-358`**

The per-row send/record work sits inside a single batch-wide `try`. Any exception escaping it leaves every claimed row in `sending` with `attempts` **not** incremented. `_reclaim_stale` returns them to `queued` without advancing `attempts`, so `MAX_ATTEMPTS` is never reached and the row never becomes `failed`. Neither admin read path selects `sending` or `queued`, so nothing appears anywhere. The row also aborts the rest of its batch on every cycle, forever, with one stderr line every five minutes.

This is reachable: see B3.

**Fix:** give each row its own `try`; on an unexpected exception record a synthetic `SendResult(ok=False, status_code=0, error=repr(exc))` so the ladder advances and the row goes terminal-visible. Have `_reclaim_stale` increment `attempts`.

### B3 🟠 `mailer.send` can raise, despite documenting that it never does

**`mailer.py:74-97`**

Payload construction sits **outside** the `try`. `filename, content, mimetype = attachment` raises `ValueError` on a malformed tuple; `base64.b64encode(content)` raises `TypeError` on a non-bytes body. Both are exactly the programming-error class that B2 then converts into a permanent invisible stall.

**Fix:** move payload construction inside the `try`.

### B4 🟠 The nightly report is scheduled through a Railway key that does not exist

**`rw.txt:8-15`**

I added a `[[cron]]` array table. Railway's config-as-code has no such key — scheduling is a per-service `cronSchedule`, and a cron service runs its own service's start command. The repo's own precedent confirms it: `docs/runbook.md:55` shows the existing backup job is a **dashboard-configured Cron Schedule service** with its own `Dockerfile.backup`, and `rw.txt` carried no cron entry before this change. Railway ignores unknown keys silently, so the likely outcome is that the report never runs and nothing reports an error.

T12's checklist recorded "cron entry is correct — `test_railway_toml.py` passes". That test asserts only build/start/nix. The verification was vacuous and I reported it as passed.

**Fix:** provision a cron service the way `backup` is provisioned, document it in `docs/runbook.md`, and either delete the `[[cron]]` block or reduce it to a comment saying where the schedule actually lives. Then assert whatever `rw.txt` ends up carrying.

### B5 🟠 Even if the cron ran, the PDF would not be attached

**`scripts/send_daily_report.py:161` · `notifications.py:299-306`**

The cron process writes the PDF to the Railway Volume; the **web** process's worker reads it back at send time. A Railway Volume mounts to exactly one service. A separate cron service cannot see the web service's volume, so `_resolve_attachment` takes its `OSError` branch every night: the report mails body-only, saying "Attached is the UniFleet supplier sheet", marked `sent` — and per B6, flagged nowhere.

This is a direct consequence of the option-A design I recommended during T13. I did not check Railway's volume semantics before recommending it.

**Fix:** store the PDF bytes on the notification row, or rebuild at send time, or run the job inside the web service that owns the volume.

### B6 🟠 Stored XSS in the recipients page

**`templates/admin_recipients.html:109`**

`onsubmit="return confirm('Remove {{ r.email }} …')"` interpolates a stored value into a JS string inside an HTML attribute. Jinja escapes `'` to `&#39;`, but the HTML parser decodes that back to a real quote before the JS parser runs. `_EMAIL_RE` permits `'` — it only excludes `@` and whitespace.

Verified: `x'-alert(1)-'@evil.co` passes both copies of the validator, and `markupsafe.escape` → HTML-decode reproduces the breakout exactly.

Severity is raised by the absence of CSRF protection on `POST /admin/recipients` (F19): an off-site page can plant the payload in a logged-in admin's list without the admin typing anything, turning admin self-XSS into cross-user escalation with full admin-session rights.

**Fix:** drop the interpolation — the row already shows which address it is.

---

## High-Severity Findings

### F7 🟠 Failed registration emails are invisible in every admin surface

`main.py:277-291` joins flags by `voucher_id` only. `account_code` notifications carry `voucher_id = NULL`, so a failed account-code send never appears. `list_flagged()` — implemented, tested, and specified in ARCH for exactly this — **has no caller anywhere in the app**. R7's acceptance criterion ("registration … produces a visible failure flag in admin") is unmet, and R8's resend is unreachable for those rows. This also leaves N5 unsatisfiable through the UI: `flags_by_voucher` returns neither recipient nor any timestamp.

### F8 🟠 `attachment_missing` never reaches an admin

`_resolve_attachment` records the note, `_mark_sent` writes it to `last_error` on a **`sent`** row, and both read paths filter `status IN ('failed','skipped')`. So a confirmation email that shipped without its voucher — the exact case ARCH A6 was written for, and REQ edge-case E7 — produces a flag nobody can see. Confirmed by inspection of both queries.

### F9 🟠 The foreign key blocks the flag it is supposed to raise

`notifications.account_code REFERENCES customers(account_code)`. The `enqueue_skipped` branch documents itself as covering "the account code matches no customer" — but that insert violates the FK, raises, and is swallowed. Verified live:

```
ERROR: insert or update on table "notifications" violates foreign key constraint
       "notifications_account_code_fkey"  Key (account_code)=(ZZZZ) is not present in table "customers".
```

Same for `/register`'s `pg_error` sentinel path (customer written to CSV only) and any CSV-backed deployment. **I hit this exact error during T3 and fixed my test by inserting a customer row** — I read it as test setup rather than the production signal it was.

### F10 🟠 The 2-second pool timeout leaks process-wide

`get_pool(dsn=dsn, timeout=2)` sets the **pool's** default checkout timeout, and `db/pool.py` is first-caller-wins. Since `enqueue` runs on `/register` and `/book`, it is often the first DB touch in the process. Verified:

```
pool default checkout timeout after an enqueue constructed it: 2
what price_store/margin_store/audit_log now inherit: 2
same object: True
```

All six shared-pool consumers silently drop from 30s to 2s, nondeterministically per process, depending on which request arrives first after a restart. **Fix:** drop `timeout=` from the `get_pool` calls — `pool.connection(timeout=…)` on the next line already bounds this call correctly.

### F11 🟠 `NOTIFICATIONS_ENABLED` does not gate enqueues, so the rollout replays a backlog

ARCH A12 says the flag gates "the worker **and all enqueues**"; `is_enabled()` is consulted only by `start_worker`. During rollout step 1 (`=false`) every registration, booking and approval still writes a `queued` row. At step 4 the worker drains the accumulated backlog at 20 rows/5s — customers receive "Booking Request Received" for bookings long since confirmed, and "Booking Confirmed" for vouchers already redeemed. ARCH's claim that pre-launch bookings are "never retro-notified" is false as written.

### F12 🟠 The cron's documented exit code 1 is unreachable — a dead database produces a green run

`active_emails()` swallows every exception and returns `[]`, so a Postgres outage takes the "No active report recipients; nothing to send." branch and **exits 0**. The `try/except` at `scripts/send_daily_report.py:121-125` is dead code. A week of missed reports looks like a week of green cron runs. The test that "covers" this monkeypatches `active_emails` to raise — which the real function never does.

### F13 🟠 Two tests pass in exactly the state they exist to catch

- `tests/test_pool.py:64` asserts `elapsed < 6` while `DEFAULT_WAIT_SECONDS` is 5.0 — it passes unchanged if the env var were ignored entirely.
- `tests/test_notifications.py:944`, the guard for the flake fix, asserts `start_worker() is False`. That returns False both when disabled **and** when a worker is already running — so if conftest's kill switch regressed, a live drainer would start and this test would still pass. Its trailing `_reset_worker_for_tests()` then clears the handle while the rogue thread keeps polling, permitting a second one.

This is the third and fourth instance of the decorative-test pattern found in this change.

### F14 🟠 Cross-file test fixture leak

`tests/test_notifications.py`'s `customer`/`voucher` fixtures insert into the session-scoped database without teardown; `tests/test_customer_repo.py`'s `pg_repo` teardown deletes `HARR` and truncates `notifications` — one file cleaning up another's rows. It works only because alphabetical collection puts `test_customer_repo` first. AGENTS.md advertises the suite as parallel-safe; it is not.

---

## Medium-Severity Findings

| # | Area | Finding |
|---|------|---------|
| F15 | `notifications.py:266,328` | **Retry ladder off-by-one.** `MAX_ATTEMPTS = len(RETRY_BACKOFF_SECONDS)` with index `attempts-1` makes `600` unreachable. Verified: reachable delays are 1s/5s/30s/2m. Real window is **~2.5 minutes, not the ~15** ARCH and the code comment both claim. My test asserted the list's contents, not the delays applied. |
| F16 | `notifications.py:419-440` | `drain_once` holds one pooled connection across the whole send loop — up to 20 × 10s ≈ 200s of one of 8 connections, while request-path enqueues wait 2s. Move the sends outside the connection block. |
| F17 | `main.py:730,735` | `_voucher_fingerprint(row)` computed twice, each re-reading and re-hashing the PNG. If the PNG changed between calls, the `dedupe_key` and the stored `voucher_fingerprint` disagree — and that column is what R10 reads. |
| F18 | `main.py:470,475,482,497` | **Open redirect** — four new routes use raw `request.referrer`. The project already has `_safe_next()` at `main.py:1035`, and `admin_login` uses it. I did not look for it. |
| F19 | `main.py:412,444,459,486` | No CSRF token on four new state-changing admin POSTs (project-wide gap, not introduced here) — but these widen it meaningfully: a cross-site request can repoint a customer's email and then resend their voucher PNG to it. Also the enabler for B6. |
| F20 | `main.py:923,1007` | `/register` is unauthenticated, unthrottled, and now sends mail per POST; `dedupe_account_code` keys on the *generated* code, so repeat submissions re-send. Email-bombing vector that risks the sending domain. No `MAX_CONTENT_LENGTH` either. |
| F21 | `scripts/send_daily_report.py:145` | Cron defaults to `PERSISTENCE_BACKEND=csv`. A separate Railway service does not inherit the web service's variables, so the nightly sheet would be built from stale or absent CSVs — silently wrong rather than failing. |
| F22 | `scripts/send_daily_report.py:159` · `data_paths.py:141` | One PDF per day onto the production volume, never pruned. Plausibly 0.4–1.8 GB/year on a volume that also holds `customers.csv`, voucher PNGs and CSV persistence. A full volume takes down bookings, not just email. |
| F23 | `db/postgres_repo.py:526` | `WHERE UPPER(account_code) = %s` defeats the PK index and is inconsistent with its siblings (`get_customer`, `customer_exists` both use `account_code = %s`). The parameter is already upper-cased. |
| F24 | `db/schema.sql` · `notifications.py:571` | No index supports `list_flagged`'s `WHERE status IN (…) ORDER BY id DESC LIMIT 200`; on a never-pruned table that becomes a backwards PK walk. Add a partial index. |
| F25 | `.env.example:36,41` | `MAIL_FROM` ships a non-empty placeholder — copied verbatim with a real key, `is_configured()` returns true and every send takes a permanent 4xx (unverified domain), so the first wave of customer mail lands in `failed` rather than being held. And `NOTIFICATIONS_ENABLED=true` points the opposite way from ARCH's rollout step 1. |
| F26 | `tests/` | Further weak assertions: the ladder test asserts a constant (`test_notifications.py:453`); the Manila-date test recomputes the implementation's own expression and would pass a UTC implementation for 16 hours a day (`test_daily_report.py:218`); `test_booking_success_page_is_unchanged` asserts `"1000" in html`, which the *rejection* re-render also satisfies (`test_book_pg.py:873`); `test_requeue_is_idempotent` asserts a row count that an UPDATE can never change (`test_notifications.py:607`). |

## Low-Severity Findings

| # | Area | Finding |
|---|------|---------|
| F27 | `notifications.py`/`report_recipients.py` | The same 9-line try/pool/cursor/swallow block appears 9 times; the F10 timeout inconsistency crept in through exactly that duplication. |
| F28 | `main.py:923` · `report_recipients.py:30` | The email validator is defined twice with identical regexes; the two can drift. |
| F29 | `report_recipients.py:93` | `set_active` has no route and no template control, yet the page renders an active/inactive state nothing can produce. |
| F30 | `main.py:1007` | The `/register` enqueue is inlined rather than extracted like its two siblings, and is the only call site without an `if notifications is None` guard. |
| F31 | `persistence.py` | `DBRepo` still lacks `update_customer_email`; the route's blanket except turns that into "Could not update the email address. Please try again." forever. Justified in commit `164eeeb`, but not recorded in TASKS. |
| F32 | `specs/` | `REQ-brief-11-email-notifications.md` and `ARCH-brief-11-email-notifications.md` are **untracked**, while the TASKS file that links both by path is committed. Reviewers on a fresh clone get dangling references. |
| F33 | TASKS | T5 and T8 carry deviations recorded only in commit messages, unlike T11 and T13 which have proper `_Scope amendment_` notes. The `admin.html` nav link (commit `97a3212`) belongs to no task. |

## Manual Checks Required

- [ ] **Confirm Railway's cron mechanism** (B4) before any deploy — dashboard Cron Schedule service vs. config key, and whether an unknown top-level table is ignored or rejected.
- [ ] **Confirm Railway Volume semantics** for a separate cron service (B5) — this determines whether the PDF handoff needs redesigning or just re-siting.
- [ ] **Confirm the live `PERSISTENCE_BACKEND` value** (F21) — and reconcile CLAUDE.md's gotcha, which says `db` is "the Postgres path" while `persistence.py:47-52` maps `db` → SQLite.
- [ ] **Run `pip-audit`/`safety`** against the resolved versions (requests 2.34.2, urllib3 2.7.0, idna 3.19, certifi 2026.7.22). The known historical `requests` advisories sit below this floor, but this could not be checked offline.
- [ ] **Verify SPF/DKIM** for the `MAIL_FROM` domain before enabling sending — ARCH's own open question, still open.

## Prioritized Action Items

### Must Fix (🔴 Critical / 🟠 High)
1. **B1** — resend route must re-resolve the recipient; add the end-to-end test.
2. **B2 + B3** — per-row try in `drain_once`; move `mailer.send`'s payload construction inside its try; `_reclaim_stale` must advance `attempts`.
3. **B4 + B5** — settle the real Railway cron mechanism and the PDF handoff together; they are one decision.
4. **B6** — drop the email interpolation from the `onsubmit` handler.
5. **F7 + F8** — give account-code failures and `attachment_missing` an admin surface; wire up `list_flagged`.
6. **F9** — drop the outbox FKs (it is a log, not a relational child) or write `account_code = NULL` when the FK fires.
7. **F10** — stop passing `timeout=` to `get_pool`.
8. **F11** — gate `enqueue` on `is_enabled()`, or amend A12 and add a backlog-clearing step to the runbook.
9. **F12** — let the cron distinguish "unreachable" from "empty".
10. **F13 + F14** — fix the two decorative tests and the cross-file fixture leak.

### Should Address (🟡 Medium)
F15 (ladder off-by-one — fix the code or the comment, they disagree), F16, F17, F18, F19, F20, F21, F22, F23, F24, F25, F26.

### Nice to Have (💭 Low)
F27–F33. F32 (untracked specs) is worth doing before anyone else clones this branch.

## What Went Right

Worth recording, because the failing verdict is not the whole picture:

- **Module boundaries hold.** `notifications.py` imports only `db.pool`, `mailer`, `data_paths` and stdlib — no `main`, no Flask — exactly as ARCH specified. Verified programmatically, as was decorator adjacency for all five new routes (the CLAUDE.md gotcha).
- **Secret redaction is careful and correctly ordered** — redacting before the 500-char truncation so a key straddling the cut cannot survive as a fragment. Asserted on both the exception path and the provider-echo path.
- **Email copy is byte-faithful** to `docs/Brief-11_email.md`, including the curly apostrophe and en dash, asserted literally rather than by substring.
- **Auth gating is correct** on all five new routes — `require_admin` first, before any side effect.
- **SQL is fully parameterised**; the one f-string in SQL interpolates a module-controlled literal.
- **The schema is genuinely safe to deploy**: no destructive DDL, forward-only, idempotent, empty-table indexes, rollback leaves two inert tables.
- **`FOR UPDATE SKIP LOCKED` is correct**, and the at-least-once boundary is the right trade-off, explicitly chosen and documented.
- **The process record is honest** — two scope amendments recorded before implementing, one checklist item recorded as NOT RUN with its rationale and compensating control, and A6's narrowing of REQ decision #9 disclosed in four places with no overclaiming.

---
*Generated by Review — 2026-09-08*
