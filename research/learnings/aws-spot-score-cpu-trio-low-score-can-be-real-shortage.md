---
title: "A low score for a 3-type CPU request can be real Spot shortage"
date: 2026-10-27
---

# A low score for a 3-type CPU request can be real Spot shortage

## What the docs say

AWS states that a request of fewer than 3 instance types "will always be
low" ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html)).
The docs imply that a request of 3 or more types is free of this
penalty. The docs do not say that a 3-type request can still score low
when the Region is short of capacity.

## What we saw

This project asks for a CPU trio (`m5.2xlarge`, `m5.4xlarge`, `m5.8xlarge`)
and for `m5.4xlarge` alone, in 3 US Regions, to check the 3-type rule.
The window is several days.

## Example

Observed scores of the CPU trio over several days:

| Region | CPU trio score |
|---|---|
| us-east-1 | 9 in every run |
| us-west-2 | 9 in every run |
| us-east-2 | often below 4, mostly at 1 |

In us-east-2 the low scores came in multi-hour episodes on weekdays.
The single `m5.4xlarge` also scored 1 in the same Region and period.
The score was real Spot availability data, not a fault of the 3-type
rule.

## Why it matters

A trio that scores low does not prove that the 3-type rule applies. If
the single of the same Region also scores 1, the Region is short of
that capacity. This project stores the trio check as null, not false,
in that case ([spec/0008-aws-probes-spec.md, section 4.3](../../spec/0008-aws-probes-spec.md)).

## Sources

- [AWS API reference: `GetSpotPlacementScores`](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html)
- [spec/0008-aws-probes-spec.md, section 4.3](../../spec/0008-aws-probes-spec.md)
