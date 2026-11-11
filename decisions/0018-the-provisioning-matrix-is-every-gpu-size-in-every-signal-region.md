# 0018. The provisioning matrix is every signal GPU size in every signal Region

- Status: accepted
- Date: 2026-10-15
- Amended by: [0021](0021-provisioning-probes-measure-available-capacity-not-single-cluster-capacity.md) (no `single` entry: 45 Azure entries, 63 in all)
- Amended 2026-10-16: the matrix lists only sizes that a Region offers to the subscription. "If an instance isn't offered in a region, why include it in the experiment?" The preflight `azure/allocation-probes` check fails on an entry, simulated or not, whose size the Region does not offer ([spec 0005](../spec/0005-azure-stack-spec.md), section 11.2). A size that is offered but restricted stays in the matrix; its rows show `restricted`.
- Amended 2026-10-16: the file drops the 14 Azure entries for sizes that their Regions do not offer (RTX PRO 6000 in 8 Regions; H100 and A100 in northcentralus and westcentralus; A10 and T4 in westcentralus). The Azure matrix has 31 entries, not 45 (westus2 has 5, westcentralus has 0, northcentralus has 2, the other 6 Regions have 4), and 49 entries in all (18 AWS, 31 Azure), not 63. With the grant on, the capacity reservation groups plan in 8 Regions, not 9 (westcentralus has no entry), and the scale set cells need a probe subnet only in the Regions that have one. The counts in the table of section 5 and in the Consequences below are the counts before this amendment.

## Context

The goal of the experiment is "all probes, all providers, all Regions". The signal probes cover 3 AWS Regions and 9 Azure regions ([`config/targets.yaml`](../config/targets.yaml)). The provisioning probes (`aws_odcr`, `az_capacity_reservation`, `az_vmss_flex`) had only 1 AWS entry and 3 Azure entries, all on cheap CPU sizes, in 1 AWS Region and 2 Azure regions.

A provisioning probe allocates real capacity, so each cell costs money and needs quota. Quota state per cell is kept outside the repo; every cell stays `simulate: true` until you enable it, and no new quota requests are planned.

## Decision

### 1. Regions

The Regions of each cloud are the union of the Regions of its signal jobs. No Region is left out.

- **AWS:** us-east-1, us-east-2, us-west-2.
- **Azure:** eastus, eastus2, southcentralus, westus2, westus3, centralus, northcentralus, westus, westcentralus.

2 Azure regions were candidates to leave out, and stay in:

- **westcentralus.** Every size returned `RestrictedSkuNotAvailable` from the Spot Placement Score in every poll. That is a state of the subscription, not of the region. Other Azure restrictions flip within hours ([learning](../research/learnings/azure-restricted-sku-can-flip-within-hours.md)). The pre-check records it as a `restricted` row and makes no allocation call ([spec 0010](../spec/0010-azure-provisioning-probes-spec.md), section 5, step 5). A `restricted` row is a result, not a waste.
- **northcentralus.** Its sizes have Spot prices, so the region offers them.

### 2. Sizes

1 size per GPU class that the signals watch: the smallest size with 1 whole GPU. It is the same size as the signal, so that a cell compares with the signal of its own size.

- **AWS:** the 6 GPU types of the Spot score requests ([spec 0008](../spec/0008-aws-probes-spec.md), section 2.1): `p5.4xlarge` (H100), `g7e.2xlarge` (RTX PRO 6000), `g6e.xlarge` (L40S), `g5.xlarge` (A10G), `g4dn.xlarge` (T4), `g6.xlarge` (L4). Each has 1 GPU ([GPU lineup](../research/gpu-lineup-aws-azure.md)).
- **Azure:** the 5 sizes of `spot_sizes`: `Standard_NC40ads_H100_v5`, `Standard_NC24ads_A100_v4`, `Standard_NV36ads_A10_v5`, `Standard_NC4as_T4_v3`, `Standard_NC144ds_xl_RTXPRO6000BSE_v6`. Each has 1 whole GPU. The smaller A10 and RTX PRO 6000 sizes have a fraction of a GPU ([GPU lineup](../research/gpu-lineup-aws-azure.md)).

The GPU classes that only the AWS Capacity Block probes watch (H200, B200, and the 8-GPU A100 and H100 types) are not in the matrix. Their smallest type has 8 GPUs (`p5en.48xlarge`, `p6-b200.48xlarge`, `p4d.24xlarge`), so 1 attempt holds 8 GPUs, and the Capacity Block probes already give a free reservable-capacity signal for them ([spec 0002](../spec/0002-gpu-capacity-tracker-spec.md), section 4.1).

### 3. Azure route and probe subnets

The pre-check reads the route of each size at run time from the `CapacityReservationSupported` capability ([spec 0002](../spec/0002-gpu-capacity-tracker-spec.md), section 4.3). The stack decides at plan time from `reservation_eligible_skus` which sizes get a scale set (`terraform/azure/probe.tf`).

| Size | Route | Entries per region | Probe subnet |
|---|---|---|---|
| `Standard_NC40ads_H100_v5` | Scale set (§4.5). NCads H100 v5 does not support capacity reservations ([Microsoft](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-overview)). | 2: `multi` and `single` (D6; `single` dropped by [0021](0021-provisioning-probes-measure-available-capacity-not-single-cluster-capacity.md)) | Yes |
| `Standard_NC24ads_A100_v4` | Reservation (§4.4) | 1 | No |
| `Standard_NV36ads_A10_v5` | Reservation (§4.4) | 1 | No |
| `Standard_NC4as_T4_v3` | Reservation (§4.4) | 1 | No |
| `Standard_NC144ds_xl_RTXPRO6000BSE_v6` | Open at run time; the size is on neither Microsoft list. It is not in `reservation_eligible_skus`, so the stack plans a scale set for it. | 2: `multi` and `single` (`single` dropped by [0021](0021-provisioning-probes-measure-available-capacity-not-single-cluster-capacity.md)) | Yes |

A reservation entry still needs `placement_group_mode`. The matrix uses `multi`. A reservation has no placement group mode, so 2 entries would give 1 reservation ([spec 0010](../spec/0010-azure-provisioning-probes-spec.md), section 5, decision Z6).

Thus every Azure region of the matrix needs a probe subnet for the H100 and RTX PRO 6000 cells. A scale set needs a subnet in its own region, and the plan fails without one ([spec 0010](../spec/0010-azure-provisioning-probes-spec.md), section 7.1). A region gets its subnet when its first scale set cell is enabled for real, not before, because the project adds infrastructure only after evidence that a workload needs it. A simulated cell gets no scale set.

### 4. `n`, zone, and cadence

- **`n` = 2** for every cell. `n` = 2 is the smallest `n` at which the all-or-nothing rule tells more than yes or no: a failure at 2 halves to 1 ([ADR 0009](0009-allocation-probes-are-all-or-nothing-with-halving.md)). The quota need of a cell is 2 instances.
- **Zone.** Azure cells are regional (`zone: null`), as the existing entries. An AWS reservation needs 1 Availability Zone ([CreateCapacityReservation](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CreateCapacityReservation.html)). The matrix has 1 zone per Region, the `a` zone. A zone name can map to a different physical zone in each account; the AZ ID is the same in every account ([AWS Availability Zones](https://docs.aws.amazon.com/global-infrastructure/latest/regions/aws-availability-zones.html)), so the live file picks a zone that offers the type. A zone that does not offer it gives a `restricted` pre-check row.
- **Cadence:** 2 h, unchanged ([spec 0002](../spec/0002-gpu-capacity-tracker-spec.md), D2). 12 cycles per day.

### 5. Size of the matrix and instance-hours per cycle

| Cloud | Cells (size x Region) | Entries | Entries per cycle |
|---|---|---|---|
| AWS | 6 x 3 = 18 | 18 | 18 |
| Azure | 5 x 9 = 45 | 27 reservation + 36 scale set = 63 | 63 (54 if the RTX PRO 6000 size takes the reservation route) |
| Total | 63 | 81 | 81 |

The control entries on CPU sizes (1 AWS, 3 Azure) are not part of the matrix.

Instance-hours held per full cycle, with every entry enabled. Each size has 1 GPU, so 1 instance-hour is 1 GPU-hour.

- **Floor:** every entry succeeds at `n` = 2 and holds 2 instances for 60 s. 60 s is the billing minimum of an AWS reservation ([EC2 On-Demand Capacity Reservations pricing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html)). 81 x 2 x 60 s = **2.7 instance-hours** per cycle (AWS 0.6, Azure 2.1).
- **Bound:** with `n` = 2, the search makes at most 2 attempts (2, then 1). The 2 attempts together hold at most 2 instances: a success at 2, or a partial fill of 1 and then a success at 1. Each held instance lasts at most the probe timeout plus the teardown timeout, 90 s + 60 s = 150 s (spec 0010, section 10; `prober/ftg_prober/aws/odcr.py`). Per entry: 2 x 150 s = 300 instance-seconds. 81 x 300 s = **6.75 instance-hours** per cycle (AWS 1.5, Azure 5.25), or 81 per day.
- A teardown that does not verify is outside the bound. AWS: the reservation bills until its `EndDate` plus up to 1 hour ([spec 0002](../spec/0002-gpu-capacity-tracker-spec.md), section 4.9). Azure: the sweeper deletes it after 3 min ([spec 0002](../spec/0002-gpu-capacity-tracker-spec.md), section 6).

The same bound is a wall-time bound: an entry can take up to 300 s per cycle. The scheduler runs every probe on 1 thread ([spec 0003](../spec/0003-prober-core-spec.md), section 6, rule 3), and a provisioning probe runs its entries in series. The full matrix can take 90 min (AWS) and 315 min (Azure) per cycle, and the Spot signals of that cloud wait for it. 315 min is longer than the cadence. A later ADR moves the provisioning probes off the signal thread. Until it is done, enable only as many cells as the project accepts as Spot signal delay. A simulated entry runs on a virtual clock and takes no wall time.

### 6. Simulated by default, enabled per cell in the live file

Every cell in `config/targets.yaml` has `simulate: true` and no `allow_billable_provisioning`. `config/targets.yaml` is the reference matrix. Your own targets file is what runs. To enable a cell, your file drops its `simulate: true` and sets its own `allow_billable_provisioning: true`, ([ADR 0016](0016-rename-the-consent-key-to-allow-billable-provisioning.md)). The stack's `grant_billable_provisioning` must also be `true` ([ADR 0015](0015-two-independent-controls-replace-the-terraform-allocation-guard.md)). A test checks that the repo file enables no cell.

Enable a cell only when you have checked its quota. Enable a scale set cell only in a region where the pre-check shows the size as not restricted, because the stack creates the scale set at plan time.

### 7. Quota state per cell

Quota state per cell is kept outside the repo; every cell stays `simulate: true` until you enable it, and no new quota requests are planned.

The RTX PRO 6000 size is restricted in 8 of 9 Azure Regions in every poll, so most of its scale set cells end as `restricted` rows at the pre-check.

## Consequences

- `config/targets.yaml` lists 18 AWS and 63 Azure matrix entries, all simulated. The sim writes rows for every cell.
- Spec 0002 has a new section 4.10 with the matrix. Spec 0010 section 10 describes the repo entries.
- Probe subnets are needed in all 9 Azure regions before the scale set cells of a region run for real.
- Running provisioning probes off the signal thread blocks the full matrix from running for real.
- With the grant on, the capacity reservation groups go from 2 to 9 Regions. A group costs nothing.
- The pre-check row of each cell shows the live `n_ceiling` (spec 0010, section 4.1).
