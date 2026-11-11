---
title: "A single GPU instance type can score 9 on the AWS Spot placement score"
date: 2026-10-11
---

# A single GPU instance type can score 9 on the AWS Spot placement score

## What the docs say

The AWS User Guide states a rule for the `GetSpotPlacementScores` API:
"If you specify instance types, you must specify at least three
different instance types, otherwise Amazon EC2 will return a low Spot
placement score."
([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html))
The API reference, the CLI reference, and the Python SDK state the
same rule, with no exception for any instance type
([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html),
[CLI reference](https://docs.aws.amazon.com/cli/latest/reference/ec2/get-spot-placement-scores.html),
[boto3 reference](https://docs.aws.amazon.com/boto3/latest/reference/services/ec2/client/get_spot_placement_scores.html)).

## What we saw

Public data shows that AWS now gives most GPU, Inferentia, and
Trainium instance types a score on their own, above the "low" cap of
3. A public snapshot of the SpotLake dataset from 2026-10-11, which
sends 1 instance type per request, had 49,526 rows. Rows for
accelerated types reached a score of 9. Rows for every other type
stayed at 3 or lower. This repo's research note has the full count and
the list of 20 accelerated instance families that score above 3
([research/aws-spot-placement-score-per-gpu-type.md](../aws-spot-placement-score-per-gpu-type.md)).
Only 1 AWS source states the exception, an AWS sample README, and it
names only the P family, not the G, Inferentia, or Trainium families
that this project's evidence also covers.

## Example

Request: `GetSpotPlacementScores` with `InstanceTypes` =
`["p5.48xlarge"]` alone, `SingleAvailabilityZone` = `true` (the
SpotLake collector always sets this field to `true`, as this repo's
research note records
([research/aws-spot-placement-score-configuration-limit.md](../aws-spot-placement-score-configuration-limit.md))).

Answer, from the public SpotLake snapshot of 2026-10-11 23:50 UTC: a
score of 9 in at least 1 US Availability Zone. `p5.48xlarge` runs 8x
H100 GPUs
([research/aws-spot-placement-score-per-gpu-type.md](../aws-spot-placement-score-per-gpu-type.md)).

## Why it matters

A tool that follows the "3 types or low score" rule from the docs
groups GPU types into a mix to get a usable score. For most GPU
types, that step is not necessary and it hides which exact type the
score is about. Check each GPU type alone first, and add a CPU control
(2 types or fewer) to confirm that the low-score rule still holds for
the account.

## Sources

- [AWS User Guide: Spot placement score](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html)
- [AWS API reference: `GetSpotPlacementScores`](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html)
- [AWS CLI reference: `get-spot-placement-scores`](https://docs.aws.amazon.com/cli/latest/reference/ec2/get-spot-placement-scores.html)
- [boto3 reference: `get_spot_placement_scores`](https://docs.aws.amazon.com/boto3/latest/reference/services/ec2/client/get_spot_placement_scores.html)
- [research/aws-spot-placement-score-per-gpu-type.md](../aws-spot-placement-score-per-gpu-type.md)
- [research/aws-spot-placement-score-configuration-limit.md](../aws-spot-placement-score-configuration-limit.md)
