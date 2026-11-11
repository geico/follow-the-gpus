# Troubleshooting

This page lists messages and their fixes. Here `<cloud>` is `aws` or `azure`.

## Messages about the settings

| Message | Fix |
|---|---|
| `copy terraform/<cloud>/terraform.tfvars.example to terraform/<cloud>/terraform.tfvars and put in your values` | `make plan/<cloud>`, `make apply/<cloud>`, and `make deploy/<cloud>` print this line when the file is missing. Copy the example, and put in your values ([config](config.md)). |
| `Error: No value for required variable` | Set the variable that the message names in `terraform/<cloud>/terraform.tfvars`. The example shows it. |
| `Error: Invalid value for variable` | The value does not pass the check of the variable. The message gives the rule. Correct the value in `terraform.tfvars`. |
| `Warning: Value for undeclared variable` | The stack has no variable with that name. Correct the spelling in `terraform.tfvars`. A misspelled consent key is only a warning, so read the plan output. |

## The saved plan

`make apply/<cloud>` applies only the saved plan `terraform/<cloud>/tfplan`.

| Message | Fix |
|---|---|
| `Error: Mismatch between input and plan variable value` | You changed `terraform.tfvars` after the plan. Run `make plan/<cloud>` again, read the new plan, then run `make apply/<cloud>`. |
| `Error: Failed to load "tfplan" as a plan file` | No plan exists. Run `make plan/<cloud>` first. |

## AWS login expired

```text
dev: AWS login expired; run: aws sso login --profile <profile>
```

`make dev` prints this line when `aws sts get-caller-identity` fails. Run the command in the line. Then run `make dev` again. Until you log in, the board shows only the rows that are already in `data/live/`.

## verify lines

`make verify/aws` and `make verify/azure` exit with code 1 in these cases ([spec 0014](../spec/0014-config-and-deploy-spec.md), section 4):

- The release on the VM is not the release in the store manifest.
- The service is not active.

The last line of the output starts with `NO:` and names the failed check.

For Azure, the script `terraform/azure/scripts/verify-sweeper.sh` exists today (`make verify/azure-sweeper`). Its lines start with `ok:`, `FAIL:`, or `unknown, rerun:`.

## Deploy failures

These failures come from the account or the network, not from `terraform.tfvars`. The list of prerequisites is in [spec 0004, section 12.1](../spec/0004-aws-stack-spec.md) (AWS) and [spec 0005, section 11.1](../spec/0005-azure-stack-spec.md) (Azure).

| Symptom | Cause | Fix |
|---|---|---|
| AWS: the instance launch fails. | The On-Demand vCPU quota of the family of `instance_type` is too low. The default `t4g.small` needs 2 vCPUs. | Raise the quota, or set a smaller `instance_type`. See [GPU quota on day 1](prerequisites.md#gpu-quota-on-day-1). |
| Azure: the VM create fails with a quota error or `SkuNotAvailable`. | The subscription has no free vCPUs for `prober_vm_size` in `location`, or the size is restricted there. | Ask for quota, or set another `prober_vm_size`. Spec 0005 names `Standard_D2as_v4` as a fallback. |
| Azure: the sweeper plan create fails. | The subscription has no App Service quota for `sweeper_plan_sku` in the sweeper region. | Set `sweeper_location` in `terraform/azure/terraform.tfvars` to a region that has quota. Change it when no probe runs. |
| The deploy succeeds, but no row appears. The first boot could not install the prober. | The subnet has no outbound path. | Ask the network owner for a NAT gateway, an internet gateway, or a proxy (AWS: `egress_proxy_url`). Then replace the VM. |
