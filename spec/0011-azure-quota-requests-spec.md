---
title: "Azure quota requests spec: GPU vCPU quota from the targets file"
date: 2026-10-12
---

# Azure quota requests spec: GPU vCPU quota from the targets file

**Status:** Phase 1 build · 2026-10-12
**Parent:** [spec 0002](0002-gpu-capacity-tracker-spec.md), decision D20 (request quota on day 1). The config schema is [spec 0003](0003-prober-core-spec.md), section 3. The preflight checks are [spec 0005](0005-azure-stack-spec.md), section 11.
**Code:** `preflight/ftg_preflight/quota.py`, `quota_status.py`, `preflight/tests/test_quota.py`, `test_quota_status.py`, `tasks/Makefile.quota`, the `azure.quota` section of `config/targets.yaml`.

A person must be able to build the quota command and its `status` command again from this file and spec 0003.

## 1. Scope and rules

1. The command requests vCPU quota for the Azure GPU sizes of `azure.quota` in `config/targets.yaml`: the On-Demand family quota and the regional total, and the regional Spot quota (section 2).
2. The default is a dry run. A dry run only reads. It prints each request as an `az` command line.
3. The flag `--apply` sends the requests. Only a person runs `--apply`.
4. Every Azure call goes through the `az` CLI. The requests use the `quota` extension of the `az` CLI ([az quota](https://learn.microsoft.com/en-us/cli/azure/quota)).
5. No tracked file holds a subscription ID. The command gets it at run time (section 4).
6. The `status` command only reads (section 7).
7. An `az` output that is not JSON, or that has an unexpected shape, is a read error with a message, not a crash.
8. The tests use a fake `az`. They use no network and no credentials ([ADR 0005](../decisions/0005-prober-and-sweeper-in-python.md)).

## 2. Background

Azure sets vCPU quota at 2 levels for each region: the total regional vCPUs, and the vCPUs of each VM family. A VM deploy needs free quota at both levels ([vCPU quotas](https://learn.microsoft.com/en-us/azure/virtual-machines/quotas)). The quota resource name of the regional total is `cores`. The quota resource name of a family is the `family` field of the size in the Resource SKUs list ([Resource SKUs - List](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list)). The name can have spaces, for example `Standard NCASv3_T4 Family` (author test, 2026-10-12).

Azure also sets a Spot vCPU quota. It is a separate pool for all VM families of a region, shared by Spot VMs and scale-set instances ([Spot quota](https://learn.microsoft.com/en-us/azure/quotas/spot-quota), [About Azure Spot VMs](https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms)). The quota resource name is `lowPriorityCores`, "Total Regional Low-priority vCPUs": `az quota show --resource-name lowPriorityCores --scope /subscriptions/<id>/providers/Microsoft.Compute/locations/<region>` returns that name and that display name, with `properties.resourceType` null (author test, 2026-10-12). `--resource-type` is optional ([az quota](https://learn.microsoft.com/en-us/cli/azure/quota)), so the Spot request sends none. A new subscription can have a small value, and then no Spot VM starts. This sentence has no source: it is the author's own expectation, not a tested or documented fact.

## 3. Config: `azure.quota`

```yaml
azure:
  regions: [eastus, eastus2, ...]
  quota:
    regions: [eastus, eastus2, centralus, southcentralus, westus, westus2, westus3]
    sizes:
      - {size: Standard_NC40ads_H100_v5, count: 8}
      - {size: Standard_NC144ds_xl_RTXPRO6000BSE_v6, count: 8, regions: [westus2]}
```

| Field | Rule |
|---|---|
| `azure.quota` | Optional mapping. |
| `azure.quota.regions` | Non-empty list of canonical Azure region names: lowercase letters and digits only (`^[a-z0-9]+$`), for example `eastus2`, not `East US 2`. Unique (case-insensitive). Each region must be in `azure.regions`. |
| `azure.quota.sizes` | Non-empty list of mappings. |
| `sizes[].size` | Non-empty string, unique in the list (case-insensitive). An Azure VM size name. |
| `sizes[].count` | Integer, 1 or more. The number of VMs of the size in each region. |
| `sizes[].regions` | Optional non-empty list of canonical region names (the rule of `azure.quota.regions`), unique (case-insensitive). Each region must be in `azure.quota.regions`. The default is `azure.quota.regions`. |

`ftg-prober check-config` must report each fault of this table (spec 0003, section 3). The `aws.quota` block has the same table ([spec 0012](0012-aws-quota-requests-spec.md), section 3), and one function checks both blocks.

The YAML holds no family name and no vCPU count. The command reads both from the Resource SKUs list (section 5). Thus a wrong family name cannot be in the config.

The repo file must hold the 7 regions of the example and these sizes: `Standard_NC40ads_H100_v5` x 8, `Standard_NC24ads_A100_v4` x 8, `Standard_NV36ads_A10_v5` x 16, `Standard_NC4as_T4_v3` x 32, and `Standard_NC144ds_xl_RTXPRO6000BSE_v6` x 8 in `westus2` only.

## 4. Command line

```text
python -m ftg_preflight.quota --config PATH [--apply] [--subscription ID] [--require-vnet] [--install-extension]
make quota/azure [QUOTA_ARGS="--apply ..."]   # the subscription: ARM_SUBSCRIPTION_ID (spec 0014)
```

| Flag | Effect |
|---|---|
| `--config` | Required. The targets file. |
| `--apply` | Send the requests. Without it, the command sends nothing. |
| `--subscription` | The subscription ID. Without it: `AZURE_SUBSCRIPTION_ID`, then `ARM_SUBSCRIPTION_ID`, then the `id` of `az account show`. |
| `--require-vnet` | Skip a region that has no virtual network in the subscription. The default is off. |
| `--install-extension` | Run `az extension add --name quota` when the extension is missing. The default is off. |

Exit codes:

| Code | Meaning |
|---|---|
| 0 | The dry run or the requests finished with no error. |
| 1 | A read call or a request failed. The output names each failure. |
| 2 | The config has no valid `azure.quota` section, the `quota` extension is missing, the login read failed, or the VNet read failed with `--require-vnet`. |

## 5. Steps

The command must do these steps in this order.

1. Read `azure.quota` from the config. If it is missing or breaks a rule of section 3, exit 2. The command checks every rule of section 3 itself, because `--config` can name a file that `check-config` did not check.
2. Run `az extension show --name quota`. If it fails and `--install-extension` is not set, print `az extension add --name quota` to stderr and exit 2. The command must not install the extension without the flag.
3. Get the subscription ID (section 4).
4. Run `az network vnet list --subscription <id> --query "[].location"`. For each quota region with no virtual network, print `warning: no VNet in <region>; provision one before probes run`. The region stays in the plan. With `--require-vnet`, the command skips the region instead. A probe VM needs a subnet in its region. If this read fails, the command prints a warning, does no VNet check, and counts 1 error. With `--require-vnet`, a failed read stops the command with exit 2.
5. For each region, read the Resource SKUs list with 1 call: `az rest` GET `/subscriptions/<id>/providers/Microsoft.Compute/skus?api-version=2021-07-01&$filter=location eq '<region>'`, with each `nextLink`. The command must not use `az vm list-skus`. `az vm list-skus` took more than 120 seconds for 1 region (the same test as spec 0005 section 11), and it hides a size that the subscription cannot use (author test, 2026-10-12).
6. For each size of the region, find the row with `resourceType` `virtualMachines` and the same name, case-insensitive. Skip the size when the row is missing (`not offered`), or when the row has a restriction of type `Location` for the region, for example `NotAvailableForSubscription`. A restriction of type `Zone` is not a skip. Skip the size, with a warning, when the row has no `family` field.
7. For each size that stays, the family ask is `count` x the `vCPUs` capability. Sizes of 1 family add up.
8. For each family, read the limit: `az quota show --scope /subscriptions/<id>/providers/Microsoft.Compute/locations/<region> --resource-name <family>`. The limit is `properties.limit.value`.
9. When the region has 1 or more family asks, read the `cores` usage with `az quota usage show` (the value is `properties.usages.value`) and the `cores` limit with `az quota show`. The cores target is the usage plus the sum of the family asks of the region, rounded up to the next multiple of 100.
10. Pass 1: for each region in config order, request `cores` when its limit is lower than the target.
11. Pass 2: for each region in config order, request each family when its limit is lower than its ask. Skip the families of a region when its `cores` request failed (with `--apply`), or when its cores state is unknown: the `cores` usage or limit read failed. Print the reason for each skipped family, and count the unknown cores state as 1 error.
12. For each region that has 1 or more family asks, the Spot ask is the sum of the family asks (`count` x `vCPUs`) of the sizes that stay. Read the limit of `lowPriorityCores` with `az quota show`. Request it when its limit is lower than the ask (pass 3). A Spot request does not depend on the `cores` request. A failed Spot read skips that request and counts as an error.
13. In 1 run, the command prints or sends each pair of region and resource name 1 time only. A second ask for the same pair prints `already requested in this run; skip`.

A request is this call. The Spot request (pass 3) has `--resource-name lowPriorityCores`, no `--resource-type` (section 2), and no usage added. The command waits for each call to finish, so that pass 1 ends before pass 2, and pass 2 before pass 3.

```text
az quota update --scope /subscriptions/<id>/providers/Microsoft.Compute/locations/<region> \
  --resource-name <name> --resource-type dedicated --limit-object value=<target>
```

A read error of 1 quota skips that request and counts as an error. The command continues.

## 6. Output

Each line starts with `#` or `##`, or is an `az quota update` command line. The command line quotes a name with spaces, so that a person can paste it into a shell. A skipped request gives 1 line with the reason: the restriction reason code or `not offered`, `limit X >= target Y`, `no family in the SKU list`, `cannot read`, `the cores request failed`, or `the cores state is unknown`. The last line counts the requests and the errors, and lists the regions with no virtual network.

The output holds the subscription ID in each scope. Remove it before you paste the output into a public place.

## 7. Status command

The `status` command shows the state of the quota requests of each quota region, with the support ticket of the region. The command only reads.

```text
python -m ftg_preflight.quota status --config PATH [--top N] [--format table|json|markdown] [--subscription ID] [--install-extension]
make quota/status [QUOTA_STATUS_ARGS="--format json"]
make quota/status/md
```

`make quota/status/md` is `status --format markdown`.

| Flag | Effect |
|---|---|
| `--top` | The number of requests to read for each region. The default is 100. |
| `--format` | `table` (the default), `json` (the rows), or `markdown`. |
| `--install-extension` | Install the `quota` and `support` extensions when they are missing. |

Steps:

1. Read `azure.quota`, check the `quota` and `support` extensions, and get the subscription ID, as in section 5, steps 1 to 2 and section 4. A missing `support` extension stops the command with exit 2 and the line `az extension add --name support`.
2. Read the tickets: `az support in-subscription tickets list` ([az support in-subscription](https://learn.microsoft.com/en-us/cli/azure/support/in-subscription/tickets)). A failed read prints an error and counts 1 error. The rows are still shown, with no ticket.
3. The ticket of a region is the newest ticket (by `createdDate`) whose `name` or `title` starts with `ftg-gpu-quota-<region>-`, case-insensitive. The person files 1 ticket for each region with a name such as `ftg-gpu-quota-eastus-20261013`. The command does not read the content of a ticket.
4. For each quota region, in the order of the config, read the Resource SKUs (section 5, step 5) and the requests: `az quota request list --scope /subscriptions/<id>/providers/Microsoft.Compute/locations/<region> --top <N>`. The fields of a request are at the top level: `provisioningState` and `requestSubmitTime`. `value[0]` holds the resource name (`name.value`, in upper case) and the requested limit (`limit.value`) (author test, 2026-10-13). When the reply is a mapping, the command reads its `value` list. A read error of a region drops the rows of that region only (see Exit codes).
5. Make 1 row for each size of `azure.quota` that has the region:
   - GPU: the short class name of the size, from a small map in `quota_status.py` (`Standard_NC40ads_H100_v5` gives `H100`, `Standard_NC24ads_A100_v4` gives `A100`, `Standard_NV36ads_A10_v5` gives `A10`, `Standard_NC4as_T4_v3` gives `T4`, `Standard_NC144ds_xl_RTXPRO6000BSE_v6` gives `RTX PRO 6000`). A size not in the map shows its own name.
   - ASKED: the GPUs and the vCPUs that the config asks for: `count` times the `GPUs` capability of the SKU, and `count` times the `vCPUs` capability.
   - NOW: the current limit of the family (`az quota show`). `?` when the read failed.
   - STATE: from the newest request of the same family: `granted` (`Succeeded`), `failed`, `pending` (`InProgress` or `Accepted`), or the lower-case state. A name matches when the request name and the SKU family are equal after lower case and removal of every character that is not a letter or a digit. With no request: `restricted` when the SKU has a `Location` restriction for the region, else `—`. For a restricted size, NOW is `—`.
   - TICKET: the ticket of the region, or `—`.
6. If the region has a `cores` request, add 1 row: GPU `cores`, ASKED `— / <requested vCPUs>`, NOW the `cores` limit, and the state of the newest request.
7. The `az` calls run one at a time. Parallel `az` processes share 1 MSAL token cache, and one of them can fail with `User ... does not exist in MSAL token cache. Run az login.` (seen on 2026-10-13, while `az` calls made alone worked). A call that fails with `MSAL token cache` in its message is repeated 1 time after 2 seconds. This rule holds for the `quota` command too.

Output:

- `table`: the columns `REGION`, `GPU`, `ASKED (GPUs / vCPU)`, `NOW (vCPU)`, `STATE`, `TICKET` (the ticket number only).
- `json`: the subscription and the rows.
- `markdown`: a heading with the date, then for each region a heading `## <region> — ticket <number> (<status>)` (or `no ticket`) and a table with the columns GPU, Asked, Now, and State.

Exit codes: 0 no error; 1 a read failed (the ticket list or a region). The command prints 1 line on stderr for each failure, after the output, and keeps the other regions; 2 a config or extension error, or no subscription ID.

## 8. Decisions

| # | Decision | Reason |
|---|---|---|
| Q1 | The code is a module of `preflight/`, not a new package. | `preflight/` already calls the `az` CLI and reads the SKU list. It has the restriction rules of spec 0005, section 11.2. |
| Q2 | The family name and the vCPU count come from the SKU list, not from the YAML. | An earlier hand-written script had wrong family names. |
| Q3 | The cores target rounds up to a multiple of 100. | A small margin for the usage of other VMs. A round number is easy to read in a review. |
| Q4 | A region with no virtual network gets a warning, not a skip. `--require-vnet` restores the skip. | Quota approval takes days (no source: the author's own expectation, not a tested or documented fact). The quota request goes in before the network exists. |
| Q5 | The command uses `az quota update`, not `az quota create`. | Each Compute quota of a region exists, with a limit of 0 or more. The `update` example of the docs is a Compute family ([az quota update](https://learn.microsoft.com/en-us/cli/azure/quota#az-quota-update)). |
| Q6 | The status command finds the ticket of a region by the name prefix `ftg-gpu-quota-<region>-`. | The person files 1 ticket for each region. The name is simple to match; the content of a ticket needs many guesses about its shape. |
| Q7 | The Spot ask is the sum of the family asks, and is not rounded or added to usage. | The project starts Spot VMs of the same sizes, so the sum is the most it starts in the region. The Spot limit is separate from `cores`. |

## 9. Sources

- [az quota](https://learn.microsoft.com/en-us/cli/azure/quota)
- [vCPU quotas](https://learn.microsoft.com/en-us/azure/virtual-machines/quotas)
- [Resource SKUs - List](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list)
- [az support in-subscription tickets](https://learn.microsoft.com/en-us/cli/azure/support/in-subscription/tickets)
- [Quota payload](https://learn.microsoft.com/en-us/rest/api/support/quota-payload)
- [Support Tickets - Create](https://learn.microsoft.com/en-us/rest/api/support/support-tickets/create?view=rest-support-2024-04-01)
- [Increase spot vCPU quotas](https://learn.microsoft.com/en-us/azure/quotas/spot-quota)
- [About Azure Spot VMs](https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms)
- [Increase VM-family vCPU quotas](https://learn.microsoft.com/en-us/azure/quotas/per-vm-quota-requests)
