#!/usr/bin/env bash
# terraform/azure/scripts/verify-sweeper.sh passes when the newest code_sha in the store
# log equals the sweeper_code_sha output, and fails when it differs or when none shows up.
# A fake tofu and a fake az on PATH stand in for the real ones. Their output shapes follow the
# real commands: `az storage blob list --query ... -o tsv` prints one "name<TAB>modified" row per
# blob (with the query ending in [name, modified]), and `az storage blob download --file F -o none`
# writes the blob to F and prints nothing.
# No network, no credentials. Run: make test/verify-sweeper
set -u

here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/ftg-verify.XXXXXX")
trap 'rm -rf "$work"' EXIT INT TERM
mkdir "$work/bin"
# A temporary root holds the script and an empty azure stack folder. The fake tofu is on PATH.
mkdir -p "$work/root/terraform/azure/scripts" "$work/root/scripts"
cp "$here/../azure/scripts/verify-sweeper.sh" "$work/root/terraform/azure/scripts/"
script="$work/root/terraform/azure/scripts/verify-sweeper.sh"

good=$(printf 'a%.0s' $(seq 64))
bad=$(printf 'b%.0s' $(seq 64))

cat >"$work/bin/tofu" <<'FAKE'
#!/usr/bin/env bash
if [ "${FAKE_TOFU_MODE:-}" = none ]; then
  # Real OpenTofu 1.12 and 1.13 in a dir with no state: `output -raw X` exits 0 and prints the
  # warning on STDOUT (stderr stays empty).
  printf '\nWarning: No outputs found\n\nThe state file either has no outputs defined, or all the defined outputs\nare empty. Please define an output in your configuration with the `output`\nkeyword and run `tofu refresh` for it to become available.\n'
  exit 0
fi
if [ "${FAKE_TOFU_MODE:-}" = missing ]; then
  # Real OpenTofu with state that lacks the output: exit 1, the error on STDERR, stdout empty.
  printf 'Error: Output "%s" not found\n\nThe output variable requested could not be found in the state file.\n' "${@: -1}" >&2
  exit 1
fi
case "${@: -1}" in
  prober_vm_id) printf '%s' "/subscriptions/sub-0/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm" ;;
  data_storage_account) printf 'acct' ;;
  experiment) printf '%s' "${FAKE_EXPERIMENT:-exp}" ;;
  sweeper_code_sha) printf '%s' "$FAKE_EXPECTED" ;;
esac
FAKE
cat >"$work/bin/az" <<'FAKE'
#!/usr/bin/env bash
echo "$*" >>"$FAKE_AZ_LOG"
if [ -n "${FAKE_AZ_FAIL:-}" ] || { [ -n "${FAKE_AZ_FAIL_DOWNLOAD:-}" ] && [ "$3" = download ]; }; then
  # Download failure in the real az: BlobNotFound or 403 print "ERROR: ..." on stderr, exit 1, no file.
  # What the real az 2.90 does for a 403 AuthorizationPermissionMismatch (azure-cli
  # command_modules/storage/__init__.py get_handler_suppress_some_400 sets the message, and
  # core/util.py handle_exception exits 1 after azclierror print_error writes "ERROR: <message>"
  # to stderr). Stdout stays empty.
  printf 'ERROR: \nYou do not have the required permissions needed to perform this operation.\n' >&2
  exit 1
fi
case "$1 $2 $3" in
  "storage blob list")
    # The prefix ends in date=YYYY-MM-DD/. The fake keeps one list for today and one for yesterday.
    # The query ends in [name, modified] with -o tsv: one "name<TAB>modified" row per blob.
    m="${FAKE_MODIFIED:-2099-01-01T00:00:00+00:00}"
    if [ -n "${FAKE_LIST_RAW:-}" ]; then printf "$FAKE_LIST_RAW"; exit 0; fi
    case "$*" in
      *"date=$FAKE_TODAY/"*) for b in $FAKE_BLOBS; do printf '%s\t%s\n' "$b" "$m"; done ;;
      *) for b in ${FAKE_BLOBS_YESTERDAY:-}; do printf '%s\t%s\n' "$b" "$m"; done ;;
    esac ;;
  "storage blob download")
    # A real az can read stdin; the script must give it </dev/null.
    [ -n "${FAKE_AZ_READ_STDIN:-}" ] && cat >/dev/null
    while [ $# -gt 0 ]; do
      [ "$1" = --name ] && name="$2"
      [ "$1" = --file ] && file="$2"
      shift
    done
    cat "$FAKE_DIR/$name" >"$file" ;;
esac
exit 0
FAKE
chmod +x "$work/bin/tofu" "$work/bin/az"

# The newest blob is the prober's (no code_sha); the sweeper's blob is older.
printf '{"Body":"probe done"}\n' >"$work/prober.jsonl"
printf '{"Body":"sweeper code_sha=%s"}\n' "$good" >"$work/sweep-new.jsonl"
printf '{"Body":"sweeper code_sha=%s"}\n' "$bad" >"$work/sweep-old.jsonl"
printf '{"Body":"sweeper started"}\n' >"$work/sweep-ancient.jsonl"

fail=0
check() {
  if [ "$2" = ok ]; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi
}
today=$(date -u +%Y-%m-%d)
run() { # blobs, expected sha; FAKE_BLOBS_YESTERDAY and SWEEPER_CODE_SHA are optional env
  : >"$work/az.log"
  FAKE_TODAY="$today" FAKE_BLOBS="$1" FAKE_EXPECTED="$2" FAKE_DIR="$work" FAKE_AZ_LOG="$work/az.log" \
    FTG_VERIFY_WAIT_SECONDS=0 FTG_VERIFY_INTERVAL=0 AZURE_SUBSCRIPTION_ID=sub-0 \
    PATH="$work/bin:$PATH" bash "$script" >"$work/out" 2>"$work/err"
}

run "prober.jsonl sweep-new.jsonl" "$good"
rc=$?
unscoped=$(grep -vc -- '--subscription sub-0$' "$work/az.log")
[ "$rc" = 0 ] && [ "$unscoped" = 0 ] && grep -q '^ok' "$work/out" && r=ok || r=bad
check "the running code_sha equals the state hash: pass, every az call scoped (rc $rc)" "$r"

run "prober.jsonl sweep-old.jsonl" "$good"
rc=$?
[ "$rc" != 0 ] && grep -q "FAIL: the running sweeper has code_sha $bad" "$work/err" && r=ok || r=bad
check "an old code_sha (failed deploy, clean plan): fail (rc $rc)" "$r"

run "prober.jsonl sweep-ancient.jsonl" "$good"
rc=$?
[ "$rc" != 0 ] && grep -q "no sweeper log with a code_sha" "$work/err" && r=ok || r=bad
check "a log with no code_sha (code from before the fix): fail (rc $rc)" "$r"

run "" "$good"
rc=$?
[ "$rc" != 0 ] && grep -q "no sweeper log with a code_sha" "$work/err" && r=ok || r=bad
check "no log object at all: fail (rc $rc)" "$r"

FAKE_AZ_FAIL=1 run "prober.jsonl sweep-new.jsonl" "$good"
rc=$?
[ "$rc" = 2 ] && grep -q "unknown, rerun: az storage blob list failed" "$work/err" \
  && grep -q "You do not have the required permissions" "$work/err" \
  && ! grep -q "no sweeper log with a code_sha" "$work/err" && r=ok || r=bad
check "an az call fails (403): fail at once with az's own message, no wait (rc $rc)" "$r"

FAKE_AZ_FAIL_DOWNLOAD=1 run "prober.jsonl sweep-new.jsonl" "$good"
rc=$?
[ "$rc" = 2 ] && grep -q "unknown, rerun: az storage blob download failed" "$work/err" \
  && ! grep -q "no sweeper log with a code_sha" "$work/err" && ! grep -q "^FAIL" "$work/err" && r=ok || r=bad
check "a blob download fails: unknown, rerun, exit 2, no wait (rc $rc)" "$r"

FAKE_TOFU_MODE=missing run "prober.jsonl sweep-new.jsonl" "$good"
rc=$?
[ "$rc" = 2 ] && grep -q "unknown, rerun: reading output data_storage_account failed" "$work/err" \
  && r=ok || r=bad
check "an output is missing: the message of tofu, exit 2 (rc $rc)" "$r"

FAKE_TOFU_MODE=none run "prober.jsonl sweep-new.jsonl" "$good"
rc=$?
[ "$rc" = 2 ] && grep -q "the value of data_storage_account is empty or malformed" "$work/err" \
  && grep -q "is empty or malformed" "$work/err" && ! grep -q "az storage" "$work/err" && r=ok || r=bad
check "tofu exits 0 with a warning on stdout (no state): the script refuses it, exit 2, no az call (rc $rc)" "$r"

run "prober.jsonl sweep-new.jsonl" ""
rc=$?
[ "$rc" = 2 ] && grep -q "the value of sweeper_code_sha is empty or malformed" "$work/err" && r=ok || r=bad
check "an empty expected hash must not pass: exit 2 (rc $rc)" "$r"

short=$(printf 'a%.0s' $(seq 63))
run "prober.jsonl sweep-new.jsonl" "$short"
rc=$?
[ "$rc" = 2 ] && grep -q "value of sweeper_code_sha is empty or malformed" "$work/err" && r=ok || r=bad
check "a 63-character SWEEPER_CODE_SHA: exit 2 (rc $rc)" "$r"

FAKE_EXPERIMENT="two words" run "prober.jsonl sweep-new.jsonl" "$good"
rc=$?
[ "$rc" = 2 ] && grep -q "the value of experiment is empty or malformed" "$work/err" && r=ok || r=bad
check "an experiment with whitespace: exit 2 (rc $rc)" "$r"

printf '{"Body":"sweeper code_sha=%sa"}\n' "$good" >"$work/sweep-65.jsonl"
run "prober.jsonl sweep-65.jsonl" "$good"
rc=$?
[ "$rc" = 1 ] && ! grep -q '^ok' "$work/out" && grep -q "no sweeper log with a code_sha" "$work/err" && r=ok || r=bad
check "65 hex characters after code_sha= do not match on the first 64: fail (rc $rc)" "$r"

printf 'sweeper code_sha=%s' "$good" >"$work/sweep-eof.jsonl"
run "sweep-eof.jsonl" "$good"
rc=$?
[ "$rc" = 0 ] && r=ok || r=bad
check "code_sha at the very end of the object, no newline: pass (rc $rc)" "$r"

# Just after 00:00 UTC today's prefix holds no sweeper log; yesterday's newest does.
FAKE_BLOBS_YESTERDAY="sweep-new.jsonl" run "prober.jsonl" "$good"
rc=$?
[ "$rc" = 0 ] && grep -q '^ok' "$work/out" && r=ok || r=bad
check "no sweeper log today yet, yesterday's holds it: pass (rc $rc)" "$r"

FAKE_BLOBS_YESTERDAY="sweep-ancient.jsonl" run "prober.jsonl" "$good"
rc=$?
[ "$rc" = 1 ] && grep -q "in the newest 30 log objects of today or yesterday" "$work/err" && r=ok || r=bad
check "no code_sha today or yesterday: honest window in the message (rc $rc)" "$r"

# a newline in a value is malformed (a value with white space is not an output value); a 65th upper-case hex character is not a match.
run "prober.jsonl sweep-new.jsonl" "$(printf '%s\nWarning' "$good")"
rc=$?
[ "$rc" = 2 ] && grep -q "the value of sweeper_code_sha is empty or malformed" "$work/err" && r=ok || r=bad
check "a hash value with a newline: exit 2 (rc $rc)" "$r"

printf '{"Body":"sweeper code_sha=%sA"}\n' "$good" >"$work/sweep-65u.jsonl"
run "prober.jsonl sweep-65u.jsonl" "$good"
rc=$?
[ "$rc" = 1 ] && ! grep -q '^ok' "$work/out" && r=ok || r=bad
check "an upper-case hex 65th character does not match on the first 64: fail (rc $rc)" "$r"

# APPLY_END_UTC: the matching log must be from after the apply.
APPLY_END_UTC=2026-06-01T12:00:00Z FAKE_MODIFIED=2026-06-01T11:59:59+00:00 run "prober.jsonl sweep-new.jsonl" "$good"
rc=$?
[ "$rc" = 1 ] && ! grep -q '^ok' "$work/out" && grep -q "not proven after the apply" "$work/err" && r=ok || r=bad
check "right hash, log older than the apply end: fail, not proven (rc $rc)" "$r"

APPLY_END_UTC=2026-06-01T12:00:00Z FAKE_MODIFIED=2026-06-01T12:00:01+00:00 run "prober.jsonl sweep-new.jsonl" "$good"
rc=$?
[ "$rc" = 0 ] && grep -q '^ok' "$work/out" && r=ok || r=bad
check "right hash, log newer than the apply end: pass (rc $rc)" "$r"

APPLY_END_UTC=yesterday run "prober.jsonl sweep-new.jsonl" "$good"
rc=$?
[ "$rc" = 2 ] && grep -q "APPLY_END_UTC must look like" "$work/err" && r=ok || r=bad
check "a malformed APPLY_END_UTC: exit 2 (rc $rc)" "$r"

# refuse a non-UTC lastModified, a tab in a blob name, and a stdin read in the list loop.
APPLY_END_UTC=2026-06-01T12:00:00Z FAKE_MODIFIED=2026-06-01T08:00:01-04:00 run "prober.jsonl sweep-new.jsonl" "$good"
rc=$?
[ "$rc" = 2 ] && ! grep -q '^ok' "$work/out" && grep -q "not UTC" "$work/err" && r=ok || r=bad
check "a lastModified with an offset other than +00:00: exit 2 (rc $rc)" "$r"

APPLY_END_UTC=2026-06-01T12:00:00Z FAKE_MODIFIED=2026-06-01T12:00:01Z run "prober.jsonl sweep-new.jsonl" "$good"
rc=$?
[ "$rc" = 0 ] && r=ok || r=bad
check "a lastModified that ends in Z: pass (rc $rc)" "$r"

FAKE_LIST_RAW='sweep-new.jsonl\tx\t2099-01-01T00:00:00+00:00\n' run "" "$good"
rc=$?
[ "$rc" = 2 ] && ! grep -q '^ok' "$work/out" && grep -q "not name<TAB>modified" "$work/err" && r=ok || r=bad
check "a tab in a blob name (3 fields): exit 2 (rc $rc)" "$r"

FAKE_LIST_RAW='sweep-new.jsonl\t\tx\n' run "" "$good"
rc=$?
[ "$rc" = 2 ] && ! grep -q '^ok' "$work/out" && r=ok || r=bad
check "a doubled tab after a short name: exit 2, the short name is not used (rc $rc)" "$r"

FAKE_AZ_READ_STDIN=1 run "prober.jsonl sweep-new.jsonl" "$good"
rc=$?
[ "$rc" = 0 ] && grep -q '^ok' "$work/out" && r=ok || r=bad
check "az reads stdin in the list loop: the list is not eaten (rc $rc)" "$r"

exit $fail
