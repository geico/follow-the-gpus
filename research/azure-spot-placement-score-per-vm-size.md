---
title: "Azure Spot Placement Score per VM size"
date: 2026-10-11
---

# Azure Spot Placement Score per VM size

## Summary

The Azure Spot Placement Score gives 1 score for each VM size in each region or zone. The scale has 3 levels: High, Medium, and Low. One request can score up to 5 VM sizes in up to 8 regions. The docs show no minimum number of sizes. The REST sample scores 1 size. Thus, Azure gives a score per GPU VM size without a workaround. AWS gives 1 score for a mix of instance types.

A preview API, SKU Mix Placement Scores, scores a mix of VM sizes in 1 region. It returns placement choices with a score from 0 to 9, and a capacity limit for each VM size and zone. It covers Spot and Regular VMs.

This note extends the Spot Placement Score section of [Azure capacity signals](azure-capacity-signals.md). The AWS counterpart is [AWS Spot placement score per GPU instance type](aws-spot-placement-score-per-gpu-type.md).

## Inputs and limits

- **Inputs.** A request has 4 inputs: the number of desired Spot VMs, up to 5 VM sizes, up to 8 regions, and availability zones ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
- **Limits are in the how-to only.** The REST reference and the TypeSpec model give no maximum item count for `desiredSizes` or `desiredLocations` ([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05), [models.tsp](https://github.com/Azure/azure-rest-api-specs/blob/65eb9311d304dcf9874d05fdec39ea65baae8db8/specification/compute/resource-manager/Microsoft.Compute/Recommender/models.tsp#L26-L50)).
- **No minimum number of sizes.** The REST sample request sends 1 size, `Standard_D2_v2`, and gets a score for each zone ([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)). The docs do not state a rule like the AWS 3-type rule ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
- **Endpoint.** `POST https://management.azure.com/subscriptions/{subscriptionId}/providers/Microsoft.Compute/locations/{location}/placementScores/spot/generate?api-version=2025-06-05` ([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)).
- **Request body.** The body has `desiredLocations`, `desiredSizes` (a list of `{"sku": ...}`), `desiredCount`, and `availabilityZones` ([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)).
- **Count.** `desiredCount` is the "Desired instance count per region/zone based on the scope". The default is 1 ([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)).
- **Region default.** If `desiredLocations` is not set, the API uses the region in the request URI ([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)).
- **Scope.** `availabilityZones` sets a zonal or a regional scope. The how-to says that the feature supports both scopes ([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05), [how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
- **Versions.** The how-to names 2025-06-05 as the supported version. The spec repo keeps 2025-06-05 under `stable/` ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score), [main.tsp](https://github.com/Azure/azure-rest-api-specs/blob/65eb9311d304dcf9874d05fdec39ea65baae8db8/specification/compute/resource-manager/Microsoft.Compute/Recommender/main.tsp#L40-L54)). The spec also lists 3 preview versions: 2026-05-05-preview, 2026-09-05-preview, and 2026-10-05-preview ([main.tsp](https://github.com/Azure/azure-rest-api-specs/blob/65eb9311d304dcf9874d05fdec39ea65baae8db8/specification/compute/resource-manager/Microsoft.Compute/Recommender/main.tsp#L40-L54)).
- **Other clients.** The Azure CLI command is `az compute-recommender spot-placement-score`, marked Core and GA ([CLI reference](https://learn.microsoft.com/en-us/cli/azure/compute-recommender)). The how-to page names it `spot-placement-recommender`, which does not match the CLI reference ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)). The PowerShell cmdlet is `Invoke-AzSpotPlacementScore` in Az.Compute ([PowerShell reference](https://learn.microsoft.com/en-us/powershell/module/az.compute/invoke-azspotplacementscore)).
- **Portal.** The portal shows the score in the Spot tab when you create a Virtual Machine Scale Set ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
- **Cost and regions.** The feature has no cost. It is available in all public Azure regions ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
- **Call budget.** The API "internally calls other GET APIs and is part of your GET call quota" ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
- **Caching.** Microsoft recommends a cache for each combination of subscription, count, region, zone, and VM size. The suggested cache TTL is 15 to 30 minutes ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
- **Errors.** The how-to lists status codes 200, 400, 429 (rate limit), and 500 ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).

## Output: 1 score per VM size, per region or zone

- **Unit of the score.** The `PlacementScore` object is "The spot placement score for sku/region/zone combination" ([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)).
- **Fields in 2025-06-05.** Each item has `sku`, `region`, `availabilityZone`, `isQuotaAvailable`, and `score` ([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)).
- **Score levels.** The how-to gives 3 levels: High, Medium, and Low ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)). In 2025-06-05, `score` is a plain string, not an enum ([models.tsp](https://github.com/Azure/azure-rest-api-specs/blob/65eb9311d304dcf9874d05fdec39ea65baae8db8/specification/compute/resource-manager/Microsoft.Compute/Recommender/models.tsp#L150-L155)).
- **Other values.** The how-to describes "Restricted SKU" and "Data Not Found" results ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)). The REST sample returns `DataNotFoundOrStale` for 1 zone ([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)). The PowerShell examples return `RestrictedSkuNotAvailable` ([PowerShell reference](https://learn.microsoft.com/en-us/powershell/module/az.compute/invoke-azspotplacementscore)).
- **Restricted SKU.** This value means that the Spot VM size is not available for the subscription ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
- **Data Not Found.** This value means that the data for a score is missing, or that it is older than the service accepts as fresh ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
- **Row count.** A zonal request gives 1 row per size, region, and zone. A regional request gives 1 row per size and region. The how-to examples show both shapes ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
- **What the score measures.** The scores come from "analyses of Spot capacity allocation probability" for the given count, in each region and VM size combination ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)). The REST field description adds "the expected lifetimes of the Spot VM(s) after allocation" ([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)).

### Changes in 2026-10-05-preview (preview)

- **Score enum.** `score` becomes the enum `PlacementScoreValue`. Its values are `High`, `Medium`, `Low`, `RestrictedSkuNotAvailable`, `InsufficientQuota`, and `DataNotFoundOrStale` ([REST 2026-10-05-preview](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)).
- **New field.** `placementPreference` is an integer "from 0 to 10 (inclusive). Higher is better." ([REST 2026-10-05-preview](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)). The docs do not say how it relates to `score`.
- **Removed field.** The preview removes `isQuotaAvailable`. The preview also adds the score value `InsufficientQuota`. The docs do not say that it replaces `isQuotaAvailable` ([models.tsp](https://github.com/Azure/azure-rest-api-specs/blob/65eb9311d304dcf9874d05fdec39ea65baae8db8/specification/compute/resource-manager/Microsoft.Compute/Recommender/models.tsp#L150-L170)).
- **Required sizes.** `desiredSizes` becomes a required field ([models.tsp](https://github.com/Azure/azure-rest-api-specs/blob/65eb9311d304dcf9874d05fdec39ea65baae8db8/specification/compute/resource-manager/Microsoft.Compute/Recommender/models.tsp#L33-L39)).
- **Non-Spot counterpart.** The same preview adds Compute Placement Scores for regular VMs at `.../placementScores/regular/generate`. It uses the same request and response shape ([REST Compute Placement Scores](https://learn.microsoft.com/en-us/rest/api/recommenderrp/compute-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview), [models.tsp](https://github.com/Azure/azure-rest-api-specs/blob/65eb9311d304dcf9874d05fdec39ea65baae8db8/specification/compute/resource-manager/Microsoft.Compute/Recommender/models.tsp#L94-L104)).

## Required role

- **Role.** In its REST API section, the how-to says that you must add the RBAC role "Compute Recommendations Role" for the subscription that runs the API ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
- **Gap.** The Compute built-in roles page did not show a role with that name on 2026-10-11 ([Compute built-in roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/compute)). The exact role definition is not verified here.
- **Permission.** The Compute operations list has `Microsoft.Compute/locations/placementScores/generate/action`, "Create a request for generating Placement Scores" ([Compute permissions](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/compute)).

## What the score does and does not guarantee

- **A recommendation only.** A high score "doesn't guarantee that the Spot request will be fully or partially fulfilled" ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
- **No eviction guarantee.** "A score of High or Medium doesn't guarantee allocation success or no evictions" ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
- **Point in time.** A score is valid only at the time of the request ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
- **Exact configuration.** A score applies only to a Spot request with the same count, VM size, location, and zone ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
- **Other constraints.** The score ignores other constraints, for example the scale set `SinglePlacementGroup` setting ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
- **Quota.** You must check or request Spot VM quota separately ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)). Spot VMs use a separate quota pool ([Spot VMs](https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms)).
- **Spot exclusions.** B-series sizes and promo versions of any size do not support Spot ([Spot VMs](https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms)). The Spot VMs page lists no other excluded sizes.

## The preview mix API: SKU Mix Placement Scores (preview)

- **Status.** The API exists only in preview versions. The spec adds it in 2026-05-05-preview ([SkuMixPlacementBase.tsp](https://github.com/Azure/azure-rest-api-specs/blob/65eb9311d304dcf9874d05fdec39ea65baae8db8/specification/compute/resource-manager/Microsoft.Compute/Recommender/SkuMixPlacementBase.tsp#L18-L48)). The REST reference documents 2026-10-05-preview ([REST SKU Mix](https://learn.microsoft.com/en-us/rest/api/recommenderrp/sku-mix-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)).
- **Endpoint.** `POST .../providers/Microsoft.Compute/locations/{location}/skuMixPlacementScores/recommendations/generate?api-version=2026-10-05-preview` ([REST SKU Mix](https://learn.microsoft.com/en-us/rest/api/recommenderrp/sku-mix-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)).
- **One region per call.** The request has `zones`, `capacityProfile`, and `instanceDescription`. It has no list of regions. The region comes from the URI ([REST SKU Mix](https://learn.microsoft.com/en-us/rest/api/recommenderrp/sku-mix-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)).
- **Sizes.** `instanceDescription.vmSizes` is the list of VM sizes. Each size can have a `rank` for the `Prioritized` strategy ([REST SKU Mix](https://learn.microsoft.com/en-us/rest/api/recommenderrp/sku-mix-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)). The reference gives no maximum number of sizes.
- **Capacity.** `capacity` is 1 to 10,000 for VMs, or 1 to 100,000 for vCPUs. `capacityType` is `VM` or `VCpu` ([REST SKU Mix](https://learn.microsoft.com/en-us/rest/api/recommenderrp/sku-mix-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)).
- **Priority.** `priority` is `Regular` or `Spot` ([REST SKU Mix](https://learn.microsoft.com/en-us/rest/api/recommenderrp/sku-mix-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)).
- **Allocation strategy.** The values are `LowestPrice`, `Prioritized`, and `EvictionOptimized`. `EvictionOptimized` is for Spot only. `LowestPrice` needs `osType` ([REST SKU Mix](https://learn.microsoft.com/en-us/rest/api/recommenderrp/sku-mix-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)).
- **Zones.** If `zones` is empty, the request is regional. The zone distribution strategies are `BestEffortBalanced` (default), `Prioritized`, and `BestEffortSingleZone` ([REST SKU Mix](https://learn.microsoft.com/en-us/rest/api/recommenderrp/sku-mix-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)).
- **Spot price cap.** `spotPriorityProfile.maxPricePerVm` sets a maximum price. The default is -1, no price limit ([REST SKU Mix](https://learn.microsoft.com/en-us/rest/api/recommenderrp/sku-mix-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)).
- **Placement choices.** The response has a list of `placementChoices`. Each choice has a `score` "from 0 to 9 (inclusive). Higher is better." and a `skuSplit` ([REST SKU Mix](https://learn.microsoft.com/en-us/rest/api/recommenderrp/sku-mix-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)).
- **Split.** Each `skuSplit` item gives a VM size, a priority, a zone, and a `capacity`. The `capacity` is the "Lower range of recommended allocation capacity" ([REST SKU Mix](https://learn.microsoft.com/en-us/rest/api/recommenderrp/sku-mix-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)).
- **Capacity limits per size.** `capacityLimits` has 1 entry "for each requested (VM size, zone) combination, independent of the recommended placement". The API returns `capacityLimits` only for requests that describe instances by VM sizes ([REST SKU Mix](https://learn.microsoft.com/en-us/rest/api/recommenderrp/sku-mix-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)). The spec adds it in 2026-09-05-preview ([skuMixPlacementModels.tsp](https://github.com/Azure/azure-rest-api-specs/blob/65eb9311d304dcf9874d05fdec39ea65baae8db8/specification/compute/resource-manager/Microsoft.Compute/Recommender/skuMixPlacementModels.tsp#L227-L234)).
- **Limit value.** `limit` is a count of VMs. It is the smallest of the requested capacity, the available capacity, and the available quota, but never below the capacity already recommended for that size and zone. It is 0 when nothing is available ([REST SKU Mix](https://learn.microsoft.com/en-us/rest/api/recommenderrp/sku-mix-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)).
- **Limit reason.** `reason` is `None`, `InsufficientCapacity`, `InsufficientQuota`, or `SkuNotAvailable`. `InsufficientQuota` wins when both capacity and quota are short ([REST SKU Mix](https://learn.microsoft.com/en-us/rest/api/recommenderrp/sku-mix-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)).
- **Other fields.** The response also has `partialFulfillmentReason`, a response `id`, and `validUntil`. After `validUntil`, Microsoft tells callers to get fresh recommendations ([REST SKU Mix](https://learn.microsoft.com/en-us/rest/api/recommenderrp/sku-mix-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)).
- **Preview churn.** The 2026-09-05-preview version removed the per-choice `id` and the `capacityMax` field ([skuMixPlacementModels.tsp](https://github.com/Azure/azure-rest-api-specs/blob/65eb9311d304dcf9874d05fdec39ea65baae8db8/specification/compute/resource-manager/Microsoft.Compute/Recommender/skuMixPlacementModels.tsp#L238-L272)). The shape changes between preview versions.
- **No guarantee text.** The REST reference has no conceptual page and no statement about guarantees. This review found no how-to page for this API.

## Comparison with the AWS Spot placement score

| Item | Azure Spot Placement Score | Azure SKU Mix (preview) | AWS Spot placement score |
|---|---|---|---|
| Unit of the score | 1 VM size in 1 region or zone | 1 placement choice for a mix, in 1 region | 1 mix of types in 1 Region or AZ |
| Scale | High, Medium, Low | 0 to 9 | 1 to 10 |
| Sizes or types per call | Up to 5 | No documented maximum | Up to 1000 |
| Minimum types | None documented | None documented | 3, else the score is "always low" |
| Regions per call | Up to 8 | 1 | Up to 10 |
| Per-size detail | Yes, the score itself | Yes, `capacityLimits` | No |
| Capacity units | VM count | VMs or vCPUs | Instances, vCPUs, or memory |

Sources for the AWS column: the AWS API reference gives the 1 to 10 scale, the top 10 Regions or AZs, up to 10 Region names, up to 1000 instance types, and the "always be low" rule for 1 or 2 types ([AWS API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html)). It also lists the units `vcpu`, `memory-mib`, and `units` ([AWS API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html)). The AWS score is 1 number for the whole set of types ([How Spot placement score works](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-sps-works.html)). Public data shows that AWS now scores most single GPU types alone. The AWS docs do not state this. Only an AWS sample README says it, for P-family types ([AWS note](aws-spot-placement-score-per-gpu-type.md)).

Both clouds say that a score does not guarantee fulfillment ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score), [AWS API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html)).

## Use for a per-GPU-size heat map

- One Azure call scores up to 5 GPU sizes in up to 8 regions. Each size gets its own score. No grouping is necessary.
- To compare with an AWS row for a group of types, take the best Azure score in the group. This is a method choice, not a documented equivalence.
- Use the same count on both clouds: Azure `desiredCount` equal to AWS `TargetCapacity` in instances.
- Treat `DataNotFoundOrStale` and `RestrictedSkuNotAvailable` as "not scored", not as Low.
- Do not build on the SKU Mix API yet. It is a preview, and its fields changed between preview versions.

## Gaps

- The built-in definition of "Compute Recommendations Role" is not verified.
- The relation between `placementPreference` (0 to 10) and `score` is not documented.
- The docs do not give a call rate limit beyond the GET quota and status 429.
- The docs do not say which GPU sizes support Spot in which regions. A test call with `RestrictedSkuNotAvailable` as a result is the practical check.
- The SKU Mix API has no documented maximum number of VM sizes.

## Sources

- [Spot Placement Score how-to (Microsoft Learn)](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)
- [Spot Placement Scores - Post, REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)
- [Spot Placement Scores - Post, REST 2026-10-05-preview](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)
- [Compute Placement Scores - Post, REST 2026-10-05-preview](https://learn.microsoft.com/en-us/rest/api/recommenderrp/compute-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)
- [Sku Mix Placement Scores - Post, REST 2026-10-05-preview](https://learn.microsoft.com/en-us/rest/api/recommenderrp/sku-mix-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)
- [az compute-recommender (CLI reference)](https://learn.microsoft.com/en-us/cli/azure/compute-recommender)
- [Invoke-AzSpotPlacementScore (PowerShell reference)](https://learn.microsoft.com/en-us/powershell/module/az.compute/invoke-azspotplacementscore)
- [Azure permissions for Compute](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/compute)
- [Azure built-in roles for Compute](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/compute)
- [About Azure Spot Virtual Machines](https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms)
- [azure-rest-api-specs, Recommender TypeSpec, commit 65eb931](https://github.com/Azure/azure-rest-api-specs/tree/65eb9311d304dcf9874d05fdec39ea65baae8db8/specification/compute/resource-manager/Microsoft.Compute/Recommender)
- [AWS GetSpotPlacementScores API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html)
- [AWS: How Spot placement score works](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-sps-works.html)
