#!/usr/bin/env bash
# One quiet command to run the board locally with fresh data: `make dev` (or `mise run dev`).
# 1. Sync the rows (download only) of each deployed cloud. `make data/sync` reads the bucket and
#    storage account names from the stack outputs (`tofu output -raw`).
# 2. Rebuild the database, start the board.
# The names are never printed: the sync writes its output to a log file in a temp folder, and the
# script prints a line about it only when the sync fails.
#
# Environment:
#   AWS_PROFILE   the AWS CLI profile; used in the login line
set -u

here=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/ftg-dev.XXXXXX") || exit 1
trap 'rm -rf "$work"' EXIT
cd "$here" || exit 1

# The AWS read and the AWS sync need a login: an expired login skips aws.
clouds="aws azure"
if ! aws sts get-caller-identity >/dev/null 2>&1; then
  clouds="azure"
  echo "dev: AWS login expired; run: aws sso login --profile ${AWS_PROFILE:-<profile>}"
fi

# Download only: data/sync mirrors with `aws s3 sync` and `azcopy copy`, and deletes nothing.
# It skips a cloud with no deployment, and fails only when no cloud has one.
make -s data/sync DATA_CLOUDS="$clouds" >"$work/sync.log" 2>&1
rc=$?
grep '^data/sync: ' "$work/sync.log"
if [ "$rc" -ne 0 ]; then
  echo "dev: the sync failed (exit $rc); the board starts with the local data. Run 'make data/sync' to see why."
fi

make -s web/deps >"$work/deps.log" 2>&1 || { cat "$work/deps.log" >&2; exit 1; }
make -s web/db >"$work/db.log" 2>&1 || { cat "$work/db.log" >&2; exit 1; }
# The temp folder goes before the board starts.
rm -rf "$work"
# The board runs in the foreground of this terminal: Vite keeps stdin, so its keys work. Ctrl-C stops
# Vite and the make below, and this script exits 0, so the make above it prints no `Error 130` and no
# `Interrupt` line. A shell with a trap waits for its child first, then runs the trap.
# -o: the database is built already; web/run does not build it again.
trap 'exit 0' INT
make -s -o web/db web/run WEB_QUIET=1
exit $?
