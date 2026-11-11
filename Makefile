# One entry point for setup, lint, and test. The rules are in ADR 0002:
# decisions/0002-makefiles-follow-cloud-posse-conventions.md
# Keep this file compatible with GNU Make 3.81, the default make on macOS.

# mise runs each tool at the version that mise.toml pins.
# mise is optional. With no mise on PATH, MISE_EXEC is empty and make runs the tools on your PATH.
MISE ?= mise
MISE_EXEC ?= $(if $(shell command -v $(MISE) 2>/dev/null),$(MISE) exec --)

.DEFAULT_GOAL := help

.PHONY: help
## Show the targets and their help text (the default target)
help:
	@scripts/make-help.sh $(MAKEFILE_LIST)

.PHONY: default
## Same as help
default: help

.PHONY: deps
## Install the pinned tools from mise.toml (does nothing when mise is not installed)
deps:
	@if command -v $(MISE) >/dev/null 2>&1; then $(MISE) trust --quiet && $(MISE) install; \
	else echo "deps: mise is not installed; use the tools on your PATH (versions: mise.toml)"; fi

.PHONY: init
## Install the pinned tools when mise exists, and turn on the git hooks in .githooks
init: deps
	git config core.hooksPath .githooks

-include tasks/Makefile.*
