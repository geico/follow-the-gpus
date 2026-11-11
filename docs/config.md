# Config

Your private values live in one git-ignored file for each stack: `terraform/aws/terraform.tfvars` and `terraform/azure/terraform.tfvars`. These are plain OpenTofu variable files. OpenTofu reads `terraform.tfvars` by itself ([OpenTofu, variable definitions files](https://opentofu.org/docs/language/values/variables/#variable-definitions-tfvars-files)). The source of truth is [spec 0014](../spec/0014-config-and-deploy-spec.md).

You need no wrapper and no environment variable. Your credentials come from your own `aws` or `az` login.

## Create the file

Copy the example of the stack, and put in your values:

```sh
cp terraform/aws/terraform.tfvars.example terraform/aws/terraform.tfvars
cp terraform/azure/terraform.tfvars.example terraform/azure/terraform.tfvars
```

Every value in an example is made up. Replace each one. Git ignores `terraform.tfvars`. Never commit it.

`make plan/<cloud>`, `make apply/<cloud>`, and `make deploy/<cloud>` stop with one line when the file is missing:

```text
copy terraform/aws/terraform.tfvars.example to terraform/aws/terraform.tfvars and put in your values
```

The file stays in this folder, and so does the local state.

## Variables

Each variable of a stack is in its `variables.tf`. The example lists the ones that a new user sets, with one line of help for each.

| Stack | Required | Often set |
|---|---|---|
| AWS | `vpc_id`, `subnet_id` | `region`, `targets_file`, `tags`, `alert_email`, `enable_s3_gateway_endpoint`, `egress_proxy_url`, `grant_billable_provisioning` |
| Azure | `resource_group_name`, `subnet_id` | `subscription_id`, `location`, `targets_file`, `tags`, `alert_email`, `budget_amount`, `probe_subnet_ids`, `prober_vm_size`, `grant_billable_provisioning` |

A misspelled name is only a warning: OpenTofu prints `Warning: Value for undeclared variable` and goes on ([OpenTofu, values for undeclared variables](https://opentofu.org/docs/language/values/variables/#values-for-undeclared-variables)). Read the plan output for this warning. The value `grant_billable_provisioning` is a consent key. Keep it `false` until you want real capacity. See [cost and consent](cost-and-consent.md).

`targets_file` is a path relative to the stack folder, for example `../../config/targets.yaml`. Set the same file for both stacks. The stack uploads the whole folder of this file, so keep a private file in its own folder.

## Credentials

- **AWS:** the AWS CLI login. The default profile applies, or the profile that `AWS_PROFILE` names. The stack reads its Region from `region`.
- **Azure:** `az login`. The stack uses `subscription_id`. Without it, the provider reads `ARM_SUBSCRIPTION_ID`.

## Backend

By default the state is a local file, `terraform/<cloud>/terraform.tfstate`. Git ignores it. With local state, run the deploy targets from the main checkout only.

For remote state, use a partial backend config ([OpenTofu, partial configuration](https://opentofu.org/docs/language/settings/backends/configuration/#partial-configuration)). The stacks have no backend block, so local state stays the default.

1. Make the bucket or the container first. Turn on versioning.
2. Copy `terraform/<cloud>/backend.hcl.example` to `backend.hcl` in the same folder, and put in your values.
3. In the same folder, make `backend_override.tf` with an empty backend block of the right type, `s3` for AWS or `azurerm` for Azure. Write it on several lines. HCL does not accept the whole block on one line.

   ```hcl
   terraform {
     backend "s3" {}
   }
   ```

   OpenTofu reads a file whose name ends in `_override.tf` as an override file ([OpenTofu, override files](https://opentofu.org/docs/language/files/override/)).
4. Run `tofu -chdir=terraform/<cloud> init -backend-config=backend.hcl`.

Git ignores `backend.hcl` and `backend_override.tf`. `make plan/<cloud>`, `make deploy/<cloud>`, and `make destroy/<cloud>` add `-backend-config=backend.hcl` when the file exists. After you change a backend, OpenTofu asks you to run `tofu init` again ([OpenTofu, backend initialization](https://opentofu.org/docs/language/settings/backends/configuration/#initialization)). See [the S3 backend](https://opentofu.org/docs/language/settings/backends/s3/) and [the azurerm backend](https://opentofu.org/docs/language/settings/backends/azurerm/).

## Tool versions

`mise` installs the pinned versions of OpenTofu and the other tools (`make init`, which runs `mise trust`). It holds no setting of a stack.

## The experiment file

`config/targets.yaml` sets the regions, sizes, and cadences of the probes. It is not `terraform.tfvars`. Set `experiment:` to a dated name, `ftg-YYYYMMDD-what`, in lower-case letters, digits, and single dashes (63 characters at most). Every row is stored under `experiment=<name>/`, so a new run needs a new name. Check the file before a deploy. This command reads the file only:

```sh
make prober/check-config PROBER_CONFIG=config/my-targets.yaml
```

The format is in [spec 0003, section 3](../spec/0003-prober-core-spec.md).
