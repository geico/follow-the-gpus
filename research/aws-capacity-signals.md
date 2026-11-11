---
title: "AWS GPU Capacity Signal — Can Capacity Be Detected Before Allocation?"
date: 2026-09-25
---

# AWS GPU Capacity Signal — Can Capacity Be Detected Before Allocation?

## Testing status

**No live testing.** This note is documentation research only. No test ran against a live account. A follow-up must run the section "What the documentation shows" as a checklist with real credentials. The numeric values (Spot placement scores, quota values, and Capacity Block prices) all need a test.

## Findings

The core finding: **AWS gives 3 separate signals that you can read: the catalog, the quota, and the price. AWS gives no direct signal for free On-Demand capacity.** A launch attempt, which can fail with an insufficient capacity error (ICE), is the only true test of On-Demand capacity at launch time.

| Mechanism | What it tells you | Live or stale | Dimension | Read-only? |
|---|---|---|---|---|
| `describe-instance-type-offerings` | Instance type is *sold* in this region/AZ (catalog fact) | Catalog fact. AWS does not document how often offerings change. | Catalog | Yes |
| `service-quotas get-service-quota` / CloudWatch usage | Your account's vCPU ceiling for "Running On-Demand P instances" / "G and VT instances", and current usage | Quota near-static; usage metrics collected every minute via CloudWatch | Quota | Yes |
| `get-spot-placement-scores` | 1-10 likelihood score that a **Spot** request of given size will succeed, for the top 10 regions or AZs. Needs at least 3 instance types. | Applies at the time of the request. A recommendation only, with no guarantee of capacity or interruption risk. | Capacity (Spot only) | Yes, but no on-demand equivalent was found — `OnDemandMaxPricePercentageOverLowestPrice` is a price filter, not a capacity score |
| `describe-capacity-block-offerings` | What Capacity Block inventory (P5/P5e/P5en/P4de/P6-B200/B300 etc.) AWS is willing to sell you, for what future window, at what price | Live inventory of a specific forward-looking product, not a signal about immediate ad-hoc on-demand launches | Capacity (pre-committed product) | Yes, no commitment until you call `purchase-capacity-block` |
| `describe-capacity-reservations` | ODCRs and Capacity Blocks in your account, plus reservations shared with you, and their state/instance-count | Live | Reservation state, not general capacity | Yes |
| **Launching an instance (or InsufficientInstanceCapacity)** | Ground truth: did AWS have capacity at that instant | Instantaneous, but mutating (creates a resource) | Physical capacity | **No — this is the only true signal and it is not read-only** |

## What the documentation shows

No live AWS CLI test ran. A documentation review took its place.

- **ICE semantics**: `InsufficientInstanceCapacity` means that AWS does not currently have enough available On-Demand capacity to fulfill the request. The error can also occur when you create or modify a Capacity Reservation, or when you launch into a cluster placement group. It is separate from quota errors. For vCPU-based On-Demand quotas (such as P, and G and VT), EC2 returns `VcpuLimitExceeded`. For instance-count limits, it returns `InstanceLimitExceeded`. Quotas apply per account and per Region. You can raise them through Service Quotas. This confirms the assumption: quota and capacity are separate, and only the quota is easy to read directly.
  ([EC2 launch troubleshooting](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/troubleshooting-launch.html),
  [EC2 error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html))
- **Karpenter's ICE handling**: On an ICE error, Karpenter's AWS provider adds the (capacity type, instance type, zone) offering to its unavailable-offerings cache. The key can also include a reservation ID or a placement group. Karpenter skips cached offerings until the entry expires. The entry expires after **3 minutes**. `UnavailableOfferingsTTL` is a fixed constant, and Karpenter v1.15 has no setting to change it. A cleanup runs every 10 s, so an offering becomes available again soon after its entry expires. This pattern reacts to failures. It does not predict them.
  ([unavailableofferings.go](https://github.com/aws/karpenter-provider-aws/blob/main/pkg/cache/unavailableofferings.go),
  [cache.go](https://github.com/aws/karpenter-provider-aws/blob/main/pkg/cache/cache.go),
  [cache package docs](https://pkg.go.dev/github.com/aws/karpenter-provider-aws/pkg/cache),
  [Karpenter settings](https://karpenter.sh/docs/reference/settings/))
- **Cross-region fallback**: Each Karpenter installation runs in 1 cluster and takes 1 required cluster name ([Karpenter FAQ](https://karpenter.sh/docs/faq/), [Karpenter settings](https://karpenter.sh/docs/reference/settings/)). By default, Karpenter uses the instance types in the Region and AZs of its cluster ([EKS best practices](https://docs.aws.amazon.com/eks/latest/best-practices/karpenter.html)). Karpenter has no cross-Region fallback (not verified: no source states it). Thus a search across clouds or Regions needs an external control loop above Karpenter, 1 for each cluster ([cast.ai analysis](https://cast.ai/blog/karpenter-limitations/), secondary).
  Some GitHub reports describe fallback problems after ICE. None is open, and none confirms a general bug. [#6168](https://github.com/aws/karpenter-provider-aws/issues/6168) (2024, Spot, NodePool weights) was closed as not planned, with no root cause. In [#8885](https://github.com/aws/karpenter-provider-aws/issues/8885), a maintainer found that fallback did occur, but the request then hit the On-Demand vCPU quota. [#8909](https://github.com/aws/karpenter-provider-aws/issues/8909) was a stuck-NodeClaim regression in v1.8.x with single-zone subnets. Karpenter pull request 9023 fixed it.
- **Spot Placement Scores**: `GetSpotPlacementScores` returns a score from 1 to 10 for the top 10 Regions, or for AZs when `SingleAvailabilityZone=true`. The score estimates how likely a **Spot** request of the given target capacity is to succeed. The docs say that you must specify at least 3 instance types, and that the score is always low with 1 or 2 types. The score applies at the time of the request. It is a recommendation only, with no guarantee of capacity or of interruption risk. The parameter `OnDemandMaxPricePercentageOverLowestPrice` is a *price* filter for mixed fleets, not a capacity signal, and the docs say that this API does not support it. The docs cover Spot only. This research found no On-Demand placement score (not verified: absence claim).
  ([API docs](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html),
  [user guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html),
  [InstanceRequirementsRequest](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_InstanceRequirementsRequest.html),
  [boto3 docs](https://docs.aws.amazon.com/boto3/latest/reference/services/ec2/client/get_spot_placement_scores.html))
- **EC2 Fleet "instant" type as a probe**: An EC2 Fleet of type `instant` launches instances synchronously. It is a real allocation attempt, not a dry run. Spot Fleet does not use `instant`. An instant fleet can use capacity-optimized strategies for Spot. These strategies pick the Spot pools with the highest capacity availability. For On-Demand, the only strategies are `lowest-price` and `prioritized`, so AWS has no capacity-based placement for On-Demand. To use an instant fleet as a probe, you must launch real instances and then delete them. Thus it is not read-only. It is also not a pre-check: it is the allocation attempt.
  ([instant fleet](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instant-fleet.html),
  [SpotFleetRequestConfigData](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_SpotFleetRequestConfigData.html),
  [OnDemandOptionsRequest](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_OnDemandOptionsRequest.html))
- **Capacity Blocks for ML**: This is a separate product that you buy ahead of time. It is not general On-Demand capacity. Supported types include P5, P5e, P5en, P4de, P6-B200, and P6-B300. Reservations last 1 to 14 days in 1-day steps, then up to 182 days in 7-day steps. The start time can be up to 8 weeks ahead. Offerings can start in as soon as 30 minutes. News reports say that prices rose about 15% in January 2026 and about 20% in July 2026 (reported: the AWS pricing page shows no price history). An AWS spokesperson said that the January change "reflects the supply/demand patterns we expect this quarter" (reported). `describe-capacity-block-offerings` is read-only, and it shows future windows and prices with no commitment. A purchase is a separate `purchase-capacity-block` call, paid up front. This is the closest AWS API to a "check capacity before you commit" call. But it covers only the Capacity Block product (scheduled and prepaid), not ad hoc On-Demand launches.
  ([AWS docs](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html),
  [purchase docs](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-purchase.html),
  [Spheron blog](https://www.spheron.network/blog/aws-capacity-blocks-pricing-2026-b200-b300-hike/),
  [Network World](https://www.networkworld.com/article/4113150/aws-hikes-prices-for-ec2-capacity-blocks-amid-soaring-gpu-demand.html))
- **ODCR + Karpenter native support**: Karpenter supports `capacityReservationSelectorTerms`. It uses the selected On-Demand Capacity Reservations (ODCRs) first, then falls back to Spot or On-Demand. ODCR support arrived in v1.3, and v1.6 added Capacity Blocks. The feature is beta, behind the `ReservedCapacity` feature gate. This uses capacity that you already own. It does not find new capacity. `describe-capacity-reservations` shows reservations in your account and reservations that other accounts share with you through AWS RAM. It shows no general AWS capacity.
  ([karpenter-provider-aws odcr design doc](https://github.com/aws/karpenter-provider-aws/blob/main/designs/odcr.md),
  [Karpenter docs](https://karpenter.sh/docs/tasks/odcrs/),
  [DescribeCapacityReservations](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityReservations.html),
  [capacity reservation sharing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservation-sharing.html))
- **No new 2025-2026 capacity-availability API found** (not verified: absence claim). A search for a general On-Demand capacity API found nothing beyond Capacity Blocks and Spot Placement Scores. AWS plans to deploy an additional 2 million NVIDIA GPUs in 2027-2028 (press release, 2026-08-26). At NVIDIA GTC 2026, AWS announced plans for more than 1 million NVIDIA GPUs starting in 2026. Neither announcement is a new query API.
  ([press release](https://press.aboutamazon.com/aws/2026/8/aws-and-nvidia-to-deliver-2-million-additional-gpus-and-next-generation-infrastructure-for-agentic-and-physical-ai))

## Assumptions tested

- **HOLDS**: Quota and capacity are separate. For On-Demand, you can read the quota (Service Quotas and CloudWatch usage), but you cannot read the capacity.
- **HOLDS**: `describe-instance-type-offerings` is a catalog fact ("this type is offered in this AZ"). It is not a live capacity signal.
- **PARTLY HOLDS**: Karpenter keeps a reactive cache in each cluster. The cache key is (capacity type, instance type, zone), and an entry expires after 3 minutes. Whether Karpenter can fall back across Regions is not verified.
- **DOES NOT HOLD**: The docs give no supported way to use Spot Placement Scores as a proxy for On-Demand capacity. The closest On-Demand parameter is a price filter, not a capacity estimate. A rule such as "a low Spot score means tight On-Demand capacity" is an invented heuristic, and AWS does not support it.
- **CENTRAL ASSUMPTION — DOES NOT HOLD**: "You can detect availability before an allocation attempt" is **not true for On-Demand capacity**. The read-only pre-check APIs (offerings, quota, and Capacity Block offerings) each answer a different, narrower question: the catalog, the account limit, or the stock of a separate prepaid product. None of them tells you whether an On-Demand RunInstances call will succeed now. **An ICE result from a real attempt is the only reliable signal for On-Demand capacity.** Karpenter itself is built around that fact.

## Open questions

- Test with real credentials whether `get-spot-placement-scores` values correlate with later On-Demand ICE rates, for a GPU instance type in the same AZ and time window. AWS does not document such a correlation, but a small logged experiment costs little. The docs say that the score request must name at least 3 instance types. Public data shows that single GPU types now get a full score; see [AWS Spot placement score per GPU instance type](aws-spot-placement-score-per-gpu-type.md). Run the test from a test account, not production.
- It is not known whether AWS has an unlisted or beta "capacity insight" API for GPUs. Check the AWS "What's New" feed, and ask AWS Support.
- Find the API throttle limits of `GetSpotPlacementScores` for continuous polling, for example every 60 s across several Regions and types. This pass did not find them. The feature has no additional charge, and AWS can limit new request configurations within a 24-hour period ([user guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html)). Read the API throttle docs before you design the poller.
- Capacity Block offerings can start in as soon as 30 minutes ([AWS docs](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html)). Does that make `describe-capacity-block-offerings` a near-real-time capacity check for P5-class types, and not only a scheduled product?
- A future-dated Capacity Reservation can go to state `unsupported` because of capacity constraints ([CapacityReservation](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityReservation.html)). That is a capacity signal, but it needs a reservation request, so it is not read-only. Can a poller use it for GPU instance types?

## Sources

- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/troubleshooting-launch.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-on-demand-instances.html>
- <https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Usage-Metrics.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeInstanceTypeOfferings.html>
- <https://github.com/aws/karpenter-provider-aws/blob/main/pkg/cache/unavailableofferings.go>
- <https://github.com/aws/karpenter-provider-aws/blob/main/pkg/cache/cache.go>
- <https://pkg.go.dev/github.com/aws/karpenter-provider-aws/pkg/cache>
- <https://karpenter.sh/docs/reference/settings/>
- <https://karpenter.sh/docs/faq/>
- <https://docs.aws.amazon.com/eks/latest/best-practices/karpenter.html>
- <https://github.com/aws/karpenter-provider-aws/issues/6168>
- <https://github.com/aws/karpenter-provider-aws/issues/8885>
- <https://github.com/aws/karpenter-provider-aws/issues/8909>
- <https://cast.ai/blog/karpenter-limitations/>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_InstanceRequirementsRequest.html>
- <https://docs.aws.amazon.com/boto3/latest/reference/services/ec2/client/get_spot_placement_scores.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instant-fleet.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_SpotFleetRequestConfigData.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_OnDemandOptionsRequest.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-purchase.html>
- <https://www.spheron.network/blog/aws-capacity-blocks-pricing-2026-b200-b300-hike/>
- <https://www.networkworld.com/article/4113150/aws-hikes-prices-for-ec2-capacity-blocks-amid-soaring-gpu-demand.html>
- <https://github.com/aws/karpenter-provider-aws/blob/main/designs/odcr.md>
- <https://karpenter.sh/docs/tasks/odcrs/>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityReservations.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservation-sharing.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityReservation.html>
- <https://press.aboutamazon.com/aws/2026/8/aws-and-nvidia-to-deliver-2-million-additional-gpus-and-next-generation-infrastructure-for-agentic-and-physical-ai>
