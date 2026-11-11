#!/usr/bin/env bash
# Check that `make destroy/aws` and `make destroy/azure` pass backend.hcl to `tofu init`, as plan and
# deploy do. A stack with a partial backend block (backend "s3" {} in backend_override.tf) fails to
# initialize without -backend-config, so the destroy would stop at its first command.
# This test uses the real tofu, no fake. The backend points at a closed port on 127.0.0.1, so it
# calls no cloud. With the file passed, tofu accepts the backend settings and fails later, on the
# connection ("Failed to get existing workspaces"). Without it, tofu stops on "Invalid bucket value".
set -u
repo=$(cd "$(dirname "$0")/../.." && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/ftg-destroy-test.XXXXXX")
trap 'rm -rf "$work"' EXIT
fail=0
bad() { echo "FAIL $1" >&2; fail=1; }

command -v tofu >/dev/null 2>&1 || { echo "FAIL tofu is not on PATH" >&2; exit 1; }

for cloud in aws azure; do
  upper=$(printf '%s' "$cloud" | tr '[:lower:]' '[:upper:]')
  dir="$work/$cloud"
  mkdir -p "$dir"
  printf 'terraform {\n  backend "s3" {}\n}\n' >"$dir/backend_override.tf"
  cat >"$dir/backend.hcl" <<'HCL'
bucket                      = "example-bucket"
key                         = "example.tfstate"
region                      = "us-east-1"
access_key                  = "example"
secret_key                  = "example"
skip_credentials_validation = true
skip_requesting_account_id  = true
skip_metadata_api_check     = true
max_retries                 = 1
endpoints = {
  s3 = "http://127.0.0.1:1"
}
HCL
  out="$work/$cloud.out"
  make -C "$repo" "destroy/$cloud" "${upper}_TF_DIR=$dir" AUTO_APPROVE=1 >"$out" 2>&1
  rc=$?
  [ "$rc" -ne 0 ] || bad "destroy/$cloud: expected a failure on the closed port, got exit 0"
  grep -q 'Failed to get existing workspaces' "$out" || bad "destroy/$cloud: init did not use backend.hcl (see $out)"
  ! grep -q 'Invalid bucket value' "$out" || bad "destroy/$cloud: init ran without backend.hcl"
  # Without backend.hcl the init is plain, so the same stack stops on the missing settings.
  rm "$dir/backend.hcl"
  make -C "$repo" "destroy/$cloud" "${upper}_TF_DIR=$dir" AUTO_APPROVE=1 >"$out" 2>&1
  grep -q 'Invalid bucket value' "$out" || bad "destroy/$cloud: no backend.hcl, expected the missing-settings error (see $out)"
done
[ "$fail" -eq 0 ] && echo "destroy.test: ok"
exit "$fail"
