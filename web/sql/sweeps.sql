-- The sweeps of spec 0006 section 5.3, in 2 levels: the zone searches, then the groups.
-- Parameters: $t0 (TIMESTAMPTZ, hour 0), $lo and $hi (keep the sweeps with $lo <= h < $hi).
-- Field names: `got` is the most 1 zone placed (`gotZoneMax`),
-- `gotZoneSum` is the sum over the answered zones (a lower bound), `zones` and `zonesYes` are
-- the coverage. A null `n_config` is 1 value; a null `zone` is 1 zone.
WITH a AS (
  SELECT *, floor((epoch_us(ts) - epoch_us($t0::TIMESTAMPTZ)) / 3600000000.0)::BIGINT AS row_h
  FROM r
  WHERE prov AND anchor
), a2 AS (
  -- The round key: the run, or for a row with no run the hour of the row. The hour is finer than
  -- any cadence, so 1 cycle of a 1 h or a 2 h cadence is 1 round (spec 0006 section 5.3).
  SELECT *, coalesce(run_id, '~' || row_h::VARCHAR) AS rk FROM a
), zs AS (
  -- 1 row for each zone search.
  SELECT provider, probe_type, region, sku, n_config, rk, zone,
    min(ts) AS any_ts,
    coalesce(max(n_requested) FILTER (WHERE NOT up), 0) AS asked_z,
    count(*) FILTER (WHERE NOT up AND outcome IN ('success', 'partial', 'no_capacity', 'none')) AS n_ans,
    max(CASE WHEN NOT up THEN CASE outcome WHEN 'success' THEN n_requested
      WHEN 'partial' THEN n_placed END END) AS best,
    max(n_requested) FILTER (WHERE outcome = 'success') AS max_succ,
    arg_min(ts, (step, ts)) FILTER (WHERE NOT up) AS first_ts,
    arg_min(outcome, (step, ts)) FILTER (WHERE NOT up) AS first_outcome,
    arg_min_null(error_code, (step, ts)) FILTER (WHERE NOT up) AS first_err
  FROM a2
  GROUP BY provider, probe_type, region, sku, n_config, rk, zone
), zr AS (
  SELECT *,
    CASE WHEN n_ans = 0 THEN NULL ELSE coalesce(best, 0) END AS result_z,
    coalesce(first_ts, any_ts) AS tz
  FROM zs
), zt AS (
  SELECT *,
    CASE
      WHEN result_z IS NOT NULL THEN
        CASE WHEN result_z > 0 AND result_z >= asked_z THEN 2 WHEN result_z > 0 THEN 1 ELSE 0 END
      WHEN first_outcome = 'quota' THEN -1
      WHEN first_outcome = 'restricted' AND starts_with(probe_type, 'az_') THEN -1
      WHEN first_outcome = 'restricted_or_no_capacity' THEN -3
      WHEN first_outcome = 'restricted' AND probe_type = 'aws_odcr' THEN -4
      ELSE -2
    END AS state_z,
    coalesce(max_succ, result_z, 0) AS nmax_z
  FROM zr
), zg AS (
  SELECT *,
    coalesce(
      max(state_z) FILTER (WHERE result_z IS NOT NULL) OVER w,
      CASE
        WHEN bool_or(state_z = -3) OVER w THEN -3
        WHEN bool_or(state_z = -1) OVER w THEN -1
        WHEN bool_or(state_z = -4) OVER w THEN -4
        ELSE -2
      END) AS state
  FROM zt
  WINDOW w AS (PARTITION BY provider, probe_type, region, sku, n_config, rk)
), sw AS (
  SELECT provider, probe_type, region, sku, n_config, rk, state,
    min(tz) AS ts0,
    coalesce(max(asked_z) FILTER (WHERE result_z IS NOT NULL), max(asked_z), 0) AS asked,
    coalesce(max(result_z), 0) AS got_max,
    coalesce(sum(result_z), 0) AS got_sum,
    max(nmax_z) AS nmax,
    count(*) FILTER (WHERE NOT (probe_type = 'aws_odcr' AND zone IS NULL
      AND first_outcome IN ('restricted', 'error'))) AS zones,
    count(*) FILTER (WHERE result_z > 0) AS zones_yes,
    -- The per-zone list of an aws_odcr sweep: 1 entry for each named zone, in zone
    -- order; `got` is the zone's result, null when the zone did not answer.
    list(struct_pack(zone := zone, got := result_z::INTEGER) ORDER BY zone)
      FILTER (WHERE probe_type = 'aws_odcr' AND zone IS NOT NULL) AS zone_list,
    arg_min_null(first_err, (tz, zone)) FILTER (WHERE state_z = state) AS err,
    arg_min(CASE WHEN first_outcome = 'quota' THEN 'quota'
      WHEN first_outcome = 'restricted' THEN 'restricted' END, (tz, zone))
      FILTER (WHERE state_z = state) AS reason_z
  FROM zg
  GROUP BY provider, probe_type, region, sku, n_config, rk, state
), out AS (
  SELECT
    floor((epoch_us(ts0) - epoch_us($t0::TIMESTAMPTZ)) / 3600000000.0)::INTEGER AS h,
    round((epoch_us(ts0) - epoch_us($t0::TIMESTAMPTZ)) / 3600000000.0, 2)::DOUBLE AS t,
    provider AS cloud, region, sku, probe_type AS probe,
    asked::INTEGER AS asked, got_max::INTEGER AS "gotZoneMax",
    got_sum::INTEGER AS "gotZoneSum", state::INTEGER AS state, nmax::INTEGER AS "nMax",
    CASE WHEN state = -1 THEN reason_z END AS reason, err,
    zones::INTEGER AS zones, zones_yes::INTEGER AS "zonesYes", zone_list AS "zoneList", n_config, rk
  FROM sw
)
SELECT * EXCLUDE (n_config, rk) FROM out
WHERE h >= $lo AND h < $hi
ORDER BY h, t, cloud, region, sku, probe, state, asked, "gotZoneMax", "gotZoneSum", nMax, err, n_config, rk;
