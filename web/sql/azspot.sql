-- Azure Spot scores (spec 0006 section 5.4 item 4): the count of High, Medium, Low, and other
-- scores for each (sku, region, hour), regional rows only (zone is null).
-- Parameters: $t0 (TIMESTAMPTZ), $lo and $hi (keep $lo <= h < $hi).
SELECT sku || '|' || region AS key, h,
  count(*) FILTER (WHERE score = 'High')::INTEGER AS high,
  count(*) FILTER (WHERE score = 'Medium')::INTEGER AS medium,
  count(*) FILTER (WHERE score = 'Low')::INTEGER AS low,
  count(*) FILTER (WHERE score IS NULL OR score NOT IN ('High', 'Medium', 'Low'))::INTEGER AS other
FROM (
  SELECT sku, region, (details->>'score') AS score,
    floor((epoch_us(ts) - epoch_us($t0::TIMESTAMPTZ)) / 3600000000.0)::INTEGER AS h
  FROM r
  WHERE probe_type = 'az_spot_score' AND zone IS NULL
)
WHERE h >= $lo AND h < $hi
GROUP BY sku, region, h
ORDER BY key, h;
