# 0022. Azure provisioning probes are listed by probe type and find their resources by tags

- Status: accepted
- Date: 2026-10-16
- Amends: [0010](0010-one-experiment-file-of-modules-and-jobs-with-a-billable-guard.md) (the shape of `azure.provisioning_probes`), [0018](0018-the-provisioning-matrix-is-every-gpu-size-in-every-signal-region.md) (which entries use which probe)

## Context

An Azure provisioning entry chose its probe in 3 places that could disagree:

1. The entry key `route` (`reservation` or `vmss`).
2. The capability `CapacityReservationSupported` that the pre-check read at run time, when `route` was not set.
3. The OpenTofu list `reservation_eligible_skus`, which decided whether the stack built a scale set.

The prober also found its scale set by computing its name, `<prefix>-<size slug>-<region>-multi`, with a copy of the OpenTofu formula. The name had no experiment in it, so 2 experiments in 1 resource group would have used the same scale set. The word "route" also means something else in networking.

## Decision

1. **The file lists entries by probe type.** `azure.provisioning_probes` is a mapping from a probe type to a list of entries. The probe types are `az_capacity_reservation` (create a capacity reservation of `n`, record the result, delete it) and `az_vmss_flex` (scale a scale set from 0 to `n`, record the result, scale it to 0). The entry keys do not change, except that `route` is removed. The loader flattens the mapping and adds `probe_type` to each entry. The key stays `az_vmss_flex`, the same as the `probe_type` of the rows, so the store and the dashboard keep one name.
2. **No run-time choice.** The probe type of the list decides what the entry does. The capability is a fact in the `az_precheck` row, and nothing reads it.
3. **OpenTofu builds the resources.** It builds one scale set for each active `az_vmss_flex` entry, of any size, and one reservation group for each Region of the `az_capacity_reservation` entries. `reservation_eligible_skus` is removed. The names hold the first 8 characters of the hash of the experiment name. The scale set tags are `experiment`, `sku` (the exact size name), `region` (lower case), `target`, and `run_id`. The reservation group tags are `experiment` and `region`.
4. **The prober selects by tags.** `az_vmss_flex` lists the scale sets in the probe resource group and keeps those with the tags `experiment`, `sku`, and `region` of its entry. A reservation probe lists the reservation groups and selects by `experiment` and `region`. Exactly 1 match is probed. No match, more than 1 match, or a failed list gives 1 `error` row and no Azure write. The prober never computes a name.
5. **The pre-check runs first.** The `restricted` and `quota` rows are written before any list call. Each probe type pre-checks only its own entries.
6. **The config check and the plan refuse the old shape.** They refuse the old list, `route`, an unknown probe type key, and 2 `az_vmss_flex` entries for 1 size and Region (any letter case, any zone). The same size and Region under both probe types is allowed.

## Options

| Option | Result |
|---|---|
| A. OpenTofu writes a file with the scale set IDs | A new file in the config archive, and a second source of truth for what exists. |
| B. The experiment file names each scale set | The prober would find a resource by a name that the file author must keep in step with OpenTofu. |
| C. Select by tags (this decision) | No name is shared. Another experiment's resources are never selected. |

The tag selection holds only if no Azure Policy assignment adds, changes, or removes tags on the probe resource group. Check this before you deploy.

## Consequences

- **One replace.** The names change, so the apply replaces the empty scale sets, their policy updates, and the empty reservation groups. All of them hold no capacity. Apply between probe cycles: a replace during a probe fails the probe.
- **Old code, new file, and the reverse, fail loudly.** The plan fails on the old shape (a targets check), and the prober fails the config check. Neither plans a destroy.
- **Same prefix, same group.** Two stacks in 1 resource group still need different `name_prefix` values. A new experiment name replaces every scale set and reservation group, by design.
- **Rows.** The `az_precheck` details lose `route` and `route_source`, and gain `entry_probe_type`. The scale set and reservation rows lose `details.route` and gain `details.match`.
