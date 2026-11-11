#!/usr/bin/env bash
# Write the committed synthetic store web/sample/ (spec 0006 section 8) with the duckdb CLI.
#
# Usage: make-sample.sh [OUT_DIR]     (default: web/sample next to this script)
#
# The rows are synthetic, simulated = true, for 48 hours from 2026-10-09T00:00:00Z. All values come
# from md5 of fixed strings (the seed below), so 2 runs give the same files. The script deletes
# only the *.jsonl files under OUT_DIR before it writes the new ones.
# Runs the duckdb CLI on PATH. Run it through `mise exec --` to get the pinned version.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
out=${1:-"$here/../sample"}
seed="ftg-sample-v1"

command -v duckdb >/dev/null 2>&1 || {
  echo "make-sample.sh: duckdb not found; run it through 'mise exec --' after 'make init'" >&2
  exit 1
}

tmp=$(mktemp -d "${TMPDIR:-/tmp}/ftg-sample.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

cat >"$tmp/sample.sql" <<SQL
SET TimeZone = 'UTC';
SET threads = 1;
CREATE MACRO rnd(s) AS (md5_number(s || '|$seed') % 1000000)::DOUBLE / 1000000.0;
CREATE MACRO iso(t) AS strftime(t, '%Y-%m-%dT%H:%M:%SZ');
CREATE MACRO uuid_of(s) AS
  substr(md5(s), 1, 8) || '-' || substr(md5(s), 9, 4) || '-4' || substr(md5(s), 14, 3) || '-8'
  || substr(md5(s), 18, 3) || '-' || substr(md5(s), 21, 12);
CREATE MACRO latency(s, lo, span) AS (lo + floor(rnd(s || '|lat') * span))::INTEGER;

CREATE TABLE hours AS
SELECT h, TIMESTAMPTZ '2026-10-09 00:00:00+00' + to_hours(h) AS hts FROM range(48) t(h);

-- 1 row per record. call_key names the probe call; 1 call is 1 file.
CREATE TABLE rows (
  probe_type VARCHAR, provider VARCHAR, region VARCHAR, zone VARCHAR, sku VARCHAR,
  ts TIMESTAMPTZ, call_key VARCHAR, ord INTEGER, outcome VARCHAR, error_code VARCHAR,
  placement_group_mode VARCHAR, n_requested INTEGER, n_placed INTEGER, latency_ms INTEGER,
  details JSON
);

-- 1. aws_spot_score: hourly. Score 1 to 10 from a slow wave. 1 cap, 1 timeout, 1 missing key.
INSERT INTO rows
WITH t AS (
  SELECT * FROM (VALUES
    ('us-east-1', 'p5.48xlarge', 4.0, 0.0), ('us-east-1', 'p4d.24xlarge', 7.0, 1.3),
    ('us-west-2', 'p5.48xlarge', 5.0, 2.1), ('us-west-2', 'p4d.24xlarge', 8.0, 0.7),
    ('us-east-1', 'p5.4xlarge', 6.0, 0.4), ('us-west-2', 'p5.4xlarge', 5.5, 1.6)
  ) v(region, sku, base, phase)
), c AS (
  SELECT t.*, h, hts + INTERVAL 5 MINUTE AS ts,
    'aws_spot_score|' || sku || '|' || region || '|' || h AS k
  FROM t CROSS JOIN hours
), o AS (
  SELECT *,
    CASE
      WHEN region = 'us-east-1' AND sku = 'p5.48xlarge' AND h = 13 THEN 'cap'
      WHEN region = 'us-west-2' AND sku = 'p4d.24xlarge' AND h = 30 THEN 'timeout'
      ELSE 'success'
    END AS outcome,
    greatest(1, least(10, round(base + 3 * sin(h / 7.0 + phase) + (rnd(k) - 0.5) * 2)))::INTEGER AS score
  FROM c
)
SELECT 'aws_spot_score', 'aws', region, NULL, sku, ts, k, 0, outcome,
  CASE outcome WHEN 'timeout' THEN 'RequestTimeout' ELSE NULL END,
  'n/a', 1, NULL, latency(k, 80, 300),
  CASE
    WHEN outcome <> 'success' THEN json_object()
    WHEN region = 'us-east-1' AND sku = 'p4d.24xlarge' AND h = 40 THEN json_object('instance_types', [sku])
    ELSE json_object('score', score, 'instance_types', [sku])
  END
FROM o;

-- 2. az_spot_score: hourly. Low, Medium, High. 1 quota, 1 restricted, 1 unknown score string.
INSERT INTO rows
WITH t AS (
  SELECT * FROM (VALUES
    ('eastus', 'Standard_ND96isr_H100_v5', 0.30, 0.0), ('eastus', 'Standard_NC24ads_A100_v4', 0.55, 1.1),
    ('westus2', 'Standard_ND96isr_H100_v5', 0.45, 2.4), ('westus2', 'Standard_NC24ads_A100_v4', 0.70, 0.4)
  ) v(region, sku, base, phase)
), c AS (
  SELECT t.*, h, hts + INTERVAL 7 MINUTE AS ts,
    'az_spot_score|' || sku || '|' || region || '|' || h AS k
  FROM t CROSS JOIN hours
), o AS (
  SELECT *,
    CASE
      WHEN region = 'eastus' AND sku = 'Standard_ND96isr_H100_v5' AND h = 20 THEN 'quota'
      WHEN region = 'westus2' AND sku = 'Standard_ND96isr_H100_v5' AND h = 33 THEN 'restricted'
      ELSE 'success'
    END AS outcome,
    base + 0.3 * sin(h / 5.0 + phase) + (rnd(k) - 0.5) * 0.2 AS v
  FROM c
)
SELECT 'az_spot_score', 'azure', region, NULL, sku, ts, k, 0, outcome,
  CASE outcome WHEN 'quota' THEN 'OperationNotAllowed' WHEN 'restricted' THEN 'SkuNotAvailable' ELSE NULL END,
  'n/a', 1, NULL, latency(k, 150, 400),
  CASE
    WHEN outcome <> 'success' THEN json_object()
    WHEN region = 'westus2' AND sku = 'Standard_NC24ads_A100_v4' AND h = 8 THEN
      json_object('score', 'Unknown', 'desired_count', 1)
    ELSE json_object('score', CASE WHEN v < 0.33 THEN 'Low' WHEN v < 0.66 THEN 'Medium' ELSE 'High' END,
      'desired_count', 1)
  END
FROM o;

-- 3. aws_spot_price: hourly, 1 call per (sku, Region) with 1 row per zone. A price row only at a
-- price change, else a none row. Zone b starts 3 h later, so its first hours show the none glyph.
-- 1 gap (no call) and 1 error call.
INSERT INTO rows
WITH t AS (
  SELECT * FROM (VALUES
    ('us-east-1', 'us-east-1a', 0, 'p5.48xlarge', 12.0), ('us-east-1', 'us-east-1b', 1, 'p5.48xlarge', 12.6),
    ('us-east-1', 'us-east-1a', 0, 'g6e.xlarge', 0.75), ('us-east-1', 'us-east-1b', 1, 'g6e.xlarge', 0.71),
    ('us-west-2', 'us-west-2a', 0, 'p5.48xlarge', 11.4), ('us-west-2', 'us-west-2b', 1, 'p5.48xlarge', 12.1),
    ('us-west-2', 'us-west-2a', 0, 'g6e.xlarge', 0.69), ('us-west-2', 'us-west-2b', 1, 'g6e.xlarge', 0.73),
    ('us-east-1', 'us-east-1a', 0, 'p5.4xlarge', 1.9), ('us-east-1', 'us-east-1b', 1, 'p5.4xlarge', 2.0),
    ('us-west-2', 'us-west-2a', 0, 'p5.4xlarge', 1.8), ('us-west-2', 'us-west-2b', 1, 'p5.4xlarge', 1.95)
  ) v(region, zone, zi, sku, base)
), c AS (
  SELECT t.*, h, hts + INTERVAL 2 MINUTE AS ts,
    'aws_spot_price|' || sku || '|' || region || '|' || h AS k,
    list_position([0, 9, 20, 31, 40], h - zi * 3) AS change
  FROM t CROSS JOIN hours
  WHERE NOT (region = 'us-east-1' AND sku = 'p5.48xlarge' AND h = 15)
), o AS (
  SELECT *,
    CASE
      WHEN region = 'us-west-2' AND sku = 'p5.48xlarge' AND h = 25 THEN 'error'
      WHEN change IS NOT NULL THEN 'success'
      ELSE 'none'
    END AS outcome
  FROM c
)
SELECT 'aws_spot_price', 'aws', region, zone, sku, ts, k, zi, outcome,
  CASE outcome WHEN 'error' THEN 'InternalError' ELSE NULL END,
  'n/a', 1, NULL, latency(k, 60, 200),
  CASE outcome
    WHEN 'success' THEN json_object(
      'price', round(base * [1.0, 0.82, 1.0, 1.25, 1.05][change] * (0.97 + rnd(k || zone) * 0.06), 4),
      'price_ts', iso(ts - INTERVAL 1 MINUTE - to_seconds((rnd(k || zone || 's') * 200)::BIGINT)))
    ELSE json_object()
  END
FROM o;

-- 4. az_spot: hourly. Eviction rate range and price. 1 null eviction rate, 1 null price,
-- 1 eviction rate with no leading integer, 1 error.
INSERT INTO rows
WITH t AS (
  SELECT * FROM (VALUES
    ('eastus', 'Standard_NC40ads_H100_v5', 0.35, 1.9, 0.0), ('eastus', 'Standard_NV36ads_A10_v5', 0.15, 0.62, 1.4),
    ('westus2', 'Standard_NC40ads_H100_v5', 0.55, 1.8, 2.2), ('westus2', 'Standard_NV36ads_A10_v5', 0.25, 0.66, 0.6)
  ) v(region, sku, base, price, phase)
), c AS (
  SELECT t.*, h, hts + INTERVAL 9 MINUTE AS ts,
    'az_spot|' || sku || '|' || region || '|' || h AS k
  FROM t CROSS JOIN hours
), o AS (
  SELECT *,
    CASE WHEN region = 'westus2' AND sku = 'Standard_NC40ads_H100_v5' AND h = 36 THEN 'error' ELSE 'success' END
      AS outcome,
    greatest(0, least(4, floor((base + 0.25 * sin(h / 8.0 + phase) + (rnd(k) - 0.5) * 0.15) * 5)))::INTEGER AS bin
  FROM c
)
SELECT 'az_spot', 'azure', region, NULL, sku, ts, k, 0, outcome,
  CASE outcome WHEN 'error' THEN 'InternalServerError' ELSE NULL END,
  'n/a', 1, NULL, latency(k, 200, 500),
  CASE
    WHEN outcome <> 'success' THEN json_object()
    ELSE json_object(
      'eviction_rate',
        CASE
          WHEN region = 'eastus' AND sku = 'Standard_NC40ads_H100_v5' AND h = 17 THEN NULL
          WHEN region = 'eastus' AND sku = 'Standard_NV36ads_A10_v5' AND h = 44 THEN 'unknown'
          ELSE ['0-5', '5-10', '10-15', '15-20', '20+'][bin + 1]
        END,
      'price',
        CASE
          WHEN region = 'westus2' AND sku = 'Standard_NV36ads_A10_v5' AND h = 28 THEN NULL
          ELSE round(price * (1 + 0.15 * sin(h / 6.0 + phase)), 4)
        END,
      'os_type', 'Linux')
  END
FROM o;

-- 5. aws_spot_advisor: daily at 00:10. The hold rule fills the other buckets. 1 none row.
INSERT INTO rows
WITH t AS (
  SELECT * FROM (VALUES
    ('us-east-1', 'p5.48xlarge', 1), ('us-east-1', 'p4d.24xlarge', 0),
    ('us-west-2', 'p5.48xlarge', 3), ('us-west-2', 'p4d.24xlarge', 2)
  ) v(region, sku, bin)
), c AS (
  SELECT t.*, h, hts + INTERVAL 10 MINUTE AS ts,
    'aws_spot_advisor|' || sku || '|' || region || '|' || h AS k
  FROM t CROSS JOIN hours WHERE h % 24 = 0
)
SELECT 'aws_spot_advisor', 'aws', region, NULL, sku, ts, k, 0,
  CASE WHEN region = 'us-west-2' AND sku = 'p4d.24xlarge' AND h = 24 THEN 'none' ELSE 'success' END,
  NULL, 'n/a', 1, NULL, latency(k, 400, 900),
  CASE
    WHEN region = 'us-west-2' AND sku = 'p4d.24xlarge' AND h = 24 THEN json_object()
    ELSE json_object(
      'interruption_range', ['<5%', '5-10%', '10-15%', '15-20%', '>20%'][least(4, bin + h // 24) + 1],
      'savings_pct', (55 + floor(rnd(k) * 30))::INTEGER)
  END
FROM c;

-- 6. aws_cb_offering: every 2 h. 1 success row per offering seen, or 1 none row. 1 unknown outcome.
-- The sample follows config/targets.yaml: p5.4xlarge asks for 1, 4, and 8 instances;
-- every other type asks for 1.
INSERT INTO rows
WITH t AS (
  SELECT * FROM (VALUES
    ('us-east-1', 'p5.48xlarge', 0.0), ('us-east-1', 'p5en.48xlarge', 1.7), ('us-east-1', 'p5.4xlarge', 0.6),
    ('us-west-2', 'p5.48xlarge', 0.9), ('us-west-2', 'p5en.48xlarge', 2.5), ('us-west-2', 'p5.4xlarge', 1.3)
  ) v(region, sku, phase)
  CROSS JOIN (VALUES (1), (4), (8)) q(cnt)
  WHERE cnt = 1 OR sku = 'p5.4xlarge'
), c AS (
  SELECT t.*, h, hts + INTERVAL 15 MINUTE AS ts,
    'aws_cb_offering|' || sku || '|' || region || '|' || h || CASE WHEN cnt = 1 THEN '' ELSE '|' || cnt END AS k
  FROM t CROSS JOIN hours WHERE h % 2 = 0
), o AS (
  SELECT *,
    CASE WHEN region = 'us-west-2' AND sku = 'p5en.48xlarge' AND h = 10 THEN 'throttled' END AS odd,
    greatest(0, least(6, round(2.5 + 2.5 * sin(h / 9.0 + phase) + (rnd(k) - 0.5) * 2)))::INTEGER AS n
  FROM c
), r AS (
  SELECT o.*, j FROM o, range(greatest(1, CASE WHEN odd IS NULL THEN n ELSE 1 END)) x(j)
)
SELECT 'aws_cb_offering', 'aws', region, NULL, sku, ts, k, j,
  CASE WHEN odd IS NOT NULL THEN odd WHEN n = 0 THEN 'none' ELSE 'success' END,
  NULL, 'n/a', cnt, NULL, latency(k, 300, 600),
  CASE
    WHEN odd IS NOT NULL OR n = 0 THEN json_object()
    ELSE json_object(
      'start', iso(date_trunc('day', ts) + to_days(j + 1) + INTERVAL 11 HOUR + INTERVAL 30 MINUTE),
      'end', iso(date_trunc('day', ts) + to_days(j + 2) + INTERVAL 11 HOUR + INTERVAL 30 MINUTE),
      'duration_hours', 24,
      'start_window_days', 14,
      'upfront_fee', round(24 * 31.5 * (0.9 + rnd(k || j) * 0.2), 2))
  END
FROM r;

-- 7 and 8. Fill-ratio probes: every 2 h, 2 VMs requested. success, partial, or no_capacity.
INSERT INTO rows
WITH t AS (
  SELECT * FROM (VALUES
    ('az_capacity_reservation', 'eastus', 'Standard_NC40ads_H100_v5', 'n/a', 20),
    ('az_capacity_reservation', 'eastus', 'Standard_NC4as_T4_v3', 'n/a', 20),
    ('az_capacity_reservation', 'eastus2', 'Standard_NC40ads_H100_v5', 'n/a', 20),
    ('az_capacity_reservation', 'eastus2', 'Standard_NC4as_T4_v3', 'n/a', 20),
    ('az_vmss_flex', 'eastus', 'Standard_NC40ads_H100_v5', 'multi', 25),
    -- 'single' rows below are old-row coverage: the prober no longer writes single (ADR 0021)
    ('az_vmss_flex', 'eastus', 'Standard_NV36ads_A10_v5', 'single', 25),
    ('az_vmss_flex', 'eastus2', 'Standard_NC40ads_H100_v5', 'multi', 25),
    ('az_vmss_flex', 'eastus2', 'Standard_NV36ads_A10_v5', 'single', 25)
  ) v(probe_type, region, sku, pgm, minute)
), c AS (
  SELECT t.*, h, hts + to_minutes(minute) AS ts,
    probe_type || '|' || sku || '|' || region || '|' || h AS k
  FROM t CROSS JOIN hours WHERE h % 2 = 0
), o AS (
  SELECT *,
    CASE
      WHEN probe_type = 'az_capacity_reservation' AND region = 'eastus2' AND sku = 'Standard_NC4as_T4_v3' AND h = 12
        THEN 'restricted_or_no_capacity'
      WHEN probe_type = 'az_vmss_flex' AND region = 'eastus' AND sku = 'Standard_NC40ads_H100_v5' AND h = 18 THEN 'quota'
      WHEN probe_type = 'az_capacity_reservation' AND region = 'eastus' AND sku = 'Standard_NC40ads_H100_v5' AND h = 4 THEN 'partial'
      WHEN probe_type = 'az_vmss_flex' AND region = 'eastus2' AND sku = 'Standard_NV36ads_A10_v5' AND h = 6 THEN 'partial'
      WHEN probe_type = 'az_capacity_reservation' AND region = 'eastus' AND sku = 'Standard_NC4as_T4_v3' AND h = 30
        THEN 'no_capacity'
      WHEN probe_type = 'az_vmss_flex' AND region = 'eastus' AND sku = 'Standard_NV36ads_A10_v5' AND h = 40 THEN 'no_capacity'
      WHEN rnd(k) < 0.12 THEN 'no_capacity'
      WHEN rnd(k) < 0.32 THEN 'partial'
      ELSE 'success'
    END AS outcome
  FROM c
)
SELECT probe_type, 'azure', region, NULL, sku, ts, k, 0, outcome,
  CASE outcome
    WHEN 'no_capacity' THEN 'AllocationFailed'
    WHEN 'partial' THEN 'AllocationFailed'
    WHEN 'restricted_or_no_capacity' THEN 'SkuNotAvailable'
    WHEN 'quota' THEN 'OperationNotAllowed'
    ELSE NULL
  END,
  pgm, 2,
  CASE outcome WHEN 'success' THEN 2 WHEN 'partial' THEN 1 WHEN 'no_capacity' THEN 0 ELSE NULL END,
  latency(k, 2000, 9000), json_object()
FROM o;

-- 9. aws_odcr: every 2 h at :30, 1 call (1 run_id) for each (Region, type). Before hour 24 the probe asks
-- 1 zone and writes no details.zone_id (the old method); from hour 24 it asks every zone of the Region
-- and writes details.zone_id (meta.zoneStartH). A zone search halves from C: yes, a partial
-- yes, or no. After a full yes it sometimes goes up (ADR 0023). Lane kinds: cap (the states of capacity),
-- quota (every ask stops at our limit), restricted (the type is not offered in the Region).
INSERT INTO rows
WITH l AS (
  SELECT * FROM (VALUES
    ('us-east-1', 'use1', 'p5.4xlarge', 4, 0.25, 'cap'), ('us-west-2', 'usw2', 'p5.4xlarge', 4, 0.35, 'cap'),
    ('us-east-1', 'use1', 'g5.xlarge', 8, 0.60, 'cap'), ('us-west-2', 'usw2', 'g4dn.xlarge', 8, 0.70, 'cap'),
    ('us-east-1', 'use1', 'p4d.24xlarge', 2, 0.0, 'quota'), ('us-west-2', 'usw2', 'p4d.24xlarge', 2, 0.0, 'quota'),
    ('us-east-1', 'use1', 'p6-b200.48xlarge', 2, 0.0, 'restricted')
  ) v(region, short, sku, cfg, pfull, kind)
), z AS (
  SELECT * FROM (VALUES
    ('us-east-1', 'us-east-1a', 0), ('us-east-1', 'us-east-1b', 1), ('us-east-1', 'us-east-1c', 2),
    ('us-west-2', 'us-west-2a', 0), ('us-west-2', 'us-west-2b', 1)
  ) v(region, zone, zi)
), c AS (
  SELECT l.*, z.zone, z.zi, h, hts + INTERVAL 30 MINUTE AS ts, h >= 24 AS pz,
    'aws_odcr|' || sku || '|' || region || '|' || h AS k
  FROM l JOIN z USING (region) CROSS JOIN hours
  WHERE h % 2 = 0 AND (h >= 24 OR zi = 0) AND NOT (kind = 'restricted' AND h >= 24 AND zi > 0)
), s AS (
  SELECT *, rnd(k || zone || '1') < pfull AS ok1, rnd(k || zone || '2') < 0.5 AS ok2,
    rnd(k || zone || '3') < 0.4 AS ok3, rnd(k || zone || 'u') AS ru,
    region = 'us-west-2' AND sku = 'p5.4xlarge' AND h = 40 AS err
  FROM c
), u AS (
  SELECT *, 1 AS step, cfg AS n, false AS up,
    CASE WHEN kind <> 'cap' THEN kind WHEN err THEN 'error' WHEN ok1 THEN 'success' ELSE 'no_capacity' END AS outcome
  FROM s
  UNION ALL
  SELECT *, 2, greatest(1, cfg // 2), false, CASE WHEN ok2 THEN 'success' ELSE 'no_capacity' END
  FROM s WHERE kind = 'cap' AND NOT err AND NOT ok1
  UNION ALL
  SELECT *, 3, 1, false, CASE WHEN ok3 THEN 'success' ELSE 'no_capacity' END
  FROM s WHERE kind = 'cap' AND NOT err AND NOT ok1 AND NOT ok2 AND cfg // 2 > 1
  UNION ALL
  SELECT *, 2, cfg * 2, true, CASE WHEN ru < 0.15 THEN 'success' ELSE 'no_capacity' END
  FROM s WHERE kind = 'cap' AND NOT err AND ok1 AND ru < 0.3 AND h >= 8
)
SELECT 'aws_odcr', 'aws', region, CASE WHEN kind = 'restricted' AND pz THEN NULL ELSE zone END, sku, ts, k,
  zi * 10 + step, outcome,
  CASE outcome
    WHEN 'no_capacity' THEN 'InsufficientInstanceCapacity' WHEN 'quota' THEN 'VcpuLimitExceeded'
    WHEN 'error' THEN 'InternalError' WHEN 'restricted' THEN 'Unsupported' ELSE NULL
  END,
  'n/a', n, CASE outcome WHEN 'success' THEN n WHEN 'no_capacity' THEN 0 ELSE NULL END, latency(k || zone, 2000, 7000),
  CASE
    WHEN outcome = 'restricted' THEN json_object()
    ELSE json_merge_patch(
      json_object('search_step', step, 'n_config', cfg),
      CASE WHEN up THEN json_object('search_direction', 'up') ELSE json_object() END,
      CASE WHEN pz THEN json_object('zone_id', short || '-az' || (zi + 1)) ELSE json_object() END)
  END
FROM u;

-- An old per-zone restricted row ("not asked", spec 0006 section 5.3) and 2 cleanup rows (not attempts).
INSERT INTO rows
SELECT 'aws_odcr', 'aws', 'us-west-2', 'us-west-2b', 'p5.4xlarge', hts + INTERVAL 30 MINUTE,
  'aws_odcr|old-restricted', 0, 'restricted', 'Unsupported', 'n/a', 4, NULL, 1500, json_object()
FROM hours WHERE h = 10;
INSERT INTO rows
SELECT 'aws_odcr', 'aws', 'us-east-1', 'us-east-1a', 'p5.4xlarge', hts + INTERVAL 45 MINUTE,
  'aws_odcr|cleanup|' || h, 0, 'success', NULL, 'n/a', 0, NULL, 1800000,
  json_object('cleanup', true, 'reservation_id', 'cr-sample' || h)
FROM hours WHERE h IN (12, 36);

-- 10. aws_cb_longest: every 2 h. The longest block that starts soon. 1 none row for every 5th ask, 1 error.
INSERT INTO rows
WITH t AS (
  SELECT * FROM (VALUES
    ('us-east-1', 'p5.48xlarge'), ('us-east-1', 'p5en.48xlarge'),
    ('us-west-2', 'p5.48xlarge'), ('us-west-2', 'p5.4xlarge')
  ) v(region, sku)
), c AS (
  SELECT t.*, h, hts + INTERVAL 17 MINUTE AS ts, 'aws_cb_longest|' || sku || '|' || region || '|' || h AS k
  FROM t CROSS JOIN hours WHERE h % 2 = 0
), o AS (
  SELECT *,
    CASE
      WHEN region = 'us-west-2' AND sku = 'p5.48xlarge' AND h = 14 THEN 'error'
      WHEN rnd(k) < 0.2 THEN 'none'
      ELSE 'success'
    END AS outcome
  FROM c
)
SELECT 'aws_cb_longest', 'aws', region, NULL, sku, ts, k, 0, outcome,
  CASE outcome WHEN 'error' THEN 'InternalError' ELSE NULL END,
  'n/a', 1, NULL, latency(k, 300, 600),
  CASE outcome
    WHEN 'success' THEN json_object(
      'earliest_start', iso(ts + to_days(1 + (rnd(k || 'e') * 5)::INTEGER)),
      'longest_duration_hours', [168, 336, 672][1 + (rnd(k || 'd') * 3)::INTEGER],
      'earliest_duration_hours', 24)
    ELSE json_object()
  END
FROM o;

-- 1 output line per record: the object key, a tab, and the JSONL line.
SELECT
  provider || '/experiment=sample/provider=' || provider || '/probe_type=' || probe_type
    || '/date=' || strftime(ts, '%Y-%m-%d') || '/' || strftime(ts, '%Y%m%dT%H%M%SZ')
    || '_' || regexp_replace(lower(sku || '-' || region), '[^a-z0-9-]', '-', 'g')
    || '_' || uuid_of(call_key) || '.jsonl' AS path,
  to_json({
    ts_utc: iso(ts), run_id: 'sim-r' || substr(md5(call_key), 1, 12), simulated: true,
    provider: provider, region: region, zone: zone, probe_type: probe_type, sku: sku,
    placement_group_mode: placement_group_mode, n_requested: n_requested, n_placed: n_placed,
    outcome: outcome, error_code: error_code, latency_ms: latency_ms, details: details
  })::VARCHAR AS line
FROM rows
ORDER BY path, ord;
SQL

(cd "$tmp" && duckdb -init /dev/null -bail -noheader -list -separator "$(printf '\t')" :memory: <sample.sql >lines.tsv)

mkdir -p "$out"
find "$out" -type f -name '*.jsonl' -delete
find "$out" -mindepth 1 -type d -empty -delete
awk -F '\t' -v root="$out" '
  $1 != cur {
    if (cur != "") close(file)
    cur = $1
    file = root "/" cur
    dir = file
    sub(/\/[^\/]*$/, "", dir)
    if (!(dir in made)) {
      system("mkdir -p \"" dir "\"")
      made[dir] = 1
    }
  }
  { print $2 > file }
' "$tmp/lines.tsv"

files=$(find "$out" -type f -name '*.jsonl' | wc -l | tr -d ' ')
rows=$(wc -l <"$tmp/lines.tsv" | tr -d ' ')
echo "make-sample.sh: wrote $rows rows in $files files to $out"
