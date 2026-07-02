"""One-off: backfill data/customers.csv -> Postgres `customers` (upsert).

Non-destructive: touches ONLY the customers table (ON CONFLICT DO UPDATE via
repo.create_customer). Safe to re-run. Unlike scripts/migrate_to_postgres.py,
this does NOT truncate audit_log or touch any other table.

Usage (inside the web container, prod DSN via env):
    DATABASE_URL=<prod-dsn> python scripts/backfill_customers.py
"""

import csv
import os
import sys

import data_paths
from db.postgres_repo import PostgresRepo


def main() -> int:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("UNIFLEET_DB_DSN")
    if not dsn:
        print("ERROR: set DATABASE_URL (or UNIFLEET_DB_DSN)")
        return 1

    path = str(data_paths.CUSTOMERS_CSV)
    if not os.path.exists(path):
        print(f"ERROR: {path} not found")
        return 1

    # Dedup by account_code, keep last occurrence (matches the F2.5 migrate rule).
    by_code = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            code = (r.get("account_code") or "").strip().upper()
            if code:
                by_code[code] = r

    print(f"== customer backfill ==")
    print(f"  source: {path}  ({len(by_code)} unique account_codes)")

    repo = PostgresRepo(dsn=dsn)
    n = 0
    try:
        for code, r in by_code.items():
            repo.create_customer(r)
            n += 1
            print(f"  upserted {code}")
    finally:
        repo.close()

    print(f"OK: {n} customers upserted (customers table only; audit_log untouched)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
