---
title: "Pair a single type with a same-class mix, to test for the Spot score penalty"
date: 2026-10-13
---

# Pair a single type with a same-class mix, to test for the Spot score penalty

## What the docs say

AWS states a rule for the number of instance types in a
`GetSpotPlacementScores` request: fewer than 3 types "will always be
low" ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html)).
The docs do not say how a caller can tell a real low-capacity score
apart from this fewer-than-3-types penalty, for a given GPU type.

## What we saw

This project's request list pairs a GPU type with a mix, to test whether
that type's single-type score carries the fewer-than-3-types penalty.
The request list pairs only the A10G class (`g5`), for a 24-hour test: each
of 3 A10G sizes alone, and a 3-size mix of the same class
([spec/0008-aws-probes-spec.md, section 4.3](../../spec/0008-aws-probes-spec.md)).
The request list also keeps a separate, lasting CPU single and CPU trio
control. That pair checks the 3-type rule itself (does a CPU
single stay at 3 or lower, does a CPU mix score above 3). It does not
test the capacity of 1 CPU type, with one exception: the trio check is
null, not false, when the single shows real `m5` shortage in the same
Region and run
([spec/0002-gpu-capacity-tracker-spec.md, section 4.2](../../spec/0002-gpu-capacity-tracker-spec.md),
[spec/0008-aws-probes-spec.md, section 4.3](../../spec/0008-aws-probes-spec.md),
[learning](aws-spot-score-cpu-trio-low-score-can-be-real-shortage.md)).

A single reading tells a caller only 1 number. A pair gives a caller
more to compare: if the mix also scores low, capacity looks tight
for the whole class. If the mix scores high while the single stays
low, the gap fits 2 explanations, and a caller needs more than 1
reading to tell them apart (see "Example" below).

This repo's research note on the AWS Spot placement score recommends
a GPU mix control that "contains a single-type row"
([research/aws-spot-placement-score-per-gpu-type.md](../aws-spot-placement-score-per-gpu-type.md)).
That note's own evidence used a mix of 3 different GPU classes
(A10G, T4, and L4), not 3 sizes of 1 class. This project's spec
instead builds the mix from 3 sizes of the same class as the single
type it tests, so a reader can tell which class the mix score
belongs to.

## Example

Observed in one set of live calls in 3 US Regions (us-east-1, us-east-2,
us-west-2):

| Request | Score, 3 Regions |
|---|---|
| `g5.xlarge` alone | 2, 9, 1 |
| `g5.xlarge`, `g5.2xlarge`, `g5.4xlarge` (1 class, 3 sizes) | 8, 9, 5 |
| `m5.4xlarge` alone | 3, 3, 3 |
| `m5.2xlarge`, `m5.4xlarge`, `m5.8xlarge` (1 class, 3 sizes) | 9, 9, 9 |

In us-east-1, the `g5.xlarge` single scored 2 while the `g5` family
mix scored 8. The gap fits 2 different explanations: the single type
can carry the fewer-than-3-types penalty, or the larger sizes in the
mix can have more real capacity than `g5.xlarge` alone. A 24-hour test can decide between the 2 explanations. In this
project's test, the 3-size mix did not score higher often enough to
show a single-type penalty.

## Why it matters

Reading a GPU type alone, with no paired mix of the same class,
cannot tell a reader whether a low score means low capacity or means
the request had fewer than 3 types. To test 1 GPU type for the
penalty, pair its single-type reading with a mix of the same class.
The pair narrows the question to 2 explanations instead of many, and
a dedicated test over time can then decide between them.

## Sources

- [AWS API reference: `GetSpotPlacementScores`](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html)
- [research/aws-spot-placement-score-per-gpu-type.md](../aws-spot-placement-score-per-gpu-type.md)
- [spec/0002-gpu-capacity-tracker-spec.md, section 4.2](../../spec/0002-gpu-capacity-tracker-spec.md)
- [spec/0008-aws-probes-spec.md, section 4.3](../../spec/0008-aws-probes-spec.md)
