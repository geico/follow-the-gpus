# Prerequisites

This page lists what you bring before the first deploy. The stacks create no network and no login. The deploy creates the rest. The config examples are in [config/examples](../config/examples/README.md).

Deploy to one cloud or to both. Read the section for each cloud that you use. Then run the read-only check of the cloud. It gives the fix for each gap.

```sh
make preflight/aws     # fails when a gap blocks the deploy
make preflight/azure
```

A `FAIL` line blocks the deploy. A `WARN` line does not. Fix a failed config check with [troubleshooting](troubleshooting.md). The checks are in [spec 0004, section 12](../spec/0004-aws-stack-spec.md) and [spec 0005, section 11](../spec/0005-azure-stack-spec.md). The preflight reads the values of `terraform/<cloud>/terraform.tfvars` ([config](config.md)).

## Tools

- `make`, and either [mise](https://mise.jdx.dev/) or the tools below at the versions in `mise.toml`. With mise, run `make init` once (it runs `mise trust`). mise is optional: it only pins tool versions, and make runs the tools on your PATH when mise is absent. Without mise you need OpenTofu, Python 3.12, [uv](https://docs.astral.sh/uv/), Node 22 (for the board), and DuckDB. To run `make lint` or `make fmt`, or the git hooks, add [markdownlint-cli2](https://github.com/DavidAnson/markdownlint-cli2) and [lychee](https://lychee.cli.rs/).
- The [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) for AWS.
- The [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) and `azcopy` for Azure.

## AWS

| Item | What you bring |
|---|---|
| Network | A VPC and a subnet in the Region. The zone of the subnet must offer the instance type. The stack creates neither. A new account has a default VPC with a public subnet in each zone. You can use one of its subnets. |
| Outbound access | A path to the internet from the subnet, for example a NAT gateway ([cost](cost-and-consent.md#what-a-stack-costs)). The preflight gives a `WARN` when it cannot confirm a path. |
| Rights | Create IAM roles and instance profiles, pass a role, run EC2 instances, create security groups, and create S3 buckets and objects. The list is in [spec 0004, section 12.3](../spec/0004-aws-stack-spec.md). |
| Credentials | A login of the AWS CLI: the default profile, or the profile that `AWS_PROFILE` names ([config](config.md#credentials)). After `aws login`, OpenTofu can need a second profile with `credential_process`. See [AWS CLI, login credentials as process credentials](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html#cli-configure-sign-in-cached-credentials). |
| VM quota | The prober VM is one `t4g.small` (2 vCPUs). The quota is "Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances" (code `L-1216C47A`). Your other running instances also count. |

## Azure

| Item | What you bring |
|---|---|
| Subscription | The subscription ID goes in `subscription_id` in `terraform/azure/terraform.tfvars` ([config](config.md#credentials)). Select the same subscription for the `az` CLI with `az account set --subscription <id>`. |
| Network | A resource group and a subnet in `location`, and in the subscription of the deploy. The stack creates neither. |
| Outbound access | A path to the internet from the subnet, for example a NAT gateway or a firewall. The network owner sets it up. |
| Rights | Owner, or User Access Administrator plus Contributor, on the subscription. The stack creates custom roles and role assignments. Contributor alone cannot write them. See [spec 0005, section 11.3](../spec/0005-azure-stack-spec.md). |
| Resource providers | The deploy registers most of them. Register `Microsoft.Quota` for the sweeper quota check. Register `Microsoft.Consumption` if you set `alert_email`. The command is `az provider register --namespace <name>`. |
| VM quota | The prober VM size has a default of `Standard_B2ats_v2` (2 vCPUs). A new subscription can have 0 quota for it. Then set `prober_vm_size` to a size with free cores. |
| Sweeper quota | The sweeper plan (`Y1` by default) needs App Service quota of 1 or more. If your region has none, set `sweeper_location` to a region that has quota. |

### A network for a new subscription

If you have no network, make one. Replace each name and the region with your own values.

```sh
az group create -n my-rg -l eastus
az network public-ip create -g my-rg -n my-nat-ip --sku Standard
az network nat gateway create -g my-rg -n my-nat --public-ip-addresses my-nat-ip
az network vnet create -g my-rg -n my-vnet --address-prefixes 10.0.0.0/16
az network vnet subnet create -g my-rg --vnet-name my-vnet -n my-subnet \
  --address-prefixes 10.0.1.0/24 --nat-gateway my-nat
az network vnet subnet show -g my-rg --vnet-name my-vnet -n my-subnet --query id -o tsv
```

The last command prints the `subnet_id` for `terraform.tfvars`. The group name is the `resource_group_name`. The NAT gateway costs money while it exists ([cost](cost-and-consent.md#what-a-stack-costs)).

### Probe subnet in another region

A scale-set probe in a region other than `location` needs a subnet in that region. Set it in `probe_subnet_ids`, one subnet for each region. The plan cannot check the region of a subnet. A subnet in the wrong region passes the plan and fails when the scale set starts.

This subnet needs no outbound path. The probe VM is only created, counted, and deleted by calls from the prober VM. It never calls anything. So the subnet can be an isolated network in your own resource group:

- one small subnet;
- no peering, no route table, no NAT gateway, and no public IP;
- [default outbound access](https://learn.microsoft.com/en-us/azure/virtual-network/ip-services/default-outbound-access) turned off;
- a network security group that denies all inbound traffic.

A subnet ID can appear for one region only. [Spec 0005, section 4.3](../spec/0005-azure-stack-spec.md) has the rule. Capacity reservations need no subnet.

## GPU quota on day 1

Request the GPU quota in each target Region on the first day. Approval can take days. The signal probes run in the meantime.

```sh
make quota/aws      # a dry run: prints the requests
make quota/azure
```

Add `QUOTA_ARGS=--apply` to send the requests. The requests come from the experiment file ([spec 0011](../spec/0011-azure-quota-requests-spec.md)). On AWS, a new account gets a low cap on the Spot placement score, in line with its Spot quota. Request `All P Spot Instance Requests` and `All G and VT Spot Instance Requests`.

## State

The default is a local state file in `terraform/<cloud>/`. Keep it until you destroy the stack. For a team, or for a backup, keep the state in a bucket or container that you make first. Then use `backend.hcl`. See [Backend](config.md#backend).

The next pages are [deploy on AWS](deploy-aws.md) and [deploy on Azure](deploy-azure.md). Costs are in [cost and consent](cost-and-consent.md).
