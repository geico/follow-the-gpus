#!/usr/bin/env bash
# Run scripts/test-hooks.sh and prove that it removes its /tmp/ftg-test.* folder.
# Usage: scripts/check-test-hooks-cleanup.sh
# Cases: a full run, a run that fails, a run that gets INT, and a run that gets TERM.
# The check looks only at the folders that its own runs print. It does not touch other folders.
# Set FTG_KEEP_TEST_DIR=1 to keep the folder of the full run. The check then skips that case.
set -u

here=$(cd "$(dirname "$0")" && pwd)
test_hooks="$here/test-hooks.sh"

pass=0
failn=0
ok() { echo "PASS  $1"; pass=$((pass + 1)); }
bad() { echo "FAIL  $1"; failn=$((failn + 1)); }

L=""
bg=""
cleanup() {
  if [ -n "$bg" ]; then kill -TERM -- "-$bg" 2>/dev/null; fi
  case "$L" in
    /tmp/ftg-check.?*) rm -rf -- "$L" ;;
  esac
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
L=$(mktemp -d /tmp/ftg-check.XXXXXX) || exit 1

# test_dir LOG: print the folder that a run of test-hooks.sh wrote to LOG.
test_dir() { sed -n 's/^test dir: //p' "$1" | head -n 1; }

# expect_removed NAME DIR: pass if DIR is a /tmp/ftg-test.* folder that does not exist.
expect_removed() {
  case "$2" in
    /tmp/ftg-test.?*) ;;
    *)
      bad "$1: no test dir in the output"
      return
      ;;
  esac
  if [ -e "$2" ]; then bad "$1: $2 stays"; else ok "$1: $2 removed"; fi
}

# 1. Full run. Its output goes to the terminal and to a log.
echo "== full run of test-hooks.sh"
"$test_hooks" 2>&1 | tee "$L/full.log"
full_rc=${PIPESTATUS[0]}
if [ "$full_rc" -eq 0 ]; then ok "full run passes"; else bad "full run passes (rc=$full_rc)"; fi
if [ -n "${FTG_KEEP_TEST_DIR:-}" ]; then
  echo "SKIP  full run: FTG_KEEP_TEST_DIR is set"
else
  expect_removed "full run" "$(test_dir "$L/full.log")"
fi

# 2. A run that fails: the repo path does not exist, so the copy fails.
echo "== failed run"
env -u FTG_KEEP_TEST_DIR "$test_hooks" "$L/no-such-repo" >"$L/fail.log" 2>&1
rc=$?
if [ "$rc" -ne 0 ]; then ok "failed run exits non-zero (rc=$rc)"; else bad "failed run exits non-zero (rc=0)"; fi
expect_removed "failed run" "$(test_dir "$L/fail.log")"

# 3 and 4. Interrupted runs: wait until the copy has a git repo, then send the signal to the process group.
# interrupt SIGNAL RC: run test-hooks.sh, send SIGNAL, and expect exit code RC.
interrupt() {
  local sig="$1" want="$2" log="$L/$1.log" dir="" i
  echo "== run that gets $sig"
  # Job control gives the background run its own process group, so INT is not ignored and the signal reaches the whole run.
  # It is on only for this launch, so Ctrl-C at the terminal still stops the whole check.
  set -m
  env -u FTG_KEEP_TEST_DIR "$test_hooks" >"$log" 2>&1 &
  bg=$!
  set +m
  for i in $(seq 1 600); do
    dir=$(test_dir "$log")
    if [ -n "$dir" ] && [ -d "$dir/work/.git" ]; then break; fi
    if ! kill -0 "$bg" 2>/dev/null; then break; fi
    sleep 0.1
  done
  kill "-$sig" -- "-$bg" 2>/dev/null
  wait "$bg"
  local rc=$?
  bg=""
  if [ "$rc" -eq "$want" ]; then ok "$sig run exits $want"; else bad "$sig run exits $want (rc=$rc)"; fi
  expect_removed "$sig run" "$(test_dir "$log")"
}
interrupt INT 130
interrupt TERM 143

echo "cleanup check passed: $pass  failed: $failn"
[ "$failn" -eq 0 ]
