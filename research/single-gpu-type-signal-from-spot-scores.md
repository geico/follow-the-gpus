---
title: "Limits for a single GPU type signal from Spot score requests"
date: 2026-10-12
---

# Limits for a single GPU type signal from Spot score requests

## Summary

This note collects the public limits that bound a request design for the Spot score APIs when you want the most signal for 1 GPU type. It uses score, price, and offering APIs only. It launches and reserves no instance.

- **AWS.** The binding limit is the configuration limit, not the request rate. AWS gives no number. See the [configuration limit note](aws-spot-placement-score-configuration-limit.md).
- **Azure.** The binding limit is an undocumented 429 limit. See the [Azure rate-limit note](azure-spot-placement-score-rate-limit.md).
- **Capacity Blocks.** On an account without Capacity Block access, every call returns `CapacityBlockDescribeLimitExceeded`.
- Each limit that depends on the account (the configuration limit, the target capacity limit, SKU restrictions, and Capacity Block access) must be checked on the account that runs the experiment.

Scope: commercial regions in the continental US, GPU types only.

The request budget is checked against the [rate-limit note](api-rate-limits-of-signal-probes.md).

## Limits that bound the designs

### AWS `GetSpotPlacementScores`

- **Configuration limit.** "We can limit the number of new request configurations within a 24-hour period if we detect patterns not associated with the intended use of the Spot placement score feature. If you reach the limit, you can retry the request configurations that you've already used, but you can't specify new request configurations until the next 24-hour period." ([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html))
- **Error.** `MaxConfigLimitExceededException`: "You can retry configurations that you used within the last 24 hours, or wait for 24 hours before specifying a new configuration." ([error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html))
- **Size of the limit.** AWS gives no number. The AWS tracker sample says "a few (10) diversified configurations" ([README](https://github.com/aws-solutions-library-samples/guidance-for-ec2-spot-placement-score-tracker/blob/433c22eebc26f85a84bea05cae46282ab3476888/README.md)). The 2022 SpotLake paper reports "a maximum of 50 unique queries in 24 hours" ([SpotLake paper](https://arxiv.org/abs/2202.02973)). The [configuration limit note](aws-spot-placement-score-configuration-limit.md) collects more reports.
- **Key of a configuration.** AWS does not say which fields key the configuration limit. The how-it-works page names the fields of "the Spot placement score configuration" as target capacity, target capacity unit, and instance types or attributes, for a matching Spot request. It does not name `RegionNames` or `SingleAvailabilityZone` ([How Spot placement score works](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-sps-works.html)). The SpotLake paper says "the combination of regions, instance types, and the number of desired instances" ([SpotLake paper](https://arxiv.org/abs/2202.02973)). That definition does not name `SingleAvailabilityZone` or the unit type.
- **Target capacity limit.** "If you have no recent Spot usage, we provide you with a low default limit aligned with your Spot request limit." ([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html)). The error is `TargetCapacityLimitExceededException` ([error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html)). AWS gives no number.
- **Request rate.** The throttling page does not name `GetSpotPlacementScores`. The default bucket covers "The Describe\*, List\*, Search\*, and Get\* API actions that are not included in another category": 100 tokens, refill 20 per second. EC2 throttles "for each AWS account on a per-Region basis" ([throttling](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/throttling.html)). Inference: this bucket applies to the call.
- **Top 10.** The response holds "the top 10 Regions or Availability Zones" ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html)).

### Azure Spot Placement Score

- **Size of a request.** Up to 5 VM sizes and 8 regions ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
- **Cache.** Microsoft recommends a cache for each combination of subscription, count, region, zone, and size, for "a minimum of 15 minutes and a maximum of 30 minutes" ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)). Thus 15 minutes is the shortest cadence that the guidance permits.
- **Quota.** The API "internally calls other GET APIs and is part of your GET call quota". Status 429 means "hitting a rate limit" ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)). Microsoft gives no number. The [Azure rate-limit note](azure-spot-placement-score-rate-limit.md) found no public number.
- **ARM buckets.** Azure Resource Manager uses a token bucket for each region. Subscription writes: 200 tokens, refill 10 per second. Subscription reads: 250 tokens, refill 25 per second ([ARM throttling](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/request-limits-and-throttling)).
- **Reported call cap.** SpotLake caps its calls at 10 for each subscription and URI region in a rolling 62 minutes (`CALL_LIMIT_PER_PAIR = 10`, `CALL_HISTORY_MINUTES = 62`) ([sps_location_manager.py](https://github.com/ddps-lab/spotlake/blob/fd1388c3adb066a07db2d2fbf81aff2c0fd09141/collector/spot-dataset/azure/batch/sps_module/sps_location_manager.py#L18-L19)). Microsoft does not state this limit.

### AWS `DescribeCapacityBlockOfferings`

- **Request rate.** Its own bucket: 10 tokens, refill 0.15 per second ([throttling](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/throttling.html)).
- **Account limit.** `CapacityBlockDescribeLimitExceeded`: "You've reached the limit for this account. The returned message provides details." ([error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html)). This is a separate code from the rate error `RequestLimitExceeded`.

## Sources

- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/throttling.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-purchase.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-pricing-billing.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html>
- <https://aws.amazon.com/blogs/compute/optimizing-amazon-ec2-spot-instances-with-spot-placement-scores/>
- <https://github.com/aws-solutions-library-samples/guidance-for-ec2-spot-placement-score-tracker>
- <https://github.com/aws-samples/sample-capacity-block-offering-search>
- <https://github.com/ddps-lab/spotlake>
- <https://arxiv.org/abs/2202.02973>
- <https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score>
- <https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05>
- <https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/request-limits-and-throttling>
