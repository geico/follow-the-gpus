# 0020. The provisioning probes run in Region lanes, off the signal thread

- Status: accepted
- Date: 2026-10-15

## Context

The scheduler ran every probe on 1 thread ([spec 0003](../spec/0003-prober-core-spec.md), section 6, rule 3), and a provisioning probe ran its entries in series ([spec 0010](../spec/0010-azure-provisioning-probes-spec.md), section 5). The full provisioning matrix (ADR 0018) has 18 AWS entries in 3 Regions and 63 Azure entries in 9 regions (27 reservation, 36 scale set). The worst case of 1 entry in 1 cycle is 2 attempts x (probe timeout 90 s + teardown timeout 60 s) = 300 s (ADR 0018, section 5). In series that is 90 min for AWS and 315 min for Azure. The Azure figure is longer than the 2 h cadence, and while a provisioning call runs, the 5 min and 15 min Spot signals of that cloud do not run.

3 more problems come from the same design:

1. **Rows.** The scheduler wrote the rows of a call only after `run` returned. A call of many entries kept every row in memory for up to hours, against spec 0002, 7.1 ("Write each record immediately after each probe").
2. **Stop.** On SIGTERM the call ran on through all its entries. systemd sends SIGKILL after `TimeoutStopSec` = max(180, probe + teardown + 30), so a stop during a call of more than 1 entry lost every row of the call. A search deadline per entry does not help: the call is many entries.
3. **The slowest attempt.** A scale set attempt can run a pre-teardown (up to the teardown timeout) before its probe and teardown ([spec 0010](../spec/0010-azure-provisioning-probes-spec.md), section 7.2, step 2): 60 + 90 + 60 = 210 s at the defaults, above the 180 s that the formula gives.

Quota and rate limits are per Region on both clouds:

- Azure: "The vCPU quotas for virtual machines and scale sets are arranged in two tiers for each subscription, in each region" ([vCPU quotas](https://learn.microsoft.com/en-us/azure/virtual-machines/quotas)). "Azure Compute requests may be throttled at a subscription and on a per-region basis" ([Compute throttling](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machines/windows/troubleshooting-throttling-errors)).
- AWS: "Amazon EC2 throttles EC2 API requests for each AWS account on a per-Region basis" ([EC2 API throttling](https://docs.aws.amazon.com/ec2/latest/devguide/ec2-api-throttling.html)). The On-Demand vCPU quotas that a Capacity Reservation uses are per Region and per instance family group (spec 0002, section 4.9).

The Azure pre-check computes the `n_ceiling` from the free quota of the region ([spec 0010](../spec/0010-azure-provisioning-probes-spec.md), section 4). The number is right only when no other cell of ours holds quota in that region at the same time.

## Options

| Option | Signals delayed? | Full matrix per cycle | Cost |
|---|---|---|---|
| A. As now: 1 thread | Yes, for hours | 315 min (Azure) | none |
| B. 1 worker thread for all provisioning probes | No | 315 min (Azure), over the cadence | small |
| C. A second `run --probe` process per provisioning probe (a systemd unit each) | No | 315 min unless C also splits Regions | new units in both stacks, a stop timeout and a health check per unit, 1 Python and SDK copy per process |
| D. Bound the entries per call (a part of the matrix per cycle) | Yes, shorter | under the cadence | each cell runs less often than its cadence (ADR 0018 wants every cell every cycle) |
| E. Lanes: 1 thread per Region, 1 lane at a time per Region | No | 35 min (Azure), 30 min (AWS) | threads, a Region lock |
| F. E with more than 1 lane per Region | No | shorter | 2 cells of 1 Region hold quota at once: a pre-check `n_ceiling` that is wrong, a `quota` row that is ours, more load on the per-Region rate limits |

E is the minimum that solves both parts of the problem. B and C fix only the delay. F adds risk that buys nothing: E is already far under the cadence.

## Decision

### 1. Which probes

A probe class with `region_lanes = True` runs in lanes: `aws_odcr`, `az_capacity_reservation`, `az_vmss_flex`, and `az_precheck`. The pre-check holds no quota, but it reads the same entries and makes 2 reads per entry, and in a lane its usage numbers never include a cell of ours in progress. Every other probe stays on the scheduler thread, unchanged.

### 2. Lanes

When a lane probe is due, the scheduler starts 1 thread per Region of the `provisioning_probes` entries. The thread calls `run` 1 time with the section in which `provisioning_probes` holds only the entries of its Region, in config order. The scheduler loop does not wait. A lane of the last call that still runs when its probe is due again is not started again (`lane.overrun`). Each lane has its own shallow copy of the probe, so that the state a probe keeps for 1 call is never shared by 2 threads; `Clients` builds each client once, under a lock. boto3 clients come from their own `Session` ([boto3, multithreading](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/clients.html#multithreading-or-multiprocessing-with-clients)).

### 3. 1 lane at a time per Region

A lock per Region, shared by the lane probes of the process, is held for the whole lane call. At most 1 entry, and 1 attempt of its search, holds quota or calls the provider in a Region at a time, as on 1 thread. The per-(region, SKU) series lock of spec 0010 stays; the Region lock is coarser.

### 4. Rows per entry

A probe hands the rows of each finished entry to `ctx.emit`, and the scheduler writes them at once, 1 object per `simulated` value. A store failure there logs `probe.failed`, and the next entry still runs. Logs stay per event; `probe.done` of a lane call has a `region` field.

### 5. Stop

`ctx.stopping()` turns true when the stop flag is set or `run_forever` ends. Then:

- a lane that waits for its Region lock makes no call;
- the probe starts no new entry (`allocation.stopped`, `odcr.stopped`);
- a search starts no new attempt, and its last row of a real entry has `details.search_stopped` = `"stop"` (a simulated entry gets no such key);
- the attempt in progress ends with its teardown, and its rows are written;
- `run_forever` waits for every lane, so the store log handler closes after the last row.

All lanes drain at the same time, so the drain time is 1 attempt, not a sum: at most a pre-teardown, the probe, and the teardown. The stop timeout becomes **max(180, probe + 2 x teardown + 30)**: 240 s at the defaults (it was 180 s). This covers the 210 s scale set attempt with the 30 s margin. `sync.sh` writes it to the drop-in, as before (ADR 0012).

The Azure search loop must check `ctx.stopping()` before each new attempt, as `aws_odcr` does here (spec 0010, section 5, step 7). Its call-wide deadline is a stop-gap that this decision replaces.

**Amendment, 2026-10-15:** the Azure search deadline is per entry, not per lane call ([spec 0010](../spec/0010-azure-provisioning-probes-spec.md), section 5, step 7). Each real entry gets `probe_timeout_seconds + teardown_timeout_seconds` from its own start, before its precheck. With a deadline per lane call, a Region with 2 or more real cells gave the later cells a `details.search_stopped` = `"deadline"` row in every cycle, so they never got data.

The options were:

| Option | Each cell gets data every cycle? | Change |
|---|---|---|
| A. 1 scheduler call per entry, each with its own deadline | Yes | The scheduler splits a lane into calls. Each call makes its own adapter and `run_id`, and the dedupe of equal entries (spec 0010, decision Z6) no longer sees the other entries, so 2 equal entries give 2 searches and duplicate rows. |
| B. 1 deadline per entry, inside the lane call | Yes | The deadline moves into the entry loop. The lane, the Region lock, the dedupe, and the `run_id` do not change. |
| C. Rotate the entry order per cycle | No: a Region of k slow cells gives each cell data about 1 cycle in k | State across cycles, and each cycle still ends some entries at the deadline. |

B is the smallest change. It gives each entry the same guarantee as option A: section 5 above makes the stop wait for 1 attempt, not for the deadline, so the deadline only bounds the time of 1 entry. The rules of this decision hold: 1 lane at a time per Region (section 3), `ctx.stopping()` before each entry and each attempt, including the first (section 5), the cadence and the overrun of the lanes, and 1 write per finished entry with no duplicate row (section 4). The stop timeout does not change: a stop drains 1 attempt per lane.

Cycle time with B, all 63 Azure entries real and no capacity at any size: an entry ends about 150 s after its start (the deadline, plus at most 1 poll interval of overshoot per loop, spec 0010, section 10), so the slowest Region of 7 entries ends its lane in about 7 x 150 s = 17.5 min. This is under the 35 min bound of the table below and under the 2 h cadence. `prober/tests/azure/provisioning_lanes_test.py` shows it on a virtual clock per Region with the real probes. AWS does not change.

## Numbers

### Cycle time of the full matrix (worst case, 300 s per entry)

| Cloud | Entries per Region | Per Region (1 lane at a time) | Cycle (Regions at once) | In series before |
|---|---|---|---|---|
| Azure | 7 (3 reservation + 4 scale set), plus 7 pre-check reads of 2 calls | 7 x 300 s = 35 min | 35 min | 315 min |
| AWS | 6 | 6 x 300 s = 30 min | 30 min | 90 min |

A Region can hold 24 entries at 300 s before its lane passes the 2 h cadence.

### Peak quota held

`n` = 2 for every cell. The vCPUs per size come from the Azure size name (the number after the family letters is the vCPU count, [Azure VM naming](https://learn.microsoft.com/en-us/azure/virtual-machines/vm-naming-conventions)) and from the AWS specification table ([accelerated computing specifications](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html)).

| Cloud | Largest cell | Peak per Region (any quota of the Region) | Peak at 1 moment, all Regions | GPUs at 1 moment |
|---|---|---|---|---|
| Azure | RTX PRO 6000 (NC144ds), 2 x 144 = 288 vCPUs; H100 (NC40ads) 80; A10 (NV36ads) 72; A100 (NC24ads) 48; T4 (NC4as) 8 | 288 vCPUs, the same as in series | 9 x 288 = 2,592 vCPUs, only when every region is at its RTX PRO 6000 cell at once | 18 (2 per region) |
| AWS | `p5.4xlarge`, 2 x 16 = 32 vCPUs of the P group; `g7e.2xlarge` 2 x 8 = 16 of the G and VT group | 32 vCPUs, the same as in series | 3 x 32 = 96 vCPUs | 6 (2 per Region) |

No quota sees more than it did in series, because every quota is per Region. The instance-hours per cycle do not change (ADR 0018, section 5): the same attempts run, at the same time instead of one after the other.

### API load

Each Region's rate buckets see the load of 1 entry at a time, as before. A lane polls about every `poll_interval_seconds` (5 s). With 9 Azure lanes, the subscription sees about 9 polls per 5 s, each in its own region.

## Consequences

- A provisioning call cannot delay a signal probe. The full matrix fits the 2 h cadence on both clouds.
- A crash loses at most the entry in progress. A stop loses no row.
- `TimeoutStopSec` rises from 180 s to 240 s at the defaults: a restart can take up to 4 min.
- [Spec 0003](../spec/0003-prober-core-spec.md) gets section 6B. [Spec 0010](../spec/0010-azure-provisioning-probes-spec.md), section 5, and [spec 0002](../spec/0002-gpu-capacity-tracker-spec.md), section 4.9, state the rule. Specs 0004 and 0005 state the new stop timeout.
- An AWS search deadline, if one is added later, does not change this: with or without it, a stop ends the search after the attempt in progress.
- Tests: `prober/tests/test_lanes.py` (the full matrix on a virtual clock per Region with real threads, the signal probe on time while a lane is blocked, the stop, the overrun, a store failure), the stop tests in `tests/aws/test_odcr.py` and `tests/azure/allocation_capacity_reservation_test.py`, and the stop timeout checks in `terraform/tests/sync.test.sh`.
