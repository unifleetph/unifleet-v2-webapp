# Requirements: Exclude Redeemed Orders from Supplier Report

> **Date:** 2026-10-27
> **Type:** bugfix
> **Source:** docs/Bugfix-1.md (bug 1 of 2), verbal gap-fill interview
> **Phase:** 1 of 5 (Requirement Engineering)

## Summary

Admins can currently only delete (soft-delete) orders that are Unredeemed or earlier — a Redeemed order can never be removed, so test/dummy orders that got redeemed stay stuck in the Supplier PDF and Supplier CSV exports forever. This adds a separate, reversible "exclude from supplier report" flag that admins can set on a Redeemed order to omit it from those two exports, without soft-deleting it or affecting anything else about the order.

## Problem & Motivation

The existing delete action blocks Redeemed orders on purpose, because the existing soft-delete (`deleted_at`) hides an order from everything — the redeem page 404s, it drops out of every export, it's effectively gone. That's correct for a real transaction, but wrong for a test order that happened to get redeemed: there's currently no way to keep it visible and functional (in case it needs to be referenced later) while still keeping it out of the supplier-facing billing documents. Admins need a lighter-weight, reversible way to say "don't bill the supplier for this one" without the order disappearing from the system.

## Users & Consumers

- **Admin (internal staff)** — needs to mark a specific Redeemed order as excluded from the supplier report, and reverse that later if marked by mistake.
- **Supplier (via the PDF/CSV export)** — must never see an excluded order on either export.

## Functional Requirements

| ID | Requirement | Acceptance Criterion |
|----|-------------|------------------------|
| R1 | Admin can mark a Redeemed order as excluded from the supplier report. | Admin triggers "Exclude from supplier report" on a Redeemed order; the order is now flagged excluded, and the action now reads "Include in supplier report". |
| R2 | An excluded order does not appear in the Supplier PDF export. | Generating the Supplier PDF for a period containing the excluded order produces no row for it. |
| R3 | An excluded order does not appear in the Supplier CSV export. | Generating the Supplier CSV for a period containing the excluded order produces no row for it. |
| R4 | Admin can reverse the exclusion. | Admin triggers "Include in supplier report" on a previously-excluded order; it reappears in the next generated PDF and CSV. |
| R5 | Only Redeemed orders can be flagged. | Attempting to exclude/include on any other order status is rejected; the order's excluded state is unchanged. |
| R6 | Excluding an order changes nothing else about it. | The excluded order still appears in the admin dashboard order list and the redeem page still loads for it exactly as before; no audit log entry is written for the toggle. |

## Non-Functional Requirements

None identified beyond the functional requirements above — this is a small, low-traffic admin toggle with no performance, security, or scale dimension of its own.

## Behaviors & Domain Rules

**Relationship to the existing `deleted_at` soft-delete:**
- This is a *separate* flag from `deleted_at`. `deleted_at` continues to mean "gone everywhere" (redeem page 404s, excluded from every export, etc.) and remains blocked for Redeemed orders exactly as today. The new exclusion flag means only "omit from the two supplier exports" — everything else about the order (status, redeem page, dashboard visibility) is untouched.
- An order can only be excluded if it's currently Redeemed. If its status could ever change away from Redeemed in the future, that's out of scope for this REQ (today's system has no path back out of Redeemed).

**Why this rule matters:**
- Keeping this as a separate flag (rather than extending `deleted_at` to Redeemed orders) avoids redefining what `deleted_at` means for every other piece of code that already checks it (redeem page, ops status route, audit trail) — a much larger, riskier change than this bug warrants.

**Common mistakes:**
- Conflating this flag with `deleted_at` and accidentally making an excluded order disappear from the admin dashboard or redeem page too — it must only affect the two supplier exports.
- Allowing the flag to be set on non-Redeemed orders "just in case" — explicitly restricted to Redeemed only (R5).

## Edge Cases & Failure Modes

| Scenario | Decision | Rationale |
|----------|----------|-----------|
| Admin tries to exclude a non-Redeemed order | Rejected; no state change | Matches R5 — the flag only makes sense for the specific "redeemed test order" problem this bug describes |
| Admin un-excludes a previously-excluded order | Flag cleared; order reappears in the next PDF/CSV | Matches R4 — must be fully reversible |
| Admin excludes an already-excluded order (double action) | No-op, no error | Idempotent toggle, not a destructive action |
| Order is excluded, then queried for anything else (dashboard, redeem page) | No change in behavior anywhere except the two exports | Matches R6 — this is a narrowly-scoped flag |

## Decisions Log

| # | Decision | Alternatives Considered | Chosen Because |
|---|----------|---------------------------|------------------|
| 1 | New, separate flag dedicated to supplier-report exclusion | Extend `deleted_at` to also apply to Redeemed orders | `deleted_at` already has broad meaning across the codebase (redeem page, exports, audit); redefining it for Redeemed orders risks breaking other paths that currently rely on "Redeemed + not deleted_at" |
| 2 | Flag excludes from both PDF and CSV supplier exports | PDF only | Both exports serve the same supplier-billing purpose; a test order shouldn't leak through either |
| 3 | Restricted to Redeemed orders only | Any status | Non-Redeemed orders can already be removed via the existing delete action; this flag exists specifically for the one case delete can't handle |
| 4 | No audit log entry for toggling the flag | Log like `delete_order` does | Low-stakes, easily-reversible toggle; not worth the audit noise |

## Scope Boundaries

### In Scope
- A new per-order exclusion flag, settable and clearable by an admin, restricted to Redeemed orders.
- Supplier PDF export honoring the flag.
- Supplier CSV export honoring the flag.

### Out of Scope
- Any audit logging of the toggle (reason: explicitly decided against, see Decision 4).
- Extending the flag to any other order status (reason: delete already covers those cases).
- Bulk-exclude / bulk-include actions (not raised in this brief).
- Any change to the existing `deleted_at` soft-delete behavior.

## Open Questions

- None — the brief's scope, mechanism, and reversibility were all resolved in this session.

---
_This requirements document is the input for the **plan-architecture** skill._
_Next step: `/plan-architecture from: specs/requirements/REQ-supplier-report-exclusion-flag.md`_
