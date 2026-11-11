#!/usr/bin/env bash
# For each Terraform stack: plant a stale zip at the old, git-ignored dist/ output path, run
# the real (unmocked) archive build through `tofu test`, and show that the stale file is
# never the file the build produces. No credentials and no network calls to a cloud provider.
# Usage: scripts/check-stale-build-artifact.sh [AWS_TF] [AZURE_TF]
#   AWS_TF and AZURE_TF default to "tofu -chdir=terraform/<stack>".
set -u

here=$(cd "$(dirname "$0")/.." && pwd)
aws_tf=${1:-tofu -chdir=$here/terraform/aws}
azure_tf=${2:-tofu -chdir=$here/terraform/azure}

pass=0
failn=0
ok() { echo "PASS  $1"; pass=$((pass + 1)); }
bad() { echo "FAIL  $1"; failn=$((failn + 1)); }

stale_files=()
cleanup() {
  local f
  for f in "${stale_files[@]:-}"; do [ -n "$f" ] && rm -f "$f"; done
}
trap cleanup EXIT

# check STACK TF_DIR TF_CMD NAME...: plant a stale dist/<name>.zip for each NAME, run the real
# build through `tofu test`, then check that each built file is not the stale one.
check() {
  local stack="$1" tf_dir="$2" tf_cmd="$3"
  shift 3
  local name zip_name
  mkdir -p "$tf_dir/dist"
  for name in "$@"; do
    zip_name="$tf_dir/dist/$name.zip"
    printf 'stale %s.zip from a build a day earlier\n' "$name" >"$zip_name"
    stale_files+=("$zip_name")
  done

  local log
  log=$(mktemp)
  if ! $tf_cmd test -filter=tests/code_zip_location.tftest.hcl >"$log" 2>&1; then
    bad "$stack: tofu test (code_zip_location) failed"
    cat "$log"
    rm -f "$log"
    return
  fi
  ok "$stack: tofu test (code_zip_location) passed with a stale dist/ zip present"
  rm -f "$log"

  for name in "$@"; do
    local stale_zip="$tf_dir/dist/$name.zip"
    local built_zip="$tf_dir/.terraform/ftg-build/$name.zip"
    # The Azure sweeper zip name holds a hash of the source.
    [ -f "$built_zip" ] || built_zip=$(ls -t "$tf_dir/.terraform/ftg-build/$name"-*.zip 2>/dev/null | head -n 1)
    if [ ! -f "$built_zip" ]; then
      bad "$stack: $built_zip was not built"
      continue
    fi
    # This cmp is only a sanity check. The tofu test assertions above give the real
    # protection: they check the build from the Terraform side, not from this script.
    if cmp -s "$built_zip" "$stale_zip"; then
      bad "$stack: $built_zip has the stale bytes of $stale_zip"
    else
      ok "$stack: $built_zip is not the stale $stale_zip"
    fi
    rm -f "$stale_zip"
  done
}

check aws "$here/terraform/aws" "$aws_tf" prober config sweeper
check azure "$here/terraform/azure" "$azure_tf" prober config sweeper

echo "check-stale-build-artifact: passed $pass  failed $failn"
[ "$failn" -eq 0 ]
