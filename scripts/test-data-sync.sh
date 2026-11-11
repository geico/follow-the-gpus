#!/usr/bin/env bash
# Test scripts/data-sync.sh with a fake aws, az, and azcopy over a directory that stands for the
# object stores. No network, no cloud credentials. For each cloud it checks that:
#   - a first run on an empty copy copies every row day, and no log object;
#   - a re-run with no new object copies nothing, and lists or copies no day before the newest local day;
#   - a new object on the newest day, and a new day, are copied;
# and, for the daily files, that:
#   - a day with a good daily folder (the data file's size and sha256 match the manifest) copies no
#     raw object, and the daily files of every day are copied;
#   - the open day (no daily folder) copies its raw objects;
#   - a data file that does not match its manifest warns, renames the local manifest to
#     <name>.jsonl.zst.manifest.json.bad, and copies the raw objects of that day, also when the day is older than the
#     newest local raw day;
#   - no local or remote file is ever deleted.
# The fakes print what the real tools print (stdout, exit code, files written)
# for the side-by-side with the real calls. Set DATA_SYNC_SCRIPT to test another script.
set -u
here=$(cd "$(dirname "$0")" && pwd)
script="${DATA_SYNC_SCRIPT:-$here/data-sync.sh}"
work=$(mktemp -d "${TMPDIR:-/tmp}/ftg-data-sync-test.XXXXXX")
trap 'rm -rf "$work"' EXIT
fail=0
bad() { echo "FAIL $1" >&2; fail=1; }

mkdir -p "$work/bin" "$work/store/aws-bucket" "$work/store/azure-data"
export FAKE_STORE="$work/store" FAKE_CALLS="$work/calls.log"

# key_list DIR: the keys under DIR, relative, in byte order.
cat >"$work/bin/lib.sh" <<'EOF'
key_list() { (cd "$1" && find . -type f | sed 's|^\./||' | LC_ALL=C sort); }
# prefixes STORE PREFIX [START_AFTER]: the child prefixes of PREFIX (ends in /), 1 per line.
prefixes() {
  key_list "$1" | LC_ALL=C awk -v p="$2" -v s="${3:-}" '
    index($0, p) == 1 && ($0 > s || s == "") {
      r = substr($0, length(p) + 1); i = index(r, "/")
      if (i > 0) print p substr(r, 1, i)
    }' | LC_ALL=C sort -u
}
EOF

# fake aws: s3api list-objects-v2 with --query CommonPrefixes --output text, s3 sync.
cat >"$work/bin/aws" <<'EOF'
#!/usr/bin/env bash
. "$(dirname "$0")/lib.sh"
echo "aws $*" >>"$FAKE_CALLS"
if [ "$1 $2" = "s3api list-objects-v2" ]; then
  shift 2
  while [ $# -gt 0 ]; do
    case "$1" in
      --bucket) bucket=$2; shift 2 ;;
      --prefix) prefix=$2; shift 2 ;;
      --start-after) start=$2; shift 2 ;;
      --delimiter|--query|--output) shift 2 ;;
      *) echo "fake aws: unknown flag $1" >&2; exit 252 ;;
    esac
  done
  out=$(prefixes "$FAKE_STORE/$bucket" "${prefix:-}" "${start:-}")
  # --output text: the prefixes on 1 line, separated by tabs; "None" when the query finds nothing.
  if [ -z "$out" ]; then echo None; else printf '%s\n' "$out" | paste -sd '\t' -; fi
  exit 0
fi
if [ "$1 $2" = "s3 sync" ]; then
  shift 2
  args=()
  inc=()
  exact=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --only-show-errors) shift ;;
      --exact-timestamps) exact=1; shift ;;
      --exclude) shift 2 ;;
      --include) inc+=("$2"); shift 2 ;;
      *) args+=("$1"); shift ;;
    esac
  done
  src=${args[0]#s3://}; dst=${args[1]}
  bucket=${src%%/*}; prefix=${src#*/}; [ "$prefix" = "$src" ] && prefix=""
  while IFS= read -r k; do
    case "$k" in "$prefix"*) ;; *) continue ;; esac
    if [ ${#inc[@]} -gt 0 ]; then
      ok=1; for i in "${inc[@]}"; do case "$k" in $i) ok=0 ;; esac; done; [ $ok -eq 0 ] || continue
    fi
    rel=${k#"$prefix"}
    # real aws s3 sync (awscli sync strategy, and its help text): copy when the destination is missing or
    # its size differs. For a same-size file: by default it is ignored unless the local one is NEWER than
    # the store object; with --exact-timestamps it is ignored only when the times are equal. The real
    # tool sets the local time to the store time after a download, so the fake copies with cp -p.
    src="$FAKE_STORE/$bucket/$k"
    if [ -e "$dst/$rel" ] && [ "$(wc -c <"$dst/$rel")" = "$(wc -c <"$src")" ]; then
      if [ "$exact" = 1 ]; then
        { [ "$dst/$rel" -nt "$src" ] || [ "$dst/$rel" -ot "$src" ]; } || continue
      else
        [ "$dst/$rel" -nt "$src" ] || continue
      fi
    fi
    mkdir -p "$(dirname "$dst/$rel")"; cp -p "$src" "$dst/$rel"
  done < <(key_list "$FAKE_STORE/$bucket")
  exit 0
fi
echo "fake aws: unknown command $*" >&2; exit 252
EOF

# fake az: storage blob list --prefix P --delimiter / --query '[].name' --output tsv.
cat >"$work/bin/az" <<'EOF'
#!/usr/bin/env bash
. "$(dirname "$0")/lib.sh"
echo "az $*" >>"$FAKE_CALLS"
[ "$1 $2 $3" = "storage blob list" ] || { echo "fake az: unknown command $*" >&2; exit 2; }
shift 3
prefix=""
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) prefix=$2; shift 2 ;;
    --account-name|--container-name|--auth-mode|--delimiter|--query|--output) shift 2 ;;
    *) echo "fake az: unknown flag $1" >&2; exit 2 ;;
  esac
done
prefixes "$FAKE_STORE/azure-data" "$prefix"
EOF

# fake azcopy: copy URL DST --recursive --overwrite=false [--include-path "a;b"] ...; the last path part of URL goes under DST.
cat >"$work/bin/azcopy" <<'EOF'
#!/usr/bin/env bash
. "$(dirname "$0")/lib.sh"
echo "azcopy $*" >>"$FAKE_CALLS"
[ "$1" = copy ] || { echo "fake azcopy: unknown command $*" >&2; exit 1; }
src=$2; dst=$3; shift 3
over=true
incl=""
while [ $# -gt 0 ]; do
  a=$1; shift
  case "$a" in
    --recursive|--log-level|ERROR) ;;
    --include-path) incl=$1; shift ;;
    --overwrite=false) over=false ;;
    --overwrite=ifSourceNewer) over=newer ;;
    --overwrite=true) over=true ;;
    *) echo "fake azcopy: unknown flag $a" >&2; exit 1 ;;
  esac
done
key=${src#https://*.blob.core.windows.net/data/}
base=$(basename "$key")
while IFS= read -r k; do
  case "$k" in "$key"/*) ;; *) continue ;; esac
  # real azcopy: --include-path is a ';'-separated list of paths relative to the source; it takes only those
  if [ -n "$incl" ]; then
    hit=1; IFS=';' read -ra ips <<<"$incl"
    for ip in "${ips[@]}"; do case "$k" in "$key/$ip"/*) hit=0 ;; esac; done
    [ $hit -eq 0 ] || continue
  fi
  rel=$base/${k#"$key"/}
  [ -e "$dst/$rel" ] && [ "$over" = false ] && continue
  [ -e "$dst/$rel" ] && [ "$over" = newer ] && { cmp -s "$dst/$rel" "$FAKE_STORE/azure-data/$k" || [ "$dst/$rel" -nt "$FAKE_STORE/azure-data/$k" ]; } && continue
  mkdir -p "$(dirname "$dst/$rel")"; cp "$FAKE_STORE/azure-data/$k" "$dst/$rel"
done < <(key_list "$FAKE_STORE/azure-data")
exit 0
EOF
chmod +x "$work/bin/aws" "$work/bin/az" "$work/bin/azcopy"

# put STORE KEY: write 1 object. A row key is <top>/provider=<p>/probe_type=<t>/date=<D>/<file>.
put() { mkdir -p "$(dirname "$FAKE_STORE/$1/$2")"; echo "$2" >"$FAKE_STORE/$1/$2"; }
count() { find "$1" -type f 2>/dev/null | wc -l | tr -d ' '; }


run() { # run CLOUD: the script under test, with the fakes first on PATH
  : >"$FAKE_CALLS"
  AWS_DATA_BUCKET=aws-bucket AZURE_DATA_ACCOUNT=acct \
    DATA_DIR="$work/mirror" PATH="$work/bin:$PATH" bash "$script" "$1" >"$work/out.log" 2>&1
  rc=$?
  [ "$rc" -eq 0 ] || { bad "$1: exit $rc"; cat "$work/out.log" >&2; }
}

for cloud in aws azure; do
  if [ "$cloud" = aws ]; then store=aws-bucket; else store=azure-data; fi
  p="experiment=e/provider=$cloud"
  for t in ta tb; do
    for d in 2026-10-01 2026-10-02 2026-10-03; do
      put $store "$p/probe_type=$t/date=$d/${d}_1.jsonl"
      put $store "$p/probe_type=$t/date=$d/${d}_2.jsonl"
    done
  done
  put $store "$p/probe_type=ta/date=2026-10-03/20261003T060000Z_raw_x.json"
  put $store "run_mode=old/provider=$cloud/probe_type=ta/date=2026-09-30/old_1.jsonl"
  put $store "experiment=e/logs/provider=$cloud/date=2026-10-03/log_1.jsonl"
  mirror="$work/mirror/$cloud"

  # 1. First run on an empty copy: every row day, no log.
  run $cloud
  [ "$(count "$mirror")" = 14 ] || bad "$cloud first run: expected 14 files, got $(count "$mirror")"
  [ ! -e "$mirror/experiment=e/logs" ] || bad "$cloud first run: a log object was copied"
  [ -f "$mirror/run_mode=old/provider=$cloud/probe_type=ta/date=2026-09-30/old_1.jsonl" ] || bad "$cloud first run: the run_mode row is missing"

  # 2. Re-run, no new object: nothing copied, no older day listed or copied.
  before=$(count "$mirror")
  run $cloud
  [ "$(count "$mirror")" = "$before" ] || bad "$cloud re-run: files changed"
  if grep -E 'date=2026-10-0[12]' "$FAKE_CALLS" >/dev/null; then
    bad "$cloud re-run: a day before the newest local day was listed or copied"; grep -E 'date=2026-10-0[12]' "$FAKE_CALLS" >&2
  fi
  grep -q 'date=2026-10-03' "$FAKE_CALLS" || bad "$cloud re-run: the newest day was not checked"
  grep -q '/logs' "$FAKE_CALLS" && bad "$cloud re-run: the logs prefix was listed or copied"

  # 3. A new object on the newest day, a new day, and a late object on an old day.
  put $store "$p/probe_type=ta/date=2026-10-03/20261003T070000Z_new.jsonl"
  put $store "$p/probe_type=tb/date=2026-10-04/20261004T000000Z_new.jsonl"
  put $store "$p/probe_type=ta/date=2026-10-01/late.jsonl"
  run $cloud
  [ -f "$mirror/$p/probe_type=ta/date=2026-10-03/20261003T070000Z_new.jsonl" ] || bad "$cloud: new object on the newest day not copied"
  [ -f "$mirror/$p/probe_type=tb/date=2026-10-04/20261004T000000Z_new.jsonl" ] || bad "$cloud: new day not copied"
  [ ! -e "$mirror/$p/probe_type=ta/date=2026-10-01/late.jsonl" ] || bad "$cloud: an object of an old day was fetched"

  # 4. A new probe type with no local copy is fetched in full.
  put $store "$p/probe_type=tc/date=2026-10-02/tc_1.jsonl"
  run $cloud
  [ -f "$mirror/$p/probe_type=tc/date=2026-10-02/tc_1.jsonl" ] || bad "$cloud: a new probe type was not fetched"
done

# Daily files. The layout is experiment=<name>/daily/v1/date=<D>/<type>_<D>.jsonl.zst plus
# <type>_<D>.jsonl.zst.manifest.json (names: the variables at the top of the script).
# put_daily STORE TYPE DAY good|bad: a data file and a sidecar; "bad" has a sidecar that does not match.
put_daily() {
  local dir="$FAKE_STORE/$1/experiment=e/daily/v1/date=$3" f bytes sha
  f="$dir/$2_$3.jsonl.zst"
  mkdir -p "$dir"
  printf 'zstd frame of %s %s\n' "$2" "$3" >"$f"
  bytes=$(wc -c <"$f" | tr -d ' ')
  sha=$(shasum -a 256 "$f" | cut -d' ' -f1)
  if [ "$4" = bad ]; then sha=$(printf 'x' | shasum -a 256 | cut -d' ' -f1); fi
  printf '{"file_bytes": %s, "file_sha256": "%s", "row_count": 2}\n' "$bytes" "$sha" >"$f.manifest.json"
}
snapshot() { (cd "$1" && find . -type f | LC_ALL=C sort) 2>/dev/null; }

for cloud in aws azure; do
  if [ "$cloud" = aws ]; then store=aws-bucket; else store=azure-data; fi
  rm -rf "${FAKE_STORE:?}/$store"; mkdir -p "$FAKE_STORE/$store"; rm -rf "$work/mirror"
  p="experiment=e/provider=$cloud"
  mirror="$work/mirror/$cloud"
  dm="$mirror/experiment=e/daily/v1"
  for d in 2026-10-01 2026-10-02 2026-10-03 2026-10-04; do
    for t in ta tb; do
      put $store "$p/probe_type=$t/date=$d/${d}_1.jsonl"
      put $store "$p/probe_type=$t/date=$d/${d}_2.jsonl"
    done
  done
  for t in ta tb; do put_daily $store $t 2026-10-01 good; put_daily $store $t 2026-10-02 good; done
  put_daily $store ta 2026-10-03 bad
  put_daily $store tb 2026-10-03 good
  store_before=$(snapshot "$FAKE_STORE/$store")

  # 5. Good pairs copy no raw object; the open day and the mismatched pair copy theirs.
  run $cloud
  [ -f "$dm/date=2026-10-01/ta_2026-10-01.jsonl.zst" ] && [ -f "$dm/date=2026-10-01/ta_2026-10-01.jsonl.zst.manifest.json" ] || bad "$cloud daily: the daily files of a good day are not copied"
  [ -f "$mirror/$p/probe_type=ta/date=2026-10-04/2026-10-04_1.jsonl" ] || bad "$cloud daily: the open day's raw objects are not copied"
  for t in ta tb; do
    ls "$mirror/$p/probe_type=$t/" | grep -q -E '2026-10-0[12]$' && bad "$cloud daily: a raw day with a good daily pair was copied ($t)"
  done
  [ -f "$mirror/$p/probe_type=ta/date=2026-10-03/2026-10-03_1.jsonl" ] || bad "$cloud daily: a mismatched pair did not fall back to raw"
  [ ! -e "$mirror/$p/probe_type=tb/date=2026-10-03" ] || bad "$cloud daily: the good pair of a day with a bad pair was copied as raw"
  [ -f "$dm/date=2026-10-03/ta_2026-10-03.jsonl.zst.manifest.json.bad" ] && [ ! -e "$dm/date=2026-10-03/ta_2026-10-03.jsonl.zst.manifest.json" ] || bad "$cloud daily: the mismatched sidecar was not renamed to .bad"
  grep -q 'WARNING' "$work/out.log" || bad "$cloud daily: no warning for the mismatched daily file"
  # The sync lists the date folders of daily/v1 once and no daily object listing by type.
  grep -q -E 'provider=.*daily|daily.*provider=' "$FAKE_CALLS" && bad "$cloud daily: an old daily layout key was used"
  after1=$(snapshot "$mirror")

  # 6. A re-run deletes nothing, in the mirror or in the store, and copies no day before the newest complete day.
  run $cloud
  after2=$(snapshot "$mirror")
  [ -z "$(comm -23 <(printf '%s\n' "$after1") <(printf '%s\n' "$after2"))" ] || bad "$cloud daily: a local file was deleted"
  [ "$(snapshot "$FAKE_STORE/$store")" = "$store_before" ] || bad "$cloud daily: a store file changed or was deleted"
  grep -E '(sync|copy) ' "$FAKE_CALLS" | grep 'daily/v1/date=2026-10-0[12]' >/dev/null && bad "$cloud daily re-run: an older complete day was copied again"

  # 6b. Azure copies with --include-path: 1 azcopy for the daily days, then 1 for each probe type and day.
  if [ "$cloud" = azure ]; then
    grep '^azcopy ' "$FAKE_CALLS" | grep -v -q -e '--include-path' && bad "azure daily re-run: an azcopy call without --include-path"
    grep -q '^azcopy .*--include-path' "$FAKE_CALLS" || bad "azure daily re-run: no azcopy call with --include-path"
  fi

  # 7. A good pair, older than the newest complete day, that turns bad in the store: raw is copied.
  printf 'rebuilt but wrong\n' >"$FAKE_STORE/$store/experiment=e/daily/v1/date=2026-10-01/tb_2026-10-01.jsonl.zst"
  # An older day is not re-copied, so make the local pair bad the way a damaged mirror would be.
  printf 'damaged\n' >"$dm/date=2026-10-02/tb_2026-10-02.jsonl.zst"
  run $cloud
  [ -f "$dm/date=2026-10-02/tb_2026-10-02.jsonl.zst.manifest.json.bad" ] || bad "$cloud daily: a damaged local pair was not renamed"
  [ -f "$mirror/$p/probe_type=tb/date=2026-10-02/2026-10-02_1.jsonl" ] || bad "$cloud daily: an older day that turned bad did not fall back to raw"
  [ ! -e "$mirror/$p/probe_type=ta/date=2026-10-02" ] || bad "$cloud daily: the good pair of the same day was copied as raw"

  # 8. The store repairs the mismatched pair: it is complete and the sync keeps the raw copy.
  put_daily $store ta 2026-10-03 good
  run $cloud
  [ -f "$dm/date=2026-10-03/ta_2026-10-03.jsonl.zst.manifest.json" ] || bad "$cloud daily: a repaired daily pair is not complete again"
  [ -f "$dm/date=2026-10-03/ta_2026-10-03.jsonl.zst.manifest.json.bad" ] || bad "$cloud daily: the .bad sidecar was deleted"
  [ -f "$mirror/$p/probe_type=ta/date=2026-10-03/2026-10-03_1.jsonl" ] || bad "$cloud daily: a raw file was deleted"

  # 9. An old pair that has only a .bad sidecar (step 7) and lost its raw copy (a failed or killed run):
  # the next run copies the raw rows again, until a good sidecar is there.
  put_daily $store tb 2026-10-02 bad
  printf 'damaged\n' >"$dm/date=2026-10-02/tb_2026-10-02.jsonl.zst"
  run $cloud
  rm -rf "$mirror/$p/probe_type=tb/date=2026-10-02"
  run $cloud
  [ "$(count "$mirror/$p/probe_type=tb/date=2026-10-02")" = 2 ] || bad "$cloud daily: the raw rows of an old .bad pair were not copied again"

  # 10. A damaged local data file of the same size, on a day older than the newest complete day.
  # Run 1 finds it bad (the sidecar is renamed). Run 2 fetches the day again by its prefix, and the store
  # copy replaces the damaged file. AWS lists from the newest complete day, so the old code never listed
  # the day. Two cases: a damaged file NEWER than the store object (real azcopy --overwrite=ifSourceNewer
  # keeps it), and one OLDER than the store object (real aws s3 sync ignores a same-size older file, and
  # replaces it only with --exact-timestamps).
  put_daily $store ta 2026-10-01 good
  f1="$dm/date=2026-10-01/ta_2026-10-01.jsonl.zst"
  for age in newer older; do
    sleep 1
    n1=$(wc -c <"$f1" | tr -d ' ')
    head -c "$n1" /dev/zero | tr '\0' x >"$f1"
    [ "$age" = older ] && touch -t 2000010100.00 "$f1"
    run $cloud
    [ -f "$f1.manifest.json.bad" ] || bad "$cloud daily ($age): a damaged data file did not turn its sidecar bad"
    run $cloud
    cmp -s "$f1" "$FAKE_STORE/$store/experiment=e/daily/v1/date=2026-10-01/ta_2026-10-01.jsonl.zst" || bad "$cloud daily ($age): a damaged data file of an old bad day was not replaced"
    [ -f "$f1.manifest.json" ] || bad "$cloud daily ($age): the sidecar of an old bad day was not fetched again"
  done
done

# A failed older day is fetched on the next run. The days of a probe type are copied oldest
# first and the type stops at its first failure, else a later day would succeed, the next run would list from
# that day, and the failed one would be lost. The fake aws fails the sync of date=2026-10-02 once.
rm -rf "${FAKE_STORE:?}/aws-bucket" "$work/mirror"; mkdir -p "$FAKE_STORE/aws-bucket"
mv "$work/bin/aws" "$work/bin/aws.real"
cat >"$work/bin/aws" <<'EOW'
#!/usr/bin/env bash
if [ "$1 $2" = "s3 sync" ] && [[ "$*" == *"date=2026-10-02/"* ]] && [ ! -e "$FAKE_STORE/failed-once" ]; then
  touch "$FAKE_STORE/failed-once"; echo "fatal error: An error occurred (SlowDown)" >&2; exit 1
fi
exec "$(dirname "$0")/aws.real" "$@"
EOW
chmod +x "$work/bin/aws"
for d in 2026-10-01 2026-10-02 2026-10-03; do put aws-bucket "experiment=e/provider=aws/probe_type=ta/date=$d/${d}_1.jsonl"; done
AWS_DATA_BUCKET=aws-bucket DATA_DIR="$work/mirror" PATH="$work/bin:$PATH" bash "$script" aws >"$work/out.log" 2>&1 &&
  bad "a failed day copy: run 1 exited 0"
AWS_DATA_BUCKET=aws-bucket DATA_DIR="$work/mirror" PATH="$work/bin:$PATH" bash "$script" aws >"$work/out.log" 2>&1 ||
  bad "a failed day copy: run 2 exited non-zero"
[ -f "$work/mirror/aws/experiment=e/provider=aws/probe_type=ta/date=2026-10-02/2026-10-02_1.jsonl" ] ||
  bad "a failed day copy: the failed day was not fetched on the next run"
[ -f "$work/mirror/aws/experiment=e/provider=aws/probe_type=ta/date=2026-10-03/2026-10-03_1.jsonl" ] ||
  bad "a failed day copy: the later day was not fetched"

# An empty bucket name stops the script.
if AWS_DATA_BUCKET= PATH="$work/bin:$PATH" bash "$script" aws >/dev/null 2>&1; then
  bad "an empty AWS_DATA_BUCKET did not stop the script"
fi

# The leaf targets data/sync/aws and data/sync/azure take the name as a make variable. (data/sync reads
# the names from the deploy outputs; scripts/test-dev.sh tests that.)
repo=$(cd "$here/.." && pwd)
rm -rf "${FAKE_STORE:?}/aws-bucket" "${FAKE_STORE:?}/azure-data"
mv "$work/bin/aws.real" "$work/bin/aws"
put aws-bucket "experiment=e/provider=aws/probe_type=ta/date=2026-10-01/2026-10-01_1.jsonl"
put azure-data "experiment=e/provider=azure/probe_type=ta/date=2026-10-01/2026-10-01_1.jsonl"
msync() { # msync NAME TARGET VARS...: make TARGET with the fakes first on PATH
  n=$1; t=$2; shift 2; rm -rf "$work/mirror"
  env "$@" DATA_DIR="$work/mirror" PATH="$work/bin:$PATH" make -s -C "$repo" MISE_EXEC= "$t" >"$work/make-$n.log" 2>&1
}
msync aws data/sync/aws AWS_DATA_BUCKET=aws-bucket || bad "make data/sync/aws failed"
[ "$(count "$work/mirror/aws")" -gt 0 ] || bad "make data/sync/aws did not sync aws"
msync az data/sync/azure AZURE_DATA_ACCOUNT=acct || bad "make data/sync/azure failed"
[ "$(count "$work/mirror/azure")" -gt 0 ] || bad "make data/sync/azure did not sync azure"

if [ "$fail" -eq 0 ]; then echo "test-data-sync: ok"; fi
exit "$fail"
