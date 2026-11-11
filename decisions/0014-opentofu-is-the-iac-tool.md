# 0014. OpenTofu is the IaC tool

- Status: accepted
- Date: 2026-10-13
- Note 2026-11-03: [ADR 0029](0029-plain-tfvars-files-replace-the-config-wrapper.md) replaces the follow-up for a separate deployment repo in the Consequences. The stacks deploy from this repo with plain `tofu`, and Terragrunt is not used. The decisions of this ADR stay.

## Context

[ADR 0007](0007-mise-pins-every-tool-and-the-hooks-stay-plain.md) pins `terraform` in `mise.toml`, and `make test` runs `terraform test`. Many teams that run a stack like this use OpenTofu, and its community prefers OpenTofu as well. A plan run on a laptop that had `tofu` on the PATH instead of `terraform` failed with "Inconsistent dependency lock file", because Terragrunt picked `tofu` while the committed lock files named `registry.terraform.io` providers. Testing with one tool (`terraform`, through `make test`) and deploying with another (`tofu`, through Terragrunt) breaks the evidence behind the [ADR 0011](0011-store-and-per-job-simulate-replace-run-mode.md) consent guard, which was proved on Terraform 1.16.5: a guard the test suite never runs under the deploy-time tool is not proven for that tool.

OpenTofu is a fork of Terraform's source after HashiCorp changed Terraform's license from the MPL to the Business Source License, continued "in the Linux Foundation under the name OpenTofu" and governed so "the community governs the project for the community" ([OpenTofu manifesto](https://opentofu.org/manifesto/)). It stays source-compatible with the `.tf`/`.tftest.hcl` language and with most of the Terraform CLI surface ("OpenTofu aims to maintain compatibility with Terraform configurations", [migration overview](https://opentofu.org/docs/intro/migration/)), and `tofu init` downloads providers from its own registry ("Download required providers from the OpenTofu registry", [migration guide](https://opentofu.org/docs/intro/migration/migration-guide/)), `registry.opentofu.org`, rather than `registry.terraform.io`.

## Decision

### 1. Options considered

**Option A: keep Terraform.** This avoids a tool swap but keeps the mismatch in the Context: the people and the community around this project run OpenTofu, and a Terraform-only CI gives no evidence about the tool that actually deploys. The lock-file failure above is not hypothetical; it already happened once.

**Option B (chosen): move to OpenTofu for both stacks and for `make test`.** One tool for test and deploy removes the lock-file mismatch class of failure outright, and it matches the tool the operator teams and the community already use. OpenTofu's own test runner supports the same mock-provider test style this repo already uses (`mock_provider`, `override_resource`, `override_data` blocks that let `tofu test` run fully offline, "In such scenario, creation and retrieval of provider resources and data sources will be skipped. Instead, OpenTofu will automatically generate all computed attributes and blocks to be used in tests", [`tofu test` reference](https://opentofu.org/docs/cli/commands/test/)), so the existing `.tftest.hcl` suites need no redesign, only the small compatibility fixes recorded in section 3 below.

### 2. The choice: option B

OpenTofu is the IaC tool for both stacks (`terraform/aws/`, `terraform/azure/`) and for `make test`. This ADR supersedes ADR 0007's Terraform pin only; the rest of ADR 0007 (mise pins every tool, the hooks stay plain, `mise.lock` after a version change) is unchanged.

1. `mise.toml` pins `opentofu` in place of `terraform`. The pinned version is `1.12.6`, the newest stable release at least 2 weeks old at the time of this change: `1.13.0` (2026-09-30), `1.13.1` (2026-10-01), and `1.12.7` (2026-10-01) are all under 2 weeks old; `1.12.6` (2026-08-19) is the next-newest stable release ([OpenTofu releases](https://github.com/opentofu/opentofu/releases)).
2. Every Make target and script that ran `terraform` now runs `tofu`, including `tofu fmt`, `tofu validate`, and `tofu test`.
3. The 2 tracked `.terraform.lock.hcl` files are regenerated with `tofu providers lock`, at the same provider versions they already pinned (`hashicorp/aws 6.68.0`, `hashicorp/archive 2.8.1`, `hashicorp/random 3.9.1` for AWS; `hashicorp/azurerm 4.81.0`, `Azure/azapi 2.13.0`, `hashicorp/archive 2.8.1`, `hashicorp/tls 4.4.1` for Azure). No provider version changed. `tofu providers lock` rewrites each provider's lock entry from `registry.terraform.io/...` to `registry.opentofu.org/...` and replaces the hashes, because "the OpenTofu project's provider releases are not byte-for-byte identical" (observed warning text from `tofu providers lock`, OpenTofu 1.12.6, 2026-10-13); the version numbers it resolved were unchanged.
4. The ADR 0011 section 7 item 7 guard is re-proved on OpenTofu 1.12.6 (see that ADR for the updated per-claim results). The guard design does not change; the re-proof found 1 narrow, version-specific difference in how `contains()` handles a `null` argument, noted in ADR 0011 directly, that does not reach the shipped guard formula.
5. `terraform.tf` files, the `terraform {}` block, `terraform_data`, `.tf` and `.tftest.hcl` file names, and any text about Terraform's history stay as they are: these name the HCL language and its file conventions, not the CLI binary.

### 3. Compatibility fixes the move required

Re-running the AWS and Azure stacks' `.tftest.hcl` suites under OpenTofu 1.12.6 surfaced 3 kinds of real tool differences, all fixed without weakening any assertion:

- **`override_during` is not an OpenTofu argument.** 4 Azure test files set `override_during = plan` on an `override_resource` or `override_data` block, a Terraform-only argument that controls whether a mock value is generated at plan time or apply time. OpenTofu's `override_resource`/`override_data` reference lists only `target` and `values` ([`tofu test` reference](https://opentofu.org/docs/cli/commands/test/)); there is no plan/apply timing choice to make, because OpenTofu resolves a mocked computed value regardless of `command = plan` or `command = apply`. The lines were removed; no assertion changed.
- **OpenTofu resolves mocked computed attributes at plan time; Terraform defers most of them to apply time.** Terraform's own mocking reference says: "By default, Terraform generates data during the `apply` operation and returns `(known after apply)` values during the `plan` operation" ([Terraform mocking reference](https://developer.hashicorp.com/terraform/language/tests/mocking)). OpenTofu generates the same kind of random placeholder value but does not defer it for a plan-only test run. Several Azure stack resources feed a provider-side ID or key-format validator from another mocked resource's computed attribute (a network interface ID, a storage account ID, a Log Analytics workspace ID, a service plan ID, an SSH public key, a scale set ID used by `azapi`, and 2 storage container IDs used by `azurerm_role_assignment.scope`). Under Terraform those attributes stayed unknown at plan time, so the format validator never ran; under OpenTofu they resolve to a random placeholder string immediately, and the validator fails it ("is not a complete SSH2 Public Key", "parsing the NetworkInterface ID: the number of segments didn't match", and similarly for the others). The fix adds an explicit `override_resource` block with a correctly shaped mock value for each of these attributes, the same pattern one Azure test file (`egress_proxy.tftest.hcl`) already used for `azurerm_storage_account.data`, for a reason already in its comment: "The VM custom_data must be known at plan time, so these values get fixed mock values." No assertion changed; each test still checks the same condition it checked before.
- **OpenTofu refuses to override a non-computed field.** The AWS stack's `stack.tftest.hcl` set a mock `output_path` value on `data.archive_file.prober` and `data.archive_file.config`, alongside the computed `output_sha256`/`output_md5` values, even though `output_path` is a configuration argument the stack itself sets (`main.tf`), not a computed attribute. OpenTofu errors: "Non-computed field `output_path` is not allowed to be overridden." The 2 `output_path` lines were removed; `code_zip_location.tftest.hcl` already asserts the real `output_path` value elsewhere, so no coverage was lost.

Neither fix touches `terraform/aws/`'s or `terraform/azure/`'s own resource code, only the test files' mock values.

## Consequences

- `mise.toml` and `mise.lock` name `opentofu`, not `terraform`. `make init` installs `tofu`, not `terraform`, and no target or script calls `terraform`.
- `make lint test` passes with no `terraform` binary on the PATH; every call goes through `mise exec -- tofu`.
- The 2 tracked lock files resolve providers from `registry.opentofu.org` and keep every provider's existing version.
- `decisions/README.md` lists this ADR, and [ADR 0007](0007-mise-pins-every-tool-and-the-hooks-stay-plain.md) is marked superseded in part by this ADR, for its Terraform pin only.
- A deployment that wraps this repo needs the same pin, the same `tofu` targets, and its own lock-file regeneration and plan-shows-no-changes check.
- `terraform/aws/README.md`, `terraform/azure/README.md`, the specs, and CONTRIBUTING.md name `tofu` as the command; they keep "Terraform" where it names the HCL language, the `.tf` files, `terraform_data`, the `terraform {}` block, or a cited source's own title.
