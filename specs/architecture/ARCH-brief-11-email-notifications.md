# Architecture: Customer Email Notifications & Daily Internal Supplier PDF

> **Date:** 2026-09-07
> **Phase:** 2 of 5 (System Architecture)
> **Requirements source:** specs/requirements/REQ-brief-11-email-notifications.md
> **Tasks:** TASKS-brief-11-email-notifications.md
> **Type:** feature

## Architecture Summary

All outbound mail goes through a **Postgres-backed outbox**. Request handlers never talk to the mail provider: `/register`, `/book`, and the admin approve action each write one `notifications` row and return immediately, so a slow or dead provider cannot stall the single gunicorn worker or fail a customer's action (REQ R7, N1). A daemon thread started at app import drains the outbox — claiming due rows, calling Resend over HTTP, and recording the result with a bounded retry ladder. Rows that exhaust their retries, or that fail permanently on a 4xx, stay visible as flags in the admin UI with a per-booking Resend control (R8).

Three new Postgres-only modules follow the existing `audit_log.py` / `margin_store.py` precedent — straight to `db.pool.get_pool()`, bypassing the `CSVRepo`/`DBRepo`/`PostgresRepo` triplication: `notifications.py` (outbox + copy), `mailer.py` (the only file that knows Resend), `report_recipients.py` (the admin-managed internal list). The daily internal report is a separate Railway cron entrypoint, `scripts/send_daily_report.py`, running at 16:00 UTC (= 00:00 Asia/Manila), which builds the all-stations supplier PDF and enqueues one outbox row per recipient with a date-scoped dedupe key (R11–R14, N3).

Email is an additive layer: no existing route changes its status transitions, its response codes, or its data model. The one behavioral change to an existing flow is that `/register` now requires a valid email address (R2).

## High-Level Structure

```
        REQUEST PATH (never blocks on mail)
  ┌───────────────┐   ┌──────────┐   ┌───────────────────────┐
  │ /register     │   │ /book    │   │ /ops/voucher/.../     │
  │               │   │ POST     │   │   status/Unredeemed   │
  └───────┬───────┘   └────┬─────┘   └───────────┬───────────┘
          │ enqueue        │ enqueue             │ enqueue (post-set_status,
          │ account_code   │ booking_received    │  fingerprint-gated)
          └────────────────┴──────────┬──────────┘
                                      ▼
                         ┌────────────────────────┐
                         │ notifications.py       │   Postgres-only,
                         │  enqueue / drain_once  │   via db.pool.get_pool()
                         │  list_flagged / resend │   (audit_log.py pattern)
                         └───────┬────────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  notifications table     │  queued | sent | failed | skipped
                    └────────────┬─────────────┘
                                 │ polled
        DELIVERY PATH            ▼
                    ┌──────────────────────────┐      ┌───────────────┐
                    │ worker thread (daemon)   │─────▶│  mailer.py    │──▶ Resend HTTP API
                    │  claim → send → record   │      │ (only vendor- │
                    └──────────────────────────┘      │  aware file)  │
                                                      └───────────────┘
        SCHEDULED PATH                                  ADMIN PATH
  ┌──────────────────────────────┐            ┌──────────────────────────────┐
  │ Railway cron 16:00 UTC       │            │ /admin           flags+Resend│
  │ scripts/send_daily_report.py │            │ /admin/recipients  CRUD list │
  │  build_supplier_pdf(all)     │            │ /admin/customers/<c>/email   │
  │  → enqueue per recipient     │            └──────────────────────────────┘
  └──────────────────────────────┘
```

**Added:** `mailer.py`, `notifications.py`, `report_recipients.py`, `scripts/send_daily_report.py`, `templates/admin_recipients.html`, two Postgres tables, one Railway cron service.

**Modified:** `main.py` (3 enqueue call sites, 4 new admin routes, worker start, `/admin` flag join), 4 templates, `persistence.py` + `db/postgres_repo.py` (one new method), `db/schema.sql`, config files.

**Unchanged:** the voucher status machine, `report_pdf.build_supplier_pdf`'s signature, the repo abstraction's existing methods, and every response code on existing routes except `/register`'s new validation failure.

## Tech Choices

| Area | Decision | Alternatives Considered | Rationale |
|------|----------|-------------------------|-----------|
| Mail provider | Resend, over its HTTP API | SendGrid + SDK; SMTP via stdlib `smtplib` | Free tier already provisioned by the team; JSON API with base64 attachments needs no MIME assembly. Confined to `mailer.py` so a vendor swap is one file |
| HTTP client | `requests` (new prod dependency) | stdlib `urllib.request` (zero deps) | Developer preference for readability at the call site; one POST per email makes the 4 transitive packages a cheap trade |
| Send scheduling | Postgres outbox + in-process daemon worker thread | Synchronous inline send; fire-and-forget thread per send; second Railway cron drain service | `--workers 1` means an inline send freezes the whole app. The outbox additionally survives restarts and gives retries and flags for free, which a bare thread cannot |
| Notification storage | New Postgres tables, accessed directly via `db.pool` | Add methods to all three repo classes | Exactly the `audit_log.py` / `margin_store.py` precedent. The repo abstraction would triple the surface for a feature that only functions against Postgres anyway |
| Daily schedule | Railway Cron Schedule service running a script | APScheduler in-process; a `while True: sleep` thread | Mirrors `scripts/backup_postgres.py`. Survives redeploys, needs no new dependency, and keeps the report independent of web-process uptime |
| Timezone handling | `zoneinfo.ZoneInfo("Asia/Manila")` | `pytz` (already a dependency) | `main.py` already uses `ZoneInfo` for the Manila-dated PDF filename; stay consistent |
| Attachment resolution | Store a reference (kind + voucher_id); the worker resolves bytes at send time | Store base64 bytes on the outbox row | Keeps rows small, and the confirmed email always ships the current PNG rather than a stale snapshot |
| Feature gate | `NOTIFICATIONS_ENABLED` env flag | Ship ungated | Lets an operator stop all sending without a code redeploy if copy or deliverability goes wrong |

## Patterns & Conventions

- **Transactional outbox** — applied because the request path must never depend on an external service's availability (R7, N1); affects all three customer notifications and the daily report.
- **Postgres-only side modules** — from `audit_log.py` and `margin_store.py`: import `get_pool` from `db.pool`, swallow failures, keep the public function signature narrow. Followed by `notifications.py`, `mailer.py`, `report_recipients.py`.
- **Forward-only schema** — from `db/schema.sql`: `CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, no DROP. Applied by the existing `db/apply.py` chain in the Dockerfile CMD and `rw.txt` `preDeployCommand`, so no new migration mechanism is introduced.
- **Failure-swallowing side effects** — from `audit_log.append_audit`: an enqueue failure prints to stderr and returns; it never propagates into the request. This is what makes `PERSISTENCE_BACKEND=csv` local dev keep working with no `DATABASE_URL`.
- **Admin routes gated by `require_admin(request)`** — from every existing `/admin/*` route in `main.py`; all four new routes follow it, redirecting to `admin_login` on failure.
- **Cron script conventions** — from `scripts/backup_postgres.py`: module docstring stating exit codes, timestamped stdout logging, distinct non-zero exits per failure class.
- **Deliberately not applied:** no Celery/RQ/Redis broker (volume does not justify the infrastructure), no ORM (the codebase is raw psycopg throughout), and no blueprint extraction (CLAUDE.md: `main.py` is the single-file app).

## Data Models

### notifications

**Purpose:** the outbox row — one per email UniFleet intends to send. It is simultaneously the queue, the delivery-attempt history, and the source of the admin flag list.

**Key fields:**
| Field | Type / Constraint | Notes |
|-------|-------------------|-------|
| `id` | BIGINT GENERATED ALWAYS AS IDENTITY PK | Follows the `presets` table's id convention |
| `kind` | VARCHAR(40) NOT NULL | `account_code` \| `booking_received` \| `booking_confirmed` \| `daily_report` |
| `recipient` | VARCHAR(320) | NULL only for `skipped` rows where no address existed (R6) |
| `account_code` | VARCHAR(16) REFERENCES customers(account_code) | Nullable — `daily_report` rows have none |
| `voucher_id` | VARCHAR(32) REFERENCES vouchers(voucher_id) | Nullable — `account_code` and `daily_report` rows have none |
| `status` | VARCHAR(20) NOT NULL DEFAULT 'queued' | `queued` \| `sending` \| `sent` \| `failed` \| `skipped` |
| `attempts` | SMALLINT NOT NULL DEFAULT 0 | Drives the retry ladder; max 5 |
| `next_attempt_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | The worker claims rows where this is in the past |
| `last_error` | TEXT | Provider message or exception text — the R7/N5 diagnostic |
| `last_status_code` | SMALLINT | Resend's HTTP status; distinguishes permanent 4xx from retryable 5xx |
| `provider_message_id` | VARCHAR(120) | Resend's id, for support tickets |
| `dedupe_key` | VARCHAR(200) UNIQUE | The idempotency guarantee (N3, and the double-approve case) |
| `voucher_fingerprint` | VARCHAR(64) | SHA-256 over amount, total, station, and PNG bytes — the R10 change test |
| `attachment_ref` | VARCHAR(200) | `voucher_png:<voucher_id>` or `daily_pdf:<Manila date>`; resolved to bytes at send time |
| `subject` / `body` | TEXT NOT NULL | Rendered at enqueue time so the sent copy is exactly what was queued |
| `created_at` / `sent_at` | TIMESTAMPTZ | `created_at` NOT NULL DEFAULT NOW() |

Indexes: `(status, next_attempt_at)` for the worker's poll; `(voucher_id)` for the `/admin` flag join; UNIQUE on `dedupe_key`.

**Dedupe key construction:**
- `acct:<account_code>` — one account-code email per customer, ever.
- `booked:<voucher_id>` — one received email per booking.
- `confirmed:<voucher_id>:<fingerprint>` — re-approval with unchanged substance collides and is a no-op; a changed voucher yields a new key and a new email (R10).
- `daily:<YYYY-MM-DD Manila>:<recipient>` — one report per recipient per Manila day, however many times the cron fires (N3).

**Relationships:** many-to-one to `customers` and to `vouchers`, both nullable, both `ON DELETE` unspecified (matching the existing FK style — vouchers are soft-deleted, never removed).

**Lifecycle:** `queued` → (worker claims) `sending` → `sent`, or back to `queued` with `attempts+1` and a backed-off `next_attempt_at`, or → `failed` on the 5th exhausted attempt / any 4xx. `skipped` is a terminal state written at enqueue time when there is no address. An admin Resend on a `failed` or `skipped` row resets it to `queued` with `attempts = 0`.

### report_recipients

**Purpose:** the admin-managed internal distribution list for the daily supplier PDF (R14).

| Field | Type / Constraint | Notes |
|-------|-------------------|-------|
| `id` | BIGINT GENERATED ALWAYS AS IDENTITY PK | |
| `email` | VARCHAR(320) NOT NULL UNIQUE | Case-normalized to lowercase on write |
| `label` | VARCHAR(200) | Optional human note ("ops team") |
| `is_active` | BOOLEAN NOT NULL DEFAULT TRUE | Soft-disable, mirroring `stations.is_active` |
| `created_at` / `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

**Lifecycle:** created by an admin → optionally deactivated → hard-deleted only via the explicit delete control. The cron reads `is_active = TRUE` rows only.

### customers (modified)

No schema change. `email` gains a de-facto NOT NULL for newly created rows through form validation only (R2) — the column stays nullable so the legacy blank rows that R6 exists to serve remain valid.

## API Contracts / Interfaces

### mailer.py

**Boundary:** internal module; the sole outbound HTTP integration.

| Op | Signature | Purpose | Returns |
|----|-----------|---------|---------|
| send | `send(to: str, subject: str, body: str, attachment: tuple[str, bytes, str] \| None = None) -> SendResult` | POST one email to Resend | `SendResult(ok, provider_message_id, status_code, error)`. Never raises: network errors and non-2xx both come back as `ok=False` with `status_code` set (0 for transport failures) |
| is_configured | `is_configured() -> bool` | Whether `RESEND_API_KEY` and `MAIL_FROM` are present | bool |

Config read from env: `RESEND_API_KEY`, `MAIL_FROM` (the monitored support address, R15), `MAIL_REPLY_TO` (optional). Request timeout 10s. The attachment tuple is `(filename, bytes, mimetype)`, base64-encoded into Resend's `attachments[]`.

### notifications.py

**Boundary:** internal module; owns the outbox table and the three customer copy templates.

| Op | Signature | Purpose | Returns / Errors |
|----|-----------|---------|------------------|
| enqueue | `enqueue(kind, *, recipient, subject, body, dedupe_key, account_code=None, voucher_id=None, attachment_ref=None, fingerprint=None) -> int \| None` | Insert one queued row | Row id, or `None` on dedupe collision or DB failure. Never raises into the caller |
| enqueue_skipped | `enqueue_skipped(kind, *, account_code, voucher_id, reason) -> None` | Record the no-address case as a terminal `skipped` row (R6) | — |
| drain_once | `drain_once(limit: int = 20) -> int` | Claim, send, and record every due row | Count of rows processed |
| start_worker | `start_worker() -> None` | Start the daemon polling thread; no-op if already running or `NOTIFICATIONS_ENABLED` is false | — |
| flags_by_voucher | `flags_by_voucher(voucher_ids: list[str]) -> dict[str, dict]` | Bulk flag lookup for the `/admin` table | Maps voucher_id → `{status, kind, attempts, last_error, notification_id}` |
| list_flagged | `list_flagged(limit: int = 200) -> list[dict]` | Every `failed`/`skipped` row, newest first | — |
| requeue | `requeue(notification_id: int, *, recipient: str \| None = None) -> bool` | Admin Resend: reset to `queued`, `attempts = 0`, optionally repoint the address | False if the id does not exist |
| render_* | `render_account_code(code)`, `render_booking_received()`, `render_booking_confirmed()` | The brief's verbatim copy (R1, R3, R4) | `(subject, body)` |

### Row claiming

`drain_once` claims with a single `UPDATE ... SET status='sending' WHERE id IN (SELECT id FROM notifications WHERE status='queued' AND next_attempt_at <= NOW() ORDER BY id FOR UPDATE SKIP LOCKED LIMIT n) RETURNING *`. `SKIP LOCKED` keeps the design correct if the app is ever scaled past one worker or if a cron sweep is added later.

### report_recipients.py

| Op | Signature | Purpose |
|----|-----------|---------|
| list_all | `list_all(include_inactive: bool = False) -> list[dict]` | Admin page listing |
| active_emails | `active_emails() -> list[str]` | What the cron sends to |
| add | `add(email: str, label: str = "") -> dict \| None` | `None` when the address already exists |
| set_active | `set_active(id: int, active: bool) -> bool` | Soft-disable |
| delete | `delete(id: int) -> bool` | Hard delete |

### New HTTP routes (all `require_admin`)

| Method | Path | Purpose | Returns |
|--------|------|---------|---------|
| POST | `/admin/notifications/<int:id>/resend` | Requeue one flagged notification (R8) | 302 back to referrer with a flash; 404 on unknown id |
| GET | `/admin/recipients` | Recipient list page (R14) | `admin_recipients.html` |
| POST | `/admin/recipients` | Add a recipient | 302 back; flash on duplicate or invalid address |
| POST | `/admin/recipients/<int:id>/delete` | Remove a recipient | 302 back |
| POST | `/admin/customers/<account_code>/email` | Set or correct a customer's email (R9) | 302 back to the customer detail view; flash on invalid format |

Unauthenticated access to any of these redirects to `admin_login`, exactly as the existing admin routes do.

### scripts/send_daily_report.py

**Boundary:** CLI entrypoint for the Railway cron service. `python scripts/send_daily_report.py [--dry-run]`.

Behavior: read active recipients → if none, log and exit 0 → build the PDF from `_exclude_deleted(repo.list_all_vouchers())` filtered to `status == 'Unredeemed'`, all stations, via `report_pdf.build_supplier_pdf` → enqueue one row per recipient with `dedupe_key = daily:<Manila date>:<email>`.

Exit codes, in the style of `backup_postgres.py`: `0` success (including the no-recipients case), `1` `DATABASE_URL` unset or the DB unreachable, `2` PDF generation failed (nothing enqueued).

## Module Boundaries

| Module | Responsibility | Allowed Dependencies |
|--------|----------------|----------------------|
| `mailer.py` | Speak Resend's HTTP API; know nothing about UniFleet's domain | `requests`, `os`, stdlib only |
| `notifications.py` | Own the outbox table, the copy, the retry policy, the worker thread | `db.pool`, `mailer`, `data_paths`, stdlib. **Must not** import `main` |
| `report_recipients.py` | Own the recipient table | `db.pool`, stdlib |
| `scripts/send_daily_report.py` | Assemble the daily PDF and enqueue it | `persistence` (via `get_repo`), `report_pdf`, `report_recipients`, `notifications`, `data_paths` |
| `main.py` | Call `enqueue` at the three trigger points; expose the admin routes; start the worker | all of the above |

Rule for crossing: mail flows one way. `main.py` and the cron script call `notifications`; `notifications` calls `mailer`; nothing calls back up. `notifications.py` never imports `main` or Flask's request context — unlike `audit_log.py`, it must run inside a bare worker thread and a bare cron process, where no request exists.

## Change Footprint

### New files / modules

| Path | Purpose | Pattern reference |
|------|---------|-------------------|
| `mailer.py` | Resend HTTP client, `send()` / `is_configured()` | `margin_store.py` (small single-purpose module) |
| `notifications.py` | Outbox: enqueue, drain, worker thread, flags, requeue, copy templates | `audit_log.py` (Postgres-only, `db.pool`, failure-swallowing) |
| `report_recipients.py` | CRUD over `report_recipients` | `margin_store.py` |
| `scripts/send_daily_report.py` | Railway cron entrypoint, 16:00 UTC | `scripts/backup_postgres.py` |
| `templates/admin_recipients.html` | Recipient management page | `templates/admin_stations.html` |
| `tests/test_mailer.py` | Provider call shaping, 4xx/5xx/timeout classification | `tests/test_margin_store.py` |
| `tests/test_notifications.py` | Enqueue, dedupe collision, retry ladder, requeue, skipped rows | `tests/test_postgres_repo.py` (SQLite monkeypatch style) |
| `tests/test_daily_report.py` | Recipient fan-out, empty-recipient exit, dedupe key, exit codes | `tests/test_supplier_exports_exclude_deleted.py` |
| `tests/test_report_recipients.py` | CRUD + admin route auth | `tests/test_admin_stations.py` |
| `tests/test_admin_notification_flags.py` | Flag rendering on `/admin`, Resend route | `tests/test_admin_delete_order.py` |

### Modified files / modules

| Path | What changes here |
|------|-------------------|
| `db/schema.sql` | Append `CREATE TABLE IF NOT EXISTS notifications` and `report_recipients` plus their indexes, at the end, forward-only |
| `main.py` — imports | Add `notifications`, `report_recipients`; guard the import the way `build_supplier_pdf` is guarded so a broken module cannot take the app down |
| `main.py:697` `register()` | Add required + format validation for `email` before the `contact_number` check; after `_append_customer_csv_if_absent(new_row)` and before the redirect, `enqueue` the `account_code` email |
| `main.py:1274` `/book` POST | Replace the bare `except: print` swallow with a `created`-aware branch: on success resolve `repo.get_customer(account_code)` and enqueue `booking_received`; on a blank/missing email call `enqueue_skipped` |
| `main.py:651` approve branch | After `repo.set_status(voucher_id, 'Unredeemed', "")` — the existing 500-on-asset-failure abort above it is untouched — compute the fingerprint from amount/total/station plus the `official_qr_png_path` bytes and enqueue `booking_confirmed` |
| `main.py:238` `/admin` | Join `notifications.flags_by_voucher` onto the row loop next to the existing `png_exists` computation |
| `main.py` — new routes | `/admin/notifications/<id>/resend`, `/admin/recipients` (GET/POST), `/admin/recipients/<id>/delete`, `/admin/customers/<code>/email` |
| `main.py` — startup | `notifications.start_worker()` at module import, after the app object exists |
| `templates/admin.html` | Per-row flag badge and Resend button, beside the existing PNG-status column |
| `templates/admin_customer_lookup.html` | Email becomes an editable inline form in the `detail` state |
| `templates/register.html` | Mark the email input `required` and adjust its label |
| `persistence.py` | Add `update_customer_email(account_code, email)` to `CSVRepo` (lock-file guarded, mirroring `create_customer_if_absent`) and to `DBRepo` |
| `db/postgres_repo.py` | Add `update_customer_email(account_code, email)` to `PostgresRepo` |
| `pyproject.toml` / `poetry.lock` | Add `requests` |
| `.env.example` | `RESEND_API_KEY`, `MAIL_FROM`, `MAIL_REPLY_TO`, `NOTIFICATIONS_ENABLED` |
| `docker-compose.yml` | Pass the four new env vars through to the web service |
| `rw.txt` | Add the cron service entry for `scripts/send_daily_report.py` at `0 16 * * *` |
| `AGENTS.md` | Document the four new env vars in the environment section |

### Deleted / replaced

Nothing. The change is purely additive; no existing module or route is retired.

### Touched but not changed (silent-regression hotspots)

| Path | Why it matters |
|------|----------------|
| `tests/test_register_pg.py` | POSTs `/register`; any case omitting `email` now fails validation instead of creating a customer |
| `tests/test_register_optional_labels.py` | Asserts which register fields are optional — the email label and `required` attribute both change |
| `tests/test_register_success_text.py` | Drives registration end-to-end; will now also hit the enqueue path |
| `tests/test_admin_approve_margin.py` | Monkeypatches `generate_assets_for_row` and drives approve; the new post-`set_status` enqueue runs in that path |
| `tests/test_book_calculator_inversion.py` | Same approve path, same monkeypatch |
| `tests/test_schema.py` | Asserts against the table set in `db/schema.sql`; two new tables appear |
| `tests/test_railway_toml.py` | Asserts `rw.txt` contents; the new cron entry changes the file |
| `tests/test_admin_customers.py` | Renders `admin_customer_lookup.html`, which gains an edit form |
| `tests/test_supplier_exports_exclude_deleted.py` | Owns the soft-delete contract the cron's PDF now depends on |
| `report_pdf.build_supplier_pdf` | Gains a second caller with no signature change; any future signature edit now breaks two call sites |
| `db/apply.py` | Applies the two new tables on every Railway deploy and in `make test-db` — unchanged code, newly load-bearing |
| `data/customers.csv` dual-write path | `_append_customer_csv_if_absent` still runs before the enqueue; the email column must be populated there for CSV-backed lookups to resolve a recipient |

## Areas of Impact

| Area | Impact | Risk | Why |
|------|--------|------|-----|
| `/register` flow | Email becomes mandatory; a new failure mode on submit | **M** | The only existing user-facing behavior change in the REQ, and three test files drive this form |
| `/book` POST | The exception swallow becomes a branch point | **M** | Touching a path that currently "succeeds" even when the repo write fails; getting the condition wrong could suppress bookings or emails |
| Approve flow (`/ops/voucher/.../status/...`) | New enqueue after `set_status` | **M** | Highest-value email and the only one with an attachment; two tests already drive this branch |
| `/admin` dashboard | New per-row flag column and Resend control | **L** | Additive rendering; the row loop already computes `png_exists` the same way |
| Postgres schema | Two new tables, no column changes to existing tables | **L** | Forward-only, `IF NOT EXISTS`, applied by the existing `db/apply.py` chain |
| Railway deployment | One new cron service; new required env vars | **M** | The app boots without `RESEND_API_KEY` but silently sends nothing — a config miss looks like a code failure |
| Local dev (`PERSISTENCE_BACKEND=csv`) | Enqueue no-ops without `DATABASE_URL` | **L** | Matches `audit_log.py`'s existing behavior; developers already expect this |
| Web process resource use | One extra daemon thread polling Postgres | **L** | A few-second poll of an indexed table against `--workers 1` |
| Customer inbox / deliverability | UniFleet becomes an email sender for the first time | **M** | Unverified sending domains land in spam, which looks identical to "the feature does not work" |
| Dependencies | `requests` + 4 transitive packages | **L** | Widely used, no build-time complications |

**Contract changes:** none external. The supplier API, `/api/v1/*`, the supplier CSV/PDF exports, and the voucher status values are all untouched. `report_pdf.build_supplier_pdf` keeps its signature. The only contract that shifts is the `/register` form's — it now rejects submissions it previously accepted.

**Cross-cutting ripples:** `db/apply.py` picks up two tables automatically; `rw.txt` gains a cron service (asserted by `test_railway_toml.py`); a new class of secret (`RESEND_API_KEY`) enters the env surface; `NOTIFICATIONS_ENABLED` is a new operational kill switch; audit coverage extends via `append_audit` calls on admin Resend and recipient changes.

## Cross-Cutting Concerns

- **Errors.** Enqueue failures are swallowed with a stderr line, exactly like `audit_log.append_audit` — no mail concern can fail a registration, a booking, or an approval (R7). Inside the worker, `mailer.send` never raises: transport errors return `status_code = 0`, provider errors return the real status. 5xx and 0 go back to `queued` with backoff; any 4xx goes straight to `failed` (a bad address will not improve with retries). After 5 attempts a row is `failed`. Both terminal states surface as admin flags with `last_error` intact. The cron script exits non-zero on DB or PDF failure so Railway shows a failed run rather than silence.
- **Retry ladder.** Delays after attempts 1–4: 1s, 5s, 30s, 2m, 10m — a provider blip self-heals, and a real outage is visible in the admin flag list within roughly fifteen minutes.
- **Logging & metrics.** stderr lines on enqueue failure, on each send attempt (kind, recipient domain, status code, attempt number), and on worker start/stop, following the existing `print`-to-stdout convention. `append_audit` records the admin-facing actions: `notification_resend`, `recipient_add`, `recipient_delete`, `customer_email_update`. No metrics backend exists in this project, so none is introduced. **Recipient addresses are logged by domain only, never in full.**
- **Auth / authz.** All four new routes call `require_admin(request)` first and redirect to `admin_login` on failure, matching every existing admin route. The cron script has no HTTP surface. `mailer.py` reads its key from env and never logs it.
- **Performance.** Request handlers gain one INSERT each — well inside the current response envelope (N1). The worker polls an indexed `(status, next_attempt_at)` predicate; at a handful of rows per day this is a small index scan. The daily PDF build already exists as the `/supplier-sheet.pdf` workload and moves to a cron process, off the web worker entirely.
- **Security.** `RESEND_API_KEY` is env-only and never logged, echoed, or rendered. Email addresses are validated by format at both entry points (`/register`, the admin edit route) before storage. Rendered copy is fixed text with a single account-code substitution — no user-controlled interpolation into the email body, so there is no header- or content-injection surface. Attachments are read from `data_paths` locations only; no user-supplied path reaches the mailer. Notification bodies contain only the recipient's own account code and booking (N4), and every enqueue that carries a voucher resolves its recipient from that voucher's own `account_code`.
- **Migrations / rollout.** Schema lands via the existing `db/apply.py` chain — `IF NOT EXISTS`, safe to re-run, no backfill. Rollout order: (1) deploy with `NOTIFICATIONS_ENABLED=false`, confirming the schema applies and the app boots; (2) verify the Resend sending domain (SPF/DKIM); (3) add internal recipients on the new admin page; (4) enable the flag; (5) enable the cron service. Rollback is setting the flag to false — queued rows simply stop draining and remain for later. The two new tables are inert if left behind. Nothing pre-existing is backfilled: bookings made before the flag is enabled are never retro-notified, per the REQ's out-of-scope list.

## Architecture Decisions Log

| # | Decision | Alternatives | Chosen Because | Satisfies REQs |
|---|----------|--------------|----------------|----------------|
| A1 | Postgres outbox table; handlers enqueue and return | Inline synchronous send; fire-and-forget thread per send | `--workers 1` makes an inline send a whole-app stall. The outbox also survives restarts and yields retries, flags, and history from one structure | R7, R8, N1, N5 |
| A2 | Daemon worker thread inside the web app drains the outbox | A second Railway cron service on a 1-minute schedule | Customers get their account code in seconds rather than up to a minute; queued rows still survive a restart because the queue is in Postgres. `SKIP LOCKED` keeps a cron sweep addable later | R1, N1 |
| A3 | Notification state in new Postgres tables via `db.pool`, bypassing the repo abstraction | Add methods to `CSVRepo`, `DBRepo`, `PostgresRepo` | Precisely the `audit_log.py` / `margin_store.py` precedent; avoids triplicating a Postgres-only feature across three backends | R6, R7, R8 |
| A4 | Resend over its HTTP API, isolated in `mailer.py` | SendGrid SDK; stdlib `smtplib` over the provider's SMTP | Account already provisioned; JSON attachments avoid MIME assembly; a one-file vendor boundary keeps a future swap cheap | R1, R3, R4, R11 |
| A5 | `requests` as the HTTP client | stdlib `urllib.request` (zero new dependencies) | Developer preference for a readable call site; 4 transitive packages is an acceptable price at this volume | — |
| A6 | The existing 500-on-asset-failure abort in the approve branch stays; the enqueue goes after `set_status` | Reorder `set_status` ahead of asset generation to implement REQ decision #9's attachment-less send | Preserves today's guarantee that an approved voucher always has assets. REQ #9's attachment-less path is consequently reachable only when a PNG disappears after approval, where the flag still applies | R4, and narrows REQ #9 |
| A7 | Unique `dedupe_key` per notification, shaped per kind | Application-level "have we sent this?" checks | A database constraint is the only version that holds under concurrent approves and repeated cron firings | R10, N3 |
| A8 | Re-send test is a SHA-256 fingerprint over amount, total, station, and PNG bytes, embedded in the dedupe key | Compare stored field values; a "needs re-notify" flag set by admin edits | Catches a regenerated voucher image that leaves the numbers unchanged, and needs no separate comparison logic — a changed fingerprint is simply a new key | R10 |
| A9 | Railway cron service at `0 16 * * *` UTC runs `scripts/send_daily_report.py` | APScheduler in-process; a sleep-loop thread | Mirrors `scripts/backup_postgres.py`; 16:00 UTC is 00:00 Asia/Manila year-round (PHT has no DST), and the report does not depend on web-process uptime | R11, N3 |
| A10 | The cron enqueues into the same outbox rather than sending directly | Send inline from the cron process | One delivery path, one retry policy, one flag surface; a provider blip at midnight retries instead of losing the report | R11, R13 |
| A11 | `attachment_ref` stores a reference; the worker resolves bytes at send time | Store base64 attachment bytes on the row | Keeps rows small and guarantees the customer receives the current voucher rather than a snapshot taken at enqueue | R4 |
| A12 | `NOTIFICATIONS_ENABLED` gates the worker and all enqueues | Ship ungated | Copy or deliverability problems can be stopped in seconds without a code deploy | — |
| A13 | Recipient list in its own table with a dedicated admin page | Env var list; controls bolted onto the existing `/admin` page | Staff churn must not need a redeploy; `admin_stations.html` is a proven template for this shape | R14 |
| A14 | Email format validated at both write points; the `customers.email` column stays nullable | `NOT NULL` on the column | R6 exists precisely to serve legacy blank rows; a NOT NULL constraint would break them on any update | R2, R6, R9 |
| A15 | `notifications.py` never touches Flask's request context | Reuse `audit_log.py`'s `flask.request` capture | It must run in a bare worker thread and a bare cron process, where no request exists | R11 |

## Risk & Stress-Test Scenarios

### Forward — runtime failure scenarios

| Scenario | How the Design Handles It |
|----------|---------------------------|
| Resend unreachable for 30 seconds | Sends return `status_code = 0`; rows go back to `queued` at 1s/5s/30s; delivered on recovery with no admin action. Request handlers were never involved |
| Resend returns 4xx for a nonexistent address | No retry — straight to `failed` with `last_error`; appears in the admin flag list within one poll cycle |
| Two admins approve the same voucher simultaneously | Both compute the same fingerprint, so both enqueues carry the same `dedupe_key`; the UNIQUE constraint makes the second a no-op. Exactly one email |
| App redeploys mid-send | The row is `sending` with an incremented `attempts`; a startup sweep returns stale `sending` rows older than 5 minutes to `queued`. Worst case one duplicate — the REQ explicitly prefers a duplicate to a drop |
| Cron fires twice at midnight, or the service restarts | `dedupe_key = daily:<Manila date>:<email>` collides; exactly one report per recipient per day (N3) |
| Postgres unavailable when a customer registers | Registration already tolerates this (`pg_error` sentinel at `main.py:1224`); the enqueue swallows its own failure. The customer gets their code on screen but no email, and no flag exists to record it — an accepted gap, since the flag store is the same unavailable database |
| `RESEND_API_KEY` missing in production | `mailer.is_configured()` is false; the worker leaves rows `queued` and logs the misconfiguration rather than burning the retry ladder. Rows drain once the key is set |
| The notifications table grows | Append-only, roughly tens of rows per day; the worker's poll is an index scan on `(status, next_attempt_at)`. No pruning needed for years |
| The daily PDF exceeds Resend's 40MB attachment limit | A 4xx from the provider → `failed` → visible flag. No size fallback, per the REQ's out-of-scope list |
| Recipient list is empty at midnight | The cron logs it and exits 0 without enqueuing — a legitimate state, not a failure |
| `build_supplier_pdf` raises inside the cron | Exit 2, nothing enqueued, Railway shows a failed run. No partial or corrupt report is emailed |
| Voucher PNG missing when the worker resolves the attachment | The email sends without it and the row records `attachment_missing` in `last_error`, flagging manual follow-up (the residual REQ #9 case under A6) |
| Local dev with no `DATABASE_URL` | Enqueue swallows, the worker never starts, the app runs exactly as it does today |
| Admin clicks Resend twice quickly | Two requeues of the same row; the second finds it already `queued` and is a no-op. `requeue` is idempotent while the row is queued |

### Backward — regression risk per touched area

| Touched area | What could regress | How we'd know / mitigation |
|--------------|--------------------|----------------------------|
| `main.py:697` `register()` | A required email rejects submissions that used to succeed; the register form is a live customer-acquisition path | `test_register_pg.py`, `test_register_optional_labels.py`, `test_register_success_text.py` must be updated to post an email and to assert the new rejection. Highest-risk item in this change |
| `main.py:1274` `/book` POST | Restructuring the exception swallow could make a booking fail, or could reference `created` when the repo write raised | Keep the try/except boundary exactly where it is and branch on a `created = None` initialization; `test_book_pg.py` and `test_book_calculator_inversion.py` cover the happy path |
| `main.py:651` approve branch | An enqueue placed above `set_status`, or one that raises, could leave a voucher unapproved — the exact failure the 500-abort was written to prevent | Enqueue strictly after `set_status`, wrapped so it cannot raise. `test_admin_approve_margin.py` asserts the status transition |
| `templates/admin.html` | A template error on the new flag column breaks the whole admin dashboard | `test_admin_display_layout.py` renders this page; extend it to cover both flagged and unflagged rows |
| `templates/admin_customer_lookup.html` | The new edit form could break the existing `picklist` / `not_found` / `detail` states | `test_admin_customers.py` exercises all three states |
| `persistence.py` / `db/postgres_repo.py` | A new method on shared repo classes; `CSVRepo`'s write must respect the existing `.lock` file or it can corrupt `customers.csv` | Mirror `create_customer_if_absent`'s `fcntl.flock` usage; `test_customer_repo.py` and `test_persistence.py` cover the class |
| `db/schema.sql` | A malformed statement breaks the Dockerfile CMD chain, and the app will not boot on Railway | `test_schema.py` plus `make test-db`, which applies the real schema |
| `rw.txt` | A cron entry in the wrong shape could disturb the deploy config | `test_railway_toml.py` asserts this file's contents |
| `report_pdf.build_supplier_pdf` | Two callers now; a future signature change silently breaks the nightly report while `/supplier-sheet.pdf` still passes | `test_daily_report.py` calls it through the cron path, so both callers are covered |
| `main.py` module import | A new import or `start_worker()` raising at import time takes down the entire app | Guard the imports the way `build_supplier_pdf` is guarded at `main.py:24-27`, and make `start_worker` catch everything |
| `data/customers.csv` dual-write | An email present in Postgres but absent from the CSV would resolve to "no recipient" under `PERSISTENCE_BACKEND=csv` | `_append_customer_csv_if_absent` already writes the email column; the admin edit route must update both stores through the repo |

## Open Questions

- Is the Resend sending domain verified (SPF/DKIM) for the chosen `MAIL_FROM`?
  - **Impact if unresolved:** mail lands in spam, which is indistinguishable from the feature not working, and the outbox will report every send as successful.
  - **Suggested default:** treat domain verification as a deploy prerequisite in the rollout sequence, before the flag is enabled.
- Does `DBRepo` (the SQLite backend) need `update_customer_email` for parity?
  - **Impact if unresolved:** a code path that raises `AttributeError` if anyone runs that backend with the admin edit route.
  - **Suggested default:** implement it — it is a few lines and keeps the three repo classes uniform.
- Should the worker's poll interval be configurable?
  - **Impact if unresolved:** tuning delivery latency requires a code change.
  - **Suggested default:** hardcode 5 seconds; add an env var only if it becomes a problem.
- Does Railway's cron service on this project run with the same image and env as the web service?
  - **Impact if unresolved:** the cron process may lack `DATABASE_URL` or `RESEND_API_KEY` and fail every night at exit 1.
  - **Suggested default:** mirror whatever `scripts/backup_postgres.py`'s existing cron service does, and verify with a manual run before enabling the schedule.

## Out of Scope

- Bounce, complaint, and unsubscribe webhooks (reason: REQ out-of-scope; needs an inbound HTTP surface and provider event handling).
- HTML or templated email bodies, and customer-name personalization (reason: the client's approved copy is plain text).
- A retry/queue broker such as Celery or RQ (reason: the volume does not justify the infrastructure; the outbox table plus a thread is sufficient).
- Multi-worker scaling of the drain loop (reason: `--workers 1` today; `SKIP LOCKED` already makes the design safe if that changes).
- Pruning or archiving old `notifications` rows (reason: growth is tens of rows per day).
- Emails on the `Redeemed` transition, on cancellation, or on order deletion (reason: REQ out-of-scope).
- Blueprint extraction of the new admin routes (reason: CLAUDE.md — `main.py` is deliberately the single-file app).
