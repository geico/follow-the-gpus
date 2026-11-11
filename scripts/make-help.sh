#!/usr/bin/env bash
# Print the Make targets and their help text. The Makefile calls it for "make help".
# Usage: scripts/make-help.sh MAKEFILE...
# A target is listed if the line directly above it starts with "## ". That line is its help text.
set -u

printf 'Targets:\n\n'
awk '
  /^[a-zA-Z0-9_\/.%-]+:/ {
    if (last ~ /^## /) {
      name = $1
      sub(/:.*$/, "", name)
      printf "  %-16s %s\n", name, substr(last, 4)
    }
  }
  { last = $0 }
' "$@" | sort -u
