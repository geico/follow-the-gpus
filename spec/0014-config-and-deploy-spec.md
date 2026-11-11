---
title: "Config and deploy spec: plain tfvars files, and the deploy runs from this repo"
date: 2026-10-31
---

# Config and deploy spec

**Status:** accepted · 2026-11-03.
**Decision:** [ADR 0029](../decisions/0029-plain-tfvars-files-replace-the-config-wrapper.md).
**Code:** `terraform/aws/terraform.tfvars.example`, `terraform/azure/terraform.tfvars.example`, `terraform/aws/backend.hcl.example`, `terraform/azure/backend.hcl.example`, `.gitignore`, `tasks/Makefile.aws`, `tasks/Makefile.azure`, `tasks/Makefile.data`, `tasks/Makefile.preflight`, `tasks/Makefile.quota`, `tasks/Makefile.probes`, `scripts/dev.sh`.

A user who has the prerequisites in place (a network, credentials, and optionally a state backend) deploys AWS and Azure from this repo. The user copies an example file, puts in the private values, and runs the make targets. The files are the plain variable files of OpenTofu. There is no wrapper script, and the user exports no environment variable for the deploy. A value that a deploy writes, for example a store name, is read from the deploy outputs. Nobody types it.

A person must be able to build the files and the targets again from this file, [spec 0004](0004-aws-stack-spec.md), and [spec 0005](0005-azure-stack-spec.md).

## 1. Scope

In scope:

- The settings files of each stack, and their examples (section 2).
- The credentials (section 3).
- The targets that read deploy outputs (section 4).
- The state backend (section 5).
- The make targets of each cloud (section 6).
- The commands of a new user (section 8), and the tests (section 9).

Out of scope: the resources of the stacks (specs 0004 and 0005), the experiment file (spec 0003 section 3), and the board (spec 0006). This spec changes no consent control and no cost guard (section 7).

## 2. The settings files

1. **Name and place.** Each stack has 1 settings file: `terraform/aws/terraform.tfvars` and `terraform/azure/terraform.tfvars`. OpenTofu "automatically loads" a file named exactly `terraform.tfvars` from the root module folder ([OpenTofu, variable definitions files](https://opentofu.org/docs/language/values/variables/#variable-definitions-tfvars-files)). The format is the OpenTofu variable file format: `name = value` lines, with maps and lists as HCL values.
2. **The example.** Each stack has a tracked `terraform.tfvars.example`, like an `env.sample` file. It holds every variable that a new user must set or usually sets, with 1 line of help for each. Every value is clearly made up: a subscription of zeros (`00000000-0000-0000-0000-000000000000`), names that contain `example`, IDs of zeros that pass the validation of the variable, and the experiment name `ftg-20260101-example` in a comment. The consent variable `grant_billable_provisioning` is `false`. An optional variable is commented out. The header says: copy this file to `terraform.tfvars` in the same folder (`cp terraform.tfvars.example terraform.tfvars`), replace every value, and never commit `terraform.tfvars`.
3. **Git.** `.gitignore` lists `terraform.tfvars`, `*.auto.tfvars`, `backend.hcl`, `backend_override.tf`, and `tfplan`. It does not list the `.example` files.
4. **Variable names.** The names are the variables of the stack ([spec 0004](0004-aws-stack-spec.md) section 3, [spec 0005](0005-azure-stack-spec.md) section 2). For a name that the stack does not declare, OpenTofu prints `Warning: Value for undeclared variable` and goes on (author test, OpenTofu 1.12.6, 2026-11-03; [OpenTofu, values for undeclared variables](https://opentofu.org/docs/language/values/variables/#values-for-undeclared-variables)). The docs tell the user to read the plan output for this warning.
5. **The experiment file.** `targets_file` names the experiment file, relative to the stack folder. The default is `../../config/targets.yaml`. Both stacks name the same file ([ADR 0010](../decisions/0010-one-experiment-file-of-modules-and-jobs-with-a-billable-guard.md) section 2: 1 file). The stack uploads the whole folder of this file, so a private file goes in a folder of its own.
6. **Worktrees.** A worktree has no `terraform.tfvars`, because git ignores it. Run the deploy targets from the main checkout.
7. **Tests read the file.** `tofu test` also loads `terraform.tfvars` (author test, OpenTofu 1.12.6, 2026-11-03). A value in it can change the result of `make test/aws` or `make test/azure`. With the example values, both suites pass.

## 3. Credentials

Credentials never go into a file of this repo. They come from the user's own login.

1. **AWS.** The AWS CLI login: the default profile, or the profile that `AWS_PROFILE` names. The stack takes its Region from the variable `region`.
2. **Azure.** `az login`. The stack takes the subscription from the variable `subscription_id` ([spec 0005](0005-azure-stack-spec.md) section 1). When it is `null`, the provider reads `ARM_SUBSCRIPTION_ID`. The `az` calls of the scripts and of the preflight and quota code use the subscription that their own code selects.

## 4. Values that a deploy writes

A target that needs a value that a deploy writes runs `tofu -chdir=terraform/<cloud> output -raw <name>` in the main checkout's stack folder. With `-raw`, "OpenTofu will convert the specified output value to a string and print that string directly to the output" ([OpenTofu `output`](https://opentofu.org/docs/cli/commands/output/#usage)).

1. The target reads the main checkout's stack folder, also from a worktree, because the local state is there (section 5).
2. An empty value, a value with whitespace, or a value that starts with `Warning` counts as no output. Reason: some OpenTofu versions print `Warning: No outputs found` on stdout and exit 0 when the folder has no state (`terraform/azure/scripts/verify-sweeper.sh`).
3. The stderr of `tofu` goes to stderr as it is.

| Consumer | Reads |
|---|---|
| `data/sync` | `bucket_name` (aws) and `data_storage_account` (azure). A cloud with no output is skipped with 1 line: `data/sync: no aws deployment (no output bucket_name in terraform/aws); skipping aws`. The target fails only when neither cloud has the output. |
| `make dev` | the same names, through `data/sync`. It keeps no cache file. |
| `probes/status`, `probes/spend` | the same store names |
| `status/aws`, `status/azure` | unchanged: they already read the outputs of their own stack |
| `verify/aws` | `bucket_name`, `instance_id`, `region`, `experiment` |
| `verify/azure` | `data_storage_account`, `prober_vm_id`, `experiment`, `sweeper_code_sha` |

The make variables `AWS_DATA_BUCKET` and `AZURE_DATA_ACCOUNT` leave the targets. `scripts/data-sync.sh` keeps its environment interface, because make sets it from the outputs and its test drives the script directly.

## 5. State backend

1. **Default: local state.** The state is `terraform/<cloud>/terraform.tfstate`, and git ignores it. "OpenTofu defaults to using the local backend, which stores state as a plain file in the current working directory" ([OpenTofu, backend configuration](https://opentofu.org/docs/language/settings/backends/configuration/#default-backend)). This needs no setup. With local state, run the deploy targets from the main checkout only.
2. **No backend block.** The stacks have no backend block. An empty `backend "s3" {}` block would make the backend required, so local state would no longer be the default.
3. **Remote state (optional).** The user makes a state bucket or container first. The user copies `terraform/<cloud>/backend.hcl.example` to `backend.hcl` and puts in the values, then makes `terraform/<cloud>/backend_override.tf` with 1 empty backend block: `terraform { backend "s3" {} }` (AWS) or `terraform { backend "azurerm" {} }` (Azure). Then the user runs `tofu -chdir=terraform/<cloud> init -backend-config=backend.hcl`. This is a partial configuration: the backend block leaves out the arguments, and `-backend-config=PATH` gives them from a file ([OpenTofu, partial configuration](https://opentofu.org/docs/language/settings/backends/configuration/#partial-configuration)). OpenTofu reads a file whose name ends in `_override.tf` as an override file, and "the presence of a block defining a backend ... in an override file always takes precedence" ([OpenTofu, override files](https://opentofu.org/docs/language/files/override/)). Author test, OpenTofu 1.12.6, 2026-11-03: a module with no backend block, a `backend_override.tf` with `backend "local" {}`, and a `backend.hcl` with `path = "other.tfstate"` wrote the state to `other.tfstate`.
4. **Examples.** `terraform/aws/backend.hcl.example` holds the `s3` arguments `bucket`, `key`, `region`, and `use_lockfile = true`. With `use_lockfile`, OpenTofu locks "directly into the configured bucket", and it "recommends to have versioning enabled on the S3 bucket" ([OpenTofu, S3 backend](https://opentofu.org/docs/language/settings/backends/s3/#s3-state-locking)). `terraform/azure/backend.hcl.example` holds the `azurerm` arguments `subscription_id`, `resource_group_name`, `storage_account_name`, `container_name`, `key`, and `use_azuread_auth = true` ([OpenTofu, azurerm backend](https://opentofu.org/docs/language/settings/backends/azurerm/)). The values are made up. The header of each example gives the steps of item 3.
5. **A change of backend.** "When you change a backend's configuration, you must run `tofu init` again" ([OpenTofu, backend initialization](https://opentofu.org/docs/language/settings/backends/configuration/#initialization)).

## 6. Make targets

`<cloud>` is `aws` or `azure`. Each target runs plain `$(MISE_EXEC) tofu -chdir=terraform/<cloud> ...`. Make sets no `TF_VAR_*` and exports nothing.

**Settings check.** `plan/<cloud>`, `apply/<cloud>`, and `deploy/<cloud>` first check that `terraform/<cloud>/terraform.tfvars` exists. When it is missing, the target prints this 1 line on stderr, runs no `tofu`, and exits 2:

```text
copy terraform/<cloud>/terraform.tfvars.example to terraform/<cloud>/terraform.tfvars and put in your values
```

**Init.** The `init` of `plan/<cloud>` and `deploy/<cloud>` is `tofu init -input=false`, plus `-backend-config=backend.hcl` when `terraform/<cloud>/backend.hcl` exists.

| Target | Does |
|---|---|
| `plan/<cloud>` | the settings check, `init`, then `tofu plan -input=false -out=tfplan`. It writes `terraform/<cloud>/tfplan`. |
| `apply/<cloud>` | the settings check, then `tofu apply -input=false tfplan`. It applies the saved plan only. With no `tfplan`, OpenTofu stops with `Failed to load "tfplan" as a plan file`. |
| `deploy/<cloud>` | the settings check, `init`, then `tofu apply -input=false` with the prompt. `AUTO_APPROVE=1` skips the prompt. |
| `destroy/<cloud>` | `tofu destroy`. It prompts for confirmation. Read the plan it prints first. |
| `status/<cloud>` | as today |
| `verify/<cloud>` | prints the newest row object and the newest log object under `experiment=<name>/`, the sha256 of the store manifest, the release that runs on the VM, and the state and start time of the service. It exits 1 unless the release equals the manifest hash and the service is active. `verify/azure` also runs `verify/azure-sweeper`. It reads its values from the outputs (section 4). |
| `preflight/<cloud>` | as today. The preflight reads the stack variables with `tofu console`, which loads `terraform.tfvars`. |
| `quota/<cloud>`, `quota/status`, `quota/status/md` | as today |
| `probes/status`, `probes/spend` | as today; the store names come from section 4 |
| `data/sync`, `data/sync/<cloud>`, `dev` | as today; the store names come from section 4 |

`apply/<cloud>` applies only what `plan/<cloud>` showed. In saved plan mode, "OpenTofu takes the actions in the saved plan without prompting you for confirmation" ([OpenTofu `apply`](https://opentofu.org/docs/cli/commands/apply/#saved-plan-mode)). OpenTofu also loads `terraform.tfvars` at the apply. When a value differs from the plan, it stops with `Error: Mismatch between input and plan variable value` (author test, OpenTofu 1.12.6, 2026-11-03). So a plan that was read before a change of `terraform.tfvars` cannot be applied.

These targets need no `terraform.tfvars`: `help`, `lint`, `test`, `fmt`, `web/*`, `data/rows`, `data/db`, `lint/<cloud>`, and `test/<cloud>`.

Make command-line variables such as `AUTO_APPROVE=1`, `QUOTA_ARGS`, `APPLY_END_UTC`, `WEB_PORT`, and `OUT=` stay: they are not exports.

## 7. Consent and cost guards

This spec changes none of them.

1. `grant_billable_provisioning` ([ADR 0015](../decisions/0015-two-independent-controls-replace-the-terraform-allocation-guard.md), [ADR 0016](../decisions/0016-rename-the-consent-key-to-allow-billable-provisioning.md)) is a stack variable. The user sets it in `terraform/<cloud>/terraform.tfvars`. Its default is `false`, and the example sets it to `false`.
2. `allow_billable_provisioning` stays a key of each job in the experiment file. The prober checks it.
3. The guard of [ADR 0011](../decisions/0011-store-and-per-job-simulate-replace-run-mode.md), the budget and alert variables, and the preflight checks stay in the stack and preflight code.
4. A plan that was read before a change of `terraform.tfvars` cannot be applied (section 6).

## 8. Commands of a new user

```sh
git clone <the URL of this repository> follow-the-gpus && cd follow-the-gpus
make init                     # pinned tools and git hooks
cp terraform/aws/terraform.tfvars.example terraform/aws/terraform.tfvars
$EDITOR terraform/aws/terraform.tfvars   # put in your values
aws sso login                 # or: az login (the normal login)
make quota/aws                # day 1: a dry run of the GPU quota requests; QUOTA_ARGS=--apply sends them
make preflight/aws            # read-only; fails on a gap and gives the fix
make plan/aws                 # read the plan
make apply/aws                # applies that plan only
make verify/aws               # the new release runs, and rows arrive
make dev                      # sync the rows, build the database, open http://127.0.0.1:4173/
```

For Azure, use `azure` in place of `aws`. With no cloud, `make init && make web/run` shows the synthetic sample.

`mise` is optional for the settings: it installs the pinned tool versions only.

## 9. Tests

1. Each example file passes the validation of its stack: `tofu console` with the example copied to `terraform.tfvars` prints the values with no error.
2. `make test/aws` and `make test/azure` pass with the example copied to `terraform.tfvars`.
3. `make plan/<cloud>`, `make apply/<cloud>`, and `make deploy/<cloud>` print the line of section 6 and exit 2 when `terraform.tfvars` is missing.
4. `git check-ignore` ignores `terraform/<cloud>/terraform.tfvars` and `terraform/<cloud>/backend.hcl`, and does not ignore the `.example` files.
5. A fake `tofu` in `scripts/test-dev.sh` has the stdout, the stderr, and the exit code of the real `tofu output -raw`, for a present output and for a missing one.
6. The fakes of `aws ssm`, `az vm run-command`, and the store reads in the `verify/<cloud>` tests match a recorded real run (stdout, stderr, exit code).

## Sources

- [OpenTofu, input variables](https://opentofu.org/docs/language/values/variables/)
- [OpenTofu, override files](https://opentofu.org/docs/language/files/override/)
- [OpenTofu, backend configuration](https://opentofu.org/docs/language/settings/backends/configuration/)
- [OpenTofu, S3 backend](https://opentofu.org/docs/language/settings/backends/s3/)
- [OpenTofu, azurerm backend](https://opentofu.org/docs/language/settings/backends/azurerm/)
- [OpenTofu `output`](https://opentofu.org/docs/cli/commands/output/)
- [OpenTofu `apply`](https://opentofu.org/docs/cli/commands/apply/)

## Revisions

- 2026-11-03: Plain `terraform.tfvars` files from committed examples ([ADR 0029](../decisions/0029-plain-tfvars-files-replace-the-config-wrapper.md)). A partial backend config (`backend.hcl`) and a git-ignored `backend_override.tf` give remote state. Credentials come from the user's own login. The Azure stack has a `subscription_id` variable.
