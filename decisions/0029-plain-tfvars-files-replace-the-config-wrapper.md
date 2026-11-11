# 0029. Plain tfvars files replace the config wrapper

- Status: accepted
- Date: 2026-11-03

## Context

A user must deploy AWS and Azure from this repo alone, with no second repo. The private values (VPC, subnet, subscription, experiment name, consent switches) must stay out of Git. One option is a wrapper script that reads 1 config file and sets the environment of each cloud command. A wrapper is a second tool to learn, and it needs rules for every way that a value can reach OpenTofu around the file: an inherited `TF_VAR_*`, `TF_CLI_ARGS`, `TF_WORKSPACE`, an untracked `*.tfvars` file, an inherited `AWS_*` variable, and a changed key after a plan. OpenTofu already solves this problem with its own files.

## Options

1. **A wrapper script.** One file for both clouds, with strict key checks. Cost: a custom tool, its own messages and tests, and more hardening at each review.
2. **`mise.local.toml`.** mise sets environment variables from a git-ignored file. Cost: mise makes variables only from top-level keys, a missing file is skipped with no message ([mise, environments](https://mise.jdx.dev/environments/)), and the values reach every command, also the tests.
3. **`.env` and direnv.** A git-ignored `.env` file that direnv loads. Cost: a new tool, every value is an environment variable (`TF_VAR_*`), and a user who does not run direnv gets no values with no message.
4. **Native OpenTofu files.** A committed `terraform.tfvars.example` for each stack, which the user copies to a git-ignored `terraform.tfvars`. Most open-source OpenTofu and Terraform repos do this.

## Decision

Option 4.

1. **Settings.** Each stack has a committed `terraform/<cloud>/terraform.tfvars.example` with clearly made-up values. The user runs `cp terraform.tfvars.example terraform.tfvars` and puts in their own values. Git ignores `terraform.tfvars`. OpenTofu "automatically loads" it ([OpenTofu, variable definitions files](https://opentofu.org/docs/language/values/variables/#variable-definitions-tfvars-files)). The consent switches, for example `grant_billable_provisioning`, are ordinary variables in this file, and the example sets them to `false`.
2. **Remote state is optional.** Local state is the default. For remote state, the user copies `backend.hcl.example` to a git-ignored `backend.hcl` and runs `tofu init -backend-config=backend.hcl` ([OpenTofu, partial configuration](https://opentofu.org/docs/language/settings/backends/configuration/#partial-configuration)). The stacks have no backend block, so local state stays the default. The user adds the empty backend block in a git-ignored `backend_override.tf` ([OpenTofu, override files](https://opentofu.org/docs/language/files/override/)).
3. **Credentials** never go into the repo. They come from the user's own `aws` or `az` login. The Azure stack gets a `subscription_id` variable, so the subscription is a setting like the others.
4. **Make targets are thin.** They run plain `tofu -chdir=terraform/<cloud> ...`. When `terraform.tfvars` is missing, `plan/<cloud>`, `apply/<cloud>`, and `deploy/<cloud>` stop with 1 line that tells the user to copy the example. No wrapper script, and no environment variable to set.
5. **Saved plans.** `plan/<cloud>` writes `tfplan`, and `apply/<cloud>` applies only that file. OpenTofu itself stops the apply with `Mismatch between input and plan variable value` when `terraform.tfvars` changed after the plan (tested with OpenTofu 1.12.6).
6. **mise is optional.** It installs the pinned tool versions only, and holds no setting.
7. **Also decided.** Plain OpenTofu, no Terragrunt, 1 root for each cloud. The deploy outputs are the source of each value that a deploy writes (`tofu output -raw`). Azure probe subnets in other regions are a prerequisite that the user brings.

[Spec 0014](../spec/0014-config-and-deploy-spec.md) is the source of truth for the files, the backend, and the targets.

## Consequences

- New files: `terraform/aws/terraform.tfvars.example`, `terraform/azure/terraform.tfvars.example`, `terraform/aws/backend.hcl.example`, and `terraform/azure/backend.hcl.example`. The old `examples/*.auto.tfvars.example` files go.
- Git ignores `terraform.tfvars`, `*.auto.tfvars`, `backend.hcl`, `backend_override.tf`, and `tfplan`.
- New targets: `plan/<cloud>` and `apply/<cloud>`. `deploy/<cloud>` gets the same settings check.
- No guard stops a value that comes from outside the file. A shell export of `TF_VAR_*` or a `*.auto.tfvars` file still sets a variable, as in any OpenTofu repo, and a `*.auto.tfvars` value wins over `terraform.tfvars` ([OpenTofu, variable definition precedence](https://opentofu.org/docs/language/values/variables/#variable-definition-precedence)). A misspelled variable name is only a warning, `Value for undeclared variable`. The user reads the plan before the apply.
- `tofu test` also reads `terraform.tfvars`, so a value in it can change a test result. With the example values, `make test/aws` and `make test/azure` pass.
- A worktree has no `terraform.tfvars`. Deploys run from the main checkout.
- Specs 0004, 0005, 0006, and 0014, and ADRs 0010 and 0015, hold no wrapper.

## Cost to reverse

Low. A wrapper can set the same variables again, because the stacks do not change.
