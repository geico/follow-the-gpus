-- Which rows the board shows (spec 0006 section 5.2 item 2): the count of real and of simulated
-- rows of the 9 probe types the board reads. The handler uses the simulated rows when no real row
-- exists. A row written before ADR 0011 has its `simulated` resolved by scripts/build-db.sh.
SELECT
  count(*) FILTER (WHERE NOT coalesce(simulated, false))::INTEGER AS real_rows,
  count(*) FILTER (WHERE coalesce(simulated, false))::INTEGER AS simulated_rows
FROM rows
WHERE probe_type IN ('aws_odcr', 'az_capacity_reservation', 'az_vmss_flex', 'aws_cb_offering',
  'aws_cb_longest', 'aws_spot_score', 'az_spot_score', 'aws_spot_price', 'az_spot');
