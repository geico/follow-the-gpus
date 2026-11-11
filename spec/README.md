# Spec

The requirements, design, and decisions of the GPU capacity tracker. The spec is the source of truth for the product ([ADR 0003](../decisions/0003-specs-are-the-source-of-truth.md)).

The specs name test files as checks. The tests are not part of this repository.

| File | Topic | Date |
|---|---|---|
| [Project description](0001-project-description.md) | The problem, the broker goal, and the design limits | 2026-10-11 |
| [GPU capacity tracker spec](0002-gpu-capacity-tracker-spec.md) | Requirements, design, and decisions of the capacity measurement rig | 2026-10-10 |
| [Prober core spec](0003-prober-core-spec.md) | The prober command line, config, records, storage, probe plugins, scheduler, and the sim generator | 2026-10-11 |
| [AWS stack spec](0004-aws-stack-spec.md) | The AWS OpenTofu stack: 1 prober VM, its IAM role, and 1 S3 data bucket | 2026-10-11 |
| [Azure stack spec](0005-azure-stack-spec.md) | The Azure OpenTofu stack in an existing resource group and subnet: the prober VM, the data storage, the sweeper, and the probe resources | 2026-10-11 |
| [Dashboard spec: the GPU Availability Board](0006-dashboard-spec.md) | The single-page board: can I get this GPU machine now, anywhere; the data and logic boundaries, the server on DuckDB, the static export, and every board section | 2026-10-11 |
| [Azure sweeper spec](0007-sweeper-spec.md) | The timer function that deletes probe resources older than a threshold, the Azure cost brake | 2026-10-11 |
| [AWS probes spec](0008-aws-probes-spec.md) | The AWS probes: Capacity Block offerings, Spot placement score, Spot price history, and the Spot Instance Advisor feed | 2026-10-11 |
| [Azure spot signals spec](0009-azure-spot-signals-spec.md) | The Azure Spot Placement Score and Resource Graph Spot probes | 2026-10-11 |
| [Azure provisioning probes spec](0010-azure-provisioning-probes-spec.md) | The Azure pre-checks, capacity reservation, and VMSS Flexible probes, simulated and not | 2026-10-11 |
| [Azure quota requests spec](0011-azure-quota-requests-spec.md) | The command that requests Azure GPU vCPU quota from `azure.quota` in the targets file | 2026-10-12 |
| [AWS quota requests spec](0012-aws-quota-requests-spec.md) | The command that requests AWS GPU On-Demand and Spot vCPU quota from `aws.quota` in the targets file | 2026-10-12 |
| [AWS sweeper spec](0013-aws-sweeper-spec.md) | The scheduled Lambda function that cancels probe Capacity Reservations older than a threshold, the AWS cost brake | 2026-10-15 |
| [Config and deploy spec](0014-config-and-deploy-spec.md) | The `terraform.tfvars` settings file of each stack and its example, the credentials, the deploy outputs, the optional remote state backend, and the make targets of each cloud | 2026-10-31 |
