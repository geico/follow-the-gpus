# Azure stack

OpenTofu for the Azure side of the GPU capacity tracker. The design is in [spec 0005](../../spec/0005-azure-stack-spec.md). The product spec is [spec 0002](../../spec/0002-gpu-capacity-tracker-spec.md).

The stack deploys into an existing resource group and an existing subnet. It creates no resource group, virtual network, subnet, or network security group. Into the group go:

- the data storage account, the prober VM, and the sweeper Function App. The sweeper can run in another region (see [Quota](#quota)).
- the probe resources: 1 capacity reservation group per region, and 1 scale set at capacity 0 per provisioning probe target.

The prober VM NIC uses `subnet_id`, which must be in `location`. A scale set uses the subnet of its own region: `subnet_id` in `location`, and the `probe_subnet_ids` entry in any other region (a map of region to subnet ID, default `{}`). The plan cannot check the region of a subnet from its ID: a subnet in the wrong region passes the plan and fails at scale-out with an Azure NIC error. A subnet ID may appear for 1 region only. The plan fails for a non-simulated scale set entry in a region with no subnet. Capacity reservations need no subnet and run in any region.

A subnet in another region needs no outbound path. The prober VM makes every Azure API call through `subnet_id`. A provisioning probe VM is only created, counted, and deleted by those calls, and it never calls anything. So the subnet for another region can be an isolated virtual network in your own resource group: 1 small subnet, no peering, no route table, no NAT gateway, no public IP, [default outbound access](https://learn.microsoft.com/en-us/azure/virtual-network/ip-services/default-outbound-access) turned off, and a network security group that denies all inbound traffic (it also meets the Defender for Cloud recommendation "Subnets should be associated with a network security group", which Microsoft marks "Enable if required"; see the [Defender for Cloud networking recommendations](https://learn.microsoft.com/en-us/azure/defender-for-cloud/recommendations-reference-networking) and the [network security group overview](https://learn.microsoft.com/en-us/azure/virtual-network/network-security-groups-overview)). Only provisioning-probe VMs land there. The stack creates none of this network. Spec 0005, section 4.3, has the rule.

The group can hold resources of other owners. The sweeper deletes only resources with the `experiment` tag. Only the probe resources carry it: the scale sets, the reservation groups, and the VMs, disks, NICs, and reservations that the prober makes. The prober VM, the storage, the sweeper, and the other stack resources do not. The `tags` variable must not hold the key `experiment`.

## Prerequisites

1. OpenTofu and the other tools from `mise.toml`. With mise, run `make init` in the repo root once. mise is optional ([prerequisites](../../docs/prerequisites.md#tools)).
2. The [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli). Run `az login`. The provider and `make status/azure` use it.
3. A subscription. Set `subscription_id` in `terraform/azure/terraform.tfvars`, which git ignores. Without it, the provider reads `ARM_SUBSCRIPTION_ID`. Never write the ID into a tracked file.
4. The Owner role, or User Access Administrator plus Contributor, on the subscription. The stack creates 5 custom roles, defined in [`rbac.tf`](rbac.tf) (2 on the resource group, 2 on the subscription, 1 on the data storage account), 8 fixed role assignments (3 on the subscription), 2 more for the deploy user (data and code containers), and 2 more for each ID in `data_reader_principal_ids`. With `grant_billable_provisioning = true` (see "Provisioning probes", below), it also creates the `prober_capacity_allocator` role assignment, and, for the probe subnet of each region with a real scale set that is in another resource group (never `subnet_id`), a subnet-joiner role assignment too (see "Subnet in another resource group"). Then the deploy user needs `Microsoft.Authorization/roleAssignments/write` on the subnet. Owner or User Access Administrator on the subscription gives it. Contributor cannot write roles or role assignments ([Contributor](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/privileged#contributor), [custom roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/custom-roles)).
5. An existing resource group and an existing subnet in `location`. A scale-set probe in another region also needs a subnet there (`probe_subnet_ids`, see above). A new subscription has none: see [The network you bring](#the-network-you-bring).
6. Outbound internet access from that subnet, for apt, PyPI, the Azure APIs, and the storage account. The stack does not add an outbound path and does not change the subnet.
7. `prober/`, `config/`, and `sweeper/azure/` in the repo. `tofu plan` zips each folder and reads `config/targets.yaml`. `tofu validate` and `make test/azure` need none of them.

## Subnet in another resource group

The scale sets put their NICs in the subnet of their region (`subnet_id` or a `probe_subnet_ids` entry). The prober role covers only `resource_group_name`. For the probe subnet of each region that has a real (non-simulated) scale set, when that subnet is in another resource group, the stack assigns the custom `<prefix>-subnet-joiner` role (subnet read and join, nothing else) to the prober identity at the scope of that one subnet, but only when `grant_billable_provisioning` is also `true` (see "Provisioning probes", below). It never assigns a role on `subnet_id`, the control subnet. Without the join, a scale-out is likely to fail with `LinkedAuthorizationFailed`. The stack compares the group names without regard to case. Spec 0005, section 5.1.1, has the rule. The behavior is not yet checked on a live account.

## Provisioning probes

`grant_billable_provisioning` (default `false`) is the only thing that decides whether this stack is capable of provisioning real capacity: with it `false`, the stack plans no capacity reservation group, no scale set, and none of the `prober_capacity_allocator`/subnet-joiner role assignments, no matter what `azure.provisioning_probes` lists in the targets file. Set it to `true` in your `terraform.tfvars` file only when you also want an `provisioning_probes` entry to run for real; the targets file's own `allow_billable_provisioning` key is a separate control the prober checks, not this variable ([ADR 0015](../../decisions/0015-two-independent-controls-replace-the-terraform-allocation-guard.md)).

The sweeper's own cleanup role stays regardless of this variable, so a leaked resource from an earlier run is never stranded. But turning `grant_billable_provisioning` off removes the prober's own teardown rights and the reservation groups in 1 apply. If an entry is still active, that apply also removes its scale set. Before switching it from `true` to `false`: set every entry in the targets file to `simulate: true` first and deploy that change on its own (this apply removes the scale sets, because a simulated entry gets none, spec 0005 R4); wait until `make status/azure` shows 0 leaked resources (the line `leaked resources: 0`); only then apply the `grant_billable_provisioning = false` change (this apply removes the reservation groups and the `prober_capacity_allocator` role, spec 0005 R7).

## Deploy

```sh
make plan/azure                   # tofu init, then tofu plan -out=tfplan
make apply/azure                  # applies tfplan, and nothing else
make deploy/azure                 # tofu init, then apply with a prompt
make deploy/azure AUTO_APPROVE=1  # no prompt
make status/azure                 # VM power state, sweeper state, leaked resources, newest 5 blobs
make verify/azure                 # after an apply: newest row and log, manifest hash, running release and service; then the sweeper check
make verify/azure-sweeper         # after an apply: the running sweeper code equals the code in state;
                                  # set APPLY_END_UTC=YYYY-MM-DDTHH:MM:SSZ (apply end) to need a log from after the apply
```

The state file stays in `terraform/azure/` and is ignored by git. For remote state, see the [config page](../../docs/config.md#backend).

The settings go into `terraform/azure/terraform.tfvars`, which git ignores. OpenTofu reads it by itself ([variable definitions files](https://opentofu.org/docs/language/values/variables/#variable-definitions-tfvars-files)). Copy [terraform.tfvars.example](terraform.tfvars.example) and put in your values. `resource_group_name` and `subnet_id` are required and have no default:

```sh
cp terraform/azure/terraform.tfvars.example terraform/azure/terraform.tfvars
```

Each variable is in [variables.tf](variables.tf). The defaults match the spec. The example lists the variables that a deploy often sets, with comments.

`tags` (optional) adds tags to each resource of the stack, for example the tags that a tag policy of your subscription requires. The probe resources also get `experiment = <name>`, the targets file's own `experiment` value (ADR 0010 section 2): the stack reads it from the file, not from a variable. `tags` must not hold the key `experiment`, in any case: the plan fails, because the sweeper deletes by that tag.

### Egress proxy

Some networks allow outbound traffic only through an HTTP proxy. Two optional variables handle it. Each one is empty by default, and the VM is then set up as before.

```hcl
egress_proxy_url = "http://proxy.example.internal:80"                  # a host name and a port. No IP address, no user name.
no_proxy         = [".blob.core.windows.net"]                           # extra hosts that bypass the proxy
```

- `egress_proxy_url`: the VM uses the proxy for apt, `curl`, `pip`, and the `ftg-prober` service. The stack does not write `/etc/environment`.
- The VM always bypasses the proxy for `169.254.169.254` (instance metadata), `168.63.129.16` (Azure wire server), `localhost`, `127.0.0.1`, and `login.microsoftonline.com`. Add other hosts in `no_proxy`. The Azure Resource Manager API (`management.azure.com`) goes through the proxy.
- If the data storage account uses a private endpoint, add `.blob.core.windows.net` to `no_proxy`. A call to such an account through the proxy fails.
- The stack creates no network security group. It needs no network rule change, but the VM must be able to reach the proxy.
- A change of these variables changes the `custom_data`, thus it replaces the prober VM.

### Upgrade from the 2-group stack

An older version of this stack created its own groups, `<name_prefix>-control` and `<name_prefix>-probe`. Do not apply this version over such a deployment. The old groups leave the configuration, and the provider deletes a group with all of its contents (`prevent_deletion_if_contains_resources = false` in [versions.tf](versions.tf)). The data storage account moves to `resource_group_name`, which forces a replacement. The apply deletes the collected data blobs.

To upgrade: first copy the `data` container to a safe place. Then run `make destroy/azure` with the old code, and deploy this version fresh.

### Upgrade: a hand-made data reader role

An older version of the dashboard docs told each reader to assign `Storage Blob Data Reader` by hand. The stack now makes that assignment for the deployer and for each ID in `data_reader_principal_ids`. If a hand-made assignment for the same principal already exists on the `data` container or on the `code` container, the first apply fails with `RoleAssignmentExists` (HTTP 409). Before that apply, find it at each container scope:

```sh
az role assignment list --scope <data container resource ID> --role "Storage Blob Data Reader"
az role assignment list --scope <code container resource ID> --role "Storage Blob Data Reader"
```

Then remove it, or bring it into the state with `tofu import 'azurerm_role_assignment.data_reader["<object id, trimmed and lower case>"]' <assignment ID>` (for the `code` container: `azurerm_role_assignment.code_reader[...]`). An assignment at the storage account scope does not conflict.

Do not put the prober identity in `data_reader_principal_ids`. It already has `Storage Blob Data Reader` on the `code` container (`prober_code_reader`), so the apply fails with `RoleAssignmentExists`. The `code` container holds the manifest, the prober zip, the config zip, and the sync script; it holds no credential. The details are in [spec 0005](../../spec/0005-azure-stack-spec.md), section 5.3.

### Notes

- A change to `prober/` or to `config/` replaces the prober VM. The VM holds no data, so this is safe. The new VM installs the new code on first boot.
- The first boot waits for the role assignments. The download of each code zip retries for 10 minutes.
- Nobody logs in to the prober VM. It has no public IP and no inbound rule. To run a command on it, use `az vm run-command invoke --command-id RunShellScript --scripts "journalctl -u ftg-prober -n 50" --ids <prober_vm_id>`.
- To plan without `config/`, add `-var targets_file=examples/targets.example.yaml`. The plan still needs `resource_group_name` and `subnet_id`. OpenTofu zips the folder of the targets file, and the prober reads the same file on the VM.
- The targets file lists the provisioning entries by probe type. An `az_capacity_reservation` entry gets a reservation group for its Region and no scale set. An active `az_vmss_flex` entry gets a scale set, of any size. The names hold a hash of the experiment, and the prober finds each resource by its tags (`experiment`, `sku`, `region` for a scale set; `experiment`, `region` for a group), so a new experiment name replaces every scale set and group once ([ADR 0022](../../decisions/0022-azure-provisioning-probes-are-listed-by-probe-type-and-find-their-resources-by-tags.md)). Apply between probe cycles.
- The prober VM uses the outbound internet access of the given subnet (prerequisite 6). If the first boot cannot reach PyPI, ask the network owner for outbound access.

## Sweeper-stopped alert

The sweeper is the cost brake (spec 0002, section 6). When `alert_email` is set, the stack adds 1 alert rule that tells you when the sweeper stops. The design is in [spec 0005](../../spec/0005-azure-stack-spec.md), section 3.6.

- The rule `ftg-sweeper-stopped` runs a log query on the sweeper Application Insights resource every 5 minutes.
- It fires when no `sweeper run:` health line arrived in the last 10 minutes, or when a sweeper run failed in that time.
- The action group `ftg-alerts` sends an email to `alert_email`. The alert resolves itself when the sweeper runs again.
- When `alert_email` is empty, the stack makes no action group and no rule. The budget follows the same rule.

Verify the email address after the first deploy. Microsoft says: "Email addresses must be verified through a one-time passcode (OTP) within 30 minutes of saving the action group" ([action groups](https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/action-groups)). Microsoft adds: "An unverified receiver can't receive alert or test notifications after enforcement is active." If the passcode expires, open the action group in the portal and select **Resend**.

To look at the health lines yourself, open the sweeper Application Insights resource, select **Logs**, and run the query of the rule:

```kusto
traces
| where timestamp > ago(10m) and message startswith "sweeper run:"
```

If the alert fires:

1. Run `make status/azure`. Check the sweeper state and the leaked resources count.
2. If probe VMs exist and the sweeper does not run, deallocate the prober VM and delete the probe VMs by hand, as in the Destroy steps below.

## Quota

A new subscription can have 0 quota for 2 resources of this stack. The first deploy on a new subscription failed on both:

- 0 cores for the `Basv2` family, the family of the default `prober_vm_size` (`Standard_B2ats_v2`). Newer burstable families can start at 0 cores.
- 0 instances for the `Y1` (consumption) App Service plan of the sweeper.

Check the VM family quota in the control region before the deploy ([az vm list-usage](https://learn.microsoft.com/en-us/cli/azure/vm#az-vm-list-usage)):

```sh
az vm list-usage -l <region> --subscription <target> -o table
```

Find the row of the family of `prober_vm_size`. The limit minus the current value must be 2 or more for `Standard_B2ats_v2`.

If a quota is 0, set 1 or more of these variables in `terraform/azure/terraform.tfvars`:

```hcl
prober_vm_size   = "Standard_B2s"  # a size whose family has free cores in the list
sweeper_plan_sku = "B1"            # a dedicated plan, when Y1 has no quota
```

If the subscription has no App Service quota in the control region, for both `Y1` and `B1`, run the sweeper in another region. Set `sweeper_location` to a region where the quota is free:

```hcl
sweeper_location = "centralus"     # a region with App Service quota; null (the default) means location
sweeper_plan_sku = "Y1"
```

`sweeper_location` moves only the sweeper service plan, Function App, function storage account, Application Insights, and Log Analytics workspace. They stay in the resource group. The prober VM and the data storage stay in `location`. The sweeper acts on the probe resources by resource ID, so it needs no change of role or app setting. The sweeper-stopped alert rule follows Application Insights to the new region. The names of these resources carry the region, for example `ftg-sweeper-centralus-law`. A change of `sweeper_location` thus replaces all of them: OpenTofu creates new resources in the new region and deletes the old ones. Run the change when no probe runs. The sweeper holds no data, but the old Log Analytics workspace goes to the soft-delete state ([Delete and recover a workspace](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/delete-workspace)). Its logs stay for 14 days and cannot be read in the new workspace. Its name is reserved in the old region for that time, which does not block the new name. The same applies once when you upgrade from a version with the old names, because the names now hold the region. Check the quota of a region with `az quota list` (scope `/subscriptions/<target>/...`) for `Microsoft.Web` before you pick it ([az quota](https://learn.microsoft.com/en-us/cli/azure/quota)).

`sweeper_plan_sku` accepts `Y1` (the default), `B1`, `B2`, `B3`, and `S1`. On a dedicated SKU, the stack turns on Always on. Microsoft says: "When you run your app on an App Service plan, you should enable the **Always on** setting so that your function app runs correctly" ([Dedicated hosting](https://learn.microsoft.com/en-us/azure/azure-functions/dedicated-plan)). The zip deploy and the remote build stay the same.

Cost of `B1` against `Y1`:

- `Y1` bills per run and per second of use. Microsoft says that Consumption plan pricing "includes a monthly free grant of 1 million requests and 400,000 GB-s of resource consumption", per subscription and pay-as-you-go only ([Functions pricing](https://azure.microsoft.com/en-us/pricing/details/functions/)).
- `B1` bills for its VM instance all the time, also when the sweeper is idle. For dedicated tiers, "*each VM instance* is charged" ([App Service plans](https://learn.microsoft.com/en-us/azure/app-service/overview-hosting-plans#cost-of-app-service-plans)).
- Thus `B1` adds a fixed monthly charge. Look up `B1` in your region on the [App Service Linux pricing page](https://azure.microsoft.com/en-us/pricing/details/app-service/linux/).

A change between `Y1` and a dedicated SKU after the first deploy needs a new plan and a new Function App. Microsoft says: "Migration isn't supported on Linux" ([plan migration](https://learn.microsoft.com/en-us/azure/azure-functions/functions-how-to-use-azure-function-app-settings#plan-migration)). Set the new `sweeper_plan_sku`, then run:

```sh
mise exec -- tofu -chdir=terraform/azure apply \
  -replace=azurerm_service_plan.sweeper -replace=azurerm_linux_function_app.sweeper
```

The sweeper does not run between the destroy and the create. Run the command when no probe runs. A change between 2 dedicated SKUs, for example `B1` to `S1`, needs no replace.

Request more quota:

- VM family cores: [Increase VM-family vCPU quotas](https://learn.microsoft.com/en-us/azure/quotas/per-vm-quota-requests).
- App Service plan instances, for example `Y1`: the **Quotas** page of the portal. If you cannot change the quota there, open a support request ([quotas overview](https://learn.microsoft.com/en-us/azure/quotas/quotas-overview)).

## Day 1: request GPU quota

File the quota requests on day 1 (spec 0002, D20). `make quota/azure` prints the vCPU quota requests for the sizes and regions of `azure.quota` in `config/targets.yaml`, and `make quota/azure QUOTA_ARGS=--apply` sends them ([spec 0011](../../spec/0011-azure-quota-requests-spec.md), [preflight README](../../preflight/README.md#request-gpu-quota)). It reads the family names from the SKU list, and it also requests the regional Spot quota (`lowPriorityCores`). Azure sets vCPU quota per VM family and per region ([vCPU quotas](https://learn.microsoft.com/en-us/azure/virtual-machines/quotas)). The Spot quota is a separate pool for all families ([Spot quota](https://learn.microsoft.com/en-us/azure/quotas/spot-quota)). The command uses the regions of `azure.quota`. For a region outside `azure.quota`, request the Spot quota by hand.

The family name of a size comes from the `family` field of the SKU list ([Resource SKUs - List](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list)):

```sh
az vm list-skus --location eastus --size Standard_ND96isr_H100_v5 --subscription <target> --query "[0].family" -o tsv
```

Expected family names for common sizes (the table has no row for every size in `config/targets.yaml`, for example `Standard_NC144ds_xl_RTXPRO6000BSE_v6`, and has rows for sizes that the file does not use). No Microsoft quota page lists them, so confirm each with the command above (not verified):

| Size | Family (expected) | Quota row (expected) |
|---|---|---|
| `Standard_ND96isr_H100_v5` | `standardNDSH100v5Family` | Standard NDSH100v5 Family vCPUs |
| `Standard_NC40ads_H100_v5` | `standardNCadsH100v5Family` | Standard NCadsH100v5 Family vCPUs |
| `Standard_NC24ads_A100_v4` | `standardNCADSA100v4Family` | Standard NCADS_A100_v4 Family vCPUs |
| `Standard_NV36ads_A10_v5` | `standardNVADSA10v5Family` | Standard NVADSA10v5 Family vCPUs |
| `Standard_NC4as_T4_v3` | `Standard NCASv3_T4 Family` (with spaces; the quota API rejects `StandardNCASv3_T4Family` with `InvalidResourceName`, author test 2026-10-12) | Standard NCASv3_T4 Family vCPUs |
| `Standard_D2as_v4` (non-simulated) | `standardDASv4Family` | Standard DASv4 Family vCPUs |

Request the increase in the portal under Quotas, or with `az quota` ([Increase VM-family vCPU quotas](https://learn.microsoft.com/en-us/azure/quotas/per-vm-quota-requests)). Also request the regional vCPU total when it is lower than the sum of your targets.

## Cost per month

- The prober VM is the main cost: 1 small VM with a 30 GB Standard SSD disk, on all the time. Look up `Standard_B2ats_v2` in your region on the [Linux VM pricing page](https://azure.microsoft.com/en-us/pricing/details/virtual-machines/linux/).
- The data store holds a few megabytes of JSON lines. The storage cost is small.
- The sweeper runs about 43,000 times per month. The consumption plan has a monthly free grant ([Functions pricing](https://azure.microsoft.com/en-us/pricing/details/functions/)). A dedicated `sweeper_plan_sku` costs more (see [Quota](#quota)). The Log Analytics workspace and Application Insights take a few megabytes per month.
- Scale sets at capacity 0 and capacity reservation groups without reservations have no charge at rest.
- The probes cost money only while a probe runs: a capacity reservation bills at the VM rate from creation to deletion, and a placed VM bills from the `Starting` state (spec 0002, section 5).
- The budget on the resource group alerts at 80 and 100 percent of `budget_amount` when `alert_email` is set. It counts every resource in the group: the stack resources, the probe resources, and the resources of other owners. Cost data lags by hours. The sweeper is the real protection.
- The sweeper-stopped alert is 1 log search alert rule. Azure bills each rule by how often it runs ([alert types](https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-types)). This rule runs every 5 minutes. See [Azure Monitor pricing](https://azure.microsoft.com/en-us/pricing/details/monitor/).

## Destroy

1. Copy the data first: the destroy deletes the data container. Run `make data/sync/azure`, or `azcopy sync` the container to your computer. Then stop the prober, so that no probe starts during the destroy: `az vm deallocate --ids <prober_vm_id>`.
2. Run `make status/azure`. The line "leaked resources" must show `0`. If not, wait 4 minutes for the sweeper, then check again. Delete leftovers by hand with `az vm delete`.
3. Run `make destroy/azure`. It asks for confirmation.
4. Check: `az resource list --subscription <target> --resource-group <resource group name> --query "[?tags.experiment].name" -o tsv` prints nothing. The group, the network, and the resources of other owners stay.

A Log Analytics workspace keeps its name for 14 days after a delete (soft delete). A redeploy with the same sweeper Region and name in that time fails. Wait, or change `name_prefix`.

## The network you bring

The stack creates no resource group, virtual network, or subnet. A new subscription has none. Microsoft turns off default outbound access for new virtual networks ([default outbound access](https://learn.microsoft.com/en-us/azure/virtual-network/ip-services/default-outbound-access)), so a plain new subnet leaves the VM with no path to apt, PyPI, and the Azure APIs. The minimum is a resource group, a virtual network with one subnet, and a NAT gateway on that subnet:

```sh
az group create -n my-rg -l eastus
az network vnet create -g my-rg -n my-vnet -l eastus --address-prefixes 10.20.0.0/24 --subnet-name my-subnet --subnet-prefixes 10.20.0.0/26
az network public-ip create -g my-rg -n my-nat-ip --sku Standard --allocation-method Static
az network nat gateway create -g my-rg -n my-nat --public-ip-addresses my-nat-ip
az network vnet subnet update -g my-rg --vnet-name my-vnet -n my-subnet --nat-gateway my-nat
```

The NAT gateway and its public IP bill by the hour ([NAT gateway pricing](https://azure.microsoft.com/en-us/pricing/details/azure-nat-gateway/)). Use `az network vnet subnet show -g my-rg --vnet-name my-vnet -n my-subnet --query id -o tsv` for `subnet_id`. Adapt the names and address range to your own network.

The destroy must find 0 probe VMs. A scale set with running VMs blocks its own deletion until the VMs are gone, and each VM bills until then.

## Checks

```sh
make lint/azure   # tofu fmt -check -recursive, tofu validate
make test/azure   # tofu test with mock providers: the mapping, the placement, and the tags
```

Both run without credentials.
