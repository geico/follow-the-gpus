#!/usr/bin/env bash
# Test scripts/compact-rows.sh and scripts/build-db.sh on a generated mirror: for each day and each of
# the 12 probe types, the row count is equal in the mirror (deduplicated), the daily files, and the
# database. No network, no cloud.
# Run it through `mise exec --` so that duckdb is the pinned one.
set -u
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/ftg-db-test.XXXXXX")
trap 'rm -rf "$work"' EXIT
fail=0
bad() { echo "FAIL $1" >&2; fail=1; }

types='aws_spot_score az_spot_score aws_spot_price az_spot aws_spot_advisor aws_cb_offering aws_cb_earliest aws_cb_longest az_precheck az_capacity_reservation az_vmss_flex aws_odcr'
m="$work/mirror"
n=0
for pt in $types; do
  case "$pt" in aws_*) cloud=aws ;; *) cloud=azure ;; esac
  for k in 1 2; do
    n=$((n + 1))
    d="$m/$cloud/experiment=ftg-test/provider=$cloud/probe_type=$pt/date=2026-10-1$k"
    mkdir -p "$d"
    # 3 rows in 1 file; all in the same hour, so an hourly rule would drop 2 of them.
    : >"$d/2026101${k}T000000Z_t${n}.jsonl"
    for r in 1 2 3; do
      printf '{"ts_utc":"2026-10-1%sT00:0%s:00.000Z","run_id":"r%s","simulated":false,"provider":"%s","region":"x","zone":null,"probe_type":"%s","sku":"s","placement_group_mode":null,"n_requested":1,"n_placed":null,"outcome":"success","error_code":null,"latency_ms":5,"details":{"a":%s}}\n' \
        "$k" "$r" "$r" "$cloud" "$pt" "$r" >>"$d/2026101${k}T000000Z_t${n}.jsonl"
    done
  done
done
# The same object under the old layout (a duplicate), and an old row with run_mode and no simulated.
src="$m/aws/experiment=ftg-test/provider=aws/probe_type=aws_odcr/date=2026-10-11"
old="$m/aws/run_mode=local/provider=aws/probe_type=aws_odcr/date=2026-10-11"
mkdir -p "$old"
cp "$src"/*.jsonl "$old/"
mkdir -p "$m/aws/run_mode=local/provider=aws/probe_type=aws_spot_score/date=2026-10-09"
printf '{"ts_utc":"2026-10-09T00:00:00.000Z","run_id":"o","run_mode":"local","provider":"aws","region":"x","zone":null,"probe_type":"aws_spot_score","sku":"s","placement_group_mode":null,"n_requested":null,"n_placed":null,"outcome":"success","error_code":null,"latency_ms":1,"details":{}}\n' \
  >"$m/aws/run_mode=local/provider=aws/probe_type=aws_spot_score/date=2026-10-09/20261009T000000Z_old1.jsonl"

rows="$work/rows"
out="$work/out/ftg.duckdb"
bash "$here/compact-rows.sh" "$m" "$rows" >/dev/null || bad "compact-rows.sh failed"
bash "$here/build-db.sh" "$rows" "$out" >/dev/null || bad "build-db.sh failed"

# Expected input rows of 1 (day, probe type): the lines of the files under experiment=, plus the
# files only under run_mode= (1 file per base name).
want_of() {
  find "$m" -name '*.jsonl' -path "*probe_type=$1/*" -path "*date=$2/*" |
    awk -F/ '{b=$NF; e=($0 ~ /\/experiment=/); if (!(b in s) || e) {s[b]=$0}} END{for (b in s) print s[b]}' |
    xargs cat | wc -l | tr -d ' '
}
for pt in $types; do
  for day in 2026-10-09 2026-10-11 2026-10-12; do
    want=$(want_of "$pt" "$day")
    [ "$want" != 0 ] || continue
    gz=0
    for f in "$rows/date=$day"/*_"$pt".jsonl.gz; do
      [ -f "$f" ] && gz=$((gz + $(gzip -dc "$f" | wc -l)))
    done
    [ "$want" = "$gz" ] || bad "$pt $day: input $want, daily files $gz"
    got=$(duckdb -readonly -noheader -list "$out" "SET TimeZone = 'UTC'; SELECT count(*) FROM rows WHERE probe_type = '$pt' AND CAST(ts_utc AS DATE) = DATE '$day'")
    [ "$want" = "$got" ] || bad "$pt $day: input $want, database $got"
  done
  want=$(want_of "$pt" '*')
  got=$(duckdb -readonly -noheader -list "$out" "SELECT count(*) FROM rows WHERE probe_type = '$pt'")
  [ "$want" = "$got" ] || bad "$pt: input $want, database $got"
  views=$(duckdb -readonly -noheader -list "$out" "SELECT count(*) FROM v_$pt")
  [ "$want" = "$views" ] || bad "v_$pt: input $want, view $views"
done
total=$(duckdb -readonly -noheader -list "$out" "SELECT count(*) FROM rows")
[ "$total" = "$((24 * 3 + 1))" ] || bad "total rows $total, want 73"
sim=$(duckdb -readonly -noheader -list "$out" "SELECT simulated FROM rows WHERE run_id = 'o'")
[ "$sim" = "true" ] || bad "old row run_mode=local should read simulated=true, got $sim"
hr=$(duckdb -readonly -noheader -list "$out" "SELECT count(*) FROM rows WHERE probe_type = 'az_spot' AND provider = 'azure'")
[ "$hr" = "6" ] || bad "same-hour rows were dropped: $hr"
# A row is stored exactly as in the source file.
src1=$(find "$m" -name '*_t1.jsonl' | head -1)
gzip -dc "$rows"/date=2026-10-11/aws_aws_spot_score.jsonl.gz | head -1 | cmp -s - <(head -1 "$src1") || bad "a daily file changed a row"

# A re-run with no new input rewrites no day file.
touch "$work/marker"
sleep 1
bash "$here/compact-rows.sh" "$m" "$rows" >/dev/null || bad "second compact failed"
[ -z "$(find "$rows" -type f -newer "$work/marker")" ] || bad "a re-run rewrote a day file"
# A new file in 1 day rebuilds that day only.
d="$m/aws/experiment=ftg-test/provider=aws/probe_type=aws_spot_score/date=2026-10-11"
cp "$src1" "$d/20261011T010000Z_new.jsonl"
touch "$work/marker"
sleep 1
bash "$here/compact-rows.sh" "$m" "$rows" >/dev/null || bad "third compact failed"
[ -n "$(find "$rows/date=2026-10-11" -type f -newer "$work/marker")" ] || bad "a new file did not rebuild its day"
[ -z "$(find "$rows/date=2026-10-12" -type f -newer "$work/marker")" ] || bad "an unchanged day was rewritten"
# The current UTC day is always rebuilt.
today=$(date -u +%Y-%m-%d)
t="$m/aws/experiment=ftg-test/provider=aws/probe_type=aws_spot_score/date=$today"
mkdir -p "$t"
cp "$src1" "$t/x.jsonl"
bash "$here/compact-rows.sh" "$m" "$rows" >/dev/null
touch "$work/marker"
sleep 1
bash "$here/compact-rows.sh" "$m" "$rows" >/dev/null
[ -n "$(find "$rows/date=$today" -type f -newer "$work/marker")" ] || bad "the current day was not rebuilt"

# A kill in the middle of the swap of a day never leaves a day that looks complete. The shim
# fails the last rename of day 2026-10-12, after the old folder was moved aside.
before=$(ls "$rows/date=2026-10-12" | wc -l | tr -d ' ')
d12="$m/aws/experiment=ftg-test/provider=aws/probe_type=aws_spot_score/date=2026-10-12"
mkdir -p "$d12"
cp "$src1" "$d12/20261012T020000Z_kill.jsonl"
mkdir -p "$work/shim"
cat >"$work/shim/mv" <<SHIM
#!/bin/sh
for a in "\$@"; do last=\$a; done
case "\$last" in */date=2026-10-12) exit 137 ;; esac
exec /bin/mv "\$@"
SHIM
chmod +x "$work/shim/mv"
PATH="$work/shim:$PATH" bash "$here/compact-rows.sh" "$m" "$rows" >/dev/null 2>&1
[ "$?" != 0 ] || bad "the shim should have killed the run"
bash "$here/compact-rows.sh" "$m" "$rows" >/dev/null || bad "run after the kill failed"
after=$(ls "$rows/date=2026-10-12" | wc -l | tr -d ' ')
[ "$after" -ge "$before" ] && [ -f "$rows/date=2026-10-12/.sig" ] || bad "a killed swap left an incomplete day ($before, $after)"
gz=0
for f in "$rows/date=2026-10-12"/*.jsonl.gz; do gz=$((gz + $(gzip -dc "$f" | wc -l))); done
[ "$gz" = "$(find "$m" -path '*date=2026-10-12/*' -name '*.jsonl' | xargs cat | wc -l | tr -d ' ')" ] || bad "day 2026-10-12 lost rows after a kill"
[ -z "$(find "$rows" -maxdepth 1 -name '.date=*')" ] || bad "a work folder was left"

# A file name with a space is not skipped.
sp="$m/aws/experiment=ftg-test/provider=aws/probe_type=aws_spot_score/date=2026-10-12/20261012T030000Z_a b.jsonl"
cp "$src1" "$sp"
bash "$here/compact-rows.sh" "$m" "$rows" >/dev/null || bad "compact with a space failed"
gz=0
for f in "$rows/date=2026-10-12"/*.jsonl.gz; do gz=$((gz + $(gzip -dc "$f" | wc -l))); done
[ "$gz" = "$(find "$m" -path '*date=2026-10-12/*' -name '*.jsonl' -print0 | xargs -0 cat | wc -l | tr -d ' ')" ] || bad "a file name with a space was skipped"

# Relative paths work for both scripts.
(cd "$work" && bash "$here/compact-rows.sh" mirror rel-rows >/dev/null && bash "$here/build-db.sh" rel-rows rel-out/x.duckdb >/dev/null) || bad "relative paths failed"
[ -f "$work/rel-out/x.duckdb" ] || bad "no database from relative paths"
[ -z "$(ls "$work/rel-out" | grep -v '^x.duckdb$')" ] || bad "a temporary database was left"

# Daily files from the cloud job. Prerequisite: duckdb (pinned in mise.toml) writes and
# reads the .zst files; no zstd binary is needed.
m2="$work/mirror2"
r2="$work/rows2"
o2="$work/out2/ftg.duckdb"
mkrows() { # <file> <count> <provider> <probe type> <day>
  : >"$1"
  for r in $(seq 1 "$2"); do
    printf '{"ts_utc":"%sT00:0%s:00.000Z","run_id":"r%s","simulated":false,"provider":"%s","region":"x","zone":null,"probe_type":"%s","sku":"s","placement_group_mode":null,"n_requested":1,"n_placed":null,"outcome":"success","error_code":null,"latency_ms":5,"details":{}}\n' \
      "$5" "$r" "$r" "$3" "$4" >>"$1"
  done
}
tozst() { # <jsonl file> <zst file>
  duckdb -c "COPY (SELECT l FROM read_csv('$1', header=false, columns={'l':'VARCHAR'}, delim=chr(1), quote='', escape='', auto_detect=false)) TO '$2' (FORMAT csv, HEADER false, DELIMITER chr(1), QUOTE '', ESCAPE '', COMPRESSION zstd)" >/dev/null
}
manifest() { # <data file> <row_count>  (the shape of prober/ftg_prober/compact.py)
  printf '{"raw_object_count": %s, "kept_object_count": 1, "row_count": %s, "unparsed_row_count": 0}\n' "$2" "$2" >"$1.manifest.json"
}
# <root> <cloud> <exp> <probe type> <day> <n rows> <manifest rows or "none"> : a daily folder
mkdaily() {
  d="$1/$2/experiment=$3/daily/v1/date=$5"
  mkdir -p "$d"
  mkrows "$work/d.jsonl" "$6" "$2" "$4" "$5"
  tozst "$work/d.jsonl" "$d/$4_$5.jsonl.zst"
  [ "$7" = none ] || manifest "$d/$4_$5.jsonl.zst" "$7"
}
mkraw() { # <root> <cloud> <exp or run_mode=x> <probe type> <day> <n rows> <name>
  case "$3" in run_mode=*) d="$1/$2/$3/provider=$2/probe_type=$4/date=$5" ;; *) d="$1/$2/experiment=$3/provider=$2/probe_type=$4/date=$5" ;; esac
  mkdir -p "$d"
  mkrows "$d/$7.jsonl" "$6" "$2" "$4" "$5"
}
# A: raw (3) and daily with manifest (4): 4 rows, not 7. Experiment B has raw only (2): kept, so 6.
mkraw "$m2" aws ftg-a aws_spot_score 2026-10-13 3 20261013T000000Z_a
mkdaily "$m2" aws ftg-a aws_spot_score 2026-10-13 4 4
mkraw "$m2" aws ftg-b aws_spot_score 2026-10-13 2 20261013T000000Z_ab
# Legacy: a run_mode= object of the same key (a twin of the experiment= copy) is skipped too.
mkraw "$m2" aws run_mode=local aws_spot_score 2026-10-13 3 20261013T000000Z_a
# B: raw (2) and daily with no manifest (5): 2 rows.
mkraw "$m2" aws ftg-a aws_spot_price 2026-10-14 2 20261014T000000Z_b
mkdaily "$m2" aws ftg-a aws_spot_price 2026-10-14 5 none
# C: Azure, daily with manifest only (2).
mkdaily "$m2" azure ftg-a az_spot 2026-10-13 2 2
# D: same day as A, other probe type, raw only (2): kept.
mkraw "$m2" aws ftg-a aws_spot_advisor 2026-10-13 2 20261013T000000Z_d
# E: two experiments, both with daily files (3 + 2 = 5), and raw of both skipped.
mkdaily "$m2" aws ftg-a aws_odcr 2026-10-15 3 3
mkdaily "$m2" aws ftg-b aws_odcr 2026-10-15 2 2
mkraw "$m2" aws ftg-a aws_odcr 2026-10-15 1 20261015T000000Z_e1
mkraw "$m2" aws ftg-b aws_odcr 2026-10-15 1 20261015T000000Z_e2
# F: a bad daily file: the raw rows (2 each) are used, with a warning naming the file.
mkdaily "$m2" aws ftg-a aws_cb_offering 2026-10-16 3 4            # count mismatch
mkraw "$m2" aws ftg-a aws_cb_offering 2026-10-16 2 20261016T000000Z_f1
mkdaily "$m2" aws ftg-a aws_cb_earliest 2026-10-16 3 3            # truncated early
head -c 10 "$m2/aws/experiment=ftg-a/daily/v1/date=2026-10-16/aws_cb_earliest_2026-10-16.jsonl.zst" >"$work/t.zst"
cp "$work/t.zst" "$m2/aws/experiment=ftg-a/daily/v1/date=2026-10-16/aws_cb_earliest_2026-10-16.jsonl.zst"
mkraw "$m2" aws ftg-a aws_cb_earliest 2026-10-16 2 20261016T000000Z_f2
mkdaily "$m2" aws ftg-a aws_cb_longest 2026-10-16 3 3             # empty file
: >"$m2/aws/experiment=ftg-a/daily/v1/date=2026-10-16/aws_cb_longest_2026-10-16.jsonl.zst"
mkraw "$m2" aws ftg-a aws_cb_longest 2026-10-16 2 20261016T000000Z_f3
mkdaily "$m2" azure ftg-a az_precheck 2026-10-16 3 3              # manifest without row_count
echo '{"raw_object_count": 1}' >"$m2/azure/experiment=ftg-a/daily/v1/date=2026-10-16/az_precheck_2026-10-16.jsonl.zst.manifest.json"
mkraw "$m2" azure ftg-a az_precheck 2026-10-16 2 20261016T000000Z_f4
mkdaily "$m2" aws ftg-a aws_cb_corrupt 2026-10-17 3 3             # zstd magic and then garbage: DuckDB errors
printf '\050\265\057\375garbage garbage garbage garbage' >"$m2/aws/experiment=ftg-a/daily/v1/date=2026-10-17/aws_cb_corrupt_2026-10-17.jsonl.zst"
mkraw "$m2" aws ftg-a aws_cb_corrupt 2026-10-17 2 20261017T000000Z_f5
bash "$here/compact-rows.sh" "$m2" "$r2" >/dev/null 2>"$work/warn.txt" || bad "compact with daily files failed"
for pt in aws_cb_offering aws_cb_earliest aws_cb_longest az_precheck; do
  grep -q "warning: .*/${pt}_2026-10-16.jsonl.zst" "$work/warn.txt" || bad "no warning naming the $pt daily file"
done
# A daily file that DuckDB cannot read: the warning prints the DuckDB error as returned.
grep -q "DuckDB said:" "$work/warn.txt" && grep -A3 "DuckDB said:" "$work/warn.txt" | grep -q -i "error" || bad "no DuckDB error in the warning of an unreadable daily file"
# A daily file with no manifest is not read and gets no warning; its raw rows are used (spec 0006, section 5.5).
grep -q "aws_spot_price_2026-10-14" "$work/warn.txt" && bad "a daily file with no manifest got a warning"
bash "$here/build-db.sh" "$r2" "$o2" >/dev/null || bad "build with daily files failed"
cnt() { duckdb -readonly -noheader -list "$o2" "SET TimeZone = 'UTC'; SELECT count(*) FROM rows WHERE probe_type = '$1' AND CAST(ts_utc AS DATE) = DATE '$2'"; }
chk() { got=$(cnt "$1" "$2"); [ "$got" = "$3" ] || bad "$4: want $3, got $got"; }
chk aws_spot_score 2026-10-13 6 "daily+raw day, other experiment kept, run_mode skipped"
chk aws_spot_price 2026-10-14 2 "daily without manifest"
chk az_spot 2026-10-13 2 "azure daily"
chk aws_spot_advisor 2026-10-13 2 "raw probe type next to a daily one"
chk aws_odcr 2026-10-15 5 "two experiments with daily files"
chk aws_cb_offering 2026-10-16 2 "count mismatch uses raw"
chk aws_cb_earliest 2026-10-16 2 "truncated file uses raw"
chk aws_cb_longest 2026-10-16 2 "empty file uses raw"
chk az_precheck 2026-10-16 2 "manifest without row_count uses raw"
# A mirror path that contains "daily" still reads its raw rows.
m3="$work/my-daily-mirror"
mkraw "$m3" aws ftg-a aws_spot_score 2026-10-13 3 20261013T000000Z_p
bash "$here/compact-rows.sh" "$m3" "$work/rows3" >/dev/null || bad "compact in a path with daily failed"
[ "$(gzip -dc "$work/rows3"/date=2026-10-13/*.gz | wc -l | tr -d ' ')" = 3 ] || bad "a mirror path with daily lost its raw rows"

# A mirror root that holds "experiment=" in its path: the experiment is the one under the
# cloud folder, so the daily file skips its own raw objects and the run_mode= twin is still deduplicated.
m4="$work/experiment=zz/mirror4"
mkraw "$m4" aws ftg-a aws_spot_score 2026-10-13 3 20261013T000000Z_a
mkdaily "$m4" aws ftg-a aws_spot_score 2026-10-13 4 4
mkraw "$m4" aws ftg-a aws_spot_price 2026-10-14 2 20261014T000000Z_b
mkraw "$m4" aws run_mode=local aws_spot_price 2026-10-14 2 20261014T000000Z_b
bash "$here/compact-rows.sh" "$m4" "$work/rows4" >/dev/null 2>&1 || bad "compact in a mirror root with experiment= failed"
bash "$here/build-db.sh" "$work/rows4" "$work/out4/ftg.duckdb" >/dev/null || bad "build in a mirror root with experiment= failed"
got=$(duckdb -readonly -noheader -list "$work/out4/ftg.duckdb" "SELECT count(*) FROM rows")
[ "$got" = 6 ] || bad "a mirror root with experiment= counted rows twice or lost rows: want 6, got $got"

# A work folder of a run that is still alive is not removed; one of a run that is gone is.
true & deadpid=$!
wait "$deadpid"
sleep 30 & livepid=$!
mkdir -p "$rows/.date=2026-10-12.new.$deadpid" "$rows/.date=2026-10-12.new.$livepid"
bash "$here/compact-rows.sh" "$m" "$rows" >/dev/null || bad "compact beside a live work folder failed"
[ -d "$rows/.date=2026-10-12.new.$livepid" ] || bad "the work folder of a live run was removed"
[ ! -e "$rows/.date=2026-10-12.new.$deadpid" ] || bad "the work folder of a dead run was left"
kill "$livepid" 2>/dev/null
rmdir "$rows/.date=2026-10-12.new.$livepid" 2>/dev/null
# A live process of another user: kill -0 fails with EPERM (real: kill -0 1 as a non-root user), but
# the work folder stays. PID 1 is only probed, never signaled. Skipped as root.
if [ "$(id -u)" != 0 ] && ! kill -0 1 2>/dev/null && ps -p 1 >/dev/null 2>&1; then
  mkdir -p "$rows/.date=2026-10-12.new.1"
  bash "$here/compact-rows.sh" "$m" "$rows" >/dev/null || bad "compact beside an EPERM work folder failed"
  [ -d "$rows/.date=2026-10-12.new.1" ] || bad "the work folder of a live run of another user was removed"
  rmdir "$rows/.date=2026-10-12.new.1" 2>/dev/null
fi

# 2 builds to the same file at the same time both succeed. A shim makes the first build
# wait after its DuckDB step, and a second build runs in that time.
realduckdb=$(command -v duckdb)
mkdir -p "$work/shim2"
cat >"$work/shim2/duckdb" <<SHIM
#!/bin/sh
"$realduckdb" "\$@"; rc=\$?
if [ ! -e "$work/shim2/flag" ]; then : >"$work/shim2/flag"; sleep 3; fi
exit \$rc
SHIM
chmod +x "$work/shim2/duckdb"
PATH="$work/shim2:$PATH" bash "$here/build-db.sh" "$rows" "$work/out5/ftg.duckdb" >/dev/null 2>&1 &
first=$!
while [ ! -e "$work/shim2/flag" ]; do sleep 0.2; done
bash "$here/build-db.sh" "$rows" "$work/out5/ftg.duckdb" >/dev/null 2>&1 || bad "a build beside another build failed"
wait "$first" || bad "the first of 2 builds at the same time failed"
[ "$(duckdb -readonly -noheader -list "$work/out5/ftg.duckdb" "SELECT count(*) FROM rows")" -gt 0 ] || bad "the database of 2 builds at the same time is empty"

# The experiment= preference tests a folder segment, as compact.py does: a file NAME that starts with
# experiment= does not count (the copy under a real experiment= folder wins). The folder "aaa" is
# listed first, so a script that tests the name keeps the wrong copy.
m5="$work/mirror4"
pk="provider=aws/probe_type=aws_spot_score/date=2026-10-14"
mkdir -p "$m5/aws/aaa/$pk" "$m5/aws/experiment=ftg-a/$pk"
echo '{"from":"name-only"}' >"$m5/aws/aaa/$pk/experiment=z.jsonl"
echo '{"from":"folder"}' >"$m5/aws/experiment=ftg-a/$pk/experiment=z.jsonl"
bash "$here/compact-rows.sh" "$m5" "$work/rows5" >/dev/null || bad "compact with a file named experiment= failed"
[ "$(gzip -dc "$work/rows5"/date=2026-10-14/*.gz)" = '{"from":"folder"}' ] || bad "a file NAME that starts with experiment= won over the experiment= folder copy"

# The leftover cleanup of build-db.sh touches only <out>.tmp.<digits> of a dead run.
mkdir -p "$work/out6"
: >"$work/out6/ftg.duckdb.tmp.bak"
: >"$work/out6/ftg.duckdb.tmp.$deadpid"
bash "$here/build-db.sh" "$rows" "$work/out6/ftg.duckdb" >/dev/null || bad "build with leftovers failed"
[ -e "$work/out6/ftg.duckdb.tmp.bak" ] || bad "a work-file name that does not end in a PID was removed"
[ ! -e "$work/out6/ftg.duckdb.tmp.$deadpid" ] || bad "the work file of a dead run was left"

# A stale work file with the PID of the current build is removed before the ATTACH. The
# shell makes a real DuckDB file with a rows table at <out>.tmp.<its PID>, then execs the build, so
# the build runs under that PID.
mkdir -p "$work/out8"
sh -c 'duckdb "$1.tmp.$$" "CREATE TABLE rows(x INT)" && exec bash "$2" "$3" "$1"' _ \
  "$work/out8/ftg.duckdb" "$here/build-db.sh" "$rows" >/dev/null 2>"$work/out8.err" || bad "build with a stale work file of its own PID failed: $(cat "$work/out8.err")"
[ -s "$work/out8/ftg.duckdb" ] || bad "no database after a build with a stale work file of its own PID"

# Trailing slashes on the mirror argument are all stripped.
bash "$here/compact-rows.sh" "$m3//" "$work/rows7" >/dev/null || bad "compact with 2 trailing slashes failed"
[ "$(gzip -dc "$work/rows7"/date=2026-10-13/*.gz | wc -l | tr -d ' ')" = 3 ] || bad "2 trailing slashes lost rows"

# A bad path exits 2.
bash "$here/compact-rows.sh" "$work/none" "$work/r2" >/dev/null 2>&1
[ "$?" = "2" ] || bad "a missing mirror should exit 2"
# A mirror argument of / (or //) is rejected before any scan, with exit 2 and no rows folder.
for root in / //; do
  bash "$here/compact-rows.sh" "$root" "$work/rows-root" >/dev/null 2>"$work/root.err"
  [ "$?" = "2" ] || bad "a mirror of $root should exit 2"
done
[ ! -e "$work/rows-root" ] || bad "a mirror of / created the rows folder"
# Other spellings of /: /. and /./ and a symlink to /. A bounded run stops a scan of / (rc 124).
# No timeout command is needed: the run is a background job, polled for 20 s, and only its own PID is killed.
bounded() {
  bash "$@" &
  bpid=$!
  bn=0
  while kill -0 "$bpid" 2>/dev/null && [ "$bn" -lt 40 ]; do sleep 0.5; bn=$((bn + 1)); done
  if kill -0 "$bpid" 2>/dev/null; then kill "$bpid" 2>/dev/null; wait "$bpid" 2>/dev/null; return 124; fi
  wait "$bpid"
}
ln -s / "$work/rootlink"
for root in /. /./ /.// "$work/rootlink" "$work/rootlink/"; do
  bounded "$here/compact-rows.sh" "$root" "$work/rows-root2" >/dev/null 2>"$work/root.err"
  [ "$?" = "2" ] || bad "a mirror of $root should exit 2"
  grep -q 'cannot be /' "$work/root.err" || bad "a mirror of $root gave no / message"
done
[ ! -e "$work/rows-root2" ] || bad "a mirror that resolves to / created the rows folder"
bash "$here/build-db.sh" "$work/none" "$work/x.duckdb" >/dev/null 2>&1
[ "$?" = "2" ] || bad "a missing rows folder should exit 2"

[ "$fail" = 0 ] && echo "test-build-db: ok"
exit "$fail"
