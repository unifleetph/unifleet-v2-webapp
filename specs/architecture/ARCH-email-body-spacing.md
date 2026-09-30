# Architecture: Fix Email Body Paragraph Spacing

> **Date:** 2026-09-30
> **Phase:** 2 of 5 (System Architecture)
> **Requirements source:** specs/requirements/REQ-email-body-spacing.md
> **Tasks:** TASKS-email-body-spacing.md
> **Type:** bugfix (lightweight — full architecture pass skipped per plan-architecture's own trivial-bugfix exception)

## Architecture Summary

Two of `notifications.py`'s three plain-text email body constants (`_ACCOUNT_CODE_BODY`, `_BOOKING_RECEIVED_BODY`) are missing the blank-line paragraph breaks their sibling `_BOOKING_CONFIRMED_BODY` already has. Fix is a string-literal edit in those two constants only, following `_BOOKING_CONFIRMED_BODY`'s existing spacing pattern exactly. No new module, no design decision, no data model — `mailer.py` already sends `body` verbatim as Resend's `text` field, so a plain-text spacing change is sufficient.

## Change Footprint

### Modified files / modules

| Path                                | What changes here                                                                                         |
|-------------------------------------|--------------------------------------------------------------------------------------------------------------|
| `notifications.py`                  | `_ACCOUNT_CODE_BODY` and `_BOOKING_RECEIVED_BODY` gain blank-line (`"\n"`) separators between paragraphs, matching `_BOOKING_CONFIRMED_BODY`'s pattern; `_BOOKING_RECEIVED_BODY`'s mashed sentence splits into its own paragraph |
| `tests/test_notifications.py`       | `test_account_code_copy_matches_the_brief` and `test_booking_received_copy_matches_the_brief` expected-body strings updated to match |

### Touched but not changed (silent-regression hotspots)

| Path                                | Why it matters                                                                 |
|-------------------------------------|----------------------------------------------------------------------------------|
| `notifications.py` (`_BOOKING_CONFIRMED_BODY`, `render_booking_confirmed`) | Reference pattern — must stay byte-identical (R4); `test_booking_confirmed_copy_matches_the_brief` guards it |
| `mailer.py` (`send`)                | Sends `body` as-is via `text` field — no HTML path exists, so a plain-text spacing fix is guaranteed to reach the customer the way the screenshots show it |
| `notifications.py` (`render_account_code` format call) | `{account_code}` substitution must keep working after the literal changes (R5); `test_account_code_is_the_only_interpolated_value` guards it |

## Areas of Impact

| Area                    | Impact                                  | Risk (L/M/H) | Why                                                                 |
|--------------------------|------------------------------------------|--------------|----------------------------------------------------------------------|
| `notifications.py` copy  | Two email bodies change visually          | L            | Plain string literal change, no logic touched, existing exact-match tests catch any mistake |
| `tests/test_notifications.py` | Two exact-match assertions updated  | L            | Tests already assert full-string equality; a missed edit fails loudly, not silently |

**Contract changes:** None — `render_account_code`/`render_booking_received` signatures and return shape (`subject, body` tuple) unchanged.

**Cross-cutting ripples:** None — no migration, no flag, no deploy step beyond the normal one.

## Risk & Stress-Test Scenarios

### Forward — runtime failure scenarios

| Scenario                                              | How the Design Handles It                                                        |
|---------------------------------------------------------|--------------------------------------------------------------------------------------|
| Blank line breaks `{account_code}` placeholder substitution | Guarded by existing `test_account_code_is_the_only_interpolated_value`; placeholder position unchanged, only surrounding lines get `\n` neighbors |

### Backward — regression risk per touched area

| Touched area (from Change Footprint)     | What could regress                                | How we'd know / mitigation                          |
|--------------------------------------------|------------------------------------------------------|---------------------------------------------------------|
| `_BOOKING_CONFIRMED_BODY`                  | Accidentally edited while "in the file"              | `test_booking_confirmed_copy_matches_the_brief` exact-match assertion fails immediately |
| `render_account_code` substitution         | Placeholder mis-positioned relative to new blank lines | `test_account_code_is_the_only_interpolated_value` and the updated exact-match test both fail if wrong |

## Out of Scope

- `daily_report.py` supplier email body (reason: different template shape, no "Hi,"/sign-off structure, not the reported bug)
- Any HTML-formatted email body (reason: `mailer.py` sends plain `text` only; no HTML field exists to add)
- Subject line changes (reason: not flagged as an issue)
