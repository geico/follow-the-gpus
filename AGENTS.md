# Agent guide

This file is for AI coding agents that work in this repo. People can read it too. The full rules are in [CONTRIBUTING.md](CONTRIBUTING.md). Read it before you change anything. This file adds only the rules that an agent must not miss.

## Hard stops

This tool runs with real cloud credentials, and some calls cost money. Do these actions only when a person asks for that action in the current session:

- Deploy, apply, or destroy a stack: `make deploy/*`, `make apply/*`, `make destroy/*`, `tofu apply`, or `tofu destroy`.
- Send GPU quota requests: `QUOTA_ARGS=--apply`.
- Make any cloud call that creates a resource or can cost money. A test call uses a cheap, plentiful instance type, never a rare GPU type ([CONTRIBUTING.md](CONTRIBUTING.md#calls-to-a-cloud-api)).
- Set `allow_billable_provisioning: true` in a config file, or set `AUTO_APPROVE=1`.

Ask before any command that uses the cloud login of the person, also a read-only command such as `make plan/*`, `make preflight/*`, or `make status/*`.

Never write a credential, an account ID, a subscription ID, or a tenant ID into a tracked file. These values go only into the ignored `terraform/<cloud>/terraform.tfvars` and `backend.hcl` files, or into the environment.

## Work loop

1. Make a branch. Never commit on `main`. Make small [conventional commits](https://www.conventionalcommits.org/).
2. When you change product behavior, change the spec in [spec/](spec/README.md) first, in the same pull request. The spec is the source of truth.
3. Run the targeted tests for the files that you change, for example `make prober/test`, `make web/test`, or `make test/aws`. Then run `make fmt` and `make lint`. `make lint` must pass. `make test` runs all tests with no cloud credentials.
4. This repo has no CI. These local checks are the only gate. Say in the pull request which tests you ran.
5. After a rebase, run the checks again, because a rebase does not run the git hooks.

To see the board with no cloud, run `make web/run`. It shows a synthetic sample. `make prober/sim` writes synthetic rows.

## Writing

- Docs and specs use short sentences, active voice, and simple English. Give a source link for each factual claim.
- A new ADR in [decisions/](decisions/README.md) or a new spec takes the next free number.
