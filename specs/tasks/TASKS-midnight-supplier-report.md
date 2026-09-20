# Tasks

_Source: `specs/architecture/ARCH-midnight-supplier-report.md` · Requirements: `specs/requirements/REQ-midnight-supplier-report.md` · Generated 2026-09-20._

_Order: T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9. T3 and T4 both edit `notifications.py`, so they run one after the other. T3 must land before T6 (batch enqueue)._

_Cross-task decisions confirmed with the developer: Status column sits after Voucher ID; blank status shows as "Unverified"; blank amount cells are fine; the daily report passes no station filter; "sent late" wording appears when the report is queued more than 60 minutes after 00:00 Manila; all recipient rows for a day are queued in one transaction._

_Project conventions: pytest, `tests/test_<module>.py`. Tests that touch the outbox use the Postgres-backed `schema_db` fixture (`make test-db`); tests of `daily_report` stub the repo, recipients and outbox like `tests/test_daily_report.py`. Before any test run, confirm `.env` `DATABASE_URL` points at the local `db` container, never the shared Railway database (CLAUDE.md). Never insert a helper between an `@app.route` decorator and its `def` in `main.py`._

---

## Task T1: Supplier PDF options — Status column, "No orders" state, report-date label

> **Status:** done
> **Verification:** tdd
> **Effort:** s
> **Priority:** high
> **Depends on:** None
> **Satisfies REQs:** R3, R6, R8, R1
> **Footprint slice:** Modified: `report_pdf.py` (optional arguments for a Status column, a "No orders for [date]" message, a "Report for [date]" title; default output unchanged); `tests/test_report_pdf.py`
> **High-risk areas touched:** `report_pdf.py` (M) — shared with the on-demand `/supplier-sheet.pdf`; default output must not move.

### Description

The daily report needs a PDF that shows each order's status, says "No orders for [date]" on an empty day, and is labelled with the day it covers. These are opt-in options on the existing builder so the admin's on-demand supplier sheet stays exactly as it is.

### Test Plan

#### Test File(s)
- `tests/test_report_pdf.py`

#### Test Scenarios

##### Status column

- **status column position** — GIVEN the daily options WHEN the header and a row are built THEN Status sits after Voucher ID and before Name / Signature _(verifies R3)_
- **status text** — GIVEN vouchers with status Unverified, Unredeemed and Redeemed WHEN rows are built THEN each row shows its own status _(verifies R3)_
- **blank status shows Unverified** — GIVEN a voucher with a blank or missing status WHEN its row is built THEN the Status cell reads "Unverified" _(verifies R3, decision 2)_
- **blank amount** — GIVEN an Unverified voucher with no amount or price WHEN its row is built THEN it builds without error and the amount cell is blank _(verifies R3)_

##### Empty state and labelling

- **empty day message** — GIVEN zero orders and report date 2026-09-20 WHEN the PDF is built THEN it contains "No orders for 2026-09-20" and no dash-only placeholder row _(verifies R6)_
- **report label** — GIVEN a report date WHEN the title is built THEN it contains "Report for 2026-09-20" and does not use "Unredeemed Fuel Vouchers" _(verifies R8)_
- **valid PDF** — GIVEN the daily options with mixed statuses WHEN the PDF is built THEN the result is non-empty bytes starting with `%PDF` _(verifies R1)_

##### Regression Guard

- **default output unchanged** — GIVEN a call with none of the new arguments WHEN header, rows and title are built THEN they match today's seven columns and the "Unredeemed Fuel Vouchers" title _(guards ARCH backward-regression risk for `report_pdf.py`)_
- **existing tests stay green** — GIVEN the current `test_existing_columns_retain_values_and_order` and FAQ tests WHEN run unchanged THEN they pass _(guards the same risk)_

_Testability note: there is no PDF-text parser in the project. Test pure helpers (row builder, header, title and empty-message helpers) plus a smoke test on the bytes, as the existing tests do._

### Implementation Notes

- **Module(s):** `report_pdf.py`
- **Pattern reference:** `_build_supplier_row` and `build_supplier_pdf` in `report_pdf.py`; the existing pure-helper tests in `tests/test_report_pdf.py`.
- **Key decisions:** A6 (Status column), A7 ("No orders" replaces the dash row), A10 (report-date label), A12 (`/supplier-sheet.pdf` unchanged).
- **Libraries:** reportlab (already in use). No new dependency.
- **High-risk callouts:** the default call must keep producing today's header, rows and title; the two regression scenarios pin this. Column widths must be retuned only for the daily variant so the page still fits A4 landscape.

### Scope Boundaries

- Do NOT change `/supplier-sheet.pdf` or its default output (ARCH Out of Scope).
- Do NOT add pagination here; that is T2.
- Do NOT add station filtering, live-order selection or any repo access; that is T5.
- Only implement the three opt-in options on `build_supplier_pdf`.

### Files Expected

**New files:** none

**Modified files:**
- `report_pdf.py` (optional Status column, "No orders for [date]" state, "Report for [date]" title; default unchanged)
- `tests/test_report_pdf.py`

**Must NOT modify:**
- `main.py` `/supplier-sheet.pdf` (silent-regression hotspot — covered by the default-output regression tests)
- `scripts/send_daily_report.py` (belongs to T7)

---

## Task T2: Supplier PDF pagination

> **Status:** done
> **Verification:** test-after
> **Effort:** m
> **Priority:** high
> **Depends on:** T1
> **Satisfies REQs:** R3
> **Footprint slice:** Modified: `report_pdf.py` (table splits across pages); `tests/test_report_pdf.py`
> **High-risk areas touched:** `report_pdf.py` (M) — also used by `/supplier-sheet.pdf`.

### Description

The current builder draws the table once on one page, so a long list runs off the bottom and later rows silently vanish. The daily report will hold every order ever (Redeemed included), so the table must split across pages with no lost rows.

### Test Plan

#### Test File(s)
- `tests/test_report_pdf.py`

#### Test Scenarios

##### Pagination

- **multi-page output** — GIVEN 200 orders WHEN the PDF is built THEN it has more than one page, counted from the PDF's page markers _(verifies R3)_
- **no lost rows** — GIVEN 200 orders WHEN the PDF is built THEN all 200 rows are handed to the table _(verifies R3)_

##### Regression Guard

- **short list keeps today's layout** — GIVEN about 10 orders WHEN the PDF is built THEN it is the same two pages as today (table on page 1, FAQ spilling onto page 2) and the drawn content is identical to before the change _(guards ARCH backward-regression risk for `report_pdf.py`)_. _Corrected during implementation: the plan said "one page", but the FAQ already pushes a 10-row sheet onto a second page._
- **on-demand sheet** — GIVEN the default call used by `/supplier-sheet.pdf` WHEN built with a long list THEN it paginates too and the existing tests pass _(guards the same risk)_

### Verification Checklist

- **Generate a 300-row sample PDF and open it** — expected: the header row repeats at the top of every page
- **Scroll to the last page** — expected: the final order row is visible and nothing is cut off at the bottom
- **Check the end of the document** — expected: the FAQ section follows the table without overlapping it

_Testability note: page count comes from the PDF bytes (verify in implementation that compression doesn't hide the page markers); row completeness is asserted on the row list given to the table._

### Implementation Notes

- **Module(s):** `report_pdf.py`
- **Pattern reference:** the current single-page table drawing and the FAQ `ensure_space` logic in `report_pdf.py`.
- **Key decisions:** none from the Decisions Log directly; this closes a gap found during task generation (all-time report vs. a one-page table). It supports A5.
- **Libraries:** reportlab (already in use).
- **High-risk callouts:** the on-demand sheet shares this builder, so the short-list and default-call regression scenarios must pass before the change is accepted.

### Scope Boundaries

- Do NOT redesign the page layout or FAQ content (REQ Out of Scope).
- Do NOT cap or page-limit the report; a size cap is deferred (ARCH Out of Scope).
- Only implement table splitting with a repeated header.

### Files Expected

**New files:** none

**Modified files:**
- `report_pdf.py` (table splits across pages, header repeats)
- `tests/test_report_pdf.py`

**Must NOT modify:**
- `main.py` `/supplier-sheet.pdf` (silent-regression hotspot — covered by regression guards)

---

## Task T3: Outbox additions — periodic tick and atomic batch enqueue

> **Status:** done
> **Verification:** tdd
> **Effort:** m
> **Priority:** high
> **Depends on:** T2
> **Satisfies REQs:** R11, R9, N1
> **Footprint slice:** Modified: `notifications.py` (worker loop runs registered periodic ticks; `register_periodic_task`; batch enqueue in one transaction); `tests/test_notifications.py`
> **High-risk areas touched:** Outbox worker (`notifications.py`) (M) — shared with every customer email.

### Description

The daily report will be scheduled by the web process's own worker. This adds a way to register a periodic task into the worker loop, and a batch enqueue that writes all recipients' rows for a day in one transaction, so a crash can't leave a day half-queued.

### Test Plan

#### Test File(s)
- `tests/test_notifications.py` (Postgres-backed `schema_db` fixture where the outbox is involved)

#### Test Scenarios

##### Periodic tick

- **tick runs from the worker loop** — GIVEN a registered tick and an interval WHEN the worker loop iterates past the interval THEN the tick runs about once per interval _(verifies R11)_
- **tick errors are contained** — GIVEN a tick that raises WHEN the worker loop iterates THEN the error is logged and both the loop and sending continue _(verifies N1)_
- **kill switch** — GIVEN `NOTIFICATIONS_ENABLED` off WHEN the worker is started THEN no worker and no tick run _(verifies REQ edge case: kill switch)_

##### Batch enqueue

- **atomic** — GIVEN rows for several recipients WHEN one insert fails THEN none of the rows persist _(verifies ARCH A3 safety)_
- **idempotent** — GIVEN some dedupe keys already exist WHEN the batch is queued THEN existing ones are skipped and the returned count is the number newly queued _(verifies R9)_

##### Regression Guard

- **customer email paths** — GIVEN account code, booking received and booking confirmed emails WHEN enqueued and drained as before THEN behaviour is unchanged _(guards ARCH backward-regression risk for `notifications.py` customer email paths)_

### Implementation Notes

- **Module(s):** `notifications.py`
- **Pattern reference:** `register_attachment_resolver` and `_drain_forever` in `notifications.py`; the existing worker and enqueue tests in `tests/test_notifications.py`.
- **Key decisions:** A2 (register, don't import — `notifications.py` must not import the repo or PDF builder), A1 (schedule inside the worker), A3 (atomic queuing supports the "day done" rule).
- **Libraries:** none new.
- **High-risk callouts:** this file is shared with every customer email. A tick must never raise into the loop, and nothing here may change single `enqueue` or `drain_once` behaviour; the regression scenario pins it.

### Scope Boundaries

- Do NOT change retry behaviour; that is T4.
- Do NOT import the repo, recipients or PDF builder into `notifications.py` (ARCH A2).
- Do NOT add a new database table or column (no schema change).
- Only implement the periodic-task registration and the batch enqueue.

### Files Expected

**New files:** none

**Modified files:**
- `notifications.py` (`register_periodic_task`, worker loop runs ticks, batch enqueue)
- `tests/test_notifications.py`

**Must NOT modify:**
- `db/schema.sql` (no schema change expected)
- `report_recipients.py` (read by the tick later, unchanged)

---

## Task T4: Retry rules for daily reports

> **Status:** done
> **Verification:** tdd
> **Effort:** m
> **Priority:** high
> **Depends on:** T3
> **Satisfies REQs:** R7, R8, R1
> **Footprint slice:** Modified: `notifications.py` (`_record_failure` keeps daily reports retrying with a 10-minute cap; a failed daily PDF build becomes a retry, not an email without the PDF); `tests/test_notifications.py`
> **High-risk areas touched:** Outbox worker (`notifications.py`) (M) — the retry ladder is shared with customer emails.

### Description

Today a failed send goes terminal after 6 attempts (about 12.5 minutes), and a PDF that can't be built still sends an email without it. Daily reports must instead retry through outages until they succeed, and must never go out without their PDF. Customer emails keep today's behaviour.

### Test Plan

#### Test File(s)
- `tests/test_notifications.py` (Postgres-backed `schema_db` fixture)

#### Test Scenarios

##### Daily report retries

- **5xx keeps retrying** — GIVEN a daily report and 5xx failures WHEN 6 attempts have failed THEN the row is still queued, not failed _(verifies R7)_
- **delay is capped** — GIVEN a daily report past the ladder WHEN the next attempt is scheduled THEN the delay is 600 seconds _(verifies R7)_
- **no response is retried** — GIVEN a status code 0 failure on a daily report WHEN recorded THEN the row is requeued _(verifies R7)_
- **rejected address is terminal** — GIVEN a 4xx on a daily report WHEN recorded THEN the row is failed immediately _(verifies REQ edge case: rejected address)_
- **no attachment-less report** — GIVEN the daily PDF can't be built at send time WHEN the row is processed THEN it is retried and no email is sent _(verifies ARCH A9)_
- **late label preserved** — GIVEN a daily report for 2026-09-20 retried after the Manila date changes WHEN sent THEN its PDF is still labelled 2026-09-20 _(verifies R8)_

##### Regression Guard

- **customer emails still exhaust** — GIVEN account code, booking received and booking confirmed emails WHEN 6 attempts fail THEN they go terminal as today _(guards ARCH backward-regression risk for `notifications._record_failure`)_
- **customer missing attachment** — GIVEN a customer voucher email with a missing attachment WHEN sent THEN it still sends and is flagged _(guards the same risk; A9 applies to daily reports only)_

### Implementation Notes

- **Module(s):** `notifications.py`
- **Pattern reference:** `_record_failure`, `_resolve_attachment` and the ladder tests (`test_the_ladder_terminates_after_five_attempts`, `test_a_missing_attachment_still_sends_and_flags`) in `tests/test_notifications.py`.
- **Key decisions:** A8 (retry without limit, 10-minute cap; 4xx terminal), A9 (a failed PDF build is a retry), A10 (late label carried by the attachment reference date).
- **Libraries:** none new.
- **High-risk callouts:** the retry rule applies to daily reports only, identified by their `daily_pdf:` attachment reference or `daily:` dedupe key. Both customer regression scenarios pin the unchanged behaviour. An unconfigured mailer still leaves rows queued without consuming attempts.

### Scope Boundaries

- Do NOT change retry behaviour for account code, booking received or booking confirmed emails.
- Do NOT add admin alert emails on failure (ARCH Out of Scope).
- Do NOT add a daily-report resend button (ARCH Out of Scope).
- Only implement the daily-report retry rule and the A9 behaviour.

### Files Expected

**New files:** none

**Modified files:**
- `notifications.py` (`_record_failure` and send-time attachment handling for daily reports)
- `tests/test_notifications.py`

**Must NOT modify:**
- `notifications.py` customer email rendering and enqueue paths (silent-regression hotspot — covered by regression guards)

---

## Task T5: `daily_report.py` — live-order rule and PDF build

> **Status:** done
> **Verification:** tdd
> **Effort:** s
> **Priority:** high
> **Depends on:** T1, T2
> **Satisfies REQs:** R3, R4, R5, R6, R8
> **Footprint slice:** New: `daily_report.py` (`live_orders`, `manila_date`, `build_pdf`); `tests/test_daily_report_tick.py`
> **High-risk areas touched:** Supplier-facing content (M) — the report now includes Unverified and Redeemed orders.

### Description

One module defines what counts as a live order and builds the daily PDF from it. Today that rule is written in three places; this becomes the single source. The daily PDF includes every non-deleted order of any listed status, over all time, from any station.

### Test Plan

#### Test File(s)
- `tests/test_daily_report_tick.py` (stubbed repo, like `tests/test_daily_report.py`)

#### Test Scenarios

##### Live-order rule

- **statuses** — GIVEN orders that are deleted, Unverified, Unredeemed and Redeemed WHEN live orders are selected THEN deleted ones are excluded and the other three are included _(verifies R4)_
- **blank status** — GIVEN an order with a blank status WHEN selected THEN it is included and shown as Unverified _(verifies R4, decision 2)_
- **no date window** — GIVEN an order from a year ago WHEN selected THEN it is included _(verifies R3)_
- **any station** — GIVEN an order with a blank or unknown station WHEN the PDF is built THEN it still appears _(verifies R3)_
- **no payment filter** — GIVEN orders that used a voucher WHEN selected THEN they are included _(verifies R5)_

##### PDF build

- **labels** — GIVEN a report date WHEN the PDF is built THEN the title says "Report for [date]", and with zero orders it says "No orders for [date]" _(verifies R6, R8)_

##### Regression Guard

- **on-demand sheet** — GIVEN the existing `/supplier-sheet.pdf` route WHEN called THEN it still uses only Unredeemed, undeleted orders _(guards ARCH backward-regression risk for `main.py`)_

### Implementation Notes

- **Module(s):** `daily_report.py`
- **Pattern reference:** `live_vouchers` and `build_report` in `scripts/send_daily_report.py`; `_build_daily_report_pdf` in `main.py`.
- **Key decisions:** A5 (live = not deleted, Unverified / Unredeemed / Redeemed, all time), A6, A7, A12. The daily build passes no station filter.
- **Libraries:** none new; uses `report_pdf` from T1 and T2.
- **High-risk callouts:** the report content changes for recipients. The Status column and the blank-status rule make that visible; scenarios pin who is in and out.

### Scope Boundaries

- Do NOT change `/supplier-sheet.pdf` or its Unredeemed-only rule (ARCH Out of Scope).
- Do NOT add a date window or size cap (ARCH Out of Scope).
- Do NOT queue anything or touch the outbox; that is T6.
- Only implement the live-order rule, the Manila date and the PDF build.

### Files Expected

**New files:**
- `daily_report.py` (live-order rule, Manila date, PDF build; extracted from `scripts/send_daily_report.py` and `main.py`)
- `tests/test_daily_report_tick.py`

**Modified files:** none

**Must NOT modify:**
- `main.py` `/supplier-sheet.pdf` (silent-regression hotspot)
- `tests/test_supplier_exports_exclude_deleted.py` (guards the deleted-order exclusion the new rule must keep)

---

## Task T6: `daily_report.py` — `ensure_today_queued`

> **Status:** not started
> **Verification:** tdd
> **Effort:** m
> **Priority:** high
> **Depends on:** T3, T5
> **Satisfies REQs:** R1, R2, R7, R9, R10, R8, N1
> **Footprint slice:** Modified: `daily_report.py` (`ensure_today_queued`); `tests/test_daily_report_tick.py`
> **High-risk areas touched:** Web process load (M) — the all-time PDF is built inside the web worker's thread.

### Description

The idempotent tick body: if today's Manila-date report hasn't been queued, queue one email per active recipient, all in one transaction. It catches up after downtime, waits quietly on an empty list, and never raises. A report queued more than 60 minutes after 00:00 Manila says so in its email.

### Test Plan

#### Test File(s)
- `tests/test_daily_report_tick.py` (stubbed repo, recipients and outbox; Postgres-backed `schema_db` for the atomic batch)

#### Test Scenarios

##### Queuing

- **one per recipient** — GIVEN recipients and no report yet today WHEN the tick runs THEN one row per recipient is queued with key `daily:D:<recipient>` and attachment ref `daily_pdf:D` _(verifies R1)_
- **repeat is a no-op** — GIVEN today's report is already queued WHEN the tick runs again THEN nothing new is queued _(verifies R9)_
- **empty day still queued** — GIVEN zero live orders WHEN the tick runs THEN the report is still queued _(verifies R2, R6)_

##### Recipient list

- **late recipient waits** — GIVEN today's report is queued and a recipient is added afterwards WHEN the tick runs THEN the new recipient gets nothing until the next day _(verifies R10)_
- **empty list** — GIVEN no active recipients WHEN the tick runs THEN nothing is queued and nothing raises, and once a recipient is added later that day the next tick queues that day's report _(verifies R10)_

##### Resilience and dates

- **PDF pre-flight fails** — GIVEN the PDF can't be built WHEN the tick runs THEN nothing is queued, it returns quietly, and the next tick retries _(verifies R7)_
- **Manila date rule** — GIVEN 15:59 UTC and 16:00 UTC WHEN the date is read THEN the first is the earlier Manila day and the second the next _(verifies REQ edge case: Manila date)_
- **no backfill** — GIVEN several days with no report WHEN the tick runs THEN only today's report is queued _(verifies ARCH A4)_
- **sent-late wording** — GIVEN a report queued 61 minutes after 00:00 Manila WHEN the email body is built THEN it says it was sent late and names the day; queued at 00:05 it does not _(verifies R8)_

### Implementation Notes

- **Module(s):** `daily_report.py`
- **Pattern reference:** `main()` in `scripts/send_daily_report.py`; `dedupe_daily_report` and `enqueue` in `notifications.py`; the batch enqueue from T3.
- **Key decisions:** A3 (day done once any row exists for the Manila date), A4 (no backfill), A11 (empty list keeps checking), A13 (first tick after deploy queues today's report), A10 (label).
- **Libraries:** none new.
- **High-risk callouts:** the tick runs every minute in the web process, so the "already queued today" check must be cheap and only build the PDF when a report is actually due. Log the PDF size in bytes each build.

### Scope Boundaries

- Do NOT change what `notifications.py` does on send or retry (T3, T4).
- Do NOT add a run-marker table or any schema change (ARCH A3).
- Do NOT backfill missed days or add a resend button (ARCH Out of Scope).
- Only implement `ensure_today_queued` and the sent-late wording.

### Files Expected

**New files:** none

**Modified files:**
- `daily_report.py` (`ensure_today_queued`)
- `tests/test_daily_report_tick.py`

**Must NOT modify:**
- `report_recipients.py` (read only; silent-regression hotspot)
- `db/schema.sql` (no schema change)

---

## Task T7: Wiring — `main.py` and the script wrapper

> **Status:** not started
> **Verification:** test-after
> **Effort:** s
> **Priority:** high
> **Depends on:** T4, T6
> **Satisfies REQs:** R1, R2, R3, R9, R11
> **Footprint slice:** Modified: `main.py` (`_build_daily_report_pdf` delegates to `daily_report`; registers the tick beside the resolver); `scripts/send_daily_report.py` (thin wrapper); `tests/test_daily_report.py`
> **High-risk areas touched:** Outbox worker (`notifications.py`) (M) — wiring starts the tick in production; `main.py` route decorators.

### Description

Connect the pieces: the app registers the tick and the send-time PDF builder, so the report runs with no separate cron service. The old script stays as a manual trigger with the same exit codes.

### Test Plan

#### Test File(s)
- `tests/test_daily_report.py` (updated)

#### Test Scenarios

##### Wiring

- **send-time builder** — GIVEN the resolver called with 2026-09-20 WHEN it runs THEN it returns `UniFleet_Supplier_Sheet_2026-09-20.pdf` and includes Redeemed orders _(verifies R3)_
- **registration** — GIVEN the app imported WHEN checked THEN exactly one tick and the `daily_pdf` resolver are registered _(verifies R11)_
- **no cron service needed** — GIVEN no separate scheduled service WHEN the registered tick is called THEN today's report is queued _(verifies R11)_

##### Manual script

- **exit codes** — GIVEN no `DATABASE_URL` or no `PERSISTENCE_BACKEND` WHEN the script runs THEN it exits 1, and a PDF failure exits 2; `--dry-run` writes nothing _(guards ARCH touched-file `scripts/send_daily_report.py`)_
- **repeat run** — GIVEN the script already ran today WHEN it runs again THEN nothing new is queued _(verifies R9)_

##### Regression Guard

- **routes still bound** — GIVEN the recipients and `/supplier-sheet.pdf` routes WHEN requested THEN each reaches its own handler _(guards the `main.py` decorator gotcha)_
- **no double email** — GIVEN the tick and the script both run on the same day WHEN both complete THEN each recipient has one row _(guards ARCH backward-regression risk: duplicate report on rollout)_

### Implementation Notes

- **Module(s):** `main.py`, `scripts/send_daily_report.py`
- **Pattern reference:** the existing `register_attachment_resolver("daily_pdf", ...)` block in `main.py`; `tests/test_daily_report.py`.
- **Key decisions:** A1, A2, A13. `NOTIFICATIONS_ENABLED` off means no worker and no tick; that is intended.
- **Libraries:** none new.
- **High-risk callouts:** do not insert a helper between an `@app.route` decorator and its `def` in `main.py` (CLAUDE.md gotcha). The registration must stay guarded so an email import failure still can't stop the app booting.

### Scope Boundaries

- Do NOT change `/supplier-sheet.pdf` (ARCH Out of Scope).
- Do NOT add routes or admin controls for the report.
- Only wire the resolver and tick, and slim the script to call `daily_report`.

### Files Expected

**New files:** none

**Modified files:**
- `main.py` (`_build_daily_report_pdf` delegates to `daily_report`; tick registered beside the resolver)
- `scripts/send_daily_report.py` (thin wrapper keeping `--dry-run` and exit codes)
- `tests/test_daily_report.py`

**Must NOT modify:**
- `main.py` `/supplier-sheet.pdf` route (silent-regression hotspot)
- `tests/test_supplier_exports_exclude_deleted.py` (covered by regression guards)

---

## Task T8: Empty-recipient warning on the admin recipients page

> **Status:** not started
> **Verification:** ui
> **Effort:** xs
> **Priority:** medium
> **Depends on:** None
> **Satisfies REQs:** R10, N1
> **Footprint slice:** Modified: `main.py` (recipients route passes the warning), `templates/admin_recipients.html` (warning)
> **High-risk areas touched:** Admin recipients page (L) — template only.

### Description

If the recipient list is empty, no report can be sent, and silence is exactly how this bug looked to the user. Admins see a clear warning on the recipients page so they can fix it.

### Verification Checklist

- **Open `/admin/recipients` with zero active recipients, desktop width** — expected: a visible warning says the daily report has nobody to send to
- **Same page at 375px width** — expected: the warning is readable with no horizontal scroll
- **Add one active recipient and reload** — expected: the warning is gone
- **Deactivate the only recipient** — expected: the warning returns
- **Add and remove a recipient as before** — expected: both work exactly as they do today
- **Load the page with email support unavailable** — expected: the existing "Recipient management is unavailable" message still shows

#### Testable Seams

- The route passes an "empty list" flag to the template
- The template renders the warning only when the flag is set
- The warning text is present and readable (basic a11y: it is real text, not only colour)

### Implementation Notes

- **Module(s):** `main.py` (recipients route), `templates/admin_recipients.html`
- **Pattern reference:** existing flash and notice patterns in `templates/admin_recipients.html`; `tests/test_report_recipients.py`.
- **Key decisions:** A11 (empty list warns admins).
- **Libraries:** none.
- **High-risk callouts:** none beyond the route-decorator gotcha in `main.py`.

### Scope Boundaries

- Do NOT add email alerts or other notification of an empty list (ARCH Out of Scope).
- Do NOT change how recipients are added, removed or stored.
- Only implement the warning.

### Files Expected

**New files:** none

**Modified files:**
- `main.py` (recipients route passes the empty-list flag)
- `templates/admin_recipients.html` (warning block)

**Must NOT modify:**
- `report_recipients.py` (silent-regression hotspot — read only)

---

## Task T9: Docs and rollout

> **Status:** not started
> **Verification:** checklist
> **Effort:** s
> **Priority:** medium
> **Depends on:** T7, T8
> **Satisfies REQs:** N3, R11
> **Footprint slice:** Modified: `docs/runbook.md`, `rw.txt` (comment), `AGENTS.md`
> **High-risk areas touched:** Operations runbook (L).

### Description

Retire the "create a Railway `daily-report` service" instructions, since the app now schedules the report itself. Then prove the fix in the real environment, because the report has never once worked in production.

### Verification Checklist

- **Search `docs/runbook.md`, `rw.txt` and `AGENTS.md` for the old cron-service provisioning steps** — expected: none remain; any mention describes the script as a manual trigger only
- **Read the runbook's report section** — expected: it says how the in-app schedule works, how to check it, and that `NOTIFICATIONS_ENABLED` off stops the report on purpose
- **Read the `AGENTS.md` rollout order** — expected: it no longer says to provision a cron service
- **Confirm `.env` `DATABASE_URL` targets the local `db` container** — expected: not the shared Railway database
- **`make test-db`** — expected: all tests pass, with no skipped DB tests
- **`python scripts/verify_build.py`** — expected: exits 0
- **Deploy to `dev`, wait for the first tick** — expected: each recipient gets one report email with the PDF, and no second copy within an hour (N3, part 1)
- **Open the PDF** — expected: Status column, all live orders, and a "Report for [date]" title
- **Next real 00:00 Manila** — expected: exactly one email per recipient, with no manual step (N3, part 2)

### Implementation Notes

- **Module(s):** docs and config comments only.
- **Pattern reference:** the current "The `daily-report` cron service" section in `docs/runbook.md` (lines 62–89) and the comment at the bottom of `rw.txt`.
- **Key decisions:** A1, A13.
- **High-risk callouts:** a stale instruction could lead someone to create the old cron service too. That is harmless because of dedupe, but the docs should not suggest it.

### Scope Boundaries

- Do NOT change application code in this task.
- Do NOT delete `scripts/send_daily_report.py`; it stays as a manual trigger.
- Only update documentation, the `rw.txt` comment and the verification steps.

### Files Expected

**New files:** none

**Modified files:**
- `docs/runbook.md` (replace the cron-service section with the in-app schedule and how to check it)
- `rw.txt` (rewrite the comment about the dashboard cron service)
- `AGENTS.md` (update the "Email notifications" paragraph and the rollout order)

**Must NOT modify:**
- Any `.py` file (application code belongs to T1–T8)

---

_Status values: `not started` | `in progress` | `done` | `blocked`. The implement skill updates this field as it works._
