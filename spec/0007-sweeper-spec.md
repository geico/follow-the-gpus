---
title: "Azure sweeper spec"
date: 2026-10-11
---

# Azure sweeper spec

The sweeper is the runaway-cost brake of [spec 0002](0002-gpu-capacity-tracker-spec.md), section 6, D9, and D10. It is a timer-triggered Azure Function. Every minute, it deletes each probe resource that is older than a threshold. A person must be able to build it again from this file and spec 0002.

The code lives in `sweeper/azure/`. The Azure stack (spec 0005) deploys it and gives it its identity and rights.

The AWS stack has its own sweeper, a scheduled Lambda function that cancels probe Capacity Reservations: [spec 0013](0013-aws-sweeper-spec.md).

## 1. Scope

The sweeper must:

1. Run every minute, independent of the prober VM.
2. Find the probe resources with 1 Resource Graph query.
3. Delete each resource that is older than the threshold, in any power or provisioning state.
4. Never touch a resource outside the probe resource group.
5. Never delete a resource without the `experiment` tag of `FTG_EXPERIMENT`. `FTG_EXPERIMENT` stays, as the sweeper still reads it; the stack fills it from the `experiment` value of the targets file, the only source ([ADR 0010](../decisions/0010-one-experiment-file-of-modules-and-jobs-with-a-billable-guard.md) section 2), instead of taking it as a separate input.
6. Log 1 line per decision, and 1 health line per run.

The probe resource group is the existing group of the Azure stack, `resource_group_name` (spec 0005, section 1). It also holds the prober VM, its disk and NIC, the storage accounts, the sweeper, its identity, and resources of other owners. None of them carries the `experiment` tag, and the sweeper must never delete them. Rule 5 is the guard: the query (section 5) and the age rule (section 6, check 2) both require the tag.

The sweeper must not create resources, must not read the data store, and must not hold state between runs.

## 2. Folder layout and the deployment zip

The Function App runs the Python v2 programming model. In this model, `function_app.py` is the entry point, and decorators declare the trigger ([Python developer guide](https://learn.microsoft.com/en-us/azure/azure-functions/functions-reference-python?pivots=python-mode-decorators)). The folder is:

```text
sweeper/azure/
  function_app.py      # the Functions entry point: the timer trigger, 1 function
  host.json            # host settings: timeout, logging, extension bundle
  requirements.txt     # runtime packages; the remote build installs this file
  sweeper/             # the logic, no Azure SDK import except in azure_clients.py
    settings.py        # the app settings
    model.py           # types, delete stages, the Resource record
    query.py           # the Resource Graph query and the row parser
    rules.py           # the age rule
    run.py             # 1 run: query, decide, delete, fallback, health line
    azure_clients.py   # the real Resource Graph, ARM, and Compute clients
  tests/               # pytest, with fakes; no network, no credentials
  pyproject.toml       # dev project for uv: ruff and pytest, plus the runtime pins
  uv.lock
```

The deployment zip must have `function_app.py`, `host.json`, and `requirements.txt` at its root, and the `sweeper/` package next to them. The Azure stack makes the zip with `archive_file` from `sweeper/azure/` and deploys it with `zip_deploy_file`. The zip must not include `.venv/`, `tests/`, `__pycache__/`, `.pytest_cache/`, or `.ruff_cache/`. The zip can include `pyproject.toml` and `uv.lock`.

The zip holds no packages. The Function App must build the packages from `requirements.txt` on the server. For zip deploy, the app setting `SCM_DO_BUILD_DURING_DEPLOYMENT=true` turns the remote build on. On Linux, the app also needs `ENABLE_ORYX_BUILD=true` ([zip deployment](https://learn.microsoft.com/en-us/azure/azure-functions/deployment-zip-push#deployment-customization)). The OpenTofu `zip_deploy_file` argument needs `WEBSITE_RUN_FROM_PACKAGE=1` or `SCM_DO_BUILD_DURING_DEPLOYMENT=true` in the app settings ([azurerm_linux_function_app](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/linux_function_app#zip_deploy_file-1)). The stack must set `SCM_DO_BUILD_DURING_DEPLOYMENT=true` and `ENABLE_ORYX_BUILD=true`, and must not set `WEBSITE_RUN_FROM_PACKAGE`.

The runtime is Python 3.12. The pins in `requirements.txt` and in `pyproject.toml` must be the same. A test checks that.

## 3. Trigger

The function `sweep_probe_resources` has 1 timer trigger:

| Setting | Value | Note |
|---|---|---|
| `schedule` | `0 */1 * * * *` | An NCRONTAB expression with 6 fields: second, minute, hour, day, month, day of week ([timer trigger](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-timer#ncrontab-expressions)). The run starts at second 0 of every minute. |
| `run_on_startup` | `false` | The function does not run when the host starts. Microsoft advises against `runOnStartup` in production ([timer trigger](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-timer#configuration)). |
| `functionTimeout` | `00:05:00` | In `host.json`. On the Consumption plan, the default is 5 minutes and the maximum is 10 minutes ([function app timeout duration](https://learn.microsoft.com/en-us/azure/azure-functions/functions-scale#function-app-timeout-duration)). |

The timer trigger runs 1 instance at a time. It uses a storage lock, so 1 timer instance runs when the app scales out ([timer trigger](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-timer#function-apps-sharing-storage)). The timer does not start a new run while a run is still in progress ([timer trigger](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-timer#scale-out)).

When the host reports that the timer is past due ([timer trigger](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-timer#usage)), the run logs `sweeper timer: past due` and continues.

## 4. Settings

The function reads these app settings. The Azure stack sets the first 5.

| Setting | Required | Default | Meaning |
|---|---|---|---|
| `FTG_PROBE_RG` | yes | none | The probe resource group: `resource_group_name` of the Azure stack. The sweeper acts only here. A missing value stops the run with an error. |
| `FTG_EXPERIMENT` | yes | none | The value of the `experiment` tag that marks a probe resource. The stack fills it from the targets file's own `experiment` key ([ADR 0010](../decisions/0010-one-experiment-file-of-modules-and-jobs-with-a-billable-guard.md) section 2), not from a separate input. |
| `FTG_MAX_AGE_SECONDS` | no | `180` | The age threshold in seconds. The value must be a whole number, 0 or more. It must be 30 s or more above the provisioning probe timeout plus the teardown timeout (spec 0010, section 10). |
| `FTG_DATA_STORAGE_ACCOUNT` | no | none | The data storage account's name (spec 0005 section 3.1). When set, the sweeper attaches the store log handler of section 9 and writes its own log objects to the `data` container. When not set, the sweeper logs through `logging` only, as before this change. |
| `AZURE_SUBSCRIPTION_ID` | yes | none | The query runs against this subscription only. A missing value stops the run with an error; the query is never unscoped, because an unscoped query runs against each subscription that the identity can read ([query scope](https://learn.microsoft.com/en-us/azure/governance/resource-graph/concepts/query-language#query-scope)). |

`FTG_PROBE_RG` and `FTG_EXPERIMENT` must match `^[A-Za-z0-9._()-]{1,90}$`. The sweeper puts both values into the query text, and this rule keeps the query safe.

The Function App must not set `AZURE_CLIENT_ID` or `AZURE_CLIENT_SECRET`. These settings change the identity that `DefaultAzureCredential` uses (section 10).

## 5. Discovery: the Resource Graph query

Each run sends 1 query to Azure Resource Graph. The query reads the `Resources` table and returns the probe resources of 5 types ([supported tables](https://learn.microsoft.com/en-us/azure/governance/resource-graph/reference/supported-tables-resources)). The sweeper deletes 4 of them. The scale sets serve the fallback only (section 8).

```kusto
Resources
| where resourceGroup =~ '<FTG_PROBE_RG>'
| where tostring(tags['experiment']) =~ '<FTG_EXPERIMENT>'  // the stack fills this from the targets file's own `experiment` key
| where type in~ ('microsoft.compute/virtualmachines', 'microsoft.compute/disks',
    'microsoft.network/networkinterfaces',
    'microsoft.compute/capacityreservationgroups/capacityreservations',
    'microsoft.compute/virtualmachinescalesets')
| project id, name, type, resourceGroup, tags,
    timeCreated = tostring(properties.timeCreated),
    vmssId = tostring(properties.virtualMachineScaleSet.id),
    attachedVmId = tostring(properties.virtualMachine.id),
    capacity = toint(sku.capacity)
| order by id asc
```

Rules:

1. The sweeper must follow the `$skipToken` and read every page ([paging results](https://learn.microsoft.com/en-us/azure/governance/resource-graph/concepts/work-with-data#paging-results)). By default, Resource Graph returns at most 1000 records for a query ([data set result size](https://learn.microsoft.com/en-us/azure/governance/resource-graph/concepts/work-with-data#data-set-result-size)).
2. The sweeper must take the resource group of each row from its `id`, not from the `resourceGroup` column. The id is the value that the delete call uses.
3. The sweeper must parse `timeCreated` as an ISO 8601 time with a `Z` suffix or a `+00:00` offset and up to 7 fraction digits. It must treat an empty value as "no timeCreated".
4. Resource Graph is discovery only. Its data can lag behind Resource Manager: the data is not strongly consistent, and Resource Graph updates it from Resource Manager notifications and from a regular full scan ([Resource Graph overview](https://learn.microsoft.com/en-us/azure/governance/resource-graph/overview#how-resource-graph-is-kept-current)). Thus a row can name a resource that is already gone, and a new resource can be missing. Section 7 states how the delete handles a gone resource.
5. When the query fails, the run logs the error, logs the health line with `errors 1`, and then fails the invocation. A failed invocation shows as a failed request in Application Insights: category `Host.Results`, table `requests` ([configure monitoring](https://learn.microsoft.com/en-us/azure/azure-functions/configure-monitoring#configure-categories)).
6. A row that the parser cannot read counts as 1 error, and the run continues with the other rows.

The columns come from the resource properties. `timeCreated` exists on a virtual machine ([Virtual Machines - Get](https://learn.microsoft.com/en-us/rest/api/compute/virtual-machines/get), minimum api-version 2021-11-01), on a disk ([Disks - Get](https://learn.microsoft.com/en-us/rest/api/compute/disks/get)), and on a capacity reservation ([Capacity Reservations - Get](https://learn.microsoft.com/en-us/rest/api/compute/capacity-reservations/get), minimum api-version 2021-11-01). A network interface has no `timeCreated` property. It has a `virtualMachine` reference to the VM that uses it ([Network Interfaces - Get](https://learn.microsoft.com/en-us/rest/api/virtualnetwork/network-interfaces/get)). A VM in a Flexible scale set names its scale set in `virtualMachineScaleSet.id` ([orchestration modes](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes)).

## 6. The age rule

For each row, the run makes 1 decision: `delete`, `keep`, or `skip`. The checks run in this order. The first check that applies gives the decision.

| # | Check | Decision | Reason text |
|---|---|---|---|
| 1 | The resource group of the id is not `FTG_PROBE_RG` (case-insensitive) | `skip` | `outside probe resource group` |
| 2 | The `experiment` tag is not `FTG_EXPERIMENT` | `skip` | `experiment tag does not match`. The comparison value comes from the targets file, through the stack's `FTG_EXPERIMENT` app setting (ADR 0010 section 2). |
| 3 | The type is a scale set, or any type that section 7 does not list | `skip` | `type is not deletable` |
| 4 | `timeCreated` is present and `now - timeCreated > FTG_MAX_AGE_SECONDS` | `delete` | `older than <N> s` |
| 5 | `timeCreated` is present (the age is at or under the threshold) | `keep` | `young` |
| 6 | A network interface with a `virtualMachine` reference | `keep` | `attached to a VM, age unknown` |
| 7 | A network interface with no `virtualMachine` reference, while a VM in the result is young | `keep` | `unattached, but a young VM is present` |
| 8 | A network interface with no `virtualMachine` reference, and no young VM | `delete` | `unattached, age unknown` |
| 9 | Any other resource with no `timeCreated` | `keep` | `no timeCreated` |

Notes:

- `now` is the UTC clock of the run, read once per run. Age = `now - timeCreated` in seconds. A resource at the threshold is young. The comparison is strict.
- Checks 1 and 2 repeat the filters of the query. The query is the first guard, and the rule is the second. A resource outside `FTG_PROBE_RG` or without the tag never reaches a delete call. Thus the prober VM, its disk and NIC, and the other untagged resources in the shared group are never deleted (section 1).
- The power state and the provisioning state of a resource play no part. A `Failed` VM, a `Creating` VM, and a stopped VM get the same decision as a running VM.
- Checks 6 to 8 exist because a network interface has no `timeCreated` (section 5). A tagged network interface that no VM uses is a leftover. A young VM in the result shows a probe in flight, and a VM create can make its network interface before the VM. Thus the rule waits in that case.

## 7. Deletes

The run deletes in 3 stages. A stage starts each of its deletes, then waits for all of them, then the next stage starts.

| Stage | Types | Why this order |
|---|---|---|
| 1 | `microsoft.compute/virtualmachines` | A VM delete also removes the disks and network interfaces that have `deleteOption: Delete` ([Virtual Machines - Get](https://learn.microsoft.com/en-us/rest/api/compute/virtual-machines/get); spec 0002, section 4.5). |
| 2 | `microsoft.network/networkinterfaces`, `microsoft.compute/disks` | A network interface can be deleted only when it is not attached to a VM ([network interface](https://learn.microsoft.com/en-us/azure/virtual-network/virtual-network-network-interface#delete-a-network-interface)). Azure prevents the delete of a disk that is attached to a VM ([storage deletion errors](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machines/windows/storage-resource-deletion-errors)). |
| 3 | `microsoft.compute/capacityreservationgroups/capacityreservations` | A capacity reservation must have no associated VMs before it can be deleted ([Capacity Reservations - Delete](https://learn.microsoft.com/en-us/rest/api/compute/capacity-reservations/delete)). Block and Future capacity reservations have more delete limits. |

Rules:

1. Each delete is an Azure Resource Manager `DELETE` on the resource id ([Resources - Delete By Id](https://learn.microsoft.com/en-us/rest/api/resources/resources/delete-by-id)). The code calls `ResourceManagementClient.resources.begin_delete_by_id(resource_id, api_version=...)` from `azure-mgmt-resource`. The `api_version` argument is required ([ResourcesOperations](https://learn.microsoft.com/en-us/python/api/azure-mgmt-resource/azure.mgmt.resource.resources.operations.resourcesoperations)). Implementer's choice: any GA api-version that supports `DELETE` for the type. The code uses `2024-07-01` for VMs and capacity reservations ([Virtual Machines - Delete](https://learn.microsoft.com/en-us/rest/api/compute/virtual-machines/delete?view=rest-compute-2024-07-01)), `2024-03-02` for disks ([Disks - Delete](https://learn.microsoft.com/en-us/rest/api/compute/disks/delete?view=rest-compute-2024-03-02)), and `2024-05-01` for network interfaces ([template reference](https://learn.microsoft.com/en-us/azure/templates/microsoft.network/2024-05-01/networkinterfaces)).
2. A `DELETE` of a VM, disk, network interface, or capacity reservation that does not exist returns `204 No Content` ([Virtual Machines - Delete](https://learn.microsoft.com/en-us/rest/api/compute/virtual-machines/delete)). The sweeper must read the status of the first response. A 204 counts as done, and the run logs the action `already gone`. This is the lag case of section 5.
3. A 404 also counts as done and logs `already gone`. A 404 can come when a parent of the resource is gone. The SDK raises `ResourceNotFoundError` for a 404 ([azure.core.exceptions](https://learn.microsoft.com/en-us/python/api/azure-core/azure.core.exceptions.resourcenotfounderror)). A test with a fake HTTP transport checks both the 204 and the 404 path.
4. Any other error on a delete counts as 1 error. The run logs the action `error` with the message and continues with the next resource. The next run tries again.
5. A `202 Accepted` response means that Resource Manager accepted the delete ([Virtual Machines - Delete](https://learn.microsoft.com/en-us/rest/api/compute/virtual-machines/delete)). The run waits for the deletes of a stage with 1 shared budget of 240 seconds per run, measured from the first delete. When the budget ends, the run stops waiting. A delete that is still in progress logs the action `delete accepted`. A delete that completed logs `deleted`.
6. The sweeper is idempotent. A run can repeat any step of an earlier run with no harm: a second delete of a gone resource returns 204, and a second scale to 0 changes nothing.
7. The sweeper never deletes a scale set, a capacity reservation group, a virtual network, or a resource of any type that the table above does not list. Section 6, check 3, enforces this.

## 8. Fallback: scale set to capacity 0

Spec 0002, section 6, asks for a fallback when a scale set re-creates deleted members. After the 3 stages, the run looks at the VMs with the decision `delete` that name a scale set in `virtualMachineScaleSet.id`:

1. For each such scale set id, the run finds the scale set row in the query result (type `microsoft.compute/virtualmachinescalesets`). The match ignores case.
2. When the row exists, the run sets `sku.capacity` of the scale set to 0 with a `PATCH` ([Virtual Machine Scale Sets - Update](https://learn.microsoft.com/en-us/rest/api/compute/virtual-machine-scale-sets/update)) and waits inside the budget of section 7. It logs `sweeper fallback: action=scaled to 0 instances=<N> capacity_was=<C> id=<id>`, or `action=scale to 0 accepted` when the budget ended first.
3. When the row does not exist, the scale set is outside `FTG_PROBE_RG` or has no tag. The run does not touch it and logs `sweeper fallback: action=skip instances=<N> id=<id> reason=scale set is not in the query result`.
4. An error counts as 1 error and logs `sweeper fallback: action=error`.

The fallback runs only when at least 1 instance is old. Young instances, and a scale set with no instances, do not start it. The fallback covers Flexible scale sets, whose members are VMs of type `microsoft.compute/virtualmachines` in the `Resources` table ([orchestration modes](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes)). Uniform scale set instances are open (section 12).

## 9. Log lines

The code logs through the Python `logging` module, logger name `sweeper`. The Functions host sends records of the Python root logger to Application Insights ([Python developer guide, logging and monitoring](https://learn.microsoft.com/en-us/azure/azure-functions/functions-reference-python?pivots=python-mode-decorators#logging-and-monitoring)). The `sweeper` logger must propagate to the root logger: the Functions host itself needs that, both for invocation-level telemetry (`Host.Results`, rule 5 above) and for the "sweeper stopped" alert of section 3.6, which fires on a missing `sweeper run:` trace. Application Insights, under the logs/metrics rule, is host health monitoring, kept for that alert; it is not our log of record ([spec 0003](0003-prober-core-spec.md) section 8.2; [CONTRIBUTING.md](../CONTRIBUTING.md)).

**The store handler (spec 0003 section 8.2).** The rule: the sweeper writes its own log to the store too, apart from the prober's rows, which the sweeper has none of. `sweeper/log_store.py` is the sweeper's own small copy of the prober's `StoreLogHandler` (it is a separate deployable unit, with its own `pyproject.toml` and `requirements.txt`, so it does not import `ftg_prober`): a `BlobLogHandler` buffers every record of the `sweeper` logger as 1 JSON object mapped to the OpenTelemetry log data model and writes the buffer as 1 JSONL blob through a `ContainerClient.upload_blob` (the same no-overwrite call the prober's `BlobStore` makes, spec 0003 section 9), to the key `experiment=<tag>/logs/provider=azure/date=<YYYY-MM-DD>/<ts>_<host>_<uuid>.jsonl`.

The handler takes every record of every logger that reaches the root logger, ours and the SDK loggers (`azure`, `urllib3`), at the configured level, and it has no logger-name filter. No message is filtered, redacted, or rewritten; the byte caps cut length only (spec 0003 section 8.2). Reason: the prober and the sweepers use no static credentials (an instance role on AWS, a managed identity on Azure), so a log has no credential to leak, and logs are never filtered. `Attributes` hold `logger` plus every `kv()` field of the message, parsed like the prober does; `run_id` moves to its own field.

If the attach fails for any reason (an import, `ContainerClient`, or `DefaultAzureCredential` raises), `function_app.py` prints 1 stdout line, does not retry until the next cold start, and `sweep()` still runs: a broken store log never stops the sweep.

`function_app.py` attaches the handler once, at cold start, only when `FTG_DATA_STORAGE_ACCOUNT` is set (section 4): with no value, the sweeper logs through `logging` only, as before this change, and `log.handlers` stays empty. When it is set, the handler is attached to the root logger (so that SDK records reach it too) and the `sweeper` logger still propagates: the same records keep reaching Application Insights, for the host-health uses above, and now also reach the store, our log of record. The handler keeps no background thread: the timer already runs `sweep_probe_resources` once a minute (section 3), so `function_app.py` flushes the handler once at the end of every invocation, in a `finally` block, whether or not `sweep()` raised. A failed flush writes 1 stdout line and keeps the records, capped at 10,000 records and 8 MiB (oldest dropped first, the same byte cap as spec 0003 section 8.2; a single record may take at most `min(cap / 64, 64 KiB)`, which is 64 KiB at the 8 MiB default: it first loses the tail of each Attribute value and of `run_id` beyond 256 characters, then (if still too long) all Attributes but the logger name, then (if still too long) the tail of `experiment`, `provider`, and `host` beyond 256 characters, then the tail of its `Body`, cut by its JSON-escaped length, and each cut ends with `...[truncated]`; see spec 0003 section 8.2; a message over 1 MiB is not parsed for `key=value` pairs), for the next flush, and never raises. `host.json` sets the default log level to `Information` and turns Application Insights sampling off, so that no health line is dropped. Sampling is on by default ([host.json reference](https://learn.microsoft.com/en-us/azure/azure-functions/functions-host-json#applicationinsightssamplingsettings)).

One line per resource:

```text
sweeper decision: action=<action> age_s=<age> type=<type> id=<id> reason=<reason>
```

- `action` is one of `keep`, `skip`, `deleted`, `already gone`, `delete accepted`, or `error`. A resource with the decision `delete` gets 1 line after its outcome is known, not 2.
- `age_s` is the age in whole seconds, or `unknown`.
- `reason` is the reason text of section 6, or the error message.

A row that the parser cannot read logs `sweeper decision: action=error reason=bad row (<message>)`.

One line per fallback action, as section 8 states.

One health line at the end of every run, also when the query fails:

```text
sweeper run: scanned N, deleted N, errors N
```

- `scanned` is the number of rows that the query returned.
- `deleted` is the number of resources with the action `deleted`, `already gone`, or `delete accepted`. Each of them is gone, or Resource Manager is removing it.
- `errors` is the number of actions `error`, plus 1 for a failed query.

This line is the signal for the "sweeper stopped" alert of spec 0002, section 6, item 5. An alert rule must fire when no `sweeper run:` trace arrives for 10 minutes, and when Application Insights shows failed requests of the function (section 5, rule 5).

**Code hash.** Each invocation logs 1 line `sweeper code_sha=<hash>` before the sweep (`sweeper/code_sha.py`). The hash is sha256 over the entries `<path>:<sha256 of the file>`, joined by `,` and sorted by path, for `function_app.py`, `host.json`, `requirements.txt`, and every `.py` file of the `sweeper/` package. The Azure stack computes the same value as the zip name hash and the `sweeper_code_sha` output (spec 0005, section 3.5), and `make verify/azure-sweeper` compares them after an apply. Reason: a failed zip deploy can leave old code with a clean plan.

## 10. Identity rights

The Function App uses a system-assigned managed identity. The code uses `DefaultAzureCredential`, which uses the managed identity on Azure and the Azure CLI login on a laptop ([DefaultAzureCredential](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.defaultazurecredential)). The sweeper takes no secrets.

The rights must match the `ftg-cleanup` role of the Azure stack (spec 0005): a custom role on `FTG_PROBE_RG` only, plus `Reader` at the subscription. That group also holds the stack resources, so the role can delete the prober VM. The `experiment` tag guard of section 1, rule 5, keeps the sweeper from it.

The `sweeper_cleanup` role assignment that grants these delete rights is unconditional: the Azure stack's `grant_billable_provisioning` variable does not gate it (spec 0005, section 4.1, R7; [ADR 0015](../decisions/0015-two-independent-controls-replace-the-terraform-allocation-guard.md) section 1 item 3a). The sweeper keeps its delete rights with the variable at `false`, so it can still remove a resource that an earlier run leaked.

| Call | Action |
|---|---|
| Resource Graph query | Read access on the resources. Resource Graph returns only the resources that the identity can read ([Resource Graph overview](https://learn.microsoft.com/en-us/azure/governance/resource-graph/overview#permissions-in-azure-resource-graph)). The `Reader` role gives it ([built-in roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/general#reader)). |
| Delete a VM | `Microsoft.Compute/virtualMachines/delete` ([compute permissions](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/compute)) |
| Delete a disk | `Microsoft.Compute/disks/delete` ([compute permissions](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/compute)) |
| Delete a network interface | `Microsoft.Network/networkInterfaces/delete` ([networking permissions](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/networking)) |
| Delete a capacity reservation | `Microsoft.Compute/capacityReservationGroups/capacityReservations/delete` ([compute permissions](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/compute)) |
| Scale a scale set to 0 | `Microsoft.Compute/virtualMachineScaleSets/write` ([compute permissions](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/compute)). For Flexible orchestration, Microsoft lists "Compute Virtual Machine Scale Sets Write, Compute VM Write, Network" as required RBAC ([orchestration modes](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes)). The role also holds `Microsoft.Compute/virtualMachines/write` and the network rights (open item 4). |
| Read VMs, instance views, scale sets and their instances, disks, and capacity reservation groups and reservations | `.../virtualMachines/read`, `.../virtualMachines/instanceView/read`, `.../virtualMachineScaleSets/read`, `.../virtualMachineScaleSets/virtualMachines/read`, `.../disks/read`, `.../capacityReservationGroups/read`, `.../capacityReservationGroups/capacityReservations/read` (all in the `ftg-cleanup` role of `rbac.tf`) |
| Scale set extras | `Microsoft.Compute/virtualMachineScaleSets/scale/action` and `Microsoft.Compute/virtualMachineScaleSets/virtualMachines/delete`, also in the role |
| Write a log object (section 9, when `FTG_DATA_STORAGE_ACCOUNT` is set) | Custom `<prefix>-data-writer` (blob write and add, no read, no delete) on the `data` container, with an ABAC condition that allows a write only under `experiment=<experiment>/logs/` (spec 0005 section 5.2). |

The read actions of the same types (`.../read`) come with `Reader`. `Reader` at the subscription also lets the SDK poll the status of a long-running delete.

## 11. Make targets and tests

`tasks/Makefile.sweeper` gives 2 targets. Both run `uv` through `mise exec --`, with `--locked`, so that `uv.lock` pins each package.

| Target | What it runs |
|---|---|
| `make sweeper/test` | `pytest` in `sweeper/azure` |
| `make sweeper/lint` | `ruff check .` and `ruff format --check .` in `sweeper/azure` |

The tests use fakes for the 3 clients: a row source, a deleter, and a scaler. They must run with no network and no credentials. They must cover at least: a young resource stays, an old resource is deleted, a 204 or a 404 logs `already gone` and counts as done, a resource with another tag value stays, a resource in another resource group stays, a run on a shared group deletes only the tagged probe resources and skips the untagged prober VM, disk, NIC, storage, and Function App and the resources of other owners, the delete order, the fallback runs only when a scale set has old instances, the health line, and the timer settings of section 3.

## 12. Decisions and open items

| # | Decision |
|---|---|
| S1 | One generic ARM delete by resource id for all 4 types, with 1 pinned GA api-version per type. One client, one code path. |
| S2 | A network interface has no `timeCreated`. The rule deletes a tagged network interface when no VM uses it and no young VM is in the result (section 6). |
| S3 | A resource of any other type with no `timeCreated` stays. The sweeper never deletes on an unknown age. |
| S4 | The age rule repeats the resource group and tag filters of the query, and allows only the 4 listed types. Defense in depth against a wrong query. |
| S5 | Each stage waits for its deletes inside 1 budget of 240 seconds per run, below the 5 minute `functionTimeout`. After the budget, a delete logs `delete accepted` and the run goes on. |
| S6 | The `deleted` count of the health line includes `already gone` and `delete accepted`. |
| S7 | The fallback scales only a scale set that the query returned. A scale set id from a VM that is not in the result is logged and left alone. |
| S8 | A failed query logs the health line with `errors 1` and fails the invocation, so that the failure shows as a failed request in Application Insights. |
| S9 | Application Insights sampling is off and the log level is `Information`, so that every health line arrives. |
| S10 | `AZURE_SUBSCRIPTION_ID` is required. Without it, the run fails before the query; the query never runs across other subscriptions. |
| S11 | The dev project pins Python to 3.12 (`>=3.12,<3.13`), the runtime of the Function App. The runtime pins live in `requirements.txt` and in `pyproject.toml`, and a test keeps them the same. |
| S12 | The threshold comparison is strict: a resource exactly at `FTG_MAX_AGE_SECONDS` is young. |
| S13 | Spec 0002, section 6, says "404 = already gone". Resource Manager answers a delete of a missing resource with 204. The sweeper treats both 204 and 404 as `already gone`. |

Open items:

1. Uniform scale set instances. Spec 0002, section 6, names a Uniform fallback for the case that P0 fails. Uniform instances are not rows of the `Resources` table. They are in the `computeresources` table ([supported tables](https://learn.microsoft.com/en-us/azure/governance/resource-graph/reference/supported-tables-resources)). This spec does not cover them. Decide after P0.
2. Resource Graph and `timeCreated` (spec 0002, P8). Confirm during the first deploy that Resource Graph returns `properties.timeCreated` for VMs, disks, and capacity reservations in the probe resource group, and in which time format.
3. The alert rule for the health line belongs to the Azure stack. This spec states the signal (section 9), not the rule.
4. Resolved. Scale to 0 on a Flexible scale set: `terraform/azure/rbac.tf` grants `Microsoft.Compute/virtualMachines/write` and the network rights to the `ftg-cleanup` role since the first commit, because Microsoft lists them as required for Flexible scale set writes. The first deploy still confirms that the `PATCH` to capacity 0 succeeds.
5. The first deploy must confirm that the `sweeper run:` line arrives in Application Insights at the `Information` level.

## Sources

- [Python developer guide](https://learn.microsoft.com/en-us/azure/azure-functions/functions-reference-python?pivots=python-mode-decorators)
- [Zip deployment](https://learn.microsoft.com/en-us/azure/azure-functions/deployment-zip-push#deployment-customization)
- [azurerm_linux_function_app](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/linux_function_app#zip_deploy_file-1)
- [Timer trigger](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-timer#ncrontab-expressions)
- [Function app timeout duration](https://learn.microsoft.com/en-us/azure/azure-functions/functions-scale#function-app-timeout-duration)
- [Resource Graph query scope](https://learn.microsoft.com/en-us/azure/governance/resource-graph/concepts/query-language#query-scope)
- [Resource Graph supported tables](https://learn.microsoft.com/en-us/azure/governance/resource-graph/reference/supported-tables-resources)
- [Resource Graph paging results](https://learn.microsoft.com/en-us/azure/governance/resource-graph/concepts/work-with-data#paging-results)
- [Resource Graph overview](https://learn.microsoft.com/en-us/azure/governance/resource-graph/overview#how-resource-graph-is-kept-current)
- [Configure monitoring](https://learn.microsoft.com/en-us/azure/azure-functions/configure-monitoring#configure-categories)
- [Virtual Machines - Get](https://learn.microsoft.com/en-us/rest/api/compute/virtual-machines/get)
- [Disks - Get](https://learn.microsoft.com/en-us/rest/api/compute/disks/get)
- [Capacity Reservations - Get](https://learn.microsoft.com/en-us/rest/api/compute/capacity-reservations/get)
- [Network Interfaces - Get](https://learn.microsoft.com/en-us/rest/api/virtualnetwork/network-interfaces/get)
- [Orchestration modes](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes)
- [Network interface](https://learn.microsoft.com/en-us/azure/virtual-network/virtual-network-network-interface#delete-a-network-interface)
- [Storage deletion errors](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machines/windows/storage-resource-deletion-errors)
- [Capacity Reservations - Delete](https://learn.microsoft.com/en-us/rest/api/compute/capacity-reservations/delete)
- [Resources - Delete By Id](https://learn.microsoft.com/en-us/rest/api/resources/resources/delete-by-id)
- [ResourcesOperations](https://learn.microsoft.com/en-us/python/api/azure-mgmt-resource/azure.mgmt.resource.resources.operations.resourcesoperations)
- [Virtual Machines - Delete (2024-07-01)](https://learn.microsoft.com/en-us/rest/api/compute/virtual-machines/delete?view=rest-compute-2024-07-01)
- [Disks - Delete (2024-03-02)](https://learn.microsoft.com/en-us/rest/api/compute/disks/delete?view=rest-compute-2024-03-02)
- [Network interfaces template reference (2024-05-01)](https://learn.microsoft.com/en-us/azure/templates/microsoft.network/2024-05-01/networkinterfaces)
- [Virtual Machines - Delete](https://learn.microsoft.com/en-us/rest/api/compute/virtual-machines/delete)
- [azure.core.exceptions](https://learn.microsoft.com/en-us/python/api/azure-core/azure.core.exceptions.resourcenotfounderror)
- [Virtual Machine Scale Sets - Update](https://learn.microsoft.com/en-us/rest/api/compute/virtual-machine-scale-sets/update)
- [host.json reference](https://learn.microsoft.com/en-us/azure/azure-functions/functions-host-json#applicationinsightssamplingsettings)
- [DefaultAzureCredential](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.defaultazurecredential)
- [Built-in roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/general#reader)
- [Compute permissions](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/compute)
- [Networking permissions](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/networking)
