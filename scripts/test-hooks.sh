#!/usr/bin/env bash
# Test the git hooks in a throwaway copy of the repo.
# Usage: scripts/test-hooks.sh [repo path]   (default: this repo)
# Needs mise (for markdownlint-cli2, lychee) and tar.
# The copy holds only the tracked files and the files that git does not ignore.
# The script removes its temporary folder when it ends, also after a failure, INT, or TERM.
# Set FTG_KEEP_TEST_DIR=1 to keep the folder for debugging.
set -u

src="${1:-$(git rev-parse --show-toplevel)}"
T=""

cleanup() {
  cd / || return
  if [ -n "${FTG_KEEP_TEST_DIR:-}" ]; then
    echo "test dir kept: $T"
    return
  fi
  case "$T" in
    /tmp/ftg-test.?*) rm -rf -- "$T" ;;
  esac
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
T=$(mktemp -d /tmp/ftg-test.XXXXXX) || exit 1
echo "test dir: $T"

pass=0
failn=0
ok() { echo "PASS  $1"; pass=$((pass + 1)); }
bad() { echo "FAIL  $1"; failn=$((failn + 1)); }

# expect 0|1 NAME COMMAND...: 0 means the command must pass, 1 means it must be blocked.
expect() {
  local want="$1" name="$2"
  shift 2
  "$@" >/dev/null 2>&1
  local rc=$?
  if [ "$want" = 0 ] && [ "$rc" -eq 0 ]; then
    ok "$name"
  elif [ "$want" != 0 ] && [ "$rc" -ne 0 ]; then
    ok "$name (blocked, rc=$rc)"
  else
    bad "$name (rc=$rc)"
  fi
}

undo() {
  git restore --staged "$@" 2>/dev/null
  for f in "$@"; do rm -f "$f"; done
}

# copy_repo SRC DEST: copy the tracked files and the files that git does not ignore.
# Ignored files (node_modules/, .terraform/, .venv/) stay out.
# Worktrees and Terraform plugin folders stay out also if the ignore rules change.
# The function runs in a subshell, so pipefail does not change the rest of the script.
copy_repo() (
  set -o pipefail
  mkdir -p "$2" || exit
  git -C "$1" ls-files -z --cached --others --exclude-standard |
    while IFS= read -r -d '' p; do
      case "$p" in
        .terraform/* | */.terraform/*) continue ;;
      esac
      # A tracked file that was deleted in the work tree stays out.
      if [ -f "$1/$p" ] || [ -L "$1/$p" ]; then printf '%s\0' "$p"; fi
    done |
    tar -C "$1" --null -T - -cf - | tar -C "$2" -xf -
)

copy_repo "$src" "$T/work" || exit 1
cd "$T/work" || exit 1
git init -q -b main
git config core.hooksPath .githooks
git init -q --bare "$T/remote.git"
git remote add origin "$T/remote.git"

git add -A
expect 0 "repo copy commits through all hooks" git commit -q -m "chore: test copy"

printf '#Heading without a space\n' > m1.md
git add m1.md
expect 1 "Markdown style fault (markdownlint)" git commit -q -m "test: lint"
undo m1.md

printf '# Title\n\nSee [the plan](missing-file.md).\n' > m2.md
git add m2.md
expect 1 "broken relative link (lychee)" git commit -q -m "test: link"
undo m2.md

printf '# Title\n\nSee [the plan](missing-file.md).\n' > "m 3.md"
git add "m 3.md"
expect 1 "broken link in a path with a space" git commit -q -m "test: space"
undo "m 3.md"

printf '# Title\n\nText.\n' > c.md
git add c.md
expect 0 "clean Markdown file" git commit -q -m "test: clean"

git rm -q c.md
expect 0 "commit that only deletes a Markdown file" git commit -q -m "test: delete"

printf -- '---\ntitle: x\n---\n\n# X\n\nBody.\n' > research/x.md
git add research/x.md
expect 0 "research file with no gate frontmatter" git commit -q -m "test: research"

old=$(ls research/*.md | grep -v README.md | head -n 1)
printf '\nMore text.\n' >> "$old"
git add "$old"
expect 0 "edit of a tracked research file, no gated date" git commit -q -m "test: research edit"

printf '# S\n\nBody.\n' > spec/no-number.md
git add spec/no-number.md
expect 1 "spec file name without NNNN- number" git commit -q -m "test: spec name"
undo spec/no-number.md

printf '# S\n\nBody.\n' > spec/0099-Upper-Case.md
git add spec/0099-Upper-Case.md
expect 1 "spec file name with upper case" git commit -q -m "test: spec case"
undo spec/0099-Upper-Case.md

printf '# S\n\nBody.\n' > spec/0099-test-spec.md
git add spec/0099-test-spec.md
expect 0 "spec file name NNNN-<slug>.md" git commit -q -m "test: spec number"

expect 0 "push of a clean branch" git push -q origin main

printf '# F\n\nMore.\n' > f.md
git add f.md
expect 1 "missing tools block" env PATH=/usr/bin:/bin git commit -q -m "test: missing tools"

echo "passed: $pass  failed: $failn"
[ "$failn" -eq 0 ]
