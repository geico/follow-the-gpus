#!/usr/bin/env bash
# every az call of terraform/azure/scripts/status.sh passes --subscription <target>, and the
# script fails when it has no target subscription or the target differs from the prober VM id.
# A fake tofu and a fake az on PATH record the calls. No network, no credentials. Run: make test/status
set -u

here=$(cd "$(dirname "$0")" && pwd)
script="$here/../azure/scripts/status.sh"
work=$(mktemp -d "${TMPDIR:-/tmp}/ftg-status.XXXXXX")
trap 'rm -rf "$work"' EXIT INT TERM
mkdir "$work/bin"

cat >"$work/bin/tofu" <<'FAKE'
#!/usr/bin/env bash
case "${@: -1}" in
  prober_vm_id) printf '%s' "/subscriptions/${FAKE_VM_SUB}/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm" ;;
  *) printf 'x' ;;
esac
FAKE
cat >"$work/bin/az" <<'FAKE'
#!/usr/bin/env bash
echo "$*" >>"$FAKE_AZ_LOG"
case "$1 $2" in
  "vmss list") echo "scale1" ;;
  "capacity reservation") [ "$3" = "group" ] && echo "grp1" ;;
esac
case "$1 $2" in
  "vm list" | "disk list" | "network nic" | "vmss list-instances" | "capacity reservation") [ "$3" = "list" ] && echo 0 ;;
esac
exit 0
FAKE
chmod +x "$work/bin/tofu" "$work/bin/az"

fail=0
check() { # name, condition result
  if [ "$2" = ok ]; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi
}
run() { # env assignments are in the caller's environment
  : >"$work/az.log"
  FAKE_AZ_LOG="$work/az.log" PATH="$work/bin:$PATH" bash "$script" x >"$work/out" 2>"$work/err"
}

# 1. Target from AZURE_SUBSCRIPTION_ID: every az call is scoped.
(export FAKE_VM_SUB=sub-0 AZURE_SUBSCRIPTION_ID=sub-0; unset ARM_SUBSCRIPTION_ID; run)
rc=$?
calls=$(wc -l <"$work/az.log" | tr -d ' ')
unscoped=$(grep -vc -- '--subscription sub-0$' "$work/az.log")
[ "$rc" = 0 ] && [ "$calls" -gt 5 ] && [ "$unscoped" = 0 ] && r=ok || r=bad
check "every az call has --subscription sub-0 ($calls calls, $unscoped unscoped, rc $rc)" "$r"

# Blob reads use the signed-in identity, never an account key.
blob=$(grep -c -- '^storage blob list .*--auth-mode login' "$work/az.log")
grep -q -- '--auth-mode key' "$work/az.log" && blob=0
[ "$blob" = 1 ] && r=ok || r=bad
check "the blob list uses --auth-mode login" "$r"

# 2. Target from the prober VM id when no setting exists.
(export FAKE_VM_SUB=sub-1; unset AZURE_SUBSCRIPTION_ID ARM_SUBSCRIPTION_ID; run)
rc=$?
unscoped=$(grep -vc -- '--subscription sub-1$' "$work/az.log")
[ "$rc" = 0 ] && [ "$unscoped" = 0 ] && r=ok || r=bad
check "target taken from the VM id" "$r"

# 3. A target that differs from the VM id: fail before any az read.
(export FAKE_VM_SUB=sub-1 AZURE_SUBSCRIPTION_ID=sub-2; unset ARM_SUBSCRIPTION_ID; run)
rc=$?
[ "$rc" != 0 ] && [ ! -s "$work/az.log" ] && grep -q "differs" "$work/err" && r=ok || r=bad
check "a different target subscription fails with no az call" "$r"

# 4. No target and no subscription in the VM id: fail loudly.
(export FAKE_VM_SUB=; unset AZURE_SUBSCRIPTION_ID ARM_SUBSCRIPTION_ID; run)
rc=$?
[ "$rc" != 0 ] && [ ! -s "$work/az.log" ] && grep -q "no target subscription" "$work/err" && r=ok || r=bad
check "no target subscription fails with no az call" "$r"

# 5. make status/aws on a stack with no state: 1 clear line, exit 2, no traceback. This uses the real
# tofu in an empty folder (no cloud call).
if command -v tofu >/dev/null 2>&1; then
  mkdir "$work/empty"
  make -C "$here/../.." status/aws AWS_TF_DIR="$work/empty" >"$work/aws.out" 2>&1
  rc=$?
  [ "$rc" != 0 ] && grep -q "not deployed" "$work/aws.out" && ! grep -q Traceback "$work/aws.out" && r=ok || r=bad
  check "status/aws on an undeployed stack gives a clear message, no traceback" "$r"
fi

exit $fail
