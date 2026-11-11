-- AWS Spot scores (spec 0006 section 5.4 item 3): 1 mean score for each (sku, region, hour).
-- Parameters: $t0 (TIMESTAMPTZ), $lo and $hi (keep $lo <= h < $hi). The score is an integer.
SELECT sku || '|' || region AS key, h, round(avg(score), 2)::DOUBLE AS score
FROM (
  SELECT sku, region, try_cast((details->>'score') AS INTEGER) AS score,
    floor((epoch_us(ts) - epoch_us($t0::TIMESTAMPTZ)) / 3600000000.0)::INTEGER AS h
  FROM r
  WHERE probe_type = 'aws_spot_score' AND outcome = 'success'
)
WHERE score IS NOT NULL AND h >= $lo AND h < $hi
GROUP BY sku, region, h
ORDER BY key, h;
