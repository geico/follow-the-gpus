#!/usr/bin/env bash
# Guard. The azurerm provider deploys the sweeper zip only when the value of
# zip_deploy_file changes (it is not ForceNew). If a change to the sweeper code gives the same
# value, the plan shows no diff and the new code never reaches the Function App. This script
# builds the real zip in a temporary copy of the repo, with the real archive provider and mocked
# azurerm, and reads the planned zip_deploy_file:
#   1. a change to a file that the zip holds must change the value;
#   2. a change to a file that the zip leaves out (tests, caches) must not change it;
#   3. the hash in the zip name is the hash that the sweeper computes over its own files at run
#      time (sweeper/azure/sweeper/code_sha.py), which the post-apply check relies on.
# No credentials and no cloud calls. Usage: scripts/check-sweeper-redeploy.sh [REPO_ROOT]
set -u

root=$(cd "${1:-$(dirname "$0")/..}" && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/ftg-sweeper-redeploy.XXXXXX")
trap 'rm -rf "$work"' EXIT

mkdir -p "$work/sweeper"
cp -R "$root/terraform" "$work/terraform"
cp -R "$root/sweeper/azure" "$work/sweeper/azure"
cp -R "$root/prober" "$work/prober"
cp -R "$root/config" "$work/config"
rm -rf "$work/terraform/azure/.terraform" "$work/terraform/aws/.terraform" "$work/sweeper/azure/.venv"

tf="tofu -chdir=$work/terraform/azure"
log="$work/init.log"
if ! $tf init -backend=false >"$log" 2>&1; then
  echo "FAIL  tofu init failed" >&2
  cat "$log" >&2
  exit 1
fi

# deploy_path: print the planned zip_deploy_file of the sweeper.
deploy_path() {
  local out="$work/test.log"
  $tf test -verbose -filter=tests/code_zip_location.tftest.hcl >"$out" 2>&1 || {
    echo "FAIL  tofu test failed" >&2
    cat "$out" >&2
    return 1
  }
  sed -n 's/.*zip_deploy_file *= *"\([^"]*\)".*/\1/p' "$out" | head -n 1
}

before=$(deploy_path) || exit 1
if [ -z "$before" ]; then
  echo "FAIL  no planned zip_deploy_file in the test output" >&2
  exit 1
fi

fail=0

# 1. A code change.
printf '\n# change\n' >>"$work/sweeper/azure/function_app.py"
after_code=$(deploy_path) || exit 1
if [ "$after_code" = "$before" ]; then
  echo "FAIL  a sweeper code change gives the same zip_deploy_file ($before): no plan diff, no redeploy" >&2
  fail=1
else
  echo "PASS  a sweeper code change changes zip_deploy_file"
fi

# 2. Files that the zip leaves out.
mkdir -p "$work/sweeper/azure/tests" "$work/sweeper/azure/sweeper/__pycache__"
printf 'x\n' >"$work/sweeper/azure/tests/test_extra.py"
printf 'x\n' >"$work/sweeper/azure/sweeper/__pycache__/x.pyc"
after_extra=$(deploy_path) || exit 1
if [ "$after_extra" != "$after_code" ]; then
  echo "FAIL  a file that the zip leaves out changes zip_deploy_file: a needless redeploy" >&2
  fail=1
else
  echo "PASS  a file that the zip leaves out does not change zip_deploy_file"
fi

# 3. The run-time hash of the sweeper equals the hash in the planned zip name.
runtime=$(python3 -I - "$work/sweeper/azure" <<'PY'
import importlib.util, sys
from pathlib import Path
root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("code_sha", root / "sweeper" / "code_sha.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.code_sha(root))
PY
)
case "$after_extra" in
  *"sweeper-$runtime.zip") echo "PASS  the run-time hash of the sweeper is the hash in the zip name" ;;
  *)
    echo "FAIL  the run-time hash ($runtime) differs from the planned zip name ($after_extra): the post-apply check would always fail" >&2
    fail=1
    ;;
esac

exit "$fail"
