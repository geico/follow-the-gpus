---
title: "AWS interruptible Capacity Reservations"
date: 2026-10-12
---

# AWS interruptible Capacity Reservations

## Summary

- An interruptible Capacity Reservation lets the owner of an On-Demand Capacity Reservation (ODCR) lend unused instances to other teams. The owner can take them back at any time. The instances of the borrower are then terminated.
- The interruption notice is 2 minutes. AWS says so in 3 documentation pages and in the blog post, quoted in the section "Interruption".
- An account without a reservation shared with it cannot find or use this capacity. This review found no marketplace and no public describe API in the pages read.
- It is not a capacity signal for this project. It is not an allocation probe either, because the capacity belongs to an owner who chose to share it.
- Recommendation: build no probe. Record it in the probe catalogue of ADR 0010 as "not probeable: only an owner's shared capacity". [ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md) does not change.

## Testing status

No live test ran. No AWS API was called. All statements come from public AWS documentation and one AWS blog post. A follow-up can test the API with a real ODCR if a use for it appears.

## What it is

- Interruptible Capacity Reservations "help you make unused capacity temporarily available for other workloads within your account", and "when you reclaim capacity, any instances running inside the interruptible reservation are terminated" ([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/interruptible-capacity-reservations.html)).
- The owner picks a number of unused instances in a source ODCR. AWS "transfer[s] these instances from your source reservation to the new interruptible reservation" in the same account. AWS "retain[s] the association between the source reservation and the interruptible Capacity Reservation" ([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/interruptible-capacity-reservations.html)).
- The owner can then share the interruptible reservation with AWS Resource Access Manager (AWS RAM) ([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/interruptible-capacity-reservations.html)).
- AWS announced the feature on 24 November 2025. It is "available at no additional cost to all Capacity Reservations customers" ([announcement](https://aws.amazon.com/about-aws/whats-new/2025/11/amazon-ec2-interruptible-capacity-reservations/)).

## Owner and consumer

| Item | Capacity owner | Capacity consumer |
|---|---|---|
| Who | "You own the source Capacity Reservation and create the interruptible Capacity Reservation" | "You launch instances into shared interruptible reservations" |
| Creates the reservation | Yes, with `CreateInterruptibleCapacityReservationAllocation` | No |
| Can launch into it | Yes. The owner can "view, launch, tag, share, and assign billing" | Yes, if the owner shared it with the account |
| Can change or cancel it | Only through the source reservation | No |

Sources for the table: [User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/interruptible-capacity-reservations.html) (roles and considerations), [owner procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-owner-procedures.html), [consumer procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-consumer-procedures.html).

### Owner rules

- The source ODCR must be "in active state with no end date set". It must have unused capacity ([owner procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-owner-procedures.html)).
- "You can create only one interruptible allocation per source Capacity Reservation" ([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/interruptible-capacity-reservations.html)).
- The owner can allocate "a maximum of 1000 instances at once". A change is also limited to 1000 instances at once ([owner procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-owner-procedures.html)).
- "You cannot directly modify or cancel an interruptible Capacity Reservation." The owner changes the allocation on the source reservation. A target count of 0 cancels it ([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/interruptible-capacity-reservations.html), [owner procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-owner-procedures.html)).
- A zero size preference of `retain` keeps the reservation active at 0 instances. The default value `default` cancels it ([CreateInterruptibleCapacityReservationAllocation](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CreateInterruptibleCapacityReservationAllocation.html)).

### Consumer rules

- Interruptible reservations "are by default targeted Capacity Reservations". The consumer must name the reservation ID in the launch. They do not "automatically cover matching instances" ([consumer procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-consumer-procedures.html)).
- The launch sets the market type `interruptible-capacity-reservation` ([consumer procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-consumer-procedures.html)).
- A consumer "can't add interruptible Capacity Reservations to capacity reservation groups" ([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/interruptible-capacity-reservations.html)).
- There is "no fallback to On-Demand or Spot" ([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/interruptible-capacity-reservations.html)).
- A consumer can use an Auto Scaling group. The launch template sets the market type and the reservation ID ([Auto Scaling guide](https://docs.aws.amazon.com/autoscaling/ec2/userguide/capacity-reservation-create-asg-procedure.html)).

### Sharing

- The owner shares through AWS RAM ([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/interruptible-capacity-reservations.html)).
- The blog post says the owner can share with account IDs, or with an organization or organizational unit ([blog post](https://aws.amazon.com/blogs/compute/optimize-unused-capacity-with-amazon-ec2-interruptible-capacity-reservations/)).
- The owner page says: "You can share interruptible reservations only within your AWS organization using AWS Resource Access Manager (RAM)" ([owner procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-owner-procedures.html)).
- The page for standard shared reservations lists "Specific AWS accounts inside or outside of its AWS organization". The owner page for interruptible reservations says "only within your AWS organization". The AWS pages disagree on accounts outside the organization. The user guide says the owner can "share it with additional AWS accounts or your AWS organization" ([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/interruptible-capacity-reservations.html)). The blog says "in your organization" ([blog post](https://aws.amazon.com/blogs/compute/optimize-unused-capacity-with-amazon-ec2-interruptible-capacity-reservations/)). The announcement says "within your organization" ([announcement](https://aws.amazon.com/about-aws/whats-new/2025/11/amazon-ec2-interruptible-capacity-reservations/)). This note treats the owner page as the rule for interruptible reservations ([shared Capacity Reservations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservation-sharing.html)).
- If a consumer account leaves the organization, AWS unshares the reservation from it. "Any instances running in the unshared reservation are eventually terminated" ([owner procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-owner-procedures.html)).
- Sharing to an organization needs "sharing with AWS Organizations" enabled. "Capacity Reservation sharing is not available to new AWS accounts or AWS accounts that have a limited billing history" ([shared Capacity Reservations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservation-sharing.html)).
- The consumer must launch in the Availability Zone that maps to the same AZ ID as the owner's zone ([shared Capacity Reservations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservation-sharing.html)).

## Billing

- "When you create an interruptible reservation, you're billed for it as an independent new reservation." The source reservation bills "total capacity minus allocated capacity". The interruptible reservation bills the allocated capacity ([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/interruptible-capacity-reservations.html)).
- Owner: the owner pays the On-Demand rate for any unused instance in the interruptible reservation ([blog post](https://aws.amazon.com/blogs/compute/optimize-unused-capacity-with-amazon-ec2-interruptible-capacity-reservations/)). Standard reservations bill at "the equivalent On-Demand rate whether you run instances in reserved capacity or not" ([pricing and billing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html)).
- Consumer: the consumer pays the On-Demand rate for the instances that it launches and runs. The blog example is 30 instances for 4 hours, which is 120 instance-hours ([blog post](https://aws.amazon.com/blogs/compute/optimize-unused-capacity-with-amazon-ec2-interruptible-capacity-reservations/)).
- For a standard shared reservation, "consumers are billed for the instances that they run inside the shared Capacity Reservation". The owner can assign the billing of the available capacity to one consumer account ([shared Capacity Reservations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservation-sharing.html)). The interruptible pages say that "all other sharing functionality works the same as standard Capacity Reservations", and the owner can "assign billing" ([owner procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-owner-procedures.html), [User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/interruptible-capacity-reservations.html)).
- There is no fee for the feature itself ([announcement](https://aws.amazon.com/about-aws/whats-new/2025/11/amazon-ec2-interruptible-capacity-reservations/)).
- The source ODCR counts toward the On-Demand quota of the owner. Consumer instances in a shared reservation also count toward the quota of the owner ([shared Capacity Reservations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservation-sharing.html)).

## Interruption

The notice time is 2 minutes. The AWS text says:

- "When capacity is reclaimed by the owner, you receive an interruption notice 2 minutes before instance termination." ([consumer procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-consumer-procedures.html))
- "Running instances receive a 2-minute interruption warning through EventBridge events." ([owner procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-owner-procedures.html))
- "you receive this notification 2 minutes before your instances are terminated" ([monitoring page](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/monitor-interruptible-cr.html))
- "consuming instances receive a 2-minute instance interruption warning notice through Amazon EventBridge" ([blog post](https://aws.amazon.com/blogs/compute/optimize-unused-capacity-with-amazon-ec2-interruptible-capacity-reservations/)).

What happens to the instances:

- After the notice period, the running instances "enter a shutting down state and get terminated" ([owner procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-owner-procedures.html)).
- The action is `terminate`. The event has the field `instance-action` ([monitoring page](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/monitor-interruptible-cr.html)). The documents name no stop or hibernate option.
- When an owner reduces the allocation, "we first reclaim available instances, then running instances". If unused instances meet the count, "no termination occurs" ([owner procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-owner-procedures.html)).
- The owner can reclaim "at any time" ([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/interruptible-capacity-reservations.html)). The documents give no minimum run time and no limit on how often.
- "Complete reclamation can take a few minutes depending on instance type and shutdown time." After it, the reclaimed instances "become available in your source Capacity Reservation for immediate use" ([owner procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-owner-procedures.html)).
- The notice is the same length as the Spot notice, "two minutes before Amazon EC2 stops or terminates your Spot Instance" ([Spot interruption notices](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-instance-termination-notices.html)). The Spot page says that Spot notices "are emitted on a best effort basis". The interruptible pages make no such statement.

Events and logs:

| Event | Who gets it | Source |
|---|---|---|
| EventBridge `EC2 Capacity Reservation Instance Interruption Warning`. The detail has `instance-id`, `instance-action`, `instance-termination-time`, and `azId`. | The account that runs the instance | [monitoring page](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/monitor-interruptible-cr.html) |
| EventBridge `EC2 Interruptible Capacity Reservation Allocation Reclamation Completed`. The detail has `sourceCapacityReservationId`, `instanceType`, `availabilityZoneId`, `TotalInstanceCount`, `ReclaimedInstanceCount`, and `targetInstanceCount`. | The owner of the source reservation | [monitoring page](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/monitor-interruptible-cr.html) |
| CloudTrail `InterruptibleCapacityReservationCreated`, `InterruptibleCapacityReservationAllocationUpdated`, `InterruptibleCapacityReservationCancelled`, `CapacityReservationModified`, and `InterruptibleCapacityReservationInstancesTerminated` | Account that calls | [monitoring page](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/monitor-interruptible-cr.html) |

The owner can also poll `DescribeCapacityReservations`. The `interruptibleCapacityAllocation` object has `instance-count`, `target-instance-count`, and `status`. The status is `updating` during reclamation and `active` when it ends ([owner procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-owner-procedures.html)).

## Instance types, regions, and limits

- Instance types: no page that this review read lists the supported instance types. The user guide, the owner and consumer pages, the API pages, and the announcement name none. The blog example uses `r7i.4xlarge`, which is not a GPU type ([blog post](https://aws.amazon.com/blogs/compute/optimize-unused-capacity-with-amazon-ec2-interruptible-capacity-reservations/)). The Auto Scaling page uses `p5.48xlarge` in a launch template example for a Capacity Block ([Auto Scaling guide](https://docs.aws.amazon.com/autoscaling/ec2/userguide/capacity-reservation-create-asg-procedure.html)). It is an example, not a support statement.
- Regions: the announcement lists none. It says: "Refer to the AWS Capabilities by Region website for the feature's regional availability" ([announcement](https://aws.amazon.com/about-aws/whats-new/2025/11/amazon-ec2-interruptible-capacity-reservations/)). This review did not read that site. The regions are unknown.
- Source reservation: shared tenancy only, in `active` or `scheduled` state ([shared Capacity Reservations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservation-sharing.html)). The interruptible source must be `active` with no end date ([owner procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-owner-procedures.html)).
- Limits: 1 allocation for each source reservation. At most 1000 instances for each create or change ([owner procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-owner-procedures.html)).
- Auto Scaling: no mixed instances policy and no warm pool with launch-template targeting ([Auto Scaling guide](https://docs.aws.amazon.com/autoscaling/ec2/userguide/capacity-reservation-create-asg-procedure.html)).
- Interruption type: the API has one value, `adhoc` ([InterruptibleCapacityAllocation](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_InterruptibleCapacityAllocation.html)).

## APIs

| API | Use | Source |
|---|---|---|
| `CreateInterruptibleCapacityReservationAllocation` | Owner. Takes `CapacityReservationId`, `InstanceCount`, optional `ZeroSizePreference`, `ClientToken`, `DryRun`, and tags. Returns `interruptionType`, `sourceCapacityReservationId`, `status`, and `targetInstanceCount`. | [API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CreateInterruptibleCapacityReservationAllocation.html) |
| `UpdateInterruptibleCapacityReservationAllocation` | Owner. Takes `CapacityReservationId`, `TargetInstanceCount`, and `ZeroSizePreference`. A lower number reclaims capacity. | [API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_UpdateInterruptibleCapacityReservationAllocation.html) |
| `DescribeCapacityReservations` | Owner and consumer. The owner reads the `interruptibleCapacityAllocation` object. The consumer looks for `Interruptible` = `true`. | [owner procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-owner-procedures.html), [consumer procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-consumer-procedures.html) |
| `RunInstances` with `MarketType` = `interruptible-capacity-reservation` and a reservation ID | Consumer launch | [consumer procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-consumer-procedures.html) |

The allocation status values are `pending`, `active`, `updating`, `canceling`, `canceled`, and `failed` ([InterruptibleCapacityAllocation](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_InterruptibleCapacityAllocation.html)).

## Can an account without its own reservation see or use it?

Short answer: no, except through a share from the owner.

- A consumer finds interruptible reservations with `DescribeCapacityReservations`, "in your account" ([consumer procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-consumer-procedures.html)). The call "describe[s] only the Capacity Reservations in the AWS Region that you're currently using" and has an `owner-id` filter ([DescribeCapacityReservations](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityReservations.html)). Thus the call shows a reservation that the owner shared with the account.
- The blog says consumers "can now find the shared interruptible capacity in their account". The owner can "specify which consumer account IDs in your organization will get access to the interruptible ODCR. Alternatively, you can share the ODCR with your entire AWS Organization or Organizational Unit (OU)." ([blog post](https://aws.amazon.com/blogs/compute/optimize-unused-capacity-with-amazon-ec2-interruptible-capacity-reservations/)).
- The documents that this review read describe no marketplace, no public listing, and no API that lists the interruptible capacity of other accounts. This is an absence claim. It is not verified beyond those pages.
- A consumer sees only the total and the available capacity of a shared reservation ([shared Capacity Reservations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservation-sharing.html)).
- The capacity is not AWS spare capacity. It is the idle part of a reservation that an owner pays for. Spot is the product for spare AWS capacity ([Spot Instances](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-instances.html)).

Consequence for this project: the signal exists only for an account that an owner chose to share with. It says nothing about the free GPU capacity of AWS. It is not a capacity signal.

## Comparison

| | ODCR | Interruptible CR | Capacity Block | Spot | On-Demand launch |
|---|---|---|---|---|---|
| Who supplies the capacity | AWS reserves it for the account | An owner account, from its own ODCR | AWS reserves it for the account | AWS spare capacity | AWS, if it has capacity at the time |
| Price | On-Demand rate, used or not | Owner: On-Demand rate for unused instances. Consumer: On-Demand rate for instances it runs. | Set at purchase, paid up front | The Spot price | Static hourly price |
| Interruption | None by AWS | Owner reclaims at any time. 2-minute notice, then terminate. | None until the block ends. Termination begins 30 minutes before the end for instance types. | EC2 can interrupt. 2-minute notice. | You decide |
| Cancel | Immediate-use reservation: at any time | Owner only, through the source reservation | "Capacity Block cancellations aren't allowed" | You terminate the instance | You terminate the instance |
| Minimum length | No commitment for immediate use. Billed per second, 60 seconds minimum. | No minimum stated in the pages read. Billed as a new reservation. | 1 to 14 days, or a multiple of 7 days up to 182 days | No minimum stated in the pages read | No minimum stated in the pages read |
| Probe for this project | Allocation (planned) | None | Signal (offerings). Never allocate. | Signal (Spot placement score, price) | Allocation by a launch is not planned. The ODCR is the On-Demand probe. |

Sources for the table:

- ODCR: [On-Demand Capacity Reservations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html), [pricing and billing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html).
- Interruptible: the sections above.
- Capacity Block: [Capacity Blocks](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html), [how they work](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-how.html), [pricing and billing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-pricing-billing.html).
- Spot: [Spot Instances](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-instances.html), [interruption notices](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-instance-termination-notices.html). For Spot, "Your Spot Instances run until you terminate them, capacity is no longer available, or ..." ([Spot Instances](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-instances.html)).
- On-Demand: the comparison table of [Spot Instances](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-instances.html) says that without capacity "you get an insufficient capacity error (ICE)", that the price is static, and that "You determine when an On-Demand Instance is interrupted".
- Probe column: [AWS capacity signals per instance type](aws-capacity-signals-per-instance-type.md), [AWS capacity signals](aws-capacity-signals.md), and [ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md).

## Recommendation

1. Build no probe now.
2. Add this line to the probe catalogue of ADR 0010: "Interruptible Capacity Reservation (AWS): not probeable. It is only an owner's shared capacity." ADR 0010 is not on the main branch yet, so this note does not edit it. The line goes into the ADR when it merges, or in a follow-up.
3. Reasons:
   - An account that has no share sees no interruptible capacity. A probe has nothing to read.
   - A probe that creates the capacity needs a source ODCR first. That test is the ODCR probe of ADR 0009. The interruptible step adds a second owner call and no new information about AWS capacity.
   - The reservation can hold only unused instances of an ODCR that the account already created. The capacity is no larger than the ODCR.
4. When the feature is relevant: an organization that shares its idle ODCRs between its own accounts. The consumers then run interruptible work in the idle capacity. A future research note can check this case. It is a way to use capacity. It is not a way to measure it.
5. ADR 0009 does not change. The AWS On-Demand probe still uses an ODCR only. An interruptible allocation is a second API call on top of an `active` ODCR, and the probe does not need it. The probe never launches an instance, so it never consumes an interruptible reservation.

## Open questions

- Which instance types and regions support the feature? No page that this review read says.
- Can an owner share an interruptible reservation with an account outside its organization? The AWS pages disagree (section "Sharing").
- Is the 2-minute notice a best-effort notice? The interruptible pages do not say. The Spot page does.

## Sources

- <https://aws.amazon.com/blogs/compute/optimize-unused-capacity-with-amazon-ec2-interruptible-capacity-reservations/>
- <https://aws.amazon.com/about-aws/whats-new/2025/11/amazon-ec2-interruptible-capacity-reservations/>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/interruptible-capacity-reservations.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-owner-procedures.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-consumer-procedures.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/monitor-interruptible-cr.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CreateInterruptibleCapacityReservationAllocation.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_UpdateInterruptibleCapacityReservationAllocation.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_InterruptibleCapacityAllocation.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityReservations.html>
- <https://docs.aws.amazon.com/autoscaling/ec2/userguide/capacity-reservation-create-asg-procedure.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservation-sharing.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-how.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-pricing-billing.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-instances.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-instance-termination-notices.html>
