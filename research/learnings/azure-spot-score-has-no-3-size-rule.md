---
title: "The Azure Spot Placement Score has no 3-size rule"
date: 2026-10-11
---

# The Azure Spot Placement Score has no 3-size rule

## What the docs say

Microsoft's how-to for the Spot Placement Score API says a request
takes up to 5 VM sizes, up to 8 regions, a desired VM count, and a
list of availability zones
([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
The docs state no minimum number of sizes.

## What we saw

The REST reference's own sample request scores 1 VM size,
`Standard_D2_v2`, alone, and returns a score for each zone
([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)).
This repo's research note compares this directly against AWS, which
documents a rule that fewer than 3 instance types gives "always low"
([research/azure-spot-placement-score-per-vm-size.md](../azure-spot-placement-score-per-vm-size.md),
and
[research/aws-spot-placement-score-per-gpu-type.md](../aws-spot-placement-score-per-gpu-type.md)).
Azure also returns 1 score per VM size: the API gives "the spot
placement score for sku/region/zone combination"
([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)).
AWS returns 1 score for the whole set of instance types in a request
([How Spot placement score works](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-sps-works.html)).

## Example

Request: `POST
.../providers/Microsoft.Compute/locations/{location}/placementScores/spot/generate`
with `desiredSizes` = `[{"sku": "Standard_D2_v2"}]`, 1 size.

Answer, from the REST reference sample: a `PlacementScore` item for
each zone, each with its own `score` field. No "low score" penalty
applies for using 1 size.

## Why it matters

A design that groups GPU VM sizes to avoid an AWS-style 3-size penalty
does not need that step on Azure. Score each Azure GPU VM size on its
own, in 1 request of up to 5 sizes, and read each size's score
straight from the response.

## Sources

- [Spot Placement Score how-to (Microsoft Learn)](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)
- [Spot Placement Scores - Post, REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)
- [AWS: How Spot placement score works](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-sps-works.html)
- [research/azure-spot-placement-score-per-vm-size.md](../azure-spot-placement-score-per-vm-size.md)
- [research/aws-spot-placement-score-per-gpu-type.md](../aws-spot-placement-score-per-gpu-type.md)
