# 0023. The provisioning search goes up after a full success, to a per-entry `n_max`

- Status: accepted
- Date: 2026-10-16
- Amends: [0009](0009-allocation-probes-are-all-or-nothing-with-halving.md) (section 2, the search), [0020](0020-provisioning-probes-run-in-region-lanes-off-the-signal-thread.md) (the entry budget)

## Context

The search of [ADR 0009](0009-allocation-probes-are-all-or-nothing-with-halving.md) answers "Can I get this many now?" for the entry `n`, and it goes down when the answer is no. When the answer is yes at `n`, the search stops. It cannot say how large the count can go. The project wants that number: the largest count that a cloud gives now, up to a fixed cap.

Billing facts, from the primary sources:

- AWS bills a capacity reservation "at per-second granularity, with a minimum of 60 seconds" ([pricing and billing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html)). A create that fails provisions nothing.
- Azure prices a capacity reservation "at the same rate as the underlying VM size" ([overview](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-overview)). The page gives no billing granularity. The billing export must confirm it after the first day ([research/learnings/azure-capacity-reservation-billing-granularity-not-documented.md](../research/learnings/azure-capacity-reservation-billing-granularity-not-documented.md)). Azure VMs bill full minutes ([Linux VM pricing FAQ](https://azure.microsoft.com/en-us/pricing/details/virtual-machines/linux/)).

## Decision

1. **One optional entry key, `n_max`.** It is valid on `aws.provisioning_probes` and on both Azure probe types. The config check requires an integer greater than the entry `n` and at most the hard limit `N_MAX_LIMIT` = 16. It is valid on a simulated entry. There is no cadence key: the upward search runs in every provisioning cycle, for each entry that has `n_max`.
2. **The search.** The first attempt does not change: C = min(`n`, the quota ceiling). The cap is U = min(`n_max`, the quota ceiling). After a full success at C, with C < U, the probe requests min(2 × `n_requested`, U), all or nothing, with a verified teardown between the steps. It stops at the first step that is not a success (no bisection), at U, when the process stops, and at the entry deadline. The AWS probe reuses its Service Quotas ceiling. The Azure probes use the ceiling of the pre-check. An unreadable AWS ceiling leaves U = `n_max`.
3. **Down and up exclude each other.** A cycle that fails at C halves, as ADR 0009 section 2 says, and never goes up. A cycle that succeeds at C goes up and never halves. A success after a halving ends that search as before.
4. **Rows.** Each step is 1 row. `details.search_step` continues from the last step. A row of a step above C also has `details.search_direction` = `"up"` and `details.n_max`. A row of any other step has neither key, and an entry without `n_max` writes the same rows as before.
5. **The Azure entry budget** (amends the entry deadline of ADR 0020). With `n_max`, the budget of an entry is (1 + k) x (`probe_timeout_seconds` + `teardown_timeout_seconds`), with k = ceil(log2(`n_max` / `n`)), the most steps that the doubling can take. For 2 to 16 and the default timeouts, k = 3 and the budget is 600 s. Without `n_max` the budget is 1 x (probe + teardown), as before. The stop timeout does not change: a stop still waits for the attempt in progress only. The longest lane of the reference file is eastus, with 4 reservation entries with `n_max` and 1 scale set entry: 4 x 600 s + 1 x 150 s = 42.5 min, under the 2-hour cadence. The dedupe key of an Azure entry includes `n_max`.
6. **AWS time.** A step takes about 2 s. The worst case of an entry is (1 + k) x (probe + teardown) attempts, with the same k as above: 4 for 2 to 16, and 5 for `n` = 1. A lane stays under the cadence. `aws_odcr` has the same entry budget as the Azure entries: the search stops before an attempt that cannot finish its probe and teardown, with `details.search_stopped` = `"deadline"`.
7. **The dashboard.** `search_result` and `search_ceiling` read the rows that are not upward rows, so "2 of 2" keeps its meaning. The stop mark of a cell (the `!` of the old page of spec 0006, section 6.3; that rule is gone, and the board shows a sweep, spec 0006 section 5.3) reads the rows that are not upward rows, so an upward step that ends in a timeout or a stop does not mark a "2 of 2" cell. A teardown that is not verified on any row still marks it, because a leak must show. The reason an upward step ended shows on the tooltip line below. A new `search_max` is the largest count that succeeded in the whole search, and `search_n_max` is the entry `n_max`. The tooltip shows "max 8 of 16", with "(stopped: timeout)" when an upward step ended early. Both fields are null when the search has no upward row.
8. **Scope.** The reference file sets `n_max: 16` on every `aws_odcr` entry and on every `az_capacity_reservation` entry. The live file does the same for its real entries. No `az_vmss_flex` entry has `n_max`: 16 VMs cannot boot inside the probe timeout, so the steps would be `timeout` rows.

## Options

| Option | Result |
|---|---|
| A. Raise the entry `n` to 16 | A failure halves from 16, so the probe spends its attempts below 16. A cell that rarely fits 2 would still ask for 16 first. |
| B. A separate upward probe on its own cadence | A second schedule, a second lock, and a second set of rows for one question. |
| C. Go up inside the one search (this decision) | One loop, one lock, one run id, and a step that cannot inform is not made. |

## Consequences

- **Cost.** A full success at 2 followed by the steps 4, 8, and 16 holds 4 + 8 + 16 = 28 instance-minutes at the 60 s minimum. A step that fails creates nothing and bills nothing, so an entry that rarely succeeds at 2 adds about nothing. The search stops at the first failure, so a step that cannot inform is never made.
- **A leak is larger.** A leak at `n_requested` = 16 is 8 times a leak at `n_requested` = 2. The search stops at once when a teardown is not verified, and the sweepers clean up.
- **Azure step time can grow with the count.** A step that passes the probe timeout ends the search as a `timeout` row.
- **The AWS ceiling is the quota limit,** not the free vCPUs.
- **Rollout order.** An old prober faults on the unknown key `n_max`. Thus the code ships first, then the live file gets `n_max: 16`, then one apply for each cloud.
- **Follow-up, not built:** a daily spend estimate from the rows (`n_placed` x max(60 s, hold time) x list price).
