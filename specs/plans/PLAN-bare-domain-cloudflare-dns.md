# Plan: Fix Bare Domain (`unifleet.asia`) via Cloudflare DNS

## Context

Brief-11 item 3: `www.unifleet.asia` works, bare `unifleet.asia` redirects to a
GoDaddy parked-domain lander. `specs/requirements/REQ-brief-11-bare-domain.md`
(2026-09-02) decided the fix must make the apex **mirror** `www.` directly (no
redirect) via Railway custom-domain + DNS config — no application code involved.

Work was picked up 2026-09-04 and hit a real DNS-spec wall, logged in
`.trackway/records/`:
- GoDaddy's apex (`@`) can't take a `CNAME` (DNS spec: `@` must coexist with
  NS/SOA, which requires exclusivity that only `CNAME` violates).
- GoDaddy's plain DNS manager doesn't support ALIAS/ANAME or CNAME-flattening.
- GoDaddy's domain-forwarding/parking is an HTTP redirect — rejected, violates
  the "mirror, not redirect" requirement.
- Recommended fix (never implemented, no explicit sign-off recorded): move DNS
  hosting to **Cloudflare** (free tier), keep GoDaddy as registrar, use
  Cloudflare's CNAME-flattening at the apex.

Verified today: still broken — `dig unifleet.asia` returns nothing, no `@`
record exists. Confirmed live: `unifleet.asia`'s nameservers are still GoDaddy's
(`ns13/ns14.domaincontrol.com`), and GoDaddy also hosts **MX records** for mail
(`smtp.secureserver.net`, `mailstore1.secureserver.net`) — email is live on this
domain today. This is the main risk: **moving DNS hosting off GoDaddy means every
existing record (MX, `www`, wildcard, TXT/SPF/DKIM if any) must be recreated on
Cloudflare before cutover, or mail breaks.**

Intended outcome: `unifleet.asia` resolves to the same Railway service as
`www.unifleet.asia`, over HTTPS with a valid cert, with zero disruption to
existing mail delivery.

## Approach

Entirely operator/console work (Railway dashboard, GoDaddy, Cloudflare) — no
code changes. Two doc updates capture the outcome for future operators.

### Step 1 — Inventory existing GoDaddy DNS records (before touching anything)
In GoDaddy's DNS manager for `unifleet.asia`, record every existing entry:
`www` CNAME target, wildcard `*` CNAME target, the two MX records + priorities,
and any TXT/SPF/DKIM records (mail-related TXT records are easy to miss and
silently break deliverability if dropped). This list is the source of truth for
Step 3.

### Step 2 — Add Cloudflare account + zone (no cutover yet)
1. Create a free Cloudflare account, add `unifleet.asia` as a zone.
2. Cloudflare scans and imports existing DNS records automatically — cross-check
   the import against the Step 1 inventory; add anything it missed.
3. Do **not** change nameservers at the registrar yet.

### Step 3 — Configure records on Cloudflare
1. Re-create every record from Step 1 (MX, `www` CNAME, wildcard, TXT/SPF/DKIM),
   proxy status **DNS only** (grey cloud) for MX and any mail-related record —
   Cloudflare's proxy doesn't apply to MX and must not be orange-clouded for `www`
   either, since Railway needs to see real client IPs / issue its own TLS cert.
2. Add the apex `@` record pointing at the same Railway target `www` already
   uses, using Cloudflare's CNAME-flattening (a plain `CNAME`-style record at `@`
   that Cloudflare flattens to A/AAAA automatically).

### Step 4 — Railway custom domain
In the Railway prod web service → Settings → Networking → Custom Domain, add
`unifleet.asia` alongside the existing `www.unifleet.asia`. Railway will confirm
DNS resolution and auto-provision a TLS cert for the apex once Step 5 cutover
completes.

### Step 5 — Cutover nameservers at the registrar
At GoDaddy, change `unifleet.asia`'s nameservers from GoDaddy's own
(`ns13/ns14.domaincontrol.com`) to the two Cloudflare-assigned nameservers.
Propagation: minutes to ~24h (registrar-dependent). GoDaddy remains the
registrar of record — only DNS hosting moves.

### Step 6 — Verify (do not consider done until all pass)
- `dig +short NS unifleet.asia` → returns Cloudflare nameservers.
- `dig +short unifleet.asia` → resolves (Cloudflare-flattened A/AAAA).
- `curl -sSI https://unifleet.asia/` → 200/302 to the app (not a GoDaddy lander).
- `curl -sSI https://www.unifleet.asia/` → still works, unchanged (no regression).
- TLS: `https://unifleet.asia` loads with no cert warning (Railway-issued apex
  cert).
- **Mail check:** send/receive a test email through the domain's existing
  mailbox — confirms MX cutover didn't break delivery. This is the one
  destructive-if-wrong step in the whole plan; verify before declaring done.

### Step 7 — Update docs
- `docs/runbook-staging-and-dns.md` §5: replace the generic "use the
  registrar's ALIAS/ANAME/CNAME-flattening" guidance with the concrete finding
  — GoDaddy doesn't support it, Cloudflare does, here's the record list from
  Step 1/3 as a reference for the next person who touches this domain.
- `specs/requirements/REQ-brief-11-bare-domain.md`: no change needed (decisions
  already correct); the trackway record's "unresolved" outcome becomes stale
  once this ships — no action needed there, trackway is a log, not a doc to edit.

## Verification

All checks are the Step 6 list above, run from a shell with `dig`/`curl` (same
commands already used to confirm the bug is still live today). No pytest/CI
involvement — this is infra-only, matching REQ's explicit Out of Scope: "Any
application code change."

## Risk

- **Medium-high, but reversible:** nameserver cutover is the only irreversible-
  feeling step, but reverting is just switching nameservers back to GoDaddy's —
  DNS TTLs bound the blast radius to minutes—hours, not permanent.
- **Real risk is incompleteness, not the migration itself:** if Step 1's
  inventory misses a record (most likely an SPF/DKIM TXT record protecting mail
  deliverability), cutover silently degrades email rather than failing loudly.
  Step 1 and the Step 6 mail check exist specifically to catch this.
