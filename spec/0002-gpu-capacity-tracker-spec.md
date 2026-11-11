---
title: "GPU Capacity Tracker — Requirements, Spec & Decisions"
date: 2026-10-10
---

# GPU Capacity Tracker — Requirements, Spec & Decisions

**Status:** Draft, partly implemented (Phase 1 probes, the AWS provisioning probe `aws_odcr`, and the Azure provisioning probes are built; the `n_ceiling` from the quota is read; the quota requests are open) · 2026-10-10, updated 2026-10-11 (VMSS allocation behavior; two-phase plan), updated 2026-10-12 (all-or-nothing provisioning probes; AWS provisioning probe)
**Purpose:** Measure where and when GPU capacity is obtainable across AWS and Azure (continental US), over a ~2-week run, and produce a region × SKU × time heat map for the "Follow the GPUs" project.
**Distribution:** Public repo for attendees. Must be runnable on AWS only, Azure only, or both.
**Starting point:** AWS Solutions Library [Guidance for EC2 Spot Placement Score Tracker](https://github.com/aws-solutions-library-samples/guidance-for-ec2-spot-placement-score-tracker) — reuse its scheduled polling and config-driven target list; replace its CloudWatch sink and CDK.

Legend for claims: **[V]** verified against a source (§12) · **[S]** secondary source only · **[T]** unverified — must be tested in the pilot (§9).

---

## 1. Scope and phasing

Work is split by whether it needs GPU quota (D18). GPU quota takes time to obtain; Phase 1 collects real data and builds the whole rig in the meantime.

### Phase 1 — no GPU quota (start now)

#### Live data — the heat map starts filling immediately

- AWS Capacity Block offerings (§4.1) — read-only, free.
- AWS Spot Placement Score (§4.2) — free, but scorable target capacity is capped by recent Spot usage; with no recent usage, AWS applies a low default limit aligned with the Spot request limit **[V]**.
- AWS Spot price history (§4.8) — read-only, for each GPU type and AZ.
- AWS Spot interruption data (§4.8) — public feed, daily.
- Azure Spot Placement Score (§4.6) — free; 1 score for each VM size in each region or zone **[V]**.
- Azure spot eviction rate and price (§4.6) — Resource Graph, Reader role only.
- Azure pre-checks (§4.3) — SKU eligibility, region/zone restrictions, quota; recorded as rows.

#### Build and validate

- OpenTofu for both stacks; prober; sweeper; S3 and Blob write path (§6–8).
- Azure paid-probe path on a non-simulated, cheap non-GPU size (D19), so both Azure probe paths run end to end. The CPU size is any size that the subscription can allocate in the region and that supports capacity reservations. To check a size, run `az vm list-skus -l <region> --size <size>` with the full size name. The output must list the size with an empty `restrictions` list, and its capability `CapacityReservationSupported` must be `True` **[V]**. A size that the subscription cannot use can be missing from the output **[V]**; that also fails the check. The size also needs free vCPU quota in its family (§4.3). B-series is 1 example: it supports capacity reservations **[V]** and VMSS. `config/targets.yaml` uses `Standard_D2as_v4`, from the Das v4 series, which supports capacity reservations **[V]**.
- Fault injection in the Azure adapter (a simulated entry, `simulate: true`): `AllocationFailed`, partial fill, hang in `Creating` — no Azure calls. A partial fill gives a failure row, not `partial` ([ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md)).
- Pilot tests that need no GPU quota (§9, Phase column).
- Soak on a non-simulated, cheap non-GPU size: build, run for days, destroy.

#### Day-one admin (D20)

- Azure: request GPU quota for every target region × GPU family.
- AWS: request Spot quota increases for the GPU families in every target region, so Spot Placement Score can score realistic counts.

Phase 1 proves: OpenTofu build/destroy, teardown + sweeper, schedules, data landing, Resource Graph behavior. It cannot prove real failure paths (cheap sizes don't run out) or anything GPU-specific.

### Phase 2 — needs Azure GPU quota (per region, as approvals land)

- Azure capacity reservation probes on GPU sizes — reservations require quota the same way VMs do **[V]** (§4.4).
- Azure VMSS Flexible probes on GPU sizes (§4.5).
- GPU pilot tests (§9).
- Rollout per target: when a region's quota lands, flip its targets' SKU from a cheap non-GPU size to the GPU size in config. No code or infra change.
- AWS provisioning probe on GPU types (§4.9, D21), every 2 h by default, as the Azure provisioning probes (D2). **Implemented as `aws_odcr`** (§4.9). It needs AWS On-Demand quota for each GPU family to try a GPU type. That request has not been made (D20); until it lands, the entry uses a cheap non-GPU type.

**Asymmetry:** AWS's reservable-capacity signal (Capacity Block offerings) needs no quota and is fully live in Phase 1. The provisioning probes of both clouds wait for quota: Azure GPU quota, and AWS On-Demand GPU quota (§4.9).

### Common to both phases

- Runaway-cost protection for paid Azure probes
- Durable, crash-safe data capture
- All infrastructure as OpenTofu

### Out of scope

- Visualization (later — local DuckDB, see §7.5)
- Purchasing capacity
- Broker / Karpenter integration
- Clouds other than AWS and Azure
- AWS GovCloud and Azure Government

---

## 2. Decisions

| # | Decision |
|---|---|
| D1 | Signal per case: **AWS** — Capacity Block offerings API (read-only, free). **Azure, reservation-eligible SKU** — create capacity reservation, record result, delete. **Azure, reservation-ineligible SKU** — scale a pre-created VMSS to the requested count, record result, scale to 0. **AWS allocation** — D21 (Phase 2, implemented as `aws_odcr`, §4.9). **Spot, both clouds** — AWS Spot Placement Score, Spot price history, and Spot interruption data (§4.2, §4.8); Azure Spot Placement Score, eviction rate, and spot price (§4.6). Spot signals are the core data of the experiment. |
| D2 | Cadence: Spot signals every 5 min, except the Azure Spot Placement Score (every 15 min, to match the suggested cache TTL, §4.6) and the AWS interruption feed (daily, §4.8); provisioning probes every 2 h by default (12/day per target). The cadence comes from the config: the code default is 2 h, and a config can set 1 h (24 cycles a day), which gives twice the data. The measured lanes are far below the bound. A default config passes the lane check. On AWS, 1 h needs `probe_timeout_seconds` 60 and `teardown_timeout_seconds` 45 to pass the lane check ([spec 0003](0003-prober-core-spec.md), section 6B, rule 8). A provisioning probe runs on a UTC grid of its cadence, so it runs at most ceil(24 h / cadence) cycles a UTC day (12 at 2 h, 24 at 1 h), whatever the number of restarts ([spec 0003](0003-prober-core-spec.md), section 6, rule 2). |
| D3 | Architecture is independent of target count. Targets are config; count affects cost only. |
| D4 | Azure ineligible-SKU vehicle is **VMSS in Flexible orchestration mode** — members are standard VMs, individually taggable, readable, countable, and deletable through the normal VM APIs. Standalone VMs ruled out: ND H100 v5 InfiniBand is auto-configured between VMs in the same scale set **[V]**. Microsoft's generic IB guide also allows an availability set **[V]**; untested for ND H100 v5. **Placement group note (2026-10-16):** Microsoft lists `singlePlacementGroup` as unsupported in Flexible; the platform selects the value **[V]**. The Availability Zones doc says placement groups apply only to Uniform **[V]**. At the apply of 2026-10-15, a Flexible scale set with `singlePlacementGroup` set to `true` failed at create with `OperationNotAllowed` ([learning brief](../research/learnings/azure-flexible-scale-set-rejects-single-placement-group.md)). The project dropped the single placement group probe, and every scale set is Flexible with `singlePlacementGroup` set to `false` ([ADR 0021](../decisions/0021-provisioning-probes-measure-available-capacity-not-single-cluster-capacity.md)). The probes never boot VMs, so InfiniBand itself is not part of the signal. |
| D5 | One VMSS per (SKU × region × placement-group mode), pre-created at capacity 0. The recurring probe is a capacity change, not a create. Pre-creation separates config failures from capacity failures. |
| D6 | Probe regional headroom only: a Flexible scale set with `singlePlacementGroup=false`. The project measures the capacity that exists per size and Region, not the capacity that fits in 1 cluster ([ADR 0021](../decisions/0021-provisioning-probes-measure-available-capacity-not-single-cluster-capacity.md)). The earlier second probe, `singlePlacementGroup=true` (co-located depth), is dropped: Flexible rejects it, and a Uniform path would be a second code path. |
| D7 | Probes never wait for boot. Tear down as soon as the allocation outcome is known. |
| D8 | **All or nothing, and a failure halves the count** ([ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md)). Each attempt of a provisioning probe asks for exactly its requested count (`n_requested`). Fewer than `n_requested` placed is a failure: the probe tears down at once, and the outcome comes from the error codes (§4.5 table), normally `no_capacity`. The next attempt halves the count, then a binary search runs between the last success and the last failure. The search is bounded by min(quota `n_ceiling`, the entry `n`), in node units. It stops on any outcome other than `success` and `no_capacity`, and on a teardown that is not verified. The Azure probes (`az_capacity_reservation`, `az_vmss_flex`) run this search, as `aws_odcr` does (spec 0010, section 5, step 7). A partial fill is not a depth signal (§4.7). **Upward search ([ADR 0023](../decisions/0023-the-provisioning-search-goes-up-after-a-full-success.md))**: an entry with the optional key `n_max` (an integer greater than `n`, at most 16) goes up after a full success at C = min(`n`, the quota ceiling). The probe requests min(2 × `n_requested`, U), U = min(`n_max`, the quota ceiling), all or nothing, with a verified teardown between the steps. It stops at the first step that is not a success, at U, at a stop, and at the deadline. A cycle halves or goes up, never both. |
| D9 | Sweeper runs as a **scheduled function (FaaS), every 1 min**, independent of the prober VM. A prober crash must not leave GPUs billing. |
| D10 | Sweeper discovers probe resources by experiment tag; judges age from the platform's own `timeCreated` — no TTL tag to maintain. Deletes anything older than **3 min** (emergency brake). |
| D11 | Cost budget alert as last-resort backstop. |
| D12 | **One small prober VM per cloud** (AWS VM in AWS, Azure VM in Azure), each using native identity (instance profile / managed identity). No cross-cloud credentials. |
| D13 | Each cloud writes to its own object store: **S3** (AWS), **Blob** (Azure). Write-through per data point; prober holds no durable state. |
| D14 | No CloudWatch or other provider-specific metrics services for telemetry. |
| D15 | All infra in OpenTofu; per-cloud stacks deploy independently. |
| D16 | Visualization is decoupled from the rig: local DuckDB reading S3 + Blob directly. Built later. |
| D17 | Paid Azure probes are an accepted experiment cost. |
| D18 | **Two phases, split by GPU-quota dependence** (§1). Phase 1 runs live on every signal that needs no GPU quota and builds the whole rig. Phase 2 adds paid Azure GPU probes, region by region, as quota lands. |
| D19 | **`simulated` on every record** (ADR 0011, superseding the earlier `run_mode` of `cloud-gpu` / `cloud-cpu` / `local`): `true` when the row came from a simulator (`sim.py`, or a provisioning probe against a simulated client), `false` when it came from a real provider call. A run-wide `--simulate` flag or a job's own `simulate: true` key decides, not a per-record mode. Only `simulated = false` rows feed the heat map. A CPU-sized job is an ordinary job, not a mode. |
| D20 | File quota requests on day one: Azure GPU quota and AWS GPU Spot quota, every target region. A target goes live by config change when its quota lands. `make quota/azure` and `make quota/aws` file the On-Demand and Spot requests ([spec 0011](0011-azure-quota-requests-spec.md), [spec 0012](0012-aws-quota-requests-spec.md)). **State on 2026-10-12:** the AWS GPU Spot quota request has not been made. The AWS provisioning probe (D21) also needs AWS On-Demand quota for each GPU family. That request has not been made either. |
| D21 | **AWS provisioning probe** (§4.9, Phase 2, implemented as `aws_odcr`): the AWS order mirrors Azure. Free signals, then an On-Demand Capacity Reservation (create for `n_requested`, read the state, cancel) only. A reservation capacity failure halves the count. Any other failure ends the search. EC2 Fleet `instant` is future work, for a Spot probe and a calibration pair. Never an Auto Scaling group: it keeps retrying in the background and reports late ([ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md)). |

---

## 3. Architecture

```text
AWS account                                  Azure subscription
┌──────────────────────────────┐             ┌──────────────────────────────────────────┐
│ Prober VM (small, cron)      │             │ Existing RG (sweeper deletes only        │
│   • Capacity Block offerings │             │   resources with the experiment tag)     │
│   • Spot Placement Score     │             │   • Prober VM (small, cron, managed ID)  │
│   • Spot price history       │             │     Spot Placement Score, spot data      │
│   • Spot interruption data   │             │   • Sweeper function (timer, 1 min)      │
│          │ write-through     │             │   • Storage account → Blob container     │
│          ▼                   │             │   • Capacity reservation groups / region │
│ S3 bucket                    │             │   • VMSS Flexible per target, capacity 0 │
│ Existing VPC and subnet      │             │ Existing subnet in the control region    │
│ Sweeper function (1 min)     │             │                                          │
└──────────────────────────────┘             └──────────────────────────────────────────┘
                     ▲                                            ▲
                     └────────── DuckDB on a laptop (later) ──────┘
                               httpfs (S3) + azure (Blob)
```

**Why each cloud has a sweeper.** The signal probes of both clouds create no billable resource. The provisioning probes do: on Azure, VMs, disks, NICs, and capacity reservations (§4.5, §5); on AWS, On-Demand Capacity Reservations, charged at the On-Demand rate whether instances use them or not (§4.9, §5). Each prober tears down its own resources, and the AWS probe also cancels older leftovers at the start of its next call (§4.9). Neither runs while its VM is stopped, replaced, or broken. Thus each cloud has a scheduled sweeper outside the prober VM (D9): an Azure Function (§6, [spec 0007](0007-sweeper-spec.md)) and an AWS Lambda function (§6A, [spec 0013](0013-aws-sweeper-spec.md), [ADR 0019](../decisions/0019-the-aws-sweeper-is-a-scheduled-lambda-function.md)).

---

## 4. Probe specifications

### 4.1 AWS — Capacity Block offerings (every 2 h)

- API: `DescribeCapacityBlockOfferings`. Read-only, no charge.
- Inputs from config: instance type, instance count, block duration, start-date window.
- Supported types include P6-B300, P6-B200, P5en, P5e (H200), P5 (H100; `p5.4xlarge` and `p5.48xlarge`), P4d and P4de (A100), Trn2, Trn1, and P6e-GB200 as UltraServer blocks only (us-east-1-dfw-2a) **[V]**. Region coverage differs by type. A P6e-GB200 query needs `UltraserverType`, not `InstanceType`. (Interpretation: AWS does not exclude high-contention SKUs from Capacity Blocks.)
- The repo config queries `p5.4xlarge` (1x H100) as well. [Supported instance types and Regions](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html#capacity-blocks-prerequisites) lists `p5.4xlarge` for `us-east-1`, `us-east-2`, and `us-west-2`. [DescribeCapacityBlockOfferings](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html) takes any `InstanceType` string, thus no API change is needed **[V]**.
- Record every offering returned (AZ, start, end, duration hours and minutes, count, upfront fee, currency, offering ID, `tenancy`, `zone_type`, `ultraserver_type`, `ultraserver_count`), each field raw as returned and null when the offering lacks it ([CapacityBlockOffering](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityBlockOffering.html); spec 0008, section 3.2). If none returned, write an explicit "none" row — absence is data.
- **Earliest start.** A range search returns the lowest-priced offering, so these rows show the cheapest block, not the soonest. The probe `aws_cb_earliest` searches a narrow window that admits only the earliest start ([spec 0008](0008-aws-probes-spec.md), section 3A).
- **First free day, and the longest block there.** The probe `aws_cb_longest` finds the first day a block is for sale and the longest block that starts on that day, per Region and type, as a search in steps on the call queue ([spec 0008](0008-aws-probes-spec.md), section 3B).
- **What the signal answers.** An offering for a given number of instances shows that AWS can sell a Capacity Block of that many instances for that window. The purchase is the guarantee: a Capacity Block gives "predictable capacity assurance for GPU instances" **[V]**. Thus this question needs no provisioning probe.
- **What it does not answer.** A Capacity Block reserves capacity for a future window, and offerings can start in as soon as 30 minutes **[V]**. Instances in a Capacity Block "don't count against your On-Demand Instances limits" **[V]**. Capacity Block stock is a separate product from On-Demand capacity (§11). Thus the 2 can disagree in both directions: an offering does not show free On-Demand capacity now, and no offering does not show that On-Demand capacity is absent (interpretation). The On-Demand question needs the provisioning probe (§4.9).
- **Coverage.** Capacity Blocks cover only the instance types that AWS lists: P6-B300, P6-B200, P5, P5e, P5en, P4d, P4de, Trn1, and Trn2, and the UltraServer types Trn2 and P6e-GB200 **[V]** (list read on 2026-10-12). The list has no G-family type **[V]**. Thus the G-family GPUs of the §4.2 request list (T4 `g4dn`, A10G `g5`, L4 `g6`, L40S `g6e`, and RTX PRO 6000 `g7e`) get no Capacity Block signal. They get only the Spot signals (§4.2, §4.8) and an allocation attempt (§4.9).
- **Capacity Block or On-Demand Capacity Reservation (ODCR).** The 2 products are different. §4.1 reads Capacity Block offerings. The AWS provisioning probe (§4.9) creates ODCRs.

  | | ODCR | Capacity Block |
  |---|---|---|
  | What you get | On-Demand capacity in 1 Availability Zone "for any duration" **[V]** | GPU instances "on a future date", for a fixed window **[V]** |
  | Start | At once: "available for use immediately" **[V]** | Up to 8 weeks ahead; offerings can start in as soon as 30 minutes **[V]** |
  | Payment | The On-Demand rate whether used or not, per second, 60-second minimum **[V]** | "Charged up front at the time the reservation is made" **[V]** |
  | Cancel | At any time, "to stop incurring charges" **[V]** | "Capacity Block cancellations aren't allowed" **[V]** |
  | Quota | "Active and unused Capacity Reservations count toward your On-Demand Instance limits" **[V]** | Does not count against the On-Demand limits **[V]** |
  | Instance types | Not limited by a published list of types; the documented limits are in the [Limitations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html#capacity-reservations-limitations) section **[V]** | Only the listed P-family and Trainium types **[V]** |
  | Capacity | The same On-Demand capacity as an On-Demand launch (interpretation) | Stock set aside for Capacity Blocks (interpretation, from the separate quota and the price set by supply and demand **[V]**) |

### 4.2 AWS — Spot Placement Score (every 5 min)

Background and sources: [AWS Spot placement score per GPU instance type](../research/aws-spot-placement-score-per-gpu-type.md).

- API: `GetSpotPlacementScores`. Regional scores for the target US Regions (`RegionNames`), `SingleAvailabilityZone=false`, `TargetCapacity=1`, `TargetCapacityUnitType=units`. No charge **[V]**.
- **Minimum instance types:** the docs say a request with fewer than 3 instance types returns a low score **[V]**. Public data shows that accelerated types (GPU, Inferentia, Trainium) get a full score for 1 type, while CPU types stay at 3 or lower **[S]**. Not yet tested in the rig's account **[T]** (P9).
- **One score per mix:** the score is 1 number per Region or AZ for the whole request. It assumes a `capacity-optimized` Spot request that can use any listed type **[V]**. Thus each GPU row uses 1 instance type. A group of `p5.48xlarge`, `p5e.48xlarge`, and `p5en.48xlarge` scores only the H100 type: both H200 types scored 1 in every US AZ on 2026-10-11 **[S]**. The record's `sku` field holds the instance type, or the type set for a mix entry.
- **Spot score requests:** 1 to 11 configurations, fixed for the whole run. AWS states no number for its limit ([Spot placement score, request configurations limit](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html#sps-limitations)), so the cap of 11 is a project guard (`MAX_SPOT_SCORE_REQUESTS` in `config.py`), not an AWS value. Day 1 used 8. The request list has 9 entries (rows 1 to 9). Row 9 (`g5-family`) is the lasting GPU mix control. The 24 h A10G test  ran with 11 entries and is closed; the project dropped its 2 single-size rows (see the 24 h test below). The request list follows the GPU classes of the quota plan. The Azure classes are in [spec 0011](0011-azure-quota-requests-spec.md). The AWS classes (H100, A100, RTX PRO 6000, L40S, A10G, and T4) are a project choice. Each class uses its 1-GPU size where AWS offers one, so the signal measures the size that the quota requests and the later probes use. The 1-GPU sizes are listed as instance types in [EC2 accelerated computing instance types](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html). [Capacity Blocks for ML](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html#capacity-blocks-prerequisites) lists `p5.4xlarge`, `p5.48xlarge`, and `p4d.24xlarge` for the 3 Regions.

  | # | `InstanceTypes` | Row | Reason |
  |---|---|---|---|
  | 1 | `p5.4xlarge` | Spot score, 1x H100, this size | 1-GPU H100 size of the quota plan |
  | 2 | `g7e.2xlarge` | Spot score, 1x RTX PRO 6000, this size | 1-GPU RTX PRO 6000 size of the quota plan |
  | 3 | `g6e.xlarge` | Spot score, 1x L40S, this size | 1-GPU L40S size of the quota plan |
  | 4 | `g5.xlarge` | Spot score, 1x A10G, this size | 1-GPU A10G size of the quota plan |
  | 5 | `g4dn.xlarge` | Spot score, 1x T4, this size | 1-GPU T4 size of the quota plan |
  | 6 | `g6.xlarge` | Spot score, 1x L4, this size | 1-GPU L4 size of the quota plan |
  | 7 | `m5.4xlarge` | Control: CPU alone | Control from the day-1 design |
  | 8 | `m5.2xlarge`, `m5.4xlarge`, `m5.8xlarge` | Control: CPU 3 sizes | A lasting control, not a test row: a CPU-only mix, so the mix rule can be read from the CPU side too. |
  | 9 | `g5.xlarge`, `g5.2xlarge`, `g5.4xlarge` | Spot score, 1x A10G, any of 3 sizes | A lasting GPU mix control . Replaces the old mixed-GPU trio: 1 GPU class, so a heat-map row can use it. |

  The request list has no 8-GPU entry: the project does not run 8-GPU H100 boxes. The Capacity Block offerings (section 4.1) keep the 8-GPU types, because AWS sells most Capacity Blocks only as 8-GPU instances and a search costs nothing. A100 has no 1-GPU size on AWS, so Capacity Block offerings of `p4d.24xlarge` (section 4.1) cover A100. The old entries `p5en.48xlarge` (8x H200) and `p6-b200.48xlarge` (8x B200) are out too; the GPU classes above have no H200 or B200 class. `config/targets.yaml` confirms this: `aws.modules.aws_spot_score.spot_score_requests` has no entry for `p5.48xlarge`, `p5en.48xlarge`, or `p6-b200.48xlarge` (it lists `p5.4xlarge` instead); those 3 types appear only in `aws.modules.aws_cb_offering.instance_types` (section 4.1), which `aws_cb_offering`, `aws_cb_earliest`, and `aws_cb_longest` read. `aws_spot_price` (and `aws_spot_advisor`, section 4.8) read the GPU types of the request list through `request_gpu_types`; `aws_spot_score` reads `spot_score_requests` directly, with no such helper. A row for 1 of the 3 types under `aws_spot_price` or `aws_spot_score` is from before this request change; the current request list writes no new one.

  **The 24 h test , closed:** rows 9 to 11 ran for 24 h to tell a 3-type penalty apart from real capacity, for the A10G, 1-GPU class. The 3-size mix beat the best single A10G size by 2 or more in 66.4% of the (Region, run) pairs, below the 90% line of the decision rule. The project chose to keep the mix row as a per-GPU-family mix control and to drop the 2 single-size rows (`g5-2x`, `g5-4x`). Row 8 (`control-cpu-trio`) is a lasting control, not a test row. The old mixed-GPU trio (`g5.xlarge`, `g4dn.xlarge`, `g6.xlarge`) mixed 3 GPU classes, so no heat-map row could use it. Row 9 mixes 3 sizes of 1 class instead, and the mix control (the controls bullet below) compares it with row 4. A restart of the prober after the 24 h window rolls brings a frozen entry back.

  **Rollout rule:** when the request list changes, pause the AWS Spot score probe for 24 h before the new request list runs. Stop the AWS prober process for 24 h. In the jobs model (spec 0003 section 3), removing the `aws_spot_score` job from `config/targets.yaml` does pause the probe, because a job-eligible probe type with no job never runs; but it also stops `aws_spot_price` and `aws_spot_advisor`, which read the `aws_spot_score` job's request list and need that job listed too (`check-config` refuses a file that lists either of them with no `aws_spot_score` job). A flat `cadence` entry is not a way around this: `check-config` refuses a `*.cadence` entry for a job-eligible probe type outright. Stopping the process, not removing the job, is still the rollout rule. The old and new configurations together could exceed the AWS limit, which has no stated number, and cause `MaxConfigLimitExceeded` and the freeze below. **One-off exception:** rows 8 to 11 shipped without this 24 h pause, and the freeze (spec 0008, section 4.4) keeps every entry that already got a score. This is a one-off; the rule above still applies to a later request change. A change that only removes rows adds no new configuration, so it needs no 24 h pause.
- **Controls on each cycle:** the CPU single control (row 7) must stay at 3 or lower; a higher score means that AWS changed the rule for all types. The CPU mix control (row 8) must score above 3; a score of 3 or lower means that the penalty now also hits a request of 3 types, or that `m5` Spot capacity is low. The probe stores `ok` as null, not false, when the CPU single control scores 1 in the same Region and run, because the low mix score then shows real `m5` shortage ([spec 0008](0008-aws-probes-spec.md), section 4.3). `g5.xlarge` alone (row 4) must not score above the mix (row 9).
- **Configuration limit:** AWS can limit new configurations within 24 h if usage looks unintended, and the error allows a retry of configurations "used within the last 24 hours" **[V]**. The window rolls, so the limit applies to the whole request list. An AWS sample puts the number at about 10 **[S]**. On `MaxConfigLimitExceeded` (with or without the `Exception` suffix), freeze the request list and record the count of live configurations.
- **Target capacity cap:** based on the account's recent Spot usage; with none, AWS applies "a low default limit aligned with your Spot request limit" **[V]**. The default Spot quota for P and for G and VT is 0 vCPUs **[V]**, so a request at target capacity 1 can fail with `TargetCapacityLimitExceeded` **[T]** (P9). Record `cap` for that row, not a score. Raising GPU-family Spot quota likely raises the cap **[T]**.
- **Label:** heat-map rows from this probe read "Spot slack (1 instance)". The score measures spare Spot capacity, not On-Demand capacity.

### 4.3 Azure — pre-checks (start of each 2-h cycle)

- Resource SKUs API (`az vm list-skus`):
  - `CapacityReservationSupported` capability → a fact for the row. It does not choose the probe: the experiment file lists each entry under `az_capacity_reservation` (4.4) or `az_vmss_flex` (4.5) [ADR 0022](../decisions/0022-azure-provisioning-probes-are-listed-by-probe-type-and-find-their-resources-by-tags.md) **[V]**.
  - Region/zone restrictions for the subscription → record `restricted`, skip the probe.
- Quota (`az vm list-usage`): effective `n_ceiling` = min(available family vCPUs, available Total Regional vCPUs) ÷ vCPUs per VM **[V]**.
- There is no public API for real-time capacity per SKU/region/zone; capacity is only known at allocation time **[S]**.
- **Probe type of each GPU size.** The sizes are those of `spot_sizes` and the quota plan (`quota.sizes`) in the `azure:` section of `config/targets.yaml`. The file lists each size under the probe type that fits its series; the table below follows Microsoft's lists of the series that support capacity reservations and of the series that do not **[V]** (read on 2026-10-12). Microsoft says that the Resource SKUs list "advertises the set of supported VM sizes" **[V]**.

  | Size | Series | Probe type |
  |---|---|---|
  | `Standard_NC40ads_H100_v5` | NCads H100 v5 | `az_vmss_flex` (§4.5). On Microsoft's list of series that are not supported. |
  | `Standard_NC24ads_A100_v4` | NC A100 v4 | `az_capacity_reservation` (§4.4) |
  | `Standard_NV36ads_A10_v5` | NVadsA10 v5 | `az_capacity_reservation` (§4.4) |
  | `Standard_NC4as_T4_v3` | NCasT4_v3 | `az_capacity_reservation` (§4.4) |
  | `Standard_NC144ds_xl_RTXPRO6000BSE_v6` | RTX PRO 6000 size | `az_vmss_flex` (§4.5). The size is on neither Microsoft list; the pre-check row records the capability. The stack plans a scale set for it, and its region needs a subnet (§4.10). |

  An `az_capacity_reservation` entry needs no subnet, so it can run in any region. An `az_vmss_flex` entry needs a subnet in the region of the scale set, so it runs only in a region that has a subnet: `location` with `subnet_id`, or a region with an entry of `probe_subnet_ids` (spec 0010, section 7.1; spec 0005, R2 and section 4.3). Thus the H100 size is probed only in those regions.
- Record: the pre-check writes 1 row per allocation-probe entry, with `probe_type` = `az_precheck` (§7.2). The row holds the outcome, the probe type of the entry, and the `n_ceiling` (spec 0010, section 4.1).

### 4.4 Azure — capacity reservation probe (eligible SKUs, every 2 h by default)

- Pre-created capacity reservation group per region (zones as configured).
- Create reservation (SKU, zone, quantity `n_requested`) → record success / failure + error code → delete → verify deleted.
- The create is all or nothing: "Capacity reservation creation succeeds or fails in its entirety" **[V]**. A failure halves the count for the next attempt (D8).
- No VM is deployed. Reservations bill from creation **[V]**; per-probe cost expected small — measure (P4).
- **Requires quota**, same as creating VMs; quota and capacity are separate checks **[V]**. → Phase 2 for GPU sizes.
- **Coverage:** reservation-supported GPU series include NC A100 v4, NCasT4 v3, NCads A10 v4, NVads A10 v5. **ND-series and NCads H100 v5 are not supported** **[V]** — H100/H200-class Azure targets always take the VMSS path (4.5). Reservation-supported GPU series allow multi-zone HA only, not fault domains **[V]**.
- B-series and Das v4 support reservations **[V]**. The CPU size comes from a series that supports reservations (§1).

### 4.5 Azure — VMSS Flexible probe (ineligible SKUs, every 2 h by default)

#### Scale set profile

- `orchestrationMode: Flexible`, capacity 0.
- `singlePlacementGroup`: `false` for every target. Microsoft lists it as unsupported in Flexible; the platform selects it **[V]**. Ansible's module requires `false` for Flexible **[V]**. A request with `true` failed with `OperationNotAllowed` at create (D4).
- Gen2 marketplace image — required for ND H100 v5 **[V]**.
- Cheapest OS disk, no public IP, no extensions.
- OS disk and NIC set to delete with the VM (`deleteOption: Delete`, the default in Flexible) **[V]** — prevents orphaned disks/NICs billing.
- Tags on the scale set: `experiment`, `target`, `run_id`. Flexible scale-set members do not get these tags (not verified; P0 checks). The scale set VM profile has no `tags` property **[V]**, and resources do not inherit the tags of a resource group or a subscription **[V]**. The prober tags each member VM, OS disk, and NIC with the same 3 tags right after the scale-out, before it polls (spec 0005, A21; spec 0010, section 7.3). P0 reads the member tags of a CPU-sized scale-out.
- Overprovisioning: not applicable — not supported in Flexible **[V]**.
- **Resilient delete: on** — retries deletes that fail with transient platform errors, during scale-in or scale-set deletion, up to 5 attempts **[V]**. No doc says that it covers direct per-VM deletes.
- **Resilient create: off**. It only retries `OSProvisioningTimedOut` and `VMStartTimedOut` **[V]**. Both occur after allocation while the VM runs and bills, so retries add billed time (inference).

#### Probe sequence

1. Set capacity to `n_requested`.
2. Poll each VM's power/provisioning state; read the allocation outcome as early as it is exposed. Do not wait for OS provisioning. **All or nothing** ([ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md)): when 1 VM is Failed, the attempt cannot place all `n_requested`. Stop polling at once and go to step 4. Do not wait for the pending VMs.
3. **Probe timeout:** if any VM is still `Creating` after X seconds (P2 sets X), record `timeout` and go to step 5. Scale operations can hang with no error (§4.7).
4. Record the raw counts `details.n_placed`, `details.n_failed`, and `details.n_pending`; classify (table below). Write the row after step 5, so that the row holds the teardown result.
5. Set capacity to 0 and delete any remaining VMs, including Failed ones. Verify zero VMs remain.
6. A failure halves the count for the next attempt (D8). Each attempt is 1 row.

**Outcome classification** (exact codes to confirm in P7)

| Error / state | Outcome | Counts as capacity signal? |
|---|---|---|
| All `n_requested` allocated | `success`, `n_placed` = `n_requested` | Yes |
| Fewer than `n_requested` allocated, 1 VM or more Failed | A failure. The outcome comes from the codes of the Failed VMs, with the rows below; for lack of capacity it is `no_capacity`. `n_placed` = 0 for `no_capacity` and `restricted_or_no_capacity`, null for `quota` and `error` (spec 0010, section 7.5); `details.n_placed` = the raw count ([ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md)). Never `partial`. | Yes — `n_requested` is not available |
| Still `Creating` at probe timeout | `timeout` | No — unknown |
| `AllocationFailed`, `ZonalAllocationFailed`, `OverconstrainedAllocationRequest`, `OverconstrainedZonalAllocationRequest` | `no_capacity` | Yes |
| `SkuNotAvailable` | `restricted_or_no_capacity` — Microsoft names no capacity as a cause for Spot only; disambiguate with 4.3; confirm in P7 | Partially |
| Quota errors | `quota` | No — exclude from heat map |
| Anything else | `error` | No |

Microsoft's allocation failure page names `OverconstrainedZonalAllocationRequest` next to `OverconstrainedAllocationRequest` **[V]**. Request constraints can also cause these 2 codes **[V]**. Thus their mapping to `no_capacity` is a decision; P7 confirms it.

**InfiniBand note:** For ND H100 v5, InfiniBand is configured automatically between VMs in the same scale set **[V]**, and IB within a scale set requires a single placement group **[V]**. Microsoft's orchestration-modes table lists InfiniBand as not supported in Flexible, and as supported in Uniform with a single placement group only **[V]**. The probe never boots VMs, so it does not test IB. The project does not measure IB-capable single-cluster capacity: the single placement group probe is dropped (D4, D6, [ADR 0021](../decisions/0021-provisioning-probes-measure-available-capacity-not-single-cluster-capacity.md)).

**Series rule (requirement):** probes per (region, SKU) run 1 at a time, so 2 entries of 1 (region, SKU) do not compete for the same capacity. A capacity reservation group also holds only 1 reservation per VM size per zone, or per VM size when the group has no zones **[V]**.

### 4.6 Azure — spot signals

Background and sources: [Azure Spot Placement Score per VM size](../research/azure-spot-placement-score-per-vm-size.md).

#### Spot Placement Score (every 15 min)

- API: `POST .../providers/Microsoft.Compute/locations/{location}/placementScores/spot/generate?api-version=2025-06-05`. No cost; available in all public regions **[V]**.
- One request scores up to 5 VM sizes in up to 8 regions, for a desired Spot VM count, regional or zonal **[V]**. No minimum number of sizes is documented **[V]**.
- Output: 1 score for each VM size in each region or zone: `High`, `Medium`, or `Low` **[V]**. Other values include `RestrictedSkuNotAvailable` and `DataNotFoundOrStale` **[V]**. Store every value as returned, in `details`. Map it to `outcome`: `High`, `Medium`, or `Low` → `success`. `RestrictedSkuNotAvailable` → `restricted`. `InsufficientQuota` (preview) → `quota`. `DataNotFoundOrStale` → `none`. Any other value → `error`. Only `success` rows count as scored in the heat map. The score is a recommendation only, valid at the time of the request **[V]**.
- Cadence: Microsoft suggests a cache TTL of 15 to 30 minutes for each combination of count, region, zone, and size **[V]**. Thus poll every 15 min; a 5-min poll returns mostly repeated values **[T]** (P11).
- Calls count against the subscription's GET call quota; a rate limit returns 429 **[V]**. Group GPU sizes 5 per request and US regions 8 per request. On a 429, retry once after 60 s. If the retry also fails, write the rows of that request with `outcome` = `error`.
- Scope: regional scores, to match the regional AWS scores in §4.2. A regional request sends `"availabilityZones": false` (spec 0009, A1). The regional example of the Microsoft how-to sets availability zones to False **[V]**. Write 1 §7.2 row for each (VM size, region): `probe_type` = `az_spot_score`, `sku` = the size, `region`, `zone` = null, and the returned value in `details`. P11 also records 1 zonal request per region to compare.
- Count: `desiredCount` = 1, to match the AWS Spot score requests (§4.2). The score applies only to a request with the same count, size, location, and zone **[V]**.
- Role: the how-to names a "Compute Recommendations Role" **[V]**; the exact role definition is not verified. The required permission is `Microsoft.Compute/locations/placementScores/generate/action` **[V]** (§8.3).
- Granularity differs from AWS: 3 levels per size here, 1 to 10 for a type mix on AWS **[V]**. Store each cloud's native scale. This difference is part of what the experiment presents.

#### Resource Graph spot data (every 5 min)

- Azure Resource Graph, `SpotResources` table **[V]**:
  - Eviction rate: `type =~ 'microsoft.compute/skuspotevictionrate/location'` (bucketed % ranges, e.g. "5-10" **[S]**)
  - Price history: `type =~ 'microsoft.compute/skuspotpricehistory/ostype/location'`
- Low eviction rate and low price both read as available.
- Refresh frequency of this data is unknown; 5-min polls can return repeated values **[T]**.

### 4.7 VMSS allocation behavior (research 2026-10-11)

**No source supports all-or-nothing.** Evidence points to per-VM outcomes. Thus the probe makes the all-or-nothing rule itself (D8):

- VMs that fail provisioning can stay in the scale set as Failed until deleted **[V]**. Not documented for Flexible without Resilient create; P1 confirms.
- Kubernetes cluster-autoscaler logs show VMSS scale-outs failing per node (`OutOfResource.provisioning-state-failed`) **[S]**.
- Exception: with fixed fault-domain spreading (`platformFaultDomainCount` > 1), the request fails if the scale set can't allocate to at least that fault-domain count **[V]**.
- Single placement group: allocation can be scoped to a single cluster **[V]**. The project does not probe it (D6).

#### The scale set does not keep retrying to reach capacity by default

- Resilient create (opt-in) retries only OS-provisioning and VM-start timeouts — not allocation failures **[V]**.
- Automatic instance repairs doesn't cover VMs unhealthy due to provisioning failure; Microsoft's guidance is to delete the failed instance and add a new one **[V]**.
- Zone balancing adds VMs to under-filled zones only on subsequent scale-outs you initiate **[V]**.
- Likely reason (interpretation, not documented): failed VMs still exist as scale-set members, so the scale set doesn't see itself as short.
- Unlike a Kubernetes controller, VMSS doesn't reconcile toward desired count after allocation failures. Tools that need retry build it themselves — cluster-autoscaler removes failed nodes and tries again **[S]**.

**Hang case:** a 0→1 scale-out has been reported stuck for hours with no allocation and no error; suggested fix is scale to 0 and back up to force re-evaluation **[S]**. `Creating` isn't billed, but the probe must time out and the sweeper must catch anything left.

#### Implications

- No-retry works in the experiment's favor: the scale set won't keep acquiring GPUs after the probe gives up.
- A short scale-out is a failure, not a depth signal ([ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md)). The probe stops at the first Failed VM and tears down. It keeps the raw count in `details.n_placed`. The next attempt halves the count (D8).
- Probe needs its own timeout (4.5 step 3).
- Sweeper deletes tagged VMs in any state, including Failed (§6).
- All of this is inferred from docs, not observed — P1, P6, P10 confirm.

### 4.8 AWS — Spot price history and interruption data

Background and sources: [AWS capacity signals per instance type](../research/aws-capacity-signals-per-instance-type.md).

#### Spot price history (every 5 min)

- API: `DescribeSpotPriceHistory`, filtered by `instance-type`, `availability-zone`, and `product-description` = `Linux/UNIX`, for the GPU types of the §4.2 request list in all AZs of the §4.2 Regions **[V]**. Read-only. No fee statement was found (not verified).
- With a start and an end time, the API returns the prices in that range, back up to 90 days, plus the last price change before the start time, which is the effective price as of the start time **[V]** ([DescribeSpotPriceHistory](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeSpotPriceHistory.html)). Whether the API writes exactly 1 record for each price change is not stated on that page or on the [Spot Instance pricing history](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-instances-history.html) page (not verified: absence claim). Poll with the start time set to the poll time minus the 5-min cadence and the end time set to the poll time. A record is new when its timestamp is at or after the start time. The record from before the start time is the effective price and is not new. The prober keeps no state (D13). Write 1 §7.2 row for each new record: `probe_type` = `aws_spot_price`, `sku`, `zone`, `outcome` = `success`, and the price and the record timestamp in `details`. For each (type, AZ) that has a record but no new record this poll, write 1 `none` row; it carries the effective price in `details` when 1 is known (spec 0008, section 5.2). For a type with no record anywhere in the Region, write 1 `none` row with `zone` null and no effective price.
- Throttle: the default bucket for `Describe*` calls, 100 tokens, refill 20 per second **[V]**. Make 1 call per Region with all request GPU types in the filter, and follow `NextToken` until it is empty.
- Coverage: the documented valid values include the GPU types of the request list except `p6-b300.48xlarge` **[V]**. Whether a filter for `p6-b300.48xlarge` returns data is untested **[T]** (P12).
- AWS documents no formula from a Spot price to free capacity (not verified: absence claim). Store the price as its own signal.

#### Spot interruption data (daily)

- The Spot Instance Advisor shows the average interruption frequency over the last 30 days, in ranges of < 5%, 5-10%, 10-15%, 15-20%, and > 20%, for each instance type and Region **[V]**.
- The page loads `https://spot-bid-advisor.s3.amazonaws.com/spot-advisor-data.json` **[V]**. AWS does not document this feed **[S]**. Fetch it once a day. If the fetch fails or the format does not parse, write 1 row per request GPU type × target Region with `outcome` = `error`, and keep the other probes running.
- The data is a trailing month at Region level. It is a slow signal next to the 5-min Spot Placement Score (§4.2).
- The feed is public HTTPS, so it needs no IAM permission.
- Storage: write the raw JSON as its own object under the §7.3 layout, with a unique key: `experiment=<name>/provider=aws/probe_type=aws_spot_advisor/date=<YYYY-MM-DD>/<ts>_raw_<uuid>.json`. A rerun on the same day writes a new object and never overwrites (§7.1). Then write 1 §7.2 row for each request GPU type × target Region, from the Linux values: `probe_type` = `aws_spot_advisor`, `sku` = the type, `region` = the Region, `zone` = null, `outcome` = `success` (or `none` when the type is not in the feed), and the range and savings in `details`. The range is the label as returned, the numeric bounds of the label, and the feed `max` of the range, in different keys (§7.2). An unknown label gives a `success` row with null label bounds. The keys are in [spec 0008](0008-aws-probes-spec.md), section 6.3.

### 4.9 AWS — provisioning probe (Phase 2, every 2 h by default; `probe_type` `aws_odcr`, implemented)

Decision: D21 and [ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md). The probe types, the `details` keys, the IAM actions, and the cleanup are below. The probe types are in §7.2. The probe launches no instance, so it needs no launch template, AMI, or subnet.

**Implemented, with 2 gaps flagged for review:**

1. **The `n_ceiling` from quota is read.** The search ceiling C is `min(n, floor(quota vCPUs ÷ vCPUs per instance))` (section 4.9, step 2). A C of 0 writes the `quota` row of ADR 0009 section 2 step 1, as Azure does (spec 0010 section 5). The quota is the limit; the probe does not subtract the vCPUs in use (open, P13). A quota error (`InstanceLimitExceeded`, `VcpuLimitExceeded`) still stops the search; the probe catches it at `CreateCapacityReservation` time instead of at a pre-check.
2. **The cleanup (below) uses no new service.** At the start of a run, for each Region with a configured entry, the probe lists its own reservations by the `experiment` tag and cancels any `active` one older than the probe timeout plus the teardown timeout, recording 1 row per cancelled reservation. A `pending` reservation cannot be cancelled and is left to its `EndDate` backstop.

- **Question:** can the account reserve `n_requested` On-Demand instances of 1 GPU type in each Availability Zone that offers it now? An entry names the type and the Region, never a zone. AWS has no read-only signal for free On-Demand capacity. Only a real attempt answers it ([AWS capacity signals](../research/aws-capacity-signals.md)).
- **Method:** an On-Demand Capacity Reservation (ODCR) only (decision, 2026-10-12). No EC2 Fleet and no `RunInstances`. EC2 Fleet `instant` is future work (below).
- **Cadence:** every 2 h by default, as the Azure provisioning probes (D2). The attempts of 1 cycle follow the search of D8. The entries of each Region run in their own lane, off the signal thread, 1 entry at a time per Region, and the Regions at the same time ([spec 0003](0003-prober-core-spec.md), section 6B; [ADR 0020](../decisions/0020-provisioning-probes-run-in-region-lanes-off-the-signal-thread.md)). When the prober stops, the search starts no new attempt (`details.search_stopped` = `"stop"`, on a real entry only; a simulated entry gets no such key, [spec 0010](0010-azure-provisioning-probes-spec.md), section 5). A real entry has 1 deadline, from before its precheck: (1 + k) x (`probe_timeout_seconds` + `teardown_timeout_seconds`), with k of [ADR 0023](../decisions/0023-the-provisioning-search-goes-up-after-a-full-success.md) (0 with no `n_max`). Before every attempt the probe window is the time left minus `teardown_timeout_seconds`; the attempt gets it as its probe timeout, and no create call starts when it is shorter than the longest probe so far or than `poll_interval_seconds`. The rows so far are returned and the last one has `details.search_stopped` = `"deadline"`; an entry with no attempt yet gets 1 `error` row with `details.skipped` = true and `details.reason` = `call deadline`. The attempt in progress always ends with its verified teardown. A simulated entry has no deadline, as in [spec 0010](0010-azure-provisioning-probes-spec.md), section 5, step 7.
- **Simulated and not simulated (D19):** a non-simulated entry calls AWS, on a GPU type or a cheap non-GPU type (to prove the path); a simulated entry (`simulate: true`) runs with fault injection and no AWS call.
- **Order** (mirrors §4.3 and §4.4):
  1. Free signals: §4.1, §4.2, and §4.8 run as before.
  2. Pre-check, read-only: the zones that offer the type. `DescribeInstanceTypeOfferings` with `LocationType` = `availability-zone-id` and the `instance-type` filter lists the zone IDs ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeInstanceTypeOfferings.html)); `DescribeAvailabilityZones` with `ZoneIds` and the `zone-type` filter `availability-zone` gives each zone name in this account ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeAvailabilityZones.html)). A zone name such as `us-east-1a` is not the same physical zone in each account; the zone ID (for example `use1-az1`) is ([AWS: Availability Zone IDs](https://docs.aws.amazon.com/ram/latest/userguide/working-with-az-ids.html)). Both calls run once per entry and cycle. The steps below (the `n_ceiling`, create, poll, cancel, and the upward search) then run once for each zone, in zone ID order, each zone on its own `n`, `n_max`, halving, and deadline; the consent keys of the entry apply to every zone. The pages of the offerings call are followed (`NextToken`). A zone ID with no name in the account is left out and logged (`odcr.zone_without_name`); when no offered zone ID has a name, the row is an `error` row with `details.reason` = "the zone IDs that offer the type have no zone name in this account". A simulated entry never halts a Region: it opens no real reservation. The probe goes on to the next zone or entry of a Region only on positive proof that no reservation is open: every row of the zone holds no reservation or has a verified teardown, an ambiguous create was followed by a successful list (`details.cleanup_listed` = `true`) (design limit: a list that succeeds but returns nothing counts as proof, so a reservation that the list does not show yet stays open until its `EndDate` backstop; the EC2 API is eventually consistent, so a result "might not be immediately visible to all subsequent commands", [AWS: Eventual consistency in the Amazon EC2 API](https://docs.aws.amazon.com/ec2/latest/devguide/eventual-consistency.html)), and no exception escaped the zone. Anything else halts the Region for the rest of the cycle: the remaining zones and entries are skipped (`odcr.region_halted`, with a `reason`), and the cleanup and the sweeper cancel what is open (ADR 0009, section 2, step 8). Then the `n_ceiling`. On-Demand quotas count vCPUs, per instance family group, for example "Running On-Demand P instances" and "Running On-Demand G and VT instances" **[V]**. `n_ceiling` = floor(quota vCPUs ÷ vCPUs per instance). The probe reads the quota with Service Quotas `GetServiceQuota` (service code `ec2`; quota codes `L-1216C47A` Standard (A, C, D, H, I, M, R, T, Z), `L-DB2E81BA` G and VT, `L-417A185B` P; [EC2 instance type quotas](https://docs.aws.amazon.com/ec2/latest/instancetypes/ec2-instance-quotas.html)) and the vCPUs per instance with `DescribeInstanceTypes` (`VCpuInfo.DefaultVCpus`), once for each zone, before the first attempt in that zone. It does not read the vCPUs in use, so the ceiling is the limit, not the free part. The row `details` of each attempt hold `quota_code`, `quota_vcpus`, `instance_vcpus`, and `n_ceiling`. A ceiling of 0 writes 1 `quota` row (`skipped` = `true`, `reason` = "quota ceiling is 0", `n_placed` null) and makes no attempt. A family with no mapped quota code (for example `inf`, `trn`, `x`) gets no cap (`details.quota_skipped`). When a call fails, the cap is not applied, `n_requested` stays the entry `n`, and `details.quota_error` holds the raw code.
  3. Create: `CreateCapacityReservation` with `InstanceType`, `InstancePlatform` = `Linux/UNIX`, `AvailabilityZoneId` (the zone ID; [API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CreateCapacityReservation.html)), `InstanceCount` = `n_requested`, `InstanceMatchCriteria` = `targeted`, `ClientToken` (a fresh UUID for this attempt), and the experiment tags in `TagSpecifications` **[V]**. `targeted` accepts only instances that "explicitly target the Capacity Reservation" **[V]**, so no other instance of the account uses it. Set `EndDateType` = `limited` with an `EndDate` a few minutes ahead as a backstop for a probe crash: AWS ends the reservation "within an hour" of that time **[V]**. The call uses a client with `max_attempts` = 1 (spec 0008, section 2.5), so a botocore retry never hides a second create behind what this probe counts as 1 attempt.
     - **Ambiguous create error.** A read timeout or a connection error on the call does not say whether AWS created the reservation; it has no `.response`, unlike a normal rejection such as `InsufficientInstanceCapacity`. The probe retries the same call, with the same `ClientToken`, up to 3 times total, with a short backoff (1 s, then 2 s), while still inside the probe timeout: a repeated `CreateCapacityReservation` request "with the same client token... returns information about the previously created capacity reservation" **[V]** (`ClientToken`, [idempotent API requests](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/Run_Instance_Idempotency.html)), so a retry that reaches AWS gets the existing reservation's ID back, and the probe continues the normal poll and teardown below. A normal rejection on any retry (it has a `.response`) is classified at once, with no further retry. If every retry still fails ambiguously, the probe cannot poll or cancel by ID; it runs a cleanup for this attempt's own tags only (`experiment`, `run_id`, `target`), with no age threshold, since a reservation that may have just been created must not wait to age (unlike the per-run cleanup below). It cancels an `active` match, and tries to cancel any other match too (the same cancel-even-on-a-non-cancellable-state rule as the teardown below). The row is `error`, with `details.ambiguous_create` = `true` and `details.create_attempts`.
  4. Read the state. `active` with `totalInstanceCount` = `n_requested` is `success`. `pending` means "the capacity provisioning is still pending" **[V]**: poll until `active` or `failed`, or until the probe timeout.
  5. Teardown: `CancelCapacityReservation` on an `active` reservation, then read the state `cancelled` to verify **[V]**. AWS lists the states that you can cancel: `assessing`, `scheduled`, `active`, and `delayed` **[V]**. `pending` is not on the list. Thus, after a `timeout`, the probe keeps polling for a bounded time (the teardown timeout) until the state is `active` or `failed`, and cancels an `active` reservation. The `EndDate` backstop covers what remains. P13 tests a cancel in `pending`.
     - A reservation already in `failed`, `expired`, or `cancelled` holds no capacity and bills nothing, so it counts as verified at once: no cancel call, no wait, no Region halt. `details.teardown.final_state` says which state was seen. The API reference lists `failed` as a request that failed (it is viewable for 60 minutes) and `expired` and `cancelled` as capacity that is no longer available, and billing starts when the reservation is provisioned **[V]** ([states](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityReservation.html), [billing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html)). That a `failed` reservation never bills is our reading of these pages (interpretation).
     - If the cancel fails or is throttled (`RequestLimitExceeded`; a `Client.` prefix is not verified), retry with backoff until the teardown timeout, then read the state again. Record the result in `details.teardown`.
     - A teardown that is not verified does not change the outcome of the attempt, but it stops the search ([ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md), section 2, step 8).
     - The `EndDate` offset must be more than the probe timeout plus the teardown timeout, so that the reservation cannot expire during a poll. The exact numbers are open (P13). A hard maximum of 900 s (`END_DATE_MAX_SECONDS`, about 4 times the default offset of 220 s) bounds the offset: a larger value raises before `CreateCapacityReservation`, and the probe makes no create call. It never clamps, because a clamp could put `EndDate` before the poll ends. IAM cannot bound a relative `EndDate`: `ec2:EndDate` takes a fixed date, and a policy variable cannot be the value of a Date condition ([policy variables](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_variables.html)).
- **Upward search ([ADR 0023](../decisions/0023-the-provisioning-search-goes-up-after-a-full-success.md)).** An entry with `n_max` goes up after a full success at C: `n_requested` = min(2 × `n_requested`, U) with U = min(`n_max`, the `n_ceiling` of step 2; an unreadable ceiling leaves `n_max`). Each step is the same create, poll, and verified cancel as steps 3 to 5. The search stops at the first step that is not a success (no bisection), at U, and at a stop. A row of a step above C has `details.search_direction` = `"up"` and `details.n_max`; `details.search_step` continues. A step takes about 2 s, and an entry makes at most 1 + ceil(log2(`n_max` / `n`)) steps. `CreateCapacityReservation` takes `InstanceCount` up to 1000 **[V]** ([CreateCapacityReservation](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CreateCapacityReservation.html)), so 16 is valid; a test checks the counts against the botocore model. A full success at 2, then 4, 8, and 16, holds 4 + 8 + 16 = 28 instance-minutes at the 60-second billing minimum. A create that fails bills nothing. A `SimEc2` entry supports the steps up: its `success` step succeeds at every `n_requested`.
- **All or nothing.** The reservation create can fail when EC2 "does not have sufficient capacity" **[V]**. AWS documents no partial reservation (not verified: absence claim). The probe does not trust this alone. An `active` reservation with `totalInstanceCount` < `n_requested` is a failure: the probe cancels it at once and writes the row of D8.
- **Outcome classification** (exact codes to confirm in P13):

  | Code or state | Outcome |
  |---|---|
  | `active` with `totalInstanceCount` = `n_requested` | `success`, `n_placed` = `n_requested` |
  | `active` with `totalInstanceCount` < `n_requested` | Cancel at once. `no_capacity` by the D8 rule: `n_placed` = 0, `details.n_placed` = `totalInstanceCount`. The next attempt halves the count. |
  | `InsufficientInstanceCapacity` | `no_capacity`. The next attempt halves the count (D8). |
  | `InstanceLimitExceeded`, `VcpuLimitExceeded` | `quota`. The search stops. |
  | `RequestLimitExceeded` (throttling) | `error`. The search stops. |
  | Still `pending` at the probe timeout | `timeout`. The search stops. |
  | State `failed` with no error code | `error`. The search stops. The state is stored raw. AWS names "request parameters that are not valid, capacity constraints, or instance limit constraints" as causes **[V]**. Open (P13): whether a `failed` state for capacity must halve the count. |
  | An error that says that the type or the zone does not support a reservation | `error`. The search stops. |
  | Anything else | `error`. The search stops. |
  | Pre-check: no zone of the Region offers the type | `restricted`, 1 row for the entry with no zone. No attempt runs. |
  | Pre-check: the `DescribeInstanceTypeOfferings` or the `DescribeAvailabilityZones` call fails | `error`, 1 row for the entry with no zone. No attempt runs. |
  | The build of a real client (EC2, the `Create` client, or Service Quotas) raises | `error`, 1 row for the entry: `error_code` = the raw AWS code or the exception type name, `latency_ms` = 0, `details.reason` = `Client build failed`, `details.n_ceiling` = null. No attempt runs for the entry; the loop goes on with the next entry, which builds again (a failed build is not cached). The EC2 client is built first and the Region cleanup runs right after it, so the cleanup is skipped only when the EC2 build fails (the next entry of the Region retries it; the sweeper of spec 0013 still covers a leak); a failure to build the `Create` or Service Quotas client comes after the cleanup. The message goes to the log as returned (event `odcr.client_failed`, not the search-failure event `odcr.failed`), never filtered ([spec 0003](0003-prober-core-spec.md), section 8.2). |

  The pre-check runs once per entry, before the search; its row has no `teardown` and no `search_step`. The code writes each row's `details` as returned, so it has no fixed shape, but every attempt row has `reservation_id` (the ODCR ID, once created) and the attempt loop's `polls`, `state`, `search_step`, and `n_config` (the entry `n`, before the ceiling of step 1); the pre-check's `restricted` or `error` row has `skipped` = `true`, `reason`, and `n_config` instead (the other skipped rows, `quota` with a ceiling of 0, `stop`, and `call deadline`, have `n_config` too), and the client-build `error` row has only `n_ceiling` (null) and `reason`. A `success`, `no_capacity`, `timeout`, or `failed` row also has `teardown`: `verified` (boolean), `errors` (the raw codes of each failed cancel or describe call, in order), `seconds`, and `final_state`. A cleanup row (below) has `cleanup` = `true`, `reservation_id`, `age_seconds`, and `teardown`.

  `InsufficientInstanceCapacity` "can occur if you ... create a new Capacity Reservation" **[V]**. `InstanceLimitExceeded` can occur "if you are creating a Capacity Reservation" **[V]**. AWS describes `VcpuLimitExceeded` for the vCPUs of running instances **[V]**; whether a reservation create returns it is to confirm in P13. `RequestLimitExceeded` means that "the maximum request rate permitted by the Amazon EC2 APIs has been exceeded" **[V]**.
- **Instance types.** The limitations of an ODCR name no GPU instance type. They name Dedicated Hosts, spread and partition placement groups, Windows BYOL, and hibernation, among others **[V]**.
- **Not an Auto Scaling group.** An Auto Scaling group starts instances "in response to a difference between desired and actual capacity" and reports errors later, in its scaling activities **[V]**. After a Spot capacity failure it "continues to automatically make the launch request until capacity becomes available" **[V]**. Thus it reports late and can get instances after the probe has recorded a failure. Details: ADR 0009, section 3.2.
- **Quota:** "Active and unused Capacity Reservations count toward your On-Demand Instance limits" **[V]**. Reservations in the `assessing`, `scheduled`, `pending`, `active`, and `delayed` states count toward the quota **[V]**. The default On-Demand quota for P and for G and VT is 0 vCPUs **[V]**. The quota request has not been made (D20).
- **Cleanup, implemented.** The AWS sweeper (§6A, spec 0013) cancels a leaked reservation every minute, independent of the prober. In addition, at the start of each `aws_odcr` call, for each Region with a configured entry that is not simulated (a simulated entry calls no provider and has nothing to clean up), the probe lists its own reservations by the `experiment` tag (`DescribeCapacityReservations` with a tag filter) and cancels any `active` one older than the probe timeout plus the teardown timeout, the same way as the per-attempt teardown below. It records 1 row per cancelled reservation, with `details.cleanup` = `true` and a fresh `run_id` of its own, not the call's `run_id`: a cleanup row is not an attempt of any target's search, and ADR 0009 section 2 step 11 derives a cycle's result from the rows that share a `run_id` and a target, so a shared `run_id` could turn a null result into a false 0. A `pending` reservation cannot be cancelled and is left to its `EndDate` backstop, which is the only protection for a reservation that a crash leaves `pending`. A query that derives ADR 0009 section 2 step 11's search result must exclude every row with `details.cleanup` = `true`: a cleanup-only `run_id` has no target search to be the result of, so it must give no result (null), never a false 0 (spec 0006, section 5.3, has the same rule). A probe that never runs again (for example, a removed config entry) stops this cleanup in its Region; the sweeper still covers the Region while the targets file names it under `aws` (spec 0013 section 4).
- **Simulated.** An entry with its own `simulate: true` key (ADR 0011 section 2) never calls AWS. The probe replaces the boto3 `ec2` client with a `SimEc2` and the wall clock with a virtual clock that advances only when the probe sleeps, so a `pending_timeout` step reaches the probe timeout at once. The entry's optional `sim_script` names 1 or a list of fault-injection steps: `success`, `short_active` (the reservation places half of `n`, so the search halves and converges), `capacity` (`InsufficientInstanceCapacity` at create), `quota` (`InstanceLimitExceeded`), `throttle` (`RequestLimitExceeded`), `pending_timeout` (the reservation never leaves `pending`), `failed` (the reservation state is `failed`), and `cancel_fails` (every cancel call fails, so the teardown never verifies). With no `sim_script`, the entry runs `success`. A list cycles 1 step per probe call, in order. A simulated entry never runs the cleanup (above): there is no real account to list.
- **Leak cost:** a reservation that the probe cannot cancel bills `n_requested` instances at the On-Demand rate **[V]** until `EndDate` plus up to 1 hour **[V]**. For that time it also holds On-Demand quota **[V]**, so the next attempt can get `InstanceLimitExceeded` (`quota`, and the search stops).
- **Worst-case cost per target per cycle.** Normal path (every attempt verifies its teardown): the sum over the cycle's attempts of N_i × max(60 seconds, the time to cancel and verify), at the On-Demand rate, because billing is per second with a 60-second minimum **[V]**. Failure path (a teardown that does not verify): at most 1 attempt's reservation, billed until its `EndDate` plus up to 1 hour, or until the next cleanup pass cancels it, whichever comes first; the search stops at that attempt (ADR 0009, section 2, step 8), so no later attempt in the same cycle adds to this cost.
- **Future work, not built: EC2 Fleet `instant`** (ADR 0009, section 6). The project keeps it for 2 uses: a Spot provisioning probe, because an ODCR reserves On-Demand capacity only, and a daily calibration pair, an ODCR and an instant fleet at the same count, to measure how often the 2 answers disagree. An instant fleet is "a synchronous one-time request that makes only one attempt" **[V]**. With `MinTargetCapacity`, "If this minimum capacity isn't reached, no instances are launched"; it needs `SingleAvailabilityZone` or `SingleInstanceType`, and its maximum value is 1000 **[V]**.

### 4.10 Provisioning matrix (all provisioning probes, all Regions)

Decision: [ADR 0018](../decisions/0018-the-provisioning-matrix-is-every-gpu-size-in-every-signal-region.md). The matrix is every GPU size that the signals watch (the smallest size with 1 whole GPU of each class) in every Region of the signal jobs of its cloud. `config/targets.yaml` lists each cell as an `provisioning_probes` entry with `simulate: true` and `n` = 2. It is the reference matrix; the live targets file is what runs, and a cell runs for real only with its own `allow_billable_provisioning: true` (ADR 0016) and the stack's `grant_billable_provisioning` (ADR 0015). The cheap CPU control entries (§1) stay and are not part of the matrix.

**AWS** (`aws_odcr`, §4.9): 6 types x 3 Regions = 18 cells, 1 entry each. An entry names the type and the Region; the probe covers every zone of the Region that offers the type. The types are `p5.4xlarge` (H100), `g7e.2xlarge` (RTX PRO 6000), `g6e.xlarge` (L40S), `g5.xlarge` (A10G), `g4dn.xlarge` (T4), and `g6.xlarge` (L4). Quota state per cell is kept outside the repo; every cell stays `simulate: true` until an operator enables it, and no new quota requests are planned.

**Azure** (§4.3 to §4.5): 5 sizes x 9 regions = 45 cells, of which 31 are listed: the file has no entry for a size that its Region does not offer (RTX PRO 6000 in all Regions except westus2; H100 and A100 in northcentralus and westcentralus; A10 and T4 in westcentralus). The probe type of each size is in §4.3. A reservation cell has 1 entry. A scale set cell (H100 and RTX PRO 6000) has 1 entry, `multi` (D6), and needs a probe subnet in its region when it runs for real (spec 0010, section 7.1). Thus the regions with a scale set cell need a probe subnet before their scale set cells run. Quota state per cell is kept outside the repo; every cell stays `simulate: true` until an operator enables it, and no new quota requests are planned.

**Size of 1 cycle.** 49 entries (18 AWS, 31 Azure), every 2 h by default (every 1 h in a config that sets it, D2: 24 cycles a day, twice the cost per day of 12 cycles). With `n` = 2, an entry holds at most 2 instances, each for at most the probe timeout plus the teardown timeout (150 s): 300 instance-seconds. Floor (every entry succeeds and holds 2 instances for the 60 s billing minimum): 1.6 instance-hours per cycle. Bound: 4.1 instance-hours per cycle (AWS 1.5, Azure 2.6). Each size has 1 GPU, so an instance-hour is a GPU-hour. A teardown that does not verify is outside the bound (§4.9, §6).

**With `n_max`** (the reference file sets `n_max: 16` on every `aws_odcr` entry and every `az_capacity_reservation` entry), an entry that succeeds at every step holds 2, 4, 8, and 16 instances in turn, and its budget grows (spec 0010, section 5, step 7). The bound above is for entries without `n_max`.

**Wall time.** The same bound is up to 300 s per entry. The scheduler runs every probe on 1 thread ([spec 0003](0003-prober-core-spec.md), section 6, rule 3), so a provisioning call delays the Spot signals of its cloud. The full matrix can take 90 min (AWS: 18 x 300 s) and 155 min (Azure: 31 x 300 s) per cycle. At the default cadence of 2 h, Azure is longer than the cadence; at a cadence of 1 h (D2), both clouds are, so the cycle bound is the limit, not the typical time. An entry with `n_max` has a longer deadline: 600 s for `n` = 2 to `n_max` = 16 at the defaults (spec 0010, section 5, step 7), so the bound is higher for the reference file. Until the provisioning probes leave the signal thread, enable only as many cells as the Spot signal delay allows.

---

## 5. Cost model

| Item | Cost |
|---|---|
| AWS signal probes | Free (read-only APIs). Rig cost = small VM + S3. |
| AWS provisioning probe (§4.9, implemented) | A Capacity Reservation is charged "at the equivalent On-Demand rate whether you run instances in reserved capacity or not", per second with a minimum of 60 seconds **[V]**. |
| Azure VM probe — failed allocation | ~$0 compute. |
| Azure VM probe — successful allocation | VM power state `Creating` is **not billed**; `Starting` **is billed** **[V]**. A successful allocation moves to `Starting`, so each placed VM incurs at least the minimum billing increment **[T]** (P3). |
| Azure capacity reservation probe | Billed from creation for its short lifetime. |
| Disks / NICs | Disks bill independently of VM compute **[V]**. NICs: no base charge found (not verified). Both covered by `deleteOption` + sweeper. |
| Sweeper | ~20,160 invocations over 2 weeks: executions ≈ $0.004, plus ≈ $0.04 in total if each run lasts 1 s at 128 MB **[V]**. The monthly free grant covers it. |
| Phase 1 CPU-sized probes | Cheap non-GPU size; same billing mechanics as GPU probes at a small fraction of the rate. |

Per Azure VM target per cycle ≈ Σ over successful search steps of (VMs placed × minimum billed increment × hourly rate). Total scales linearly with target count and cadence.

> **Correction to earlier design discussion:** compute is not "near zero" on success. Billing starts at `Starting`, not `Running`.

---

## 6. Runaway-cost protection (Azure)

1. **Prober** tears down within its own flow and verifies zero resources remain.
2. **Sweeper** — Azure Function, timer trigger every 1 min, managed identity with delete rights on the existing resource group. It deletes **only resources with the `experiment` tag**. The sweeper, the prober, and the data storage do not carry the tag, so it never deletes them (spec 0007). Each run:
   - Resource Graph query: VMs, capacity reservations, disks, NICs with the experiment tag.
   - Age = now − `timeCreated`. If > 3 min → delete via ARM, regardless of power or provisioning state (Failed VMs included).
   - Idempotent: 404 = already gone. Resource Graph is discovery only; tolerate lag (P8).
   - If a Flexible VMSS re-creates deleted members (P6), also set that VMSS's capacity to 0. Research suggests it won't (§4.7).
   - Resilient delete on the probe scale sets backs up the prober's scale-in to 0 **[V]**. No doc says that it covers the sweeper's direct per-VM deletes. During its retries, a VM can alternate between Deleting and Failed **[V]**.
3. **Worst-case orphan lifetime** ≈ 3 min threshold + 1 min sweeper interval + delete latency.
4. **3-min threshold must exceed a legitimate probe cycle.** The probe timeout plus the teardown timeout must be 30 s or more under the threshold. The defaults are 90 s and 60 s (spec 0010, decision Z17). P2 can revise the 3 numbers together.
5. **Alert if the sweeper stops running** (function failures / no runs).
6. **Budget alert** on the existing resource group — backstop only; cost data lags by hours.

---

## 6A. Runaway-cost protection (AWS)

1. **Prober** cancels its own reservation and verifies `cancelled`, or sees `failed` or `expired`, which hold no capacity (§4.9). At the start of each call it also cancels its older leftovers.
2. **Sweeper** — Lambda function, EventBridge Scheduler every 1 min, outside the VPC, with a role that can cancel only a reservation with the `experiment` tag ([spec 0013](0013-aws-sweeper-spec.md), [ADR 0019](../decisions/0019-the-aws-sweeper-is-a-scheduled-lambda-function.md)). It is deployed whether or not `grant_billable_provisioning` is set. Each run, in each Region that the targets file names under `aws`:
   - `DescribeCapacityReservations` with the `experiment` tag.
   - Age = now − `CreateDate`. If > 3 min and the state accepts a cancel (`assessing`, `active`, `delayed`) → `CancelCapacityReservation`.
   - A `pending` reservation cannot be cancelled; it waits for its `EndDate` (§4.9).
   - It writes its own log to the store, under the logs prefix; its role has no CloudWatch Logs permission.
3. **Worst-case orphan lifetime** ≈ 3 min threshold + 1 min sweeper interval for a cancellable reservation; `EndDate` plus up to 1 hour for a `pending` one (§4.9).
4. The §6 item 4 rule holds: the `aws_odcr` probe timeout plus teardown timeout (90 s and 60 s) is 30 s or more under the threshold.
5. **Alert if the sweeper stops running:** a CloudWatch alarm on the built-in `Invocations` and `Errors` metrics of the function, which fires when no run succeeds for 10 minutes, and emails `alert_email` through SNS ([ADR 0024](../decisions/0024-the-aws-sweeper-alarm-reads-the-lambda-metrics.md), [spec 0004](0004-aws-stack-spec.md) section 4.8). It reads metrics, not logs and not telemetry, so D14 holds.

---

## 7. Data

### 7.1 Storage

- AWS → S3 bucket. Azure → Blob container. Each VM writes only to its own cloud's store with native identity.
- Write each record immediately after each probe or poll. Never batch in VM memory or local disk.
- Unique object key per record; never overwrite. Prober is stateless — a VM crash loses at most the in-flight probe.

### 7.2 Record schema (one row per probe attempt or poll)

| Field | Notes |
|---|---|
| `ts_utc` | Probe completion time |
| `run_id` | Unique per cycle |
| `simulated` | `true` / `false` (ADR 0011 section 6, item 4, superseding `run_mode` and D19). Heat-map queries filter `simulated = false`. A row written before this change has `run_mode` and no `simulated` field; the reader rule (ADR 0011 section 5) is `run_mode: local` means `simulated: true`, any other value means `simulated: false`. |
| `provider` | `aws` / `azure` |
| `region`, `zone` | Zone null when regional |
| `probe_type` | `aws_cb_offering`, `aws_cb_earliest`, `aws_cb_longest` (§4.1), `aws_spot_score`, `aws_spot_price`, `aws_spot_advisor`, `aws_odcr` (§4.9), `az_capacity_reservation`, `az_vmss_flex`, `az_spot_score`, `az_spot` (Resource Graph eviction rate and price), `az_precheck` (§4.3) |
| `sku` | Instance type / VM size |
| `placement_group_mode` | `multi` / `n/a` (`single` is removed, ADR 0021; old rows can hold it) |
| `n_requested` | |
| `n_placed` | Provisioning probes: `n_requested` on `success`, 0 on `no_capacity` and `restricted_or_no_capacity`. The raw placed count goes in `details.n_placed` ([ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md)). |
| `outcome` | `success`, `partial`, `no_capacity`, `restricted`, `restricted_or_no_capacity`, `quota`, `timeout`, `error`, `none`, `cap` (§4.5 table; signal rows below). A provisioning probe must not write `partial` ([ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md)); the value stays for older rows. |
| `error_code` | Raw provider code |
| `latency_ms` | Request → outcome |
| `details` | JSON: spot score, eviction bucket, price, offering start/end/duration/fee, etc. |

For signal rows (`aws_spot_score`, `aws_spot_price`, `aws_spot_advisor`, `az_spot_score`, `az_spot`), `outcome` is `success` when the provider returned a value, `none` when it returned no value for the type, `cap` for a target-capacity cap (§4.2), and `error` on a failure. The value itself (a score, a price, a range) goes in `details`. For `aws_spot_price` only, a `none` row can still carry a real value: `details.effective_price` holds the last known price when the type has 1, even with no new record this poll (§4.8).

**Raw values** ([ADR 0006](../decisions/0006-store-raw-provider-values-normalize-separately.md)). Each row keeps the raw provider values. The rule applies to every probe type:

1. A probe must store each numeric value that a provider returns as a number. It must not round it, bucket it, or replace it with a category.
2. A probe must store each provider-specific string exactly as returned. This includes an error code and a value that the probe does not know. An unknown value must not become null.
3. When a provider returns a range, the row must hold the string as returned and the numeric bounds. The bound keys are `<name>_min_<unit>` and `<name>_max_<unit>`, where `<name>` is the key of the string and `<unit>` is the unit of the value, for example `eviction_rate_min_pct`. An open bound is null. If the provider gives the bounds as numbers, store them as returned. If it gives only the string, parse the bounds from it. A string that does not parse gives null bounds and is still stored. If the numbers and the string disagree, store both in different keys.
4. A derived value, for example `outcome` or an `n_ceiling`, must be stored next to the raw values. It must not replace them.
5. A scale that spans providers is a separate layer. It must read the raw fields. It must never write into them.

The rule covers each value that a probe reads as a signal, and each error code. It does not cover identifiers and request parameters. The spec of each probe states the keys of its `details`. The rule does not change the error message rules of spec 0009 (decision A7).

An `az_precheck` row records the §4.3 pre-check of 1 allocation-probe entry: `outcome` is `success`, `restricted`, or `error`, and `details` holds the probe type of the entry (`entry_probe_type`) and the `n_ceiling` from quota (spec 0010, section 4.1).

`probe_type` stays on every row. Offerings, live allocations, and spot signals are different signals and must not be merged into one number.

### 7.3 Layout

`experiment=<name>/provider=<p>/probe_type=<t>/date=<YYYY-MM-DD>/<ts>_<target>_<uuid>.jsonl` ([ADR 0010](../decisions/0010-one-experiment-file-of-modules-and-jobs-with-a-billable-guard.md) section 5, item 4, kept by ADR 0011 section 6). Hive-style partitions keep DuckDB globs cheap; `date` stays last. A simulated row's key looks exactly like a real row's key: `simulated` is a row field (§7.2), not a path segment. A fully simulated run (`run --simulate`, or `sim`) writes only to a local `file://` folder, never to the cloud bucket.

### 7.4 Volume

Small: tens of records per 2-h cycle plus spot polls every 5 min. No compaction needed for a 2-week run.

### 7.5 Visualization requirements (build later)

- Runs on a developer laptop; no dependency on the rig being alive.
- DuckDB with `httpfs` (S3) and `azure` (Blob) extensions **[V]**; one query can union both stores **[S]**.
- Single-cloud users point at one store.

---

## 8. Infrastructure (OpenTofu)

### 8.1 Repo layout (proposed)

```text
config/targets.yaml        # regions, zones, SKUs, entry `n` and optional `n_max` values, PG modes, cadences, CB durations/window
prober/                    # shared core + aws and azure adapters
sweeper/azure/             # timer-triggered function
sweeper/aws/               # scheduled Lambda function (spec 0013)
terraform/aws/             # standalone
terraform/azure/           # standalone
web/                       # later
docs/
```

### 8.2 AWS stack

- Small EC2 instance for the prober; cron or systemd timer.
- Sweeper: a Lambda function on an EventBridge Scheduler schedule, every 1 min, outside the VPC (§6A, spec 0013).
- Instance profile, least privilege: `ec2:DescribeCapacityBlockOfferings`, `ec2:GetSpotPlacementScores`, `ec2:DescribeSpotPriceHistory`, `s3:PutObject` on the data bucket. Outbound HTTPS for the Spot Instance Advisor feed (§4.8).
- S3 bucket, public access blocked.
- No inbound access required.
- Deploys into an existing VPC and subnet, given by required variables (spec 0004). Outbound internet access from the subnet is a deploy prerequisite that the network owner sets up.
- **Manual prerequisite:** Spot quota increase for GPU families in each target region (D20) — raises the Spot Placement Score target cap (§4.2). Document in README.

### 8.3 Azure stack

- **Existing network (spec 0005, section 1):** the stack deploys into 1 existing resource group and 1 existing subnet, given by required variables. It creates no resource group, VNet, subnet, or NSG. Outbound internet access from the subnet is a deploy prerequisite that the network owner sets up.
- **Stack resources in that group:** prober VM (system-assigned managed identity), sweeper Function App (consumption plan) + its storage, data storage account + Blob container. None carries the `experiment` tag.
- **Probe resources in that group:** capacity reservation group per region; VMSS Flexible per target at capacity 0, on the existing subnet. A scale set needs a subnet in its own region, thus scale set probes run only in the region of the subnet (spec 0010, section 7.1). Each probe resource carries the `experiment` tag.
- **RBAC:**
  - Prober: create/scale/delete on the group; Blob write on data container; Reader at subscription for Resource Graph and SKU/quota reads; `Microsoft.Compute/locations/placementScores/generate/action` at subscription for the Spot Placement Score (§4.6), through the named role if it exists, else a custom role with this 1 action.
  - Sweeper: delete on the group, only for resources with the `experiment` tag (spec 0007); Reader for Resource Graph.
- Budget + alert on the group.
- **Manual prerequisite:** GPU quota per family per region (support request); file on day one (D20). Required for both reservation and VMSS GPU probes. Document in README.

### 8.4 Config

One `targets.yaml` drives both clouds, with per-provider sections so a single-cloud user deletes the other section.

Each allocation target carries its own `simulate` key (ADR 0011 section 2), not `run_mode`. ADR 0010 is built: `targets.yaml` is 1 experiment file of modules and jobs; Phase 2 rollout is adding an allocation job, or removing a `simulate: true` key, not flipping a mode. [ADR 0015](../decisions/0015-two-independent-controls-replace-the-terraform-allocation-guard.md) is built too, in place of ADR 0010 section 7: each stack's own `grant_billable_provisioning` variable, not the targets file, decides whether the OpenTofu stack of that cloud plans an allocation-probe resource or grants the rights to provision one (spec 0004 section 4.2, spec 0005 section 4.1, R7); the targets file's own `allow_billable_provisioning` key stays, checked only by the prober.

---

## 9. Pilot tests (before the 2-week run)

| # | Phase | Test | Why |
|---|---|---|---|
| P0 | 1 | **Free — run first.** Create a Flexible VMSS, ND H100 v5, `singlePlacementGroup: false`, capacity 0. | Nothing allocates, so no cost. Shows whether a capacity-0 GPU scale set deploys with zero GPU quota **[T]**. |
| P1 | 2 | Scale a Flexible VMSS 0 → `n_requested` where capacity < `n_requested`. Do allocated VMs stay while the rest go Failed? How soon does the first Failed VM show? | Evidence points to per-VM outcomes (§4.7). The probe stops at the first Failed VM and tears down (D8). P1 shows how much billed time the stop saves. |
| P2 | 2 | Time from capacity request → allocation outcome; time spent in `Creating` before `Starting`. | Sets the 3-min threshold margin and teardown timing. The CPU size in Phase 1 gives a preview only. |
| P3 | 2 | Cost Management charge for one successful H100-class probe. | Validates §5; confirms minimum billing increment. |
| P4 | 1 → 2 | Capacity reservation create/delete latency and charge. | Mechanics on the CPU size in Phase 1; GPU cost in Phase 2. |
| P5 | 1 | Kill the prober mid-probe. | Sweeper must remove everything within threshold + 1 min. |
| P6 | 1 | Delete a Flexible member VM directly; leave Failed VMs in place for 10 min. Does the VMSS re-create or retry anything? | Research says no (§4.7). Confirms the sweeper only needs to delete VMs. |
| P7 | 1 → 2 | Capture actual error codes. Phase 1: quota and restricted codes (GPU request with zero quota). Phase 2: no-capacity and reservation-failure codes. | Finalizes the classification table. |
| P8 | 1 | Resource Graph lag for new resources; `timeCreated` present on VMs, capacity reservations, disks, NICs. | Sweeper correctness. |
| P9 | 1 | AWS Spot score requests (§4.2): does any single GPU type score above 3 while the CPU single control stays at 3 or lower and the CPU mix control scores above 3; `TargetCapacityLimitExceeded` at target capacity 1; `MaxConfigLimitExceeded` with 11 configurations (the closed test); the g5-family mix never below `g5.xlarge` alone. | Confirms 1 instance type per GPU row. If no single GPU type scores above 3 in 24 h, G rows move to size-mixes of 1 family and P rows show as "not measurable". Shows whether Spot quota must rise first (§4.2). The 24 h A10G test  reads the same request list. |
| P10 | 2 | Hang case: confirm the probe timeout plus scale-to-0 clears a scale-out stuck in `Creating`. | Covers the reported multi-hour hang (§4.7). |
| P11 | 1 | Azure Spot Placement Score (§4.6): the role or custom role that grants `placementScores/generate`; 5 GPU sizes and 8 US regions in 1 request; how often a score changes between 15-min polls; the 429 threshold; `RestrictedSkuNotAvailable` and `DataNotFoundOrStale` rates for GPU sizes. | Sets the Azure Spot cadence and request grouping. Confirms the prober RBAC (§8.3). |
| P12 | 1 | AWS Spot price history and interruption data (§4.8): records per GPU type and AZ over 24 h; whether a filter for `p6-b300.48xlarge` returns data; whether the Advisor feed lists the GPU types of the request list, and how often it changes. | Sets the price poll and the feed schedule. Shows which request GPU types have price data. |
| P13 | 1 → 2 | AWS provisioning probe (§4.9) on a cheap non-GPU type, then on GPU types: Capacity Reservation create and cancel latency, the state after create, the error codes; the code when a type does not accept reservations; a cancel of a `pending` reservation; whether a `failed` state for capacity occurs. | Finalizes the AWS classification table. |

---

## 10. Open questions

- Target regions and SKUs (continental US).
- `n_ceiling` per SKU — needs quota numbers.
- Does a capacity-0 Flexible VMSS for a GPU size deploy with zero GPU quota? (P0) If not, Phase 1 can't pre-create GPU scale sets.
- Single-cluster capacity: closed, not measured ([ADR 0021](../decisions/0021-provisioning-probes-measure-available-capacity-not-single-cluster-capacity.md)). A later decision can bring it back as a Uniform probe.
- Capacity Block query parameters: which durations and start windows to sample.
- Probe timeout X for VMs stuck in `Creating`: the default is 90 s (`probe_timeout_seconds`, spec 0010, section 10). P2 sets the final value, within the rule of §6, item 4.
- Prober/sweeper language (reference repo is Python).
- Probe concurrency across targets: closed. Probes per (region, SKU) run in series (§4.5, series rule).
- Zonal vs regional probes in Azure — per zone, or regional?
- Visualization design (later).

---

## 11. Corrections captured from the design discussion

- Azure billing starts at `Starting`, not `Running`. Successful probes are not free.
- `overprovision` does not apply to Flexible mode, so that setting drops out of the design.
- VMSS all-or-nothing allocation was stated without a source. Research (§4.7) points to per-VM outcomes and no automatic retry; P1 and P6 confirm.
- D8 said that 1 request for the maximum count could replace the search, because a short fill gives `n_placed`. The project rejected partial fills: a short fill is a failure, and a failure halves the count ([ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md)).
- The AWS side was described as "fully live today." Not quite: Spot Placement Score's target capacity is capped by recent Spot usage; with no recent usage, a low default limit aligned with the Spot request limit applies (§4.2). Capacity Block offerings are unaffected.
- Capacity Block offerings were loosely called an on-demand depth signal. They show reservable future capacity, not instant on-demand availability.
- Capacity reservation probes were assumed to possibly run without GPU quota. They require quota like VMs (§4.4) → Phase 2.

---

## 12. Sources

- Azure VM power states and billing — `Creating`: not billed; `Starting`: "Virtual machine is powering up" — billed. <https://learn.microsoft.com/azure/virtual-machines/states-billing>
- Azure Spot VMs — `SpotResources` table in Resource Graph for eviction rates and price history. <https://learn.microsoft.com/azure/virtual-machines/spot-vms>
- Azure Spot Placement Score how-to (Microsoft Learn) — inputs, limits, levels, cache TTL, GET quota, role name; the regional example sets availability zones to False. <https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score>
- Azure Spot Placement Scores REST API 2025-06-05 — endpoint, request body, score per size, region, and zone. <https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05>
- Azure Compute permissions — `Microsoft.Compute/locations/placementScores/generate/action`. <https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/compute>
- AWS `DescribeSpotPriceHistory` API reference — filters, 90 days, valid values. <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeSpotPriceHistory.html>
- AWS EC2 API throttling — default bucket for `Describe*` calls. <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/throttling.html>
- AWS Spot Instance Advisor — 30-day interruption frequency ranges; page source loads the feed. <https://aws.amazon.com/ec2/spot/instance-advisor/>
- AWS capacity signals per instance type (research note) — Spot price history, the Spot Instance Advisor and its feed, throttle buckets. [aws-capacity-signals-per-instance-type.md](../research/aws-capacity-signals-per-instance-type.md)
- Azure Spot Placement Score per VM size (research note) — inputs, limits, a score per VM size, cache guidance, role and permission. [azure-spot-placement-score-per-vm-size.md](../research/azure-spot-placement-score-per-vm-size.md)
- VMSS orchestration modes comparison — lists Single Placement Group as unsupported in Flexible (the platform selects the value; true with FD>1 and false with FD=1 for sizes without memory-preserving updates); lists InfiniBand as not supported in Flexible and as single-placement-group only in Uniform; lists overprovisioning as not supported in Flexible; Flexible members are standard VMs. <https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes>
- VMSS properties — single placement group limited to max 100 VMs. <https://learn.microsoft.com/en-us/python/api/azure-mgmt-compute/azure.mgmt.compute.types.virtualmachinescalesetproperties?view=azure-python>
- InfiniBand setup: the applies-to banner lists Flexible and Uniform; "limit the deployment to a single placement group for InfiniBand communication." The orchestration-modes table lists InfiniBand as not supported in Flexible. The page also allows an availability set, and says a single placement group caps at 100 VMs by default (300 by support request). <https://learn.microsoft.com/en-us/azure/virtual-machines/setup-infiniband>
- ND H100 v5 series — IB auto-configured between VMs in the same scale set; Gen2 image required. <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndh100v5-series>
- Azure VM delete — `deleteOption` for OS disk and NIC; Delete is the default in Flexible. <https://learn.microsoft.com/en-us/azure/virtual-machines/delete>
- Azure vCPU quotas — family vCPUs and Total Regional vCPUs tiers; quota and capacity checked separately. <https://learn.microsoft.com/en-us/azure/virtual-machines/quotas>
- `az capacity reservation` CLI reference (Microsoft Learn) — SKUs must have `CapacityReservationSupported` = true. <https://learn.microsoft.com/en-us/cli/azure/capacity/reservation?view=azure-cli-latest>
- `az vm list-skus` CLI reference (Microsoft Learn) — `--size` accepts a partial name; the output includes subscription-level restrictions; `--all` adds sizes not available under the subscription. <https://learn.microsoft.com/en-us/cli/azure/vm?view=azure-cli-latest#az-vm-list-skus>
- Use tags to organize your Azure resources (Microsoft Learn) — resources don't inherit the tags of a resource group or a subscription. <https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/tag-resources>
- Virtual Machine Scale Sets - Create Or Update, REST API 2024-11-01 (Microsoft Learn) — `VirtualMachineScaleSetVMProfile` has no `tags` property. <https://learn.microsoft.com/en-us/rest/api/compute/virtual-machine-scale-sets/create-or-update?view=rest-compute-2024-11-01>
- On-demand capacity reservation overview (Microsoft Learn, updated 2026-02-17) — 1 reservation per VM size per zone in a group; reservation fails if capacity unavailable; billed whether or not VMs are deployed; creating one "requires a quota in the same manner as when you create VMs"; ND-series and NCads H100 v5 not supported; B-series, Da and Das v4 and newer, and NC A100 v4 supported; GPU series multi-zone HA only. <https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-overview>
- Spot placement score (AWS EC2 User Guide) — target capacity limit based on recent Spot usage, low default aligned with Spot request limit; AWS can limit new request configurations within 24 h; fewer than 3 instance types returns a low score; no charge. <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html>
- How Spot placement score works (AWS EC2 User Guide) — scores per Region or per AZ; 1 score for the whole request, "calculated based on the target capacity, the composition of the instance types, the current Spot usage trends, and the time of the request"; assumes the `capacity-optimized` allocation strategy. <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-sps-works.html>
- AWS Service Reference for EC2 — `DescribeCapacityBlockOfferings` action, read-only. <https://servicereference.us-east-1.amazonaws.com/v1/ec2/ec2.json>
- Microsoft Q&A, "Azure VM Provisioning Failure Due to Real-Time Capacity Constraints (SkuNotAvailable Issue)" **[S]** — no public API for real-time capacity; checked at allocation time. <https://learn.microsoft.com/en-us/answers/questions/5853193/azure-vm-provisioning-failure-due-to-real-time-cap>
- Troubleshoot ZonalAllocationFailed / AllocationFailed / OverconstrainedAllocationRequest (Microsoft Learn). <https://learn.microsoft.com/en-us/troubleshoot/azure/azure-kubernetes/error-codes/zonalallocation-allocationfailed-error>
- Troubleshooting Azure VM allocation failures (Microsoft Learn) — names `OverconstrainedZonalAllocationRequest` and `OverconstrainedAllocationRequest`. <https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machines/windows/allocation-failure>
- Amazon EC2 Capacity Blocks for ML — supported instance types; P6e-GB200 as UltraServer blocks only. <https://aws.amazon.com/ec2/capacityblocks/> and <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html>
- DuckDB Azure Extension docs — `azure` extension reads Blob. <https://duckdb.org/docs/current/core_extensions/azure.html>
- MotherDuck "Analyze Data in Azure with DuckDB" **[S]** — S3 + Azure union in one query. <https://motherduck.com/blog/analyze-data-in-azure-with-duckdb/>
- Azure Functions consumption prices (Azure retail prices API) — executions and GB-s rates. <https://prices.azure.com/api/retail/prices?$filter=serviceName%20eq%20'Functions'%20and%20armRegionName%20eq%20'eastus'%20and%20priceType%20eq%20'Consumption'>
- AWS reference repo README — Lambda writes CloudWatch custom metrics via PutMetricData; dashboards via CDK. <https://github.com/aws-solutions-library-samples/guidance-for-ec2-spot-placement-score-tracker>
- Resilient create and delete for VMSS (Microsoft Learn) — create retries only OS-provisioning and VM-start timeouts; unrecoverable VMs stay Failed; delete retries deletes that fail with transient platform errors during scale-in or scale-set deletion, up to 5 attempts. <https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/resilient-vm-create-delete>
- Troubleshoot VMSS instances not repaired (Microsoft Learn) — repairs don't cover provisioning failures; delete the failed instance and add a new one. <https://learn.microsoft.com/troubleshoot/azure/virtual-machine-scale-sets/restart-stop/instances-not-repaired>
- Troubleshoot AllocationFailed / ZonalAllocationFailed for VMSS (Microsoft Learn) — single-placement-group allocation scoped to one cluster. <https://learn.microsoft.com/troubleshoot/azure/virtual-machine-scale-sets/allocationfailed-or-zonalallocationfailed>
- VMSS with Availability Zones (Microsoft Learn) — with static fixed spreading, request fails below fault-domain count; placement groups apply only to Uniform; best-effort zone balance applies on subsequent scale-outs. <https://learn.microsoft.com/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-use-availability-zones>
- Microsoft Q&A, "VMSS stuck in '0 to 1' state for hours, no allocation, no errors" **[S]**. <https://learn.microsoft.com/en-us/answers/a/12616338>
- kubernetes/autoscaler issue 7432 — per-node VMSS provisioning failures **[S]**. <https://github.com/kubernetes/autoscaler/issues/7432>
- Ansible `azure_rm_virtualmachinescaleset` module (azure.azcollection 3.21.0) — states Flexible requires `single_placement_group=False`. This is an Ansible client rule, not an Azure API rule. <https://docs.ansible.com/ansible/latest/collections/azure/azcollection/azure_rm_virtualmachinescaleset_module.html>

Sources of single facts in the text:

- [`DescribeCapacityBlockOfferings` is read-only; Offering query inputs: instance type, count, duration, start-date window](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html)
- [Querying Capacity Block offerings has no charge](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-pricing-billing.html)
- [Offerings return AZ, start, end, duration, count, upfront fee](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityBlockOffering.html)
- [P5e = H200, P5 = H100](https://aws.amazon.com/ec2/instance-types/p5/)
- [P4d = A100](https://aws.amazon.com/ec2/instance-types/p4/)
- [IAM action `ec2:GetSpotPlacementScores`](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/sps-iam-permission.html)
- [Resource Graph needs only Reader (read) access](https://learn.microsoft.com/en-us/azure/governance/resource-graph/overview)
- [Eviction rate is bucketed % ranges, e.g. "5-10"](https://learn.microsoft.com/en-us/answers/questions/1193700/how-to-interpret-spotevictionrate-in-azure-resourc)
- [`SkuNotAvailable` can mean restriction or no capacity](https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/error-sku-not-available)
- [Zone balancing fills under-provisioned zones only on later scale-outs](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-zone-balancing)
- [NICs bill independently of VM compute](https://azure.microsoft.com/en-us/pricing/details/virtual-network/)
- [A scale set needs a subnet in its own region (VNets are regional)](https://learn.microsoft.com/en-us/azure/virtual-network/virtual-networks-faq)
- [Azure cost data lags by hours](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets)
- [single GPU, Inferentia, and Trainium types score above 3; CPU types stay at 3 or lower](https://d2krkjqajp4l0e.cloudfront.net/latest_aws.json)
- [AWS can limit new configurations within 24 h; retry of configurations "used within the last 24 hours"](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html)
- [an AWS sample puts the configuration limit at about 10](https://github.com/aws-solutions-library-samples/guidance-for-ec2-spot-placement-score-tracker/blob/433c22eebc26f85a84bea05cae46282ab3476888/README.md)
- [default Spot quota for P and for G and VT is 0 vCPUs](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-limits.html)
- [3 levels per size on Azure vs 1 to 10 per type mix on AWS](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html)
- [AWS does not document the Advisor feed](https://github.com/alexei-led/spotinfo/blob/a1306dc745a82450ecd89878980372ccb03e8317/docs/data-sources.md)
- [B-series supports scale sets](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/b-family)
- [`Standard_D2as_v4` is in the Dasv4 series](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/dasv4-series)
- [A Capacity Block gives "predictable capacity assurance for GPU instances"; offerings can start in as soon as 30 minutes; Capacity Block instances "don't count against your On-Demand Instances limits"; the supported instance types and UltraServer types, with no G-family type](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html)
- [Resource SKUs list "advertises the set of supported VM sizes"; the series that support capacity reservations (NCasT4_v3, NC_A100_v4, NVadsA10_v5) and the series that do not (NCads H100 v5)](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-overview)
- ["Capacity reservation creation succeeds or fails in its entirety"](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-create)
- [On-Demand quotas count vCPUs per family group; "Running On-Demand P instances" and "Running On-Demand G and VT instances" default to 0; Capacity Reservations count "even if they are unused"; On-Demand instances bill per second while `running`, with a 60-second minimum](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-on-demand-instances.html)
- [`CreateCapacityReservation` can fail when EC2 "does not have sufficient capacity"; `InstanceCount` 1 to 1000; `limited` end within an hour of `EndDate`; `TagSpecifications` tags the reservation](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CreateCapacityReservation.html)
- [Reservation states `pending`, `active`, `failed`, and `cancelled`; `totalInstanceCount`](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityReservation.html)
- [`CancelCapacityReservation` releases the capacity and sets the state to `cancelled`; it can cancel `assessing`, `scheduled`, `active`, and `delayed` reservations](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CancelCapacityReservation.html)
- [`targeted` accepts only instances that explicitly target the reservation](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/cr-concepts.html)
- [An ODCR reserves capacity "for any duration"; an immediate ODCR is "available for use immediately" and you can cancel it "to stop incurring charges"](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html)
- ["Active and unused Capacity Reservations count toward your On-Demand Instance limits"; reservations in the `assessing`, `scheduled`, `pending`, `active`, and `delayed` states count toward the quota; the Limitations section names no GPU instance type](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html)
- [A Capacity Block price "is charged up front at the time the reservation is made" and "depends on available supply and demand"](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-pricing-billing.html)
- [Capacity Blocks reserve GPU instances "on a future date"; start up to 8 weeks ahead; "Capacity Block cancellations aren't allowed"](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html)
- [A Capacity Reservation is charged at the On-Demand rate whether used or not, per second with a minimum of 60 seconds](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html)
- [`InsufficientInstanceCapacity` and `InstanceLimitExceeded` can occur when you create a Capacity Reservation; `VcpuLimitExceeded` is the vCPU limit of running instances; `RequestLimitExceeded` is the API request rate](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html)
- [`ClientToken`: "if you repeat the request with the same client token, if the original request was received and processed successfully, the subsequent retries with the same client token will not be processed further, and the information about the previously created capacity reservation will be returned"](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/Run_Instance_Idempotency.html)
- [An instant fleet is "a synchronous one-time request that makes only one attempt"; deleting an instant fleet terminates its instances](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instant-fleet.html)
- [`MinTargetCapacity`: "If this minimum capacity isn't reached, no instances are launched"; maximum 1000; needs `SingleAvailabilityZone` or `SingleInstanceType`](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_OnDemandOptionsRequest.html)
- [An Auto Scaling group starts instances "in response to a difference between desired and actual capacity"; errors are in the scaling activities](https://docs.aws.amazon.com/autoscaling/ec2/userguide/CHAP_Troubleshooting.html)
- [Auto Scaling "continues to automatically make the launch request until capacity becomes available" (Spot capacity error)](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ts-as-instancelaunchfailure.html)
