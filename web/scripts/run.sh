#!/usr/bin/env bash
# Run the board on a laptop (spec 0006 section 8.3): the Vite dev server on 127.0.0.1 with the API
# plugin (web/server/), which answers api/meta.json and api/window/<hz>/<page>.json from the
# DuckDB file WEB_DB. There is no second server. The server reads the file again after
# `make web/db` replaced it, so a new snapshot needs no restart.
#
# Environment: WEB_DB (required, the database file), WEB_PORT (default 4173), WEB_QUIET (set: the script
# prints no line of its own; Vite prints its normal banner with the Local URL, and its errors).
# Needs node on PATH and the npm packages in web/node_modules: use `make web/run`.
set -u

here=$(cd "$(dirname "$0")" && pwd)
web=$(cd "$here/.." && pwd)
port=${WEB_PORT:-4173}

case "$port" in
  '' | *[!0-9]*)
    echo "run.sh: WEB_PORT must be a port number" >&2
    exit 2
    ;;
esac
[ -n "${WEB_DB:-}" ] || {
  echo "run.sh: set WEB_DB to the database file; use 'make web/run'" >&2
  exit 2
}
vite="$web/node_modules/.bin/vite"
[ -x "$vite" ] || {
  echo "run.sh: $vite not found; run 'make web/deps' first" >&2
  exit 1
}

if [ -z "${WEB_QUIET:-}" ]; then
  echo "run.sh: board at http://127.0.0.1:$port/ (Ctrl-C stops it)"
fi
cd "$web" || exit 1
# Vite runs in the background of this script so that a TERM or HUP reaches the trap at once (a shell runs a trap only
# after its foreground child ends). <&0 keeps the terminal as its stdin, so its keys work. Ctrl-C reaches both.
"$vite" --host 127.0.0.1 --port "$port" --strictPort --clearScreen false <&0 &
pid=$!
# shellcheck disable=SC2329
stop() { kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; exit "$1"; }
trap 'stop 0' INT
trap 'stop 143' TERM
trap 'stop 129' HUP
wait "$pid"
status=$?
if [ "$status" -ne 0 ] && [ "$status" -lt 128 ]; then
  echo "run.sh: the dev server stopped with exit code $status. If port $port is in use, set WEB_PORT." >&2
fi
exit "$status"
