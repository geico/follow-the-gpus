# 0009. Allocation probes are all or nothing, a failure halves the count, and the AWS On-Demand probe uses an ODCR only

- Status: accepted
- Date: 2026-10-12

## Context

An allocation probe asks a cloud for a number of GPU VMs or instances now ([ADR 0008](0008-name-things-by-what-they-do-and-cost.md)). [Spec 0002](../spec/0002-gpu-capacity-tracker-spec.md) decision D8 had a binary search on the count. A note said that 1 request for the maximum count, which reads `n_placed`, would likely replace the search. Section 4.5 recorded a short fill as `partial`, with depth = `n_placed`. The reason was the research of section 4.7: a VMSS scale-out gives an outcome for each VM, not for the whole request.

The project rejected partial fills on 2026-10-12: "We want partial requests to fail. We use binary / 2 to see where the limit is." The experiment asks 1 question in both clouds: "Can I get this many now?" A user who asks for a count and gets fewer did not get that count.

Spec 0002 had no AWS allocation probe. AWS has no read-only signal for free On-Demand capacity. A real attempt is the only test ([AWS capacity signals](../research/aws-capacity-signals.md)). The project decided on 2026-10-12: "EC2 Fleet instant is what we want, not ASG." Later the same day, the project limited the On-Demand probe to the On-Demand Capacity Reservation: "keep fleet spot for future, we dont need it now."

## Decision

### 1. All or nothing

1. Each attempt of an allocation probe asks for exactly its requested count (`n_requested`) in 1 request.
2. An attempt is a success only when all `n_requested` are placed. Fewer than `n_requested` is a failure.
3. On a failure, the probe must tear down at once all that it placed. It must not wait for the members that are still pending.
4. The outcome of a short fill comes from the error codes of the part that failed, with the existing table of spec 0002, section 4.5: `no_capacity`, `restricted_or_no_capacity`, `quota`, or `error`. A short fill for lack of capacity is `no_capacity`.
5. An allocation probe must not write the outcome `partial`. The value stays in the schema only for rows written before this ADR.
6. The counts on the row:

   | Field | Value |
   |---|---|
   | `n_requested` | The count that the attempt asks for. |
   | `n_placed` | `n_requested` on `success`. 0 on `no_capacity` and `restricted_or_no_capacity`. This is the result of an all-or-nothing request, a derived value. |
   | `details.n_placed` | The raw count of VMs or instances placed when the probe stopped, before the teardown ([ADR 0006](0006-store-raw-provider-values-normalize-separately.md)). Next to `details.n_failed` and `details.n_pending` where the path has them. |

**Why `no_capacity` and not a new value such as `partial_failed`:**

- The answer to "Can I get this many now?" is no, for the same reason as with 0 placed.
- The core and the heat map rules already know `no_capacity`. The fill ratio `n_placed / n_requested` of 1 attempt stays 0 or 1.
- The row keeps the raw count: `details.n_placed` > 0 marks a short fill, and a query can separate the 2 cases.
- The dashboard must change (section 5, not implemented yet): 1 cycle now writes several rows with the same `run_id`, so the last row of a cell does not show the depth.
- A new value needs changes in the core, the `local` generator, and the dashboard glyphs, and it adds no information.

### 2. A failure halves the count

The search runs for each target in each 2-hour cycle (spec 0002, D2).

1. The ceiling C = min(the entry `n`, the `n_ceiling` from quota) (spec 0002, D8). With C = 0, the probe makes no attempt. Azure writes the existing `quota` row (spec 0010, section 5). AWS has no spec for that row yet (spec 0002, section 4.9).
2. `lo` = 0 is the largest `n_requested` that succeeded. `hi` = none is the smallest `n_requested` that failed.
3. The first attempt asks for `n_requested` = C.
4. After a `success` at `n_requested`: `lo` = `n_requested`. With `hi` = none, stop: C succeeded.
5. After `no_capacity` at `n_requested`: `hi` = `n_requested`.
6. The next `n_requested` = floor((`lo` + `hi`) / 2). Stop when the next `n_requested` equals `lo`. Thus the first failures halve the count, and a later success searches up between `lo` and `hi`.
7. Stop after any other outcome. `quota`, `restricted`, `restricted_or_no_capacity`, `timeout`, and `error` do not tell the probe that a smaller count can succeed.
8. Stop when a teardown is not verified. The probe must not stack new resources on resources that remain.
9. The attempts of 1 target run in series under the (region, SKU) lock (spec 0010, decision Z4). Each teardown ends before the next attempt.
10. Each attempt writes 1 row with the same `run_id` and `details.search_step` (1 for the first attempt).
11. The result of the cycle is `lo`: the largest `n_requested` with `outcome` = `success` for the target and the `run_id`, or 0 when no attempt succeeded and 1 attempt or more was `no_capacity`. When no attempt was `success` or `no_capacity`, for example when the first attempt is `quota`, `error`, or `timeout`, the result is null. This matches spec 0006, section 5.3. It is derived from the rows. No extra row holds it. **Amendment, 2026-10-13:** a row with `details.cleanup` = `true` (the AWS cleanup of spec 0002, section 4.9) is not an attempt and must be excluded from this derivation; its `run_id` is its own, not a target's search, so a cleanup-only `run_id` gives null here, never a false 0.
12. The search assumes that the capacity does not change during the attempts of 1 cycle. A cycle takes minutes, so a change between attempts can give a result that is 1 step wrong.

Example: C = 8, and the cloud has capacity for 5. Attempts: 8 fails, 4 succeeds, 6 fails, 5 succeeds. Then `hi` − `lo` = 1, so the search stops. The result is 5.

**Amendment, 2026-10-16:** an entry with `n_max` goes up after a full success at C, instead of stopping at step 4 (the rule of [ADR 0023](0023-the-provisioning-search-goes-up-after-a-full-success.md)). The halving above is unchanged for a failure at C.

A cycle makes 1 + ceil(log2(C)) attempts or fewer for each target. Examples: C = 8 gives 4 attempts or fewer; C = 32 gives 6 attempts or fewer in each 2-hour cycle.

### 3. The AWS On-Demand probe uses an ODCR only

The AWS allocation probe runs in Phase 2, every 2 hours, as the Azure allocation probes do (spec 0002, D2). It measures On-Demand capacity with an On-Demand Capacity Reservation (ODCR) only. It never launches an instance. The order:

**Amendment, 2026-10-13:** the probe (`probe_type` `aws_odcr`) is implemented, with its cleanup and the IAM. See spec 0002, section 4.9, for the exact `details` keys, the pre-check outcome rows, and the `local`-mode fault-injection steps, none of which this section fixes; and spec 0004, section 4.3, for the IAM. 2 gaps are flagged for review there: the `n_ceiling` from quota (step 1, below) is not read, and the cleanup has no separate scheduled service.

1. **Free signals.** The Spot signals and the Capacity Block offerings run as before (spec 0002, sections 4.1, 4.2, and 4.8).
2. **Pre-check.** Read-only: the type is offered in the Availability Zone (`DescribeInstanceTypeOfferings`), and the On-Demand quota of the family gives the `n_ceiling` ([AWS capacity signals](../research/aws-capacity-signals.md)).
3. **ODCR.** Create 1 reservation for `n_requested` instances of the type in 1 Availability Zone, with `InstanceMatchCriteria` = `targeted` and an `EndDate` a few minutes ahead as a backstop. Read its state. Poll a `pending` state until `active` or `failed`, or until the probe timeout.
4. **Teardown.** Cancel an `active` reservation and verify the state `cancelled`. Spec 0002, section 4.9, has the details.

Outcomes that end the search (section 2, step 7):

- A reservation still `pending` at the probe timeout gives `timeout`. AWS lists the states that you can cancel: `assessing`, `scheduled`, `active`, and `delayed`, not `pending` ([CancelCapacityReservation](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CancelCapacityReservation.html)). Thus the probe keeps polling for a bounded time until the state is `active` or `failed`, then cancels an `active` reservation. The `EndDate` backstop covers a probe crash. A pilot tests a cancel in `pending`.
- An error that says that the type or the zone does not support a reservation, and any other unknown error, gives `error`. No other launch runs.

An ODCR capacity failure (`InsufficientInstanceCapacity`) is the answer "no capacity at this count": the next attempt halves the count.

#### 3.1 ODCR facts

- `CreateCapacityReservation` takes `InstanceCount` from 1 to 1000. A request "could fail if" EC2 "does not have sufficient capacity", and AWS says to "request a smaller Capacity Reservation" ([CreateCapacityReservation](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CreateCapacityReservation.html)). AWS documents no partial reservation (not verified: absence claim). The probe still checks the count: an `active` reservation with `totalInstanceCount` < `n_requested` is a failure, and the probe cancels it at once.
- `InsufficientInstanceCapacity` "can occur if you ... create a new Capacity Reservation" ([error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html)).
- The reservation states include `pending`, "the capacity provisioning is still pending", `active`, and `failed`, which can come from "request parameters that are not valid, capacity constraints, or instance limit constraints" ([CapacityReservation](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityReservation.html)). A `failed` state gives `error` for now. A pilot shows whether a `failed` state for capacity must halve the count.
- "Active and unused Capacity Reservations count toward your On-Demand Instance limits". Reservations in the `assessing`, `scheduled`, `pending`, `active`, and `delayed` states count toward the On-Demand quota ([On-Demand Capacity Reservations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html)). `InstanceLimitExceeded` can occur "if you are creating a Capacity Reservation" ([error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html)).
- The limitations of an ODCR name no GPU instance type. They name Dedicated Hosts, spread and partition placement groups, Windows BYOL, and hibernation, among others ([On-Demand Capacity Reservations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html), Limitations).
- Billing: reservations "are charged at the equivalent On-Demand rate whether you run instances in reserved capacity or not". They are "billed at per-second granularity, with a minimum of 60 seconds" ([pricing and billing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html)).
- `CancelCapacityReservation` "releases the reserved capacity, and changes the Capacity Reservation's state to `cancelled`". An `active` reservation with no commitment duration can be cancelled ([CancelCapacityReservation](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CancelCapacityReservation.html)). An immediate reservation has no term commitment, and you "can cancel it at any time to release the reserved capacity and to stop incurring charges" ([On-Demand Capacity Reservations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html)).
- With `EndDateType` = `limited`, the reservation "is cancelled within an hour from the specified time" ([CreateCapacityReservation](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CreateCapacityReservation.html)). This is a backstop only.

#### 3.2 Not an Auto Scaling group

An Auto Scaling group (ASG) is the wrong vehicle for an allocation probe, now and in the future work of section 6:

- An ASG reconciles toward its desired capacity in the background. A scaling activity records that "an instance was started in response to a difference between desired and actual capacity". Errors appear later, in the description of the scaling activities ([Auto Scaling troubleshooting](https://docs.aws.amazon.com/autoscaling/ec2/userguide/CHAP_Troubleshooting.html)).
- An ASG keeps trying after a capacity failure. For a Spot capacity error, AWS says: "Amazon EC2 Auto Scaling continues to automatically make the launch request until capacity becomes available" ([launch failures](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ts-as-instancelaunchfailure.html)). The reconcile of the first item applies to On-Demand too.
- Thus an ASG reports late, and it can get instances after the probe has recorded a failure. Those instances bill, and the row is wrong.
- Amazon EC2 Auto Scaling itself uses "EC2 Fleet of type *instant* to launch EC2 instances" ([instant fleet](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instant-fleet.html)). A direct instant fleet makes the same launch call with no retry loop, and it gives the answer in the response. Thus a future launch probe uses an instant fleet (section 6).

### 4. Azure

- The capacity reservation path is already all or nothing. Microsoft: "Capacity reservation creation succeeds or fails in its entirety" ([create a capacity reservation](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-create)).
- The VMSS path must make the rule itself. The research facts of spec 0002, section 4.7, stay: a scale-out gives an outcome for each VM, and the scale set does not retry. Those facts are now the reason for the rule, not a source of depth. The probe stops at the first `failed` member, tears down, and writes the row of section 1.

### 5. The dashboard reads the search result

Built. For the allocation probe types, the board shows the search result of the cycle, not the last row. The search result is the largest `n_requested` with `outcome` = `success` among the rows with the same `run_id` and the same target. It is 0 when none succeeded and 1 or more was `no_capacity`, and null when no row was `success` or `no_capacity` (section 2, step 11). The cell also shows C, the largest `n_requested` of the cycle. [Spec 0006](../spec/0006-dashboard-spec.md), section 5.3, states the rule: the board takes the search result of each zone search, and the sweep of an entry and a round combines them.

### 6. Future work, not built: EC2 Fleet `instant`

The project keeps the EC2 Fleet of type `instant` for later. It has 2 future uses:

1. **A Spot allocation probe.** An ODCR reserves On-Demand capacity: AWS charges it "at the equivalent On-Demand rate" ([pricing and billing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html)), and it counts toward the On-Demand quota ([On-Demand Capacity Reservations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html)). Thus a Spot probe needs a launch.
2. **A daily calibration pair** for each type: an ODCR and an instant fleet at the same count, at the same time. It measures how often the 2 answers disagree.

Facts for that work:

- An instant fleet "is a synchronous one-time request that makes only one attempt to launch your desired capacity". "The API response lists the instances that launched, along with errors for those instances that could not be launched" ([instant fleet](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instant-fleet.html)).
- `MinTargetCapacity` for On-Demand: "If this minimum capacity isn't reached, no instances are launched." Maximum value 1000, "Supported only for fleets of type `instant`". "At least one of the following must be specified: `SingleAvailabilityZone` | `SingleInstanceType`" ([OnDemandOptionsRequest](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_OnDemandOptionsRequest.html)). AWS example 7 sets `MinTargetCapacity`, `SingleInstanceType`, and `SingleAvailabilityZone` in the Spot options, and the fleet launches Spot instances "only if it can launch all 20 instances at the same time" ([instant fleet](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instant-fleet.html), example 7).
- The response has `fleetInstanceSet` and `errorSet`, both "Supported only for fleets of type `instant`" ([CreateFleet](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CreateFleet.html)). Each error has an `errorCode` from the EC2 error codes ([CreateFleetError](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CreateFleetError.html)).
- "When you delete an `instant` fleet, Amazon EC2 automatically terminates all its instances" ([instant fleet](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instant-fleet.html)).

With `MinTargetCapacity` = `TotalTargetCapacity` = `n_requested`, AWS documents all or nothing. A future probe must still treat 1 to `n_requested` − 1 instances as a failure and terminate them at once. A launch needs a launch template, an AMI, and a subnet, which the ODCR probe does not need.

## Consequences

- This change edits specs only. Spec 0002: D1, D8, D20, D21 (new), sections 1, 3, 4.1, 4.3, 4.4, 4.5, 4.7, 4.9 (new), 5, 7.2, 9, 11, and 12. Spec 0010: sections 5, 6, 7.2, 7.5, 8.3, 9, and decision Z19 (Azure only). Spec 0003 (section 7.2) gets a note that this ADR supersedes its `partial` rows. Spec 0006 gets the search result rule (sections 5.3, 5.4, and 6.3) and the same note (section 8, item 1).
- The code does not change in this pull request. An implementation change must change: the Azure probes (the search, the stop at the first failed member, the `partial` step of `local` mode); the core `local` generator of [spec 0003](../spec/0003-prober-core-spec.md), section 7.2, which still writes `partial` rows; and [spec 0006](../spec/0006-dashboard-spec.md), sections 5.3, 5.4, and 6.3, the search result of section 5. The dashboard keeps reading `partial` in old rows. **Amendment, 2026-10-13:** the Azure halving search and the `partial`-to-all-or-nothing change above are still not implemented; only the AWS allocation probe below is.
- The AWS allocation probe needs its own spec before the build: the probe types, the IAM actions in the AWS stack ([spec 0004](../spec/0004-aws-stack-spec.md)), and an AWS cleanup of tagged reservations. An ODCR launches no instance, so it needs no launch template, AMI, or subnet. The AWS stack has no sweeper today, because the AWS signal probes create nothing. **Amendment, 2026-10-13:** done; spec 0002, section 4.9, and spec 0004, section 4.3.
- Cost: a cycle makes up to 1 + ceil(log2(C)) attempts for each target, for example 6 for C = 32. Each success bills. An ODCR bills for 60 seconds or more at the On-Demand rate.
- Quota: the AWS allocation probe needs On-Demand quota for each GPU family, because reservations count toward it. That request has not been made. The AWS Spot quota request of D20 has not been made either.
