# 0008. Name roles, probes, and cleanup by what they do and cost

- Status: accepted
- Date: 2026-10-12

## Context

Some names hide what a thing does or what it costs. The Azure custom role `prober-probe` creates and deletes VMs, NICs, and capacity reservations, which costs money. "Deep probe" does not say that the probe allocates capacity. `run_mode: dry` makes real cloud calls, so it is not a dry run, but `make quota/azure` is a real dry run. One word had 2 meanings.

## Decision

1. One word has one meaning. The tables below are the vocabulary. New code, specs, and docs use it.
2. A role is named by the action it allows, not by the component that holds it.
3. "Dry run" means only a command that reads and prints and changes nothing.
4. Run modes have 3 values. Each value says where the calls go and if the rows count.
5. Do not call a mode "dry" if it makes real cloud calls.

Superseded by [ADR 0010](0010-one-experiment-file-of-modules-and-jobs-with-a-billable-guard.md), not implemented yet: the table below is replaced by a `--mode sim|real` flag and a job list. A CPU stand-in size is an ordinary job, not a mode. Superseded further by [ADR 0011](0011-store-and-per-job-simulate-replace-run-mode.md): there is no `--mode` flag; `--store`, a run-wide `--simulate` flag, and per-job `simulate`/`allow_allocation` keys replace it.

| Run mode | Calls | Allocation probes | Rows |
|---|---|---|---|
| `local` | No cloud calls. | None run. | Generated on this machine. Dashboard and test data. Never results. |
| `cloud-cpu` | Real cloud calls. Signal probes read as usual. | Create and delete small non-GPU (CPU) stand-in sizes, to prove the path. | Not results. |
| `cloud-gpu` | Real cloud calls. | Create and delete the target GPU sizes. This is billable at GPU rates. | Results. |

| Term | Meaning |
|---|---|
| Signal probe | A probe that only reads a provider API or a public file. It creates nothing. It is free, apart from API rate limits. |
| Precheck | The read-only check of the SKU list and the usage before an allocation probe. It is free. |
| Allocation probe | A probe that creates real capacity, measures the result, and deletes the capacity. It costs money. It replaces "deep probe". |
| Cleanup | The action of the sweeper: delete old allocation resources and set allocation scale sets to capacity 0. |

| Azure role | Holder | Allows |
|---|---|---|
| `<prefix>-capacity-allocator` | prober | Create and delete capacity reservations and VMs. Scale the allocation scale sets. Resource group only. |
| `<prefix>-cleanup` | sweeper | Delete old allocation VMs, disks, NICs, and reservations. Resource group only. |
| `<prefix>-placement-score-reader` | prober | Read placement scores. Subscription. |
| `Network Contributor` (subnet joiner) | prober | Join the scale-set NICs to the subnet, when the subnet is in another resource group. Subnet only. |
| `Storage Blob Data Contributor` (data writer) | prober | Write rows to the `data` container. (Superseded 2026-10-15 by a later change: the grant is now the custom role `<prefix>-data-writer` with an ABAC path condition; see spec 0005 section 5.) |
| `Storage Blob Data Reader` (code reader) | prober | Read the `code` container. |

The Terraform role assignments use the same words: `prober_capacity_allocator`, `prober_data_writer`, `prober_code_reader`, `prober_subscription_reader`, `prober_placement_score_reader`, `prober_subnet_joiner`, `sweeper_cleanup`, `sweeper_subscription_reader`. The budget resource is `budget.stack`, because it covers the whole resource group.

## Consequences

- No Terraform state exists, so the address changes need no `moved` blocks.
- The Azure names of the capacity reservation groups and scale sets do not change. The prober builds them by convention.
- The stored `run_mode` value and the partition path `run_mode=<value>` change. The old values were `sim`, `dry`, and `live`.
- `sim` names only the generator that writes `run_mode=local` rows: the `ftg-prober sim` command, the `data/sim` folder, and `SimAdapter`. It is not a run mode.
- Phase 2, not in this change: the `probe_type` values, the `deep_probes` config key, the Python names that contain `deep`, the `--probe` flag, the `azure/deep-probes` preflight check ID, the file names `0010-azure-deep-probes-spec.md`.
- Phase 3, not in this change: `FTG_PROBE_RG`.
