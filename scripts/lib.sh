# Shared functions for the git hooks. Source this file, do not run it.
# The tool versions are only in mise.toml. Do not repeat them here.

# ftg_tool CMD ARGS...: run CMD at the version that mise.toml pins.
# mise reads the mise.toml of the checkout, and a new worktree or clone is not trusted yet.
# Thus trust only this checkout, only for this call. The hooks are repo code, so this adds no new trust.
# With no mise on PATH, run CMD from PATH. Return 127 if CMD is not available.
ftg_tool() {
  command -v mise >/dev/null 2>&1 || { "$@"; return $?; }
  local top
  top=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
  MISE_TRUSTED_CONFIG_PATHS="$top" mise exec -C "$top" -- "$@"
}

# ftg_check LABEL CMD ARGS...: run a check through ftg_tool.
# Return 0 if it passes. Return 1 if it fails, or if mise is missing.
ftg_check() {
  local label="$1"
  shift
  ftg_tool "$@"
  local rc=$?
  if [ "$rc" -eq 0 ]; then
    return 0
  fi
  if [ "$rc" -eq 127 ]; then
    echo "follow-the-gpus: $label is not available. Install it, or install mise and run 'make init'." >&2
    return 1
  fi
  echo "follow-the-gpus: $label failed (see the output above)." >&2
  return 1
}
