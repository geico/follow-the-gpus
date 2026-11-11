#!/usr/bin/env bash
# /opt/ftg/bin/sync.sh -- the VM-side pull and restart (ADR 0012). Runs as root, from
# ftg-prober-sync.service, on ftg-prober-sync.timer and once at first boot. 1 code path for the
# first install and every later update (ADR 0012 section 4, rule 11). Identical on both clouds;
# the few cloud differences (how to fetch an object, which pip extra, which manifest field
# names) come from /etc/ftg/sync-env, not from this file.
#
# State on disk:
#   /opt/ftg/releases/<release ID>/        a built release: config/, venv/, .sha256, .complete
#   /opt/ftg/current                       symlink to the release the service runs
#   /etc/ftg/manifest.json                 the manifest of the release /opt/ftg/current runs
#   /etc/ftg/previous-release              release ID to roll back to
#   /etc/ftg/download-failing-since        Unix epoch (seconds) of the first unbroken failure
#   /etc/ftg/manifest.failed               content of a manifest this script refuses to retry
#   /etc/ftg/rollback.failed               content of a manifest whose rollback also failed
#   /etc/ftg/sync.trial                    sha256 of a self-updated sync.sh not yet proven to run;
#                                          ftg-prober-sync.service reverts to sync.sh.prev if the
#                                          unit fails (or a run exits 0 without removing this
#                                          file) while this is present (spec 0003 section 6A)
#   /etc/ftg/sync.failed                   sha256 of each sync.sh this script refuses to install
#   /opt/ftg/bin/sync.sh.prev              the sync.sh before the last self-update
#
# set -u only: 1 failed step (for example a failed fetch) must not kill the rest of the tick
# under -e; each step that can fail checks its own exit status instead.
set -u

# Overridable only for the test suite (terraform/tests/sync.test.sh), which runs as a normal
# user against a throwaway root, never as a VM-side default: production never sets these.
RELEASES=${FTG_SYNC_RELEASES:-/opt/ftg/releases}
CURRENT=${FTG_SYNC_CURRENT:-/opt/ftg/current}
ETC=${FTG_SYNC_ETC:-/etc/ftg}
BIN=${FTG_SYNC_BIN:-/opt/ftg/bin}
SERVICE=ftg-prober.service
DOWNLOAD_FAIL_LIMIT_S=3600
HEALTH_MARGIN_S=60
HEALTH_CAP_S=900
HEALTH_POLL_S=${FTG_SYNC_HEALTH_POLL_S:-5}
STOP_TIMEOUT_FLOOR_S=180
STOP_TIMEOUT_MARGIN_S=30
DROPIN_DIR=${FTG_SYNC_DROPIN_DIR:-/etc/systemd/system/ftg-prober.service.d}
UNIT_DIR=${FTG_SYNC_UNIT_DIR:-/etc/systemd/system}

# set -a: every plain NAME=value assignment sync-env makes (including AWS_DEFAULT_REGION, which
# the AWS CLI itself reads from its environment, not from a flag this script passes) is
# exported, so it reaches `aws` and every other child process, not just this shell.
set -a
# shellcheck source=/dev/null
. "$ETC/sync-env"
set +a
PROVIDER=${FTG_PROVIDER:?FTG_PROVIDER is not set in /etc/ftg/sync-env}
CONFIG_FILE=${FTG_CONFIG_FILE:?FTG_CONFIG_FILE is not set in /etc/ftg/sync-env}
PIP_EXTRA=${FTG_PIP_EXTRA:?FTG_PIP_EXTRA is not set in /etc/ftg/sync-env}
FTG_BUCKET=${FTG_BUCKET:-}

mkdir -p "$RELEASES" "$ETC"

now_utc() { date -u +%FT%TZ; }
log() { echo "$(now_utc) sync.sh: $*"; }
event() {
  # event(name, k=v ...): 1 line in the journal an operator (section 6) can grep for.
  local name=$1
  shift
  echo "$(now_utc) sync.sh: $name $*"
}

# fetch_object(name, out): fetches 1 object to $out. Echoes "ok" or "error". On "error", also
# writes the command's own stderr, trimmed to 1 line, to $RELEASES/.last-fetch-error, for
# sync.download_failed_repeatedly (rule 3) to report "the last download error as returned".
# A file, not a variable: callers read this result with `result=$(fetch_object ...)`, a command
# substitution, which runs fetch_object in a subshell -- any plain variable it set there would be
# lost the moment that subshell exits.
LAST_FETCH_ERROR_FILE=$RELEASES/.last-fetch-error
fetch_object() {
  local name=$1 out=$2
  if [ "$PROVIDER" = aws ]; then
    if aws s3api get-object --bucket "$FTG_BUCKET" --key "$name" "$out" >/dev/null 2>"$out.err"; then
      echo ok
    else
      cat "$out.err" >&2
      tr '\n' ' ' <"$out.err" >"$LAST_FETCH_ERROR_FILE"
      echo error
    fi
  else
    if "$BIN/fetch-blob.sh" "$name" "$out" >/dev/null 2>"$out.err"; then
      echo ok
    else
      cat "$out.err" >&2
      tr '\n' ' ' <"$out.err" >"$LAST_FETCH_ERROR_FILE"
      echo error
    fi
  fi
  rm -f "$out.err"
}

# fetch_manifest(out): fetches manifest.json to $out. Echoes "ok", "notfound", or "error"
# (rule 1). A real BlobNotFound on Azure counts as "notfound", every other failure as "error".
fetch_manifest() {
  local out=$1
  if [ "$PROVIDER" = aws ]; then
    if aws s3api get-object --bucket "$FTG_BUCKET" --key manifest.json "$out" >/dev/null 2>"$out.err"; then
      echo ok
    else
      cat "$out.err" >&2
      echo error
    fi
    rm -f "$out.err"
  else
    "$BIN/fetch-blob.sh" manifest.json "$out" >/dev/null 2>"$out.err"
    local rc=$?
    cat "$out.err" >&2
    rm -f "$out.err"
    case "$rc" in
      0) echo ok ;;
      3) echo notfound ;;
      *) echo error ;;
    esac
  fi
}

# sha256_from_name(name): the sha256 hex the object's own key or blob name carries, for example
# code/prober-<sha256>.zip, code/sync-<sha256>.sh (AWS) or prober-<sha256>.zip (Azure). Empty
# when the name has none.
sha256_from_name() {
  local name=$1
  if [[ "$name" =~ ([0-9a-f]{64})\.(zip|sh)$ ]]; then
    echo "${BASH_REMATCH[1]}"
  fi
}

# manifest_field(field, manifest): $field is "prober" or "config". The manifest's own key name
# differs by cloud (ADR 0012 section 3: prober_key/config_key on AWS, prober_blob/config_blob
# on Azure).
manifest_field() {
  local field=$1 manifest=$2
  if [ "$PROVIDER" = aws ]; then
    jq -r ".${field}_key" "$manifest"
  else
    jq -r ".${field}_blob" "$manifest"
  fi
}

# --- download-failing-since (rule 3) ---------------------------------------------------------

# The file holds a plain Unix epoch (seconds), not an ISO stamp: portable with only `date +%s`
# (no `date -d` parsing of its own output), and just as readable to an operator, who can convert
# it with `date -d @<epoch>` over the same session access used to read the rest of the log.
start_failing_clock_if_needed() {
  local f=$ETC/download-failing-since now stamp
  now=$(date -u +%s)
  if [ -f "$f" ]; then
    stamp=$(cat "$f" 2>/dev/null)
    if [[ "$stamp" =~ ^[0-9]+$ ]] && [ "$stamp" -le "$now" ]; then
      return 0
    fi
    log "download-failing-since is unreadable or in the future; restarting the clock at now"
  fi
  date -u +%s >"$f"
}

download_failing_elapsed_s() {
  local f=$ETC/download-failing-since now stamp
  [ -f "$f" ] || { echo -1; return; }
  now=$(date -u +%s)
  stamp=$(cat "$f")
  [[ "$stamp" =~ ^[0-9]+$ ]] || { echo -1; return; }
  echo $((now - stamp))
}

clear_failing_clock() { rm -f "$ETC/download-failing-since"; }

write_failed_manifest() { cp "$1" "$ETC/manifest.failed"; }

# --- releases (rule 2, rule 3, rule 9) --------------------------------------------------------

cleanup_interrupted() {
  local d
  for d in "$RELEASES"/*/; do
    [ -d "$d" ] || continue
    [ -f "${d%/}/.complete" ] || {
      log "removing incomplete release $d"
      rm -rf "${d%/}"
    }
  done
  rm -f "$RELEASES"/.tmp-* "$RELEASES"/.next-* 2>/dev/null
}

# combined_hash(dir): 1 sha256 over the sorted list of every file under $dir (not .complete or
# .sha256 themselves), each paired with its own sha256. rule 2 recomputes and compares this
# before reusing an existing release on a revert; rule 3 writes it on a fresh build.
combined_hash() {
  local dir=$1
  (cd "$dir" && find . -type f ! -name .complete ! -name .sha256 -print0 | sort -z | xargs -0 sha256sum) \
    | sha256sum | cut -d' ' -f1
}

in_use() {
  # True when release_id $1 is the target of /opt/ftg/current or /etc/ftg/previous-release.
  local release_id=$1
  [ "$(readlink "$CURRENT" 2>/dev/null)" = "$RELEASES/$release_id" ] && return 0
  [ -f "$ETC/previous-release" ] && [ "$(cat "$ETC/previous-release")" = "$release_id" ] && return 0
  return 1
}

prune_old_releases() {
  # rule 9: keep current and previous-release only.
  local d base
  for d in "$RELEASES"/*/; do
    [ -d "$d" ] || continue
    base=$(basename "${d%/}")
    in_use "$base" || { log "pruning old release $base"; rm -rf "${d%/}"; }
  done
}

# build_release(release_id, prober_name, config_name): rule 3. Downloads, verifies by sha256,
# unzips, and builds the venv, in place at $RELEASES/$release_id -- never anywhere else and
# moved. Returns 0 on success; on a failure it has already logged, removed the throwaway
# download and the incomplete release directory, and (download failures only) managed the
# download-failing-since clock. It does not write manifest.failed; the caller does, because a
# gate failure (rule 4) needs the same marker and this function does not run the gate.
build_release() {
  local release_id=$1 prober_name=$2 config_name=$3
  local dir=$RELEASES/$release_id
  mkdir -p "$dir"
  local prober_zip=$RELEASES/.tmp-$$-prober.zip config_zip=$RELEASES/.tmp-$$-config.zip
  local result

  # A key or blob name with no sha256 cannot be verified: fail the manifest before any fetch,
  # the same as a sha256 mismatch (rule 3), instead of installing unverified bytes.
  local prober_hash config_hash
  prober_hash=$(sha256_from_name "$prober_name")
  config_hash=$(sha256_from_name "$config_name")
  if [ -z "$prober_hash" ] || [ -z "$config_hash" ]; then
    event sync.build_failed "release=$release_id reason=no sha256 in the name of $prober_name or $config_name"
    rm -rf "$dir"
    return 2
  fi

  result=$(fetch_object "$prober_name" "$prober_zip")
  if [ "$result" != ok ]; then
    log "download of $prober_name failed"
    rm -f "$prober_zip" "$config_zip"
    rm -rf "$dir"
    start_failing_clock_if_needed
    return 1
  fi
  result=$(fetch_object "$config_name" "$config_zip")
  if [ "$result" != ok ]; then
    log "download of $config_name failed"
    rm -f "$prober_zip" "$config_zip"
    rm -rf "$dir"
    start_failing_clock_if_needed
    return 1
  fi
  clear_failing_clock

  local got_prober got_config
  got_prober=$(sha256sum "$prober_zip" | cut -d' ' -f1)
  got_config=$(sha256sum "$config_zip" | cut -d' ' -f1)
  if [ "$got_prober" != "$prober_hash" ] || [ "$got_config" != "$config_hash" ]; then
    log "sha256 mismatch on $prober_name or $config_name"
    rm -f "$prober_zip" "$config_zip"
    rm -rf "$dir"
    return 2
  fi

  mkdir -p "$dir/prober" "$dir/config"
  if ! unzip -q -o "$prober_zip" -d "$dir/prober" || ! unzip -q -o "$config_zip" -d "$dir/config"; then
    log "unzip of $release_id failed"
    rm -f "$prober_zip" "$config_zip"
    rm -rf "$dir"
    return 2
  fi
  rm -f "$prober_zip" "$config_zip"

  if ! python3 -m venv "$dir/venv"; then
    log "venv build failed for $release_id"
    rm -rf "$dir"
    return 2
  fi
  local attempt
  for attempt in 1 2 3; do
    "$dir/venv/bin/pip" install --quiet --no-cache-dir "$dir/prober[$PIP_EXTRA]" && break
    if [ "$attempt" -eq 3 ]; then
      log "pip install failed for $release_id after 3 attempts"
      rm -rf "$dir"
      return 2
    fi
    log "pip install attempt $attempt/3 failed for $release_id; retrying"
    sleep 5
  done
  return 0
}

# gate(release_dir): rule 4. Everything `cmd_run` does up to and including the Scheduler
# construction and scheduler.select(), stopping 1 line before run.start: no network call on
# either side of this line.
gate() {
  local dir=$1
  "$dir/venv/bin/ftg-prober" check-config --provider "$PROVIDER" "$dir/config/$CONFIG_FILE"
}

finish_release() {
  # rule 3, last step: the combined hash, then .complete, strictly in that order.
  local dir=$1
  combined_hash "$dir" >"$dir/.sha256"
  : >"$dir/.complete"
}

# --- health window (rule 6) --------------------------------------------------------------------

# health_window(release_dir): echoes "<seconds> exact|capped". The shortest cadence or interval
# this provider's config actually runs, +60s margin, capped at 900s (15 min). "capped" means no
# probe's cadence fit the cap, so the probe.done check (the 4th check) does not apply.
health_window() {
  local dir=$1
  "$dir/venv/bin/python3" - "$dir/config/$CONFIG_FILE" "$PROVIDER" "$HEALTH_MARGIN_S" "$HEALTH_CAP_S" <<'PYEOF'
import sys

from ftg_prober.config import load_config
from ftg_prober.probes import load_probes, packages_for
from ftg_prober.scheduler import Scheduler
from ftg_prober.store import FileStore

path, provider, margin, cap = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
config = load_config(path)
probes = load_probes(packages_for(provider))
# The same object the gate builds (rule 4): cadence_s falls back to each probe's own
# default_cadence when the config sets no interval for it, which a plain read of the config's
# own `cadence` dict would miss for a job-eligible type that relies on that default.
scheduler = Scheduler(probes, config=config, provider=provider, store=FileStore("/dev/null"))
seconds = list(scheduler.cadence_s.values())
if seconds:
    # "exact" is decided on the raw fastest cadence, not on the window: a cadence of cap - margin
    # gives a window of exactly cap, and a probe of that cadence still finishes inside it.
    fastest = min(seconds)
    print(min(fastest + margin, cap), "exact" if fastest <= cap else "capped")
else:
    print(cap, "capped")
PYEOF
}

# health_check(window_s, mode): rule 6. Polls every $HEALTH_POLL_S for the whole window and
# passes only if every check still holds at the END of it: a check that was met and then
# regresses (the unit goes inactive, or crashes and its Restart= policy fires, after an earlier
# poll already saw it healthy) must still fail, so this never returns early just because 1 poll
# happened to see every check pass. It always polls the full window, win or lose.
health_check() {
  local window_s=$1 mode=$2
  local invocation
  invocation=$(systemctl show -p InvocationID --value "$SERVICE")
  local elapsed=0 ok=0
  while :; do
    ok=1
    if [ "$(systemctl is-active "$SERVICE")" != active ]; then
      log "health check: $SERVICE is not active"
      ok=0
    elif [ "$(systemctl show -p InvocationID --value "$SERVICE")" != "$invocation" ]; then
      log "health check: $SERVICE started a new invocation after $invocation"
      ok=0
    elif ! journalctl "_SYSTEMD_INVOCATION_ID=$invocation" 2>/dev/null | grep -q 'run\.start'; then
      log "health check: no run.start for invocation $invocation yet"
      ok=0
    elif [ "$(systemctl show -p NRestarts --value "$SERVICE")" != "0" ]; then
      log "health check: NRestarts is above 0"
      ok=0
    elif [ "$mode" = exact ] && ! journalctl "_SYSTEMD_INVOCATION_ID=$invocation" 2>/dev/null | grep -q 'probe\.done'; then
      log "health check: no probe.done for invocation $invocation yet"
      ok=0
    fi
    [ "$elapsed" -ge "$window_s" ] && break
    sleep "$HEALTH_POLL_S"
    elapsed=$((elapsed + HEALTH_POLL_S))
  done
  [ "$ok" -eq 1 ]
}

# --- restart and swap (rule 5, rule 6, rule 7) -------------------------------------------------

# stop_timeout(release_dir): echoes TimeoutStopSec for the unit. On SIGTERM each Region lane
# finishes only the attempt in progress, all lanes at once (ADR 0020): the slowest is a scale set
# attempt, a pre-teardown, the probe timeout, then the teardown, so max(floor, probe + 2 x
# teardown + margin). The defaults are those of prober/ftg_prober/aws/odcr.py and
# azure/prechecks.py. Read with the release's own config loader, so a value the gate accepted is
# the value used here.
stop_timeout() {
  local dir=$1
  "$dir/venv/bin/python3" - "$dir/config/$CONFIG_FILE" "$PROVIDER" "$STOP_TIMEOUT_FLOOR_S" "$STOP_TIMEOUT_MARGIN_S" <<'PYEOF'
import sys

from ftg_prober.config import load_config

path, provider, floor, margin = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
section = load_config(path).section(provider)
probe = section.get("probe_timeout_seconds", 90)
teardown = section.get("teardown_timeout_seconds", 60)
print(max(floor, probe + 2 * teardown + margin))
PYEOF
}

# write_stop_timeout_dropin(seconds): TimeoutStopSec comes from the release's config, so it is a
# systemd drop-in that sync.sh writes, never a value in user_data or custom_data (ADR 0012: a
# config change must not change those or replace the VM). Each write is followed by a reload.
write_stop_timeout_dropin() {
  local seconds=$1
  mkdir -p "$DROPIN_DIR" || return 1
  printf '[Service]\nTimeoutStopSec=%s\n' "$seconds" >"$DROPIN_DIR/.stop-timeout.conf.new" \
    && mv "$DROPIN_DIR/.stop-timeout.conf.new" "$DROPIN_DIR/stop-timeout.conf" || return 1
  systemctl daemon-reload
}

# current_stop_timeout(): the TimeoutStopSec in the drop-in now, which governs the stop of the
# prober that runs now; 0 when there is no drop-in or it holds no number.
current_stop_timeout() {
  local v
  v=$(sed -n 's/^TimeoutStopSec=//p' "$DROPIN_DIR/stop-timeout.conf" 2>/dev/null | head -1)
  [[ "$v" =~ ^[0-9]+$ ]] && echo "$v" || echo 0
}

restart_and_health_check() {
  local dir=$1 seconds current
  seconds=$(stop_timeout "$dir")
  if ! [[ "$seconds" =~ ^[0-9]+$ ]]; then
    event sync.stop_timeout_invalid "value=${seconds:-(empty)}"
    return 1 # fail closed: a bad value would otherwise write a broken drop-in
  fi
  # The restart stops the OLD prober, which runs with the old config's timeouts. So the stop
  # uses max(old value, new value), reloaded before the restart; the new value follows once the
  # new prober runs. A drop-in with only the new value could SIGKILL a teardown (a leaked resource).
  current=$(current_stop_timeout)
  # 10# keeps a hand-edited value with a leading zero (0900) decimal; bash reads 0900 as octal.
  write_stop_timeout_dropin "$((10#$seconds > 10#$current ? 10#$seconds : 10#$current))" || return 1
  systemctl enable "$SERVICE" >/dev/null 2>&1
  systemctl restart "$SERVICE"
  # The new prober runs now. A failed write here is safe: the larger value stays in force (in the
  # file when the mv failed; in systemd's memory when only the reload failed, because the file
  # then already holds the new, smaller value). It must not fail the install and roll back a
  # release that may be healthy.
  write_stop_timeout_dropin "$((10#$seconds))" \
    || event sync.stop_timeout_write_failed "value=$seconds"
  local window="" mode=""
  read -r window mode < <(health_window "$dir")
  if ! [[ "$window" =~ ^[0-9]+$ ]]; then
    event sync.health_window_invalid "window=${window:-(empty)} mode=${mode:-(empty)}"
    return 1 # fail closed: an empty window would otherwise never end the poll loop
  fi
  if [ "$mode" != exact ] && [ "$mode" != capped ]; then
    event sync.health_window_invalid "window=$window mode=${mode:-(empty)}"
    return 1 # fail closed: any other mode would silently skip the probe.done check
  fi
  health_check "$window" "$mode"
}

swap_current_to() {
  local release_id=$1
  local target=$RELEASES/$release_id
  if [ -L "$CURRENT" ]; then
    local existing
    existing=$(readlink "$CURRENT")
    if [ "$existing" = "$target" ]; then
      return 0 # rule 5: already here (an interrupted sync landed back on this release)
    fi
    basename "$existing" >"$ETC/previous-release"
  fi
  ln -s "$target" "$RELEASES/.next-$$"
  mv -T "$RELEASES/.next-$$" "$CURRENT"
}

# --- terminal outcomes (rule 7, rule 11) -------------------------------------------------------

first_boot_failure() {
  # rule 11: no release to fall back to. The sync unit itself shows failed.
  local manifest_file=$1 dir=$2
  systemctl stop "$SERVICE" >/dev/null 2>&1 || true
  rm -f "$CURRENT"
  rm -rf "$dir"
  write_failed_manifest "$manifest_file"
  event sync.first_boot_failed "release=$(basename "$dir")"
}

terminal_rollback_failed() {
  # rule 7, 2nd bullet: current stays exactly where it is; nothing left to fall back to.
  local manifest_file=$1 failed_release=$2 reason=$3
  cp "$manifest_file" "$ETC/rollback.failed"
  event sync.rollback_failed "failed_release=$failed_release reason=$reason"
}

rollback_or_fail() {
  # rule 7: the health check of a forward update failed. Roll back to previous-release, unless
  # it already names the release that just failed (the interrupted-sync guard of rule 5).
  local manifest_file=$1 failed_release=$2
  local prev
  prev=$(cat "$ETC/previous-release" 2>/dev/null || true)
  if [ -z "$prev" ] || [ "$prev" = "$failed_release" ]; then
    log "no distinct previous release to roll back to"
    terminal_rollback_failed "$manifest_file" "$failed_release" "no distinct previous release"
    return 1
  fi
  ln -s "$RELEASES/$prev" "$RELEASES/.next-$$"
  mv -T "$RELEASES/.next-$$" "$CURRENT"
  if restart_and_health_check "$RELEASES/$prev"; then
    in_use "$failed_release" || rm -rf "$RELEASES/$failed_release"
    write_failed_manifest "$manifest_file"
    event sync.rollback_succeeded "failed_release=$failed_release rolled_back_to=$prev"
    CONFIRMED_HEALTHY=1
    return 0
  fi
  terminal_rollback_failed "$manifest_file" "$failed_release" "rollback to $prev also failed"
  return 1
}

# --- rule 2 through rule 9: install 1 manifest --------------------------------------------------

# install_release(manifest_file, tmp): its own return value is the external contract (0: this
# tick is done, whether or not anything changed, never a failed unit; 1: a first-boot failure,
# the unit shows failed). CONFIRMED_HEALTHY is a separate, internal signal main() reads
# afterward: 1 only on a path that actually swapped, restarted, and health-checked something
# (a genuine new install or a successful rollback), never on a benign skip (a transient download
# failure still under its limit, a sha256 mismatch, or a gate failure on an already-running,
# untouched release) that also returns 0. Clearing rollback.failed (main()) must key off this,
# not off the 0 return value alone, or a benign skip after a prior rollback.failed would wrongly
# clear a marker nothing has actually re-proven healthy.
install_release() {
  local manifest_file=$1 tmp=$2
  CONFIRMED_HEALTHY=0
  local release_id
  release_id=$(sha256sum "$manifest_file" | cut -d' ' -f1)
  local dir=$RELEASES/$release_id
  local first_boot=1
  [ -L "$CURRENT" ] && first_boot=0

  if [ -d "$dir" ] && [ -f "$dir/.complete" ]; then
    if [ "$(combined_hash "$dir")" != "$(cat "$dir/.sha256" 2>/dev/null || true)" ]; then
      log "release $release_id failed its .sha256 recheck; rebuilding"
      rm -rf "$dir"
    fi
  fi

  if [ ! -f "$dir/.complete" ]; then
    local prober_name config_name rc
    prober_name=$(manifest_field prober "$manifest_file")
    config_name=$(manifest_field config "$manifest_file")
    build_release "$release_id" "$prober_name" "$config_name"
    rc=$?
    if [ "$rc" -eq 1 ]; then
      if [ "$(download_failing_elapsed_s)" -ge "$DOWNLOAD_FAIL_LIMIT_S" ]; then
        event sync.download_failed_repeatedly "manifest=$release_id since=$(cat "$ETC/download-failing-since") error=$(cat "$LAST_FETCH_ERROR_FILE" 2>/dev/null || echo "(none)")"
        # manifest.failed first, then the clock: a crash between the 2 must never leave a
        # manifest blacklisted with no record of why, nor a clock a later, unrelated failure
        # could misread as its own (rule 3, "the same step").
        write_failed_manifest "$manifest_file"
        clear_failing_clock
        if [ "$first_boot" -eq 1 ]; then
          first_boot_failure "$manifest_file" "$dir"
          return 1
        fi
        return 0
      fi
      log "download failing for $release_id, under the 1 hour limit; retry next tick"
      return 0
    elif [ "$rc" -ne 0 ]; then
      write_failed_manifest "$manifest_file"
      if [ "$first_boot" -eq 1 ]; then
        first_boot_failure "$manifest_file" "$dir"
        return 1
      fi
      return 0
    fi
    finish_release "$dir"
  fi

  # rule 2, rule 4: the gate runs here whether $dir was just built or reused on a revert.
  local gate_log=$tmp/gate.log
  if ! gate "$dir" >"$gate_log" 2>&1; then
    log "release $release_id failed the config gate: $(cat "$gate_log")"
    # rule 7: never delete a release directory current or previous-release still names, even
    # a reused, previously-good release that fails this gate on a later recheck (for example an
    # environment change outside sync.sh's own control).
    in_use "$release_id" || rm -rf "$dir"
    write_failed_manifest "$manifest_file"
    if [ "$first_boot" -eq 1 ]; then
      first_boot_failure "$manifest_file" "$dir"
      return 1
    fi
    return 0
  fi

  swap_current_to "$release_id"

  if restart_and_health_check "$dir"; then
    cp "$manifest_file" "$ETC/manifest.json"
    event sync.release_installed "release=$release_id"
    prune_old_releases
    CONFIRMED_HEALTHY=1
    return 0
  fi

  if [ "$first_boot" -eq 1 ]; then
    first_boot_failure "$manifest_file" "$dir"
    return 1
  fi
  rollback_or_fail "$manifest_file" "$release_id"
}

# --- self-update --------------------------------------------------------------------------------

# sync.sh is fetched once at first boot (AWS: a 2-step manifest-then-sync_key fetch in
# user_data; Azure: the same in cloud-init's runcmd), not reinstalled by anything below -- a
# reboot alone never refreshes it. Every tick also re-reads the fetched manifest's own
# sync_key/sync_blob field here, before anything else runs, and replaces the installed copy
# atomically (temp file, same directory, then rename) when its hash differs.
#
# refresh_self(manifest_file, tmp_dir): manifest_file's own sync_key/sync_blob field names the
# current, hash-named sync.sh object (main.tf/control.tf), verified by the sha256 its own name
# carries, not by trusting whatever bytes arrive. A new copy must also start with a `#!/` line and
# pass `bash -n`, and is staged inside $BIN so the final rename is atomic on 1 filesystem. The
# old copy is kept as sync.sh.prev, and the new hash goes to sync.trial until a run of the new
# copy proves it works (main() removes it). A hash that fails the content checks, or that the
# unit reverted, is in sync.failed and is never installed again. Returns 0 (an update was
# installed; the caller must exit now so the NEXT tick runs it), 1 (nothing to do: already
# current, blacklisted, or refused or not fetched; left for the next tick), or 2 (the sync key
# carries no sha256: it cannot be verified, so the tick fails).
refresh_self() {
  local manifest_file=$1 tmp_self=$2
  local sync_name
  sync_name=$(manifest_field sync "$manifest_file")
  if [ -z "$sync_name" ] || [ "$sync_name" = null ]; then
    log "manifest has no sync key; skipping self-refresh"
    return 1
  fi
  local want_hash
  want_hash=$(sha256_from_name "$sync_name")
  if [ -z "$want_hash" ]; then
    event sync.self_update_failed "sync_key=$sync_name reason=no sha256 in the name"
    return 2
  fi
  local current_hash
  current_hash=$(sha256sum "$BIN/sync.sh" 2>/dev/null | cut -d' ' -f1)
  if [ "$want_hash" = "$current_hash" ]; then
    return 1
  fi
  if grep -qx "$want_hash" "$ETC/sync.failed" 2>/dev/null; then
    log "sync.sh $want_hash is in sync.failed; keeping the current version"
    return 1
  fi
  local out=$tmp_self/sync.sh result
  result=$(fetch_object "$sync_name" "$out")
  if [ "$result" != ok ]; then
    log "sync.sh self-refresh: fetch of $sync_name failed ($(cat "$LAST_FETCH_ERROR_FILE" 2>/dev/null || echo "no detail")); keeping the current version"
    return 1
  fi
  local got_hash
  got_hash=$(sha256sum "$out" | cut -d' ' -f1)
  if [ "$got_hash" != "$want_hash" ]; then
    log "sync.sh self-refresh: sha256 mismatch on $sync_name (got $got_hash); keeping the current version"
    return 1
  fi
  mkdir -p "$BIN"
  local staged=$BIN/.sync.sh.new
  if ! cp "$out" "$staged" || ! chmod 0755 "$staged"; then
    event sync.self_update_failed "sync_key=$sync_name reason=could not stage $staged"
    rm -f "$staged"
    return 1
  fi
  if [ "$(head -c 3 "$staged")" != '#!/' ] || ! bash -n "$staged" 2>"$tmp_self/syntax.err"; then
    event sync.self_update_refused "sync_key=$sync_name sha256=$got_hash reason=no shebang or bash -n failed: $(tr '\n' ' ' <"$tmp_self/syntax.err" 2>/dev/null)"
    rm -f "$staged"
    echo "$got_hash" >>"$ETC/sync.failed"
    return 1
  fi
  if [ -f "$BIN/sync.sh" ] && ! cp -p "$BIN/sync.sh" "$BIN/sync.sh.prev"; then
    # No sync.sh.prev means the unit could not revert a bad copy: do not install it.
    event sync.self_update_failed "sync_key=$sync_name reason=could not keep $BIN/sync.sh.prev"
    rm -f "$staged"
    return 1
  fi
  echo "$got_hash" >"$ETC/sync.trial"
  mv "$staged" "$BIN/sync.sh"
  event sync.self_updated "sync_key=$sync_name sha256=$got_hash"
  return 0
}

# --- entry point --------------------------------------------------------------------------------

# install_compact_units(): ADR 0025. Writes ftg-compact.service and ftg-compact.timer
# (and the proxy drop-in of ftg-prober.service, when it has one), each only when its text differs
# from the file on the VM; then daemon-reload, and `enable --now` of the timer when it is not
# enabled. The daily compaction runs `ftg-prober compact` from the release /opt/ftg/current names.
# It is a side job: it never touches ftg-prober.service (no stop, no restart, no rollback, no
# change of $CURRENT), and every failure here is logged and returns 0, so the release logic
# of the tick never sees it; it runs from the EXIT trap, after the release outcome is settled.
# AWS: User=ftg, store s3://$FTG_BUCKET/. Azure: DynamicUser=yes like ftg-prober.service (the VM's
# managed identity), store from the store_url field of the manifest that runs now.
install_compact_units() {
  local user_line store_url
  case "$PROVIDER" in
    aws)
      if [ -z "$FTG_BUCKET" ]; then
        event sync.compact_units_failed reason=bucket_empty
        return 0
      fi
      user_line="User=ftg"
      store_url="s3://$FTG_BUCKET/"
      ;;
    azure)
      store_url=$(jq -r '.store_url // empty' "$ETC/manifest.json" 2>/dev/null)
      if [ -z "$store_url" ]; then
        event sync.compact_units_skipped reason=no_store_url_in_manifest
        return 0
      fi
      if ! [[ "$store_url" =~ ^az://[a-z0-9]{3,24}/[a-z0-9-]{3,63}/?$ ]]; then
        event sync.compact_units_failed reason=bad_store_url "value=$store_url"
        return 0
      fi
      user_line="DynamicUser=yes"
      ;;
    *) return 0 ;;
  esac
  local changed=0 unit_dir=$UNIT_DIR dropin_dir=$UNIT_DIR/ftg-compact.service.d new
  mkdir -p "$unit_dir" || { event sync.compact_units_failed reason=cannot_create_unit_dir; return 0; }

  new=$(mktemp "$unit_dir/.ftg-compact.XXXXXX") || { event sync.compact_units_failed reason=no_temp_file; return 0; }
  cat >"$new" <<EOF
[Unit]
Description=follow-the-gpus daily compaction (ADR 0025)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
$user_line
EnvironmentFile=/etc/ftg/env
ExecStart=/opt/ftg/current/venv/bin/ftg-prober compact --provider $PROVIDER --config /opt/ftg/current/config/$CONFIG_FILE --store $store_url
Nice=10
MemoryMax=384M
TimeoutStartSec=3h
NoNewPrivileges=yes
PrivateTmp=yes
EOF
  [ $? -eq 0 ] || { rm -f "$new"; event sync.compact_units_failed reason=cannot_write_service; return 0; }
  install_unit_file "$new" "$unit_dir/ftg-compact.service" && changed=1
  new=$(mktemp "$unit_dir/.ftg-compact.XXXXXX") || return 0
  cat >"$new" <<'EOF'
[Unit]
Description=Run ftg-compact.service 4 times a day (ADR 0025)

[Timer]
OnCalendar=*-*-* 00/6:30:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
EOF
  [ $? -eq 0 ] || { rm -f "$new"; event sync.compact_units_failed reason=cannot_write_timer; return 0; }
  install_unit_file "$new" "$unit_dir/ftg-compact.timer" && changed=1

  # The egress proxy of ftg-prober.service applies to the compaction too (same VM, same S3 path).
  if [ -f "$DROPIN_DIR/proxy.conf" ]; then
    mkdir -p "$dropin_dir" || { event sync.compact_units_failed reason=cannot_create_dropin_dir; return 0; }
    new=$(mktemp "$dropin_dir/.proxy.XXXXXX") || return 0
    cat "$DROPIN_DIR/proxy.conf" >"$new" || { rm -f "$new"; return 0; }
    install_unit_file "$new" "$dropin_dir/proxy.conf" && changed=1
  elif [ -f "$dropin_dir/proxy.conf" ]; then
    rm -f "$dropin_dir/proxy.conf" && changed=1
  fi

  if [ "$changed" -eq 1 ]; then
    event sync.compact_units_written
    systemctl daemon-reload || { event sync.compact_units_failed reason=daemon_reload; return 0; }
  fi
  if ! systemctl is-enabled ftg-compact.timer >/dev/null 2>&1; then
    if systemctl enable --now ftg-compact.timer >/dev/null; then
      event sync.compact_timer_enabled
    else
      event sync.compact_units_failed reason=enable_timer
    fi
  fi
  return 0
}

# disable_os_upgrade_timers(): . The Ubuntu image runs apt-daily-upgrade.timer (and
# apt-daily.timer) each day near 06:00 UTC; unattended-upgrades then lets needrestart restart
# ftg-prober.service when a library it maps changes. `systemctl disable --now` stops the timers and
# removes their enable links, so neither fires again; the OS gets updates only when the VM is
# re-created. It acts only on a timer that is enabled, so a later tick does nothing. A failure is
# logged and returns 0. It never touches ftg-prober.service.
# https://documentation.ubuntu.com/server/how-to/software/automatic-updates/
# https://www.freedesktop.org/software/systemd/man/latest/systemctl.html#disable%20UNIT%E2%80%A6
disable_os_upgrade_timers() {
  local t todo=()
  for t in apt-daily-upgrade.timer apt-daily.timer; do
    if systemctl is-enabled "$t" >/dev/null 2>&1; then todo+=("$t"); fi
  done
  [ "${#todo[@]}" -eq 0 ] && return 0
  if systemctl disable --now "${todo[@]}" >/dev/null 2>&1; then
    event sync.os_upgrade_timers_disabled "timers=${todo[*]}"
  else
    event sync.os_upgrade_timers_failed reason=disable
  fi
  return 0
}

# install_unit_file(new, dest): moves $new onto $dest when the text differs (returns 0), else
# removes $new (returns 1). A failed move is logged and returns 1.
install_unit_file() {
  local new=$1 dest=$2
  if [ -f "$dest" ] && cmp -s "$new" "$dest"; then
    rm -f "$new"
    return 1
  fi
  chmod 0644 "$new"
  if mv "$new" "$dest"; then
    return 0
  fi
  rm -f "$new"
  event sync.compact_units_failed reason=cannot_write "file=$dest"
  return 1
}

tmp=

# finish_tick(rc): the EXIT trap of main. Removes the temp dir, then installs the compaction units
# in a subshell, so nothing in there can change this shell's variables, the exit code ($rc is
# kept: the trap does not call exit), CONFIRMED_HEALTHY, or the prober service. Skipped while a
# self-updated sync.sh is still on trial (it has not yet proven it runs).
finish_tick() {
  local rc=$1
  [ -n "$tmp" ] && rm -rf "$tmp"
  if [ ! -f "$ETC/sync.trial" ]; then
    ( install_compact_units ) || event sync.compact_units_failed reason=install_aborted
    ( disable_os_upgrade_timers ) || event sync.os_upgrade_timers_failed reason=aborted
  fi
  return "$rc"
}

main() {
  mkdir -p "$RELEASES" "$ETC"
  tmp=$(mktemp -d)
  trap 'finish_tick $?' EXIT

  local manifest_file=$tmp/manifest.json result
  result=$(fetch_manifest "$manifest_file")

  # sync.sh's own hash-named key lives in the manifest it just fetched (manifest_field "sync"),
  # the same object the 2 code zips are: so this can only run once a fetch has actually
  # succeeded. A self-update exits now, 0 (nothing is wrong), so the NEXT tick -- on disk a
  # different file by then -- does the rest of this one's work; this tick does not also try to
  # install whatever the manifest's prober/config keys name under the OLD sync.sh logic.
  if [ "$result" = ok ]; then
    refresh_self "$manifest_file" "$tmp"
    case $? in
      0) exit 0 ;;
      2) exit 1 ;;
    esac
  fi
  # This copy started, read sync-env, and got past self-refresh: it is proven enough. From here
  # a non-zero exit is a release outcome, not a broken sync.sh, so the unit must not revert it.
  rm -f "$ETC/sync.trial"

  # rule 7, last paragraph: rollback.failed is terminal until a genuinely new manifest arrives,
  # even when this tick's own fetch failed or (Azure) returned "not found". force_full_reinstall
  # also guards the rule 10 cache-match check below: the build that failed this way left the
  # service in an unconfirmed state (either the new, unhealthy build is still current, or the
  # rollback target failed its own health check too), so a manifest that happens to match the
  # cache must still go through a real gate, swap, restart, and health check, not the plain
  # "already running, nothing to do" shortcut rule 10 uses the rest of the time. Only a build
  # that actually passes this full recheck clears rollback.failed; an operator can also clear it
  # by hand (section 6), the same as manifest.failed.
  local force_full_reinstall=0
  if [ -f "$ETC/rollback.failed" ]; then
    if [ "$result" != ok ] || diff -q "$manifest_file" "$ETC/rollback.failed" >/dev/null 2>&1; then
      log "rollback.failed is present; staying failed until a new manifest or an operator clears it"
      exit 1
    fi
    log "a new manifest replaces the one rollback.failed named; revalidating in full"
    clear_failing_clock
    force_full_reinstall=1
  fi

  if [ -f "$ETC/manifest.failed" ]; then
    if [ ! -L "$CURRENT" ]; then
      # rule 11: the first boot never succeeded.
      if [ "$result" != ok ] || diff -q "$manifest_file" "$ETC/manifest.failed" >/dev/null 2>&1; then
        log "the first boot never succeeded and manifest.failed still matches; staying failed"
        exit 1
      fi
      log "a new manifest replaces the one manifest.failed named after a first-boot failure"
      clear_failing_clock
    else
      # rule 7: a running, healthy previous build; do not retry a blacklisted manifest.
      if [ "$result" = ok ] && diff -q "$manifest_file" "$ETC/manifest.failed" >/dev/null 2>&1; then
        log "the fetched manifest still matches manifest.failed; not retrying"
        exit 0
      fi
      if [ "$result" != ok ]; then
        if [ "$result" = notfound ]; then
          log "manifest.json not found (routine on Azure); retrying next tick"
        else
          log "manifest fetch failed; retrying next tick"
        fi
        exit 0
      fi
      log "a new manifest replaces the one manifest.failed named"
      clear_failing_clock
    fi
  elif [ "$result" != ok ]; then
    if [ "$result" = notfound ]; then
      log "manifest.json not found (routine on Azure); retrying next tick"
    else
      log "manifest fetch failed; retrying next tick"
    fi
    exit 0
  fi

  # rule 10: a manifest that matches the one already running. Skipped when
  # force_full_reinstall is set: a build that only reached rollback.failed through an unproven
  # state must not be waved through on a byte match alone.
  #
  # Accepted trade-off, by design, not an oversight: a manifest that keeps alternating between
  # this cache-matching one and some other, failing-to-download manifest B resets B's own
  # download-failing-since clock on every tick that matches here, the same as a plain revert to
  # a good manifest does. The ADR considered keying the clock to "the manifest it is timing" to
  # close this, and rejected it: that would also stop a manifest that matches the cache from
  # ever clearing a DIFFERENT manifest's old clock, which reopens the alternating-manifest hole
  # rule 3's single, unkeyed clock exists to close (2 different bad manifests, failing on
  # alternating ticks, must still trip the 1-hour limit together). Rule 10 stays unconditional
  # here for that reason; only force_full_reinstall (rollback.failed, above) narrows it.
  if [ "$force_full_reinstall" -eq 0 ] \
    && [ -f "$ETC/manifest.json" ] && diff -q "$manifest_file" "$ETC/manifest.json" >/dev/null 2>&1; then
    clear_failing_clock
    exit 0
  fi

  cleanup_interrupted
  install_release "$manifest_file" "$tmp"
  local rc=$?
  if [ "$CONFIRMED_HEALTHY" -eq 1 ]; then
    # Proven healthy end to end (a genuine install or a successful rollback, not a benign skip
    # that also returns 0): any rollback.failed's job is done. This checks the file itself, not
    # the force_full_reinstall flag, so it still clears a stale marker left over from an earlier
    # incident even on a tick that did not set that flag.
    rm -f "$ETC/rollback.failed"
  fi
  exit "$rc"
}

main "$@"
