# Requirements: Customer Email Notifications & Daily Internal Supplier PDF

> **Date:** 2026-09-07
> **Type:** feature
> **Source:** docs/Brief-11_email.md (client brief, "Email workflow")
> **Phase:** 1 of 5 (Requirement Engineering)

## Summary

UniFleet currently sends no email at all. Customers register, book fuel, and get approved without any written confirmation, so they learn their account code and their voucher only by looking at the screen at the moment of the action. This REQ introduces outbound email: three customer-facing notifications (account code on registration, booking-received acknowledgement, booking-confirmed with the fuel voucher attached) and one internal notification (the supplier PDF emailed to UniFleet staff every day at midnight Manila time).

Email sending must never block the business flow. Registration, booking, and admin approval all succeed even when the mail provider is down; every failed notification is flagged in the admin UI with a Resend button so staff can recover it.

## Problem & Motivation

**Trigger:** client brief (docs/Brief-11_email.md). Customers have complained that they have no record of their account code and no proof of a confirmed booking, and staff currently have to remember to pull the supplier PDF by hand each night.

**Who benefits:**
- **Customers** get a durable record of their account code, an acknowledgement that their booking was received (and is *not yet* confirmed), and their voucher as an email attachment they can show at the station.
- **UniFleet staff** stop hand-pulling the nightly supplier sheet and stop fielding "what was my account code again?" support messages.
- **Station operations** benefit because the confirmed email restates the redemption requirements (voucher, valid ID, registered vehicle) and the 48-hour redemption window.

**If we don't do it:** account codes keep getting lost, customers keep arriving at stations before confirmation because nothing told them to wait, and the nightly supplier sheet remains a manual chore that gets skipped.

## Users & Consumers

- **Customer (fleet operator)** — needs their account code in writing, needs to know a booking was received but not yet confirmed, and needs the voucher image to redeem at a station.
- **UniFleet admin** — needs to see which notifications failed, resend them, fix a missing customer email, and manage who receives the nightly internal PDF.
- **UniFleet internal staff (report recipients)** — need the live supplier PDF in their inbox daily without asking anyone.
- **Email service provider** — an external transactional email API; an outbound-only integration.

## Functional Requirements

| ID | Requirement | Acceptance Criterion |
|----|-------------|----------------------|
| R1 | On successful registration, the system emails the new customer their 4-letter account code, using the brief's copy verbatim. | Submitting the registration form with a valid email results in exactly one email to that address with subject `UniFleet Account Code` and a body matching the brief's copy, with `[XXXX]` replaced by the customer's actual account code. |
| R2 | Email address is a required field on the registration form. | Submitting registration with a blank or malformed email re-renders the form with a field-level error, keeps the other entered values, and creates no customer record. |
| R3 | On successful booking submission, the system emails the booking's customer a "request received" acknowledgement, using the brief's copy verbatim. | Submitting a booking for an account code whose customer has an email results in exactly one email with subject `Booking Request Received - UniFleet` and the brief's body text, and the copy explicitly states the booking is not yet confirmed. |
| R4 | When an admin moves a voucher from `Unverified` to `Unredeemed` (approve), the system emails the customer a confirmation with the branded voucher PNG attached, using the brief's copy verbatim. | Approving a booking results in exactly one email with subject `Booking Confirmed - UniFleet`, the brief's body text (3 redemption requirements + 48-hour reminder), and the voucher PNG as an attachment. |
| R5 | The recipient address for booking emails (R3, R4) is resolved from the customer record matching the booking's account code. | A booking whose account code maps to a customer with email `x@y.com` sends to `x@y.com`; no email address is read from the booking form. |
| R6 | If the resolved customer has no email on file (legacy record) or the account code matches no customer, no email is sent and the booking is flagged in the admin UI. | Booking under an emailless account code succeeds, produces no send attempt, and the booking appears in the admin list with a visible "no email" flag. |
| R7 | If a send attempt fails for any reason, the underlying action still succeeds and the failure is flagged against that booking/customer in the admin UI. | With the mail provider forced to error, registration, booking, and approval all complete normally and each produces a visible failure flag in admin. |
| R8 | Admins can resend any flagged notification from the admin UI, per booking. | A flagged row shows a Resend control; clicking it re-attempts that specific email, and on success the flag clears; on failure the flag remains and shows the new attempt time. |
| R9 | Admins can add or edit a customer's email address on the admin customers page. | Editing a customer with a blank email to a valid address saves it, and a subsequent Resend on that customer's flagged booking sends to the new address. |
| R10 | Re-approving a voucher (status flipped back to `Unverified`, then to `Unredeemed` again) only re-sends the confirmed email if the voucher's substance changed since the last successful send. | Re-approving with no change to requested amount, station, or voucher PNG sends nothing; re-approving after any of those changed sends a fresh confirmed email with the current attachment. |
| R11 | Every day at 00:00 Asia/Manila, the system emails the supplier PDF to the internal recipient list. | At 00:00 Manila, each configured internal recipient receives one email with the supplier PDF attached; the PDF content matches what `/supplier-sheet.pdf` would produce for all stations at that moment. |
| R12 | The scheduled PDF covers **all** stations and all currently live orders — no date window and no per-admin station filter. | The scheduled PDF's contents equal the on-demand PDF requested with no station filter; orders that admins have cancelled/deleted do not appear; nothing is excluded by booking date. |
| R13 | The daily internal email is sent even when there are no live orders. | On a day with zero live orders, recipients still receive the email with the (empty) PDF attached. |
| R14 | Admins manage the internal recipient list from the admin UI. | An admin can add and remove internal recipient addresses; the next scheduled run sends to exactly the current list, with no redeploy. |
| R15 | Outgoing customer email is sent from a monitored UniFleet support address so replies reach a human. | Every customer-facing email's From (or Reply-To) is the configured monitored support address, and a reply to it lands in that mailbox. |

## Non-Functional Requirements

| ID | Requirement | Acceptance Criterion |
|----|-------------|----------------------|
| N1 | Email delivery is asynchronous to the user's perception of success — mail latency must not visibly slow registration, booking, or approval. | With the provider artificially slowed, the register/book/approve responses still return within their current response-time envelope. |
| N2 | Provider credentials are supplied via configuration, never committed. | No API key or password appears in the repository; a missing credential disables sending with a clear operator-visible error rather than crashing the app. |
| N3 | The daily job is exactly-once per calendar day — an app restart or redeploy near midnight must not double-send. | Restarting the app repeatedly across the midnight boundary results in exactly one internal email per recipient per day. |
| N4 | Customer emails contain no data beyond that customer's own account code and booking; a customer never receives another customer's information. | An approval email for booking A contains only booking A's voucher and no other customer's details. |
| N5 | Email failures are diagnosable after the fact — each attempt records what was tried, to whom, when, and why it failed. | For any flagged failure, an admin or operator can determine the recipient, the notification type, the timestamp, and the provider's error reason. |

## Behaviors & Domain Rules

### Notification triggers

There are exactly four notification events:

1. **Account code** — fires once, on successful creation of a customer record via registration.
2. **Booking received** — fires once, on successful creation of a booking. Its copy must make clear the booking is *not* confirmed and the customer must not travel to the station yet.
3. **Booking confirmed** — fires on the `Unverified` → `Unredeemed` transition (the admin approve action), after the voucher PNG has been generated. May fire again only under R10.
4. **Daily internal supplier PDF** — fires on a clock schedule, not on any user action.

No other status transition sends email. In particular `Unredeemed` → `Redeemed` (the station scanning the voucher) sends nothing, and order cancellation/deletion sends nothing.

### Email copy

The three customer emails use the client-approved copy from docs/Brief-11_email.md verbatim, including the bare `Hi,` salutation with no customer name. The only substitution is the account code in the first email. Copy is plain text.

### Failure and flagging model

A "flag" is a durable, per-booking (or per-customer, for the account-code email) marker that a notification did not reach the customer. Flags are visible to admins in the same lists they already use, and are cleared by a successful Resend. Three distinct situations produce a flag:

- **No address** (R6) — nothing was attempted because there is no email to send to.
- **Send failed** (R7) — an attempt was made and the provider rejected it or was unreachable.
- **Missing attachment** (see edge cases) — the confirmed email went out without its voucher PNG and needs manual follow-up.

### Sequencing at approval

The approve action's order is: persist the new status → generate the voucher PNG → send the confirmed email with the PNG attached. Email is last so a mail failure can never leave the voucher unapproved, and the attachment is always the freshly generated asset rather than a stale one.

**Why these rules matter:**
- **Nothing blocks on email** — a mail-provider outage would otherwise stop customers from booking, i.e. stop revenue, for a reason the customer cannot act on. The flag-and-resend model converts an outage into a recoverable admin chore.
- **Address resolved from the customer record, not the booking form** — the booking form only collects an account code today, and adding a per-booking email field would create two competing sources of truth for the same customer.
- **Re-approve only re-sends when the voucher changed** — admins do flip status back and forth while correcting data; sending on every flip would spam customers with identical vouchers, while sending never would let a corrected voucher go undelivered.
- **Registration email required, legacy blanks tolerated** — making it required fixes the problem going forward, while tolerating existing blank records avoids breaking bookings for customers who registered before this change.
- **No date window on the daily PDF** — expiry is handled by admins cancelling orders, so a live-orders view is always correct and a date filter would silently hide orders that are still redeemable.
- **All stations on the scheduled PDF** — the on-demand PDF's station filter comes from an admin's browser cookie, which does not exist in a scheduled context; defaulting to all stations makes the nightly report deterministic and independent of whichever admin last touched a checkbox.

**Common mistakes:**
- Sending the confirmed email inside the approve handler *before* the voucher PNG is generated, producing an attachment-less email on the happy path.
- Reading the recipient's email from the booking form or from the request, rather than from the customer record keyed by account code.
- Letting a mail exception propagate and roll back the registration/booking/approval it was attached to.
- Treating the daily job's schedule as server-local time instead of Asia/Manila — the deployment platform runs UTC, so "midnight" would land at 8 AM Manila.
- Re-using the on-demand PDF path unchanged for the scheduled job and picking up (or requiring) the `pdf_station_ids` cookie.
- Firing the account-code email before the customer row is actually persisted, so a customer gets a code for an account that failed to save.
- Sending the confirmed email on the `Redeemed` transition as well as on approval.

## Edge Cases & Failure Modes

| Scenario | Decision | Rationale |
|----------|----------|-----------|
| Customer registers with a blank or malformed email | Registration is rejected with a field-level error; no record created | R2 — every new customer must be reachable |
| Booking made under an account code with no email on file (legacy record) | Booking succeeds; no send attempted; "no email" flag shown in admin | Old customers must not be blocked from booking |
| Booking made under an account code that matches no customer at all | Booking follows its existing behavior; no send; flagged like the no-email case | Email must not introduce a new failure path into booking |
| Mail provider is unreachable or returns an error during registration | Customer record is created; failure flagged; admin can Resend | Customer keeps their account; code can be re-delivered |
| Mail provider fails during booking submission | Booking is saved; failure flagged; admin can Resend | An outage must never cost a booking |
| Mail provider fails during admin approval | Status stays `Unredeemed` (approved); failure flagged; admin can Resend | The voucher is valid regardless of whether the email left |
| Voucher PNG is missing or fails to generate at approval time | Send the confirmed email anyway, without the attachment, and raise a distinct "attachment missing — manual follow-up" flag in admin | Customer learns they are confirmed; staff know to deliver the voucher by hand |
| Admin flips an approved booking back to `Unverified` and re-approves with no data change | No second confirmed email | Prevents duplicate emails from routine admin toggling |
| Admin re-approves after changing amount, station, or regenerating the voucher | Send a fresh confirmed email with the current attachment | The customer's copy of the voucher must match reality |
| Admin clicks Resend and it fails again | Flag remains, updated with the latest attempt time and reason | Repeated failure must stay visible, not look resolved |
| Admin clicks Resend twice in quick succession | The customer may receive duplicates; this is acceptable | A duplicate email is a far smaller harm than a silently dropped one |
| Customer's email address is valid in form but bounces at the provider | Out of scope for this REQ — treated as delivered | Bounce/complaint handling requires provider webhooks; deferred |
| Two bookings approved at the same moment for the same customer | Both emails send, each with its own voucher | Notifications are per booking, not per customer |
| The daily job's window is missed entirely (app down at midnight) | No catch-up send; the next day's run covers the then-current live orders | The report is a live snapshot, not a ledger; a missed snapshot has no lasting value |
| The app restarts repeatedly around midnight | Exactly one internal email per recipient per day (N3) | Duplicate nightly reports erode trust in the report |
| There are zero live orders at midnight | Send the email with the empty PDF | Recipients must be able to distinguish "nothing today" from "the job broke" |
| The internal recipient list is empty at send time | Send nothing; record that the run happened with no recipients | Not an error state; the list is admin-managed and may legitimately be empty |
| Supplier PDF generation fails at midnight | No internal email is sent; the failure is recorded for operators | An email with a corrupt or absent report is worse than a visibly missed run |
| The PDF grows large enough to hit provider attachment limits | Treat as a send failure and record it; no size-based fallback in this REQ | Volume today is far below any provider limit; a fallback is speculative work |
| Provider credentials are missing or invalid at startup | The app runs normally; every send attempt fails and is flagged; the misconfiguration is visible to operators | The app must not be unbootable because of a mail misconfiguration |
| An admin adds an email to a legacy customer after several flagged bookings | Each flagged booking can be resent individually | Per-booking resend keeps the admin in control of what the customer receives |
| A very long or unusual account code / customer name in the email body | Copy is fixed text with a single account-code substitution; no other interpolation | Minimal substitution surface avoids formatting and injection problems |

## Decisions Log

| # | Decision | Alternatives Considered | Chosen Because |
|---|----------|-------------------------|----------------|
| 1 | Use a transactional email API provider, credentials via config; exact vendor chosen in Phase 2 | Gmail / Google Workspace SMTP | Better deliverability and attachment handling; no coupling to a personal mailbox or its password policy |
| 2 | Email is required at registration | Keep it optional | Every customer created from now on must be reachable by the notifications this REQ adds |
| 3 | Legacy customers without an email are skipped and flagged, not blocked | Block their bookings; silently skip | Existing customers must keep booking; silence would hide the gap from staff |
| 4 | No email failure ever blocks registration, booking, or approval; all failures are flagged | Block and show an error (the user's initial preference); block only on register and approve | A provider outage would otherwise halt bookings — revenue lost for a reason the customer cannot fix. Flag-and-resend recovers the same outcome without the outage cost |
| 5 | Per-booking Resend control in the admin UI, clearing the flag on success | Flag only, with admins emailing by hand; automatic background retry | Keeps recovery inside the app and under the admin's control, without building retry infrastructure |
| 6 | Confirmed email re-sends on re-approval only when the voucher's substance changed | Always re-send; never re-send | Avoids duplicate spam from routine admin status toggling while still delivering corrected vouchers |
| 7 | Recipient resolved from the customer record via account code | Add an email field to the booking form | The customer record is the single source of truth; a per-booking field would create divergent addresses for the same customer |
| 8 | Admins can edit a customer's email on the admin customers page | Leave email uneditable and out of scope | Without it, a flagged legacy booking can never be resent inside the app |
| 9 | If the voucher PNG is unavailable, send the confirmed email without it and flag for manual follow-up | Fail the approval; hold the email | The customer still learns they are confirmed; staff get an explicit signal to hand-deliver the voucher |
| 10 | Daily internal send at 00:00 Asia/Manila | The brief's original 21:00 | User's correction — midnight is the operationally meaningful boundary for their staff |
| 11 | The daily PDF has no date window; it always reflects currently live orders | Same-day bookings only; all unredeemed vouchers | Expiring orders are cancelled by admins and disappear from the live set, so a date filter would only hide still-valid orders |
| 12 | The scheduled PDF covers all stations, ignoring the per-admin cookie filter | Admin-configurable default station set | Deterministic output, and a scheduled job has no browser cookie to read |
| 13 | The internal recipient list is managed in the admin UI | Comma-separated environment variable; one fixed distribution address | Staff churn should not require a redeploy |
| 14 | Send the daily email even when there are no live orders | Skip empty days | Recipients can distinguish "no bookings" from "the job failed" |
| 15 | Customer email is sent from a monitored support address | no-reply address; no-reply From with support Reply-To | Customers do reply to these emails; replies must reach a person |
| 16 | Email bodies use the brief's copy verbatim, plain text, with the bare `Hi,` salutation | Personalize with the customer name; HTML templates | Copy is client-approved as written; personalization and HTML are separate later decisions |
| 17 | All four notifications ship in one REQ | Split customer emails from the daily internal report | They share the entire mail-sending, configuration, and failure-flagging surface; splitting would duplicate that groundwork |

## Scope Boundaries

### In Scope
- Sending the three customer emails (account code, booking received, booking confirmed with voucher attachment) with the brief's copy.
- Attaching the branded voucher PNG to the confirmed email.
- Making email required and validated for format on the registration form.
- Admin visibility of failed/skipped notifications, and a per-booking Resend action.
- Admin editing of a customer's email address.
- A daily 00:00 Asia/Manila job that emails the all-stations, live-orders supplier PDF to internal recipients.
- Admin management of the internal recipient list.
- Sending even when the report is empty; not sending when the recipient list is empty.

### Out of Scope
- Bounce, complaint, and unsubscribe handling (reason: needs provider webhooks and an inbound-processing surface; no volume pressure yet).
- SMS or push notifications (reason: not requested in the brief).
- HTML or branded email templates, and per-customer personalization such as using the customer's name (reason: the client supplied plain-text copy; restyling is a separate design decision).
- Emails on the `Redeemed` transition, on cancellation, or on order deletion (reason: not in the brief).
- Customer-facing email preferences or opt-out (reason: these are transactional notifications tied to actions the customer initiated).
- Catch-up or backfill sends for days the daily job was missed (reason: the report is a live snapshot with no lasting value once the day passes).
- Retro-notifying customers about bookings made before this feature ships (reason: would deliver confusing, stale confirmations).
- Attachment-size fallbacks such as linking instead of attaching the PDF (reason: current volume is far below provider limits; speculative).
- Localization / Tagalog versions of the email copy (reason: the client's approved copy is English-only).

## Open Questions

- Which specific transactional email provider, and does UniFleet already hold an account?
  - **Impact if unresolved:** Phase 2 cannot finalize the integration or the configuration surface, and someone must create and fund an account before this can ship.
  - **Suggested default:** pick a provider with a straightforward API and attachment support during plan-architecture; keep the sending interface provider-agnostic so a later switch is cheap.
- What is the exact monitored support address, and is the sending domain verified (SPF/DKIM) for it?
  - **Impact if unresolved:** unverified sending domains land in spam, so customers silently do not receive account codes or vouchers.
  - **Suggested default:** use the UniFleet domain's existing support mailbox and complete domain verification as a deployment prerequisite before go-live.
- Should the internal recipient list be seeded with any addresses at launch, or start empty?
  - **Impact if unresolved:** if it ships empty and nobody notices, the daily report silently goes nowhere.
  - **Suggested default:** ship empty and make the first admin's task adding recipients, with the empty-list state visible in the admin UI.

---
_This requirements document is the input for the **plan-architecture** skill._
_Next step: `/plan-architecture from: specs/requirements/REQ-brief-11-email-notifications.md`_
