#!/usr/bin/env bash
# Mirror the rows of 1 cloud's data store to a local folder, and fetch only what is new.
# Usage: scripts/data-sync.sh aws|azure
# Environment:
#   AWS_DATA_BUCKET     the S3 bucket (aws)
#   AZURE_DATA_ACCOUNT  the storage account (azure); the container is `data`
#   DATA_DIR            the mirror folder, default data/live; the cloud goes to $DATA_DIR/<cloud>/
# Spec: spec/0006-dashboard-spec.md, sections 4.3 and 4.4.
#
# A row key is <top>/provider=<p>/probe_type=<t>/date=<D>/<file>, where <top> is `experiment=<name>`
# or the old `run_mode=<m>`. The logs (`experiment=<name>/logs/...`) are not rows: no data tool
# reads them, so this script never lists or copies them. For each <top>/provider/probe_type it finds
# the newest date=<D> in the local copy and copies the day <D> and later. It never lists or compares
# an older day. A day on the store is complete once the next day starts, and an object is never
# rewritten. A first run on an empty copy fetches every day. Nothing is ever deleted locally.
# The requests run in parallel: 1 background job for each experiment (or run_mode), provider, and probe type. A higher request
# count for each transfer (aws s3.max_concurrent_requests, AZCOPY_CONCURRENCY_VALUE) was measured
# slower on top of that (spec 0006, section 4.3), so the tool defaults stay.
#
# Daily files. The prober VM packs each closed day of 1 probe
# type into 1 data file and a sidecar manifest: `experiment=<name>/daily/v1/date=<D>/<type>_<D>.jsonl.zst`
# and `<type>_<D>.jsonl.zst.manifest.json` (written last; it marks that type's day complete). For
# each `experiment=` top this script (1) lists only the `date=` folders under daily/v1/ and copies
# the day folders (about 14 small files each) from the newest complete local day on, that day again
# included; (2) checks each local sidecar against its data file; (3) copies the raw row objects
# only of the (day, probe type) pairs with no complete sidecar (the rule above, newest local raw
# day on, still limits which raw days it lists), and of every bad pair whatever its age. A pair
# whose data file does not match its sidecar gets a warning, the local sidecar is renamed (suffix
# below), and its raw objects are copied. This script never deletes a local or a remote file.
set -u
cloud="${1:-}"
data_dir="${DATA_DIR:-data/live}"

# The ONE place that names the daily folders and files. Change these to rename them.
#   <top>/<daily_root>/date=<D>/<type>_<D><daily_data_suffix>
#   <top>/<daily_root>/date=<D>/<type>_<D><daily_data_suffix><daily_manifest_suffix>
daily_root="daily/v1"
daily_data_suffix=".jsonl.zst"
daily_manifest_suffix=".manifest.json"
daily_bad_suffix=".bad"   # appended to the sidecar name when the pair fails the check

# daily_prefix TOP: the daily prefix of a top (`experiment=<name>/`).
daily_prefix() { printf '%s%s/\n' "$1" "$daily_root"; }

# newest_complete_day DIR: the newest date=<D> folder in DIR (a local daily folder) that holds a
# sidecar, or nothing.
newest_complete_day() {
  local d
  for d in $(ls "$1" 2>/dev/null | grep '^date=' | sort -r); do
    if ls "$1/$d" 2>/dev/null | grep -F "$daily_manifest_suffix" | grep -v -F "$daily_bad_suffix" | grep -q .; then
      echo "$d"
      return 0
    fi
  done
}

# check_daily DIR: print `date=<D> <type> ok|bad` for each sidecar in the date folders of DIR (a
# local daily folder). ok = the data file is there and its size and SHA-256 are the sidecar's
# file_bytes and file_sha256. bad = it is not: the sidecar is renamed to <sidecar><bad suffix> (an
# older file of that name is replaced) and a warning goes to stderr. A data file with no sidecar
# is not listed: it is not complete. A renamed sidecar with no good one beside it is `bad` on every run.
check_daily() {
  python3 -I - "$1" "$daily_data_suffix" "$daily_manifest_suffix" "$daily_bad_suffix" <<'PY'
import hashlib, json, os, sys
d, data_sfx, man_sfx, bad = sys.argv[1:5]
for day in sorted(os.listdir(d)) if os.path.isdir(d) else []:
    if not day.startswith("date="):
        continue
    names = sorted(os.listdir(os.path.join(d, day)))
    for name in names:
        if name.endswith(data_sfx + man_sfx + bad) and name[: -len(bad)] not in names:
            # A sidecar renamed by an earlier run and no good one now: still bad, so the raw rows
            # are copied again on every run until a good sidecar is there.
            stem = name[: -len(data_sfx + man_sfx + bad)]
            print(day, stem[: -len(day[5:]) - 1] if stem.endswith("_" + day[5:]) else stem, "bad")
            continue
        if not name.endswith(data_sfx + man_sfx):
            continue
        man = os.path.join(d, day, name)
        data = man[: -len(man_sfx)]
        stem = os.path.basename(data)[: -len(data_sfx)]
        ptype = stem[: -len(day[5:]) - 1] if stem.endswith("_" + day[5:]) else stem
        try:
            m = json.load(open(man, encoding="utf-8"))
            h, n = hashlib.sha256(), 0
            with open(data, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
                    n += len(chunk)
            ok = n == m["file_bytes"] and h.hexdigest() == m["file_sha256"]
            why = "size or sha256 differs from the manifest"
        except Exception as e:  # unreadable sidecar, missing data file, missing key
            ok, why = False, f"{type(e).__name__}: {e}"
        if ok:
            print(day, ptype, "ok")
        else:
            os.replace(man, man + bad)
            print(f"data-sync.sh: WARNING {data}: {why}; manifest renamed to {os.path.basename(man)}{bad}, raw rows will be copied", file=sys.stderr)
            print(day, ptype, "bad")
PY
}

# copy_prefixes TOP OVERWRITE PREFIX...: copy the files of the given prefixes (each ends in `/`, each
# starts with TOP) that the mirror lacks. OVERWRITE is the azcopy mode: ifSourceNewer for a daily
# folder (a rebuilt file replaces the local one), false for raw rows (an object never changes), true
# for a bad daily day (the store copy replaces a damaged local file whatever its time). On AWS that is
# `--exact-timestamps`: a same-size file is ignored only when the times are equal. Without it, `aws s3
# sync` ignores a same-size file unless the local one is newer, so a damaged older file would stay.
# AWS: 1 `aws s3 sync` in the background for each prefix. Azure: 1 `azcopy copy` of the top with
# --include-path for all the prefixes. Without prefixes it makes no call.
copy_prefixes() {
  local top="$1" over="$2" pids=() p x inc="" rc=0 flags=()
  shift 2
  [ "$over" = true ] && flags=(--exact-timestamps)
  [ "$#" -gt 0 ] || return 0
  case "$cloud" in
    aws)
      for x in "$@"; do
        aws s3 sync --only-show-errors "${flags[@]+"${flags[@]}"}" "s3://$AWS_DATA_BUCKET/$x" "$data_dir/$cloud/$x" &
        pids+=("$!")
      done
      for p in "${pids[@]}"; do wait "$p" || rc=1; done
      ;;
    azure)
      for x in "$@"; do inc="$inc;${x#"$top"}"; done
      inc=${inc#;}; inc=${inc//\/;/;}; inc=${inc%/}
      azcopy copy "https://$AZURE_DATA_ACCOUNT.blob.core.windows.net/data/${top%/}" "$data_dir/$cloud/" \
        --recursive --overwrite="$over" --include-path "$inc" --log-level ERROR || rc=1
      ;;
  esac
  return "$rc"
}

# bad_days DIR: the date=<D> folders of DIR (a local daily folder) that hold a renamed sidecar with no
# sidecar of that name beside it (a pair that is still bad).
bad_days() {
  local d b
  for d in $(ls "$1" 2>/dev/null | grep '^date='); do
    for b in "$1/$d"/*"$daily_manifest_suffix$daily_bad_suffix"; do
      [ -e "$b" ] || continue
      [ -e "${b%"$daily_bad_suffix"}" ] || { echo "$d"; break; }
    done
  done
}

# sync_daily TOP: copy the day folders of the daily prefix of 1 top. One delimiter listing; days
# older than the newest complete local day are not copied, except a day that holds a renamed (bad)
# sidecar: it is fetched again by its prefix, also on AWS where the listing starts at the newest
# complete day, and its files replace the local ones (a damaged local file is not kept).
sync_daily() {
  local dp ldir newest days day name start want=() force=() d rc=0
  dp=$(daily_prefix "$1")
  ldir="$data_dir/$cloud/$dp"
  newest=$(newest_complete_day "$ldir")
  if [ -n "$newest" ] && [ "$cloud" = aws ]; then start="${dp}${newest}"; else start=""; fi
  days=$(list_prefixes "$dp" $start) || return 1
  for d in $(bad_days "$ldir"); do force+=("$dp$d/"); done
  for day in $days; do
    name=${day#"$dp"}
    case " ${force[*]-} " in *" $day "*) continue ;; esac
    if [ -n "$newest" ] && [ "${name%/}" \< "$newest" ]; then continue; fi
    want+=("$day")
  done
  copy_prefixes "$1" ifSourceNewer "${want[@]+"${want[@]}"}" || rc=1
  copy_prefixes "$1" true "${force[@]+"${force[@]}"}" || rc=1
  return "$rc"
}

# newest_day DIR: the newest date=<D> folder name in DIR, or nothing. The names sort as dates.
newest_day() {
  ls "$1" 2>/dev/null | grep '^date=' | sort | tail -n 1
}

# type_days PREFIX: list the raw days of 1 probe type (PREFIX ends in `/`): line 1 is PREFIX, then
# the day prefixes from the newest local raw day on (all days with no local copy).
type_days() {
  local prefix="$1" newest
  newest=$(newest_day "$data_dir/$cloud/$prefix")
  echo "$prefix"
  if [ -n "$newest" ]; then list_prefixes "$prefix" "${prefix}${newest}"; else list_prefixes "$prefix"; fi
}

# raw_wanted FILE: from the output of type_days in FILE, print the raw day prefixes to copy: the newest
# local raw day and later days, except a pair with a complete sidecar, and every bad pair whatever
# its age. Needs `states` (check_daily).
raw_wanted() {
  local prefix type bad newest day name copied=" "
  prefix=$(sed -n 1p "$1")
  type=${prefix%/}; type=${type##*probe_type=}
  bad=$(printf '%s\n' "$states" | awk -v t="$type" '$2 == t && $3 == "bad" { print $1 }')
  newest=$(newest_day "$data_dir/$cloud/$prefix")
  for day in $(sed 1d "$1"); do
    name=${day#"$prefix"}
    # Azure lists every day: a day older than the newest local day is not copied.
    if [ -n "$newest" ] && [ "$name" \< "$newest/" ]; then continue; fi
    # A complete sidecar: the raw objects of this day and type are not copied.
    if printf '%s\n' "$states" | grep -qx "${name%/} $type ok"; then continue; fi
    echo "$day"
    copied="$copied$name "
  done
  for name in $bad; do
    case "$copied" in *" $name/ "*) continue ;; esac
    echo "$prefix$name/"
  done
}

# copy_type TOP FILE: copy the raw days that raw_wanted prints for FILE, one at a time, oldest first;
# stop at the first failure.
copy_type() {
  local p
  for p in $(raw_wanted "$2"); do
    copy_prefixes "$1" false "$p" || return 1
  done
}

# sync_top TOP: sync 1 top. The daily copy and the listing of the raw days run at the same time;
# then the sidecars are checked (`states`), then the raw days that need rows are copied for each probe type, one day at a time.
sync_top() {
  local top="$1" dpid="" provs prov types t i=0 p f rc=0 pids=() files=() ok=()
  states=""
  case "$top" in
    experiment=*) sync_daily "$top" & dpid=$! ;;
  esac
  if provs=$(list_prefixes "$top"); then
    for prov in $(printf '%s\n' "$provs" | grep '/provider='); do
      if types=$(list_prefixes "$prov"); then
        for t in $types; do
          i=$((i + 1)); f="$tmp/$i"
          type_days "$t" >"$f" &
          pids+=("$!"); files+=("$f")
        done
      else
        rc=1
      fi
    done
  else
    rc=1
  fi
  for i in "${!pids[@]}"; do
    if wait "${pids[$i]}"; then ok+=("${files[$i]}"); else rc=1; fi
  done
  if [ -n "$dpid" ]; then
    if wait "$dpid"; then
      states=$(check_daily "$data_dir/$cloud/$(daily_prefix "$top")") || { states=""; rc=1; }
    else
      echo "data-sync.sh: the daily copy of $top failed" >&2; rc=1
    fi
  fi
  # The probe types run at the same time; the days of 1 type are copied one after the other, oldest first,
  # and the type stops at its first failure. Else a later day could succeed after an older one failed, the
  # next run would list from that later day, and the failed day would never be fetched again.
  pids=()
  for f in "${ok[@]+"${ok[@]}"}"; do
    copy_type "$top" "$f" &
    pids+=("$!")
  done
  for p in "${pids[@]+"${pids[@]}"}"; do wait "$p" || rc=1; done
  return "$rc"
}

# list_prefixes PREFIX [START_AFTER]: the child prefixes of PREFIX, 1 per line, each ending in `/`.
# With START_AFTER (aws only), the listing starts after that key. No object is listed, only prefixes.
list_prefixes() {
  case "$cloud" in
    aws)
      local args=(--bucket "$AWS_DATA_BUCKET" --delimiter / --prefix "$1")
      [ -n "${2:-}" ] && args+=(--start-after "$2")
      aws s3api list-objects-v2 "${args[@]}" --query 'CommonPrefixes[].Prefix' --output text |
        tr '\t' '\n' | grep -v '^None$' | grep -v '^$'
      # grep -v exits 1 on no line; the pipe status is the one of the last grep, so check the list call.
      [ "${PIPESTATUS[0]}" -eq 0 ] || return 1
      return 0
      ;;
    azure)
      az storage blob list --account-name "$AZURE_DATA_ACCOUNT" --container-name data \
        --auth-mode login --prefix "$1" --delimiter / --query '[].name' --output tsv |
        grep -v '^$'
      [ "${PIPESTATUS[0]}" -eq 0 ] || return 1
      return 0
      ;;
  esac
}

case "$cloud" in
  aws)
    if [ -z "${AWS_DATA_BUCKET:-}" ]; then
      echo "data-sync.sh: AWS_DATA_BUCKET is empty; set it to the data bucket name" >&2
      exit 1
    fi
    ;;
  azure)
    if [ -z "${AZURE_DATA_ACCOUNT:-}" ]; then
      echo "data-sync.sh: AZURE_DATA_ACCOUNT is empty; set it to the storage account name" >&2
      exit 1
    fi
    export AZCOPY_AUTO_LOGIN_TYPE="${AZCOPY_AUTO_LOGIN_TYPE:-AZCLI}"
    ;;
  *)
    echo "usage: data-sync.sh aws|azure" >&2
    exit 2
    ;;
esac

# Walk top -> provider -> probe type on the store (3 listings of prefixes in a row; the daily listing
# and the day listing of each probe type run at the same time).
tmp=$(mktemp -d "${TMPDIR:-/tmp}/ftg-data-sync.XXXXXX") || exit 1
trap 'rm -rf "$tmp"' EXIT
tops=$(list_prefixes "") || { echo "data-sync.sh: cannot list the store" >&2; exit 1; }
rc=0
for top in $(printf '%s\n' "$tops" | grep -E '^(experiment|run_mode)='); do
  sync_top "$top" || rc=1
done
[ "$rc" -eq 0 ] || echo "data-sync.sh: a listing or a copy failed (see above)" >&2
exit "$rc"
