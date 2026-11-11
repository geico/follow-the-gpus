# 0021. Provisioning probes measure available capacity, not single-cluster capacity

- Status: accepted
- Date: 2026-10-16
- Amends: [0018](0018-the-provisioning-matrix-is-every-gpu-size-in-every-signal-region.md) (the matrix no longer has a `single` entry)

## Context

The scale set sizes of the Azure provisioning matrix (the H100 and RTX PRO 6000 sizes) had 2 entries each: `multi` and `single`. The `single` entry asked for a scale set with `singlePlacementGroup` set to `true`. A single placement group scopes an allocation to 1 cluster ([Microsoft](https://learn.microsoft.com/troubleshoot/azure/virtual-machine-scale-sets/allocationfailed-or-zonalallocationfailed)). Spec 0002, D6, kept it to see how deep a cluster-scoped request goes, next to the regional answer of `multi`.

3 facts changed the cost of that entry:

1. **Flexible rejects it.** The orchestration modes page lists "single placement group" as unsupported in Flexible ([Microsoft](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes)). At the apply of 2026-10-15, the create call of an H100 scale set with `true` failed with `OperationNotAllowed` ([learning brief](../research/learnings/azure-flexible-scale-set-rejects-single-placement-group.md)).
2. **Uniform is a second path.** Placement groups apply only to Uniform scale sets ([Microsoft](https://learn.microsoft.com/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-use-availability-zones)). A Uniform path needs its own create, scale, tag, and delete code. Its instances are not standalone VMs with their own tags, so the sweeper needs more work too. An earlier unmerged change built this path.
3. **The answer is near zero.** The project measures the capacity that exists per size and Region. Under the current contention, the project expects the one-cluster answer to be zero almost always, so the entry adds cost and almost no signal.

## Options

| Option | Result |
|---|---|
| A. Keep `single` with a Uniform scale set | A second code path, more sweeper work, and a signal that is near zero. |
| B. Keep `single` and let the platform pick the value on Flexible | The entry no longer measures a single cluster, so it repeats `multi` under another name. |
| C. Drop `single` | One path. The signal is regional capacity. |

## Decision

C. The provisioning probes measure the available capacity per size and Region, not the capacity that fits in 1 cluster. Only `multi` scale set probes exist. A scale set is Flexible, with `singlePlacementGroup` set to `false`.

The key `placement_group_mode` stays in the file and in the rows, with the value `multi` for a scale set entry, so the live targets files that set it keep working. The config check and the Terraform plan reject the value `single` with a message that names this ADR.

## Consequences

- **Lost signal.** The project no longer measures one-cluster capacity, which a workload that needs InfiniBand between all its VMs depends on ([Microsoft](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes) lists InfiniBand as supported only in Uniform with a single placement group). A regional answer above zero does not show that a cluster has the room.
- **Fewer entries.** Each scale set cell has 1 entry, not 2. The matrix of ADR 0018 has 45 Azure entries (27 reservation, 18 scale set), not 63, and 63 entries in all, not 81.
- **No Terraform destroy.** The `single` scale set could never be created on Flexible, so no state holds one. The `multi` scale sets keep their keys and settings.
- **Old rows.** Rows that already hold `placement_group_mode` = `single` stay in the store. The row schema still accepts the value, so a reader does not fail on them.
- **Can be revisited.** A later decision can bring the probe back as a Uniform scale set. The earlier unmerged change is the starting point, and it needs the sweeper work above.
