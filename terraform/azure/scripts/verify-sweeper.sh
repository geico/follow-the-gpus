#!/usr/bin/env bash
# Post-apply check for the sweeper that runs is the sweeper that the state holds.
# The azurerm provider deploys the zip only when zip_deploy_file changes, and it can leave the new
# path in state when the zip deploy fails (research/learnings/azure-failed-zip-deploy-can-leave-a-clean-plan.md), so a clean plan does not prove the code runs.
# The sweeper logs "code_sha=<hash>" in every invocation. This script compares the newest such
# value in the store with the sweeper_code_sha output, and fails when they differ or when no
# value shows up in time. Usage: verify-sweeper.sh. Needs the az CLI.
# The script reads four stack outputs (sweeper_code_sha, data_storage_account, experiment,
# prober_vm_id) with `tofu output -raw <name>` in terraform/azure. It reads no env override. Credentials are your own az login.
# Exit codes: 0 the running code equals the expected hash. 1 it differs, or no sweeper log shows
# a code_sha in time. 2 unknown: a read failed (az or an output read), so nothing was proven; rerun.
# APPLY_END_UTC (optional, YYYY-MM-DDTHH:MM:SSZ, UTC) is the end time of the apply. When it is set,
# the newest code_sha must come from a log object modified at or after that time. Without it, a
# log from before the apply, or from a redeploy with the same hash, can pass.
# FTG_VERIFY_WAIT_SECONDS (default 600) is the time to wait; FTG_VERIFY_INTERVAL (default 30) is the
# pause between reads. Every az call is scoped to the target subscription, as in status.sh.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
tf_dir="$here/.."
apply_end="${APPLY_END_UTC:-}"
if [ -n "$apply_end" ] && ! [[ $apply_end =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; then
  echo "verify-sweeper: APPLY_END_UTC must look like 2026-01-31T23:59:59Z (UTC)." >&2
  exit 2
fi
wait_seconds="${FTG_VERIFY_WAIT_SECONDS:-600}"
interval="${FTG_VERIFY_INTERVAL:-30}"

if ! command -v az >/dev/null 2>&1; then
  echo "verify-sweeper: the az CLI is not installed. See terraform/azure/README.md." >&2
  exit 1
fi

tmp=$(mktemp -d "${TMPDIR:-/tmp}/ftg-verify-sweeper.XXXXXX")
trap 'rm -rf "$tmp"' EXIT INT TERM

# valid_output: does the value have the shape of output $1? OpenTofu 1.13 `output -raw X` exits 0
# and prints "Warning: No outputs found" on stdout when the dir has no state, so check every value.
# The tests match the whole string (a value with a newline is malformed, not a per-line match).
valid_output() {
  case "$1" in
    sweeper_code_sha) [[ $2 =~ ^[0-9a-f]{64}$ ]] ;;
    data_storage_account) [[ $2 =~ ^[a-z0-9]{3,24}$ ]] ;;
    experiment | prober_vm_id) [[ -n $2 && ! $2 =~ [[:space:]] ]] ;;
  esac
}

output() {
  local value
  if ! value=$(tofu -chdir="$tf_dir" output -no-color -raw "$1" 2>"$tmp/err"); then
    echo "verify-sweeper: unknown, rerun: reading output $1 failed, so the expected value is unknown. tofu said:" >&2
    cat "$tmp/err" >&2
    exit 2
  fi
  if ! valid_output "$1" "$value"; then
    echo "verify-sweeper: unknown, rerun: the value of $1 is empty or malformed (first 80 characters: $(printf '%s' "$value" | head -c 80 | tr '\n' ' '))." >&2
    exit 2
  fi
  printf '%s' "$value"
}

account=$(output data_storage_account)
experiment=$(output experiment)
expected=$(output sweeper_code_sha)
vm_id=$(output prober_vm_id)

from_id=$(printf '%s' "$vm_id" | sed -n 's#^/subscriptions/\([^/]*\)/.*#\1#ip')
subscription="${AZURE_SUBSCRIPTION_ID:-${ARM_SUBSCRIPTION_ID:-$from_id}}"
if [ -z "$subscription" ]; then
  echo "verify-sweeper: no target subscription. Set AZURE_SUBSCRIPTION_ID or ARM_SUBSCRIPTION_ID." >&2
  exit 1
fi
if [ -n "$from_id" ] && [ "$(printf '%s' "$subscription" | tr 'A-Z' 'a-z')" != "$(printf '%s' "$from_id" | tr 'A-Z' 'a-z')" ]; then
  echo "verify-sweeper: the target subscription differs from the subscription of the prober VM. Refusing to read it." >&2
  exit 1
fi

azs() {
  az "$@" --subscription "$subscription"
}

# az_failed: an az call failed (for example a 403 for a missing role). Print the real cause at
# once, as az printed it, instead of waiting out the timer.
az_failed() {
  echo "unknown, rerun: az $1 failed, so the running code is unknown (not a mismatch). az said:" >&2
  cat "$tmp/err" >&2
}

# utc_day: print the UTC date N days ago (GNU date, then BSD date).
utc_day() {
  date -u -d "-$1 day" +%Y-%m-%d 2>/dev/null || date -u -v-"$1"d +%Y-%m-%d
}

# scan_day: print "<code_sha> <lastModified>" of the newest log object under one UTC day that holds one. The
# prober writes to the same prefix and logs no code_sha, so look at the newest $newest_n objects.
# Return 0 when it read all it needed (found or not), 1 when an az call failed.
newest_n=30
scan_day() {
  local prefix names name modified found
  prefix="experiment=${experiment}/logs/provider=azure/date=$1/"
  if ! names=$(azs storage blob list --account-name "$account" --container-name data --prefix "$prefix" \
    --auth-mode login \
    --query "reverse(sort_by([].{name: name, modified: properties.lastModified}, &modified))[:$newest_n].[name, modified]" -o tsv 2>"$tmp/err"); then
    az_failed "storage blob list"
    return 1
  fi
  # Each row must be exactly "name<TAB>modified". A tab inside a blob name makes more fields, and
  # read would cut the name short or shift the time: refuse, do not guess.
  if [ -n "$names" ] && printf '%s\n' "$names" | awk -F'\t' 'NF != 2 { bad = 1 } END { exit !bad }'; then
    echo "unknown, rerun: a row of the blob list is not name<TAB>modified (a tab in a blob name?), so the running code is unknown." >&2
    return 1
  fi
  while IFS=$'\t' read -r name modified; do
    modified="${modified%$'\r'}"
    [ -n "$name" ] || continue
    # az prints lastModified in UTC ("+00:00"). Any other offset would break the text compare below.
    if ! [[ $modified =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(\+00:00|Z)$ ]]; then
      echo "unknown, rerun: lastModified of a blob is not UTC (+00:00 or Z): '$modified', so the apply-time check cannot compare it." >&2
      return 1
    fi
    if ! azs storage blob download --account-name "$account" --container-name data --name "$name" \
      --auth-mode login --file "$tmp/blob" --no-progress -o none --overwrite true </dev/null 2>"$tmp/err"; then
      az_failed "storage blob download"
      return 1
    fi
    found=$(grep -Eo 'code_sha=[0-9a-f]{64}([^0-9a-fA-F]|$)' "$tmp/blob" | tail -n 1 || true)
    if [ -n "$found" ]; then
      printf '%s %s' "$(printf '%s' "${found#code_sha=}" | cut -c1-64)" "$modified"
      return 0
    fi
  done <<<"$names"
  return 0
}

# running_sha: the code_sha of today (UTC), or of yesterday when today's newest objects hold none
# (a check right after 00:00 UTC, before the sweeper's first run of the day).
running_sha() {
  local sha day
  for day in "$(utc_day 0)" "$(utc_day 1)"; do
    sha=$(scan_day "$day") || return 1
    if [ -n "$sha" ]; then
      printf '%s' "$sha"
      return 0
    fi
  done
  return 0
}

deadline=$((SECONDS + wait_seconds))
seen=""
stale=""
while :; do
  if ! seen_line=$(running_sha); then
    exit 2
  fi
  seen="${seen_line%% *}"
  modified=""
  [ -z "$seen_line" ] || modified="${seen_line#* }"
  stale=""
  # lastModified is UTC ("2026-01-31T23:59:59+00:00"); compare the first 19 characters as text.
  if [ "$seen" = "$expected" ] && [ -n "$apply_end" ] && [[ "${modified:0:19}" < "${apply_end:0:19}" ]]; then
    stale=1
  fi
  if [ "$seen" = "$expected" ] && [ -z "$stale" ]; then
    echo "ok: the running sweeper has code_sha $expected, the hash in state."
    exit 0
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    break
  fi
  sleep "$interval"
done

if [ -n "$stale" ]; then
  echo "FAIL: not proven after the apply: the newest sweeper log with code_sha $expected was modified at $modified, before the apply ended at $apply_end. Wait for a run after the apply and rerun." >&2
elif [ -z "$seen" ]; then
  echo "FAIL: no sweeper log with a code_sha in the newest $newest_n log objects of today or yesterday (UTC) within $wait_seconds s. The sweeper may not run at all, it runs code from before , or heavy logging pushed its objects out of the newest $newest_n. Expected $expected." >&2
else
  echo "FAIL: the running sweeper has code_sha $seen, but state holds $expected. The zip deploy did not reach the Function App." >&2
fi
echo "To redeploy, run: tofu -chdir=terraform/azure apply -replace=azurerm_linux_function_app.sweeper (it replaces the app)." >&2
exit 1
