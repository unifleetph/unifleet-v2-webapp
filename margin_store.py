"""
margin_store.py — global profit-margin % store (REQ-profit-margin, T1).

Postgres-backed singleton (`margin_settings`, id=1). The admin sets one
global margin percentage that gets deducted from the supplier-provided
discount before the customer ever sees it. The raw discount value in
`discounts` is never modified — margin is applied on top of it at
display/booking time via `MarginStore.apply()`.

Public API:
  MarginStore(dsn=None)         constructor
    .get()                      -> float, current margin_pct (0 if unset)
    .set(value, actor, reason)  -> None; validates 0-100, up to 2 decimals
    .apply(raw, margin_pct, exempt) -> float  (staticmethod, no I/O)

  MarginValueError               exception
"""

from typing import Optional

from db.pool import get_pool

VALUE_PRECISION_DECIMALS = 4


class MarginValueError(ValueError):
    """Raised when an invalid margin percentage is provided."""
    pass


class MarginStore:
    """Global profit-margin percentage, backed by the Postgres
    `margin_settings` singleton row (id=1)."""

    def __init__(self, dsn: Optional[str] = None):
        self._dsn = dsn

    def get(self) -> float:
        """Return the current margin percentage, or 0.0 if unset."""
        pool = get_pool(dsn=self._dsn)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT margin_pct FROM margin_settings WHERE id = 1")
                row = cur.fetchone()
        if row is None or row[0] is None:
            return 0.0
        return float(row[0])

    def set(self, value, actor: str = "system", reason: str = "") -> None:
        """Set the global margin percentage.

        Raises MarginValueError if `value` is not a number, is outside
        [0, 100], or carries more than 2 decimal places.
        """
        validated = self._validate(value)
        pool = get_pool(dsn=self._dsn)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO margin_settings (id, margin_pct, updated_at, updated_by)
                    VALUES (1, %s, NOW(), %s)
                    ON CONFLICT (id) DO UPDATE
                    SET margin_pct = EXCLUDED.margin_pct,
                        updated_at = NOW(),
                        updated_by = EXCLUDED.updated_by
                """, (validated, actor))
            conn.commit()

    @staticmethod
    def apply(raw: float, margin_pct: float, exempt: bool) -> float:
        """Return the customer-facing discount: `raw` unchanged if
        `exempt` (grandfathered row), otherwise `raw` reduced by
        `margin_pct`, rounded to VALUE_PRECISION_DECIMALS."""
        if exempt or not margin_pct:
            return raw
        return round(raw * (1 - margin_pct / 100), VALUE_PRECISION_DECIMALS)

    @staticmethod
    def _validate(value) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise MarginValueError("margin_pct must be a number (float).")
        if v < 0 or v > 100:
            raise MarginValueError("margin_pct must be between 0 and 100.")
        if round(v, 2) != v:
            raise MarginValueError("margin_pct accepts at most 2 decimal places.")
        return v
