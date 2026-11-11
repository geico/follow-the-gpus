# Contributing

Thank you for your help. This guide tells you how to set up, change code, and open a pull request.

Write short sentences in active voice, in simple English. Give a source link for each factual claim in docs and specs. Never write a credential, an account ID, or a subscription ID into a tracked file.

Be kind. The [Code of Conduct](CODE_OF_CONDUCT.md) applies to all spaces of this project. To report a vulnerability, read [SECURITY.md](SECURITY.md).

## Setup

1. Install [mise](https://mise.jdx.dev/). Without mise, install the tools of `mise.toml` yourself, including [markdownlint-cli2](https://github.com/DavidAnson/markdownlint-cli2) and [lychee](https://lychee.cli.rs/), which `make lint`, `make fmt`, and the git hooks need.
2. In the repo, run `make init`. It runs `mise trust`, installs the pinned tools from `mise.toml`, checks them against `mise.lock`, and turns on the git hooks in `.githooks/`. mise is optional for a user who has the tools at the pinned versions, but the hooks need `make init`.
3. Run `make` to see all targets. `make lint` runs every lint check, `make fmt` formats the code, and `make test` runs every test. The tests need no cloud credentials.

This repo has no CI on GitHub. Nothing runs when you open a pull request. You run the checks on your machine: the targeted tests for the files you change, then `make fmt` and `make lint`. You can also run `make test`. A maintainer reviews by reading, so a review can take time.

## Work loop

1. Pick an open issue, or open one to describe your change.
2. Make a branch. Never commit on `main`. Make small [conventional commits](https://www.conventionalcommits.org/), for example `fix(prober): retry a throttled call`.
3. Change the spec first. [spec/](spec/README.md) is the source of truth for the product. A change to product behavior changes the spec in the same pull request ([ADR 0003](decisions/0003-specs-are-the-source-of-truth.md)).
4. Run the targeted tests for the files you change. Then run `make fmt` and `make lint`. `make lint` must pass. After a rebase, run them again, because a rebase does not run the git hooks.
5. Open a pull request. Put `Closes #<n>` in the body, and say which tests you ran.
6. A maintainer reviews the change. Push fixes to the same branch. If you force-push, use `--force-with-lease`, never plain `--force`.

## Code

- [spec/](spec/README.md) states what the code must do. A change to product behavior changes the spec in the same pull request.
- Run each tool at the version that `mise.toml` pins (`mise exec --` does this, and the Make targets already use it) ([ADR 0007](decisions/0007-mise-pins-every-tool-and-the-hooks-stay-plain.md)). Pin an exact version. After a change, run `mise lock` and commit `mise.lock`.
- Python: `uv`, `ruff`, and `pytest` ([ADR 0005](decisions/0005-prober-and-sweeper-in-python.md)). Build test UUIDs in code, for example `uuid.UUID(int=0x1234, version=4)`.
- `FTG_REQUIRE_CLOUD_EXTRAS=1` (or `true`) turns a failed cloud SDK import in the prober shape tests into an error instead of a skip. `make prober/test` sets it after it installs both extras. `0`, `false`, and an empty value leave it off.
- OpenTofu: each stack keeps local state by default. Write account, subscription, and tenant IDs only into the ignored `terraform/<cloud>/terraform.tfvars` and `backend.hcl` files ([spec 0014](spec/0014-config-and-deploy-spec.md)) or into the environment. `tofu test` also reads `terraform.tfvars`, so a value there can change a test result.
- OpenTofu provider cache: `mise.toml` sets `TF_PLUGIN_CACHE_DIR` to `~/.cache/opentofu/plugin-cache`, so every worktree, `tofu test`, and Make target shares one copy of the providers (about 1.4 GB) instead of one per `.terraform` directory. `mise install` (so `make init`) creates the directory first. Without it, OpenTofu 1.12.6 prints `Error: The specified plugin cache dir ... cannot be opened` for a missing directory, then creates it and exits 0. To clear it, run `rm -rf ~/.cache/opentofu/plugin-cache`; the next `tofu init` downloads again. A `plugin_cache_dir` in `~/.tofurc` may name the same directory; the environment variable wins.
- OpenTofu tests use mock providers. A mock fills every computed attribute with a random value. Give a data source ID-shaped values with `mock_data` when the provider parses the ID at plan time. OpenTofu resolves a mocked computed attribute immediately, even in a `command = plan` test, so give `override_resource`/`override_data` a correctly shaped value for any attribute another resource's provider-side validation reads ([ADR 0014](decisions/0014-opentofu-is-the-iac-tool.md)).
- Makefiles follow [ADR 0002](decisions/0002-makefiles-follow-cloud-posse-conventions.md). Add each new lint or test target to `lint` in `tasks/Makefile.lint` or `test` in `tasks/Makefile.test`.
- A new ADR in `decisions/` takes the next free number. A new spec is `spec/NNNN-<slug>.md` with the next free number.
- The prober records a provider's error code in the row, and the provider's error message in the log, as the provider returned them. The message goes to the log, never in a row: each failed provider call logs 1 line with the probe type, the Region, the code, the status, and the message ([spec 0003](spec/0003-prober-core-spec.md) section 8.1, item 6; [spec 0009](spec/0009-azure-spot-signals-spec.md), decision A7). It never filters, redacts, rewrites, or hides a code or a message, and its logs are unfiltered and unredacted: every record at the configured level reaches the store, from our code and from the SDK loggers, with no logger-name filter, and the byte caps cut length only. The prober and the sweepers use no static credentials, and logs are never filtered ([ADR 0006](decisions/0006-store-raw-provider-values-normalize-separately.md)).
- A `web/*` Make target stops early with a message when `WEB_DIR`, `WEB_DB`, or the repo path contains a space, because Make cannot handle a space in a path; other targets (`help`, `lint`, `data/db`) still work (`tasks/Makefile.web`).
- Logs and metrics never mix: rows are metrics; our code writes its logs to the store under the logs prefix ([spec 0003](spec/0003-prober-core-spec.md) section 8).

## Calls to a cloud API

- An exploratory or test call uses a cheap, plentiful instance type. It never uses a rare GPU type, and never targets AWS GovCloud or Azure Government ([spec 0002](spec/0002-gpu-capacity-tracker-spec.md), out of scope).
- A call never adds an AWS Spot placement score configuration beyond the configured Spot score requests. Each new configuration counts against a 24-hour limit ([research/aws-spot-placement-score-configuration-limit.md](research/aws-spot-placement-score-configuration-limit.md)).
- Every Azure read is scoped to the target subscription: Resource Graph (`az graph query --subscriptions <target>`, or `subscriptions` in the SDK `QueryRequest`) and the `az` list calls (`--subscription <target>`). A read never covers another subscription. When the subscription is missing, the call fails; it does not run unscoped. The one exception is the `SpotResources` price and eviction query of the prober, which reads a public Azure table and returns 0 rows at subscription scope ([spec 0009](spec/0009-azure-spot-signals-spec.md), section 3.2).
- Nothing that allocates capacity runs without the job's `allow_billable_provisioning: true` ([ADR 0011](decisions/0011-store-and-per-job-simulate-replace-run-mode.md)).

## Hooks

- **pre-commit:** the spec file name check, markdownlint, and a check of local links in the staged Markdown files.

The hooks are plain scripts in `.githooks/`. They run each tool with `mise exec --`, so the versions come from `mise.toml` only.

`make test/hooks` tests the hooks in a temporary copy of the repo. The copy holds only the files that git tracks or does not ignore. Each run removes its copy when it ends, also after a failure, INT, or TERM, and the target checks this. Set `FTG_KEEP_TEST_DIR=1` to keep the copy for debugging.
