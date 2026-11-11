#!/usr/bin/env bash
# Concatenate the mirror's row files into 1 gzipped JSONL file per (UTC day, provider, probe type)
# (spec 0006, section 5.5).
#
# Usage: compact-rows.sh <MIRROR_DIR> <ROWS_DIR>
#   MIRROR_DIR  the folder that `make data/sync` fills, for example data/live
#   ROWS_DIR    the folder to write, for example data/rows. It gets
#               date=YYYY-MM-DD/<provider>_<probe_type>.jsonl.gz and a date=YYYY-MM-DD/.sig
#
# A file holds the rows of its day exactly as stored: the lines of the source files, in file-name
# order, with no field changed and no row dropped. A source file that exists under both layouts
# (`run_mode=` and `experiment=`) counts once, preferring the `experiment=` copy (the base name
# is unique, spec 0006 section 5.1). The day is the `date=` key of the object (UTC).
# A day is built in a sibling folder and swapped in whole; a folder without .sig is rebuilt.
# A day is rebuilt only when its input changed: .sig holds a checksum of the source file names
# and sizes (a stored object is immutable, so a name and a size show a new or changed file).
# The current UTC day is always rebuilt.
# Daily files: the cloud job writes `experiment=<name>/daily/v1/date=<D>/<probe_type>_<D>.jsonl.zst`
# and a sidecar `<probe_type>_<D>.jsonl.zst.manifest.json` with `row_count` (the number of newline
# bytes of the decompressed file, so a blank line counts). The key has no provider: the provider is
# the cloud folder of the mirror (`<MIRROR_DIR>/<provider>/experiment=<name>/...`, the layout of
# `make data/sync`). A daily file is trusted only when the manifest has a parsable
# row_count above 0 and DuckDB decompresses the file to exactly that many lines; otherwise a warning
# names the file and the raw objects are used. A trusted file is copied unchanged to
# date=D/<provider>_<probe_type>.<experiment>.jsonl.zst and skips the raw objects of that (day,
# provider, probe type) under the same `experiment=<name>/` only; the legacy `run_mode=` raw objects
# of that key are skipped too (they all have an `experiment=` twin: spec 0003, section 9A).
# A `daily/` folder is never read as raw rows. Every test on a path (`experiment=`, `provider=`,
# `daily/`) is on the path under the mirror root, so a mirror root that holds `experiment=` is safe.
# Needs duckdb on PATH when a daily file exists (run it through `mise exec --`).
# The files hold real provider data: git-ignored.
# Exit codes: 0 success, 1 failure, 2 bad arguments (also a MIRROR_DIR of /).
set -euo pipefail

[ "$#" -eq 2 ] || {
  echo "usage: compact-rows.sh <MIRROR_DIR> <ROWS_DIR>" >&2
  exit 2
}
mirror=$1
while [ "${mirror%/}" != "$mirror" ] && [ -n "${mirror%/}" ]; do mirror=${mirror%/}; done
[ -n "$mirror" ] && [ "$mirror" != "/" ] || {
  echo "compact-rows.sh: the mirror folder cannot be /" >&2
  exit 2
}
rows=${2%/}
[ -d "$mirror" ] || {
  echo "compact-rows.sh: $mirror is not a folder; run make data/sync first" >&2
  exit 2
}
# A spelling that resolves to / (for example /., /./, or a symlink to /) is also refused.
[ "$(cd -P "$mirror" && pwd -P)" != "/" ] || {
  echo "compact-rows.sh: the mirror folder cannot be /" >&2
  exit 2
}
mkdir -p "$rows"
tmp=$(mktemp -d "${TMPDIR:-/tmp}/ftg-rows.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
today=$(date -u +%Y-%m-%d)
# A killed run can leave a sibling work folder (its name ends in the PID of the run): remove it.
# A folder of a run that is still alive is not ours to remove. A day is swapped in as a whole
# folder with rename(2); its .sig is written last, so a folder without .sig is rebuilt.
for w in "$rows"/.date=*.new.* "$rows"/.date=*.old.*; do
  [ -e "$w" ] || continue
  # kill -0 fails with EPERM for a live process of another user: ps -p finds it (as build-db.sh does).
  kill -0 "${w##*.}" 2>/dev/null || ps -p "${w##*.}" >/dev/null 2>&1 || rm -rf "$w"
done

# size <TAB> path of every row file, then 1 winner per (day, provider, probe type, base name).
# wc -c prints "<size> <path>"; the path is the rest of the line, so a space in it is kept.
find "$mirror" -type f -name '*.jsonl' -exec wc -c {} + |
  awk -v root="$mirror/" '{ n = $1; p = $0; sub(/^ *[0-9]+ /, "", p); r = substr(p, length(root) + 1); if (r ~ /(^|\/)experiment=[^\/]+\/daily\//) next; if (match(r, /provider=[^\/]+\/probe_type=[^\/]+\/date=[0-9-]+\/[^\/]+$/)) print n "\t" p "\t" r }' |
  awk -F'\t' '{
    p = $2; r = $3
    match(r, /provider=[^\/]+\/probe_type=[^\/]+\/date=[0-9-]+\/[^\/]+$/)
    t = substr(r, RSTART); split(t, a, "/")
    prov = substr(a[1], 10); pt = substr(a[2], 12); day = substr(a[3], 6)
    key = day "\t" prov "\t" pt "\t" a[4]
    # A folder segment that starts with experiment= (the file name does not count; r is under the mirror root).
    pref = (r ~ /(^|\/)experiment=[^\/]*\//) ? 1 : 0
    if (!(key in path) || (pref && !pr[key])) { path[key] = p; sz[key] = $1; pr[key] = pref }
  } END { for (k in path) print k "\t" sz[k] "\t" path[k] }' |
  LC_ALL=C sort -t "$(printf '\t')" -k1,1 -k2,2 -k3,3 -k4,4 >"$tmp/winners.raw.tsv"

# day, provider, probe type, experiment, size, path of every daily file that matches its manifest.
# Line count of a .zst file, blank lines included; "-" when DuckDB cannot read it.
# The DuckDB error goes to $tmp/duckdb.err; the warning prints it as returned.
zst_lines() {
  duckdb -noheader -list :memory: "SELECT count(*) FROM read_csv('$1', header=false, columns={'l':'VARCHAR'}, delim=chr(1), quote='', escape='', auto_detect=false, strict_mode=false)" 2>"$tmp/duckdb.err" || echo -
}
: >"$tmp/daily.tsv"
find "$mirror" -type f -name '*.jsonl.zst.manifest.json' -path '*/experiment=*/daily/v1/date=*/*' | LC_ALL=C sort |
  while IFS= read -r m; do
    z=${m%.manifest.json}
    [ -f "$z" ] || continue
    case "$z" in *"'"*) echo "compact-rows.sh: warning: $z has a quote in its path; using the raw rows" >&2; continue ;; esac
    want=$(tr -d '\n ' <"$m" | sed -n 's/.*"row_count":\([0-9][0-9]*\).*/\1/p')
    if [ -z "$want" ] || [ "$want" = 0 ]; then
      echo "compact-rows.sh: warning: $z: manifest has no usable row_count; using the raw rows" >&2
      continue
    fi
    command -v duckdb >/dev/null 2>&1 || { echo "compact-rows.sh: duckdb not found; run it through 'mise exec --'" >&2; exit 1; }
    got=$(zst_lines "$z")
    if [ "$got" != "$want" ]; then
      echo "compact-rows.sh: warning: $z: manifest says $want rows, the file has $got; using the raw rows" >&2
      [ -s "$tmp/duckdb.err" ] && { echo "compact-rows.sh: DuckDB said:" >&2; cat "$tmp/duckdb.err" >&2; }
      continue
    fi
    printf '%s\t%s\n' "$(wc -c <"$z" | tr -d ' ')" "$z"
  done |
  awk -F'\t' -v root="$mirror/" '{
    p = $2; r = substr(p, length(root) + 1)
    if (!match(r, /experiment=[^\/]+\/daily\/v1\/date=[0-9-]+\/[^\/]+\.jsonl\.zst$/)) next
    t = substr(r, RSTART); split(t, a, "/")
    day = substr(a[4], 6); f = a[5]; sub(/\.jsonl\.zst$/, "", f)
    if (substr(f, length(f) - length(day)) != "_" day) next
    pt = substr(f, 1, length(f) - length(day) - 1)
    prov = r; sub(/\/.*/, "", prov)
    if (index(r, "/") == 0 || index(prov, "experiment=") == 1) {
      print "compact-rows.sh: warning: " p " is ignored: the mirror root must hold the cloud folders (for example data/live), not one cloud folder; using the raw rows" > "/dev/stderr"
      next
    }
    print day "\t" prov "\t" pt "\t" substr(a[1], 12) "\t" $1 "\t" p
  }' | LC_ALL=C sort >"$tmp/daily.tsv"

# Raw objects of a trusted daily file's (day, provider, probe type) are skipped: the same experiment's,
# and the legacy run_mode= ones. Other experiments' raw objects stay.
awk -F'\t' -v root="$mirror/" 'FILENAME == ARGV[1] { e[$1 "\t" $2 "\t" $3 "\t" $4] = 1; k[$1 "\t" $2 "\t" $3] = 1; next }
  { x = ""; r = substr($6, length(root) + 1); if (match(r, /(^|\/)experiment=[^\/]+\//)) { x = substr(r, RSTART, RLENGTH - 1); sub(/^\/?experiment=/, "", x) }
    key = $1 "\t" $2 "\t" $3
    if (x == "" ? (key in k) : ((key "\t" x) in e)) next
    print }' \
  "$tmp/daily.tsv" "$tmp/winners.raw.tsv" >"$tmp/winners.tsv"

built=0
kept=0
for day in $(cat "$tmp/winners.tsv" "$tmp/daily.tsv" | cut -f1 | LC_ALL=C sort -u); do
  sig=$( {
    awk -F'\t' -v d="$day" '$1 == d { print $2 "\t" $3 "\t" $4 "\t" $5 }' "$tmp/winners.tsv"
    awk -F'\t' -v d="$day" '$1 == d { print "daily\t" $2 "\t" $3 "\t" $4 "\t" $5 }' "$tmp/daily.tsv"
  } | cksum | cut -d' ' -f1,2)
  if [ "$day" != "$today" ] && [ -f "$rows/date=$day/.sig" ] && [ "$(cat "$rows/date=$day/.sig")" = "$sig" ]; then
    kept=$((kept + 1))
    continue
  fi
  new="$rows/.date=$day.new.$$"
  rm -rf "$new"
  mkdir -p "$new"
  awk -F'\t' -v d="$day" '$1 == d { print $2 "_" $3 "\t" $6 }' "$tmp/winners.tsv" | cut -f1 | uniq |
    while read -r name; do
      awk -F'\t' -v d="$day" -v n="$name" '$1 == d && ($2 "_" $3) == n { print $6 }' "$tmp/winners.tsv" | tr '\n' '\0' |
        xargs -0 awk 1 | gzip -n >"$new/$name.jsonl.gz"
    done
  awk -F'\t' -v d="$day" '$1 == d { print $2 "_" $3 "." $4 "\t" $6 }' "$tmp/daily.tsv" |
    while IFS="$(printf '\t')" read -r name z; do cp "$z" "$new/$name.jsonl.zst"; done
  printf '%s\n' "$sig" >"$new/.sig"
  # Swap the whole day: aside, in, remove. A kill between the 2 renames leaves no date= folder,
  # so the next run rebuilds the day.
  if [ -d "$rows/date=$day" ]; then mv "$rows/date=$day" "$rows/.date=$day.old.$$"; fi
  mv "$new" "$rows/date=$day"
  rm -rf "$rows/.date=$day.old.$$"
  built=$((built + 1))
done
echo "compact-rows.sh: days rebuilt $built, days unchanged $kept, folder $rows"
