# Tasks

> **Date:** 2026-09-07
> **Architecture source:** specs/architecture/ARCH-brief-11-email-notifications.md
> **Requirements source:** specs/requirements/REQ-brief-11-email-notifications.md
> **Phase:** 3 of 5 (Task Generation)

**Dependency spine:** T1 → T2 → T3 → T4 → {T5, T6, T7, T9} · T1 → T10 → {T11, T13} · T8 independent of the mail path · T12 last (verifies everything, including T13's script).

**T14** was added during T3, not by generate-tasks: implementing the outbox surfaced a ~30s stall in `db/pool.py` that contradicts N1. It is independent of the spine and can be done at any point, but before this feature ships.

---

## Task T1: Add `notifications` and `report_recipients` tables to the schema

> **Status:** done
> **Verification:** test-after
> **Effort:** s
> **Priority:** critical
> **Depends on:** None
> **Satisfies REQs:** R7, R8, R14, N3, N5
> **Footprint slice:** Modified: `db/schema.sql` (two new tables + indexes)
> **High-risk areas touched:** Postgres schema (L), Railway deployment (M — a malformed statement breaks the Dockerfile CMD chain and the app will not boot)

### Description

Every other task in this plan reads or writes one of two new Postgres tables, so the schema lands first. `notifications` is the outbox — simultaneously the send queue, the delivery-attempt history, and the source of the admin flag list. `report_recipients` is the admin-managed internal distribution list for the nightly supplier PDF. Both are appended to `db/schema.sql`, which `db/apply.py` applies on every Railway container start and in `make test-db`.

### Test Plan

#### Test File(s)
- `tests/test_schema.py` (extend the existing file)

#### Test Scenarios

##### Table Creation

- **notifications table exists after apply** — GIVEN a fresh database WHEN `db/apply.py db/schema.sql` runs THEN a `notifications` table exists with the columns named in ARCH's Data Models section _(verifies A3)_
- **report_recipients table exists after apply** — GIVEN a fresh database WHEN the schema is applied THEN a `report_recipients` table exists with `email`, `label`, `is_active`, timestamps _(verifies R14)_
- **schema is idempotent** — GIVEN a database that already has both tables WHEN `db/apply.py` runs a second time THEN it exits 0 and neither table is altered or dropped _(guards ARCH backward-regression risk for `db/apply.py`)_

##### Constraints and Indexes

- **dedupe_key is unique** — GIVEN a `notifications` row with `dedupe_key = 'acct:HARR'` WHEN a second row with the same key is inserted THEN Postgres raises a unique-violation _(verifies A7, N3)_
- **worker poll index exists** — GIVEN the applied schema WHEN `pg_indexes` is queried THEN an index covering `(status, next_attempt_at)` is present _(verifies the performance claim in ARCH Cross-Cutting Concerns)_
- **foreign keys are nullable** — GIVEN the applied schema WHEN a `notifications` row is inserted with NULL `account_code` and NULL `voucher_id` THEN the insert succeeds _(daily_report rows carry neither)_
- **status defaults to queued** — GIVEN an insert that omits `status` and `attempts` WHEN the row is read back THEN `status = 'queued'` and `attempts = 0`

##### Regression Guard

- **existing tables still assert** — GIVEN the extended schema WHEN the pre-existing `test_schema.py` assertions run THEN every previously asserted table and column is still present _(guards ARCH backward-regression risk for `db/schema.sql` and `tests/test_schema.py`)_

### Implementation Notes

- **Module(s):** `db/schema.sql` only.
- **Pattern reference:** the `margin_settings` block at `db/schema.sql:287` for a small new table; the `vouchers` block at line 62 for index declaration style.
- **Key decisions:** A3 (notification state lives in Postgres, accessed directly, not through the repo abstraction) and A7 (a UNIQUE `dedupe_key` is the idempotency mechanism — it must be a real database constraint, not application logic).
- **Libraries:** none.
- **High-risk callouts:** this file is applied on every Railway deploy via the Dockerfile CMD and `rw.txt` `preDeployCommand`. Forward-only style per CLAUDE.md — `CREATE TABLE IF NOT EXISTS` only, no DROP, no destructive ALTER. `make test-db` applies the real file, so the idempotency test is the safety net.

### Scope Boundaries

- Do NOT alter any existing table, column, or index.
- Do NOT backfill or migrate any data — both tables start empty.
- Do NOT add pruning, partitioning, or archival for `notifications` (ARCH Out of Scope — growth is tens of rows per day).
- Only add the two tables and their indexes.

### Files Expected

**New files:**
- none

**Modified files:**
- `db/schema.sql` (append the two `CREATE TABLE IF NOT EXISTS` blocks plus indexes, at the end, forward-only)
- `tests/test_schema.py` (new assertions for both tables)

**Must NOT modify:**
- `db/apply.py` (unchanged code, newly load-bearing — covered by the idempotency test above)
- every existing table block in `db/schema.sql`

---

## Task T2: `mailer.py` — Resend HTTP client

> **Status:** done
> **Verification:** tdd
> **Effort:** s
> **Priority:** critical
> **Depends on:** None
> **Satisfies REQs:** R1, R3, R4, R11, R15, N2
> **Footprint slice:** New: `mailer.py`; Modified: `pyproject.toml` / `poetry.lock` (add `requests`)
> **High-risk areas touched:** Dependencies (L), customer deliverability (M)

### Description

The single file that knows the mail vendor exists. `send()` posts one email — with an optional attachment — to Resend's HTTP API and reports the outcome as data rather than exceptions, so the outbox worker in T4 can classify permanent failures (4xx) apart from retryable ones (5xx, transport errors). Nothing else in the codebase imports `requests` or mentions Resend.

### Test Plan

#### Test File(s)
- `tests/test_mailer.py`

#### Test Scenarios

##### Request Shaping

- **posts the expected payload** — GIVEN a configured mailer WHEN `send(to, subject, body)` is called THEN a POST goes to Resend's send endpoint with `from` set to `MAIL_FROM`, the recipient, subject and body in the JSON payload, and an `Authorization: Bearer <key>` header _(verifies R15)_
- **includes reply-to when configured** — GIVEN `MAIL_REPLY_TO` is set WHEN an email is sent THEN the payload carries that reply-to address _(verifies R15)_
- **base64-encodes the attachment** — GIVEN an attachment tuple of filename, bytes and mimetype WHEN `send` is called THEN the payload's `attachments[]` carries the base64 content and the filename _(verifies R4, R11)_

##### Result Classification

- **success returns the provider id** — GIVEN Resend returns 200 with an id WHEN `send` is called THEN the result is `ok=True` with `provider_message_id` populated
- **4xx is a permanent failure** — GIVEN Resend returns 422 for an invalid address WHEN `send` is called THEN the result is `ok=False` with `status_code=422` and the provider's message in `error` _(verifies REQ edge case: valid-format address rejected by the provider)_
- **5xx is a retryable failure** — GIVEN Resend returns 503 WHEN `send` is called THEN the result is `ok=False` with `status_code=503`
- **transport failure is retryable** — GIVEN the HTTP call raises a timeout or connection error WHEN `send` is called THEN the result is `ok=False` with `status_code=0` and `send` does not raise _(verifies ARCH forward stress-test: Resend unreachable for 30 seconds)_

##### Configuration

- **is_configured reflects the environment** — GIVEN `RESEND_API_KEY` or `MAIL_FROM` is missing WHEN `is_configured()` is called THEN it returns False _(verifies ARCH forward stress-test: key missing in production)_

##### Security

- **the API key never leaks into the result** — GIVEN any failure path WHEN the result's `error` string is inspected THEN it does not contain the API key _(verifies N2)_

### Implementation Notes

- **Module(s):** `mailer.py`. Allowed dependencies: `requests`, `os`, stdlib. It must know nothing about UniFleet's domain.
- **Pattern reference:** `margin_store.py` for a small single-purpose module with a narrow public surface.
- **Key decisions:** A4 (Resend over HTTP, isolated so a vendor swap is one file) and A5 (`requests`, accepted as a new production dependency).
- **Libraries:** `requests` — add to `pyproject.toml` and regenerate `poetry.lock`. Use a 10-second request timeout.
- **High-risk callouts:** `send` must never raise; every downstream caller depends on that. Deliverability (M) is a deploy concern rather than a code one — the SPF/DKIM verification prerequisite is tracked in ARCH's Open Questions, not here.

### Scope Boundaries

- Do NOT implement retry, backoff, or queuing here — that is T4's job; `mailer.send` is a single attempt.
- Do NOT read or write the database.
- Do NOT implement bounce, complaint, or webhook handling (ARCH Out of Scope).
- Do NOT render email copy here — copy lives in `notifications.py` (T3).
- Only implement `send()` and `is_configured()`.

### Files Expected

**New files:**
- `mailer.py` (Resend client — mirrors `margin_store.py`'s module shape)
- `tests/test_mailer.py`

**Modified files:**
- `pyproject.toml` (add `requests` to the main dependency group)
- `poetry.lock` (regenerated)

**Must NOT modify:**
- `main.py` (wiring happens in later tasks)

### TDD Sequence (optional)

Write the request-shaping tests against a mocked HTTP layer first — they define `send`'s signature. Result classification follows, then configuration, then the security assertion.

---

## Task T3: `notifications.py` part 1 — copy, enqueue, dedupe

> **Status:** done
> **Verification:** tdd
> **Effort:** m
> **Priority:** critical
> **Depends on:** T1
> **Satisfies REQs:** R1, R3, R4, R6, R7, N3, N4
> **Footprint slice:** New: `notifications.py` (enqueue half — copy templates, `enqueue`, `enqueue_skipped`, dedupe-key construction)
> **High-risk areas touched:** Local dev under `PERSISTENCE_BACKEND=csv` (L)

### Description

The write half of the outbox. Three render functions hold the client-approved copy verbatim, and `enqueue` inserts one `queued` row that the worker (T4) will later send. `enqueue_skipped` records the case where a customer has no email at all as a terminal row, which is what makes the admin "no email" flag possible. Every failure inside this module is swallowed — an outbox problem must never fail a registration, a booking, or an approval.

### Test Plan

#### Test File(s)
- `tests/test_notifications.py`

#### Test Scenarios

##### Email Copy

- **account-code copy matches the brief** — GIVEN account code `HARR` WHEN the account-code email is rendered THEN the subject is `UniFleet Account Code` and the body matches the brief's text with `[XXXX]` replaced by `HARR` _(verifies R1)_
- **booking-received copy matches the brief** — GIVEN a booking WHEN the received email is rendered THEN the subject is `Booking Request Received - UniFleet` and the body states the booking is not yet confirmed and tells the customer to wait before going to the station _(verifies R3)_
- **confirmed copy matches the brief** — GIVEN an approved booking WHEN the confirmed email is rendered THEN the subject is `Booking Confirmed - UniFleet` and the body lists the three redemption requirements and the 48-hour reminder _(verifies R4)_
- **copy interpolates nothing but the account code** — GIVEN any render call WHEN the body is inspected THEN the only substituted value is the account code _(verifies N4 and the security posture in ARCH Cross-Cutting Concerns)_

##### Enqueue

- **enqueue writes a queued row** — GIVEN a valid recipient WHEN `enqueue` is called THEN one row exists with `status='queued'`, `attempts=0`, `next_attempt_at` at or before now, and the rendered subject and body stored
- **duplicate dedupe key is a no-op** — GIVEN a row already exists for `confirmed:V123:<fingerprint>` WHEN `enqueue` is called with the same key THEN it returns None and no second row is created _(verifies A7, R10)_
- **dedupe keys are shaped per kind** — GIVEN each of the four kinds WHEN a row is enqueued THEN the key follows `acct:<code>`, `booked:<voucher_id>`, `confirmed:<voucher_id>:<fingerprint>`, `daily:<Manila date>:<email>` _(verifies N3)_

##### Missing Recipient

- **enqueue_skipped writes a terminal row** — GIVEN a customer with no email WHEN `enqueue_skipped` is called THEN one row exists with `status='skipped'`, a NULL recipient, and the reason in `last_error` _(verifies R6)_

##### Resilience

- **a database failure is swallowed** — GIVEN the pool cannot connect WHEN `enqueue` is called THEN it returns None, logs to stderr, and does not raise _(verifies ARCH forward stress-test: local dev with no `DATABASE_URL`, and Postgres unavailable during registration)_

### Implementation Notes

- **Module(s):** `notifications.py`. Allowed dependencies: `db.pool`, `mailer`, `data_paths`, stdlib. It must not import `main`.
- **Pattern reference:** `audit_log.py` for the Postgres-only, failure-swallowing module shape; `margin_store.py` for accepting an optional `dsn=` constructor/parameter so tests can point at the `schema_db` fixture without fighting the `get_pool()` singleton.
- **Key decisions:** A3 (Postgres-only, repo bypassed), A7 (dedupe key is a database constraint — catch the unique-violation and return None rather than pre-checking), A15 (never touch Flask's request context; unlike `audit_log.py`, this module runs in a bare worker thread and a bare cron process).
- **Libraries:** `psycopg` via `db.pool.get_pool()`.
- **High-risk callouts:** copy must be byte-faithful to `docs/Brief-11_email.md` — the brief is client-approved. Test it as a literal comparison, not a substring check.

### Scope Boundaries

- Do NOT send anything here — no `mailer` calls in this task; sending is T4.
- Do NOT add HTML bodies, templating, or customer-name personalization (ARCH Out of Scope).
- Do NOT wire any call site in `main.py` — that is T5, T6, T7.
- Only implement the render functions, `enqueue`, and `enqueue_skipped`.

### Files Expected

**New files:**
- `notifications.py` (enqueue half — mirrors `audit_log.py`)
- `tests/test_notifications.py`

**Modified files:**
- none

**Must NOT modify:**
- `audit_log.py` (the pattern source, not a target)
- `main.py`

### TDD Sequence (optional)

Copy tests first — they are pure functions and pin down the highest-value contract. Then dedupe-key construction, then the enqueue writes against `schema_db`, then the swallow-on-failure test.

---

## Task T4: `notifications.py` part 2 — worker, retry ladder, requeue

> **Status:** done
> **Verification:** tdd
> **Effort:** l
> **Priority:** critical
> **Depends on:** T2, T3
> **Satisfies REQs:** R7, R8, N1, N3, N5
> **Footprint slice:** New: `notifications.py` (delivery half — `drain_once`, retry ladder, stale-`sending` sweep, `requeue`, `start_worker`, `flags_by_voucher`, `list_flagged`)
> **High-risk areas touched:** Web process resource use (L), customer deliverability (M)

### Description

The read half of the outbox. `drain_once` claims due rows with `FOR UPDATE SKIP LOCKED`, resolves each row's attachment at send time, calls `mailer.send`, and records the outcome — advancing the retry ladder on retryable failures and going terminal on permanent ones. `start_worker` runs that loop on a daemon thread so the single gunicorn worker never blocks on the network. `requeue` is what the admin Resend button calls.

### Test Plan

#### Test File(s)
- `tests/test_notifications.py` (extend)

#### Test Scenarios

##### Claiming and Sending

- **claims only due queued rows** — GIVEN rows in `queued` with `next_attempt_at` in the future, `queued` and due, and `sent` WHEN `drain_once` runs THEN only the due queued row is claimed
- **success marks the row sent** — GIVEN the mailer returns ok WHEN a row is drained THEN its status is `sent`, `sent_at` is set, and `provider_message_id` is stored
- **resolves the attachment at send time** — GIVEN a row with `attachment_ref = voucher_png:<id>` WHEN it is drained THEN the current PNG bytes from `data_paths.official_qr_png_path` are passed to the mailer _(verifies A11, R4)_
- **missing attachment still sends** — GIVEN the referenced PNG does not exist WHEN the row is drained THEN the email is sent without an attachment and `last_error` records `attachment_missing` _(verifies A6, and the residual REQ decision #9 case)_

##### Retry Ladder

- **a 5xx returns the row to the queue with backoff** — GIVEN the mailer returns 503 WHEN the row is drained THEN status is `queued`, `attempts` is incremented, and `next_attempt_at` advances by the ladder delay for that attempt (1s, 5s, 30s, 2m, 10m) _(verifies ARCH forward stress-test: Resend unreachable for 30 seconds)_
- **a 4xx fails immediately** — GIVEN the mailer returns 422 WHEN the row is drained THEN status is `failed` on the first attempt with no further retry _(verifies REQ edge case: address rejected permanently)_
- **the ladder terminates** — GIVEN a row that has already failed four times WHEN the fifth attempt fails with a 5xx THEN status is `failed` and `last_error` holds the provider message _(verifies R7, N5)_

##### Recovery

- **stale sending rows are reclaimed** — GIVEN a row stuck in `sending` for more than five minutes WHEN the worker sweeps THEN it returns to `queued` _(verifies ARCH forward stress-test: app redeploys mid-send)_
- **requeue resets a failed row** — GIVEN a `failed` row WHEN `requeue` is called THEN status is `queued` and `attempts` is 0 _(verifies R8)_
- **requeue is idempotent** — GIVEN a row already `queued` WHEN `requeue` is called again THEN nothing changes and no duplicate row appears _(verifies REQ edge case: admin clicks Resend twice)_
- **requeue can repoint the recipient** — GIVEN a `skipped` row with no recipient WHEN `requeue` is called with a newly added address THEN the row is queued against that address _(verifies R8 with R9)_

##### Configuration and Gating

- **the worker respects the feature flag** — GIVEN `NOTIFICATIONS_ENABLED` is false WHEN `start_worker` is called THEN no thread starts and no rows are drained _(verifies A12)_
- **an unconfigured mailer does not burn attempts** — GIVEN `mailer.is_configured()` is false WHEN `drain_once` runs THEN rows stay `queued` with `attempts` unchanged and the misconfiguration is logged _(verifies ARCH forward stress-test: `RESEND_API_KEY` missing in production)_

##### Admin Read Paths

- **flags_by_voucher is a single query** — GIVEN 50 voucher ids WHEN `flags_by_voucher` is called THEN one query returns the map, not one query per id _(verifies the N+1 concern in T9)_
- **list_flagged returns failed and skipped only** — GIVEN a mix of statuses WHEN `list_flagged` is called THEN only `failed` and `skipped` rows come back, newest first _(verifies R7)_

### Implementation Notes

- **Module(s):** `notifications.py`. Same dependency rules as T3.
- **Pattern reference:** `db/pool.py` for connection handling; `audit_log.py` for the failure-swallowing posture.
- **Key decisions:** A1 (outbox, because `--workers 1` makes an inline send a whole-app stall), A2 (in-process daemon thread rather than a second cron service; `SKIP LOCKED` on the claim keeps the design correct if a sweep or a second worker is ever added), A11 (attachment resolved at send time so the customer always gets the current voucher), A12 (`NOTIFICATIONS_ENABLED` gates the worker).
- **Libraries:** `threading` from stdlib; `psycopg` via `db.pool`.
- **High-risk callouts:** `start_worker` runs at `main.py` import time in T5's wiring — it must catch every exception, or a mail problem takes the whole app down. Poll interval hardcoded at 5 seconds per ARCH's Open Questions.

### Scope Boundaries

- Do NOT introduce Celery, RQ, Redis, or any broker (ARCH Out of Scope).
- Do NOT add multi-worker coordination beyond `SKIP LOCKED` (ARCH Out of Scope — `--workers 1` today).
- Do NOT add pruning or archival of old rows (ARCH Out of Scope).
- Do NOT call `start_worker` from `main.py` here — the wiring belongs to T5.
- Only implement the delivery half of the module.

### Files Expected

**New files:**
- none (extends `notifications.py` from T3)

**Modified files:**
- `notifications.py` (add `drain_once`, `start_worker`, `requeue`, `flags_by_voucher`, `list_flagged`)
- `tests/test_notifications.py` (extend)

**Must NOT modify:**
- `mailer.py` (T2's contract is fixed — consume it, do not reshape it)
- `main.py`

### TDD Sequence (optional)

Claiming first (it defines the row lifecycle), then the success path, then the retry ladder, then recovery, then the admin read paths.

---

## Task T5: `/register` — require email and send the account code

> **Status:** done
> **Verification:** tdd
> **Effort:** m
> **Priority:** high
> **Depends on:** T3, T4
> **Satisfies REQs:** R1, R2, R7
> **Footprint slice:** Modified: `main.py:697` `register()`, `templates/register.html`; plus `main.py` imports and `notifications.start_worker()` at startup
> **High-risk areas touched:** `/register` flow (M — the only existing user-facing behavior change in the REQ, and three test files drive this form), `main.py` module import (M — a raising import takes down the app)

### Description

Registration starts requiring a valid email address, and on success the new customer immediately gets their four-letter account code by email. This is the highest-regression-risk task in the plan: `/register` is a live customer-acquisition path and three existing test files POST this form without an email today. Those tests are updated here, so the breakage never exists on its own commit. This task also carries the app-startup wiring — the guarded module imports and the `start_worker()` call.

### Test Plan

#### Test File(s)
- `tests/test_register_pg.py` (extend)
- `tests/test_register_optional_labels.py` (update)
- `tests/test_register_success_text.py` (update)

#### Test Scenarios

##### Email Validation

- **blank email is rejected** — GIVEN a registration POST with an empty email WHEN it is submitted THEN the form re-renders with a field-level error and no customer record is created _(verifies R2)_
- **malformed email is rejected** — GIVEN an email of `not-an-email` WHEN the form is submitted THEN the same rejection occurs and no customer is created _(verifies R2, REQ edge case)_
- **entered values survive the error render** — GIVEN a rejected submission WHEN the form re-renders THEN the previously entered company name, contact name and contact number are still populated _(verifies R2)_
- **the email field is marked required** — GIVEN the register page WHEN it is rendered THEN the email input carries the required attribute and its label reflects that _(verifies R2)_

##### Account Code Email

- **a valid registration queues the account-code email** — GIVEN a valid submission WHEN the customer is created THEN exactly one `account_code` notification is queued, addressed to the submitted email and carrying the customer's real account code _(verifies R1)_
- **the email is queued after both writes** — GIVEN a valid submission WHEN the enqueue happens THEN the customer row and the `customers.csv` append have both already completed _(verifies R1 — a code must never be emailed for an account that failed to save)_

##### Resilience

- **an enqueue failure does not fail registration** — GIVEN the outbox insert raises WHEN a valid registration is submitted THEN the customer is still created and the success redirect still happens _(verifies R7, ARCH forward stress-test)_
- **startup survives a broken notifications module** — GIVEN the notifications import fails WHEN the app is imported THEN the app still boots and registration still works _(verifies ARCH backward-regression risk for `main.py` module import)_

##### Regression Guard

- **the contact-number rule is unchanged** — GIVEN a submission with fewer than ten contact-number digits WHEN it is submitted THEN it is still rejected with the existing message _(guards ARCH-brief-8's R9/R10)_
- **the Postgres-down path still registers** — GIVEN `create_customer_if_absent` raises WHEN a valid registration is submitted THEN the `pg_error` sentinel path still completes the registration _(guards ARCH backward-regression risk for `main.py:1224`)_
- **existing register tests pass with an email supplied** — GIVEN the three existing register test files updated to post an email WHEN they run THEN every pre-existing assertion still holds _(guards ARCH backward-regression risk for `tests/test_register_pg.py`, `tests/test_register_optional_labels.py`, `tests/test_register_success_text.py`)_

### Implementation Notes

- **Module(s):** `main.py` (route handler + startup), `templates/register.html`.
- **Pattern reference:** the existing `contact_number` validation immediately above at `main.py:704` — same flash-and-re-render shape with `form_values=request.form`. Guard the new imports exactly like `build_supplier_pdf` at `main.py:24-27`.
- **Key decisions:** A14 (validate at the form; the `customers.email` column stays nullable, because R6 exists to serve legacy blank rows), A12 (the worker respects `NOTIFICATIONS_ENABLED`).
- **Libraries:** `re` for format validation — no new dependency; the codebase already validates this way.
- **High-risk callouts:** CLAUDE.md — never insert a helper function between an `@app.route` decorator and its `def`; it silently rebinds the decorator (this repo has been bitten twice). The enqueue goes after `_append_customer_csv_if_absent(new_row)` and before the redirect. Wrap it so it cannot raise.

### Scope Boundaries

- Do NOT backfill emails for existing customers, or retro-notify anyone who registered before this ships (ARCH Out of Scope).
- Do NOT add a NOT NULL constraint to `customers.email` (A14).
- Do NOT verify deliverability, only format.
- Do NOT touch the `/book` or approve call sites — those are T6 and T7.
- Only change registration validation, the account-code enqueue, and the startup wiring.

### Files Expected

**New files:**
- none

**Modified files:**
- `main.py` (`register()` validation + enqueue; guarded `notifications` import; `start_worker()` at import)
- `templates/register.html` (email marked required)
- `tests/test_register_pg.py`, `tests/test_register_optional_labels.py`, `tests/test_register_success_text.py` (updated)

**Must NOT modify:**
- `persistence.py` / `db/postgres_repo.py` (customer creation is unchanged here; the email-edit method is T8)
- `notifications.py` (consume T3/T4's contract)
- `main.py:1274` `/book` and `main.py:651` approve

### TDD Sequence (optional)

Update the three existing test files to post an email first, so the suite is green before the validation lands. Then the rejection tests, then the enqueue tests, then resilience.

---

## Task T6: `/book` POST — send the booking-received email

> **Status:** done
> **Verification:** test-after
> **Effort:** s
> **Priority:** high
> **Depends on:** T3, T4
> **Satisfies REQs:** R3, R5, R6, R7
> **Footprint slice:** Modified: `main.py:1274` (`/book` POST — the `created`-aware branch)
> **High-risk areas touched:** `/book` POST (M — restructuring an exception swallow on a revenue path)

### Description

A customer who submits a booking gets an acknowledgement telling them the request was received and is not yet confirmed. The recipient comes from the customer record matching the booking's account code, because the booking form only collects a code. The delicate part is the existing `try/except` around `create_unverified_booking` that prints and continues: an email must only be queued when the booking genuinely saved, and the restructuring must not turn a swallowed error into a failed booking.

### Test Plan

#### Test File(s)
- `tests/test_book_pg.py` (extend)

#### Test Scenarios

##### Happy Path

- **a booking queues the received email** — GIVEN an account code whose customer has an email WHEN a booking is submitted THEN exactly one `booking_received` notification is queued, addressed to the customer record's email _(verifies R3, R5)_
- **the address comes from the customer record** — GIVEN a booking form that carries no email field WHEN the notification is queued THEN its recipient matches the stored customer email, not anything from the request _(verifies R5)_

##### Missing Recipient

- **an emailless customer produces a skipped row** — GIVEN a legacy customer with a blank email WHEN a booking is submitted THEN the booking succeeds, no send is attempted, and a `skipped` row is recorded _(verifies R6)_
- **an unknown account code produces a skipped row** — GIVEN an account code matching no customer WHEN a booking is submitted THEN the booking's existing behavior is unchanged and a `skipped` row is recorded _(verifies REQ edge case)_

##### Resilience and Regression Guard

- **a failed repo write queues nothing** — GIVEN `create_unverified_booking` raises WHEN the booking is submitted THEN no notification is queued and the request does not 500 _(guards the existing swallow at `main.py:1274`)_
- **an enqueue failure does not fail the booking** — GIVEN the outbox insert raises WHEN a booking is submitted THEN the booking is still created and the success page still renders _(verifies R7)_
- **the booking success page is unchanged** — GIVEN a successful booking WHEN the response renders THEN the existing payment info, due amount and discount copy are all still present _(guards ARCH backward-regression risk for `tests/test_book_and_booking_success_copy.py`)_

### Implementation Notes

- **Module(s):** `main.py` `/book` POST only.
- **Pattern reference:** the enqueue call shape established in T5's `register()`.
- **Key decisions:** A1 (enqueue and return; never send inline), A3 (the outbox is Postgres-only, so this call must tolerate its own failure).
- **Libraries:** none new.
- **High-risk callouts:** initialize `created = None` before the existing `try` and branch on it, rather than moving or widening the `try/except` boundary — the swallow is deliberate existing behavior that keeps a Postgres blip from losing a booking. Recipient resolution is `repo.get_customer(account_code)`; a missing customer and a blank email are both the `enqueue_skipped` path.

### Scope Boundaries

- Do NOT add an email field to the booking form (A7 in REQ — the customer record is the single source of truth).
- Do NOT change what a booking writes, or the booking success page.
- Do NOT change the existing exception-swallow semantics — only branch on the result.
- Only add the enqueue and skip paths.

### Files Expected

**New files:**
- none

**Modified files:**
- `main.py` (`/book` POST — `created`-aware branch, enqueue, `enqueue_skipped`)
- `tests/test_book_pg.py` (extend)

**Must NOT modify:**
- `templates/book.html`, `templates/booking_success.html`
- `persistence.py` booking methods
- `tests/test_book_and_booking_success_copy.py` (assertions must pass unchanged)

---

## Task T7: Approve — fingerprint and send the confirmed email with the voucher

> **Status:** done
> **Verification:** tdd
> **Effort:** m
> **Priority:** high
> **Depends on:** T3, T4
> **Satisfies REQs:** R4, R7, R10
> **Footprint slice:** Modified: `main.py:651` (approve branch — fingerprint + enqueue after `set_status`)
> **High-risk areas touched:** Approve flow (M — the highest-value email, the only one with an attachment, and two existing tests drive this branch)

### Description

When an admin approves a booking, the customer receives their confirmation with the branded voucher PNG attached. Admins do flip status back and forth while correcting data, so the email is gated on a fingerprint over the voucher's substance — amount, total, station, and the PNG bytes — meaning an unchanged re-approval sends nothing while a corrected voucher does reach the customer. The enqueue sits strictly after `repo.set_status('Unredeemed')`; the existing 500-on-asset-failure abort above it is deliberately untouched.

### Test Plan

#### Test File(s)
- `tests/test_admin_approve_margin.py` (extend)

#### Test Scenarios

##### Happy Path

- **approval queues the confirmed email** — GIVEN an `Unverified` voucher WHEN an admin approves it THEN exactly one `booking_confirmed` notification is queued for the customer's email, referencing the voucher's `_Official.png` _(verifies R4)_
- **the enqueue happens after the status flip** — GIVEN an approval WHEN the enqueue runs THEN `repo.set_status(voucher_id, 'Unredeemed')` has already completed _(verifies A6)_

##### Re-approval Gating

- **an unchanged re-approval sends nothing** — GIVEN a voucher already confirmed once WHEN it is flipped back to `Unverified` and approved again with no data change THEN no second notification is queued _(verifies R10)_
- **a changed amount re-sends** — GIVEN a confirmed voucher whose requested amount is then changed WHEN it is re-approved THEN a new notification is queued _(verifies R10)_
- **a changed station re-sends** — GIVEN a confirmed voucher whose station is changed WHEN it is re-approved THEN a new notification is queued _(verifies R10)_
- **a regenerated PNG re-sends** — GIVEN a confirmed voucher whose PNG is regenerated with identical numbers WHEN it is re-approved THEN a new notification is queued, because the fingerprint covers the image bytes _(verifies A8)_

##### Resilience and Regression Guard

- **asset failure still aborts approval and sends nothing** — GIVEN `generate_assets_for_row` raises WHEN approval is attempted THEN the response is 500, the status stays `Unverified`, and no notification is queued _(verifies A6; guards the existing behavior at `main.py:646-651`)_
- **an enqueue failure cannot leave a voucher unapproved** — GIVEN the outbox insert raises WHEN approval runs THEN the voucher is still `Unredeemed` and the admin still gets a normal redirect _(verifies R7, ARCH backward-regression risk)_
- **concurrent approvals produce one email** — GIVEN two approvals of the same voucher with the same fingerprint WHEN both enqueue THEN the unique `dedupe_key` allows only one row _(verifies ARCH forward stress-test: two admins approve simultaneously)_
- **the Redeemed transition sends nothing** — GIVEN an `Unredeemed` voucher WHEN it is marked `Redeemed` THEN no notification is queued _(verifies REQ scope boundary)_

### Implementation Notes

- **Module(s):** `main.py` approve branch only.
- **Pattern reference:** the existing `append_audit` calls in the same branch for where side effects belong relative to the status flip.
- **Key decisions:** A6 (the existing abort stays; the enqueue goes after `set_status`, which narrows REQ decision #9 to the case where a PNG disappears after approval), A8 (SHA-256 over amount, total, station, and PNG bytes, embedded in the dedupe key — a changed fingerprint is simply a new key, so no separate comparison logic is needed), A7 (the unique constraint is what makes concurrent approvals safe).
- **Libraries:** `hashlib` from stdlib.
- **High-risk callouts:** `tests/test_admin_approve_margin.py` and `tests/test_book_calculator_inversion.py` both monkeypatch `generate_assets_for_row` and drive this exact branch — they will now hit the enqueue path and must keep passing. The PNG path is `data_paths.official_qr_png_path(voucher_id)`.

### Scope Boundaries

- Do NOT reorder `set_status` ahead of asset generation (A6 — that was explicitly rejected).
- Do NOT send email on the `Redeemed` transition, on cancellation, or on order deletion (ARCH Out of Scope).
- Do NOT change what the approve flow computes or persists.
- Only add the fingerprint computation and the enqueue.

### Files Expected

**New files:**
- none

**Modified files:**
- `main.py` (approve branch — fingerprint + enqueue after `set_status`)
- `tests/test_admin_approve_margin.py` (extend)

**Must NOT modify:**
- `generate_voucher.py`
- the compute-and-persist block at `main.py:615-636`
- `tests/test_book_calculator_inversion.py` (its assertions must pass unchanged)

### TDD Sequence (optional)

Fingerprint computation first as a pure function, then the happy-path enqueue, then the four re-approval cases, then resilience.

---

## Task T8: Admin can edit a customer's email

> **Status:** done
> **Verification:** tdd
> **Effort:** m
> **Priority:** medium
> **Depends on:** T1
> **Satisfies REQs:** R9
> **Footprint slice:** Modified: `persistence.py` and `db/postgres_repo.py` (new `update_customer_email`), `main.py` (new route), `templates/admin_customer_lookup.html` (inline edit form)
> **High-risk areas touched:** `persistence.py` / `db/postgres_repo.py` (M — a shared repo class, and the CSV write can corrupt `customers.csv` if it ignores the lock file)

### Description

Legacy customers registered before this feature have no email on file, so their bookings produce `skipped` notification rows that can never be recovered. This task closes that loop: an admin fills in the missing address on the customer detail page, then hits Resend on the flagged booking. It is the one task in the plan that touches the repo abstraction, so all three backends get the method.

### Test Plan

#### Test File(s)
- `tests/test_customer_repo.py` (extend)
- `tests/test_admin_customers.py` (extend)

#### Test Scenarios

##### Repository Layer

- **PostgresRepo updates the email** — GIVEN an existing customer WHEN `update_customer_email` is called THEN the stored email changes and no other column is touched _(verifies R9)_
- **CSVRepo updates under the lock** — GIVEN the CSV backend WHEN `update_customer_email` is called THEN the row is rewritten while holding the same `.lock` sidecar that `create_customer_if_absent` uses, and no other row is disturbed _(guards ARCH backward-regression risk: `customers.csv` corruption)_
- **DBRepo updates the email** — GIVEN the SQLite backend WHEN `update_customer_email` is called THEN the stored email changes _(resolves ARCH Open Question on backend parity)_
- **an unknown account code is a no-op** — GIVEN a code matching no customer WHEN the method is called THEN it returns False and creates nothing

##### HTTP Route

- **a valid address is saved** — GIVEN an admin session WHEN the email form is posted with a valid address THEN the customer's email is updated and the detail page reflects it _(verifies R9)_
- **a malformed address is rejected** — GIVEN a posted value of `nope` WHEN the form is submitted THEN the stored email is unchanged and an error is flashed _(verifies R9)_
- **the route requires admin** — GIVEN no admin session WHEN the route is posted THEN the response redirects to the login page _(verifies ARCH auth posture)_
- **the update is audited** — GIVEN a successful update WHEN it completes THEN `append_audit` records a `customer_email_update` action

##### End-to-End with Resend

- **a filled-in address unblocks a skipped notification** — GIVEN a legacy customer with a `skipped` booking notification WHEN an admin adds their email and clicks Resend THEN the notification is queued against the new address _(verifies R9 with R8)_

##### Regression Guard

- **all three lookup states still render** — GIVEN the customer lookup page WHEN it renders in the `not_found`, `picklist` and `detail` states THEN each renders as before with the edit form present only in `detail` _(guards ARCH backward-regression risk for `templates/admin_customer_lookup.html` and `tests/test_admin_customers.py`)_

### Implementation Notes

- **Module(s):** `persistence.py`, `db/postgres_repo.py`, `main.py`, `templates/admin_customer_lookup.html`.
- **Pattern reference:** `CSVRepo.create_customer_if_absent` at `persistence.py:283` for the `fcntl.flock` discipline; `PostgresRepo.create_customer` at `db/postgres_repo.py:433` for the SQL shape; `admin_stations.html`'s edit form for the inline-edit markup.
- **Key decisions:** A14 (the column stays nullable; validation lives at the write points).
- **Libraries:** none new.
- **High-risk callouts:** the CSV path is the dangerous one — `customers.csv` is dual-written by `_append_customer_csv_if_absent` and read by the CSV backend, so an unlocked rewrite can lose rows. Both stores must end up consistent, or a recipient lookup under `PERSISTENCE_BACKEND=csv` will still resolve to blank.

### Scope Boundaries

- Do NOT add general customer editing — email only.
- Do NOT allow editing the account code (it is the primary key and the booking join key).
- Do NOT add a customer-facing way to change an email address.
- Only add `update_customer_email`, the one route, and the inline form.

### Files Expected

**New files:**
- none

**Modified files:**
- `persistence.py` (`update_customer_email` on `CSVRepo` and `DBRepo`)
- `db/postgres_repo.py` (`update_customer_email` on `PostgresRepo`)
- `main.py` (`POST /admin/customers/<account_code>/email`)
- `templates/admin_customer_lookup.html` (inline edit form in the `detail` state)
- `tests/test_customer_repo.py`, `tests/test_admin_customers.py` (extend)

**Must NOT modify:**
- `models.py` column definitions
- `db/schema.sql` (the column already exists and stays nullable)
- `_append_customer_csv_if_absent` in `main.py`

---

## Task T9: Admin notification flags and Resend

> **Status:** done
> **Verification:** test-after
> **Effort:** m
> **Priority:** high
> **Depends on:** T4
> **Satisfies REQs:** R7, R8
> **Footprint slice:** Modified: `main.py:238` `/admin` (flag join), `main.py` (resend route), `templates/admin.html` (badge + button)
> **High-risk areas touched:** `/admin` dashboard (L — but a template error breaks the whole page)

### Description

Failed and skipped notifications become visible where admins already work. The `/admin` booking table gains a flag badge beside its existing PNG-status column, and each flagged row gets a Resend button that requeues that specific notification. This is the recovery mechanism the whole no-blocking design depends on: without it, a provider outage would silently swallow customer emails.

### Test Plan

#### Test File(s)
- `tests/test_admin_notification_flags.py`

#### Test Scenarios

##### Flag Display

- **a failed notification shows a badge** — GIVEN a voucher with a `failed` notification WHEN `/admin` renders THEN that row shows a visible failure flag _(verifies R7)_
- **a skipped notification shows a badge** — GIVEN a booking whose customer had no email WHEN `/admin` renders THEN that row shows the no-email flag _(verifies R6, R7)_
- **rows without notifications are unchanged** — GIVEN vouchers with `sent` or no notifications WHEN `/admin` renders THEN those rows carry no flag _(guards ARCH backward-regression risk for `tests/test_admin_display_layout.py`)_

##### Resend

- **the resend route requeues the notification** — GIVEN a `failed` notification WHEN an admin posts to the resend route THEN the row returns to `queued` with `attempts` reset and the admin is redirected back _(verifies R8)_
- **the route requires admin** — GIVEN no admin session WHEN the resend route is posted THEN the response redirects to the login page
- **an unknown id returns 404** — GIVEN a notification id that does not exist WHEN the route is posted THEN the response is 404
- **the resend is audited** — GIVEN a successful resend WHEN it completes THEN `append_audit` records a `notification_resend` action

##### Performance and Regression Guard

- **the flag lookup is one query** — GIVEN 50 vouchers on the dashboard WHEN the page renders THEN a single bulk query resolves the flags rather than one per row _(verifies the N+1 concern in ARCH Cross-Cutting Concerns)_
- **the PNG status column still renders** — GIVEN the dashboard WHEN it renders THEN the existing `png_exists` indicator is present and correct for every row _(guards ARCH backward-regression risk for `main.py:238` and `templates/admin.html`)_
- **the dashboard survives an unavailable outbox** — GIVEN the notifications query raises WHEN `/admin` renders THEN the page still loads with the voucher table intact and no flags _(verifies ARCH forward stress-test: local dev with no `DATABASE_URL`)_

### Implementation Notes

- **Module(s):** `main.py` (`/admin` handler + resend route), `templates/admin.html`.
- **Pattern reference:** the existing `png_exists` computation inside the row loop at `main.py:251-256`; the delete-order button in `admin.html` for a POST-per-row control.
- **Key decisions:** A2 (requeue is what the button calls; delivery still happens on the worker thread, so the request returns immediately).
- **Libraries:** none new.
- **High-risk callouts:** `/admin` is the operational hub — wrap the flag lookup so a database problem degrades to "no flags shown" rather than a 500. `tests/test_admin_display_layout.py` asserts this page's structure.

### Scope Boundaries

- Do NOT build a standalone notification-log page — flags live on the existing dashboard.
- Do NOT add bulk resend, filtering, or search.
- Do NOT let the resend button send synchronously — it requeues, and the worker delivers.
- Only add the flag join, the badge, the button, and the resend route.

### Files Expected

**New files:**
- `tests/test_admin_notification_flags.py`

**Modified files:**
- `main.py` (`/admin` flag join; `POST /admin/notifications/<id>/resend`)
- `templates/admin.html` (flag badge + Resend button)

**Must NOT modify:**
- the voucher-loading logic at `main.py:244-256` beyond adding the flag join
- `tests/test_admin_display_layout.py` (its assertions must pass unchanged)
- `tests/test_admin_delete_order.py`

---

## Task T10: `report_recipients.py` and the recipient admin routes

> **Status:** done
> **Verification:** tdd
> **Effort:** s
> **Priority:** medium
> **Depends on:** T1
> **Satisfies REQs:** R14
> **Footprint slice:** New: `report_recipients.py`; Modified: `main.py` (three recipient routes)
> **High-risk areas touched:** None

### Description

The internal distribution list for the nightly supplier PDF, managed by admins rather than by redeploying config. The module owns the table; three routes expose add, list and delete. The cron job in T13 reads `active_emails()` from here.

### Test Plan

#### Test File(s)
- `tests/test_report_recipients.py`

#### Test Scenarios

##### CRUD

- **add and list** — GIVEN an empty table WHEN a recipient is added THEN it appears in `list_all` with its label _(verifies R14)_
- **delete removes** — GIVEN an existing recipient WHEN it is deleted THEN it no longer appears _(verifies R14)_
- **duplicates are rejected** — GIVEN an existing recipient WHEN the same address is added again THEN the add returns None and no second row is created
- **email is normalized** — GIVEN an address with mixed case WHEN it is added THEN it is stored lowercase
- **active_emails excludes inactive** — GIVEN one active and one inactive recipient WHEN `active_emails()` is called THEN only the active address is returned _(verifies R14)_
- **an empty list is valid** — GIVEN no recipients WHEN `active_emails()` is called THEN it returns an empty list without raising _(verifies REQ edge case: empty recipient list)_

##### HTTP Routes

- **a malformed address is rejected** — GIVEN an admin session WHEN `nope` is posted to the add route THEN nothing is stored and an error is flashed
- **all three routes require admin** — GIVEN no admin session WHEN each route is requested THEN the response redirects to the login page
- **recipient changes are audited** — GIVEN a successful add or delete WHEN it completes THEN `append_audit` records the corresponding action

### Implementation Notes

- **Module(s):** `report_recipients.py`, `main.py` routes.
- **Pattern reference:** `margin_store.py` for the module shape (including an optional `dsn=` parameter so tests can target the `schema_db` fixture around the `get_pool()` singleton); the `/admin/stations` route group at `main.py:1554-1668` for route naming, auth guard and redirect style.
- **Key decisions:** A13 (a dedicated table and admin page, because staff churn must not require a redeploy).
- **Libraries:** none new.
- **High-risk callouts:** none — this is additive and touches no existing behavior.

### Scope Boundaries

- Do NOT add per-recipient scheduling, filtering, or report-content preferences.
- Do NOT seed default recipients (ARCH Open Question — the list ships empty).
- Do NOT build the template here — that is T11.
- Only implement the module and the three routes.

### Files Expected

**New files:**
- `report_recipients.py` (mirrors `margin_store.py`)
- `tests/test_report_recipients.py`

**Modified files:**
- `main.py` (`GET/POST /admin/recipients`, `POST /admin/recipients/<id>/delete`)

**Must NOT modify:**
- `db/schema.sql` (T1 created the table)
- `margin_store.py` (pattern source, not a target)

---

## Task T11: Recipient management page

> **Status:** done
> **Verification:** ui
> **Effort:** xs
> **Priority:** medium
> **Depends on:** T10
> **Satisfies REQs:** R14
> **Footprint slice:** New: `templates/admin_recipients.html`
> **High-risk areas touched:** None

### Description

The admin-facing surface for T10's routes: a list of internal recipients with an add form and a per-row delete, styled to match the existing station-management page.

### Verification Checklist

- **List renders** — expected: every active recipient appears with its label and a delete control, in the same table styling as `admin_stations.html`
- **Add form works** — expected: submitting a valid address adds it and the page re-renders showing the new row
- **Empty state is legible** — expected: with no recipients, the page shows an explanatory line rather than a blank table, since the list ships empty by design
- **Duplicate address feedback** — expected: adding an existing address shows the flash message rather than silently doing nothing
- **Invalid address feedback** — expected: submitting a malformed address shows a validation message and does not add a row
- **Delete confirms first** — expected: the delete control asks for confirmation before firing, matching the delete-order button's behavior on `admin.html`
- **Mobile width** — expected: the page is usable at a phone viewport width, consistent with `REQ-register-mobile-friendly`
- **Unauthenticated access** — expected: visiting the page without an admin session redirects to the login page

#### Testable Seams

- Page renders with an empty recipient list (no exception, empty-state copy present)
- Page renders with several recipients (one row each)
- Flash messages surface for the duplicate and invalid cases
- The delete control posts to the correct route with the right id

### Implementation Notes

- **Module(s):** `templates/admin_recipients.html`.
- **Pattern reference:** `templates/admin_stations.html` — same table plus add-form layout; the delete-confirmation pattern from `templates/admin.html`.
- **Key decisions:** A13 (a dedicated page rather than a section bolted onto the existing dashboard).
- **Libraries:** none — the project uses server-rendered Jinja templates with no frontend framework.
- **High-risk callouts:** none.

### Scope Boundaries

- Do NOT add client-side JavaScript beyond a delete confirmation.
- Do NOT introduce a CSS framework or restyle other admin pages.
- Do NOT add sorting, pagination, or search — the list is a handful of internal addresses.
- Only build the page.

### Files Expected

**New files:**
- `templates/admin_recipients.html` (mirrors `templates/admin_stations.html`)

**Modified files:**

_Scope amendment, agreed during implementation._ This task lists Testable Seams
but named no file to put them in. They go alongside T10's route tests, which
already cover this page's endpoints.

- `tests/test_report_recipients.py` (seam tests: empty render, populated render,
  flash surfacing, delete form target)

**Must NOT modify:**
- `templates/admin.html`, `templates/admin_stations.html`
- `main.py` (T10 owns the routes)

---

## Task T12: Deployment configuration and full-stack verification

> **Status:** done — except the cold-start check, see below
> **Verification:** checklist
> **Effort:** s
> **Priority:** high
> **Depends on:** T2, T13
> **Satisfies REQs:** R11, N2
> **Footprint slice:** Modified: `rw.txt` (cron entry), `.env.example`, `docker-compose.yml`, `AGENTS.md`
> **High-risk areas touched:** Railway deployment (M — the app boots without `RESEND_API_KEY` but silently sends nothing, so a config miss looks like a code failure)

### Description

The last task: wire the cron schedule into Railway's config, document and pass through the four new environment variables, and verify the whole feature end-to-end in the container. It runs after T13 because one of its checks invokes that script.

### Verification Checklist

- **Dependencies resolve** — run `poetry lock && poetry install`; expected: `requests` installs and `make up-d` brings the stack up healthy
- **Cron entry is correct** — inspect `rw.txt`; expected: a cron service entry running `python scripts/send_daily_report.py` at `0 16 * * *` (16:00 UTC = 00:00 Asia/Manila), and `pytest tests/test_railway_toml.py -v` passes _(guards ARCH backward-regression risk for `rw.txt`)_
- **Env vars are declared** — inspect `.env.example` and `docker-compose.yml`; expected: `RESEND_API_KEY`, `MAIL_FROM`, `MAIL_REPLY_TO`, `NOTIFICATIONS_ENABLED` are present in both, and `make shell` then `env` shows them inside the web container
- **App boots without mail config** — unset `RESEND_API_KEY` and restart; expected: the app boots, `/healthz` returns 200, queued rows stay `queued` with `attempts` unchanged, and the misconfiguration is logged _(verifies ARCH forward stress-test)_
- **Kill switch works** — set `NOTIFICATIONS_ENABLED=false` and restart; expected: no worker thread starts and no rows drain _(verifies A12)_
- **Docs updated** — inspect `AGENTS.md`; expected: the environment-variables section documents all four, matching how `SECRET_KEY` and `ADMIN_PASSWORD` are described
- **Cron script runs in-container** — run `python scripts/send_daily_report.py --dry-run` via `make shell`; expected: exits 0 and reports what it would enqueue, without writing rows
- **Schema applies on a cold start** — run `make clean && make up-d`; expected: `db/apply.py` creates both new tables and the app boots. **NOT RUN:** `make clean` drops the local Postgres volume and the developer's dev data with it. Idempotent re-apply is covered by tests/test_schema.py against an ephemeral database; run this by hand when a throwaway volume is acceptable.
- **Full suite green** — run `make test-db`; expected: every test passes, including the pre-existing suite

### Implementation Notes

- **Module(s):** `rw.txt`, `.env.example`, `docker-compose.yml`, `AGENTS.md`.
- **Pattern reference:** the existing cron service for `scripts/backup_postgres.py` — mirror its image and environment wiring; the `ADMIN_PASSWORD` / `SECRET_KEY` entries in `.env.example` for how a required secret is documented.
- **Key decisions:** A9 (Railway cron at `0 16 * * *` UTC), A12 (`NOTIFICATIONS_ENABLED` as the kill switch), and ARCH's rollout order — deploy with the flag false, verify the sending domain, add recipients, then enable the flag, then enable the cron.
- **Libraries:** none beyond T2's `requests`.
- **High-risk callouts:** `rw.txt` is the Railway config file, not `railway.toml` (CLAUDE.md, renamed in `ecdf9ae`), and `tests/test_railway_toml.py` asserts its contents. Before running anything locally, confirm `.env`'s `DATABASE_URL` points at the local `db` container and not the shared prod Railway database (CLAUDE.md).

### Scope Boundaries

- Do NOT commit any real API key — `.env.example` carries placeholders only.
- Do NOT enable the cron schedule or the feature flag in production as part of this task; that is the operator's rollout sequence.
- Do NOT seed recipients.
- Only change configuration and documentation.

### Files Expected

**New files:**
- none

**Modified files:**
- `rw.txt` (cron service entry)
- `.env.example` (four new variables with placeholder values)
- `docker-compose.yml` (pass the four through to the web service)
- `AGENTS.md` (document the four in the environment section)

**Must NOT modify:**
- `Dockerfile` (the CMD chain is unchanged; the cron service is separate)
- `tests/test_railway_toml.py` (its assertions must pass — update only if the cron entry legitimately changes the asserted shape)
- any application module

---

## Task T13: `scripts/send_daily_report.py` — nightly supplier PDF

> **Status:** done
> **Verification:** test-after
> **Effort:** m
> **Priority:** high
> **Depends on:** T3, T10
> **Satisfies REQs:** R11, R12, R13, N3
> **Footprint slice:** New: `scripts/send_daily_report.py`
> **High-risk areas touched:** `report_pdf.build_supplier_pdf` (gains a second caller), Railway deployment (M)

### Description

The internal half of the feature: at 00:00 Manila the cron service builds the supplier PDF over all stations and all currently live orders, then enqueues one outbox row per active recipient. There is deliberately no date window — expiring orders are cancelled by admins and drop out of the live set, so filtering by date would only hide still-redeemable orders. The report is sent even when it is empty, so recipients can tell "no bookings" apart from "the job broke".

### Test Plan

#### Test File(s)
- `tests/test_daily_report.py`

#### Test Scenarios

##### Fan-out and Content

- **one row per active recipient** — GIVEN three active recipients WHEN the script runs THEN three notifications are queued, one per address, each with the PDF attached _(verifies R11)_
- **the PDF covers all stations** — GIVEN vouchers across several stations WHEN the report is built THEN no station filter is applied and no cookie is consulted _(verifies R12, A9)_
- **only Unredeemed vouchers appear** — GIVEN a mix of `Unverified`, `Unredeemed` and `Redeemed` vouchers WHEN the report is built THEN only `Unredeemed` rows are included, matching `/supplier-sheet.pdf` _(verifies R12)_
- **soft-deleted orders are excluded** — GIVEN a voucher with `deleted_at` set WHEN the report is built THEN it does not appear _(guards ARCH backward-regression risk for `tests/test_supplier_exports_exclude_deleted.py`)_
- **no date filtering is applied** — GIVEN live orders with transaction dates spanning several weeks WHEN the report is built THEN all of them appear _(verifies R12)_

##### Edge Cases

- **an empty report is still sent** — GIVEN zero live orders WHEN the script runs THEN notifications are still queued with the empty PDF attached _(verifies R13)_
- **an empty recipient list exits cleanly** — GIVEN no active recipients WHEN the script runs THEN it logs the fact, enqueues nothing, and exits 0 _(verifies REQ edge case)_

##### Idempotency

- **a second run the same day enqueues nothing** — GIVEN the script has already run today WHEN it runs again THEN the `daily:<Manila date>:<email>` dedupe key blocks every insert _(verifies N3, ARCH forward stress-test: cron fires twice or the service restarts)_
- **the dedupe key uses the Manila date** — GIVEN a run at 16:05 UTC WHEN the key is built THEN it carries the corresponding Manila calendar date, not the UTC one _(verifies A9)_

##### Failure Modes

- **a missing DATABASE_URL exits 1** — GIVEN no `DATABASE_URL` WHEN the script runs THEN it logs the reason and exits 1
- **a PDF build failure exits 2** — GIVEN `build_supplier_pdf` raises WHEN the script runs THEN nothing is enqueued and it exits 2 _(verifies ARCH forward stress-test: no corrupt report is ever emailed)_

### Implementation Notes

- **Module(s):** `scripts/send_daily_report.py`. Allowed dependencies: `persistence` (via `get_repo`), `report_pdf`, `report_recipients`, `notifications`, `data_paths`.
- **Pattern reference:** `scripts/backup_postgres.py` — module docstring stating exit codes, timestamped stdout logging, a `--dry-run` flag, and distinct non-zero exits per failure class. The PDF assembly mirrors `/supplier-sheet.pdf` at `main.py:2021-2030`, minus the cookie and query-parameter station filtering.
- **Key decisions:** A9 (a Railway cron service rather than an in-process scheduler, so the report does not depend on web-process uptime), A10 (the cron enqueues into the same outbox rather than sending directly, so a midnight provider blip retries instead of losing the report), A15 (no Flask request context is available here).
- **Libraries:** `zoneinfo.ZoneInfo("Asia/Manila")` — consistent with the existing Manila-dated filename logic at `main.py:2033`.
- **High-risk callouts:** this becomes `build_supplier_pdf`'s second caller, so a future signature change now breaks two call sites — the test plan covers both paths. The script runs outside Flask entirely; it must not import `main`.

### Scope Boundaries

- Do NOT apply any date window to the report (R12 — this was explicitly decided).
- Do NOT read the `pdf_station_ids` cookie or accept a station filter (A9 — the scheduled report is always all stations).
- Do NOT send directly from the script (A10 — enqueue only).
- Do NOT implement catch-up or backfill for missed days (ARCH Out of Scope).
- Do NOT add an attachment-size fallback (ARCH Out of Scope).
- Only build the PDF and enqueue one row per active recipient.

### Files Expected

**New files:**
- `scripts/send_daily_report.py` (mirrors `scripts/backup_postgres.py`)
- `tests/test_daily_report.py`

**Modified files:**

_Scope amendment, agreed during implementation._ T4's attachment resolver only
understands `voucher_png:`, so as originally scoped the nightly email would have
gone out with no PDF attached. Option A was chosen: the cron writes the PDF to
disk and the worker reads it at send time, which keeps report building out of
`notifications.py` and leaves an artifact that can be re-read after the fact.

- `data_paths.py` (add the nightly report's path to the central registry)
- `notifications.py` (resolver branch for `daily_pdf:<date>`)
- `tests/test_data_paths.py`, `tests/test_notifications.py` (cover both)

**Must NOT modify:**
- `report_pdf.py` (`build_supplier_pdf` keeps its signature)
- `main.py` `/supplier-sheet.pdf` (the on-demand route is unchanged)
- `tests/test_supplier_exports_exclude_deleted.py` (its assertions must pass unchanged)

---

## Task T14: Bound pool construction so a dead database fails fast

> **Status:** not started
> **Verification:** tdd
> **Effort:** s
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** R7, N1
> **Footprint slice:** Modified: `db/pool.py` (bound `pool.wait()`) — an addition to ARCH's Change Footprint, discovered during T3
> **High-risk areas touched:** `db/pool.py` (H — every Postgres-touching module in the codebase goes through this singleton)

### Description

Discovered while implementing T3. `db/pool.py:63` calls `pool.wait()` with no argument at pool construction. That method's own `timeout` parameter defaults to 30 seconds and is independent of the `timeout=` passed to `ConnectionPool`, so the first caller to open the pool against an unreachable database blocks for ~30 seconds before anything raises.

This matters because `notifications.enqueue` runs inside `/register`, `/book`, and the approve handler. With `gunicorn --workers 1`, a Postgres outage means the first such request stalls the entire app for 30 seconds before the failure is swallowed — the exact stall N1 exists to prevent, and it contradicts ARCH's "Postgres unavailable when a customer registers" stress scenario, which assumed the enqueue returns promptly.

`db/pool.py` is shared by `audit_log.py`, `margin_store.py`, `discount_store.py`, `db/postgres_repo.py` and `notifications.py`, so this is a small change to a high-blast-radius file and gets its own task rather than riding along inside a feature task.

### Test Plan

#### Test File(s)
- `tests/test_pool.py` (new — no test file currently covers `db/pool.py` directly)
- `tests/test_notifications.py` (restore the timing test dropped from T3)

#### Test Scenarios

##### Fast Failure

- **construction against a dead database fails within the bound** — GIVEN a DSN pointing at an unreachable host WHEN `get_pool()` is called THEN it raises or returns within the configured bound (a few seconds), not ~30s _(verifies N1)_
- **the bound is configurable** — GIVEN an explicit wait timeout WHEN `get_pool` is called with it THEN that value is honored
- **enqueue gives up quickly** — GIVEN an unreachable database WHEN `notifications.enqueue` is called THEN it returns None in under 10 seconds _(this is the test T3 could not make pass; verifies R7, N1)_

##### Regression Guard

- **a healthy pool still opens and serves connections** — GIVEN a reachable database WHEN `get_pool()` is called THEN it returns a usable pool and a query succeeds _(guards every module depending on `db/pool.py`)_
- **the singleton still holds** — GIVEN the pool is already constructed WHEN `get_pool` is called again with a different DSN THEN the first pool is returned unchanged, as documented in its docstring
- **`reset_pool` still closes and clears** — GIVEN a constructed pool WHEN `reset_pool()` is called THEN a subsequent `get_pool` builds a new one _(the whole test suite depends on this)_
- **existing Postgres-backed suites still pass** — GIVEN the change WHEN `test_margin_store.py`, `test_discount_store.py`, `test_postgres_repo_integration.py` and `test_notifications.py` run THEN all pass unchanged _(guards the shared-singleton behavior)_

### Implementation Notes

- **Module(s):** `db/pool.py`.
- **Pattern reference:** the existing `get_pool` signature already threads `min_size`, `max_size` and `timeout` — add the wait bound the same way, with a module-level default.
- **Key decisions:** ARCH N1 (mail must never stall the request path) and A1 (the outbox exists so the request path stays fast — a 30s block at enqueue defeats it).
- **Libraries:** `psycopg_pool` — already a dependency.
- **High-risk callouts:** H-risk. Every Postgres-touching module shares this singleton, and the test suite's `reset_pool()` discipline depends on current behavior. Decide deliberately whether a failed `wait()` should raise or return a pool that retries in the background — the callers that swallow (`audit_log`, `notifications`) tolerate a raise, but `db/postgres_repo.py` may not. Check its call sites before choosing.

### Scope Boundaries

- Do NOT change the singleton semantics (first DSN wins) — the docstring documents it and the tests rely on it.
- Do NOT change `min_size`, `max_size`, or the default connection timeout.
- Do NOT introduce retry or reconnection logic beyond what `psycopg_pool` already does.
- Only bound the construction-time wait.

### Files Expected

**New files:**
- `tests/test_pool.py`

**Modified files:**
- `db/pool.py` (bound the `pool.wait()` call)
- `tests/test_notifications.py` (restore the timing test)

**Must NOT modify:**
- `audit_log.py`, `margin_store.py`, `discount_store.py`, `db/postgres_repo.py`, `notifications.py` (all consume the pool; their behavior must be unchanged)
- `tests/conftest.py`

---

## Task T15: Stop the worker racing the test suite, and stop it flooding the logs

> **Status:** done
> **Verification:** tdd
> **Effort:** xs
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** N5 (failures must stay diagnosable — they cannot be, buried under 17k lines a day)
> **Footprint slice:** Modified: `notifications.py` (log latch), `tests/conftest.py` (disable the worker in tests) — both discovered after T11
> **High-risk areas touched:** None

### Description

Two defects found after T11, both from T5's decision to call `start_worker()` at
`main.py` import time.

**The flake.** In a test process that worker is a second, uninvited drainer.
Several outbox tests monkeypatch `notifications.mailer` module-wide and point the
process-wide pool at the ephemeral test database, so the worker claims the very
rows a test is about to drain. `test_the_ladder_terminates_after_five_attempts`
failed intermittently — roughly 29 polls across a 145-second suite, each one a
chance to collide.

**The log flood.** `drain_once` printed "mailer is not configured" on every poll.
In a production environment whose API key was never set that is about 17,000
lines a day, which buries the failures N5 exists to keep diagnosable.

### Test Plan

#### Test File(s)
- `tests/test_notifications.py`

#### Test Scenarios

- **the warning is logged once, not per poll** — GIVEN an unconfigured mailer WHEN the worker polls five times THEN exactly one warning is emitted _(verifies N5)_
- **the transition back to configured is logged** — GIVEN the key is set after a period without one WHEN the next poll runs THEN "resuming sending" is logged, so suppression never hides the recovery
- **the warning returns after a recovery** — GIVEN configuration is lost again THEN it is news again, not suppressed forever
- **the worker does not start during the suite** — GIVEN conftest's setting WHEN `main` is imported THEN `start_worker()` returns False _(guards the flake from returning)_

### Implementation Notes

- **Key decisions:** A12 (`NOTIFICATIONS_ENABLED` is the existing kill switch, so tests reuse it rather than inventing a test-only code path in production).
- `conftest.py` sets it unconditionally, not via `setdefault`: docker-compose exports `NOTIFICATIONS_ENABLED=true` into the container the suite runs in, so a default would never apply.
- The tests that exercise `start_worker` set the variable themselves via monkeypatch, so their coverage is unaffected.

### Scope Boundaries

- Do NOT add test-awareness to production code (no `"pytest" in sys.modules` checks) — the existing feature flag already expresses this.
- Do NOT change the polling interval or the retry ladder.

### Files Expected

**Modified files:**
- `notifications.py` (log-once latch with an explicit recovery message)
- `tests/conftest.py` (disable the worker suite-wide)
- `tests/test_notifications.py` (four scenarios above)

**Must NOT modify:**
- `main.py` (the import-time call stays; the flag is the control)
