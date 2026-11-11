# Decisions

Each architecture decision record (ADR) is 1 file: `NNNN-<slug>.md`. Use the next free number. `make lint/decisions` fails when 2 files share a number.

| ADR | Decision |
|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions |
| [0002](0002-makefiles-follow-cloud-posse-conventions.md) | Makefiles follow the Cloud Posse conventions |
| [0003](0003-specs-are-the-source-of-truth.md) | Specs are the source of truth |
| [0004](0004-dashboard-stack.md) | The dashboard is a static TypeScript page fed by a DuckDB CLI snapshot (the view layer superseded by [0026](0026-the-board-view-is-react-with-shadcn-ui-in-the-nocturne-dark-theme.md), the data path by [0027](0027-the-board-reads-windows-from-a-duckdb-request-handler.md)) |
| [0005](0005-prober-and-sweeper-in-python.md) | The prober and the sweeper are Python |
| [0006](0006-store-raw-provider-values-normalize-separately.md) | Probe rows keep raw provider values; normalization is a separate layer |
| [0007](0007-mise-pins-every-tool-and-the-hooks-stay-plain.md) | Mise pins every tool, and the git hooks stay plain |
| [0008](0008-name-things-by-what-they-do-and-cost.md) | Roles, probes, and cleanup are named by what they do and cost |
| [0009](0009-allocation-probes-are-all-or-nothing-with-halving.md) | Allocation probes are all or nothing, a failure halves the count, and the AWS On-Demand probe uses an ODCR only |
| [0010](0010-one-experiment-file-of-modules-and-jobs-with-a-billable-guard.md) | One experiment file of modules and jobs, a billable guard, and sim versus real (section 7, the Terraform guard, superseded by [0015](0015-two-independent-controls-replace-the-terraform-allocation-guard.md)) |
| [0011](0011-store-and-per-job-simulate-replace-run-mode.md) | `--store` and a per-job `simulate` flag replace `--mode sim` or `real` (section 3 rule 4 and section 7 item 7's Terraform half superseded by [0015](0015-two-independent-controls-replace-the-terraform-allocation-guard.md)) |
| [0012](0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md) | Update the code and the config with a VM-side pull and restart, not a VM replacement |
| [0013](0013-the-prober-runs-on-a-vm-not-a-function-runtime.md) | The prober runs on a VM, not a function runtime |
| [0014](0014-opentofu-is-the-iac-tool.md) | OpenTofu is the IaC tool |
| [0015](0015-two-independent-controls-replace-the-terraform-allocation-guard.md) | Two independent controls replace the Terraform allocation guard: a per-job file flag the prober checks, and a `grant_billable_provisioning` stack variable |
| [0016](0016-rename-the-consent-key-to-allow-billable-provisioning.md) | Rename the per-job consent key `allow_allocation` to `allow_billable_provisioning` (amends [0011](0011-store-and-per-job-simulate-replace-run-mode.md)) |
| [0018](0018-the-provisioning-matrix-is-every-gpu-size-in-every-signal-region.md) | The provisioning matrix is every signal GPU size in every signal Region, simulated in the repo file |
| [0019](0019-the-aws-sweeper-is-a-scheduled-lambda-function.md) | The AWS sweeper is a Lambda function on an EventBridge Scheduler schedule, outside the VPC, with no CloudWatch Logs |
| [0020](0020-provisioning-probes-run-in-region-lanes-off-the-signal-thread.md) | The provisioning probes run in Region lanes, off the signal thread, 1 lane at a time per Region; the stop timeout covers 1 attempt with a pre-teardown |
| [0021](0021-provisioning-probes-measure-available-capacity-not-single-cluster-capacity.md) | Provisioning probes measure available capacity per size and Region, not single-cluster capacity: the `single` placement group scale set probes are dropped (amends [0018](0018-the-provisioning-matrix-is-every-gpu-size-in-every-signal-region.md)) |
| [0022](0022-azure-provisioning-probes-are-listed-by-probe-type-and-find-their-resources-by-tags.md) | Azure provisioning probes are listed by probe type, and find their scale set or reservation group by tags, not by a computed name (amends [0010](0010-one-experiment-file-of-modules-and-jobs-with-a-billable-guard.md), [0018](0018-the-provisioning-matrix-is-every-gpu-size-in-every-signal-region.md)) |
| [0023](0023-the-provisioning-search-goes-up-after-a-full-success.md) | The provisioning search goes up after a full success, to a per-entry `n_max` of at most 16 (amends [0009](0009-allocation-probes-are-all-or-nothing-with-halving.md) section 2 and the entry budget of [0020](0020-provisioning-probes-run-in-region-lanes-off-the-signal-thread.md)) |
| [0024](0024-the-aws-sweeper-alarm-reads-the-lambda-metrics.md) | The AWS sweeper-stopped alarm reads the built-in Lambda metrics |
| [0025](0025-daily-compaction-runs-on-the-prober-vm.md) | Daily compaction runs on the prober VM from a systemd timer, with the prober's identity plus read and list grants; it writes only under `experiment=<name>/daily/v1/` and never deletes |
| [0026](0026-the-board-view-is-react-with-shadcn-ui-in-the-nocturne-dark-theme.md) | The board view is React with shadcn/ui, themed with the Nocturne tokens, dark only (supersedes part of [0004](0004-dashboard-stack.md)) |
| [0027](0027-the-board-reads-windows-from-a-duckdb-request-handler.md) | The board reads windows from a read-only DuckDB request handler, with a static export (supersedes part of [0004](0004-dashboard-stack.md)) |
| [0029](0029-plain-tfvars-files-replace-the-config-wrapper.md) | Plain `terraform.tfvars` files from committed examples, an optional partial backend config, and the user's own CLI login replace a config wrapper |

There is no ADR 0017 and no ADR 0028. These numbers were never used in this repo. The numbers after them are unchanged so that existing links stay valid.
