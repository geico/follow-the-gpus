#!/usr/bin/env bash
# Test `make data/sync` and scripts/dev.sh with a fake tofu, a fake aws, and a fake make. No network,
# no cloud credentials, no server. It checks that:
#   - data/sync reads the bucket and the storage account from the stack outputs, with
#     `tofu output -raw`, and passes them to the sync; it never prints them;
#   - a cloud with no deployment (a missing output, or a state with no outputs) is skipped with
#     1 line; with no cloud deployed, data/sync fails with 1 line;
#   - dev.sh syncs both clouds; an expired AWS login gives exactly 1 line, the `aws sso login
#     --profile` command, and the sync reads azure only; a failed sync does not stop the board;
#   - Ctrl-C in a pseudo-terminal stops the board with no make line, and nothing survives.
# The fake tofu copies the real `tofu output -no-color -raw NAME` (OpenTofu 1.12, compared on
# 2026-11-03 in a root with 1 output, bucket_name):
#   - present output: exit 0, the value on stdout with no newline, nothing on stderr;
#   - missing output: exit 1, nothing on stdout, a blank line and "Error: Output "NAME" not found"
#     and an explanation on stderr;
#   - state with no outputs: exit 0, "\nWarning: No outputs found ..." on stdout.
# The fake aws copies `aws sts get-caller-identity` with an expired SSO token: nothing on stdout,
# 1 error line on stderr, exit 255. Set DEV_SCRIPT and RUN_SCRIPT to test other scripts.
# shellcheck disable=SC2016,SC2086
set -u
here=$(cd "$(dirname "$0")" && pwd)
script="${DEV_SCRIPT:-$here/dev.sh}"
run_script="${RUN_SCRIPT:-$here/../web/scripts/run.sh}"
work=$(mktemp -d "${TMPDIR:-/tmp}/ftg-dev-test.XXXXXX")
trap 'rm -rf "$work"' EXIT
fail=0
bad() { echo "FAIL $1" >&2; fail=1; }

bucket=fake-bucket-7f3a
account=fakeaccount7f3a
mkdir -p "$work/bin" "$work/fm"
export FAKE_CALLS="$work/calls.log"

# FAKE_TOFU_AWS and FAKE_TOFU_AZURE: ok (the output is present), missing, or empty (no outputs).
cat >"$work/bin/tofu" <<TOFU_EOF
#!/usr/bin/env bash
echo "tofu \$*" >>"\$FAKE_CALLS"
cloud=\${1##*/}
case "\$2" in init) exit 0 ;; esac
[ "\$2 \$3 \$4" = "output -no-color -raw" ] || exit 2
case "\$cloud" in aws) mode=\${FAKE_TOFU_AWS:-ok}; val=$bucket ;; *) mode=\${FAKE_TOFU_AZURE:-ok}; val=$account ;; esac
case "\$mode" in
  ok) printf '%s' "\$val" ;;
  missing)
    printf '\nError: Output "%s" not found\n\nThe output variable requested could not be found in the state file. If you\nrecently added this to your configuration, be sure to run \`tofu apply\`.\n' "\$5" >&2
    exit 1 ;;
  empty) printf '\nWarning: No outputs found\n\nThe state file either has no outputs defined.\n' ;;
esac
TOFU_EOF
cat >"$work/bin/aws" <<'AWS_EOF'
#!/usr/bin/env bash
echo "aws $*" >>"$FAKE_CALLS"
if [ "${FAKE_AWS_EXPIRED:-}" = 1 ]; then
  echo "Error loading SSO Token: Token for the session has expired" >&2
  exit 255
fi
printf '%s\n' fake-account
AWS_EOF
# The fake make (in its own folder, so the sync test uses the real make) records the goals and the clouds; it prints nothing, or fails when FAKE_MAKE_FAIL=1.
cat >"$work/fm/make" <<'MAKE_EOF'
#!/usr/bin/env bash
echo "make $*" >>"$FAKE_CALLS"
if [ "${FAKE_MAKE_FAIL:-}" = 1 ] && [ "$2" = data/sync ]; then
  echo "data/sync: skipping aws: no bucket_name output in terraform/aws (not applied)"
  echo "data/sync: no cloud is deployed; run make plan/aws and make apply/aws, or the same for azure" >&2
  exit 2
fi
MAKE_EOF
chmod +x "$work/bin/tofu" "$work/bin/aws" "$work/fm/make"

calls() { cat "$FAKE_CALLS"; }
has() { calls | grep -qF -- "$1"; }
shows_value() { grep -qF -e "$bucket" -e "$account" "$work/$1.out"; }

# A. data/sync in a scratch repo: the real Makefile.data, the fake tofu, and a stub
# data-sync.sh that records the cloud and the name it got.
sr="$work/sync"
mkdir -p "$sr/tasks" "$sr/scripts" "$sr/terraform/aws" "$sr/terraform/azure"
git init -q "$sr"
cp "$here/../tasks/Makefile.data" "$sr/tasks/"
printf '#!/usr/bin/env bash\nif [ "$1" = aws ]; then echo "sync aws $AWS_DATA_BUCKET"; else echo "sync azure $AZURE_DATA_ACCOUNT"; fi >>"$FAKE_CALLS"\n' >"$sr/scripts/data-sync.sh"
chmod +x "$sr/scripts/data-sync.sh"
printf 'MISE_EXEC =\nAWS_TF = tofu -chdir=terraform/aws\nAZURE_TF = tofu -chdir=terraform/azure\ninclude tasks/Makefile.data\n' >"$sr/Makefile"
sync() { # NAME [VAR=value ...]: run make data/sync with a clean environment
  local n=$1; shift
  : >"$FAKE_CALLS"
  ( cd "$sr" && env -i PATH="$work/bin:$(dirname "$(command -v python3)"):/usr/bin:/bin" HOME="$work" FAKE_CALLS="$FAKE_CALLS" "$@" make -s data/sync ) >"$work/$n.out" 2>&1
  echo $? >"$work/$n.rc"
}
rc() { cat "$work/$1.rc"; }

# A2. Both deployed: both names reach the sync; the output shows no name.
sync both
[ "$(rc both)" = 0 ] || bad "both: exit $(rc both): $(cat "$work/both.out")"
has "sync aws $bucket" || bad "both: the sync did not get the bucket: $(calls)"
has "sync azure $account" || bad "both: the sync did not get the account: $(calls)"
has "terraform/aws output -no-color -raw bucket_name" || bad "both: no bucket output read: $(calls)"
has "terraform/azure output -no-color -raw data_storage_account" || bad "both: no account output read: $(calls)"
shows_value both && bad "both: the output shows a name"

# A3. AWS output missing: 1 skip line with the error as returned, azure still syncs.
sync awsmissing FAKE_TOFU_AWS=missing
[ "$(rc awsmissing)" = 0 ] || bad "awsmissing: exit $(rc awsmissing)"
grep -q '^data/sync: skipping aws: ' "$work/awsmissing.out" || bad "awsmissing: no skip line: $(cat "$work/awsmissing.out")"
grep -q 'bucket_name' "$work/awsmissing.out" || bad "awsmissing: the skip line lost the cause: $(cat "$work/awsmissing.out")"
has "sync azure $account" || bad "awsmissing: azure was not synced: $(calls)"
has "sync aws" && bad "awsmissing: aws was synced"

# A4. A state with no outputs (tofu prints a warning and exits 0): skipped, not read as a name.
sync azempty FAKE_TOFU_AZURE=empty
grep -q '^data/sync: skipping azure: ' "$work/azempty.out" || bad "azempty: no skip line: $(cat "$work/azempty.out")"
has "sync azure" && bad "azempty: the warning was taken as a name"
has "sync aws $bucket" || bad "azempty: aws was not synced: $(calls)"

# A5. Neither deployed: exit 2 with a last line that says what to do; nothing synced.
sync none FAKE_TOFU_AWS=missing FAKE_TOFU_AZURE=missing
[ "$(rc none)" = 2 ] || bad "none: exit $(rc none)"
grep -q '^data/sync: no cloud is deployed; run make plan/aws and make apply/aws' "$work/none.out" || bad "none: wrong last line: $(cat "$work/none.out")"
has "sync " && bad "none: something was synced"

# A6. DATA_CLOUDS=azure reads azure only.
sync azonly DATA_CLOUDS=azure
has "terraform/aws" && bad "azonly: aws was read"
has "sync azure $account" || bad "azonly: azure was not synced"

# B. dev.sh with the fake make. dev.sh finds its folder from its own place: copy it into a scratch repo.
root="$work/p"
internal="$root/repo"
mkdir -p "$internal/scripts" "$internal/terraform"
git init -q "$internal"
cp "$script" "$internal/scripts/dev.sh"
run() { # NAME [VAR=value ...]: run dev.sh with a clean environment; stdout and stderr go to $work/NAME.out
  local n=$1; shift
  : >"$FAKE_CALLS"
  env -i PATH="$work/fm:$work/bin:/usr/bin:/bin" HOME="$work" TMPDIR="$work/" AWS_PROFILE=p FAKE_CALLS="$FAKE_CALLS" "$@" \
    bash "$internal/scripts/dev.sh" >"$work/$n.out" 2>&1
  echo $? >"$work/$n.rc"
}

# B1. Logged in: both clouds go to the sync, the output is empty, the board starts.
run login
has "make -s data/sync DATA_CLOUDS=aws azure" || bad "login: data/sync did not get both clouds: $(calls)"
[ -z "$(cat "$work/login.out")" ] || bad "login: expected no output: $(cat "$work/login.out")"
has "make -s -o web/db web/run WEB_QUIET=1" || bad "login: the board did not start"
[ "$(rc login)" = 0 ] || bad "login: exit $(rc login)"

# B2. Expired AWS login: exactly 1 line, the sync reads azure only, the board starts.
run expired FAKE_AWS_EXPIRED=1
[ "$(cat "$work/expired.out")" = "dev: AWS login expired; run: aws sso login --profile p" ] || bad "expired: wrong output: $(cat "$work/expired.out")"
has "make -s data/sync DATA_CLOUDS=azure" || bad "expired: the sync did not drop aws: $(calls)"
has "web/run" || bad "expired: the board did not start"

# B3. A failed sync (no deployment): the cause lines reach the user, the board starts with the local data.
run failed FAKE_MAKE_FAIL=1
grep -q '^data/sync: skipping aws' "$work/failed.out" || bad "failed: the skip line was hidden: $(cat "$work/failed.out")"
grep -q '^dev: the sync failed (exit 2)' "$work/failed.out" || bad "failed: no sync failed line: $(cat "$work/failed.out")"
has "web/run" || bad "failed: the board did not start"

# B4. No temp folder of the script remains after the run.
left=$(ls -d "$work"/ftg-dev.* 2>/dev/null)
[ -z "$left" ] || bad "tempdir: dev.sh left a temp folder: $left"

# 8. web/scripts/run.sh with WEB_QUIET does not hide Vite's output: no --logLevel flag, no line of its own.
mkdir -p "$work/w/scripts" "$work/w/node_modules/.bin"
cp "$run_script" "$work/w/scripts/run.sh"
printf '#!/usr/bin/env bash\necho "vite $*"\n' >"$work/w/node_modules/.bin/vite"
chmod +x "$work/w/node_modules/.bin/vite"
WEB_QUIET=1 WEB_DB=x bash "$work/w/scripts/run.sh" >"$work/vite.out" 2>&1
grep -q '^vite --host 127.0.0.1' "$work/vite.out" || bad "run.sh: vite did not start: $(cat "$work/vite.out")"
grep -q logLevel "$work/vite.out" && bad "run.sh: WEB_QUIET still passes --logLevel"
grep -q 'run.sh:' "$work/vite.out" && bad "run.sh: WEB_QUIET printed a line of its own"

# 10. Ctrl-C in a pseudo-terminal. A scratch repo has the real make, the real dev.sh and run.sh, and a fake
# Vite that prints a banner and then waits like Vite (it dies of SIGINT). SIGINT goes to the foreground process group,
# as the Ctrl-C key does. The output must hold no make line, the board process must be gone, and the status is 0 or 130.
if command -v python3 >/dev/null && command -v make >/dev/null; then
  pr="$work/pty"
  mkdir -p "$pr/terraform" "$pr/scripts" "$pr/web/scripts" "$pr/web/node_modules/.bin" "$pr/bin"
  git init -q "$pr"
  cp "$script" "$pr/scripts/dev.sh"
  cp "$run_script" "$pr/web/scripts/run.sh"
  printf '#!/bin/sh\necho "  Local: http://127.0.0.1:$4/"\necho $$ >"$PIDFILE"\nexec sleep 300\n' >"$pr/web/node_modules/.bin/vite"
  printf '#!/bin/sh\nexit 0\n' >"$pr/bin/aws"
  chmod +x "$pr/web/node_modules/.bin/vite" "$pr/bin/aws" "$pr/scripts/dev.sh" "$pr/web/scripts/run.sh"
  printf 'dev:\n\t@scripts/dev.sh\nweb/deps web/db data/sync:\n\t@:\nweb/run:\n\t@WEB_QUIET="$(WEB_QUIET)" WEB_DB=x WEB_PORT=4199 web/scripts/run.sh\n.PHONY: dev web/deps web/db web/run data/sync\n' >"$pr/Makefile"
  cat >"$work/pty.py" <<'PY_EOF'
import os, pty, re, select, signal, sys, time
cmd = sys.argv[1:]
pid, fd = pty.fork()
if pid == 0:
    os.execvp(cmd[0], cmd)
buf, sent, end = b"", False, time.time() + 30
while time.time() < end:
    if select.select([fd], [], [], 0.2)[0]:
        try:
            d = os.read(fd, 4096)
        except OSError:
            break
        if not d:
            break
        buf += d
    if not sent and re.search(rb"Local:", buf):
        time.sleep(0.5)
        os.killpg(os.tcgetpgrp(fd), signal.SIGINT)
        sent, end = True, time.time() + 15
for _ in range(100):
    w, st = os.waitpid(pid, os.WNOHANG)
    if w:
        break
    time.sleep(0.1)
else:
    os.kill(pid, 9)
    os.waitpid(pid, 0)
    st = 9
sys.stdout.write(buf.decode(errors="replace").replace("\r", ""))
print("status=%d" % (128 + os.WTERMSIG(st) if os.WIFSIGNALED(st) else os.WEXITSTATUS(st)))
PY_EOF
  hows=("make dev WEB_QUIET=1" "make web/run WEB_QUIET=1")
  # The mise task web: the scratch repo gets the task of the real mise.toml.
  if command -v mise >/dev/null; then
    { echo '[tasks.web]'; sed -n '/^\[tasks.web\]/,/^$/p' "$here/../mise.toml" | sed 1d; } >"$pr/mise.toml"
    hows+=("mise run web")
    misedir=$(dirname "$(command -v mise)")
  fi
  for how in "${hows[@]}"; do
    n=$(echo "$how" | tr -c 'a-z\n' _)
    ( cd "$pr" && rm -f "$work/vite.pid" && env -i PATH="$pr/bin:${misedir:-/usr/bin}:/usr/bin:/bin" HOME="$work" TMPDIR="$work/" PIDFILE="$work/vite.pid" MISE_TRUSTED_CONFIG_PATHS="$pr" python3 -I "$work/pty.py" $how >"$work/pty-$n.out" 2>&1 )
    grep -q 'Local:' "$work/pty-$n.out" || bad "$how: the board did not start: $(cat "$work/pty-$n.out")"
    grep -qE 'make|mise|\*\*\*|Error|ERROR|Interrupt|exit status' "$work/pty-$n.out" && bad "$how: Ctrl-C printed a make line: $(cat "$work/pty-$n.out")"
    case "$(tail -n 1 "$work/pty-$n.out")" in status=0 | status=130) ;; *) bad "$how: wrong status: $(tail -n 1 "$work/pty-$n.out")" ;; esac
    if [ -s "$work/vite.pid" ] && kill -0 "$(cat "$work/vite.pid")" 2>/dev/null; then
      bad "$how: the board process survived"
      kill "$(cat "$work/vite.pid")" 2>/dev/null
    fi
  done
fi

# 11. TERM and HUP to run.sh stop Vite too: a fake Vite that waits; the script gets the signal.
for sig in TERM HUP; do
  rm -f "$work/vite.pid"
  printf '#!/usr/bin/env bash\necho $$ >"$PIDFILE"\nexec sleep 300\n' >"$work/w/node_modules/.bin/vite"
  PIDFILE="$work/vite.pid" WEB_QUIET=1 WEB_DB=x bash "$work/w/scripts/run.sh" >"$work/sig.out" 2>&1 &
  rpid=$!
  for _ in $(seq 100); do [ -s "$work/vite.pid" ] && break; sleep 0.1; done
  kill -"$sig" "$rpid"
  wait "$rpid"
  sleep 0.3
  if kill -0 "$(cat "$work/vite.pid")" 2>/dev/null; then
    bad "run.sh: $sig left Vite running"
    kill "$(cat "$work/vite.pid")" 2>/dev/null
  fi
done

[ "$fail" = 0 ] && echo "test-dev: ok"
exit "$fail"
