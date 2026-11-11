#!/usr/bin/env bash
# Tests for terraform/sync.sh, the VM-side pull and restart (ADR 0012, "Tests the implementation
# must have"). No network, no cloud credentials, no real systemd or cloud CLI: a small fakebin/
# directory stands in for aws, fetch-blob.sh, systemctl, journalctl, and the release venv
# (python3, pip, ftg-prober), so each tick is deterministic and fast. Run: make test/sync
set -u

here=$(cd "$(dirname "$0")" && pwd)
sync_sh="$here/../sync.sh"

pass=0
fail=0
ok() {
  pass=$((pass + 1))
  echo "ok   $1"
}
not_ok() {
  fail=$((fail + 1))
  echo "FAIL $1" >&2
}
check() {
  local name=$1
  shift
  if "$@"; then ok "$name"; else not_ok "$name"; fi
}

root=$(mktemp -d "${TMPDIR:-/tmp}/ftg-sync-test.XXXXXX")
[ -n "${FTG_KEEP_TEST_DIR:-}" ] || trap 'rm -rf "$root"' EXIT

fakebin="$root/fakebin"
store="$root/store"
block="$root/block"
state="$root/systemctl-state"
mkdir -p "$fakebin" "$store" "$block" "$state"

# --- fakebin -------------------------------------------------------------------------------

cat > "$fakebin/aws" <<'EOF'
#!/usr/bin/env bash
set -u
if [ "$1" = s3api ] && [ "$2" = get-object ]; then
  shift 2
  key= out=
  while [ $# -gt 0 ]; do
    case "$1" in
      --bucket) shift 2 ;;
      --key) key=$2; shift 2 ;;
      *) out=$1; shift ;;
    esac
  done
  if [ -f "$FAKE_BLOCK_DIR/$key" ]; then
    echo "fake aws: blocked $key" >&2
    exit 1
  fi
  if [ -f "$FAKE_STORE_DIR/$key" ]; then
    cp "$FAKE_STORE_DIR/$key" "$out"
    # The real CLI prints the object metadata as JSON on stdout; callers must not read it as
    # their result (the same contract as fetch-blob.sh below).
    echo '{"AcceptRanges": "bytes", "ContentLength": 1, "ETag": "\"fake\""}'
    exit 0
  fi
  echo "fake aws: NoSuchKey $key" >&2
  exit 255
fi
echo "fake aws: unsupported: $*" >&2
exit 1
EOF

cat > "$fakebin/fetch-blob.sh" <<'EOF'
#!/usr/bin/env bash
set -u
blob=$1
out=$2
if [ -f "$FAKE_BLOCK_DIR/$blob" ]; then
  echo "fake fetch-blob: blocked $blob" >&2
  exit 1
fi
if [ -f "$FAKE_STORE_DIR/$blob" ]; then
  cp "$FAKE_STORE_DIR/$blob" "$out"
  # The real fetch-blob.sh prints this line on stdout; callers must not read it as their result.
  echo "fetched $blob"
  exit 0
fi
echo "fake fetch-blob: BlobNotFound $blob" >&2
exit 3
EOF

cat > "$fakebin/systemctl" <<'EOF'
#!/usr/bin/env bash
set -u
d=$FAKE_SYSTEMCTL_STATE
mkdir -p "$d"
# Every call goes in the order log; the dropin tests read it. The line records whether the
# drop-in file existed at the call, because the real systemctl reads it at daemon-reload.
echo "$* dropin=$(cat "$FTG_SYNC_DROPIN_DIR/stop-timeout.conf" 2>/dev/null | tr '\n' ' ')" >> "$d/calls"
case "$1" in
  enable) echo "${@: -1}" >> "$d/enabled"; [ -n "${FAKE_ENABLE_FAIL:-}" ] && { echo "Failed to enable unit" >&2; exit 1; }; exit 0 ;;
  is-enabled)
    # Like the real command: the state on stdout, exit 1 when it is not enabled.
    if grep -qx "$2" "$d/enabled" 2>/dev/null; then echo enabled; exit 0; fi
    echo disabled; exit 1
    ;;
  daemon-reload)
    # Like the real command: no stdout, exit 1 when it cannot reload (FAKE_DAEMON_RELOAD_FAIL).
    # FAKE_DAEMON_RELOAD_FAIL_NTH=n fails only the nth daemon-reload call (the same message and exit).
    nth=$(($(grep -c '^daemon-reload' "$d/calls")))
    if [ -n "${FAKE_DAEMON_RELOAD_FAIL:-}" ] || [ "$nth" = "${FAKE_DAEMON_RELOAD_FAIL_NTH:--1}" ]; then
      echo "Failed to reload daemon: Connection timed out" >&2
      exit 1
    fi
    exit 0
    ;;
  disable)
    # Like the real `disable --now`: stops the units and removes their enable links, exit 0.
    # FAKE_DISABLE_FAIL: "Failed to disable unit", exit 1. Only the unit names are in $@ after --now.
    [ -n "${FAKE_DISABLE_FAIL:-}" ] && { echo "Failed to disable unit: Access denied" >&2; exit 1; }
    for u in "${@:3}"; do grep -vx "$u" "$d/enabled" > "$d/enabled.new" 2>/dev/null; mv "$d/enabled.new" "$d/enabled"; done
    exit 0
    ;;
  stop) echo inactive > "$d/active"; exit 0 ;;
  restart)
    n=$(($(cat "$d/invocation_n" 2>/dev/null || echo 0) + 1))
    echo "$n" > "$d/invocation_n"
    echo "inv$n" > "$d/invocation"
    # What this invocation logs: each line of journal_template, stored under its invocation ID.
    while IFS= read -r line; do echo "inv$n $line" >> "$d/journal"; done < "$d/journal_template"
    echo "${FAKE_NRESTARTS:-0}" > "$d/nrestarts"
    rm -f "$d/is_active_calls"
    # FAKE_SYSTEMCTL_ACTIVE_SEQUENCE picks this restart's state by position: word $n, or the
    # last word once $n runs past the list, or "active" with no sequence set at all. This lets
    # 1 tick's forward restart and its rollback restart (ADR 0012 section 4, rule 7) report
    # different states, for example "inactive active" (the rollback heals).
    read -r -a words <<<"${FAKE_SYSTEMCTL_ACTIVE_SEQUENCE:-active}"
    i=$((n <= ${#words[@]} ? n - 1 : ${#words[@]} - 1))
    echo "${words[$i]}" > "$d/active"
    exit 0
    ;;
  is-active)
    # FAKE_ACTIVE_FLIP_AFTER_CALLS: this restart's is-active reports $d/active for the first N
    # calls, then "failed" after that, for simulating a crash partway through a health-check
    # window (run.start logs, then the unit dies before the window ends).
    n=$(($(cat "$d/is_active_calls" 2>/dev/null || echo 0) + 1))
    echo "$n" > "$d/is_active_calls"
    # Like the real command: the state on stdout, and exit 3 when it is not "active".
    if [ -n "${FAKE_ACTIVE_FLIP_AFTER_CALLS:-}" ] && [ "$n" -gt "$FAKE_ACTIVE_FLIP_AFTER_CALLS" ]; then
      state=failed
    else
      state=$(cat "$d/active" 2>/dev/null || echo inactive)
    fi
    echo "$state"
    [ "$state" = active ]
    exit $?
    ;;
  show)
    case "$3" in
      InvocationID)
        # FAKE_INVOCATION_FLIP_AFTER_CALLS: a crash that Restart= recovers starts a new invocation.
        calls=$(cat "$d/is_active_calls" 2>/dev/null || echo 0)
        if [ -n "${FAKE_INVOCATION_FLIP_AFTER_CALLS:-}" ] && [ "$calls" -gt "$FAKE_INVOCATION_FLIP_AFTER_CALLS" ]; then
          echo inv-after-crash
        else
          cat "$d/invocation" 2>/dev/null || echo inv0
        fi
        ;;
      NRestarts)
        calls=$(cat "$d/is_active_calls" 2>/dev/null || echo 0)
        if [ -n "${FAKE_NRESTARTS_AFTER_CALLS:-}" ] && [ "$calls" -gt "$FAKE_NRESTARTS_AFTER_CALLS" ]; then
          echo 1
        else
          cat "$d/nrestarts" 2>/dev/null || echo 0
        fi
        ;;
    esac
    exit 0
    ;;
  *) exit 0 ;;
esac
EOF

cat > "$fakebin/journalctl" <<'EOF'
#!/usr/bin/env bash
# Like the real journalctl: `_SYSTEMD_INVOCATION_ID=X` keeps only the entries of that invocation
# (journal lines are "<invocation ID> <message>"); it prints the message, not the ID. No such
# argument prints every entry. A filter that matches nothing prints nothing and exits 0.
f=${FAKE_SYSTEMCTL_STATE:-/dev/null}/journal
id=
for a in "$@"; do
  case "$a" in _SYSTEMD_INVOCATION_ID=*) id=${a#_SYSTEMD_INVOCATION_ID=} ;; esac
done
while IFS=' ' read -r inv msg; do
  if [ -z "$id" ] || [ "$inv" = "$id" ]; then echo "$msg"; fi
done < "$f" 2>/dev/null
exit 0
EOF

cat > "$fakebin/mv" <<'EOF'
#!/usr/bin/env bash
# BSD mv (macOS, this test suite only) has no -T; sync.sh's production target is Linux/GNU mv,
# where -T makes a symlink-to-directory destination a plain rename target, not a move-into.
if [ "$1" = "-T" ]; then
  shift
  rm -f "$2"
  command mv "$1" "$2"
  exit $?
fi
exec /bin/mv "$@"
EOF

cat > "$fakebin/python3" <<'EOF'
#!/usr/bin/env bash
set -u
if [ "$1" = -m ] && [ "$2" = venv ]; then
  dir=$3
  mkdir -p "$dir/bin"
  cat > "$dir/bin/pip" <<'PIP_EOF'
#!/usr/bin/env bash
exit "${FAKE_PIP_EXIT:-0}"
PIP_EOF
  cat > "$dir/bin/ftg-prober" <<'GATE_EOF'
#!/usr/bin/env bash
# fake gate: `ftg-prober check-config --provider P path`
exit "${FAKE_GATE_EXIT:-0}"
GATE_EOF
  cat > "$dir/bin/python3" <<'HEALTH_EOF'
#!/usr/bin/env bash
# fake venv python3: runs the heredoc scripts of sync.sh (health_window, stop_timeout). The
# real config parsing is irrelevant here; this test controls each answer.
script=$(cat)
case "$script" in
  *teardown_timeout_seconds*)
    # FAKE_STOP_TIMEOUT_BY_TAG="v1=530 v2=180": a value per release. The release is named by the
    # last word of its config payload ("config v2", written by make_zip); $2 is the config path.
    tag=$(awk '{print $NF}' "$(dirname "$2")/payload.txt" 2>/dev/null)
    for pair in ${FAKE_STOP_TIMEOUT_BY_TAG:-}; do
      [ "${pair%%=*}" = "$tag" ] && { echo "${pair#*=}"; exit 0; }
    done
    echo "${FAKE_STOP_TIMEOUT-180}"
    ;;
  *) echo "${FAKE_HEALTH_WINDOW-2 exact}" ;;
esac
HEALTH_EOF
  chmod +x "$dir/bin/pip" "$dir/bin/ftg-prober" "$dir/bin/python3"
  exit "${FAKE_VENV_EXIT:-0}"
fi
exit 0
EOF

chmod +x "$fakebin"/*

# The real python3, found before the fakebin shadows it: the health_window test runs the real
# heredoc of sync.sh against a stub ftg_prober package.
real_python=$(command -v python3)

export PATH="$fakebin:$PATH"
export FTG_SYNC_HEALTH_POLL_S=1
export FAKE_STORE_DIR="$store"
export FAKE_BLOCK_DIR="$block"
export FAKE_SYSTEMCTL_STATE="$state"
export HEALTH_POLL_S_OVERRIDE=""
export FTG_SYNC_DROPIN_DIR="$root/dropin"
export FTG_SYNC_UNIT_DIR="$root/units"

# --- helpers ---------------------------------------------------------------------------------

sha256_of() { shasum -a 256 "$1" | cut -d' ' -f1; }

# make_zip(path, content): a real, valid zip holding 1 small file, so `unzip` genuinely works.
make_zip() {
  local path=$1 content=$2 d
  d=$(mktemp -d)
  echo "$content" > "$d/payload.txt"
  (cd "$d" && zip -q "$path" payload.txt)
  rm -rf "$d"
}

# Builds a fresh fake AWS object store: manifest.json, prober-<sha>.zip, config-<sha>.zip,
# each under code/ except manifest.json, which sits at the bucket root (ADR 0012 section 3).
seed_aws_store() {
  local tag=${1:-v1}
  mkdir -p "$store/code"
  make_zip "$store/code/prober-tmp.zip" "prober $tag"
  make_zip "$store/code/config-tmp.zip" "config $tag"
  local ph ch
  ph=$(sha256_of "$store/code/prober-tmp.zip")
  ch=$(sha256_of "$store/code/config-tmp.zip")
  mv "$store/code/prober-tmp.zip" "$store/code/prober-$ph.zip"
  mv "$store/code/config-tmp.zip" "$store/code/config-$ch.zip"
  jq -n --arg pk "code/prober-$ph.zip" --arg ck "code/config-$ch.zip" \
    '{prober_key: $pk, config_key: $ck}' > "$store/manifest.json"
}

seed_azure_store() {
  local tag=${1:-v1}
  make_zip "$store/prober-tmp.zip" "prober $tag"
  make_zip "$store/config-tmp.zip" "config $tag"
  local ph ch
  ph=$(sha256_of "$store/prober-tmp.zip")
  ch=$(sha256_of "$store/config-tmp.zip")
  mv "$store/prober-tmp.zip" "$store/prober-$ph.zip"
  mv "$store/config-tmp.zip" "$store/config-$ch.zip"
  jq -n --arg pb "prober-$ph.zip" --arg cb "config-$ch.zip" \
    '{prober_blob: $pb, config_blob: $cb}' > "$store/manifest.json"
}

# fresh_vm(provider): a clean releases/current/etc root plus sync-env, no store wiped.
fresh_vm() {
  local provider=$1
  vm="$root/vm-$RANDOM-$RANDOM"
  mkdir -p "$vm/releases" "$vm/etc"
  mkdir -p "$state"
  rm -f "$state"/*
  rm -rf "$root/units" "$root/dropin"
  rm -rf "$block"/* 2>/dev/null
  echo active > "$state/active"
  # A health check (ADR 0012 section 4, rule 6) passes by default: run.start and probe.done
  # both already in the "journal". A test that wants a failing health check overrides
  # FAKE_SYSTEMCTL_ACTIVE_SEQUENCE or FAKE_NRESTARTS instead of touching this file.
  : > "$state/journal"
  printf 'run.start\nprobe.done\n' > "$state/journal_template"
  export FTG_SYNC_RELEASES="$vm/releases"
  export FTG_SYNC_CURRENT="$vm/current"
  export FTG_SYNC_ETC="$vm/etc"
  export FTG_SYNC_BIN="$fakebin"
  {
    echo "FTG_PROVIDER=$provider"
    echo "FTG_CONFIG_FILE=targets.yaml"
    echo "FTG_PIP_EXTRA=$provider"
    echo "FTG_BUCKET=ftg-data-test"
  } > "$vm/etc/sync-env"
}

run_sync() {
  # Reset the restart counter so FAKE_SYSTEMCTL_ACTIVE_SEQUENCE's word 1 always means "this
  # tick's own first restart" (the forward one), word 2 "this tick's own second" (a rollback),
  # regardless of how many restarts earlier ticks already made in the same fresh_vm.
  rm -f "$state/invocation_n"
  bash "$sync_sh"
}

current_release_id() {
  [ -L "$1/current" ] && basename "$(readlink "$1/current")"
}

same_release() {
  local got
  got=$(current_release_id "$1")
  [ "$got" = "$2" ]
}

different_release() {
  local got
  got=$(current_release_id "$1")
  [ -n "$got" ] && [ "$got" != "$2" ]
}

release_count_is() {
  local n
  n=$(find "$1/releases" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
  [ "$n" = "$2" ]
}

previous_release_is() {
  [ -f "$1/etc/previous-release" ] && [ "$(cat "$1/etc/previous-release")" = "$2" ]
}

# block_key(key): makes a fetch of $key fail, creating any needed parent directory under
# $block (an AWS key can carry its own "code/" prefix; an Azure blob name never does).
block_key() {
  mkdir -p "$(dirname "$block/$1")"
  touch "$block/$1"
}

unblock_key() { rm -f "$block/$1"; }


# publish_sync(content): puts a hash-named sync.sh object in the AWS store, names it in the
# manifest as sync_key, and echoes its sha256.
publish_sync() {
  local f="$root/published-sync.sh" h
  printf '%s' "$1" > "$f"
  h=$(sha256_of "$f")
  mkdir -p "$store/code"
  cp "$f" "$store/code/sync-$h.sh"
  jq --arg sk "code/sync-$h.sh" '. + {sync_key: $sk}' "$store/manifest.json" > "$store/manifest.tmp"
  mv "$store/manifest.tmp" "$store/manifest.json"
  echo "$h"
}

# install_fake_sync(content): the copy already "installed" at $BIN/sync.sh.
install_fake_sync() {
  printf '%s' "$1" > "$fakebin/sync.sh"
  chmod +x "$fakebin/sync.sh"
  rm -f "$fakebin/sync.sh.prev" "$fakebin/.sync.sh.new"
}

# run_unit: ExecStartPre, ExecStart, and ExecStopPost of ftg-prober-sync.service, in the order
# systemd runs them, with the VM paths pointed at the test root. $$ is systemd's escape for a
# dollar sign. UNIT_TIMEOUT=1 skips ExecStart and reports SERVICE_RESULT=timeout, the state
# after systemd kills a run that hangs. Returns the failure of ExecStart or of ExecStopPost.
unit_line() {
  sed -n "s/^$1=//p" "$here/../ftg-prober-sync.service" \
    | sed -e "s#/opt/ftg/bin#$fakebin#g" -e "s#/etc/ftg#$vm/etc#g" -e 's/\$\$/$/g'
}
run_unit() {
  local pre start post rc=0 result=success exit_code exit_status=0
  pre=$(unit_line ExecStartPre)
  start=$(unit_line ExecStart)
  post=$(unit_line ExecStopPost)
  rm -f "$state/invocation_n"
  eval "$pre"
  if [ -n "${UNIT_TIMEOUT:-}" ]; then
    rc=1
    result=timeout
    exit_code=killed
  elif [ -n "${UNIT_STOP:-}" ]; then
    # systemctl stop or a reboot: a Type=oneshot main process ends with SIGTERM, which systemd does
    # not count as clean (exit-status.c, is_clean_exit): result=signal, EXIT_CODE=killed,
    # EXIT_STATUS=TERM. UNIT_STOP_RESULT and UNIT_EXIT_STATUS set another outcome.
    rc=0
    result=${UNIT_STOP_RESULT:-signal}
    exit_code=${UNIT_EXIT_CODE-killed}
    exit_status=${UNIT_EXIT_STATUS-TERM}
  else
    exit_code=exited
    eval "$start"
    rc=$?
    [ "$rc" -eq 0 ] || result=exit-code
  fi
  SERVICE_RESULT=$result EXIT_CODE=$exit_code EXIT_STATUS=$exit_status eval "$post" || rc=1
  return "$rc"
}

GOOD_SYNC=$'#!/usr/bin/env bash\necho good sync\n'

# --- 1. First boot, a good build -------------------------------------------------------------

fresh_vm aws
rm -f "$store"/code/*.zip "$store/manifest.json" 2>/dev/null
seed_aws_store v1
run_sync >"$root/log.1" 2>&1
rc=$?
check "first boot good: exit 0" test "$rc" -eq 0
check "first boot good: current is set" test -n "$(current_release_id "$vm")"
check "first boot good: manifest is cached" test -f "$vm/etc/manifest.json"
check "first boot good: service is enabled and restarted" test -f "$state/active"
check "first boot good: no manifest.failed" test ! -f "$vm/etc/manifest.failed"

# --- 2. First boot, a bad config (fails the gate) --------------------------------------------

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
FAKE_GATE_EXIT=2 run_sync >"$root/log.2" 2>&1
rc=$?
check "first boot bad config: exit nonzero (unit shows failed)" test "$rc" -ne 0
check "first boot bad config: current never created" test ! -e "$vm/current"
check "first boot bad config: manifest.failed written" test -f "$vm/etc/manifest.failed"

# --- 3. First boot, passes the gate but fails the health check -------------------------------

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
FAKE_SYSTEMCTL_ACTIVE_SEQUENCE=inactive run_sync >"$root/log.3" 2>&1
rc=$?
check "first boot health-check fail: exit nonzero" test "$rc" -ne 0
check "first boot health-check fail: current removed" test ! -e "$vm/current"
check "first boot health-check fail: manifest.failed written" test -f "$vm/etc/manifest.failed"
check "first boot health-check fail: release directory removed" release_count_is "$vm" 0

# --- 4. A normal update, good build -----------------------------------------------------------

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
run_sync >"$root/log.4a" 2>&1
first_id=$(current_release_id "$vm")
seed_aws_store v2
run_sync >"$root/log.4b" 2>&1
rc=$?
second_id=$(current_release_id "$vm")
check "normal update good: exit 0" test "$rc" -eq 0
check "normal update good: current moved to the new release" test "$second_id" != "$first_id"
check "normal update good: previous-release set to the old one" previous_release_is "$vm" "$first_id"
check "normal update good: exactly 2 releases kept" release_count_is "$vm" 2

# --- 5. A normal update that fails the sha256 check -------------------------------------------

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
run_sync >/dev/null 2>&1
before=$(current_release_id "$vm")
seed_aws_store v2
# Corrupt the just-uploaded prober zip after naming it with a (now wrong) sha256. seed_aws_store
# never deletes an older version's zip, so name it from the manifest, not a glob.
bad="$store/$(jq -r .prober_key "$store/manifest.json")"
echo corrupt >> "$bad"
run_sync >"$root/log.5" 2>&1
rc=$?
check "sha256 mismatch: exit 0 (running service untouched, not a failed unit)" test "$rc" -eq 0
check "sha256 mismatch: current unchanged" same_release "$vm" "$before"
check "sha256 mismatch: manifest.failed written" test -f "$vm/etc/manifest.failed"

# --- 6. A download failure that heals before 1 hour -------------------------------------------

fresh_vm aws
rm -rf "$store" "$block" && mkdir -p "$store" "$block"
seed_aws_store v1
run_sync >/dev/null 2>&1
before=$(current_release_id "$vm")
seed_aws_store v2
key=$(jq -r .prober_key "$store/manifest.json")
block_key "$key"
run_sync >"$root/log.6a" 2>&1
check "download failing: download-failing-since written" test -f "$vm/etc/download-failing-since"
check "download failing: current untouched" same_release "$vm" "$before"
check "download failing: no manifest.failed yet" test ! -f "$vm/etc/manifest.failed"
unblock_key "$key"
run_sync >"$root/log.6b" 2>&1
check "download heals: download-failing-since cleared" test ! -f "$vm/etc/download-failing-since"
check "download heals: current advances" different_release "$vm" "$before"

# --- 7. A download failure that trips the 1 hour limit ----------------------------------------

fresh_vm aws
rm -rf "$store" "$block" && mkdir -p "$store" "$block"
seed_aws_store v1
run_sync >/dev/null 2>&1
before=$(current_release_id "$vm")
seed_aws_store v2
key=$(jq -r .prober_key "$store/manifest.json")
block_key "$key"
run_sync >/dev/null 2>&1
echo $(($(date -u +%s) - 3601)) > "$vm/etc/download-failing-since"
run_sync >"$root/log.7" 2>&1
check "download limit tripped: manifest.failed written" test -f "$vm/etc/manifest.failed"
check "download limit tripped: download-failing-since cleared" test ! -f "$vm/etc/download-failing-since"
check "download limit tripped: current untouched" same_release "$vm" "$before"
unblock_key "$key"

# --- 8. Unchanged manifest: no-op, clears a leftover clock ------------------------------------

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
run_sync >/dev/null 2>&1
before=$(current_release_id "$vm")
echo 12345 > "$vm/etc/download-failing-since"
run_sync >"$root/log.8" 2>&1
rc=$?
check "unchanged manifest: exit 0" test "$rc" -eq 0
check "unchanged manifest: current unchanged" same_release "$vm" "$before"
check "unchanged manifest: leftover clock cleared" test ! -f "$vm/etc/download-failing-since"

# --- 9. manifest.failed blocks a retry of the same manifest, until a new 1 arrives -------------

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
run_sync >/dev/null 2>&1
seed_aws_store v2
FAKE_GATE_EXIT=2 run_sync >/dev/null 2>&1
FAKE_GATE_EXIT=2 run_sync >"$root/log.9a" 2>&1
rc=$?
check "manifest.failed retried: exit 0 (not a failed unit; old build still runs)" test "$rc" -eq 0
seed_aws_store v3
run_sync >"$root/log.9b" 2>&1
rc=$?
check "manifest.failed cleared by a new manifest: exit 0" test "$rc" -eq 0
check "manifest.failed cleared by a new manifest: current advances" test -n "$(current_release_id "$vm")"

# --- 10. A normal update whose health check fails, and the rollback succeeds -------------------

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
run_sync >/dev/null 2>&1
good_id=$(current_release_id "$vm")
seed_aws_store v2
FAKE_SYSTEMCTL_ACTIVE_SEQUENCE="inactive active" run_sync >"$root/log.10" 2>&1
rc=$?
check "rollback succeeds: exit 0 (not a failed unit)" test "$rc" -eq 0
check "rollback succeeds: current back on the good release" \
  same_release "$vm" "$good_id"
check "rollback succeeds: previous-release unchanged" \
  previous_release_is "$vm" "$good_id"
check "rollback succeeds: manifest.failed written for the bad manifest" test -f "$vm/etc/manifest.failed"

# --- 11. A health check failure whose own rollback restart also fails health: terminal --------

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
run_sync >/dev/null 2>&1
seed_aws_store v2
# Both the forward restart and the rollback restart (there is a distinct previous-release, v1,
# to try) fail health the same way.
FAKE_SYSTEMCTL_ACTIVE_SEQUENCE="inactive inactive" run_sync >"$root/log.11" 2>&1
rc=$?
check "rollback also fails: exit nonzero (terminal, unit shows failed)" test "$rc" -ne 0
check "rollback also fails: rollback.failed written" test -f "$vm/etc/rollback.failed"
run_sync >"$root/log.11b" 2>&1
check "rollback also fails: a later tick with no new manifest stays failed" test "$?" -ne 0

# --- 12. A revert to a release still on disk: fetch and install are skipped, gate still runs ---

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
run_sync >/dev/null 2>&1
v1_id=$(current_release_id "$vm")
# Save the exact v1 manifest and zip bytes: a zip is not byte-reproducible across 2 separate
# builds (its own local header carries a timestamp), so re-seeding "v1" a 2nd time would name a
# new prober/config key and so compute a different release ID, not a true revert.
v1_manifest=$(cat "$store/manifest.json")
cp "$store/$(jq -r .prober_key "$store/manifest.json")" "$root/v1-prober.zip"
cp "$store/$(jq -r .config_key "$store/manifest.json")" "$root/v1-config.zip"
seed_aws_store v2
run_sync >/dev/null 2>&1 # rule 9: v1's release directory is kept as previous-release
# Revert: restore the exact v1 manifest and zip bytes, then block both zip fetches so a real
# fetch attempt would fail. A pass proves sync.sh reused the kept release directory instead.
echo "$v1_manifest" > "$store/manifest.json"
key_p=$(jq -r .prober_key "$store/manifest.json")
key_c=$(jq -r .config_key "$store/manifest.json")
mkdir -p "$(dirname "$store/$key_p")" "$(dirname "$store/$key_c")"
cp "$root/v1-prober.zip" "$store/$key_p"
cp "$root/v1-config.zip" "$store/$key_c"
block_key "$key_p"
block_key "$key_c"
run_sync >"$root/log.12" 2>&1
rc=$?
check "revert reuses an existing release: exit 0 with no fetch needed" test "$rc" -eq 0
check "revert reuses an existing release: current back on v1" \
  same_release "$vm" "$v1_id"
unblock_key "$key_p"
unblock_key "$key_c"

# --- 13. Azure: a BlobNotFound manifest poll is routine, not a failure -------------------------

fresh_vm azure
rm -rf "$store" && mkdir -p "$store"
seed_azure_store v1
run_sync >"$root/log.13a" 2>&1
# The fake fetch-blob.sh prints "fetched <blob>" on stdout like the real one: sync.sh
# must read the exit code, not that stdout, or no Azure release ever installs.
check "azure first boot: the release installs when fetch-blob.sh prints on stdout" \
  test -n "$(current_release_id "$vm")"
before=$(current_release_id "$vm")
mv "$store/manifest.json" "$store/manifest.json.bak"
run_sync >"$root/log.13" 2>&1
rc=$?
mv "$store/manifest.json.bak" "$store/manifest.json"
check "azure BlobNotFound: exit 0" test "$rc" -eq 0
check "azure BlobNotFound: current untouched" same_release "$vm" "$before"

# --- 14. An install killed mid-build leaves a release with no .complete; the next tick cleans it up --

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
run_sync >/dev/null 2>&1
seed_aws_store v2
# Simulate a half-built release: a directory with no .complete marker.
half="$vm/releases/deadbeef"
mkdir -p "$half/prober"
run_sync >"$root/log.14" 2>&1
check "interrupted build cleaned up: no stray incomplete release" test ! -d "$half"

# --- 15. The health check must hold until the END of the window, not just the first poll -----

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
# run.start (and probe.done) are logged from the first poll (the fake journal never changes),
# but the unit itself dies after 1 is-active call: a health check that returns healthy the
# moment it first sees everything pass, instead of polling the whole window, would miss this.
FAKE_HEALTH_WINDOW="3 exact" FAKE_ACTIVE_FLIP_AFTER_CALLS=1 run_sync >"$root/log.15" 2>&1
rc=$?
check "health check regression: a crash mid-window still fails (exit nonzero)" test "$rc" -ne 0
check "health check regression: current removed (first-boot failure)" test ! -e "$vm/current"
check "health check regression: manifest.failed written" test -f "$vm/etc/manifest.failed"

# --- 16. sync.sh refreshes itself from the manifest's own hash-named sync key ------------------

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
echo "old sync.sh" > "$fakebin/sync.sh" # the copy already "installed" at $BIN/sync.sh
new_sync="$root/new-sync.sh"
printf '#!/usr/bin/env bash\necho new sync.sh\n' > "$new_sync"
new_hash=$(sha256_of "$new_sync")
mkdir -p "$store/code"
cp "$new_sync" "$store/code/sync-$new_hash.sh"
manifest=$(cat "$store/manifest.json")
echo "$manifest" | jq --arg sk "code/sync-$new_hash.sh" '. + {sync_key: $sk}' > "$store/manifest.json"
run_sync >"$root/log.16" 2>&1
rc=$?
check "self-refresh: exit 0" test "$rc" -eq 0
check "self-refresh: the installed copy is replaced" cmp -s "$new_sync" "$fakebin/sync.sh"
check "self-refresh: the installed copy is executable" test -x "$fakebin/sync.sh"
check "self-refresh: no release work happened this tick" test ! -e "$vm/current"
check "self-refresh: logged" grep -q sync.self_updated "$root/log.16"
echo "#!/usr/bin/env bash" > "$fakebin/sync.sh" # restore a harmless fake for later tests
chmod +x "$fakebin/sync.sh"

# --- 17. rollback.failed is cleared once a later, different manifest installs healthy ----------

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
run_sync >/dev/null 2>&1
seed_aws_store v2
FAKE_SYSTEMCTL_ACTIVE_SEQUENCE="inactive inactive" run_sync >/dev/null 2>&1
check "rollback.failed set up: present" test -f "$vm/etc/rollback.failed"
seed_aws_store v3
run_sync >"$root/log.17" 2>&1
rc=$?
check "rollback.failed cleared on a later healthy install: exit 0" test "$rc" -eq 0
check "rollback.failed cleared on a later healthy install: file removed" test ! -f "$vm/etc/rollback.failed"
# A later tick whose manifest is unchanged (or, on Azure, BlobNotFound) must not re-fail: with
# rollback.failed gone, this is the plain rule 10 / rule 1 path, not the terminal one.
run_sync >"$root/log.17b" 2>&1
check "rollback.failed cleared: a later unchanged tick stays healthy (exit 0)" test "$?" -eq 0

# --- 18. A gate failure never deletes a release current or previous-release still names -------

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
run_sync >/dev/null 2>&1
v1_id=$(current_release_id "$vm")
# Force a full re-validation of this exact, already-installed release (rule 2, rule 4: the gate
# runs again on a revert, even with nothing to fetch or rebuild): write a bogus rollback.failed
# so the next tick's own v1 manifest counts as "a new manifest replaces the one rollback.failed
# named" and revalidates in full, per the force_full_reinstall path.
echo '{"bogus":"not-the-real-manifest"}' > "$vm/etc/rollback.failed"
FAKE_GATE_EXIT=2 run_sync >"$root/log.18" 2>&1
check "gate failure on the current release: the release directory survives" test -d "$vm/releases/$v1_id"
check "gate failure on the current release: current still points at it" same_release "$vm" "$v1_id"
check "gate failure on the current release: manifest.failed written" test -f "$vm/etc/manifest.failed"

# --- 19. A repeated download failure logs the last fetch error, not only the time stamp --------

fresh_vm aws
rm -rf "$store" "$block" && mkdir -p "$store" "$block"
seed_aws_store v1
run_sync >/dev/null 2>&1
seed_aws_store v2
key=$(jq -r .prober_key "$store/manifest.json")
block_key "$key"
run_sync >/dev/null 2>&1
echo $(($(date -u +%s) - 3601)) > "$vm/etc/download-failing-since"
run_sync >"$root/log.19" 2>&1
check "download limit tripped: event names the last error, not just the time" \
  grep -q "sync.download_failed_repeatedly.*error=.*blocked" "$root/log.19"
unblock_key "$key"

# --- 20. Two ticks: tick 1 self-updates, tick 2 sees the matching copy and installs the release -

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
install_fake_sync $'#!/usr/bin/env bash\necho old\n'
new_hash=$(publish_sync "$GOOD_SYNC")
run_sync >"$root/log.20a" 2>&1
check "2 ticks: tick 1 self-updates" grep -q "sync.self_updated" "$root/log.20a"
check "2 ticks: tick 1 installs no release" test ! -e "$vm/current"
check "2 ticks: the old copy is kept as sync.sh.prev" grep -q "old" "$fakebin/sync.sh.prev"
check "2 ticks: sync.trial names the new hash" test "$(cat "$vm/etc/sync.trial")" = "$new_hash"
run_sync >"$root/log.20b" 2>&1
check "2 ticks: tick 2 reports no update" bash -c '! grep -q sync.self_updated "$1"' _ "$root/log.20b"
check "2 ticks: tick 2 installs the release" test -n "$(current_release_id "$vm")"
check "2 ticks: tick 2 clears sync.trial" test ! -f "$vm/etc/sync.trial"

# --- 21. A sync key with no sha256 in its name fails -------------------------------------------

fresh_vm aws
rm -rf "$store" && mkdir -p "$store/code"
seed_aws_store v1
install_fake_sync "$GOOD_SYNC"
echo "$GOOD_SYNC" > "$store/code/sync-latest.sh"
jq '. + {sync_key: "code/sync-latest.sh"}' "$store/manifest.json" > "$store/m.tmp" && mv "$store/m.tmp" "$store/manifest.json"
run_sync >"$root/log.21" 2>&1
check "sync key without a hash: exit nonzero" test "$?" -ne 0
check "sync key without a hash: event logged" grep -q "sync.self_update_failed" "$root/log.21"
check "sync key without a hash: no release installed" test ! -e "$vm/current"

# --- 22. A bad sync.sh in the bucket is refused and the old one stays --------------------------

for bad in $'#!/usr/bin/env bash\nif then\n' $'echo no shebang\n'; do
  fresh_vm aws
  rm -rf "$store" && mkdir -p "$store"
  seed_aws_store v1
  install_fake_sync "$GOOD_SYNC"
  bad_hash=$(publish_sync "$bad")
  run_sync >"$root/log.22" 2>&1
  check "bad sync.sh: refused" grep -q "sync.self_update_refused" "$root/log.22"
  check "bad sync.sh: the old copy stays" test "$(cat "$fakebin/sync.sh")" = "$(printf '%s' "$GOOD_SYNC")"
  check "bad sync.sh: no staged file left" test ! -e "$fakebin/.sync.sh.new"
  check "bad sync.sh: hash blacklisted" grep -qx "$bad_hash" "$vm/etc/sync.failed"
  check "bad sync.sh: the release still installs" test -n "$(current_release_id "$vm")"
done

# --- 23. A new sync.sh that fails at run is reverted by the unit and not re-installed ----------

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
install_fake_sync "$GOOD_SYNC"
crash_hash=$(publish_sync $'#!/usr/bin/env bash\nexit 7\n')
run_sync >"$root/log.23a" 2>&1
check "failing sync.sh: tick 1 installs it" grep -q "sync.self_updated" "$root/log.23a"
run_unit >"$root/log.23b" 2>&1
check "failing sync.sh: the unit fails" test "$?" -ne 0
check "failing sync.sh: reverted event" grep -q "sync.self_update_reverted" "$root/log.23b"
check "failing sync.sh: sync.sh is the previous copy again" test "$(cat "$fakebin/sync.sh")" = "$(printf '%s' "$GOOD_SYNC")"
check "failing sync.sh: hash blacklisted" grep -qx "$crash_hash" "$vm/etc/sync.failed"
check "failing sync.sh: trial marker removed" test ! -f "$vm/etc/sync.trial"
run_sync >"$root/log.23c" 2>&1
check "failing sync.sh: not re-installed" bash -c '! grep -q sync.self_updated "$1"' _ "$root/log.23c"
check "failing sync.sh: the release installs next tick" test -n "$(current_release_id "$vm")"
# A release failure of a proven sync.sh (no trial marker) must not revert anything.
echo "$GOOD_SYNC" > "$fakebin/sync.sh.prev"
run_unit >"$root/log.23d" 2>&1
check "proven sync.sh: no revert" bash -c '! grep -q sync.self_update_reverted "$1"' _ "$root/log.23d"

# --- 24. An empty health window fails closed ---------------------------------------------------

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
FAKE_HEALTH_WINDOW="" run_sync >"$root/log.24" 2>&1
check "empty health window: exit nonzero" test "$?" -ne 0
check "empty health window: event logged" grep -q "sync.health_window_invalid" "$root/log.24"
check "empty health window: first boot failed, no current" test ! -e "$vm/current"

# --- 25. Lifecycle: first boot, self-update, healthy install, no-op, update -----------------------

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
install_fake_sync $'#!/usr/bin/env bash\necho first-boot copy\n'
publish_sync "$GOOD_SYNC" >/dev/null
run_sync >/dev/null 2>&1
check "lifecycle: tick 1 self-updates only" test ! -e "$vm/current"
run_sync >/dev/null 2>&1
v1_id=$(current_release_id "$vm")
check "lifecycle: tick 2 is healthy on v1" test -n "$v1_id"
check "lifecycle: manifest cached" test -f "$vm/etc/manifest.json"
run_sync >"$root/log.25c" 2>&1
check "lifecycle: tick 3 is a no-op, exit 0" test "$?" -eq 0
check "lifecycle: tick 3 keeps v1" same_release "$vm" "$v1_id"
seed_aws_store v2
publish_sync "$GOOD_SYNC" >/dev/null
run_sync >/dev/null 2>&1
check "lifecycle: tick 4 moves to v2" different_release "$vm" "$v1_id"
check "lifecycle: v1 is previous-release" previous_release_is "$vm" "$v1_id"
check "lifecycle: no failure markers" test ! -f "$vm/etc/manifest.failed" -a ! -f "$vm/etc/sync.failed" -a ! -f "$vm/etc/rollback.failed"

# --- 26. The health check fails on a crash that Restart= recovers (NRestarts, new invocation) ---

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
# After 1 poll the unit crashes and systemd restarts it: it reads "active" again, and the journal
# of the first invocation still shows run.start, but NRestarts is 1. Only that counter notices.
FAKE_HEALTH_WINDOW="3 exact" FAKE_NRESTARTS_AFTER_CALLS=1 run_sync >"$root/log.26a" 2>&1
check "NRestarts above 0 mid-window: exit nonzero" test "$?" -ne 0
check "NRestarts above 0 mid-window: logged" grep -q "NRestarts is above 0" "$root/log.26a"
check "NRestarts above 0 mid-window: no current" test ! -e "$vm/current"

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
# The same crash seen as a new InvocationID, with NRestarts still 0 (for example a manual restart
# during the window): the invocation the check started with is gone, so it must fail too.
FAKE_HEALTH_WINDOW="3 exact" FAKE_INVOCATION_FLIP_AFTER_CALLS=1 run_sync >"$root/log.26b" 2>&1
check "new invocation mid-window: exit nonzero" test "$?" -ne 0
check "new invocation mid-window: logged" grep -q "started a new invocation" "$root/log.26b"
check "new invocation mid-window: no current" test ! -e "$vm/current"

# --- 27. health_window: the exact/capped boundary compares the raw fastest cadence -------------

stub="$root/stub-ftg"
mkdir -p "$stub/ftg_prober"
for m in config probes scheduler store; do : >"$stub/ftg_prober/$m.py"; done
: >"$stub/ftg_prober/__init__.py"
echo 'def load_config(path): return None' >"$stub/ftg_prober/config.py"
printf 'def load_probes(p): return None\ndef packages_for(p): return None\n' >"$stub/ftg_prober/probes.py"
echo 'class FileStore:
    def __init__(self, path): pass' >"$stub/ftg_prober/store.py"
printf 'import os\nclass Scheduler:\n    def __init__(self, *a, **k):\n        v = os.environ["STUB_CADENCES"]\n        self.cadence_s = {str(i): int(x) for i, x in enumerate(v.split())}\n' >"$stub/ftg_prober/scheduler.py"
heredoc_py="$root/health_window.py"
sed -n "/<<'PYEOF'/,/^PYEOF/{p;/^PYEOF/q;}" "$sync_sh" | sed '1d;$d' >"$heredoc_py"
window_for() { STUB_CADENCES="$1" PYTHONPATH="$stub" "$real_python" "$heredoc_py" cfg.yaml aws 60 900; }
check "health window: 300 s cadence is exact with a 360 s window" test "$(window_for "300 7200")" = "360 exact"
check "health window: a cadence of cap minus margin (840 s) is exact, not capped" test "$(window_for "840")" = "900 exact"
check "health window: a cadence equal to the cap (900 s) is exact (at or under the cap)" test "$(window_for "900")" = "900 exact"
check "health window: a cadence of 901 s is capped" test "$(window_for "901")" = "900 capped"
check "health window: a cadence above the cap is capped" test "$(window_for "3600")" = "900 capped"
check "health window: no cadence is capped" test "$(window_for "")" = "900 capped"

# --- 28. A health mode other than exact or capped fails closed -----------------------------------

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
FAKE_HEALTH_WINDOW="5 weekly" run_sync >"$root/log.28" 2>&1
check "unknown health mode: exit nonzero" test "$?" -ne 0
check "unknown health mode: event logged" grep -q "sync.health_window_invalid.*mode=weekly" "$root/log.28"
check "unknown health mode: first boot failed, no current" test ! -e "$vm/current"

# --- 29. A capped window skips the probe.done check; an exact window needs it ------------------

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
printf 'run.start\n' >"$state/journal_template"
FAKE_HEALTH_WINDOW="2 capped" run_sync >"$root/log.29a" 2>&1
check "capped window: healthy with no probe.done" test -n "$(current_release_id "$vm")"
fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
printf 'run.start\n' >"$state/journal_template"
FAKE_HEALTH_WINDOW="2 exact" run_sync >"$root/log.29b" 2>&1
check "exact window: fails with no probe.done" test "$?" -ne 0

# --- 30. A prober or config key with no sha256 fails the manifest, as the sync key does ---------

for field in prober_key config_key; do
  fresh_vm aws
  rm -rf "$store" && mkdir -p "$store"
  seed_aws_store v1
  jq --arg f "$field" '.[$f] = "code/unhashed-latest.zip"' "$store/manifest.json" >"$store/m.tmp" && mv "$store/m.tmp" "$store/manifest.json"
  run_sync >"$root/log.30" 2>&1
  check "hashless $field: exit nonzero (first boot)" test "$?" -ne 0
  check "hashless $field: event logged" grep -q "sync.build_failed.*no sha256" "$root/log.30"
  check "hashless $field: manifest.failed written" test -f "$vm/etc/manifest.failed"
  check "hashless $field: no current" test ! -e "$vm/current"
done
# On a running VM the same manifest leaves the running build alone and is not retried.
fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
run_sync >/dev/null 2>&1
before=$(current_release_id "$vm")
seed_aws_store v2
jq '.prober_key = "code/unhashed-latest.zip"' "$store/manifest.json" >"$store/m.tmp" && mv "$store/m.tmp" "$store/manifest.json"
run_sync >"$root/log.30b" 2>&1
check "hashless key on a running VM: exit 0, current unchanged" bash -c 'test "$1" -eq 0' _ "$?"
check "hashless key on a running VM: current unchanged" same_release "$vm" "$before"
check "hashless key on a running VM: manifest.failed written" test -f "$vm/etc/manifest.failed"

# --- 31. The fetch helpers read the exit code, not the stdout of aws and fetch-blob.sh ---------

fresh_vm azure
rm -rf "$store" && mkdir -p "$store"
seed_azure_store v1
run_sync >"$root/log.31a" 2>&1
check "azure: fetch-blob.sh stdout does not break a fetch" test -n "$(current_release_id "$vm")"
check "aws fake prints JSON on stdout like the real CLI" bash -c '"$1/aws" s3api get-object --bucket b --key manifest.json "$2" | grep -q ContentLength' _ "$fakebin" "$root/obj.out"
fresh_vm aws
check "fake systemctl is-active exits 3 when not active" bash -c 'echo inactive >"$1/active"; ! "$2/systemctl" is-active x >/dev/null' _ "$state" "$fakebin"
echo active >"$state/active"

# --- 32. A self-update that cannot stage or keep sync.sh.prev is not installed -----------------

if [ "$(id -u)" -ne 0 ]; then
  fresh_vm aws
  rm -rf "$store" && mkdir -p "$store"
  seed_aws_store v1
  install_fake_sync "$GOOD_SYNC"
  publish_sync $'#!/usr/bin/env bash\necho new\n' >/dev/null
  : >"$fakebin/.sync.sh.new"
  chmod 0444 "$fakebin/.sync.sh.new"
  run_sync >"$root/log.32a" 2>&1
  check "unwritable staging file: event logged" grep -q "sync.self_update_failed.*could not stage" "$root/log.32a"
  check "unwritable staging file: the old copy stays" test "$(cat "$fakebin/sync.sh")" = "$(printf '%s' "$GOOD_SYNC")"
  check "unwritable staging file: no trial marker" test ! -f "$vm/etc/sync.trial"
  rm -f "$fakebin/.sync.sh.new"

  fresh_vm aws
  rm -rf "$store" && mkdir -p "$store"
  seed_aws_store v1
  install_fake_sync "$GOOD_SYNC"
  publish_sync $'#!/usr/bin/env bash\necho new\n' >/dev/null
  echo stale >"$fakebin/sync.sh.prev"
  chmod 0444 "$fakebin/sync.sh.prev"
  run_sync >"$root/log.32b" 2>&1
  check "unwritable sync.sh.prev: event logged" grep -q "sync.self_update_failed.*sync.sh.prev" "$root/log.32b"
  check "unwritable sync.sh.prev: the old copy stays" test "$(cat "$fakebin/sync.sh")" = "$(printf '%s' "$GOOD_SYNC")"
  check "unwritable sync.sh.prev: no trial marker" test ! -f "$vm/etc/sync.trial"
  rm -f "$fakebin/sync.sh.prev" "$fakebin/.sync.sh.new"
fi

# --- 33. The sync unit: revert wrapper, timeout, and a trial that never proves itself ----------

unit_value() { sed -n "s/^$1=//p" "$here/../ftg-prober-sync.service"; }
cap_s=$(sed -n 's/^HEALTH_CAP_S=//p' "$sync_sh")
timeout_s=$(unit_value TimeoutStartSec)
check "unit: TimeoutStartSec is a plain number of seconds" bash -c '[[ "$1" =~ ^[0-9]+$ ]]' _ "$timeout_s"
check "unit: TimeoutStartSec covers 2 health windows plus 1800 s of build" \
  bash -c 'test "$1" -ge $((2 * $2 + 1800))' _ "${timeout_s:-0}" "$cap_s"
check "unit: the revert is an ExecStopPost, outside the cgroup the timeout kills" \
  bash -c 'grep -q "^ExecStopPost=.*sync.self_update_reverted" "$1" && ! grep "^ExecStart=" "$1" | grep -q reverted' _ "$here/../ftg-prober-sync.service"

# sync.sh.prev missing: the revert cannot work, and the unit must say so and fail.
fresh_vm aws
install_fake_sync $'#!/usr/bin/env bash\nexit 7\n'
echo trialhash >"$vm/etc/sync.trial"
run_unit >"$root/log.33a" 2>&1
check "revert with no sync.sh.prev: the unit fails" test "$?" -ne 0
check "revert with no sync.sh.prev: says so" grep -q "sync.self_update_revert_failed" "$root/log.33a"
check "revert with no sync.sh.prev: no false reverted event" bash -c '! grep -q "sync.self_update_reverted" "$1"' _ "$root/log.33a"
check "revert with no sync.sh.prev: hash blacklisted" grep -qx trialhash "$vm/etc/sync.failed"
check "revert with no sync.sh.prev: trial removed" test ! -f "$vm/etc/sync.trial"

# A hung run: systemd kills it (SERVICE_RESULT=timeout) and ExecStopPost still reverts.
fresh_vm aws
install_fake_sync $'#!/usr/bin/env bash\nsleep 100000\n'
printf '#!/usr/bin/env bash\necho good prev\n' >"$fakebin/sync.sh.prev"
echo hunghash >"$vm/etc/sync.trial"
UNIT_TIMEOUT=1 run_unit >"$root/log.33b" 2>&1
check "timeout: the unit fails" test "$?" -ne 0
check "timeout: reverted event" grep -q "sync.self_update_reverted" "$root/log.33b"
check "timeout: sync.sh is the previous copy again" grep -q "good prev" "$fakebin/sync.sh"
check "timeout: hash blacklisted" grep -qx hunghash "$vm/etc/sync.failed"

# A stop or a reboot during the trial tick (result=signal, EXIT_CODE=killed, EXIT_STATUS=TERM) must
# not revert a good copy or blacklist it. Every case is a documented systemd combination: SERVICE_RESULT=exit-code
# always comes with EXIT_CODE=exited and a numeric EXIT_STATUS, so it is not a stop
# (https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#Environment%20variables%20set%20or%20propagated%20by%20the%20service%20manager).
for stop in signal:TERM signal:INT signal:HUP; do
  stop_result=${stop%%:*} stop_status=${stop##*:}
  fresh_vm aws
  install_fake_sync $'#!/usr/bin/env bash\necho trial copy\n'
  printf '#!/usr/bin/env bash\necho good prev\n' >"$fakebin/sync.sh.prev"
  echo stophash >"$vm/etc/sync.trial"
  UNIT_STOP=1 UNIT_STOP_RESULT=$stop_result UNIT_EXIT_STATUS=$stop_status run_unit >"$root/log.33s" 2>&1
  check "stop during the trial ($stop_result/$stop_status): no revert" bash -c '! grep -q "sync.self_update_reverted" "$1"' _ "$root/log.33s"
  check "stop during the trial ($stop_result/$stop_status): the copy on trial stays" grep -q "trial copy" "$fakebin/sync.sh"
  check "stop during the trial ($stop_result/$stop_status): not blacklisted, still on trial" bash -c '! test -f "$1/sync.failed" && test -f "$1/sync.trial"' _ "$vm/etc"
done

# SERVICE_RESULT=resources (a failed system operation, for example a fork or a cgroup failure) can come
# with no EXIT_CODE and no EXIT_STATUS (the table lists "any of the above" for it, so they may be set;
# only `protocol` and `start-limit-hit` are "not set"), so the catch-all arm must not revert without
# EXIT_CODE=exited: the guard.
# (https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#Environment%20variables%20set%20or%20propagated%20by%20the%20service%20manager)
fresh_vm aws
install_fake_sync $'#!/usr/bin/env bash\necho trial copy\n'
printf '#!/usr/bin/env bash\necho good prev\n' >"$fakebin/sync.sh.prev"
echo reshash >"$vm/etc/sync.trial"
UNIT_STOP=1 UNIT_STOP_RESULT=resources UNIT_EXIT_CODE= UNIT_EXIT_STATUS= run_unit >"$root/log.33r" 2>&1
check "resources, no EXIT_CODE: no revert" bash -c '! grep -q "sync.self_update_reverted" "$1"' _ "$root/log.33r"
check "resources, no EXIT_CODE: the copy on trial stays" grep -q "trial copy" "$fakebin/sync.sh"
check "resources, no EXIT_CODE: not blacklisted, still on trial" bash -c '! test -f "$1/sync.failed" && test -f "$1/sync.trial"' _ "$vm/etc"

# A trial copy that exits 0 at once never removes sync.trial: the unit must treat it as failed.
fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
cp "$sync_sh" "$fakebin/sync.sh" # the real script as the installed copy
chmod +x "$fakebin/sync.sh"
rm -f "$fakebin/sync.sh.prev"
imposter_hash=$(publish_sync $'#!/usr/bin/env bash\nexit 0\n')
run_unit >"$root/log.33c" 2>&1
check "trial: the self-update tick itself is not reverted" bash -c '! grep -q "sync.self_update_reverted" "$1"' _ "$root/log.33c"
check "trial: the self-update tick leaves the new copy installed" grep -q "exit 0" "$fakebin/sync.sh"
check "trial: the self-update tick leaves sync.trial" test -f "$vm/etc/sync.trial"
run_unit >"$root/log.33d" 2>&1
check "trial never proven: the unit fails" test "$?" -ne 0
check "trial never proven: reverted event" grep -q "sync.self_update_reverted" "$root/log.33d"
check "trial never proven: the real script is back" grep -q "VM-side pull and restart" "$fakebin/sync.sh"
check "trial never proven: hash blacklisted" grep -qx "$imposter_hash" "$vm/etc/sync.failed"
run_unit >"$root/log.33e" 2>&1
check "trial reverted: the next unit run installs the release" test -n "$(current_release_id "$vm")"
check "trial reverted: the unit is healthy again" bash -c '! grep -q "sync.self_update_reverted" "$1"' _ "$root/log.33e"
echo "#!/usr/bin/env bash" >"$fakebin/sync.sh"
rm -f "$fakebin/sync.sh.prev"

# --- 34. ADR 0012 tests: pip failure, the download clock, and fetch errors ---------------------

fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
run_sync >/dev/null 2>&1
before=$(current_release_id "$vm")
seed_aws_store v2
FAKE_PIP_EXIT=1 run_sync >"$root/log.34a" 2>&1
check "pip install fails: exit 0, running service untouched" test "$?" -eq 0
check "pip install fails: current unchanged" same_release "$vm" "$before"
check "pip install fails: manifest.failed written" test -f "$vm/etc/manifest.failed"
check "pip install fails: logged after 3 attempts" grep -q "pip install failed.*after 3 attempts" "$root/log.34a"
check "pip install fails: no failed release directory left" release_count_is "$vm" 1

# 2 different bad manifests: the clock keeps the time of the first failure.
fresh_vm aws
rm -rf "$store" "$block" && mkdir -p "$store" "$block"
seed_aws_store v1
run_sync >/dev/null 2>&1
seed_aws_store v2
block_key "$(jq -r .prober_key "$store/manifest.json")"
run_sync >/dev/null 2>&1
first_clock=$(cat "$vm/etc/download-failing-since")
sleep 1
seed_aws_store v3
block_key "$(jq -r .prober_key "$store/manifest.json")"
run_sync >/dev/null 2>&1
check "alternating bad manifests: the clock is not reset by the 2nd manifest" \
  test "$(cat "$vm/etc/download-failing-since")" = "$first_clock"

# A clock exactly 1 hour old trips the limit on this tick (>=, not >).
echo $(($(date -u +%s) - 3600)) >"$vm/etc/download-failing-since"
run_sync >"$root/log.34b" 2>&1
check "clock exactly 1 hour old: the limit trips" test -f "$vm/etc/manifest.failed"
# A new manifest after the trip starts its own clock instead of tripping at once.
seed_aws_store v4
block_key "$(jq -r .prober_key "$store/manifest.json")"
run_sync >"$root/log.34c" 2>&1
check "new manifest after a trip: its own clock starts" test -f "$vm/etc/download-failing-since"
check "new manifest after a trip: no immediate trip" bash -c '! grep -q download_failed_repeatedly "$1"' _ "$root/log.34c"

# An unreadable or a future clock restarts at now.
for bad_clock in "garbage" "$(( $(date -u +%s) + 99999 ))"; do
  fresh_vm aws
  rm -rf "$store" "$block" && mkdir -p "$store" "$block"
  seed_aws_store v1
  run_sync >/dev/null 2>&1
  seed_aws_store v2
  block_key "$(jq -r .prober_key "$store/manifest.json")"
  echo "$bad_clock" >"$vm/etc/download-failing-since"
  run_sync >"$root/log.34d" 2>&1
  now_s=$(date -u +%s)
  check "clock '$bad_clock': logged and restarted at now" bash -c 'grep -q "unreadable or in the future" "$1" && [ $(( $2 - $(cat "$3") )) -le 5 ]' _ "$root/log.34d" "$now_s" "$vm/etc/download-failing-since"
  check "clock '$bad_clock': no trip" test ! -f "$vm/etc/manifest.failed"
done

# First boot whose download keeps failing for 1 hour: unit fails, manifest.failed, no release dir.
fresh_vm aws
rm -rf "$store" "$block" && mkdir -p "$store" "$block"
seed_aws_store v1
block_key "$(jq -r .prober_key "$store/manifest.json")"
run_sync >/dev/null 2>&1
check "first boot download failing: under the limit the tick retries (exit 0)" test -f "$vm/etc/download-failing-since"
echo $(($(date -u +%s) - 3601)) >"$vm/etc/download-failing-since"
run_sync >"$root/log.34e" 2>&1
check "first boot download failing 1 hour: exit nonzero" test "$?" -ne 0
check "first boot download failing 1 hour: manifest.failed written" test -f "$vm/etc/manifest.failed"
check "first boot download failing 1 hour: empty release directory removed" release_count_is "$vm" 0
check "first boot download failing 1 hour: no current" test ! -e "$vm/current"

# A manifest fetch error is logged and retried, never written to manifest.failed.
fresh_vm aws
rm -rf "$store" "$block" && mkdir -p "$store" "$block"
seed_aws_store v1
run_sync >/dev/null 2>&1
before=$(current_release_id "$vm")
block_key manifest.json
run_sync >"$root/log.34f" 2>&1
check "aws manifest fetch error: exit 0" test "$?" -eq 0
check "aws manifest fetch error: no manifest.failed" test ! -f "$vm/etc/manifest.failed"
check "aws manifest fetch error: current unchanged" same_release "$vm" "$before"
fresh_vm azure
rm -rf "$store" "$block" && mkdir -p "$store" "$block"
seed_azure_store v1
run_sync >/dev/null 2>&1
before=$(current_release_id "$vm")
block_key manifest.json # the fake fetch-blob.sh exits 1 for a blocked blob: not BlobNotFound
run_sync >"$root/log.34g" 2>&1
check "azure manifest fetch error (not BlobNotFound): exit 0, no manifest.failed" \
  bash -c 'test "$1" -eq 0 && test ! -f "$2"' _ "$?" "$vm/etc/manifest.failed"
check "azure manifest fetch error: logged as a failure" grep -q "manifest fetch failed" "$root/log.34g"

# rollback.failed stays failed even when the tick's own manifest fetch fails.
fresh_vm aws
rm -rf "$store" "$block" && mkdir -p "$store" "$block"
seed_aws_store v1
run_sync >/dev/null 2>&1
seed_aws_store v2
FAKE_SYSTEMCTL_ACTIVE_SEQUENCE="inactive inactive" run_sync >/dev/null 2>&1
block_key manifest.json
run_sync >"$root/log.34h" 2>&1
check "rollback.failed with a failed manifest fetch: still exits nonzero" test "$?" -ne 0

# --- 35. ADR 0012 tests: reuse and recheck, interrupted syncs, and enabling the service --------

fresh_vm aws
rm -rf "$store" "$block" && mkdir -p "$store" "$block"
seed_aws_store v1
run_sync >/dev/null 2>&1
v1_id=$(current_release_id "$vm")
v1_manifest=$(cat "$store/manifest.json")
seed_aws_store v2
run_sync >/dev/null 2>&1
echo corrupted >"$vm/releases/$v1_id/payload-extra.txt" # changes the combined hash of v1
echo "$v1_manifest" >"$store/manifest.json"
run_sync >"$root/log.35a" 2>&1
check "revert with a failed .sha256 recheck: logged and rebuilt" grep -q "failed its .sha256 recheck; rebuilding" "$root/log.35a"
check "revert with a failed .sha256 recheck: current back on v1" same_release "$vm" "$v1_id"
check "revert with a failed .sha256 recheck: the corrupt file is gone" test ! -f "$vm/releases/$v1_id/payload-extra.txt"

# An interrupted sync that lands on the manifest current already names: no swap, previous-release
# untouched, and the restart is still made and health-checked.
fresh_vm aws
rm -rf "$store" "$block" && mkdir -p "$store" "$block"
seed_aws_store v1
run_sync >/dev/null 2>&1
v1_id=$(current_release_id "$vm")
seed_aws_store v2
run_sync >/dev/null 2>&1
v2_id=$(current_release_id "$vm")
rm -f "$vm/etc/manifest.json" # killed after the swap, before the cache was written
run_sync >"$root/log.35b" 2>&1
check "interrupted sync on the current release: exit 0" test "$?" -eq 0
check "interrupted sync on the current release: current unchanged" same_release "$vm" "$v2_id"
check "interrupted sync on the current release: previous-release untouched" previous_release_is "$vm" "$v1_id"
check "interrupted sync on the current release: the restart still ran" test -f "$state/invocation_n"
check "interrupted sync on the current release: manifest cached again" test -f "$vm/etc/manifest.json"

# A health failure on a release previous-release already names skips the rollback restart.
rm -f "$vm/etc/manifest.json"
echo "$v2_id" >"$vm/etc/previous-release"
FAKE_SYSTEMCTL_ACTIVE_SEQUENCE=inactive run_sync >"$root/log.35c" 2>&1
check "health failure on the already-current release: exit nonzero" test "$?" -ne 0
check "health failure on the already-current release: rollback.failed written" test -f "$vm/etc/rollback.failed"
check "health failure on the already-current release: only 1 restart (no rollback restart)" \
  test "$(cat "$state/invocation_n")" = 1
check "health failure on the already-current release: the release directory survives" test -d "$vm/releases/$v2_id"

# ftg-prober.service is enabled only after a build passes its gate.
fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
FAKE_GATE_EXIT=2 run_sync >/dev/null 2>&1
check "service not enabled when the first build fails its gate" bash -c '! grep -qx ftg-prober.service "$1/enabled" 2>/dev/null' _ "$state"
fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
run_sync >/dev/null 2>&1
check "service enabled after the first successful sync" grep -qx ftg-prober.service "$state/enabled"

# --- 36. The stop timeout drop-in ------------------------------------------------

# stop_timeout: the heredoc of sync.sh, run by the real python3 against a stub config loader.
stub2="$root/stub-ftg-stop"
mkdir -p "$stub2/ftg_prober"
: >"$stub2/ftg_prober/__init__.py"
printf 'import json, os\nclass C:\n    def section(self, p):\n        return json.loads(os.environ["STUB_SECTION"])\ndef load_config(path):\n    return C()\n' >"$stub2/ftg_prober/config.py"
stop_py="$root/stop_timeout.py"
awk '/<<.PYEOF./{n++; next} /^PYEOF/{if (n == 2) exit} n == 2' "$sync_sh" >"$stop_py"
stop_for() { STUB_SECTION="$1" PYTHONPATH="$stub2" "$real_python" "$stop_py" cfg.yaml aws 180 30; }
check "stop timeout: small timeouts stay at the 180 s floor" test "$(stop_for '{"probe_timeout_seconds": 10, "teardown_timeout_seconds": 10}')" = 180
check "stop timeout: the defaults (90 + 2 x 60 + 30) give 240" test "$(stop_for '{}')" = 240
check "stop timeout: large timeouts give probe + 2 x teardown + 30" test "$(stop_for '{"probe_timeout_seconds": 300, "teardown_timeout_seconds": 200}')" = 730

restart_dropin_first() { grep "^restart " "$state/calls" | head -1 | sed -n 's/.*TimeoutStopSec=\([0-9]*\).*/\1/p'; }
dropin_is() { [ "$(cat "$root/dropin/stop-timeout.conf" 2>/dev/null)" = "$(printf '[Service]\nTimeoutStopSec=%s' "$1")" ]; }

fresh_vm aws
rm -rf "$store" "$root/dropin" && mkdir -p "$store"
seed_aws_store v1
FAKE_STOP_TIMEOUT=530 run_sync >"$root/log.36a" 2>&1
check "drop-in: written with the computed value on an install" dropin_is 530
check "drop-in: daemon-reload runs before the restart, and sees the drop-in" \
  bash -c 'r=$(grep -n "^daemon-reload dropin=.*TimeoutStopSec=530" "$1/calls" | head -1 | cut -d: -f1); s=$(grep -n "^restart " "$1/calls" | head -1 | cut -d: -f1); [ -n "$r" ] && [ -n "$s" ] && [ "$r" -lt "$s" ]' _ "$state"
seed_aws_store v2
FAKE_STOP_TIMEOUT=180 run_sync >"$root/log.36b" 2>&1
check "drop-in: rewritten with the new value on an update" dropin_is 180
fresh_vm aws
rm -rf "$store" "$root/dropin" && mkdir -p "$store"
seed_aws_store v1
FAKE_STOP_TIMEOUT=abc run_sync >"$root/log.36c" 2>&1
check "drop-in: a non-numeric value fails closed, no drop-in written" bash -c 'test "$1" -ne 0 -a ! -e "$2/dropin/stop-timeout.conf"' _ "$?" "$root"
check "drop-in: a non-numeric value logs sync.stop_timeout_invalid" grep -q "sync.stop_timeout_invalid" "$root/log.36c"
fresh_vm aws
rm -rf "$store" "$root/dropin" && mkdir -p "$store"
seed_aws_store v1
FAKE_DAEMON_RELOAD_FAIL=1 run_sync >"$root/log.36d" 2>&1
check "drop-in: a failed daemon-reload fails the install and no restart is made" bash -c '! grep -q "^restart " "$1/calls"' _ "$state"

# the second write (after the restart) fails; the healthy release stays.
fresh_vm aws
rm -rf "$store" "$root/dropin" && mkdir -p "$store"
seed_aws_store v1
run_sync >"$root/log.36e0" 2>&1
# v1 is installed, so a rollback to it is possible. The fake counts daemon-reload calls
# over the whole VM: the v1 install made calls 1 and 2, the compaction units' reload is call 3, so the update's second reload is call 5.
seed_aws_store v2
FAKE_DAEMON_RELOAD_FAIL_NTH=5 run_sync >"$root/log.36e" 2>&1
rc36e=$?
check "drop-in: a failed second daemon-reload does not fail the install" test "$rc36e" -eq 0
check "drop-in: a failed second daemon-reload logs sync.stop_timeout_write_failed" grep -q "sync.stop_timeout_write_failed" "$root/log.36e"
check "drop-in: a failed second daemon-reload rolls nothing back" bash -c '! grep -q "sync.rollback" "$1"' _ "$root/log.36e"
check "drop-in: a failed second daemon-reload leaves the updated release current" different_release "$vm" "$(cat "$vm/etc/previous-release")"
# a hand-edited drop-in value with a leading zero is read as decimal.
fresh_vm aws
rm -rf "$store" "$root/dropin" && mkdir -p "$store" "$root/dropin"
printf '[Service]\nTimeoutStopSec=0900\n' >"$root/dropin/stop-timeout.conf"
seed_aws_store v1
FAKE_STOP_TIMEOUT=530 run_sync >"$root/log.36f" 2>&1
check "drop-in: a leading-zero value (0900) does not abort the install" test "$?" -eq 0
check "drop-in: a leading-zero value (0900) counts as 900 for the stop" test "$(restart_dropin_first)" = 900
check "fake systemctl daemon-reload: silent on stdout, exit 0" bash -c 'out=$("$1/systemctl" daemon-reload); [ -z "$out" ]' _ "$fakebin"
check "fake systemctl daemon-reload: exit 1 on failure" bash -c '! FAKE_DAEMON_RELOAD_FAIL=1 "$1/systemctl" daemon-reload 2>/dev/null' _ "$fakebin"

# --- 37. The old prober's stop uses the larger timeout; a rollback restores the old value ---

# restart_dropin(n): the drop-in as the nth restart saw it (the stop of the old prober happens
# inside that restart). The fake systemctl logs the drop-in content on every call.
restart_dropin() { grep "^restart " "$state/calls" | sed -n "${1}p" | sed -n 's/.*TimeoutStopSec=\([0-9]*\).*/\1/p'; }
installed_then_updated() {
  # $1 = timeouts by tag; installs v1, then updates to v2 with them.
  fresh_vm aws
  rm -rf "$store" "$root/dropin" && mkdir -p "$store"
  seed_aws_store v1
  FAKE_STOP_TIMEOUT_BY_TAG="$1" run_sync >"$root/log.37a" 2>&1
  seed_aws_store v2
  rm -f "$state/calls"
  FAKE_STOP_TIMEOUT_BY_TAG="$1" "${@:2}" run_sync >"$root/log.37b" 2>&1
}
installed_then_updated "v1=630 v2=180"
check "drop-in, large to small: the old prober is stopped with the old (larger) timeout" test "$(restart_dropin 1)" = 630
check "drop-in, large to small: the new value is written after the new prober starts" dropin_is 180
installed_then_updated "v1=180 v2=630"
check "drop-in, small to large: the stop uses the new (larger) timeout" test "$(restart_dropin 1)" = 630
check "drop-in, small to large: the drop-in holds the new value after" dropin_is 630
check "drop-in: every write is followed by a daemon-reload" \
  bash -c 'test "$(grep -c "^daemon-reload" "$1/calls")" -eq 2' _ "$state"

# A rollback: the failing new prober (900) is stopped with 900, then the drop-in is the
# previous release's value (400) again.
fresh_vm aws
rm -rf "$store" "$root/dropin" && mkdir -p "$store"
seed_aws_store v1
FAKE_STOP_TIMEOUT_BY_TAG="v1=400 v2=900" run_sync >"$root/log.37c" 2>&1
good_id=$(current_release_id "$vm")
seed_aws_store v2
rm -f "$state/calls"
FAKE_STOP_TIMEOUT_BY_TAG="v1=400 v2=900" FAKE_SYSTEMCTL_ACTIVE_SEQUENCE="inactive active" run_sync >"$root/log.37d" 2>&1
check "drop-in, rollback: it rolled back to the previous release" same_release "$vm" "$good_id"
check "drop-in, rollback: the failing new prober is stopped with its own 900 s" test "$(restart_dropin 2)" = 900
check "drop-in, rollback: the drop-in is rewritten from the previous release" dropin_is 400

# --- 38. A trial copy that a signal ends is reverted --------------------------------

# A stop is signal with EXIT_STATUS=TERM (section 33); these outcomes are not a stop (systemd.exec,
# $SERVICE_RESULT): a SIGKILL, SEGV or ABRT, a core dump, the OOM killer, the watchdog.
for kill in signal:KILL signal:SEGV signal:ABRT core-dump:ABRT oom-kill:KILL watchdog:ABRT; do
  kill_result=${kill%%:*} kill_status=${kill##*:}
  fresh_vm aws
  install_fake_sync $'#!/usr/bin/env bash\necho trial copy\n'
  printf '#!/usr/bin/env bash\necho good prev\n' >"$fakebin/sync.sh.prev"
  echo killhash >"$vm/etc/sync.trial"
  UNIT_STOP=1 UNIT_STOP_RESULT=$kill_result UNIT_EXIT_STATUS=$kill_status run_unit >"$root/log.38" 2>&1
  check "trial killed ($kill_result/$kill_status): the unit fails" test "$?" -ne 0
  check "trial killed ($kill_result/$kill_status): reverted event" grep -q "sync.self_update_reverted" "$root/log.38"
  check "trial killed ($kill_result/$kill_status): sync.sh is the previous copy again" grep -q "good prev" "$fakebin/sync.sh"
  check "trial killed ($kill_result/$kill_status): hash blacklisted" grep -qx killhash "$vm/etc/sync.failed"
done

# --- 39. The health check reads only the journal of its own invocation --------------

check "fake journalctl: the invocation filter keeps only that invocation" \
  bash -c 'printf "invA run.start\ninvB probe.done\n" >"$1/journal"; out=$(FAKE_SYSTEMCTL_STATE="$1" "$2/journalctl" _SYSTEMD_INVOCATION_ID=invA); [ "$out" = run.start ]' _ "$state" "$fakebin"
fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
: >"$state/journal_template" # the new prober logs nothing
printf 'inv0 run.start\ninv0 probe.done\ninv9 run.start\n' >"$state/journal" # other invocations did
FAKE_HEALTH_WINDOW="2 exact" run_sync >"$root/log.39" 2>&1
check "health check: lines of another invocation do not count (fails)" test "$?" -ne 0
check "health check: lines of another invocation do not count (logged)" grep -q "no run.start for invocation inv1" "$root/log.39"
check "health check: lines of another invocation do not count (no current)" test ! -e "$vm/current"


# --- 40. The compaction units ------------------------------------------------

units="$root/units"
compact_calls() { grep -c "ftg-compact" "$state/calls" 2>/dev/null; }

fresh_vm aws
seed_aws_store v1
run_sync >"$root/log.40a" 2>&1
check "compact units: the sync installs the prober and exits 0" test "$?" -eq 0
check "compact units: service text (provider, config, store URL, user)" bash -c \
  'grep -qx "ExecStart=/opt/ftg/current/venv/bin/ftg-prober compact --provider aws --config /opt/ftg/current/config/targets.yaml --store s3://ftg-data-test/" "$1/ftg-compact.service" && grep -qx "User=ftg" "$1/ftg-compact.service" && grep -qx "Type=oneshot" "$1/ftg-compact.service"' _ "$units"
check "compact units: service has Nice, MemoryMax, and the long start timeout" bash -c \
  'grep -qx "Nice=10" "$1/ftg-compact.service" && grep -qx "MemoryMax=384M" "$1/ftg-compact.service" && grep -qx "TimeoutStartSec=3h" "$1/ftg-compact.service"' _ "$units"
check "compact units: timer text (4 runs a day, Persistent=true)" bash -c \
  'grep -Fqx "OnCalendar=*-*-* 00/6:30:00 UTC" "$1/ftg-compact.timer" && grep -qx "Persistent=true" "$1/ftg-compact.timer" && grep -qx "WantedBy=timers.target" "$1/ftg-compact.timer"' _ "$units"
check "compact units: the timer is enabled and started" grep -q "^enable --now ftg-compact.timer" "$state/calls"
check "compact units: the unit reload comes before the timer enable" bash -c \
  'r=$(grep -n "^daemon-reload" "$1/calls" | head -1 | cut -d: -f1); e=$(grep -n "^enable --now ftg-compact.timer" "$1/calls" | cut -d: -f1); [ "$r" -lt "$e" ]' _ "$state"
check "compact units: the prober service is enabled and healthy as before" grep -qx ftg-prober.service "$state/enabled"
check "compact units: the prober service is not named by a compact command" bash -c '! grep -E "^(stop|restart|start|enable|disable|kill|reset-failed).*ftg-prober.service" "$1/calls" | grep -q compact' _ "$state"

# A second tick with the same text changes nothing: no rewrite, no reload, no enable.
before_units=$(cat "$units"/ftg-compact.*)
rm -f "$state/calls"
run_sync >"$root/log.40b" 2>&1
check "compact units: an unchanged tick writes no unit" test "$(cat "$units"/ftg-compact.*)" = "$before_units"
check "compact units: an unchanged tick makes no reload and no enable" bash -c '! grep -qE "^(daemon-reload|enable)" "$1/calls" 2>/dev/null' _ "$state"
check "compact units: no temp file is left in the unit folder" bash -c 'test -z "$(ls -A "$1" | grep "^\.")"' _ "$units"

# A changed text is rewritten and reloaded.
echo "# stale" >>"$units/ftg-compact.service"
rm -f "$state/calls"
run_sync >"$root/log.40c" 2>&1
check "compact units: a changed service text is replaced" bash -c '! grep -q stale "$1/ftg-compact.service"' _ "$units"
check "compact units: a changed text causes a reload" grep -q "^daemon-reload" "$state/calls"
check "compact units: a changed text with the timer enabled does not enable again" bash -c '! grep -q "^enable" "$1/calls"' _ "$state"

# A proxy drop-in of the prober is copied; a removed one is removed.
mkdir -p "$FTG_SYNC_DROPIN_DIR"
printf '[Service]\nEnvironment="http_proxy=http://p:3128"\n' >"$FTG_SYNC_DROPIN_DIR/proxy.conf"
run_sync >"$root/log.40d" 2>&1
check "compact units: the prober's proxy drop-in is copied" cmp -s "$FTG_SYNC_DROPIN_DIR/proxy.conf" "$units/ftg-compact.service.d/proxy.conf"
rm -f "$FTG_SYNC_DROPIN_DIR/proxy.conf"
run_sync >"$root/log.40e" 2>&1
check "compact units: a removed proxy drop-in is removed" test ! -e "$units/ftg-compact.service.d/proxy.conf"

# A failure to install the units must not stop, restart, or roll back the prober.
fresh_vm aws
seed_aws_store v1
run_sync >"$root/log.40f" 2>&1
release_before=$(current_release_id "$vm")
rm -rf "$units" && : >"$units" # a file where the folder should be: mkdir fails
seed_aws_store v2
rm -f "$state/calls"
run_sync >"$root/log.40g" 2>&1
check "compact units: a unit folder that cannot be written does not fail the sync" test "$?" -eq 0
check "compact units: ... and is logged" grep -q "sync.compact_units_failed reason=cannot_create_unit_dir" "$root/log.40g"
check "compact units: ... and the new release still installs" different_release "$vm" "$release_before"
rm -f "$units"

fresh_vm aws
seed_aws_store v1
run_sync >"$root/log.40h0" 2>&1
reloads=$(grep -c '^daemon-reload' "$state/calls")
fresh_vm aws
seed_aws_store v1
FAKE_DAEMON_RELOAD_FAIL_NTH=$reloads run_sync >"$root/log.40h" 2>&1
check "compact units: a failing systemctl daemon-reload does not change the exit code" test "$?" -eq 0
check "compact units: a failing reload logs an event" grep -q "sync.compact_units_failed reason=daemon_reload" "$root/log.40h"
check "compact units: a failing reload leaves the new release current" test -n "$(current_release_id "$vm")"
check "compact units: a failing reload does not stop or restart the prober beyond the install" bash -c '[ "$(grep -c "^restart " "$1/calls")" = 1 ] && ! grep -q "^stop " "$1/calls"' _ "$state"
check "compact units: a failing reload leaves the units written for the next tick" test -f "$units/ftg-compact.timer"

# the OS upgrade timers are disabled and stopped, once, with no prober restart.
fresh_vm aws
seed_aws_store v1
printf 'apt-daily-upgrade.timer\napt-daily.timer\n' >"$state/enabled"
run_sync >"$root/log.40os" 2>&1
check "os upgrade timers: both are disabled with disable --now" grep -q "^disable --now apt-daily-upgrade.timer apt-daily.timer " "$state/calls"
check "os upgrade timers: ... and are no longer enabled" bash -c '! grep -q "^apt-daily" "$1/enabled"' _ "$state"
check "os upgrade timers: ... and the event is logged" grep -q "sync.os_upgrade_timers_disabled" "$root/log.40os"
rm -f "$state/calls"
run_sync >"$root/log.40os2" 2>&1
check "os upgrade timers: a later tick does not disable again" bash -c '! grep -q "^disable" "$1/calls"' _ "$state"
fresh_vm aws
seed_aws_store v1
printf 'apt-daily-upgrade.timer\n' >"$state/enabled"
FAKE_DISABLE_FAIL=1 run_sync >"$root/log.40os3" 2>&1
check "os upgrade timers: a failing disable keeps the release" test -n "$(current_release_id "$vm")"
check "os upgrade timers: a failing disable is logged" grep -q "sync.os_upgrade_timers_failed reason=disable" "$root/log.40os3"
check "os upgrade timers: the prober restarts once, from the install only" bash -c '[ "$(grep -c "^restart " "$1/calls")" = 1 ]' _ "$state"

# The units come after the release outcome: after the prober restart, and not at all while a
# self-updated sync.sh is on trial.
fresh_vm aws
seed_aws_store v1
run_sync >"$root/log.40o" 2>&1
check "compact units: installed after the prober restart" bash -c 'r=$(grep -n "^restart " "$1/calls" | tail -1 | cut -d: -f1); e=$(grep -n "^enable --now ftg-compact.timer" "$1/calls" | cut -d: -f1); [ "$r" -lt "$e" ]' _ "$state"
fresh_vm aws
rm -rf "$store" && mkdir -p "$store"
seed_aws_store v1
install_fake_sync $'#!/usr/bin/env bash\necho old\n'
publish_sync "$GOOD_SYNC" >/dev/null
run_sync >"$root/log.40p" 2>&1
check "compact units: not installed on the tick that self-updates sync.sh (trial open)" test ! -e "$units/ftg-compact.service"
run_sync >"$root/log.40p2" 2>&1
check "compact units: installed on the next tick, once the trial is proven" test -f "$units/ftg-compact.service"

# A failed release (the gate rejects the build) still exits non-zero, and the units are still
# installed without changing that code.
fresh_vm aws
seed_aws_store v1
FAKE_GATE_EXIT=1 run_sync >"$root/log.40q" 2>&1
rc_gate=$?
check "compact units: a failed release keeps its non-zero exit code" test "$rc_gate" -ne 0
check "compact units: a failed first release does not enable the prober service" test ! -f "$state/enabled" -o -z "$(grep -x ftg-prober.service "$state/enabled" 2>/dev/null)"

fresh_vm aws
seed_aws_store v1
FAKE_ENABLE_FAIL=1 run_sync >"$root/log.40i" 2>&1
check "compact units: a failed enable does not fail the sync" test "$?" -eq 0
check "compact units: a failed enable is logged" grep -q "sync.compact_units_failed reason=enable_timer" "$root/log.40i"
rm -f "$state/enabled.compact" "$state/calls"
: >"$state/enabled" ; echo ftg-prober.service >"$state/enabled"
run_sync >"$root/log.40j" 2>&1
check "compact units: the next tick enables the timer again" grep -q "^enable --now ftg-compact.timer" "$state/calls"

# The units are installed when the manifest matches the running release (the early exit).
fresh_vm aws
seed_aws_store v1
run_sync >"$root/log.40k" 2>&1
rm -rf "$units"
run_sync >"$root/log.40l" 2>&1
check "compact units: a tick that finds nothing new still installs missing units" test -f "$units/ftg-compact.service"

# --- 41. The Azure compaction units ------------------------------------

# An Azure store whose manifest carries the store_url field, as the Terraform manifest does.
seed_azure_store_url() {
  seed_azure_store "$1"
  jq --arg u "${2-az://acctdata/data/}" '. + {store_url: $u}' "$store/manifest.json" >"$store/m.tmp" \
    && mv "$store/m.tmp" "$store/manifest.json"
}
svc="$root/units/ftg-compact.service"
tmr="$root/units/ftg-compact.timer"
calls_of() { grep -c "^$1" "$state/calls"; }

fresh_vm azure
rm -rf "$store" "$root/units" && mkdir -p "$store"
seed_azure_store_url v1
run_sync >"$root/log.41a" 2>&1
check "compact units: a healthy install exits 0 and installs the release" test -n "$(current_release_id "$vm")"
check "compact units: the service runs ftg-prober compact for azure with the manifest's store_url" \
  grep -qx "ExecStart=/opt/ftg/current/venv/bin/ftg-prober compact --provider azure --config /opt/ftg/current/config/targets.yaml --store az://acctdata/data/" "$svc"
check "compact units: the service is a oneshot with DynamicUser, Nice, and MemoryMax" \
  bash -c 'for l in Type=oneshot DynamicUser=yes Nice=10 MemoryMax=384M EnvironmentFile=/etc/ftg/env; do grep -qxF "$l" "$1" || exit 1; done' _ "$svc"
check "compact units: the timer is Persistent and runs at 00:30, 06:30, 12:30, 18:30 UTC" \
  bash -c 'grep -qxF "OnCalendar=*-*-* 00/6:30:00 UTC" "$1" && grep -qxF "Persistent=true" "$1"' _ "$tmr"
check "compact units: the timer is enabled with --now, after a daemon-reload" \
  bash -c 'grep -qx ftg-compact.timer "$1/enabled" && grep -q "^enable --now ftg-compact.timer" "$1/calls"' _ "$state"
check "compact units: the prober was enabled and restarted once, and the compaction service never" \
  bash -c '[ "$(grep -c "^restart " "$1/calls")" -eq 1 ] && ! grep -q "ftg-compact.service" "$1/calls"' _ "$state"

# A tick with an unchanged manifest and unchanged units writes nothing and reloads nothing more.
reloads=$(calls_of daemon-reload)
run_sync >"$root/log.41b" 2>&1
check "compact units: a no-op tick does not reload or enable again" \
  test "$(calls_of daemon-reload)" -eq "$reloads" -a "$(grep -c '^enable --now ftg-compact.timer' "$state/calls")" -eq 1

# A unit whose text differs from the file on the VM is written again, with a reload.
echo "tampered" >"$svc"
run_sync >"$root/log.41c" 2>&1
check "compact units: a changed unit file is rewritten and systemd reloads" \
  bash -c 'grep -q "^Type=oneshot" "$1" && [ "$(grep -c "^daemon-reload" "$2/calls")" -gt "$3" ]' _ "$svc" "$state" "$reloads"
check "compact units: the prober was not restarted for a unit rewrite" test "$(calls_of 'restart ')" -eq 1

# The proxy drop-in of the prober is copied to the compaction service.
mkdir -p "$root/dropin"
printf '[Service]\nEnvironment="HTTPS_PROXY=http://proxy.example:3128"\n' >"$root/dropin/proxy.conf"
run_sync >"$root/log.41d" 2>&1
check "compact units: the proxy drop-in of the prober is copied" \
  cmp -s "$root/dropin/proxy.conf" "$root/units/ftg-compact.service.d/proxy.conf"

# A failure to install the units does not stop, restart, or roll back the prober.
fresh_vm azure
rm -rf "$store" "$root/units" && mkdir -p "$store"
seed_azure_store_url v1
FAKE_ENABLE_FAIL=1 run_sync >"$root/log.41e" 2>&1
rc=$?
check "compact units: a failed enable of the timer exits 0" test "$rc" -eq 0
check "compact units: a failed enable of the timer logs sync.compact_units_failed" grep -q "sync.compact_units_failed reason=enable_timer" "$root/log.41e"
check "compact units: a failed enable leaves the release installed and the prober running" \
  bash -c 'test -n "$(basename "$(readlink "$1/current")")" && ! grep -q "^stop" "$2/calls" && [ "$(grep -c "^restart " "$2/calls")" -eq 1 ]' _ "$vm" "$state"
check "compact units: no rollback after a failed unit install" test ! -e "$vm/etc/rollback.failed" -a ! -e "$vm/etc/manifest.failed"
fresh_vm azure
rm -rf "$store" "$root/units" && mkdir -p "$store"
seed_azure_store_url v1
mkdir -p "$root/units" && chmod 500 "$root/units"
run_sync >"$root/log.41f" 2>&1
rc=$?
chmod 700 "$root/units"
check "compact units: an unwritable unit directory exits 0, release installed" \
  bash -c 'test "$1" -eq 0 -a -L "$2/current"' _ "$rc" "$vm"
check "compact units: an unwritable unit directory logs sync.compact_units_failed" grep -q "sync.compact_units_failed" "$root/log.41f"

# No store_url (a manifest from before this change), a bad store_url, and AWS: no units.
fresh_vm azure
rm -rf "$store" "$root/units" && mkdir -p "$store"
seed_azure_store v1
run_sync >"$root/log.41g" 2>&1
check "compact units: a manifest with no store_url installs no units and logs the skip" \
  bash -c 'test ! -e "$1" && grep -q "sync.compact_units_skipped" "$2"' _ "$svc" "$root/log.41g"
fresh_vm azure
rm -rf "$store" "$root/units" && mkdir -p "$store"
seed_azure_store_url v1 'az://acct/data/ --evil'
run_sync >"$root/log.41h" 2>&1
check "compact units: a store_url with a space is refused" \
  bash -c 'test ! -e "$1" && grep -q "sync.compact_units_failed reason=bad_store_url" "$2"' _ "$svc" "$root/log.41h"
# A failing daemon-reload (the 3rd call: the prober's 2 drop-in reloads come first), a null and an
# empty store_url: the tick still ends 0 with the release installed.
fresh_vm azure
rm -rf "$store" "$root/units" && mkdir -p "$store"
seed_azure_store_url v1
FAKE_DAEMON_RELOAD_FAIL_NTH=3 run_sync >"$root/log.41j" 2>&1
rc=$?
check "compact units: a failing daemon-reload exits 0, logs the event, keeps the release" \
  bash -c 'test "$1" -eq 0 && grep -q "sync.compact_units_failed reason=daemon_reload" "$2" && grep -q "sync.release_installed" "$2" && [ "$(grep -c "^restart " "$3/calls")" -eq 1 ]' _ "$rc" "$root/log.41j" "$state"
for bad in null ""; do
  fresh_vm azure
  rm -rf "$store" "$root/units" && mkdir -p "$store"
  seed_azure_store v1
  jq --argjson u "$([ "$bad" = null ] && echo null || echo '""')" '. + {store_url: $u}' "$store/manifest.json" >"$store/m.tmp" && mv "$store/m.tmp" "$store/manifest.json"
  run_sync >"$root/log.41k" 2>&1
  check "compact units: a ${bad:-empty} store_url installs no units, exit 0" \
    bash -c 'test "$1" -eq 0 && test ! -e "$2" && grep -q "sync.compact_units_skipped" "$3"' _ "$?" "$svc" "$root/log.41k"
done

echo
echo "sync.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
