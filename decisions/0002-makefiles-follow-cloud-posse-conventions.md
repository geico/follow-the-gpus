# 0002. Makefiles follow the Cloud Posse conventions

- Status: accepted
- Date: 2026-10-11

## Context

People and agents need one entry point for setup, lint, and test. Cloud Posse publishes written Makefile conventions ([docs page](https://docs.cloudposse.com/best-practices/developer/makefile/), last updated 2026-01-28). The page also says: "We used to rely more heavily on Makefiles, but have since transitioned our usage predominantly into Atmos itself." We use Make now, because the audience knows it. Atmos was weighed as the task runner and the Terraform orchestrator. The decision was: not now. The repo has 2 stacks that share no state, and each extra tool adds friction for the audience.

## Decision

Every Makefile in this repo must follow these rules.

From the Cloud Posse docs:

1. Namespace every target with `/`: "we recommend using `/` as the delimiter rather than `:` or `-`". For example, write `test/hooks`, not `test-hooks`.
2. The root `Makefile` uses the standard target names `deps`, `build`, `install`, `default`, and `all` for those steps. Each one exists only when the repo has that step. Today the repo has `deps` and `default`.
3. `help` is the default target. Put a `## description` comment on the line directly above each target. (The docs prose says `###`, but their own code matches lines that start with `##` and a space.)
4. Set variable defaults with `?=`. Override them per call, for example `make aws/plan AWS_REGION=us-west-2`.
5. Use target dependencies for order, for example `init: deps`.
6. Keep targets thin: "stick complex logic inside of shell scripts and call those shell scripts from a target."
7. Put each namespace in its own file, `tasks/Makefile.<namespace>`, and load the files with `-include tasks/Makefile.*`.
8. Do not use `$(eval ...)`. Indent with tabs.

Our additions:

1. Name targets `namespace/verb-noun`, for example `lint/markdown` and `aws/plan`. Cloud Posse repos use this pattern, but the docs do not state it.
2. Name variables in UPPER_CASE with a domain prefix, for example `AWS_REGION`. Cloud Posse repos do this, but the docs do not state it.
3. Declare every target `.PHONY`.
4. Use only Make features that work in GNU Make 3.81, the default make on macOS ([build-harness Makefile](https://github.com/cloudposse/build-harness/blob/master/Makefile#L76)). Newer features fail there, or are silently ignored.
5. Run the tools through `mise exec --`, so that `mise.toml` pins their versions.
6. `lint/make` runs `make -n` on each Makefile.
7. A destructive target starts its help text with "WARNING!!!" and stops if a required variable is empty.
8. Do not use build-harness or any remote include such as `-include $(shell curl ...)` ([build-harness usage](https://github.com/cloudposse/build-harness#usage)). Such an include fetches code from a moving branch on every `make` call, with no integrity check.

## Consequences

- `make help` lists every task, for people and for agents.
- When we change a Makefile, we check the Cloud Posse docs page for changes and update this ADR.
