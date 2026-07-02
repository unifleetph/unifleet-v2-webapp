# AGENTS.md — UniFleet v2 Webapp

## Project overview

Flask webapp for fleet fuel management. Deployed on Railway (Dockerfile auto-detected). PostgreSQL database, Poetry for dependencies, Python 3.11.

## Local dev

```bash
make up          # Build + start (foreground)
make up-d        # Build + start (background)
make down        # Stop stack, keep Postgres volume
make clean       # Stop stack AND drop Postgres volume
make logs        # Tail web service logs
make shell       # Bash into web container
make psql        # Open psql in db container
```

## Tests

```bash
make test-db     # Run all 107 tests (uses Postgres in Docker)
pytest tests/test_sanity_app.py -v   # Run single test file
pytest tests/test_sanity_app.py::test_healthz -v  # Run single test
```

**Test quirks:**
- `make test-db` runs inside the web container with a live Postgres — no local Postgres needed
- `make test` runs in a one-shot container (no Postgres, skips DB-dependent tests)
- Unit tests in `test_postgres_repo.py` use SQLite via monkeypatch (no real Postgres needed)
- Integration tests in `test_postgres_repo_integration.py` require live PostgreSQL
- Tests create/drop ephemeral databases (`unifleet_test_<uuid>`) — safe to run in parallel

## Database

**Apply schema + seeds:**
```bash
python db/apply.py db/schema.sql db/seed_stations.sql db/seed_prices.sql
```

**In Railway:** Schema is auto-applied on every container start via Dockerfile CMD.

**In local dev:** Run the command above manually, or use `make test-db` for tests.

**Schema location:** `db/schema.sql` — 22 tables, all `CREATE TABLE IF NOT EXISTS`

**Seeds:**
- `db/seed_stations.sql` — station data
- `db/seed_prices.sql` — pricing tiers, time periods, fare rules (idempotent via `ON CONFLICT DO NOTHING`)

## Architecture

**Entry point:** `main.py` — Flask app with all routes in one file (~1200 lines)

**Key modules:**
- `price_store.py` — station/price data from CSV
- `persistence.py` — repo abstraction (CSV or DB backend)
- `discount_store.py` — discount storage
- `audit_log.py` — Postgres-backed audit log
- `data_paths.py` — central file-path registry
- `db/apply.py` — DB schema/seeds installer
- `db/postgres_repo.py` — PostgreSQL repository layer
- `db/pool.py` — connection pooling

**Railway deploy:** Dockerfile CMD chains `db/apply.py` → gunicorn

## Environment variables

Required for Railway:
- `DATABASE_URL` — PostgreSQL connection string
- `PORT` — Set by Railway (default 5000)
- `SECRET_KEY` — signs the admin session cookie. REQUIRED — must be stable across restarts or admin sessions drop. Random per-process fallback in dev only.
- `ADMIN_PASSWORD` — password for the `/admin/login` session. Login is disabled unless set.
- `ADMIN_KEY` — legacy `?key=` / `X-Admin-Key` admin fallback. No default; key auth disabled unless set.
- `SUPPLIER_API_TOKEN` — Supplier auth token

## Gotchas

- `rw.txt` is the Railway config file (not `railway.toml` — renamed in commit `ecdf9ae`)
- Template `admin_prices.html` must guard against `None` price values (use `is not none` check)
- `main.py` is the single-file app — no blueprints, no package structure
- `data/` directory is bind-mounted in dev, Railway Volume at `/data` in prod
- Seeds are idempotent — safe to run on every deploy
