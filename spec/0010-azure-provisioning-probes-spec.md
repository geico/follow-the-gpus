---
title: "Azure provisioning probes spec: pre-checks, capacity reservation, VMSS Flexible, and `simulate`"
date: 2026-10-11
---

# Azure provisioning probes spec: pre-checks, capacity reservation, VMSS Flexible, and `simulate`

**Status:** Phase 1 build · 2026-10-11, updated 2026-10-15. The all-or-nothing rule and the halving search of [ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md) (decision Z19) are implemented for `az_capacity_reservation` and `az_vmss_flex`.
**Parent:** [spec 0002](0002-gpu-capacity-tracker-spec.md), sections 4.3, 4.4, 4.5, 4.7, 6 item 1, 7.2, D4 to D8, D19, pilots P0, P2, P5, P6, P7. The core is [spec 0003](0003-prober-core-spec.md). The stack is [spec 0005](0005-azure-stack-spec.md). The sweeper is [spec 0007](0007-sweeper-spec.md). The `details` contract is [spec 0003](0003-prober-core-spec.md), section 4.4.
**Code:** `prober/ftg_prober/azure/prechecks.py`, `capacity_reservation.py`, `vmss_flex.py`, `sim.py`, `provisioning_adapter.py`; tests in `prober/tests/azure/allocation_*`.

A person must be able to build the Azure pre-checks and the 2 provisioning probes again from this file, spec 0002, and spec 0003.

## 1. Scope and rules

1. Three probe classes are plugins of the core (spec 0003, section 5): `az_precheck`, `az_capacity_reservation`, and `az_vmss_flex`.
2. Each `provisioning_probes` entry of `config/targets.yaml` is 1 target. The file lists the entries by probe type, `az_capacity_reservation` or `az_vmss_flex`, and the loader adds `probe_type` to each entry ([ADR 0022](../decisions/0022-azure-provisioning-probes-are-listed-by-probe-type-and-find-their-resources-by-tags.md)). Each of the two probes runs only the entries of its own type. Its own `simulate` key (ADR 0011 section 2) replaces the earlier `run_mode`. Superseded by [ADR 0010](../decisions/0010-one-experiment-file-of-modules-and-jobs-with-a-billable-guard.md), not implemented yet: 1 target becomes 1 job that names an allocation module.
3. A non-simulated entry runs the same code and makes real Azure calls, whatever its SKU: a GPU size, or a cheap non-GPU size used for testing (spec 0003, section 7.1).
4. A simulated entry (`simulate: true`) runs the same probe code against a fault injection adapter. It makes no Azure call (section 9).
5. All Azure SDK calls go through 1 adapter interface (section 3). Tests replace it with fakes. The tests use no network and no credentials.
6. The code must not write a local file. The prober service runs with `DynamicUser=yes` (spec 0005, A13). Only the store gets data.
7. Scope of the experiment: continental US commercial regions, GPU only (spec 0002, D1). Phase 1 runs a non-simulated entry on a cheap non-GPU size that supports capacity reservations (spec 0002, section 1), and a simulated entry.

## 2. Probe types and cadence

| `probe_type` | Class | Module | Cadence key | Default cadence |
|---|---|---|---|---|
| `az_precheck` | `PrecheckProbe` | `prechecks.py` | `az_provisioning` | `2h` |
| `az_capacity_reservation` | `CapacityReservationProbe` | `capacity_reservation.py` | `az_provisioning` | `2h` |
| `az_vmss_flex` | `VmssFlexProbe` | `vmss_flex.py` | `az_provisioning` | `2h` |

Spec 0003 open item P13 is decided here (decision Z1): the pre-check `probe_type` is `az_precheck`, and its cadence key is `az_provisioning`. The 3 probe types share the key, so that the pre-check runs at the start of each cycle (the config sets it: 2 h by default, 1 h as an example; spec 0002, 4.3 and D2).

The core maps all 3 probe types to the cadence key `az_provisioning` (spec 0003, section 3). `az_precheck` is a valid `probe_type` of the core (spec 0003, section 4.1, rule 5).

A shared helper class `AzureAllocationBase` sets `provider = "azure"` and `default_cadence = "2h"` but no `probe_type`. Thus the loader does not load it (spec 0003, section 5.1, rule 2).

## 3. The adapter interface

`provisioning_adapter.py` defines the `AllocationAdapter` protocol, its data types, and the real adapter `AzureAllocationAdapter`. The probes call only these methods.

| Method | Returns | Real call | RBAC action (spec 0005, 5.1) |
|---|---|---|---|
| `list_skus(region)` | `SkuInfo` list: `name`, `family`, `vcpus`, `capacity_reservation_supported`, `zones`, `restrictions` | Resource SKUs - List, `$filter=location eq '<region>'` | `Reader` at the subscription |
| `list_usage(region)` | `UsageInfo` list: `name`, `localized_name`, `current`, `limit` | Usage - List | `Reader` at the subscription |
| `create_reservation(rg, group, name, region, sku, zone, capacity, tags)` | an operation | Capacity Reservations - Create Or Update | `capacityReservations/write` |
| `get_reservation(rg, group, name)` | `provisioning_state`, `capacity`, or None on 404 | Capacity Reservations - Get | `capacityReservations/read` |
| `delete_reservation(rg, group, name)` | an operation | Capacity Reservations - Delete | `capacityReservations/delete` |
| `list_scale_sets(rg)` | `ScaleSetInfo` list: `id`, `name`, `capacity`, `sku_name`, `tags` | Virtual Machine Scale Sets - List (one resource group) | `virtualMachineScaleSets/read` |
| `list_reservation_groups(rg)` | `ReservationGroupInfo` list: `name`, `tags` | Capacity Reservation Groups - List By Resource Group | `capacityReservationGroups/read` |
| `get_scale_set(rg, name)` | `id`, `name`, `capacity`, `sku_name`, `tags` (the teardown reads the capacity of the selected scale set) | Virtual Machine Scale Sets - Get | `virtualMachineScaleSets/read` |
| `update_scale_set(rg, name, sku_name, capacity, tags)` | an operation | Virtual Machine Scale Sets - Update (`PATCH`) | `virtualMachineScaleSets/write` |
| `list_members(rg, scale_set_id)` | `Member` list: `id`, `name`, `provisioning_state`, `power_state` (both as Azure returns them, with no change of case), `error_code`, `error_message`, `os_disk_id`, `nic_ids` | Virtual Machines - List, with `$filter` and `$expand=instanceView` | `virtualMachines/read`, `virtualMachines/instanceView/read` |
| `delete_vm(rg, name)` | an operation | Virtual Machines - Delete | `virtualMachines/delete` |
| `tag_resource(resource_id, tags)` | nothing | Tags - Update At Scope, operation `Merge` | `Microsoft.Resources/tags/write` |

Rules:

1. An operation has `done()` and `error()`. `error()` returns the error of a done operation, else None. Neither call blocks. The real adapter wraps the SDK poller and reads its result only when the poller is done.
2. Every Azure SDK error (`azure.core.exceptions.AzureError`) becomes `AzureCallError` with `code`, `message`, the codes of the error `details`, and the HTTP status. This includes `ServiceRequestError` and `ServiceResponseError` (a connection reset or a timeout), which have no HTTP status and no `details`: their `code` is the type name and their `message` is the provider's, as returned, with no filtering. The teardown loops (the scale-set teardown and the reservation teardown) catch any `Exception`, so a connection reset or any other failure of a teardown call is logged, retried on the next poll, and the teardown ends in a verified or an unverified state, never early. An `AzureCallError` logs as a provider error. Any other exception logs at ERROR with its type, its message as returned, and the traceback, so a programming bug stays loud while the capacity is still released. A `DeserializationError` (a `ValueError`, not an `AzureError`: a response body that does not match the model) converts the same way, with its message as returned. Any other exception passes through `_sdk_call` unchanged, except one that has a `status_code` or an `error` attribute: that one converts too, so that fake SDK errors work; the teardown loops handle it as above. A 200 body that is not JSON (for example an HTML page from a proxy) raises `json.JSONDecodeError`, and a JSON array raises `AttributeError`; both reach the teardown loops this way. The reads before the scale-out or the reservation (the lists of the scale sets, the members, and the reservation groups) catch any exception too: the cell gets an `error` row and makes no write. A non-Azure exception gives `error_code` = its type name and logs the message as returned (event `allocation.unexpected_error`). An `AzureCallError` keeps its raw `error_code` and logs as a provider error, as before (sections 7.2 and 8.1). The creation of the tags client is inside `_sdk_call`.
3. The real adapter uses the `compute` client of the core (spec 0003, section 5.3). It takes the `resources` client of the core (spec 0003, section 5.3), a `ResourceManagementClient` from `azure.mgmt.resource.resources`, for the tags; the core builds it with the shared `credential` client and the subscription.
4. The `azure` extra adds `azure-mgmt-resource>=26.0` for the Tags call. Version 26 has the client in `azure.mgmt.resource.resources` (implementer's note).
5. Every call of the prober is in the 23 actions of the custom role or in `Reader` (spec 0005, 5.1). The prober needs no other action.
6. A `SkuInfo` restriction has `type`, `reason_code` (`reasonCode`), `locations` and `zones` (`restrictionInfo`), and `values`. Each keeps the value as returned. A missing list is empty. `zones` of a `SkuInfo` are the `locationInfo` zones of the region.

Facts from the Azure docs that the adapter relies on:

- The Resource SKUs `$filter` supports only `location` ([Resource Skus - List](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list)).
- A SKU restriction has a `type` of `Location` or `Zone`, a `reasonCode` of `QuotaId` or `NotAvailableForSubscription`, and `restrictionInfo` with the restricted `locations` and `zones` ([Resource Skus - List](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list)).
- A SKU restriction also has `values`, a list of strings. For a `Location` restriction, it lists the locations where the SKU is restricted ([Resource Skus - List](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list)).
- `family` is "The Family of this particular SKU". `locationInfo` lists the zones where the SKU is available ([Resource Skus - List](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list)).
- A Usage row has `currentValue`, `limit`, and `name` with `value` and `localizedValue` ([Usage - List](https://learn.microsoft.com/en-us/rest/api/compute/usage/list)).
- The VM list `$filter` allows only `'virtualMachineScaleSet/id' eq <scale set ID>`. `$expand=instanceView` "can only be specified if a valid $filter option is specified" ([Virtual Machines - List](https://learn.microsoft.com/en-us/rest/api/compute/virtual-machines/list)).
- For Flexible orchestration, Microsoft recommends the standard VM APIs, and names "the standard List VM API" for the details of many VMs in the scale set ([orchestration modes](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes)).
- Tags - Update At Scope is a `PATCH` on `{scope}/providers/Microsoft.Resources/tags/default`. "The 'merge' option allows adding tags with new names and updating the values of tags with existing names" ([Tags - Update At Scope](https://learn.microsoft.com/en-us/rest/api/resources/tags/update-at-scope)).
- Virtual Machine Scale Sets - Update is a `PATCH` with `sku` and `tags` in the body ([Virtual Machine Scale Sets - Update](https://learn.microsoft.com/en-us/rest/api/compute/virtual-machine-scale-sets/update)).

### 3.1 Member states

`list_members` reads the instance view of each VM. "In the VM InstanceView, there's an element within the `status` array in the form of `ProvisioningState/<state>[/<errorCode>]`". A power state has the form `PowerState/<state>`, for example `PowerState/running` ([states and billing](https://learn.microsoft.com/en-us/azure/virtual-machines/states-billing)).

| Member state | Rule |
|---|---|
| `placed` | The power state is `starting`, `running`, `stopping`, `stopped`, `deallocating`, or `deallocated`. Or the provisioning state is `Succeeded`. |
| `failed` | Not placed, and the provisioning state is `Failed` or the instance view has a `ProvisioningState/failed/<code>` status. `<code>` is the member error code. |
| `pending` | Any other member, for example power state `creating` or no power state. |

The power state `Creating` means "Virtual machine is allocating resources" and is not billed. `Starting` is billed ([states and billing](https://learn.microsoft.com/en-us/azure/virtual-machines/states-billing)). Thus a power state after `creating` shows that the allocation succeeded. The probe does not wait for OS provisioning (spec 0002, 4.5, step 2).

## 4. Pre-check (spec 0002, section 4.3)

`run_precheck(adapter, target, clock)` runs for 1 entry. It never raises for an Azure error, and it never raises for any other exception of `list_skus` or `list_usage` (for example a 200 body that is not JSON): that gives an `error` outcome with `error_code` = the exception type name, and the message goes to the log as returned (event `allocation.unexpected_error`, `call` = `list_skus` or `list_usage`). An `AzureCallError` keeps its raw code and logs as a provider error.

1. Call `list_skus(region)`. On an error: `outcome` = `error`, `error_code` = the raw code.
2. Find the SKU by name, case-insensitive. Not found: `outcome` = `restricted`, reason `size not offered in the region`.
3. The probe type of the entry decides what runs; the capability `CapacityReservationSupported` is a fact in the row and nothing reads it ([ADR 0022](../decisions/0022-azure-provisioning-probes-are-listed-by-probe-type-and-find-their-resources-by-tags.md)).
4. Restrictions. A `Location` restriction whose `locations` include the region, or that lists no location, gives `restricted`. If `locations` is empty, the check uses `values`. A `Zone` restriction applies only to a zonal entry whose zone is in the restriction `zones`. `error_code` = the raw `reasonCode`. A zonal entry whose zone is not in the SKU zones gives `restricted` with no code. A `restricted` row whose `error_code` is `NotAvailableForSubscription` can mean no capacity in the data center, not only a subscription-level block: the Storage SKU reference describes this `reasonCode` as "related to capacity at DC" ([Skus - List](https://learn.microsoft.com/en-us/rest/api/storagerp/skus/list)). Compute's [Resource Skus - List](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list) gives the same `reasonCode` no description, and no Compute page confirms that the Storage meaning applies to a VM size ([no-doc-says-azure-what-if-or-validate-checks-on-demand-capacity](../research/learnings/no-doc-says-azure-what-if-or-validate-checks-on-demand-capacity.md)). The row keeps its `restricted` classification as is; this only tells a reader what the raw code can mean.
5. Call `list_usage(region)`. On an error: `outcome` = `error`.
6. Free vCPUs of the family = `limit - current` of the usage row whose `name` equals the SKU `family`, case-insensitive. Free regional vCPUs = the same for the row named `cores` or "Total Regional vCPUs".
7. `n_ceiling` = `max(0, min(free values that exist)) // vcpus`. With no matching row or no `vCPUs` capability, the ceiling is null.
8. `outcome` = `success`.

The capability check follows Microsoft's example: `az vm list-skus -l eastus --resource-type virtualMachines` with a query for `CapacityReservationSupported` = `True` ([create a capacity reservation](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-create)). vCPU quotas "are arranged in two tiers for each subscription, in each region": "Total Regional vCPUs" and "the various VM size family cores". A new VM "must not exceed the vCPU quota for the VM size family or the total regional vCPU quota" ([vCPU quotas](https://learn.microsoft.com/en-us/azure/virtual-machines/quotas)). The usage name `cores` for the regional row and the match of the usage name to the SKU `family` are not verified (open item O3).

### 4.1 The `az_precheck` row

`PrecheckProbe.run` writes 1 row per `provisioning_probes` entry of both probe types, in config order. An exception outside the list calls (for example a SKU with no name) in 1 entry gives that entry an `error` row with `error_code` = the exception type name, `latency_ms` = 0, and `details.reason` = `Pre-check failed`. It logs `allocation.failed` with the message as returned, and the next entry still runs.

| Field | Value |
|---|---|
| `probe_type` | `az_precheck` |
| `simulated` | `ctx.simulated(entry)` (ADR 0011, replacing the earlier `run_mode`). |
| `sku`, `region`, `zone` | from the entry; `zone` as a string, or null |
| `placement_group_mode` | the entry value (`multi`; `single` is removed, [ADR 0021](../decisions/0021-provisioning-probes-measure-available-capacity-not-single-cluster-capacity.md)) |
| `n_requested` | the entry `n` |
| `n_placed` | null |
| `outcome` | `success`, `restricted`, or `error` |
| `error_code` | the raw code, or null |
| `latency_ms` | the time of the pre-check calls |
| `details` | the keys of the next table |

The `details` keys follow [ADR 0006](../decisions/0006-store-raw-provider-values-normalize-separately.md). The raw keys keep each value as returned. A number that Azure writes as a string, for example the `vCPUs` capability, becomes a JSON number. The derived keys stay next to the raw keys (decision Z18).

| Key | Kind | In the row | Value |
|---|---|---|---|
| `entry_probe_type` | derived | always | the probe type that lists the entry, `az_capacity_reservation` or `az_vmss_flex`; a size and a Region can be listed under both |
| `n_ceiling` | derived | always | the `n_ceiling` of step 7, or null |
| `reason` | derived | when the outcome is `restricted` or `error` | a probe-written text, for example `Location restriction` |
| `capacity_reservation_supported` | derived | after step 2 finds the SKU | true when the capability `CapacityReservationSupported` is the string `True`, case-insensitive; else false |
| `family` | raw | after step 2 finds the SKU | the SKU `family` string, or null |
| `vcpus` | raw | after step 2 finds the SKU | the `vCPUs` capability string as an integer; null if it is missing or not a whole number |
| `sku_zones` | raw | after step 2 finds the SKU | the zones of the SKU in the region, a list of strings; empty with no zone |
| `restrictions` | raw | after step 2 finds the SKU | each restriction of the SKU, also one that does not apply to the entry; an empty list with none. Each item has `type`, `reason_code`, `values`, `locations`, and `zones` (section 3, rule 6). |
| `family_current_vcpus`, `family_limit_vcpus` | raw | on `success` | `currentValue` and `limit` of the family usage row (step 6), as integers; null if Azure returns no such row |
| `total_current_vcpus`, `total_limit_vcpus` | raw | on `success` | `currentValue` and `limit` of the regional usage row (step 6), as integers; null if Azure returns no such row |
| `family_available_vcpus`, `total_available_vcpus` | derived | on `success` | `limit - current` of the same row; null if Azure returns no such row |

## 5. Provisioning probe loop and the series rule

Each provisioning probe runs this loop over the `provisioning_probes` entries of its own probe type, in config order. The scheduler runs the loop off its own thread, 1 call per Region with the entries of that Region, and 1 call at a time per Region across the 3 Azure probes that read the entries (`az_precheck`, `az_capacity_reservation`, `az_vmss_flex`); the Regions run at the same time ([spec 0003](0003-prober-core-spec.md), section 6B; [ADR 0020](../decisions/0020-provisioning-probes-run-in-region-lanes-off-the-signal-thread.md)). Before each entry, the loop stops when the process stops (`ctx.stopping()`, log `allocation.stopped`). The rows of each finished entry go to `ctx.emit`, and the scheduler writes them at once.

1. `simulated` = `ctx.simulated(entry)` (ADR 0011 section 7, item 6, replacing `ctx.run_mode(entry)`): the run-wide `--simulate` flag, or the entry's own `simulate` key. A simulated entry gets a `SimAdapter` and a virtual clock (section 9); otherwise the loop gets the real adapter and the wall clock.
2. Take the lock of (region, SKU), case-insensitive. Hold it until the teardown of the entry is verified or has timed out.
3. Run the pre-check of section 4. On `error`: log `allocation.skip` and write no row. The `az_precheck` row shows the failure. The pre-check runs before any list call of the selection below, so a `restricted` or a `quota` entry never needs its resource.
4. An entry of another probe type is not in the loop of this probe.
5. `restricted`: write a row with `outcome` = `restricted`, the pre-check `error_code`, `n_placed` = null, `details.skipped` = true. Make no allocation call.
6. `n_ceiling` 0: write a row with `outcome` = `quota`, `n_placed` = null, `details.skipped` = true. Make no allocation call.
7. Else request `n_requested` = min(entry `n`, `n_ceiling`). A null ceiling gives the entry `n`. Spec 0002 D8 bounds `n_requested` by quota. This is the first attempt. After `no_capacity` (the codes `AllocationFailed`, `ZonalAllocationFailed`, `OverconstrainedAllocationRequest`, and `OverconstrainedZonalAllocationRequest` of section 6), the next attempt halves the count, then a binary search runs, as in [ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md), section 2: 1 + ceil(log2(C)) attempts or fewer. The search stops after any other outcome (`quota`, `restricted_or_no_capacity`, which includes `SkuNotAvailable`, `timeout`, `error`) and after a teardown that is not verified. Each attempt ends with its teardown, verified, before the next one starts, and uses the same `probe_timeout_seconds` and `teardown_timeout_seconds`. Each real entry has its own deadline: `probe_timeout_seconds + teardown_timeout_seconds` from the start of the entry, before its precheck. The deadline is per entry, not per call, so an entry late in a Region lane gets the same time as the first entry, and each cell of a Region gets its search in every cycle. Before every attempt of a real entry, including its first attempt, the probe window is the time left minus `teardown_timeout_seconds`. The attempt gets that window as its probe timeout, and no allocation call starts when the window is shorter than the longest probe of the entry so far (or shorter than `poll_interval_seconds`, so never at or below 0). A search that stops after at least 1 attempt sets `details.search_stopped` = `"deadline"` on its last row. An entry whose window is too short before its first attempt (for example after a slow precheck, or after the pre-teardown below) gets 1 row with `outcome` = `error`, `n_placed` = null, `details.skipped` = true, `details.reason` = `call deadline` (the value of the rows already written), and `details.search_stopped` = `"deadline"`. The deadline does not bound the stop: the search checks `ctx.stopping()` before each attempt, so a stop waits only for the attempt in progress, at most a pre-teardown, the probe, and the teardown, which `TimeoutStopSec` covers (spec 0005, section 3.4; [spec 0003](0003-prober-core-spec.md), section 6B, rule 6). The deadline bounds the time of 1 entry, so that the entries of a Region lane fit the cadence: at the defaults an entry ends about 150 s after its start, and a Region of 7 entries ends its lane in about 7 x 150 s = 17.5 min. A deadline per call was needed only while the scheduler wrote the rows of a call after it returned and a call ran on through all its entries after a SIGTERM; the Region lanes ([ADR 0020](../decisions/0020-provisioning-probes-run-in-region-lanes-off-the-signal-thread.md)) removed both, and a deadline per call made each later real entry of a Region end at the deadline in every cycle. A stop loses no finished row: `ctx.emit` writes the rows of each finished entry at once, and a `SIGKILL` at the stop timeout would lose only the rows of the entry in progress. A simulated entry takes no wall time and is not bound by the deadline. The pre-teardown of step 2 of section 7.2 is in this budget: an `az_vmss_flex` attempt that ran a pre-teardown gets the time left after it, minus `teardown_timeout_seconds`, as its probe window, and if that window is too short the attempt does not start and the rules above for a stopped search apply. Each attempt is 1 row with `details.search_step`, `n_requested` = its count, and its own `outcome`. **Upward search ([ADR 0023](../decisions/0023-the-provisioning-search-goes-up-after-a-full-success.md)).** An entry with the optional key `n_max` goes up after a full success at C = `n_requested` of the first attempt, with C < U = min(`n_max`, `n_ceiling`; a null ceiling leaves `n_max`): the next attempt requests min(2 × `n_requested`, U), all or nothing, and each attempt ends with its verified teardown before the next one starts. The search stops at the first attempt that is not a `success` (no bisection), at U, at a stop, at the deadline, and after a teardown that is not verified. Going down (after a failure at C) and going up (after a success at C) exclude each other in 1 cycle. A row of an attempt above C has `details.search_direction` = `"up"` and `details.n_max`; `details.search_step` continues, and an entry without `n_max` writes the rows of today. The entry deadline is (1 + k) x (`probe_timeout_seconds` + `teardown_timeout_seconds`) with k = ceil(log2(`n_max` / `n`)) when the entry has `n_max`: 600 s for 2 to 16 at the defaults. The longest lane of the reference file is eastus, with 4 reservation entries with `n_max` and 1 scale set entry: 4 x 600 s + 1 x 150 s = 42.5 min. The stop timeout does not change. A step past the probe timeout ends the search as a `timeout` row. The dedupe key of an entry includes `n_max`. The reference file sets `n_max` on the `az_capacity_reservation` entries only: 16 VMs cannot boot inside the probe timeout of an `az_vmss_flex` entry. When the process stops (`ctx.stopping()`), the search starts no new attempt, not even the first one, and its last row has `details.search_stopped` = `"stop"`; an entry with no attempt yet gets the stop row of the field text below ([spec 0003](0003-prober-core-spec.md), section 6B, rule 6). A simulated entry gets the same stop row without `details.search_stopped`.
8. An exception in 1 entry, also in its pre-check, logs `allocation.failed` and the loop goes on with the next entry. A failure to build the real adapter (the compute and resources clients) is such an exception, but it gives the entry 1 `error` row, `error_code` = the raw code (an `AzureCallError` code) or the exception type name, `latency_ms` = 0, `details.reason` = `Adapter build failed`, `details.n_ceiling` = null. The message goes to the log as returned (event `allocation.failed`, or `provider.error` for an `AzureCallError`), never filtered. A failed build is not cached: the next entry builds again, so a failure of 1 build does not silence the others.

**Field `details.search_stopped`.** It is a string. Its values are `"deadline"` and `"stop"`. The key is absent from every row that the entry deadline of step 7 did not stop; it is never `null`, `false`, or another string. It is written on a row of a real (not simulated) entry in exactly 3 cases, and on `az_vmss_flex` and `az_capacity_reservation` rows; `aws_odcr` writes the same key with the same rule ([spec 0002](0002-gpu-capacity-tracker-spec.md), section 4.9). (1) The entry made at least 1 attempt, and the check before the next attempt finds the window too short: the key is set on the last attempt row of that entry (the row with the highest `details.search_step`), and the `outcome` of that row is unchanged. (2) The entry made no attempt because its window was too short before its first attempt: the entry has 1 row with `outcome` = `error`, `n_requested` = the entry `n`, `n_placed` = null, `latency_ms` = 0, `details.skipped` = true, `details.reason` = `call deadline`, and `details.search_stopped` = `"deadline"`. A search that ends for another reason (a success with no failed attempt before it, `quota`, `restricted_or_no_capacity`, `timeout`, `error`, an unverified teardown, or the end of the binary search) does not write the key. A simulated entry never writes it. The `az_precheck` probe never writes it. (3) The process stops (`"stop"`), for a real entry only. If the entry made at least 1 attempt, the key is set on its last attempt row, in the same way as case (1). If the entry made no attempt yet (the stop came during its precheck), the entry has 1 row with `outcome` = `error`, `n_requested` = the entry `n`, `n_placed` = null, `latency_ms` = 0, `details.skipped` = true, `details.reason` = `stop`, and `details.search_stopped` = `"stop"`. A simulated entry has the same stop row with no `details.search_stopped`. An entry that the loop has not reached when the stop comes gets no row.

**Series rule (decision Z4).** Probes for the same (region, SKU) run in series. This decides the recommendation of spec 0002, 4.5. Inside 1 call, entries run 1 after the other, and each teardown ends before the next entry starts. Across calls, a process-wide lock per (region, SKU) blocks a second probe of the same pair. The Region lock of the lanes ([spec 0003](0003-prober-core-spec.md), section 6B, rule 2) is coarser: 1 entry at a time per region, across the probes. Both provisioning probes use the same locks. A capacity reservation group "can have exactly one reservation for a specific VM size". In a zonal group, the limit is 1 per size per zone ([create a capacity reservation](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-create)). Thus 2 reservation probes of 1 size in 1 region and zone must not overlap. The series rule covers this.

**Dedupe (decision Z6).** The reservation probe runs 1 time per call for each distinct (`simulated`, SKU, region, zone, `n`). A reservation has no placement group mode, thus 2 entries with the same SKU, region, zone, and `n` give 1 reservation and 1 row. The VMSS probe runs each entry.

**1 `simulated` value per object.** One allocation call covers entries of different `simulate` values. Each of the 3 probes returns all rows of the call and writes no row. The core scheduler writes 1 object per `simulated` value (spec 0003, section 6, rule 6; ADR 0011 section 6, item 6, replacing `run_mode`). This replaces decision Z7.

## 6. Outcome classification

`vmss_flex.py` holds the table of spec 0002, section 4.5, as code. The reservation probe uses the same functions.

| Code | `outcome` when fewer than `n_requested` VMs are placed |
|---|---|
| `AllocationFailed`, `ZonalAllocationFailed`, `OverconstrainedAllocationRequest`, `OverconstrainedZonalAllocationRequest` | `no_capacity` |
| `SkuNotAvailable` | `restricted_or_no_capacity` |
| `ResourceQuotaExceeded` | `quota` |
| `OperationNotAllowed` with "quota" in the message | `quota` |
| any other code, or no code | `error` |

The VMSS probe does not stop at a `Failed` member that shows no code yet, while the scale operation is not done: it polls again, because the instance view can set the code a short time after the state. That timing is an assumption: no source in this repo shows it (no live log line, row, or research note), and Microsoft's page on [states and billing](https://learn.microsoft.com/en-us/azure/virtual-machines/states-billing) describes the `ProvisioningState/<state>[/<errorCode>]` status but does not say when the code appears. The first live run must show whether it holds. If it is false, the probe is still safe: the fallback is below. The wait ends when the scale operation is done, and the member with no code gives `error`, or when `probe_timeout_seconds` passes, and the row is `timeout`.

The quota codes come from Microsoft's page on quota errors. It shows `Code=OperationNotAllowed` with "Operation results in exceeding quota limits of Core", and `Code=ResourceQuotaExceeded` ([resource quota errors](https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/error-resource-quota)). The no-capacity codes are the codes of spec 0002, 4.5, plus `OverconstrainedZonalAllocationRequest`. Microsoft's allocation failure page names `AllocationFailed` and `ZonalAllocationFailed`, and "errors like `OverconstrainedZonalAllocationRequest` or `OverconstrainedAllocationRequest`" ([allocation failure](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machines/windows/allocation-failure)). Pilot P7 confirms all codes (decision Z16).

Classification of 1 allocation attempt with `n_requested` requested:

1. Timed out (the stop condition of section 7.2, step 4.4, is not met at the timeout, for example a member is still pending): `timeout`.
2. All `n_requested` placed: `success`, `error_code` = null.
3. 0 to `n_requested` − 1 placed: a failure ([ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md)). The table above gives the outcome. The probe never writes `partial`. `classify_allocation` in `vmss_flex.py` gives the outcome of the codes for 0 < placed < `n_requested` (decision Z19).

The raw `error_code` is the first code that the table knows, from the failed members, then the operation error and its detail codes. With no known code, it is the first code.

## 7. VMSS Flexible probe (`az_vmss_flex`)

### 7.1 Names and tags

| Item | Value |
|---|---|
| Probe resource group | `FTG_PROBE_RG`, else `ftg-probe`. The Azure stack sets `FTG_PROBE_RG` to its existing group `resource_group_name` (spec 0005, section 1). |
| Prefix | the resource group name without `-probe`, else `ftg` (decision Z8). Open for a group name that does not follow `<prefix>-probe`. |
| Scale set | `<prefix>-<sku slug>-<region>-<experiment hash>` (spec 0005, 4.2). The probe never computes this name: it selects the scale set by its tags (section 7.2, step 1). |
| Scale set tags | `experiment`, `sku` (the exact size name), `region` (lower case), `target`, `run_id` (spec 0005, 4.2) |
| Member tags | `experiment` = `ctx.experiment`, `target` = `<sku slug>-<region>-multi`, `run_id` = the call `run_id` |

Subnet and region: a scale set needs a subnet in its own region. The stack has `subnet_id`, in `location`, and the optional `probe_subnet_ids` map of subnets in other regions; a scale set uses the subnet of its own region (spec 0005, 4.1, R2, and 4.3). Thus a scale set probe runs only in a region that has a subnet. The plan fails for a non-simulated scale set entry in a region with no subnet, whatever its SKU. The reservation probe (section 8) needs no subnet and runs in any region.

### 7.2 Sequence

1. Select the scale set: list the scale sets in the probe resource group and keep those whose tags `experiment`, `sku`, and `region` equal `ctx.experiment`, the entry `sku`, and the entry region (compared in lower case). Exactly 1 match: probe it. No match: `error` row, `details.reason` = `no scale set for this experiment entry`. More than 1 match: `error` row, `details.reason` = `ambiguous: <k> scale sets match this experiment entry`. A failed list: `error` row, `error_code` = the raw code, `details.reason` = `scale sets not listable`. Any exception from the list (not only an Azure error, for example a 200 body that is not JSON) gives the same row with no write. A non-Azure exception gives `error_code` = its type name and logs the message as returned (event `allocation.unexpected_error`); an `AzureCallError` keeps its raw `error_code` and logs as a provider error. Each of these rows has `details.match` (the 3 tags) and `details.scale_sets` (the names of the matches, sorted; empty for no match and for a failed list) and is written with no Azure write. The search of section 5 stops after it.
2. List the members. On an error: `error` row, `details.reason` = `scale set members not readable`. Any exception from the list gives the same row with no write. A non-Azure exception gives `error_code` = its type name and logs the message as returned (event `allocation.unexpected_error`); an `AzureCallError` keeps its raw `error_code` and logs as a provider error. If the capacity is not 0 or members exist, run the teardown of 7.4 first. If it is not verified: `error` row, reason `scale set not empty before the probe`.
3. Start the clock. Send 1 `PATCH`: `sku` = {name, capacity `n_requested`}, `tags` = the scale set tags with `run_id` = the call `run_id` (spec 0005, 4.5, tags row). Do not wait for the operation.
4. Poll loop:
   1. List the members.
   2. Tag each member VM, OS disk, and NIC that is not tagged yet (section 7.3).
   3. Count `placed`, `failed`, and `pending` (section 3.1).
   4. Stop when 0 members are pending and either the members number `n_requested` or more, or the operation is done. Also stop at once when 1 member or more is `failed`: the attempt cannot place all `n_requested` ([ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md)).
   5. Stop with a timeout when `probe_timeout_seconds` have passed since step 3.
   6. Else sleep `poll_interval_seconds`.
5. After the poll loop, log 1 `provider.error` for each `failed` member that has a `ProvisioningState/failed` status message, with `call=member_instance_view`, `vm=<name>`, and the message as returned (section 11). The message never goes into a row.
6. Classify (section 6). `latency_ms` = the time from step 3 to the stop.
7. Teardown (section 7.4). It runs also after an exception.
8. Return the row. The scheduler writes it after the teardown (spec 0003, decision P4).

The probe never boots a workload and never waits for boot (spec 0002, D7).

### 7.3 Member tags (spec 0005, A21)

Scale set members do not get the tags of the scale set: the Flexible VM profile has no `tags` property (spec 0005, 4.5 and A21). Resources do not inherit the tags of a resource group or a subscription ([tag resources](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/tag-resources)). Pilot P0 confirms the member tags. The sweeper finds VMs, disks, and NICs by the `experiment` tag (spec 0007, section 5). Thus:

1. The first poll runs at once after the scale-out request, before any sleep. It tags each member it sees. Tag first, then poll.
2. Each later poll, and each teardown poll, tags the members, disks, and NICs that are not tagged yet. A tag call can fail, for example when the disk does not exist yet (not verified, open item O2). A failed tag call counts in `details.tag_errors` and runs again at the next poll. `details.tag_errors` counts failed calls, not resources that stay untagged. Observed: the member VM lists a NIC id before Azure starts the NIC write, so the first tag call returned `ResourceNotFound` (404); the call at the next poll succeeded, 10 s later, and the NIC was tagged before the teardown. See [the learning](../research/learnings/azure-flexible-scale-set-member-lists-nic-before-it-exists.md).
3. The tag call is Tags - Update At Scope with `Merge` and the 3 tags. Microsoft: write access to `Microsoft.Resources/tags` "lets you tag any resource, even if you don't have access to the resource itself" ([tag resources](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/tag-resources)).
4. The disk ID comes from `storageProfile.osDisk.managedDisk.id` of the VM. The NIC IDs come from `networkProfile.networkInterfaces`.

This changes 1 sentence of spec 0002, section 4.5, "Scale set profile": "Tags on the VM profile: `experiment`, `target`, `run_id`." The docs issue must replace it with: "Tags on the scale set: `experiment`, `target`, `run_id`. The prober tags each member VM, OS disk, and NIC with the same 3 tags right after the scale-out, before it polls (spec 0005, A21; spec 0010, 7.3)."

A member that appears and survives a prober crash before its tag call is not visible to the sweeper. Pilot P0 checks the member tags (spec 0005, open items).

### 7.4 Teardown

1. Send a `PATCH` with capacity 0 and no tags. Do not wait. Do not wait for the scale-out operation either: the probe never waits for boot (D7).
2. Poll loop, with the same poll interval:
   1. List the members. 0 members: read the scale set. Capacity 0: verified, stop. Capacity not 0: not verified yet; when the first scale-in send failed or the last scale-in operation is done, send the scale-in again, and keep polling.
   2. When the scale-in operation is done, or 30 seconds have passed, or no scale-in operation exists (its send failed), delete each remaining member 1 time with a VM delete. This includes `Failed` members. Skip a member in provisioning state `Deleting`.
   3. Tag the members of the list of this pass that are not tagged yet, including those that just got a delete.
   4. Stop when `teardown_timeout_seconds` have passed. Then the teardown is not verified. Log `allocation.teardown_unverified`.
3. Log the error of a done scale-in operation (`provider.error`, `call=update_scale_set`) as returned, once for each operation. The code goes into `errors`.

Bound of 1 teardown call: the calls of the teardown loop are bounded: `get_scale_set`, the scale-in `update_scale_set`, `list_members`, `delete_vm`, the tag call `tag_resource` of the tagging in the loop, and the pollers of the scale-in, the VM delete, and the tag call. Each gets a connect timeout and a read timeout of max(remaining teardown budget, 10 s), read when the call starts, and no retry (`retry_total=0`) ([azure-core `RequestsTransport`](https://learn.microsoft.com/python/api/azure-core/azure.core.pipeline.transport.requeststransport), keyword arguments `connection_timeout` and `read_timeout`; [azure-core `RetryPolicy`](https://learn.microsoft.com/python/api/azure-core/azure.core.pipeline.policies.retrypolicy), keyword argument `retry_total`). The adapter applies these only through the copy that `teardown()` takes (the copy shares the Resource Management client of the original, which `ctx.clients.azure("resources")` builds once and owns, so a teardown leaves no per-run session open; the factory's client lives for the process and nothing closes it. The adapter takes this client when it is made, before the teardown budget starts, so the tag call never waits for the lock of the client factory), and the loop tags the members through that copy too. Each pass sends the VM deletes first, and then tags the members of the list of the pass, including those that just got a delete, so a hung tag call does not delay the deletes of its pass; it can delay the next pass. A tag call that fails in the teardown is logged as returned and does not stop the pass. The scale-out `PATCH` and its poller, the reads before the scale-out (the list of the scale sets and the first `list_members`), and the tag calls outside the teardown keep the SDK defaults: the SDK retries (`RetryPolicy` defaults: `retry_total` 10 in all, `retry_connect` 3, `retry_read` 3, `retry_status` 3; source: `azure/core/pipeline/policies/_retry.py` in the installed `azure-core`, class `RetryPolicy`, `__init__`) and the 300 s transport timeouts, and such a tag call blocks until it ends or fails. The poll loop retries a failed teardown call at its next pass. Worst-case overrun: a timeout limits 1 socket operation, not the whole call, and the connect and the read each get it; so a call that starts with `r` seconds left ends within about 2 x max(`r`, 10 s), which is at most `r` seconds after the budget ends when `r` is 10 s or more, and at most 20 s when less. A paged `list_members` makes 1 request for each page, each with its own bound, so the overrun grows with the page count. A pass makes 1 `list_members` call. When it lists 0 members, the pass makes up to 2 more calls (`get_scale_set`, and `update_scale_set` when the capacity is other than 0, which includes an unknown capacity, and the previous scale-in is done or absent). When it lists members, the pass makes up to 1 delete for each member not yet deleted (it skips a member that is already delete-requested or deleting, and it skips the delete step during the scale-in grace period) and 1 tag call for each untagged resource (the VM, its OS disk, and each NIC). The loop checks the budget only after the pass; each call that starts after the budget has ended adds at most 20 s. The poller of a tag call is bounded for each request (each poll carries the bounded timeouts and no retry), but not in the number of polls, so a tag operation that stays in progress can run past the budget. A response that sends bytes slowly can last longer than its timeout. A scale set whose capacity is unknown (`None`, the response has no `sku.capacity`) gets a pre-teardown, the safe reading. In the teardown, such a response is an unknown capacity, not 0: the teardown is not verified, and the scale-in is sent again only when the previous scale-in is done or absent.
4. `details.teardown` = `verified`, `remaining` (the number of members in the last list; −1 when the last list failed, when `get_scale_set` failed, or when 0 members remain but the scale set reports a capacity other than 0 or no capacity), `deleted_directly`, `errors` (raw codes), `seconds`.

A failed teardown after the probe does not change the outcome. An unverified pre-teardown is an error row (section 7.2, step 2). The sweeper removes what remains (spec 0002, section 6).

### 7.5 The `az_vmss_flex` row

| Field | Value |
|---|---|
| `placement_group_mode` | the entry value |
| `n_requested` | `n_requested` of section 5, step 7; the entry `n` for a skipped entry |
| `n_placed` | `n_requested` for `success`; 0 for `no_capacity` and `restricted_or_no_capacity`, because the attempt is all or nothing ([ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md)); null for `timeout`, `quota`, `error`, and a skipped entry |
| `error_code` | section 6 |
| `details` | `n_config`, `n_ceiling`, `match`, `scale_set`, `n_placed` (the raw placed count at the stop, decision Z19), `n_failed`, `n_pending`, `search_step`, `search_direction` and `n_max` (on the rows above C of an upward search only), `search_stopped` (`"deadline"` on the last row of a search that the entry deadline of section 5, step 7, ends, and on the row of a skipped entry), `polls`, `member_codes`, `member_states`, `error_codes` (on a failed scale-out call), `teardown`, `tagged`, `tag_errors`; `n_placed_at_timeout` on `timeout`; `pre_teardown` when step 2 ran; `sim_step` when simulated; `skipped` and `reason` for a skipped entry |

`details.member_states` is the list of the raw `(provisioning_state, power_state)` pairs of the members at the last read, with a count: `[{"provisioning_state": "Failed", "power_state": null, "count": 1}, ...]`. A pair appears 1 time, in the order of first appearance; a state that Azure did not return is null; the values are not changed ([ADR 0006](../decisions/0006-store-raw-provider-values-normalize-separately.md)). The list has at most 1 entry for each member, so at most `n_requested` entries, and `n_requested` is at most `n_max` (16 in the reference file); an entry is about 70 bytes, so the list is at most about 1.2 KB, and in practice a few pairs. `n_failed` and `n_pending` stay. `details.error_codes` is the list of all codes of the error of the scale-out call (the update call that raised, or the done operation that failed), as returned and in order: the top code, then the detail codes (`AzureCallError.codes`). `error_code` stays the primary code of section 6. `member_codes` stays the sorted set of the member and operation codes.

Spec 0006, section 5.3, reads `n_placed / n_requested`, `error_code`, and `placement_group_mode`. `no_capacity` has `n_placed` = 0, so the panel shows fill 0. The panel shows the `search_result` of the whole search, out of the first `n_requested`, not the last attempt (spec 0006, section 5.4).

## 8. Capacity reservation probe (`az_capacity_reservation`)

### 8.1 Names and tags

| Item | Value |
|---|---|
| Group | `<prefix>-crg-<region>-<experiment hash>` (spec 0005, 4.2). The probe never computes this name: it selects the group by its tags `experiment` and `region` (lower case), as the scale set of section 7.2, step 1. Reasons of the `error` row: `no reservation group for this experiment entry`, `ambiguous: <k> reservation groups match this experiment entry`, `reservation groups not listable`; `details.reservation_groups` lists the names. Any exception from the list of the groups gives the `reservation groups not listable` row with no reservation write. A non-Azure exception gives `error_code` = its type name and logs the message as returned (event `allocation.unexpected_error`); an `AzureCallError` keeps its raw `error_code` and logs as a provider error. |
| Reservation | `<prefix>-cr-<sku slug>-<region>[-z<zone>]-<first 8 characters of run_id>` |
| Tags | `experiment`, `target` = `<sku slug>-<region>[-z<zone>]`, `run_id` |

The sweeper finds capacity reservations by the `experiment` tag (spec 0007, section 5).

### 8.2 Sequence

1. Start the clock. Create the reservation: `location` = the region, `sku` = {name, capacity `n_requested`}, `zones` = [zone] for a zonal entry, and the tags. The location must match the location of the group ([create a capacity reservation](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-create)).
2. An error at once: classify its code (section 6).
3. Else poll the operation every `poll_interval_seconds` until it is done, or until `probe_timeout_seconds`: then `timeout`, `n_placed` = null.
4. Operation error: classify its code. A no-capacity code gives `n_placed` = 0.
5. Operation done with no error: `success`, `n_placed` = `n_requested`. Read the reservation for `details.provisioning_state`.
6. Delete and verify, also after an exception:
   1. Send the delete. On an error, send it again at the next poll.
   2. Read the reservation. A 404 means deleted: verified. The adapter returns None for a 404 (that a read of a deleted reservation gives 404 is not verified).
   3. Stop when verified, or when `teardown_timeout_seconds` have passed.
7. `details.teardown` = `verified`, `errors`, `seconds`.

Microsoft: "Capacity reservation creation succeeds or fails in its entirety. For a request to reserve 10 instances, success is returned only if all 10 instances could be allocated." A request "could fail if you don't have the required quota for the VM series or if Azure doesn't have enough capacity" ([create a capacity reservation](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-create)). Thus this probe gives `success` or a failure, not `partial`. No VM uses the reservation, so the delete has no associated VM (spec 0007, section 7).

### 8.3 The `az_capacity_reservation` row

| Field | Value |
|---|---|
| `placement_group_mode` | `n/a` |
| `n_requested` | `n_requested`; the entry `n` for a skipped entry |
| `n_placed` | `n_requested` on `success`; 0 on `no_capacity` and `restricted_or_no_capacity`; null otherwise, also for a skipped entry. A create with no `scheduleProfile` succeeds or fails as a whole, so the probe does not read `currentCapacity`. |
| `details` | `n_config`, `n_ceiling`, `match`, `reservation_group`, `reservation`, `search_step`, `search_direction` and `n_max` (on the rows above C of an upward search only), `polls`, `provisioning_state` and `capacity` (on `success`: the state and the `sku.capacity` number that the read of the reservation returns, as returned, which can differ from `n_requested`; null when the read finds no reservation), `error_codes` (on a failed create call or operation: all codes of the error, as returned and in order, the top code and then the detail codes; `error_code` stays the primary code of section 6), `teardown`; `sim_step` when simulated; `skipped` (boolean, true) and `reason` (string) for a skipped entry. The `reason` is the pre-check reason for `restricted`, and `quota ceiling is 0` for `quota` |

## 9. Simulated entries and fault injection

`sim.py` defines `SimAdapter`, `VirtualClock`, the step names, and the default script. Rules:

1. A simulated entry gets a new `SimAdapter` per call. It makes no Azure call and builds no Azure client.
2. A `VirtualClock` moves only when the probe sleeps. A `hang` step reaches the timeout with no real wait. `latency_ms` of a simulated row is virtual time.
3. The entry key `sim_script` is a list of steps, or 1 step as a string. No key or an empty list gives the default script: `success`, `partial`, `allocation_failed`, `hang`, `quota`.
4. Each probe keeps 1 counter per entry in memory. Each run of the probe that reaches the allocation takes the next step and cycles the script. A restart starts at the first step.
5. The pre-check for a simulated entry: 1 SKU with the entry size, family `simFamily`, 2 vCPUs, no restriction, and 1000 free vCPUs. The SKU zones are the entry zone, or `1`, `2`, and `3` for a regional entry. Both usage rows have a current value of 0 and a limit of 1000. `CapacityReservationSupported` is true only for an `az_capacity_reservation` entry. The simulated resource group holds 1 scale set and 1 reservation group, tagged as OpenTofu tags them, so a simulated entry runs the selection of section 7.2, step 1, with no Azure call.
6. An unknown step gives an `error` row with `error_code` = `SimUnknownStep`.

| Step | VMSS path | Reservation path |
|---|---|---|
| `success` | `n` members, all placed after 2 virtual seconds. Row `success`, `n_placed` = `n`. | The create succeeds. Row `success`. |
| `partial` (not yet true for `az_vmss_flex`, see Z19; the code still writes `partial`) | `n // 2` members placed, the rest `Failed` with `AllocationFailed`. Row `no_capacity`, `n_placed` = 0, `details.n_placed` = `n // 2` (decision Z19). | The create fails with `AllocationFailed`, because a reservation succeeds or fails as a whole. Row `no_capacity`, `n_placed` = 0. |
| `allocation_failed` | All members `Failed` with `AllocationFailed`. Row `no_capacity`, `n_placed` = 0. | The create fails with `AllocationFailed`. Row `no_capacity`. |
| `hang` | All members stay `Creating`; the operation never ends. Row `timeout` after `probe_timeout_seconds`. | The create never ends. Row `timeout`. |
| `quota` | The scale-out request fails at once with `OperationNotAllowed` and the quota message of section 6. Row `quota`. | The create fails at once the same way. Row `quota`. |

For a simulated entry, the scale-in removes the placed and pending members. `Failed` members stay until the probe deletes them, so that the teardown deletes Failed VMs as in spec 0002, 4.5, step 5.

## 10. Config

Keys of the `azure` section that the provisioning probes read. The core schema allows them (spec 0003, section 3).

| Key | Default | Rule |
|---|---|---|
| `provisioning_probes` | none | The entries (spec 0003, section 3). |
| `probe_timeout_seconds` | 90 | Integer, 1 or more. The probe timeout X of spec 0002, 4.5, step 3. |
| `poll_interval_seconds` | 5 | Integer, 1 or more, and shorter than `probe_timeout_seconds`: the config check refuses an equal or shorter probe timeout, because the deadline floor `max(longest probe, poll interval)` would let no allocation call start. |
| `teardown_timeout_seconds` | 60 | Integer, 1 or more. |

A bad value raises an error. The scheduler logs `probe.failed`.

The sweeper threshold bounds the timeouts (decision Z17). `probe_timeout_seconds` + `teardown_timeout_seconds` must be 30 s or more under `FTG_MAX_AGE_SECONDS` (spec 0007, section 4). The defaults give 90 + 60 = 150 s, under the threshold of 180 s. The 30 s cover 1 poll interval of overshoot per loop and the call latency. The prober does not read the threshold. To raise a timeout, also raise `FTG_MAX_AGE_SECONDS` in the stack (spec 0005).

`provisioning_probes` is a mapping from a probe type (`az_capacity_reservation` or `az_vmss_flex`) to a list of entries. The old list shape, a `route` key, an unknown probe type, and 2 `az_vmss_flex` entries for 1 size and Region (any letter case, any zone) are faults of the config check, and `terraform/azure/probe.tf` refuses them with the same text. The same size and Region under both probe types is allowed ([ADR 0022](../decisions/0022-azure-provisioning-probes-are-listed-by-probe-type-and-find-their-resources-by-tags.md)).

Optional keys of a `provisioning_probes` entry:

| Key | Values | Effect |
|---|---|---|
| `sim_script` | list of steps, or 1 step | The script of section 9. |

`config/targets.yaml` has 2 control entries on a CPU size and the 31 Azure entries of the provisioning matrix ([spec 0002](0002-gpu-capacity-tracker-spec.md), section 4.10; [ADR 0018](../decisions/0018-the-provisioning-matrix-is-every-gpu-size-in-every-signal-region.md)): 5 GPU sizes in 9 regions, minus the 14 cells whose size the Region does not offer, with 1 `multi` entry for each size, including the H100 and RTX PRO 6000 sizes, which are `az_vmss_flex` entries, while the other sizes and the control are `az_capacity_reservation` entries ([ADR 0021](../decisions/0021-provisioning-probes-measure-available-capacity-not-single-cluster-capacity.md)). Every entry is `simulate: true` and carries no `run_mode` key: `terraform/azure/probe.tf`'s "active" rule reads `simulate` directly (ADR 0011, final). A non-simulated entry needs its own `allow_billable_provisioning: true` ([spec 0003](0003-prober-core-spec.md), section 3), checked by the prober's own config guard; the Azure stack's separate `grant_billable_provisioning` variable (spec 0005, section 4.1) must also be `true` before the stack grants the rights to create capacity, or the probe fails: with the grant off the stack plans no scale set and no reservation group, so the selection of section 7.2, step 1 (or section 8.1) finds none and writes its `error` row (`no scale set for this experiment entry` or `no reservation group for this experiment entry`) with no Azure write; when the resource exists but the role lacks the right, the call fails with the provider's error, written to the log as returned ([`AuthorizationFailed`](https://learn.microsoft.com/en-us/azure/role-based-access-control/troubleshooting)), even on a consented entry ([ADR 0015](../decisions/0015-two-independent-controls-replace-the-terraform-allocation-guard.md)). The control entries are `az_capacity_reservation` entries, because `Standard_D2as_v4` supports reservations: it is a Dasv4 size, and the Das v4 series supports capacity reservations. They share 1 reservation probe per call (section 5). A simulated entry runs the probe of the type that lists it. To exercise the scale set path with a non-simulated entry, list the entry under `az_vmss_flex` (spec 0005, 4.1); with no matching scale set or reservation group the probe writes the selection `error` row and makes no call.

## 11. Logging

Lines follow spec 0003, section 8. The provider's error message goes to the log as returned, never into a row: each failed Azure call logs 1 `provider.error` warning (spec 0003, section 8.1, item 6) with `probe_type`, `region`, `code`, `status`, `message`, and `call` (the adapter method, or `member_instance_view` for the message of a failed member status, which also carries `vm`), also a failed pre-check call, a failed scale-out or reservation create, and a failed teardown call. Events: `allocation.unexpected_error` (error: a non-Azure exception from a pre-check list or from a read before the scale-out or the reservation, with the traceback), `teardown.unexpected_error` (error: a non-Azure exception from a teardown call, with the traceback), `allocation.skip` (warning: a pre-check error or no route), `allocation.failed` (an exception in 1 entry, with the traceback), `allocation.tag_failed` (warning: 1 tag call), and `allocation.teardown_unverified` (warning).

## 12. RBAC

The calls of section 3 need these actions. All are in spec 0005, section 5.1: the custom role `<prefix>-capacity-allocator` on `resource_group_name`, or `Reader` at the subscription.

| Call | Action |
|---|---|
| Resource SKUs, Usage | `Reader` (read actions) |
| Create, read, delete a reservation | `Microsoft.Compute/capacityReservationGroups/capacityReservations/write`, `/read`, `/delete` |
| Read and update a scale set | `Microsoft.Compute/virtualMachineScaleSets/read`, `/write` |
| Scale-out side effects | `Microsoft.Compute/virtualMachines/write`, `Microsoft.Network/networkInterfaces/write`, the 3 `join/action` actions (spec 0005, A19) |
| List members with instance view | `Microsoft.Compute/virtualMachines/read`, `Microsoft.Compute/virtualMachines/instanceView/read` |
| Delete a member | `Microsoft.Compute/virtualMachines/delete` |
| Tag a VM, disk, or NIC | `Microsoft.Resources/tags/write` |
| Poll a long-running operation | `Reader` |

The prober needs no action outside spec 0005, section 5.1. Pilot P0 confirms the set with a CPU-sized scale-out.

## 13. Tests

`prober/tests/azure/allocation_*_test.py` with `provisioning_fakes.py`. No network, no credentials, no real sleep (except 1 short lock test). They cover:

1. Pre-check: the `n_ceiling`, the `Location` and `Zone` restrictions, a missing size, a missing zone, an SDK error, the raw usage numbers, a missing usage row, the raw restrictions of a size with restrictions and of a size with none, the names, the settings, the `az_precheck` rows, and all rows of a call with more than 1 `simulated` value, which the scheduler writes as 1 object per value.
2. Each outcome of the table for both probes, the inner detail code, the timeout from the config, the tags before the first sleep, a failed tag call, the teardown of members that survive the scale-in, an unverified teardown, a leftover scale set, a skipped entry for `restricted` and for quota 0, `n_requested` capped by quota, the dedupe, and a delete retry.
   Selection by tags ([ADR 0022](../decisions/0022-azure-provisioning-probes-are-listed-by-probe-type-and-find-their-resources-by-tags.md)): a scale set and a reservation group with an arbitrary name, another experiment's and an untagged resource ignored, the `error` rows for 0 matches, more than 1 match, and a failed list with no Azure write, the pre-check before any list call, each probe type running only its own entries, and an H100 under `az_capacity_reservation` and an A100 under `az_vmss_flex`. The real adapter reads REST-shaped list payloads (IDs scrubbed) through the real SDK models, and the list calls bind to the signatures of the real SDK operations (`provisioning_selection_test.py`).
3. Simulated: each step on both paths, the default script cycle, an unknown step, the run-wide `--simulate` flag, and the scheduler run of the repo config with no Azure client.
4. The series rule: the second entry of 1 (region, SKU) scales out only after the teardown of the first, and the lock blocks overlap across threads.
5. The real adapter with fake SDK objects: SKU and usage parsing, the member states, the VM list filter, the error codes, the reservation body, 404 to None, and a poller that does not block.

## 14. Decisions

| # | Decision | Reason |
|---|---|---|
| Z1 | `probe_type` `az_precheck`, cadence key `az_provisioning` (closes spec 0003 P13). | The pre-check runs at the start of each cycle (the config sets it: 2 h by default, 1 h as an example) with the provisioning probes. |
| Z2 | Each provisioning probe runs its own pre-check for its entries. `az_precheck` writes the rows. | The scheduler runs probes as separate calls in a fixed order. The read calls are free. No shared state between calls. |
| Z3 | A `restricted` entry and an entry with `n_ceiling` 0 get an allocation row with no allocation call. A pre-check `error`, and a size that the region does not offer, get no allocation row. | The panel then shows why a cell is empty. A failed pre-check and a missing size have no size to probe. |
| Z4 | The series rule of spec 0002, 4.5, is a requirement: 1 probe at a time per (region, SKU). | 2 entries of 1 size must not compete. A group holds 1 reservation per size. |
| Z5 | Superseded by [ADR 0022](../decisions/0022-azure-provisioning-probes-are-listed-by-probe-type-and-find-their-resources-by-tags.md): the entry key `route` is removed. The file lists the entries by probe type. | Before: the OpenTofu list and the runtime capability could disagree. |
| Z6 | The reservation probe dedupes entries with the same SKU, region, zone, and `n`. | A reservation has no placement group. 2 identical rows would skew the fill ratio. |
| Z7 | Superseded by spec 0003 decision P16 (ADR 0011 section 6, item 6, replacing `run_mode` with `simulated`): the core scheduler writes 1 object per `simulated` value, and each probe returns all rows. Before: a probe call that covers more than 1 mode wrote 1 object per mode, the first through the scheduler, the others itself. | A simulated row must never land in the same object as a real one. The split now lives in 1 place, the core. |
| Z8 | The name prefix comes from `FTG_PROBE_RG`. | The stack named the group `<prefix>-probe`. No new variable. The stack now deploys into an existing group with any name, so the rule gives `ftg` for most names. |
| Z9 | Superseded by ADR 0022: a simulated entry runs the probe of the type that lists it. Before: it routed to the VMSS path unless `route: reservation`. | An earlier build plan: Phase 1 runs the reservation path on a non-simulated, cheap-SKU entry and the VMSS path on a simulated entry. |
| Z10 | `n_requested` = min(entry `n`, `n_ceiling`). | Spec 0002 D8 bounds `n_requested` by quota. |
| Z11 | The row is written after the teardown. The row holds the teardown result. | The scheduler writes the rows of a call when `run` returns (spec 0003, P4). |
| Z12 | `n_placed` is null for `timeout`, `quota`, and `error`. | These rows carry no capacity signal (spec 0002, 4.5 table). |
| Z13 | After the scale-in request, the probe deletes the remaining members once the operation is done, or after 30 seconds, or when no scale-in operation exists (its send failed). | Failed members can stay after a scale-in (spec 0002, 4.7). |
| Z14 | The probes read no file and write no local file. | `DynamicUser=yes` (spec 0005, A13). |
| Z15 | Implementer's choice: the poll interval default. | Decision Z17 sets the 2 timeout defaults. |
| Z16 | `OverconstrainedZonalAllocationRequest` is a no-capacity code. | Microsoft names it next to `OverconstrainedAllocationRequest`. Spec 0002, 4.5, lists the regional code only. Both codes can also come from constraints with capacity present, so the mapping is a decision. P7 confirms it. |
| Z17 | Defaults: `probe_timeout_seconds` = 90, `teardown_timeout_seconds` = 60. The sweeper threshold stays 180 s. Their sum stays 30 s or more under the threshold (section 10). Closes O1. | The sweeper must not delete the resources of a probe in progress (spec 0002, section 6, item 4). The clock starts before the allocation request, so each probe resource is younger than the probe. The worst-case orphan lifetime stays at the threshold + 1 min + the delete latency (spec 0002, section 6, item 3). A higher threshold makes it longer. The probe waits for the allocation, not for the boot (section 3.1). The 90 s timeout still ends the hang of spec 0002, 4.7. The cost: an allocation that needs more than 90 s gives `timeout`, with no capacity signal. A teardown that is not verified in 60 s leaves the rest to the sweeper. Pilot P2 measures the time in `Creating` and can change the 3 numbers together. |
| Z18 | The `az_precheck` row stores the raw usage numbers, the SKU zones, and each SKU restriction next to the derived values (section 4.1). The keys are snake case, as the other `details` keys. | ADR 0006: a later scale reads the raw rows, so the rows need no new run. A restriction that does not apply now can explain a later change. |
| Z19 | Each attempt is all or nothing. Fewer than `n_requested` placed is a failure: the VMSS probe stops at the first `failed` member, tears down, and writes `n_placed` = 0 with the raw count in `details.n_placed`. A `no_capacity` attempt halves the count for the next attempt (section 5, step 7). Implemented for both probes. | [ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md). The reservation path is already all or nothing (section 8.2). |

## 15. Edits for the docs issue

The docs issue must make these edits after this file merges. Each follows a decision above.

Spec 0002:

1. Section 4.5, "Scale set profile": replace "Tags on the VM profile: `experiment`, `target`, `run_id`." with the text of section 7.3.
2. Section 4.5, "Recommendation (not yet decided)": state that probes per (region, SKU) run in series (Z4).
3. Section 4.3: name the `probe_type` `az_precheck` and its row (section 4.1).
4. Section 7.2, `probe_type` row: add `az_precheck`.
5. Section 4.5, probe sequence step 4 "write the record": the row is written after step 5 (Z11).
6. Section 10: close "Probe concurrency across targets" (Z4). "Probe timeout X" has the default 90 s (decision Z17).
7. Section 4.5, classification table: add `OverconstrainedZonalAllocationRequest` to the `no_capacity` row (Z16).

Spec 0003 and the core code (done):

1. Section 3, `*.cadence` rule: `az_precheck` also uses `az_provisioning`. Code: `cadence_key` in `config.py`.
2. Section 4.1, rule 5: `probe_type` is 1 of 12 values. Code: `PROBE_TYPES` in `record.py`, after the spec 0002 table edit, because a test compares them.
3. Section 6, rule 6: the scheduler splits the rows of 1 call by `simulated` value into 1 object per value (ADR 0011 section 6, item 6). Then the provisioning probes return all rows (Z7).
4. Section 10, extras: `azure` adds `azure-mgmt-resource`.
5. Section 11, P13: decided by Z1.
6. Section 7.2: the core `sim` generator writes `az_precheck` rows (spec 0003 section 7.2; `az_precheck` in `sim.py`).

## 16. Open items

| # | Item | State |
|---|---|---|
| O1 | Probe timeout and the sweeper threshold. The old defaults, a probe timeout of 180 s plus a teardown timeout of 120 s, exceeded the sweeper age threshold of 180 s (spec 0007, section 4). Spec 0002, section 6, item 4, says the threshold must exceed a probe cycle. | Closed by decision Z17: 90 s plus 60 s, under the threshold of 180 s. Pilot P2 can revise the 3 numbers together. |
| O2 | Tags on a member in `Creating`. A tag call can fail until the resource exists. | Observed for a NIC (`ResourceNotFound`, fixed by the retry at the next poll). A disk is not observed. P0 still reads the member tags of a CPU-sized scale-out. |
| O3 | The usage name `cores` for "Total Regional vCPUs", and the usage name equal to the SKU `family` (not verified). | Open. P7 reads `az vm list-usage -o json`. The code also matches the localized name. |
| O4 | The exact error codes of a failed scale-out member and of a failed reservation. | Open. P7. |
| O5 | The SIGTERM stop during a provisioning probe. A probe call can last `probe_timeout_seconds` plus the teardown. The core stop flag is not visible to a probe. | Open. The sweeper covers a kill during a probe (P5). |
| O6 | The name prefix in an existing resource group (Z8). With a `name_prefix` other than `ftg`, or a group name that ends in `-probe`, the prober reads the wrong scale set and reservation group names. | |

## 17. Sources

- [Resource Skus - List](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list)
- [Usage - List](https://learn.microsoft.com/en-us/rest/api/compute/usage/list)
- [vCPU quotas](https://learn.microsoft.com/en-us/azure/virtual-machines/quotas)
- [Create a capacity reservation](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-create)
- [Virtual Machines - List](https://learn.microsoft.com/en-us/rest/api/compute/virtual-machines/list)
- [Virtual Machine Scale Sets - Update](https://learn.microsoft.com/en-us/rest/api/compute/virtual-machine-scale-sets/update)
- [Orchestration modes for Virtual Machine Scale Sets](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes)
- [States and billing status](https://learn.microsoft.com/en-us/azure/virtual-machines/states-billing)
- [Tags - Update At Scope](https://learn.microsoft.com/en-us/rest/api/resources/tags/update-at-scope)
- [Use tags to organize your Azure resources](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/tag-resources)
- [Resource quota errors](https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/error-resource-quota)
- [Allocation failure](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machines/windows/allocation-failure)

Sources of single facts in the text:

- [`SkuNotAvailable` means size not available in the location or zone, or no Spot capacity](https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/error-sku-not-available)
- [`Standard_B2s` supports capacity reservations; The Das v4 series supports capacity reservations](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-overview)
- [`azure-mgmt-resource` 26 has `ResourceManagementClient` in `azure.mgmt.resource.resources`](https://pypi.org/project/azure-mgmt-resource/)
- [RBAC actions `capacityReservationGroups/capacityReservations/read`, `/write`, `/delete` exist; RBAC actions `virtualMachineScaleSets/read` and `/write` exist](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/compute)
- [RBAC action `Microsoft.Resources/tags/write` covers tag merge](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/management-and-governance)
- [`Reader` covers Resource SKUs, Usage, and operation polling reads](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/general)
- [`Standard_D2as_v4` is a Dasv4 size; `Standard_D2as_v4` is a non-GPU size](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/dasv4-series)
