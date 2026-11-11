# 0007. Mise pins every tool, and the git hooks stay plain

- Status: accepted
- Date: 2026-10-12
- Superseded in part by [ADR 0014](0014-opentofu-is-the-iac-tool.md): the pin named `terraform` is now `opentofu`. The rest of this ADR is unchanged.

## Context

The hooks in `.githooks/` named the tool versions a second time in `scripts/lib.sh`, and the 2 lists could drift. The hooks ran 1 `mise exec` per tool. The `node` pin was a major version only. Nothing checked the downloads.

We weighed a hook manager. [hk](https://hk.jdx.dev/) and [lefthook](https://lefthook.dev/) run checks in parallel from 1 config file. [pre-commit](https://pre-commit.com/) is Python and keeps its own tool cache, so it repeats what mise does. The pre-commit hook takes under 1 second and the pre-push hook takes under 1 second, so parallel runs would gain little. hk also needs a second tool (`pkl`) and a new config format. The hooks have 4 checks, and `scripts/test-hooks.sh` already tests them.

## Decision

1. `mise.toml` is the only place for tool versions. Every entry has an exact version.
2. `mise.lock` holds the checksums of the pinned tools. Run `mise lock` after a version change, and commit the lock file.
3. The hooks stay plain scripts in `.githooks/`. `make init` turns them on with `core.hooksPath`.
4. The hooks run each tool with `mise exec --`, with no version in the script.
5. Revisit a hook manager when the hooks need more checks or take more than a few seconds.

## Consequences

- A version change touches `mise.toml` and `mise.lock` only.
- The hooks and the `make lint` targets use the same pinned tools.
- No new tool and no new config format.

## Amendment (2026-10-30)

A later change removed gitleaks and the pre-push hook. `.githooks/` now holds 1 hook, `pre-commit`. It has 3 checks: the spec file name, the Markdown style (`markdownlint-cli2`), and the local links (`lychee --offline`). The text above says there is a pre-push hook and 4 checks; that was true on 2026-10-12 and is not true now. The decision is unchanged: the hooks stay plain scripts, and each tool runs with `mise exec --`.
