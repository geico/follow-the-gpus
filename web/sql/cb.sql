-- Capacity Block checks and longest blocks (spec 0006 section 5.4 items 1 and 2).
-- Parameters: $t0 (TIMESTAMPTZ), $lo and $hi (keep the records with $lo <= h < $hi).
-- One flat result, which the handler groups by place (sku, region):
--   kind 'place'   one row for each place of the whole data, in or out of range
--   kind 'check'   n1 found, n2 leadDays, n3 durationH, n4 fee, n5 horizonDays, n6 nRequested, n7 leadH,
--                  s start (UTC ISO 8601; meta.now.cb only, the window payload ignores it)
--   kind 'longest' n1 found, n2 earliestLeadDays, n3 longestH, n4 earliestH, s earliestStartDate
-- A row with outcome 'error' is no record. A value of 0 or a missing value is null.
WITH c AS (
  SELECT *, floor((epoch_us(ts) - epoch_us($t0::TIMESTAMPTZ)) / 3600000000.0)::INTEGER AS h,
    CASE WHEN outcome = 'success' THEN 1 ELSE 0 END AS found
  FROM r
  WHERE probe_type IN ('aws_cb_offering', 'aws_cb_longest') AND outcome IS DISTINCT FROM 'error'
), places AS (
  SELECT DISTINCT sku, region FROM c
), u AS (
  SELECT 'place' AS kind, sku, region, NULL::INTEGER AS h, NULL::DOUBLE AS n1, NULL::DOUBLE AS n2,
    NULL::DOUBLE AS n3, NULL::DOUBLE AS n4, NULL::DOUBLE AS n5, NULL::DOUBLE AS n6, NULL::DOUBLE AS n7, NULL::VARCHAR AS s
  FROM places
  UNION ALL
  SELECT 'check', sku, region, h, found::DOUBLE,
    CASE WHEN outcome = 'success' AND try_cast((details->>'start') AS TIMESTAMPTZ) IS NOT NULL
      THEN round((epoch_us(try_cast((details->>'start') AS TIMESTAMPTZ)) - epoch_us(ts)) / 86400000000.0, 2)
    END,
    nullif(try_cast((details->>'duration_hours') AS DOUBLE), 0),
    nullif(try_cast((details->>'upfront_fee') AS DOUBLE), 0),
    nullif(try_cast((details->>'start_window_days') AS DOUBLE), 0),
    coalesce(n_requested, 1)::DOUBLE,
    CASE WHEN outcome = 'success' AND try_cast((details->>'start') AS TIMESTAMPTZ) IS NOT NULL
      THEN round((epoch_us(try_cast((details->>'start') AS TIMESTAMPTZ)) - epoch_us(ts)) / 3600000000.0, 4)
    END,
    CASE WHEN outcome = 'success'
      THEN strftime(try_cast((details->>'start') AS TIMESTAMPTZ), '%Y-%m-%dT%H:%M:%S.000Z')
    END
  FROM c
  WHERE probe_type = 'aws_cb_offering' AND h >= $lo AND h < $hi
  UNION ALL
  SELECT 'longest', sku, region, h, found::DOUBLE,
    CASE WHEN try_cast((details->>'earliest_start') AS TIMESTAMPTZ) IS NOT NULL
      THEN round((epoch_us(try_cast((details->>'earliest_start') AS TIMESTAMPTZ)) - epoch_us(ts)) / 86400000000.0, 2)
    END,
    nullif(try_cast((details->>'longest_duration_hours') AS DOUBLE), 0),
    nullif(try_cast((details->>'earliest_duration_hours') AS DOUBLE), 0),
    NULL, NULL, NULL,
    strftime(try_cast((details->>'earliest_start') AS TIMESTAMPTZ), '%Y-%m-%d')
  FROM c
  WHERE probe_type = 'aws_cb_longest' AND h >= $lo AND h < $hi
)
SELECT * FROM u ORDER BY sku, region, kind, h, n1, n2, n3, n4, n5, n6, n7, s;
