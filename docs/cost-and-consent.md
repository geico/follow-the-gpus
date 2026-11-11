# Cost and consent

A normal deploy costs little. The cost goes up only when a probe creates real capacity. Two keys control that. Both are off by default.

## What a stack costs

Prices change. Check the pricing pages before you deploy.

- **AWS:** about 18 US dollars per month in `us-east-1` with the default variables (prices of 2026-10-11). The VM is most of it ([EC2 pricing](https://aws.amazon.com/ec2/pricing/on-demand/)). A public IPv4 address adds about 3.65 dollars ([VPC pricing](https://aws.amazon.com/vpc/pricing/)).
- **Azure:** about 15 US dollars per month with the default variables. This is an estimate from the resources of the stack, not a quote. Look up each item in your region.
  - The prober VM `Standard_B2ats_v2` with its disk is most of it ([VM pricing](https://azure.microsoft.com/en-us/pricing/details/virtual-machines/linux/), [managed disk pricing](https://azure.microsoft.com/en-us/pricing/details/managed-disks/)).
  - The sweeper function on the `Y1` plan costs about nothing inside the free grant ([Functions pricing](https://azure.microsoft.com/en-us/pricing/details/functions/)). If your region has no `Y1` quota, a `B1` plan adds about 13 dollars.
  - A log alert rule that runs every 5 minutes costs about 1.5 dollars ([Azure Monitor pricing](https://azure.microsoft.com/en-us/pricing/details/monitor/)).
  - The storage accounts, Log Analytics, and Application Insights cost little at this volume.
  - The default `budget_amount` of 50 counts the whole resource group, not only this stack. Set it for your group.
- **Network path:** the numbers above do not include it. The VM needs a path to the internet. If your subnet has none, a NAT gateway costs about 33 dollars per month plus data ([NAT gateway pricing](https://aws.amazon.com/vpc/pricing/)). Interface VPC endpoints cost about 7 dollars per month for each endpoint and zone. The S3 gateway endpoint is free but does not give the VM the other paths. On Azure, see [NAT gateway pricing](https://azure.microsoft.com/en-us/pricing/details/azure-nat-gateway/).

A probe that creates real capacity costs money only while it runs. A capacity reservation bills from creation to deletion. To see the probe spend, run `make probes/spend`.

## The two keys

| Key | Where | Default | What it allows |
|---|---|---|---|
| `grant_billable_provisioning` | `terraform/<cloud>/terraform.tfvars` | `false` | The stack gives the prober the rights to create and remove billable capacity. |
| `allow_billable_provisioning` | One entry of `provisioning_probes` in the experiment file | `false` | The prober runs that entry for real. |

The first key controls what the stack permits. The second key controls what the prober does. Put it on an entry of `provisioning_probes`. On an entry of `jobs:` it does nothing ([spec 0003](../spec/0003-prober-core-spec.md), section 3). They are independent ([ADR 0015](../decisions/0015-two-independent-controls-replace-the-terraform-allocation-guard.md), [ADR 0016](../decisions/0016-rename-the-consent-key-to-allow-billable-provisioning.md)). Real capacity needs both.

An entry with `simulate: true` makes no cloud call. It writes simulated rows. Its `simulate` key replaces the consent key ([ADR 0011](../decisions/0011-store-and-per-job-simulate-replace-run-mode.md)).

## Turn real probes on

1. In `terraform/<cloud>/terraform.tfvars`, set `grant_billable_provisioning = true`.
2. In the experiment file, set `simulate: false` and `allow_billable_provisioning: true` on the `provisioning_probes` entry.
3. Run `make plan/<cloud>` and read the plan. Then run `make apply/<cloud>`.

## Turn real probes off

The prober needs its teardown rights while a probe may still run. So remove the grant last.

1. In the experiment file, set `simulate: true` on every `provisioning_probes` entry. Plan and apply this change on its own.
2. Wait for the VM to pick up the new config. This takes up to 5 minutes (the sync timer, [spec 0004](../spec/0004-aws-stack-spec.md) and [spec 0005](../spec/0005-azure-stack-spec.md)).
3. Run `make status/<cloud>`. Wait until it shows `leaked resources: 0`. On AWS, this count includes only reservations older than the grace period (180 seconds, [spec 0004](../spec/0004-aws-stack-spec.md)), so wait longer than that before you run the check.
4. Only then set `grant_billable_provisioning = false` in `terraform/<cloud>/terraform.tfvars`. Plan and apply again.

On Azure, the step 1 apply removes the probe scale sets. A simulated entry gets no scale set ([spec 0005](../spec/0005-azure-stack-spec.md), R4). The step 4 apply removes the capacity reservation groups and the prober's allocator role (spec 0005, R7).

The sweeper keeps its cleanup rights when the grant is `false`. It can then still remove leaked capacity.

## Other cost guards

- The sweeper removes leaked VMs, disks, NICs, and reservations.
- AWS: set `alert_email` for the sweeper-stopped alarm. With an empty value, nobody is told when the sweeper stops.
- Azure: set `alert_email` and `budget_amount` for budget alerts at 80 and 100 percent. With an empty `alert_email`, there is no budget and no alert.
- The preflight checks read your setup before you deploy. See [deploy to AWS](deploy-aws.md) and [deploy to Azure](deploy-azure.md).
