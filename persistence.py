# persistence.py
import os, sqlite3, pandas as pd
from typing import List, Dict, Optional
from models import VOUCHER_COLUMNS, SCHEMA_SQL
from datetime import datetime
import random
import string

import data_paths  # F2.6: paths come from data_paths
from db.postgres_repo import PostgresRepo

MASTER_CSV = str(data_paths.LEGACY_MASTER_VOUCHERS_CSV)
SQLITE_PATH = str(data_paths.LEGACY_UNIFLEET_DB)

# Customer columns mirror the Postgres `customers` table (minus the
# auto-managed created_at). Order matches data/customers.csv.
CUSTOMER_COLUMNS = [
    "account_code", "contact_name", "contact_number", "email",
    "company_name", "fleet_size", "areas", "refuel_locations", "hq_locations",
]


def _coerce_fleet_size(v):
    """Coerce a CSV-world value to int, or None (parity with PostgresRepo)."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None

def _ensure_dirs():
    data_paths.ensure_dirs()

def get_repo(backend: str):
    """Factory: instantiate the right Repo implementation.

    backend values:
      - 'csv' (default) -> CSVRepo (legacy, in-process pandas dataframe)
      - 'db' -> DBRepo (legacy SQLite, only 5 of 7 methods implemented)
      - 'pg' or 'postgres' -> PostgresRepo (F2.2, full Repo interface)
    """
    backend = (backend or "csv").lower()
    if backend == "db":
        return DBRepo()
    if backend in ("pg", "postgres"):
        return PostgresRepo()
    return CSVRepo()

def _now_iso() -> str:
    # Keep simple UTC ISO (no timezone) for CSV consistency
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def _gen_voucher_id() -> str:
    # CSV-style ID: UF-YYYYMMDD-XXXXX (letters/digits)
    salt = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    return f"UF-{datetime.utcnow().strftime('%Y%m%d')}-{salt}"

class CSVRepo:
    def __init__(self):
        _ensure_dirs()

    def _ensure_cols(self, df: pd.DataFrame) -> pd.DataFrame:
        for c in VOUCHER_COLUMNS:
            if c not in df.columns:
                df[c] = ""
        # Preserve extra columns (e.g., computed_at, *_php). Reorder known columns to the front.
        known = [c for c in VOUCHER_COLUMNS if c in df.columns]
        others = [c for c in df.columns if c not in known]
        return df[known + others]

    def _read(self) -> pd.DataFrame:
        if not os.path.exists(MASTER_CSV):
            return pd.DataFrame(columns=VOUCHER_COLUMNS)
        df = pd.read_csv(MASTER_CSV, encoding='utf-8-sig')
        return self._ensure_cols(df)

    def _write(self, df: pd.DataFrame):
        self._ensure_cols(df).to_csv(MASTER_CSV, index=False, encoding='utf-8-sig')

    # ===== API =====

    def list_recent_vouchers(self, limit: int = 50) -> List[Dict]:
        df = self._read()

        # Use transaction_date when available
        has_tx = 'transaction_date' in df.columns
        if has_tx:
            df['_tx_parsed'] = pd.to_datetime(df['transaction_date'], errors='coerce')
        else:
            df['_tx_parsed'] = pd.NaT

        # Keep append order as a stable fallback
        df = df.reset_index(drop=False).rename(columns={'index': '_rowidx'})

        # Sort priority:
        # 1) has_date (True first)
        # 2) parsed date desc
        # 3) append order desc
        df['_has_date'] = df['_tx_parsed'].notna()
        df = df.sort_values(
            by=['_has_date', '_tx_parsed', '_rowidx'],
            ascending=[False, False, False]
        ).drop(columns=['_has_date', '_tx_parsed', '_rowidx'])

        return df.head(limit).to_dict(orient='records')



    def list_all_vouchers(self) -> List[Dict]:
        return self._read().to_dict(orient='records')

    def get_voucher(self, voucher_id: str) -> Optional[Dict]:
        df = self._read()
        rows = df[df['voucher_id'] == voucher_id]
        return None if rows.empty else rows.iloc[0].to_dict()

    def set_status(self, voucher_id: str, new_status: str, redemption_timestamp: str):
        df = self._read()
        if voucher_id not in df['voucher_id'].values:
            raise KeyError("voucher not found")
        if new_status == 'Redeemed':
            df.loc[df['voucher_id'] == voucher_id, ['status','redemption_timestamp']] = ['Redeemed', redemption_timestamp]
        else:
            df.loc[df['voucher_id'] == voucher_id, ['status','redemption_timestamp']] = [new_status, ""]
        # bump updated_at if present
        if 'updated_at' in df.columns:
            df.loc[df['voucher_id'] == voucher_id, 'updated_at'] = _now_iso()
        self._write(df)

    def append_vouchers(self, rows: List[Dict]):
        df = self._read()
        add_df = pd.DataFrame(rows)
        # normalize to schema
        for c in VOUCHER_COLUMNS:
            if c not in add_df.columns:
                add_df[c] = ""
        add_df = add_df[VOUCHER_COLUMNS]
        df = pd.concat([df, add_df], ignore_index=True)
        self._write(df)

    def update_voucher_fields(self, voucher_id: str, fields: Dict):
        """
        Update arbitrary columns for a voucher in the CSV.
        - Ensures any missing columns in `fields` are added to the CSV.
        - Bumps updated_at if that column exists.
        - Mirrors *_php -> legacy columns when present for compatibility.
        """
        # Load
        df = self._read()
        if df.empty or 'voucher_id' not in df.columns:
            raise KeyError("voucher not found")

        # Normalize types for matching
        df['voucher_id'] = df['voucher_id'].astype(str)
        voucher_id = str(voucher_id)
        mask = df['voucher_id'] == voucher_id
        if not mask.any():
            raise KeyError(f"voucher not found: {voucher_id}")

        # Ensure all target columns exist
        for col in fields.keys():
            if col not in df.columns:
                df[col] = ""

        # Write all provided fields
        for k, v in (fields or {}).items():
            df.loc[mask, k] = v

        # Compatibility mirrors
        if 'discount_total_php' in fields and 'discount_total' in df.columns:
            df.loc[mask, 'discount_total'] = fields['discount_total_php']
        if 'total_dispensed_php' in fields and 'total_dispensed' in df.columns:
            df.loc[mask, 'total_dispensed'] = fields['total_dispensed_php']

        # Bump updated_at (ensure column exists)
        if 'updated_at' not in df.columns:
            df['updated_at'] = ""
        from datetime import datetime
        df.loc[mask, 'updated_at'] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # Save
        self._write(df)

    # NEW: used by /book in main.py
    def create_unverified_booking(self, data: Dict) -> Dict:
        """
        Create a single Unverified booking row in master_vouchers.csv.
        Returns the created row (dict) including voucher_id.
        """
        df = self._read()

        # Start with schema-shaped row of blanks
        row = {c: "" for c in VOUCHER_COLUMNS}

        # Copy fields provided by caller (ignore extras)
        for k, v in (data or {}).items():
            if k in row:
                row[k] = v

        # --- Patch A: map booking date into schema fields used by table ---
        rd = (data or {}).get("refuel_datetime") or row.get("refuel_datetime") or ""
        if rd:
            if "expected_refill_date" in row and not row.get("expected_refill_date"):
                row["expected_refill_date"] = rd
            if "transaction_date" in row and not row.get("transaction_date"):
                row["transaction_date"] = rd

        # Voucher ID
        vid = (str(row.get('voucher_id') or '').strip()) or _gen_voucher_id()
        row['voucher_id'] = vid

        # Status & timestamps
        row['status'] = 'Unverified'
        if 'redemption_timestamp' in row:
            row['redemption_timestamp'] = ""
        now = _now_iso()
        if 'created_at' in row:
            row['created_at'] = now
        if 'updated_at' in row:
            row['updated_at'] = now

        # Append and save
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        self._write(df)

        return row

    # ===== Customers (CT1) =====

    def _read_customers(self) -> pd.DataFrame:
        path = str(data_paths.CUSTOMERS_CSV)
        if not os.path.exists(path):
            return pd.DataFrame(columns=CUSTOMER_COLUMNS)
        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
        for c in CUSTOMER_COLUMNS:
            if c not in df.columns:
                df[c] = ""
        return df

    def create_customer(self, data: Dict) -> Dict:
        """Upsert a customer keyed on account_code (upper-normalized) in
        customers.csv. Returns the stored row as a dict (fleet_size as int)."""
        d = data or {}
        code = str(d.get("account_code") or "").strip().upper()
        row = {c: "" for c in CUSTOMER_COLUMNS}
        for k, v in d.items():
            if k in row:
                row[k] = "" if v is None else str(v).strip()
        row["account_code"] = code

        df = self._read_customers()
        if not df.empty:
            mask = df["account_code"].astype(str).str.strip().str.upper() == code
        else:
            mask = pd.Series([], dtype=bool)

        if mask.any():
            for c in CUSTOMER_COLUMNS:
                df.loc[mask, c] = row[c]
        else:
            df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)

        df[CUSTOMER_COLUMNS].to_csv(
            str(data_paths.CUSTOMERS_CSV), index=False, encoding="utf-8-sig"
        )
        return self.get_customer(code)

    def get_customer(self, account_code: str) -> Optional[Dict]:
        """Fetch a customer by account_code (case-insensitive). None if absent."""
        code = str(account_code or "").strip().upper()
        df = self._read_customers()
        if df.empty:
            return None
        rows = df[df["account_code"].astype(str).str.strip().str.upper() == code]
        if rows.empty:
            return None
        d = rows.iloc[0].to_dict()
        out = {c: d.get(c, "") for c in CUSTOMER_COLUMNS}
        out["fleet_size"] = _coerce_fleet_size(out.get("fleet_size"))
        return out

    def customer_exists(self, account_code: str) -> bool:
        """True if a customer with this account_code (case-insensitive) exists."""
        return self.get_customer(account_code) is not None

class DBRepo:
    def __init__(self):
        _ensure_dirs()
        self.conn = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.conn:
            self.conn.executescript(SCHEMA_SQL)

    def _row_to_dict(self, row: sqlite3.Row) -> Dict:
        return {k: row[k] for k in row.keys()}

    # ===== API =====

    def list_recent_vouchers(self, limit: int = 50) -> List[Dict]:
        rows = self.conn.execute(
            """
            SELECT * FROM vouchers
            ORDER BY
              CASE WHEN created_at IS NOT NULL AND created_at <> '' THEN 0 ELSE 1 END,
              datetime(created_at) DESC,
              CASE WHEN transaction_date IS NOT NULL AND transaction_date <> '' THEN 0 ELSE 1 END,
              transaction_date DESC,
              rowid DESC
            LIMIT ?
            """,
            (int(limit),)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_all_vouchers(self) -> List[Dict]:
        rows = self.conn.execute("SELECT * FROM vouchers").fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_voucher(self, voucher_id: str) -> Optional[Dict]:
        row = self.conn.execute("SELECT * FROM vouchers WHERE voucher_id = ?", (voucher_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def set_status(self, voucher_id: str, new_status: str, redemption_timestamp: str):
        if new_status == 'Redeemed':
            self.conn.execute(
                "UPDATE vouchers SET status = ?, redemption_timestamp = ? WHERE voucher_id = ?",
                ('Redeemed', redemption_timestamp, voucher_id)
            )
        else:
            self.conn.execute(
                "UPDATE vouchers SET status = ?, redemption_timestamp = '' WHERE voucher_id = ?",
                (new_status, voucher_id)
            )
        self.conn.commit()

    def append_vouchers(self, rows: List[Dict]):
        cols = VOUCHER_COLUMNS
        placeholders = ",".join(["?"] * len(cols))
        sql = f"INSERT OR REPLACE INTO vouchers ({','.join(cols)}) VALUES ({placeholders})"
        vals = [tuple(r.get(c, None) for c in cols) for r in rows]
        with self.conn:
            self.conn.executemany(sql, vals)
