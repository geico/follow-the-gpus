-- The rows of the 9 probe types the board reads (spec 0006 section 5.2), as the temp table `r`.
-- The handler runs this once for each database file, with $sim = true only when the database
-- holds no real row of the 9 types. `anchor` marks the rows that make sweeps and Capacity Block
-- records: they set t0 (section 5.2 item 3). A cleanup row and an old `aws_odcr` `restricted`
-- row with a zone ("not asked", section 5.3) are no part of any zone search.
CREATE OR REPLACE TEMP TABLE r AS
SELECT ts_utc AS ts, run_id, provider, region, zone, probe_type, sku, n_requested, n_placed,
  outcome, error_code, details,
  coalesce((details->>'cleanup') = 'true', false) AS cleanup,
  coalesce(try_cast((details->>'search_step') AS INTEGER), 1) AS step,
  coalesce((details->>'search_direction') = 'up', false) AS up,
  try_cast((details->>'n_config') AS INTEGER) AS n_config,
  probe_type IN ('aws_odcr', 'az_capacity_reservation', 'az_vmss_flex') AS prov,
  CASE
    WHEN probe_type IN ('aws_odcr', 'az_capacity_reservation', 'az_vmss_flex') THEN
      NOT coalesce((details->>'cleanup') = 'true', false)
      AND NOT (probe_type = 'aws_odcr' AND outcome = 'restricted' AND zone IS NOT NULL)
    WHEN probe_type IN ('aws_cb_offering', 'aws_cb_longest') THEN outcome IS DISTINCT FROM 'error'
    ELSE false
  END AS anchor
FROM rows
WHERE probe_type IN ('aws_odcr', 'az_capacity_reservation', 'az_vmss_flex', 'aws_cb_offering',
    'aws_cb_longest', 'aws_spot_score', 'az_spot_score', 'aws_spot_price', 'az_spot')
  AND coalesce(simulated, false) = $sim;
