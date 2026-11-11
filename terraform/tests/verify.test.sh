#!/usr/bin/env bash
# scripts/verify.sh (make verify/aws, verify/azure) prints the newest row and log, the
# manifest sha256, and the running release, and exits 0 only when every check holds (release = hash, service active,
# maps proof, no rollback marker, cached = store manifest, NRestarts 0, row and log newer than the start). A fake aws, a fake az, and a fake tofu stand in for the
# real ones. Their output shapes follow the documented CLI formats and the private repo's recipes:
#   - `aws s3api list-objects-v2 --query 'Contents[].[LastModified,Key]' --output text`: one
#     "<time><TAB><key>" row per object, or the line "None" when the prefix is empty;
#   - `aws s3api head-object --query LastModified --output text`: one time;
#   - `aws s3 cp --only-show-errors`: writes the file, prints nothing;
#   - `aws ssm send-command --query Command.CommandId --output text`: the id; `aws ssm wait`:
#     nothing; `get-command-invocation --query StandardOutputContent --output text`: the stdout;
#   - `az storage blob list --query '[].[properties.lastModified,name]' -o tsv`: "<time><TAB><name>";
#   - `az vm run-command invoke --query 'value[0].message' -o tsv`: "Enable succeeded: ", then
#     "[stdout]", the stdout, "[stderr]", the stderr;
#   - a denied az call: "ERROR: ..." on stderr, exit 1, empty stdout.
# The fake tofu copies `tofu output -no-color -raw NAME` of OpenTofu 1.12, compared on 2026-11-03.
# No network, no credentials. Run: make test/verify
set -u
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/ftg-verify-test.XXXXXX") || exit 1
trap 'rm -rf "$work"' EXIT INT TERM
mkdir -p "$work/bin" "$work/root/scripts" "$work/root/terraform"
cp "$here/../../scripts/verify.sh" "$work/root/scripts/"
script="$work/root/scripts/verify.sh"
fail=0
check() { if [ "$2" = ok ]; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }

manifest='{"release":"x"}'
printf '%s\n' "$manifest" >"$work/manifest.json"
if command -v sha256sum >/dev/null 2>&1; then msha=$(sha256sum <"$work/manifest.json" | cut -d' ' -f1); else msha=$(shasum -a 256 <"$work/manifest.json" | cut -d' ' -f1); fi
other=$(printf 'c%.0s' $(seq 64))

cat >"$work/bin/tofu" <<'FAKE'
#!/usr/bin/env bash
# `tofu -chdir=DIR output -no-color -raw NAME`, as OpenTofu 1.12 prints it:
#   - a present output: exit 0, the value on stdout with no newline, nothing on stderr;
#   - a missing output: exit 1, nothing on stdout, "Error: Output "NAME" not found" on stderr;
#   - a state with no outputs (FAKE_TOFU_NOSTATE=1): exit 0 and "Warning: No outputs found" on stdout.
name="${@: -1}"
if [ -n "${FAKE_TOFU_NOSTATE:-}" ]; then
  printf '\nWarning: No outputs found\n\nThe state file either has no outputs defined, or all the defined outputs\nare empty.\n'
  exit 0
fi
v=$(printenv "FAKE_OUT_$name") || {
  printf '\nError: Output "%s" not found\n\nThe output variable requested could not be found in the state file.\n' "$name" >&2
  exit 1
}
printf '%s' "$v"
FAKE
cat >"$work/bin/aws" <<'FAKE'
#!/usr/bin/env bash
echo "aws $*" >>"$FAKE_LOG"
case "$1 $2" in
  "s3api list-objects-v2") [ -n "${FAKE_LIST_EMPTY:-}" ] && echo None || printf '%b' "$FAKE_LIST" ;;
  "s3api head-object") echo "2026-10-31T09:00:00+00:00" ;;
  "s3 cp") cp "$FAKE_DIR/manifest.json" "$4" ;;
  "ssm send-command")
    printf '%s' "$*" >"$FAKE_DIR/ssm-params"
    [ -n "${FAKE_SSM_FAIL:-}" ] && { printf '\nAn error occurred (AccessDeniedException) when calling the SendCommand operation: not authorized\n' >&2; exit 254; }
    echo "11111111-2222-3333-4444-555555555555" ;;
  "ssm wait") exit 0 ;;
  "ssm get-command-invocation") printf '%b' "$FAKE_VM_OUT" ;;
esac
exit 0
FAKE
cat >"$work/bin/az" <<'FAKE'
#!/usr/bin/env bash
echo "az $*" >>"$FAKE_LOG"
case "$*" in *"--subscription sub-0"*) ;; *) echo "ERROR: unscoped az call" >&2; exit 1 ;; esac
case "$1 $2 $3" in
  "storage blob list") printf '%b' "$FAKE_LIST" ;;
  "storage blob show") echo "2026-10-31T09:00:00+00:00" ;;
  "storage blob download")
    [ -n "${FAKE_AZ_DENY:-}" ] && { printf 'ERROR: \nYou do not have the required permissions needed to perform this operation.\n' >&2; exit 1; }
    while [ $# -gt 0 ]; do [ "$1" = --file ] && f="$2"; shift; done
    cp "$FAKE_DIR/manifest.json" "$f" ;;
  "vm run-command invoke")
    printf '%s' "$*" >"$FAKE_DIR/az-scripts"
    printf 'Enable succeeded: \n[stdout]\n%b\n[stderr]\n' "$FAKE_VM_OUT" ;;
esac
exit 0
FAKE
chmod +x "$work/bin/aws" "$work/bin/az" "$work/bin/tofu"

vmout() { # release, active -> VM stdout; V_* variables override one field
  printf 'active=%s\\nrelease=%s\\nhost=%s\\nstart=Fri 2026-10-31 10:00:00 UTC\\nstartiso=%s\\nrestarts=%s\\ncached=%s\\nproof=%s\\nrollback=%s' \
    "$2" "$1" "${V_HOST:-vmhost}" "${V_START:-2026-10-31T10:00:00}" "${V_RESTARTS:-0}" "${V_CACHED:-$1}" "${V_PROOF:-OK}" "${V_ROLL:-absent}"
}
run() { # cloud
  : >"$work/log"
  FAKE_LOG="$work/log" FAKE_DIR="$work" \
    FAKE_OUT_experiment=exp FAKE_OUT_bucket_name=bkt FAKE_OUT_instance_id=i-0 FAKE_OUT_region=us-east-1 \
    FAKE_OUT_data_storage_account=acct FAKE_OUT_prober_vm_id=/subscriptions/sub-0/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm \
    PATH="$work/bin:$PATH" bash "$script" "$1" >"$work/out" 2>"$work/err"
}
aws_list='2026-10-31T10:05:00+00:00\texperiment=exp/provider=aws/probe_type=t/date=2026-10-31/20261031T100001Z_x-y_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n2026-10-31T10:30:00+00:00\texperiment=exp/provider=aws/probe_type=t/date=2026-10-31/20261031T100002Z_x-y_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n2026-10-31T10:20:00+00:00\texperiment=exp/logs/provider=aws/date=2026-10-31/20261031T100003Z_vmhost_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n2026-10-31T10:10:00+00:00\texperiment=exp/logs/provider=aws/date=2026-10-31/20261031T100004Z_vmhost_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n'

FAKE_LIST="$aws_list" FAKE_VM_OUT="$(vmout "$msha" active)" run aws
rc=$?
grep -q "newest row: 2026-10-31T10:30:00+00:00.experiment=exp/provider=aws/probe_type=t/date=2026-10-31/20261031T100002Z_x-y_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl" "$work/out" \
  && grep -q "newest log: 2026-10-31T10:20:00+00:00.experiment=exp/logs/provider=aws/date=2026-10-31/20261031T100003Z_vmhost_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl" "$work/out" \
  && grep -q "manifest.json: sha256 $msha, LastModified 2026-10-31T09:00:00+00:00" "$work/out" \
  && grep -q "running release: $msha, service start: Fri 2026-10-31 10:00:00 UTC" "$work/out" \
  && grep -q "process maps = current: OK, restarts: 0, rollback marker: absent" "$work/out" \
  && grep -q "service active: yes (state active)" "$work/out" && [ "$rc" = 0 ] && r=ok || r=bad
check "aws: release = manifest hash, service active: prints the proof, exit 0 (rc $rc)" "$r"

cmdline=$(sed -n 's/.*\\"echo \([^ ]*\) | base64 -d | bash\\".*/\1/p' "$work/ssm-params")
[ -z "$cmdline" ] && cmdline=$(sed -n 's/.*echo \([A-Za-z0-9+\/=]*\) | base64 -d | bash.*/\1/p' "$work/ssm-params")
printf '%s' "$cmdline" | base64 -d >"$work/vm.sh" 2>/dev/null
bash -n "$work/vm.sh" && grep -q "release proof\|proof=OK" "$work/vm.sh" && grep -q "/proc/\$pid/maps" "$work/vm.sh" && grep -q "rollback.failed" "$work/vm.sh" && r=ok || r=bad
check "aws: the command sent over SSM decodes to a valid script with the maps proof and the rollback marker" "$r"

FAKE_LIST="$aws_list" FAKE_VM_OUT="$(vmout "$other" active)" run aws
rc=$?
[ "$rc" = 1 ] && grep -q "service active: NO" "$work/out" && r=ok || r=bad
check "aws: running release differs from the manifest hash: exit 1 (rc $rc)" "$r"

FAKE_LIST="$aws_list" FAKE_VM_OUT="$(vmout "$msha" failed)" run aws
rc=$?
[ "$rc" = 1 ] && grep -q "service active: NO (state failed)" "$work/out" && r=ok || r=bad
check "aws: service not active: exit 1 (rc $rc)" "$r"

FAKE_LIST="$aws_list" FAKE_VM_OUT="active=active\\nrelease=\\n" run aws
rc=$?
[ "$rc" = 1 ] && grep -q "running release: unavailable" "$work/out" && r=ok || r=bad
check "aws: no release link on the VM: exit 1 (rc $rc)" "$r"

FAKE_LIST_EMPTY=1 FAKE_VM_OUT="$(vmout "$msha" active)" run aws
rc=$?
[ "$rc" = 1 ] && grep -q "newest row: none" "$work/out" && grep -q "newest log: none" "$work/out" \
  && grep -q "^NO: no row object" "$work/out" && grep -q "^NO: no log object" "$work/out" && r=ok || r=bad
check "aws: an empty prefix: exit 1, NO lines for row and log (rc $rc)" "$r"

only_rows='2026-10-31T10:30:00+00:00\texperiment=exp/provider=aws/probe_type=t/date=2026-10-31/20261031T100002Z_x-y_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n'
only_logs='2026-10-31T10:20:00+00:00\texperiment=exp/logs/provider=aws/date=2026-10-31/20261031T100003Z_vmhost_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n'
FAKE_LIST="$only_logs" FAKE_VM_OUT="$(vmout "$msha" active)" run aws
rc=$?
[ "$rc" = 1 ] && grep -q "^NO: no row object" "$work/out" && ! grep -q "^NO: no log" "$work/out" && r=ok || r=bad
check "aws: no row object: exit 1 with its NO line (rc $rc)" "$r"
FAKE_LIST="$only_rows" FAKE_VM_OUT="$(vmout "$msha" active)" run aws
rc=$?
[ "$rc" = 1 ] && grep -q "^NO: no log object" "$work/out" && ! grep -q "^NO: no row" "$work/out" && r=ok || r=bad
check "aws: no log object: exit 1 with its NO line (rc $rc)" "$r"

old_row='2026-10-31T09:30:00+00:00\texperiment=exp/provider=aws/probe_type=t/date=2026-10-31/20261031T100002Z_x-y_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n2026-10-31T10:20:00+00:00\texperiment=exp/logs/provider=aws/date=2026-10-31/20261031T100003Z_vmhost_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n'
old_log='2026-10-31T10:30:00+00:00\texperiment=exp/provider=aws/probe_type=t/date=2026-10-31/20261031T100002Z_x-y_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n2026-10-31T09:20:00+00:00\texperiment=exp/logs/provider=aws/date=2026-10-31/20261031T100003Z_vmhost_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n'
FAKE_LIST="$old_row" FAKE_VM_OUT="$(vmout "$msha" active)" run aws
rc=$?
[ "$rc" = 1 ] && grep -q "^NO: newest row (2026-10-31T09:30:00) is not newer than the service start" "$work/out" && ! grep -q "^NO: newest log" "$work/out" && r=ok || r=bad
check "aws: newest row older than the service start: exit 1 (rc $rc)" "$r"
FAKE_LIST="$old_log" FAKE_VM_OUT="$(vmout "$msha" active)" run aws
rc=$?
[ "$rc" = 1 ] && grep -q "^NO: newest log (2026-10-31T09:20:00) is not newer than the service start" "$work/out" && ! grep -q "^NO: newest row" "$work/out" && r=ok || r=bad
check "aws: newest log older than the service start: exit 1 (rc $rc)" "$r"

V_PROOF=FAILED FAKE_LIST="$aws_list" FAKE_VM_OUT="$(V_PROOF=FAILED vmout "$msha" active)" run aws
rc=$?
[ "$rc" = 1 ] && grep -q "^NO: process maps do not show exactly 1 release dir" "$work/out" && r=ok || r=bad
check "aws: process-maps proof FAILED: exit 1 (rc $rc)" "$r"
FAKE_LIST="$aws_list" FAKE_VM_OUT="$(V_ROLL=present vmout "$msha" active)" run aws
rc=$?
[ "$rc" = 1 ] && grep -q "^NO: rollback marker is not absent (present)" "$work/out" && r=ok || r=bad
check "aws: rollback marker present: exit 1 (rc $rc)" "$r"
FAKE_LIST="$aws_list" FAKE_VM_OUT="$(V_CACHED=$other vmout "$msha" active)" run aws
rc=$?
[ "$rc" = 1 ] && grep -q "^NO: cached manifest ($other) differs from the store manifest" "$work/out" && r=ok || r=bad
check "aws: cached manifest differs from the store manifest: exit 1 (rc $rc)" "$r"
FAKE_LIST="$aws_list" FAKE_VM_OUT="$(V_RESTARTS=2 vmout "$msha" active)" run aws
rc=$?
[ "$rc" = 1 ] && grep -q "^NO: NRestarts is 2, not 0" "$work/out" && r=ok || r=bad
check "aws: NRestarts not 0: exit 1 and the count is printed (rc $rc)" "$r"

FAKE_SSM_FAIL=1 FAKE_LIST="$aws_list" FAKE_VM_OUT="" run aws
rc=$?
[ "$rc" = 1 ] && grep -q "release unavailable (ssm send-command failed)" "$work/err" && grep -q "AccessDeniedException" "$work/err" && r=ok || r=bad
check "aws: ssm send-command denied: exit 1 with the CLI message (rc $rc)" "$r"

: >"$work/log"
env FAKE_LOG="$work/log" FAKE_DIR="$work" FAKE_OUT_experiment=exp PATH="$work/bin:$PATH" bash "$script" aws >"$work/out" 2>"$work/err"
rc=$?
[ "$rc" = 1 ] && grep -q "cannot read output bucket_name" "$work/err" && grep -q "Output .bucket_name. not found" "$work/err" && ! grep -q '^aws ' "$work/log" && r=ok || r=bad
check "aws: a missing output stops with 1 line and no aws call (rc $rc)" "$r"

: >"$work/log"
env FAKE_LOG="$work/log" FAKE_DIR="$work" FAKE_TOFU_NOSTATE=1 PATH="$work/bin:$PATH" bash "$script" aws >"$work/out" 2>"$work/err"
rc=$?
[ "$rc" = 1 ] && grep -q "output experiment has no value" "$work/err" && ! grep -q '^aws ' "$work/log" && r=ok || r=bad
check "aws: a state with no outputs (warning on stdout, exit 0) is no value, no aws call (rc $rc)" "$r"

az_list='2026-10-31T10:05:00+00:00\texperiment=exp/provider=azure/probe_type=t/date=2026-10-31/20261031T100001Z_x-y_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n2026-10-31T10:40:00+00:00\texperiment=exp/provider=azure/probe_type=t/date=2026-10-31/20261031T100002Z_x-y_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n2026-10-31T10:20:00+00:00\texperiment=exp/logs/provider=azure/date=2026-10-31/20261031T100003Z_vmhost_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n'
FAKE_LIST="$az_list" FAKE_VM_OUT="$(vmout "$msha" active)" run azure
rc=$?
unscoped=$(grep -vc -- '--subscription sub-0' "$work/log")
grep -q "newest row: 2026-10-31T10:40:00+00:00.experiment=exp/provider=azure/probe_type=t/date=2026-10-31/20261031T100002Z_x-y_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl" "$work/out" \
  && grep -q "newest log: 2026-10-31T10:20:00+00:00.experiment=exp/logs/provider=azure/date=2026-10-31/20261031T100003Z_vmhost_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl" "$work/out" \
  && grep -q "azure manifest.json: sha256 $msha" "$work/out" && grep -q "service active: yes" "$work/out" \
  && [ "$rc" = 0 ] && [ "$unscoped" = 0 ] && r=ok || r=bad
check "azure: pass, exit 0, every az call scoped to the VM's subscription (rc $rc)" "$r"

FAKE_LIST="$az_list" FAKE_VM_OUT="$(vmout "$other" active)" run azure
rc=$?
[ "$rc" = 1 ] && grep -q "service active: NO" "$work/out" && r=ok || r=bad
check "azure: release differs from the manifest hash: exit 1 (rc $rc)" "$r"

FAKE_AZ_DENY=1 FAKE_LIST="$az_list" FAKE_VM_OUT="$(vmout "$msha" active)" run azure
rc=$?
[ "$rc" = 1 ] && grep -q "Storage Blob Data Reader" "$work/err" && grep -q "You do not have the required permissions" "$work/err" && r=ok || r=bad
check "azure: manifest read denied: exit 1 with the role hint and az's own message (rc $rc)" "$r"

AZURE_SUBSCRIPTION_ID=sub-9 FAKE_LIST="$az_list" FAKE_VM_OUT="$(vmout "$msha" active)" run azure
rc=$?
[ "$rc" = 1 ] && grep -q "differs from the subscription of the prober VM" "$work/err" && ! grep -q '^az' "$work/log" && r=ok || r=bad
check "azure: a target subscription other than the VM's is refused before any az call (rc $rc)" "$r"

# Spec 0003 sections 4.2, 4.3, 8.2: a daily file is not a row, a sweeper log
# (same key shape, other host) is not the prober's log, and every store time must be UTC.
daily='2026-10-31T10:50:00+00:00\texperiment=exp/daily/v1/date=2026-10-30/aws_spot_score_2026-10-30.jsonl.zst\n'
good_row='2026-10-31T09:30:00+00:00\texperiment=exp/provider=aws/probe_type=t/date=2026-10-31/20261031T093000Z_x-y_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n'
good_log='2026-10-31T10:20:00+00:00\texperiment=exp/logs/provider=aws/date=2026-10-31/20261031T102000Z_vmhost_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n'
sweep_log='2026-10-31T10:55:00+00:00\texperiment=exp/logs/provider=aws/date=2026-10-31/20261031T105500Z_sweeperhost_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n'
FAKE_LIST="$good_row$good_log$daily" FAKE_VM_OUT="$(vmout "$msha" active)" run aws
rc=$?
[ "$rc" = 1 ] && grep -q "^NO: newest row (2026-10-31T09:30:00) is not newer" "$work/out" && grep -q "newest row: 2026-10-31T09:30:00" "$work/out" && r=ok || r=bad
check "aws: a daily file is not the newest row: exit 1 (rc $rc)" "$r"
FAKE_LIST="$daily" FAKE_VM_OUT="$(vmout "$msha" active)" run aws
rc=$?
[ "$rc" = 1 ] && grep -q "^NO: no row object" "$work/out" && r=ok || r=bad
check "aws: only a daily file: no row object, exit 1 (rc $rc)" "$r"
fresh_row='2026-10-31T10:30:00+00:00\texperiment=exp/provider=aws/probe_type=t/date=2026-10-31/20261031T103000Z_x-y_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n'
old_log2='2026-10-31T09:20:00+00:00\texperiment=exp/logs/provider=aws/date=2026-10-31/20261031T092000Z_vmhost_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n'
FAKE_LIST="$fresh_row$old_log2$sweep_log" FAKE_VM_OUT="$(vmout "$msha" active)" run aws
rc=$?
[ "$rc" = 1 ] && grep -q "^NO: newest log (2026-10-31T09:20:00) is not newer" "$work/out" && r=ok || r=bad
check "aws: a sweeper log (other host) does not meet the log check: exit 1 (rc $rc)" "$r"
FAKE_LIST="$fresh_row$sweep_log" FAKE_VM_OUT="$(vmout "$msha" active)" run aws
rc=$?
[ "$rc" = 1 ] && grep -q "^NO: no log object" "$work/out" && r=ok || r=bad
check "aws: only a sweeper log: no log object, exit 1 (rc $rc)" "$r"
FAKE_LIST="$fresh_row$good_log" FAKE_VM_OUT="$(vmout "$msha" active)" run aws
rc=$?
[ "$rc" = 0 ] && r=ok || r=bad
check "aws: a fresh row and a fresh prober log: exit 0 (rc $rc)" "$r"
az_row='2026-10-31T10:30:00+00:00\texperiment=exp/provider=azure/probe_type=t/date=2026-10-31/20261031T103000Z_x-y_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n'
az_sweep='2026-10-31T10:55:00+00:00\texperiment=exp/logs/provider=azure/date=2026-10-31/20261031T105500Z_sweeperhost_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl\n'
FAKE_LIST="$az_row$az_sweep" FAKE_VM_OUT="$(vmout "$msha" active)" run azure
rc=$?
[ "$rc" = 1 ] && grep -q "^NO: no log object" "$work/out" && r=ok || r=bad
check "azure: a sweeper log shares logs/provider=azure/ but does not count: exit 1 (rc $rc)" "$r"
FAKE_LIST="$(printf '%s' "$fresh_row" | sed 's/+00:00/-04:00/')$good_log" FAKE_VM_OUT="$(vmout "$msha" active)" run aws
rc=$?
[ "$rc" = 1 ] && grep -q "^NO: a row object time is not UTC" "$work/out" && r=ok || r=bad
check "aws: a row time with another offset: NO line, exit 1 (rc $rc)" "$r"
FAKE_LIST="$(printf '%s' "$fresh_row" | sed 's/+00:00/Z/')$(printf '%s' "$good_log" | sed 's/+00:00/Z/')" FAKE_VM_OUT="$(vmout "$msha" active)" run aws
rc=$?
[ "$rc" = 0 ] && r=ok || r=bad
check "aws: store times that end in Z pass (rc $rc)" "$r"

run nope
rc=$?
[ "$rc" = 2 ] && r=ok || r=bad
check "an unknown cloud: usage, exit 2 (rc $rc)" "$r"

exit $fail
