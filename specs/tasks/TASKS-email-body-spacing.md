# Tasks

## Task T1: Add paragraph spacing to Account Code and Booking Received email bodies

> **Status:** done
> **Verification:** tdd
> **Effort:** xs
> **Priority:** medium
> **Depends on:** None
> **Satisfies REQs:** R1, R2, R3, R4, R5
> **Footprint slice:** Modified: `notifications.py` (`_ACCOUNT_CODE_BODY`, `_BOOKING_RECEIVED_BODY`); Modified: `tests/test_notifications.py` (two expected-body assertions)
> **High-risk areas touched:** None — all Areas of Impact in ARCH are L risk

### Description

Add blank-line paragraph breaks to `_ACCOUNT_CODE_BODY` and `_BOOKING_RECEIVED_BODY` in `notifications.py` so both read with the same visual spacing as `_BOOKING_CONFIRMED_BODY` already has. `_BOOKING_RECEIVED_BODY`'s two-sentence mashed line also splits into its own paragraph. Both templates render as plain text via Resend (`mailer.py`'s `text` field) — no HTML involved.

### Test Plan

#### Test File(s)
- `tests/test_notifications.py` (existing file, two tests updated, two left untouched as regression guards)

#### Test Scenarios

##### Email copy — verbatim spacing

- **account code email has blank line after greeting and before sign-off** — GIVEN account code `HARR` WHEN `render_account_code("HARR")` is called THEN the body equals:
  ```
  Hi,

  Thank you for registering with UniFleet!
  Your Account Code is:
  HARR
  Please keep this code for your future UniFleet bookings and transactions.

  Thank you,
  UniFleet
  ```
  (blank line after "Hi,", the four registration lines stay tight together as one block — no blank lines *between* them — blank line before "Thank you,") _(verifies R1)_

- **booking received email has blank line after greeting, mashed sentence split into its own paragraph, blank line before sign-off** — GIVEN `render_booking_received()` is called THEN the body equals:
  ```
  Hi,

  We’ve received your UniFleet booking request.

  Your request is currently being reviewed and is not yet confirmed. You will receive another email once your booking has been confirmed.

  Please wait for our Confirmation and Fuel Voucher email before proceeding to the station.

  Thank you,
  UniFleet
  ```
  (three body paragraphs, each blank-line separated: the received-acknowledgement sentence, the review/not-yet-confirmed sentence pair as one paragraph, and the please-wait sentence) _(verifies R2, R3)_

##### Regression guard — reference template and substitution untouched

- **booking confirmed email body is unchanged** — GIVEN `render_booking_confirmed()` is called THEN the body still equals the existing `_BOOKING_CONFIRMED_BODY` string byte-for-byte (existing `test_booking_confirmed_copy_matches_the_brief`, left as-is) _(guards ARCH backward-regression risk for `_BOOKING_CONFIRMED_BODY`; verifies R4)_
- **account code substitution still the only interpolated value** — GIVEN two different account codes WHEN both are rendered THEN the bodies are identical with the code masked out, and neither contains a stray `{` or `}` (existing `test_account_code_is_the_only_interpolated_value`, left as-is) _(guards ARCH backward-regression risk for the `.format()` call; verifies R5)_

### Implementation Notes

- **Module(s):** `notifications.py` — module-level string constants `_ACCOUNT_CODE_BODY`, `_BOOKING_RECEIVED_BODY` (lines ~66–85 as of this writing)
- **Pattern reference:** `_BOOKING_CONFIRMED_BODY` (`notifications.py`) — the exact spacing convention to mirror: blank `"\n"` line as its own tuple element between paragraphs, no blank line inside the "Thank you,\nUniFleet" sign-off
- **Key decisions:** ARCH — plain-text spacing fix only, no HTML (`mailer.py` sends `text` field only); `_BOOKING_CONFIRMED_BODY` is the reference and must not change
- **Libraries:** None — no new dependency
- **High-risk callouts:** None

### Scope Boundaries

- Do NOT modify `_BOOKING_CONFIRMED_BODY` or `render_booking_confirmed` (ARCH: reference template, R4)
- Do NOT modify `daily_report.py` (ARCH Out of Scope: different template shape, no greeting/sign-off)
- Do NOT introduce HTML email bodies or touch `mailer.py`'s payload construction (ARCH Out of Scope: plain-text only)
- Do NOT change subject line constants (ARCH Out of Scope: not part of the reported bug)
- Only implement the blank-line spacing + paragraph split described in the Test Plan above — no other copy changes

### Files Expected

**Modified files:** _(from ARCH "Modified files / modules")_
- `notifications.py` (`_ACCOUNT_CODE_BODY`, `_BOOKING_RECEIVED_BODY` — add blank-line paragraph breaks per Test Plan)
- `tests/test_notifications.py` (`test_account_code_copy_matches_the_brief`, `test_booking_received_copy_matches_the_brief` — update expected-body strings to match)

**Must NOT modify:** _(from ARCH "Touched but not changed")_
- `notifications.py` (`_BOOKING_CONFIRMED_BODY`, `render_booking_confirmed`) — reference pattern, covered by `test_booking_confirmed_copy_matches_the_brief`
- `mailer.py` (`send`) — plain-text `text` field already correct, no change needed
- `daily_report.py` — out of scope, different template shape

---
_Status values: `not started` (defined, not picked up) | `in progress`
(implementation underway) | `done` (verification evidence produced) | `blocked`
(cannot proceed — see notes). The implement skill updates this field as it works._
