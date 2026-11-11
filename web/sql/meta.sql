-- The facts of meta.json that come straight from the rows (spec 0006 section 7.3). One row.
-- The fields that come from the sweeps and the Capacity Block places are computed by the handler
-- from sweeps.sql and cb.sql over the whole data. Returns no row when nothing sets t0.
-- An hour is counted from the UTC hour start of the earliest anchor row. With no anchor row (only
-- Spot rows are kept) the span uses all the kept rows (spec 0006 section 5.2 item 3).
WITH span AS (
  SELECT date_trunc('hour', min(ts)) AS t0, max(ts) AS t_end FROM r WHERE anchor OR NOT EXISTS (SELECT 1 FROM r WHERE anchor)
), zs AS (
  SELECT min(floor((epoch_us(ts) - epoch_us(span.t0)) / 3600000000.0))::INTEGER AS h
  FROM r, span
  WHERE probe_type = 'aws_odcr' AND (details->>'zone_id') IS NOT NULL
)
SELECT
  strftime(span.t0, '%Y-%m-%dT%H:%M:%S.000Z') AS t0,
  strftime(date_trunc('milliseconds', span.t_end), '%Y-%m-%dT%H:%M:%S.%gZ') AS t_end,
  (ceil((epoch_us(span.t_end) - epoch_us(span.t0)) / 3600000000.0) + 1)::INTEGER AS nh,
  (SELECT count(*) FROM r)::INTEGER AS row_count,
  (SELECT list(DISTINCT try_cast((details->>'start_window_days') AS INTEGER) ORDER BY try_cast((details->>'start_window_days') AS INTEGER))
     FILTER (WHERE try_cast((details->>'start_window_days') AS INTEGER) IS NOT NULL)
   FROM r WHERE probe_type = 'aws_cb_offering') AS cb_horizon_days,
  zs.h AS zone_start_h,
  (SELECT list(DISTINCT zone ORDER BY zone) FILTER (WHERE zone IS NOT NULL)
   FROM r WHERE probe_type = 'aws_odcr' AND anchor
     AND (zs.h IS NULL OR floor((epoch_us(ts) - epoch_us(span.t0)) / 3600000000.0) < zs.h)) AS zones
FROM span, zs
WHERE span.t0 IS NOT NULL;
