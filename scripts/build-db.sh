#!/usr/bin/env bash
# Build the full-resolution offline database from the daily files (spec 0006, section 5.5).
#
# Usage: build-db.sh <ROWS_DIR> <OUT_FILE>
#   ROWS_DIR    the daily files of scripts/compact-rows.sh, for example data/rows
#   OUT_FILE    the DuckDB file to write, for example data/ftg.duckdb
#
# Every row of every probe type goes into the table `rows`: no bucketing, no dropped row, no
# scrub. The daily files are deduplicated already (scripts/compact-rows.sh).
# The file holds real provider data: it is git-ignored and never committed.
# Exit codes: 0 success, 1 DuckDB error, 2 bad arguments. Run it through `mise exec --`.
set -euo pipefail

[ "$#" -eq 2 ] || {
  echo "usage: build-db.sh <ROWS_DIR> <OUT_FILE>" >&2
  exit 2
}
[ -d "${1%/}" ] || {
  echo "build-db.sh: $1 is not a folder; run make data/rows first" >&2
  exit 2
}
# Absolute paths: the DuckDB step runs in a temporary folder.
rows=$(cd "$1" && pwd)
mkdir -p "$(dirname "$2")"
out=$(cd "$(dirname "$2")" && pwd)/$(basename "$2")
case "$rows$out" in
  *"'"*) echo "build-db.sh: a path must not contain a quote" >&2; exit 2 ;;
esac
command -v duckdb >/dev/null 2>&1 || {
  echo "build-db.sh: duckdb not found; run it through 'mise exec --'" >&2
  exit 1
}

tmp=$(mktemp -d "${TMPDIR:-/tmp}/ftg-db.XXXXXX")
# The database is written next to OUT_FILE, so that the last step is an atomic rename. The work
# file name ends in the PID, so 2 builds at the same time do not share a file.
dbtmp="$out.tmp.$$"
trap 'rm -rf "$tmp"; rm -f "$dbtmp" "$dbtmp.wal"' EXIT
# A killed build with this same PID can have left this file (the loop below skips a live PID): remove
# our own work file and its WAL before the ATTACH.
rm -f "$dbtmp" "$dbtmp.wal"
# A killed build can leave its work file: remove the files of runs that are gone. Only a name that
# ends in a PID (digits, then maybe .wal) is touched; any other name stays. A PID that kill cannot
# signal (EPERM) still counts as alive (ps -p finds it).
for w in "$out".tmp.*; do
  [ -e "$w" ] || continue
  b=${w%.wal}
  pid=${b##*.}
  case "$pid" in '' | *[!0-9]*) continue ;; esac
  kill -0 "$pid" 2>/dev/null || ps -p "$pid" >/dev/null 2>&1 || rm -f "$w"
done
: >"$tmp/empty.jsonl"

# Raw days are .jsonl.gz, days from a cloud daily file are .jsonl.zst; read_json reads both.
globs="'$tmp/empty.jsonl'"
for g in gz zst; do
  compgen -G "$rows/date=*/*.jsonl.$g" >/dev/null && globs="'$rows/date=*/*.jsonl.$g', $globs"
done

cat >"$tmp/build.sql" <<SQL
SET TimeZone = 'UTC';
ATTACH '$dbtmp' AS db;
CREATE TEMP VIEW src AS SELECT * FROM read_json(
  [$globs],
  format = 'newline_delimited', hive_partitioning = false, union_by_name = true, filename = true,
  columns = {
    ts_utc: 'TIMESTAMPTZ', run_id: 'VARCHAR', run_mode: 'VARCHAR', simulated: 'BOOLEAN',
    provider: 'VARCHAR', region: 'VARCHAR', zone: 'VARCHAR', probe_type: 'VARCHAR',
    sku: 'VARCHAR', placement_group_mode: 'VARCHAR', n_requested: 'BIGINT', n_placed: 'BIGINT',
    outcome: 'VARCHAR', error_code: 'VARCHAR', latency_ms: 'INTEGER', details: 'JSON'
  });
CREATE TABLE db.rows AS
SELECT ts_utc, run_id, run_mode,
  coalesce(simulated, run_mode = 'local', false) AS simulated,
  provider, region, zone, probe_type, sku, placement_group_mode, n_requested, n_placed,
  outcome, error_code, latency_ms, details
FROM src
ORDER BY ts_utc, provider, probe_type, run_id;
SELECT 'ftg_probe_type=' || probe_type FROM (SELECT DISTINCT probe_type FROM db.rows) ORDER BY 1;
SQL

(cd "$tmp" && duckdb -init /dev/null -bail -noheader -list :memory: <build.sql >types.txt) || {
  echo "build-db.sh: the DuckDB build failed; $out is kept" >&2
  exit 1
}

# One view per probe type, for example v_aws_spot_score. The names come from the rows, so a
# value that is no plain identifier is skipped.
{
  echo "SET TimeZone = 'UTC';"
  echo "ATTACH '$dbtmp' AS db;"
  echo "USE db;"
  sed -n 's/^ftg_probe_type=\([a-z0-9_]*\)$/\1/p' "$tmp/types.txt" | while read -r pt; do
    echo "CREATE VIEW v_$pt AS SELECT * EXCLUDE (probe_type) FROM rows WHERE probe_type = '$pt';"
  done
} >"$tmp/views.sql"
(cd "$tmp" && duckdb -init /dev/null -bail :memory: <views.sql) || {
  echo "build-db.sh: creating the views failed; $out is kept" >&2
  exit 1
}

mv -f "$dbtmp" "$out"
echo "build-db.sh: wrote $out"
