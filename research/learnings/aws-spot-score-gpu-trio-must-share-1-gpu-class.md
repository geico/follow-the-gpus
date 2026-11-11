---
title: "A Spot placement score mix control must use 1 GPU class, not 3"
date: 2026-10-13
---

# A Spot placement score mix control must use 1 GPU class, not 3

## What the docs say

AWS states that a Spot placement score "is calculated based on the
target capacity, the composition of the instance types, the current
Spot usage trends, and the time of the request", for the whole set of
instance types in 1 request
([How Spot placement score works](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-sps-works.html)).
The docs do not say how to design a control that checks whether a
single-type score carries a penalty compared to a multi-type score of
the same GPUs.

## What we saw

This project first used a control trio of 3 different GPU classes,
`g5.xlarge`, `g4dn.xlarge`, and `g6.xlarge` (A10G, T4, and L4). That
mix scores capacity across 3 GPU classes at once. A single-GPU
heat-map row cannot read a usable comparison from it: a high mix
score can come from any of the 3 classes, not the 1 class that a
reader cares about. This repo's research note on the Spot placement
score covers single-type scores per GPU class
([research/aws-spot-placement-score-per-gpu-type.md](../aws-spot-placement-score-per-gpu-type.md)),
but it does not cover a mix control built from 1 class alone.

This repo's spec replaced that control with a mix of 3 sizes of 1 GPU
class instead, `g5.xlarge`, `g5.2xlarge`, and `g5.4xlarge`, all A10G.
It added a rule that compares this mix against each single A10G size
in the same Region: "`g5.xlarge`, `g5.2xlarge`, and `g5.4xlarge` alone
... must not score above the mix"
([spec/0002-gpu-capacity-tracker-spec.md](../../spec/0002-gpu-capacity-tracker-spec.md),
section 4.2). The rule is coded as `single_not_above_mix`
([spec/0008-aws-probes-spec.md](../../spec/0008-aws-probes-spec.md),
section 4.3). The spec also records the decision rule for a 24-hour
test built on this control: if the 3-size mix of 1 class beats the
best single size of that class by 2 or more, in more than 90% of
(Region, run) pairs, a single-type score carries a penalty, and the
request list moves to per-class mixes with the singles kept
([spec/0002-gpu-capacity-tracker-spec.md](../../spec/0002-gpu-capacity-tracker-spec.md),
section 4.2).

## Example

Spot score requests, from the repo config
([config/targets.yaml](../../config/targets.yaml),
`aws.spot_score_requests`). See also
[spec/0002-gpu-capacity-tracker-spec.md](../../spec/0002-gpu-capacity-tracker-spec.md),
section 4.2:

| Entry | Instance types | What it checks |
|---|---|---|
| `g5` | `g5.xlarge` | A10G alone, size 1 |
| `g5-family` | `g5.xlarge`, `g5.2xlarge`, `g5.4xlarge` | The 3-size mix of 1 GPU class, A10G |
| `g5-2x` | `g5.2xlarge` | A10G alone, size 2 (dropped) |
| `g5-4x` | `g5.4xlarge` | A10G alone, size 4 (dropped) |

Rule: each single-size A10G row (`g5`, `g5-2x`, `g5-4x`) must not
score above the `g5-family` row, in the same Region, on the same run.

## Why it matters

A control built from 3 different GPU classes cannot show whether a
single-type score of 1 class carries a penalty, because a high mix
score can come from a different class than the one you are testing.
Build the mix control from 3 sizes of the same GPU class that the
single-type rows test.

## Sources

- [AWS: How Spot placement score works](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-sps-works.html)
- [config/targets.yaml](../../config/targets.yaml)
- [spec/0002-gpu-capacity-tracker-spec.md, section 4.2](../../spec/0002-gpu-capacity-tracker-spec.md)
- [spec/0008-aws-probes-spec.md, section 4.3](../../spec/0008-aws-probes-spec.md)
- [research/aws-spot-placement-score-per-gpu-type.md](../aws-spot-placement-score-per-gpu-type.md)
