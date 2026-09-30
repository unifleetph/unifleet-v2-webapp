# Requirements: Fix Email Body Paragraph Spacing

> **Date:** 2026-09-30
> **Type:** bugfix
> **Source:** docs/Bugfix_email_body.md + screenshots (email_body_1.jpeg, email_body_2.jpeg, email_body_3.jpeg)
> **Phase:** 1 of 5 (Requirement Engineering)

## Summary

Two of the three customer notification emails (Account Code, Booking Request Received) render with no blank line between paragraphs — greeting, body, and sign-off all run together, hard to read. The third (Booking Confirmed) already has correct paragraph spacing and serves as the reference. Fix: add blank-line paragraph breaks to the two cramped templates so all three read consistently.

## Problem & Motivation

Customers screenshotted their inbox and flagged the Account Code and Booking Request Received emails as visually cramped compared to the Booking Confirmed email, which reads cleanly. Trigger: customer complaint captured in `docs/Bugfix_email_body.md`. Left unfixed, customers continue to receive polished-looking confirmation emails alongside two others that look unfinished or broken, undermining trust in the notification system generally.

## Users & Consumers

- Customer receiving UniFleet transactional email — needs paragraphs visually separated so the email reads as clearly as the Booking Confirmed email already does.

## Functional Requirements

| ID  | Requirement                                                                                                   | Acceptance Criterion                                                                                          |
|-----|-----------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| R1  | Account Code email body has a blank line after "Hi," and a blank line before the "Thank you," sign-off block. | Inbox view of the Account Code email shows visual gaps matching Booking Confirmed's spacing pattern.        |
| R2  | Booking Received email body has a blank line after "Hi,", and a blank line before the "Thank you," sign-off.  | Inbox view of the Booking Received email shows visual gaps matching Booking Confirmed's spacing pattern.    |
| R3  | The mashed sentence in Booking Received ("...not yet confirmed." immediately followed by "You will receive...") becomes its own paragraph, blank-line separated from the preceding sentence. | Inbox view shows "We've received your booking request..." and "Your request is currently being reviewed... You will receive another email..." as two visually distinct paragraphs. |
| R4  | Booking Confirmed email body is unchanged.                                                                     | Byte-for-byte identical body output before and after the fix.                                               |
| R5  | Account code substitution (`{account_code}`) still renders correctly after the spacing change.                | Sending the Account Code email with a sample code shows the code on its own line, unchanged from today.     |

## Non-Functional Requirements

None — this is a plain-text copy change with no performance, security, or reliability surface.

## Behaviors & Domain Rules

**Spacing pattern (from the Booking Confirmed reference template):**
- Blank line after the opening "Hi,"
- Blank line between each distinct paragraph/topic
- Blank line before the closing "Thank you," sign-off
- No blank line *within* the sign-off itself ("Thank you," and "UniFleet" stay on adjacent lines)

**Why this rule matters:**
- All three customer-facing emails should read as one consistent product, not two rushed ones and one polished one.
- The sign-off convention (no internal blank line) must stay consistent across all three so the fix doesn't introduce a new inconsistency while fixing an old one.

**Common mistakes:**
- Adding a blank line inside the "Thank you,\nUniFleet" sign-off (over-applying the pattern).
- Fixing only the visually worst offender (Booking Received) and missing Account Code, which has the same defect.
- Touching Booking Confirmed's body "while in there," introducing an unintended diff in the one template that's already correct.

## Edge Cases & Failure Modes

| Scenario                                                                 | Decision                                                                 | Rationale                                                                 |
|---------------------------------------------------------------------------|---------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| `daily_report.py` supplier email (no "Hi," greeting, different shape)     | Out of scope — not touched                                                | Different template, not part of the reported bug, no greeting/sign-off structure to fix |
| Account code placeholder substitution breaks if blank lines added around `{account_code}` incorrectly | Substitution must be verified post-edit to still resolve correctly       | R5 — regression risk if `.format()` call or placeholder position shifts |
| Email client collapses blank lines differently (e.g. some strip repeated newlines) | Not addressed here — rely on the same plain-text mechanism Booking Confirmed already uses successfully | Booking Confirmed's identical blank-line technique already renders correctly per screenshot 3, so the same technique is proven to work |

## Decisions Log

| #   | Decision                                                                 | Alternatives Considered                          | Chosen Because                                                              |
|-----|-----------------------------------------------------------------------------|---------------------------------------------------|--------------------------------------------------------------------------------|
| 1   | Fix all 3 "Hi," notification templates as one unit, using Booking Confirmed as the spacing reference | Fix only the 2 templates shown cramped in screenshots | Booking Confirmed already demonstrates the correct pattern; using it as reference keeps all 3 consistent |
| 2   | Fix via plain-text blank lines (`\n\n`), not HTML markup                | HTML `<p>` tags / CSS                            | `mailer.py` sends only a `text` field to Resend — no HTML body exists to style |
| 3   | Split Booking Received's mashed two-sentence line into its own paragraph | Leave the sentence as one merged block, just fix outer spacing | User confirmed the mid-sentence concatenation should become a distinct paragraph |
| 4   | Sign-off block ("Thank you,\nUniFleet") keeps its existing tight single-newline spacing | Blank line before "UniFleet" too, for full consistency | Matches Booking Confirmed's own sign-off, which has no internal blank line either |
| 5   | `daily_report.py` supplier report email excluded from scope             | Include it since it's also a notification email  | No "Hi," greeting/sign-off structure — not the bug being reported |

## Scope Boundaries

### In Scope
- `_ACCOUNT_CODE_BODY` in `notifications.py` — add paragraph spacing
- `_BOOKING_RECEIVED_BODY` in `notifications.py` — add paragraph spacing, split mashed sentence into own paragraph
- Verifying `_BOOKING_CONFIRMED_BODY` is left untouched

### Out of Scope
- `daily_report.py` supplier report email body (reason: different template shape, no greeting/sign-off, not the reported bug)
- Any move to HTML-formatted email bodies (reason: `mailer.py`/Resend payload currently plain-text only; out of scope for a spacing fix)
- Subject lines (reason: not flagged as an issue; only body spacing was reported)

## Open Questions

None — all decisions confirmed by user in this session.

---
_This requirements document is the input for the **plan-architecture** skill._
_Next step: `/plan-architecture from: specs/requirements/REQ-email-body-spacing.md`_
