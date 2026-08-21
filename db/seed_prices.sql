-- db/seed_prices.sql — seed the `prices` table with the 10 default station prices.
--
-- Mirror's price_store._DEFAULT_STATIONS[].price_php_per_liter. The
-- `updated_at` is set to NOW() at seed time; subsequent price changes
-- (via the admin UI) will update this row and append to `price_history`.
--
-- Apply with: python db/apply.py db/schema.sql db/seed_stations.sql db/seed_prices.sql --dsn <DSN>
-- (one apply invocation, files applied in order).
--
-- Idempotency: ON CONFLICT (station_id, fuel_type) DO UPDATE. Re-running
-- the seed will overwrite Biodiesel prices with the seed values, which is
-- the intended behavior for a fresh local/staging database.
--
-- F3.1 (fuel-types-expansion): every row is seeded as fuel_type='Biodiesel'
-- only. Premium/Unleaded are intentionally left unseeded — all current
-- prices are considered stale and will be re-entered by an admin after
-- this feature ships (ARCH decision, REQ-fuel-types-expansion).

INSERT INTO prices (station_id, fuel_type, price_php_per_liter, updated_at) VALUES
  ('cleanfuel_valenzuela', 'Biodiesel', 60.00, NOW()),
  ('unioil_mandaluyong',   'Biodiesel', 59.10, NOW()),
  ('seaoil_bicutan',       'Biodiesel', 58.90, NOW()),
  ('ecooil_qc',            'Biodiesel', 58.30, NOW()),
  ('maximumfuel_val',      'Biodiesel', 57.95, NOW()),
  ('phoenix_meyc',         'Biodiesel', 58.20, NOW()),
  ('petro_gsanj',          'Biodiesel', 58.00, NOW()),
  ('gazz_binan',           'Biodiesel', 57.80, NOW()),
  ('filoil_stamesa',       'Biodiesel', 59.40, NOW()),
  ('petron_port',          'Biodiesel', 59.90, NOW())
ON CONFLICT (station_id, fuel_type) DO UPDATE SET
  price_php_per_liter = EXCLUDED.price_php_per_liter,
  updated_at          = NOW();
