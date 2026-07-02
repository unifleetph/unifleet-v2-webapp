# Staging environment & apex domain runbook

> **Operator manual for standing up a Railway staging environment and making the
> bare `unifleet.asia` apex domain resolve to the site.**
> Companion to [`docs/runbook.md`](runbook.md) (the production operator manual).
> These are console/DNS actions — no application code is involved.

## Contents

1. [Why](#1-why)
2. [Railway staging service](#2-railway-staging-service)
3. [Environment variable matrix](#3-environment-variable-matrix)
4. [Branch → environment mapping](#4-branch--environment-mapping)
5. [Apex domain (`unifleet.asia`) DNS](#5-apex-domain-unifleetasia-dns)
6. [Verification checklist](#6-verification-checklist)
7. [Teardown](#7-teardown)

---

## 1. Why

**Staging (brief item 1):** a separate Railway environment that mirrors prod so
changes on the `dev` branch can be exercised against a real Postgres before they
reach production.

**Apex DNS (brief item 2):** today only `www.unifleet.asia` resolves to the app.
Visitors typing the bare `unifleet.asia` fail. We add a DNS record so the apex
resolves too.

> ⚠️ **Hard rule:** staging MUST use its own Postgres database. Never point a
> staging service at the production `DATABASE_URL`. A staging deploy runs schema
> apply + can write data on start — against prod that is a data-integrity risk.

---

## 2. Railway staging service

Staging is a **separate Railway environment** (or a separate project, if your plan
lacks multi-environment) with its own Postgres and its own web service.

1. **Create the environment.** In the Railway project → **Environments** → **New
   Environment** named `staging` (duplicating `production` copies service config —
   review every variable afterward against the matrix in §3).
2. **Provision a dedicated Postgres.** Add a Postgres plugin/service *in the staging
   environment*. Confirm its `DATABASE_URL` is distinct from production's.
3. **Point the web service at the `dev` branch** (see §4).
4. **Set environment variables** per §3 — especially a staging-only `DATABASE_URL`,
   `SECRET_KEY`, and `ADMIN_PASSWORD`.
5. **Deploy.** The Dockerfile CMD auto-applies schema + seeds on start
   (`db/apply.py db/schema.sql db/seed_stations.sql db/seed_prices.sql`), same as
   prod — no manual schema step needed.
6. **(Optional) Backfill customers.** If staging needs the existing customer rows,
   run the one-time migration inside the staging service shell:
   `python scripts/migrate_to_postgres.py` (idempotent). Otherwise register fresh
   test customers via `/register`.

---

## 3. Environment variable matrix

Set these on the **staging** service. Values must differ from prod where noted.

| Variable | Prod | Staging | Notes |
|----------|------|---------|-------|
| `DATABASE_URL` | prod PG | **staging PG (distinct!)** | Never share. See §1 hard rule. |
| `PERSISTENCE_BACKEND` | `db` | `db` | Vouchers + customers go through Postgres. |
| `PORT` | set by Railway | set by Railway | Do not hardcode. |
| `SECRET_KEY` | prod secret | **staging secret (distinct)** | Stable value; signs admin session cookie. |
| `ADMIN_PASSWORD` | prod pw | **staging pw** | Admin `/admin/login`. Login disabled if unset. |
| `ADMIN_KEY` | prod key | staging key | Legacy `?key=` fallback. Key auth disabled if unset. |
| `SUPPLIER_API_TOKEN` | prod token | staging token | Supplier API gate. |
| `BASE_URL` | `https://unifleet.asia` | staging URL | Used in generated links. |

> After a "duplicate environment", audit **every** secret — duplication may copy
> prod secrets into staging. Rotate `DATABASE_URL`, `SECRET_KEY`, `ADMIN_PASSWORD`.

---

## 4. Branch → environment mapping

| Environment | Git branch | Auto-deploy |
|-------------|-----------|-------------|
| production | `main` | on push to `main` |
| staging | `dev` | on push to `dev` |

Set in the Railway service → **Settings → Source** → deployment branch = `dev` for
the staging service. Flow: merge feature → `dev` → auto-deploy to staging → verify →
promote to `main` → auto-deploy to prod.

---

## 5. Apex domain (`unifleet.asia`) DNS

Goal: both `unifleet.asia` (apex) and `www.unifleet.asia` resolve to the prod web
service. `www` already works; we add the apex.

1. **Railway:** prod web service → **Settings → Networking → Custom Domain** → add
   `unifleet.asia`. Railway shows the DNS target to point at.
2. **DNS provider (registrar):**
   - Apex records can't be a plain `CNAME`. Use the registrar's **ALIAS / ANAME /
     CNAME-flattening** record for `@` pointing at the Railway-provided target.
   - If the registrar only supports `A` records at the apex, use the `A` record /
     IP Railway provides for the domain.
   - Keep the existing `www` `CNAME` → Railway target unchanged.
   - (Optional) add a redirect so `www` → apex (or apex → `www`) for one canonical
     host.
3. **Wait for propagation** (minutes to ~1h) and for Railway to issue the TLS cert
   for the apex.

---

## 6. Verification checklist

**Staging:**
- [ ] Staging `DATABASE_URL` is confirmed distinct from prod (§1 hard rule).
- [ ] `curl -sS https://<staging-url>/healthz` → `ok` (200).
- [ ] Staging Postgres has the schema (deploy log shows `db/apply.py` ran).
- [ ] `/admin/login` accepts the staging `ADMIN_PASSWORD`; `/admin/prices` loads after login.
- [ ] Pushing to `dev` triggers a staging deploy (not prod).

**Apex DNS:**
- [ ] `dig +short unifleet.asia` returns the Railway target/IP.
- [ ] `curl -sSI https://unifleet.asia/` returns 200/302 (302 → `/book` is expected).
- [ ] `curl -sSI https://www.unifleet.asia/` still works (no regression).
- [ ] TLS cert valid for the apex (no browser warning).

---

## 7. Teardown

To retire staging: delete the staging web service and its Postgres, and remove the
`dev` deploy hook. Apex DNS is permanent — leave it in place.
