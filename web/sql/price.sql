-- Spot prices (spec 0006 section 5.4 item 5): the mean price of each (sku, provider, UTC date)
-- over the rows with a numeric price above 0, any outcome, all regions and zones.
SELECT sku || '|' || provider AS key, strftime(d, '%Y-%m-%d') AS date,
  round(avg(price), 4)::DOUBLE AS price
FROM (SELECT sku, provider, date_trunc('day', ts) AS d, try_cast((details->>'price') AS DOUBLE) AS price
  FROM r WHERE probe_type IN ('aws_spot_price', 'az_spot'))
WHERE price > 0
GROUP BY sku, provider, d
ORDER BY key, date;
