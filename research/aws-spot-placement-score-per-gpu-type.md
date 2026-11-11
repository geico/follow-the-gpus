---
title: "AWS Spot placement score per GPU instance type"
date: 2026-10-11
---

# AWS Spot placement score per GPU instance type

## Summary

The AWS docs say that a Spot placement score request needs at least 3 instance types. With fewer types, the score "will always be low". The docs also say that 1 score covers the whole set of types. For a heat map per GPU type, that is a problem: 1 call gives 1 number for a mix.

Public data shows that AWS now scores most single GPU, Inferentia, and Trainium types on their own. On 2026-10-11, single-type scores for those types reach 9. Single-type scores for CPU types stay at 3 or lower. The AWS documentation (User Guide, API reference, CLI, and SDK) does not state this exception. Only an AWS sample README mentions it, and only for P-family types. A poller must treat the exception as observed behavior, and it must check it with a control on each cycle.

## What the AWS docs say

- **The 3-type rule.** "If you specify instance types, you must specify at least three different instance types, otherwise Amazon EC2 will return a low Spot placement score." ([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html))
- **Variations.** The API reference adds "variations of a single instance type (for example, an `m3.xlarge` with and without instance storage)". Sizes of 1 family count as different types: "m5.8xlarge, m5a.8xlarge, and m5.12xlarge are all considered different." ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html), [User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html))
- **One score per mix.** The score for each Region or Availability Zone (AZ) "is calculated based on the target capacity, the composition of the instance types, the current Spot usage trends, and the time of the request". The score assumes the `capacity-optimized` allocation strategy. ([How Spot placement score works](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-sps-works.html))
- **No GPU exception in the docs.** The API reference, the User Guide, the CLI reference, and boto3 state the rule with no exception for GPU or accelerated types. The rule was in the AWS launch post of 2021-10-27. ([launch post](https://aws.amazon.com/blogs/compute/identifying-optimal-locations-for-flexible-workloads-with-spot-placement-score/), [CLI](https://docs.aws.amazon.com/cli/latest/reference/ec2/get-spot-placement-scores.html), [boto3](https://docs.aws.amazon.com/boto3/latest/reference/services/ec2/client/get_spot_placement_scores.html))

## Evidence that accelerated types get a score per type

1. **AWS sample README.** The AWS Solutions Library tracker sample says: "For P-family instances (P4, P5, P6): Single instance type configurations are supported." Commit `4d76fa0` added this line on 2026-02-09. The sample code has no logic for it. ([README.md line 379](https://github.com/aws-solutions-library-samples/guidance-for-ec2-spot-placement-score-tracker/blob/433c22eebc26f85a84bea05cae46282ab3476888/README.md#L379), [commit](https://github.com/aws-solutions-library-samples/guidance-for-ec2-spot-placement-score-tracker/commit/4d76fa0f9a7b56bb6672cec3377c431750ddf8de))
2. **SpotLake daily snapshot.** SpotLake is a public Spot dataset from the DDPS Lab, now at Hanyang University. Its collector sends 1 instance type per request (`InstanceTypes = [instance_type]`). Its public snapshot of 2026-10-11 23:50 UTC has 49,526 rows (1 type, 1 AZ each). Counts from that file:

   | Group | Rows | Highest score |
   |---|---|---|
   | Non-accelerated types | 47,392 | 3 |
   | GPU, Inferentia, and Trainium types | 2,091 | 9 |
   | FPGA, DL, and VT types (f2, dl2q, vt1) | 43 | 3 |

   All 20 families with a score above 3 are accelerated: g4dn, g5, g5g, g6, g6e, g6f, g7, g7e, gr6, gr6f, inf1, inf2, p3dn, p4d, p4de, p5, p6-b200, p6-b300, trn1, trn1n. Three GPU and Trainium families never score above 3 in this snapshot: p5e, p5en, and trn2. No row scores 10. ([snapshot](https://d2krkjqajp4l0e.cloudfront.net/latest_aws.json), [collector code](https://github.com/ddps-lab/spotlake/blob/fd1388c3adb066a07db2d2fbf81aff2c0fd09141/collector/spot-dataset/aws/batch/sps/sps_query_api.py#L48-L53))
3. **SpotLake commits.** Commit `e0308fe` (2026-01-23) changed the code to handle scores above 3. Commit `1424fb2` (2026-08-19) says: "P-family instances return raw 1-10 scores (2025-09-25 onward)". This review found no public source for that date (not verified). ([e0308fe](https://github.com/ddps-lab/spotlake/commit/e0308fe293939bb8386da13ab7c12a0fc3dfe7f3), [1424fb2](https://github.com/ddps-lab/spotlake/commit/1424fb2447c713ca11e454d9982b1be3a011eb69))
4. **spot-watch log.** The public spot-watch repo is scheduled to poll `g5.xlarge` alone, and a g5, g4dn, and g6 trio, every hour in 9 Regions at target capacity 1. The log has 300 polls from 2026-08-26 to 2026-10-11. Before 2026-09-23 20:14 UTC, the single type never scored above 3 (153 polls, 1,377 Region scores). After that time, it scored 9 in 430 of 1,323 Region scores (147 polls). In 2,700 Region pairs, it never scored above the trio. The collector config did not change after 2026-08-27. ([spot-watch](https://github.com/ccam80/spot-watch), [scores.csv](https://github.com/ccam80/spot-watch/blob/c7b7ca9566f4eef4c0af5e19eb079a78aed75610/data/scores.csv))

Interpretation (not documented by AWS): the exception covers most GPU, Inferentia, and Trainium families, not only the P family. It reached P types first and G types on 2026-09-23.

## What "low" means

"Low" is a cap at 3, not a fixed value. Capped single-type scores still vary between 1 and 3. The 2022 SpotLake paper reports single-type scores of 3 or lower. ([SpotLake paper](https://arxiv.org/abs/2202.02973))

## Limits that shape a poller

- **Configuration limit.** "We can limit the number of new request configurations within a 24-hour period if we detect patterns not associated with the intended use." The error says: "You can retry configurations that you used within the last 24 hours". If the window rolls (not verified), the limit applies to the whole set of configurations that a poller uses each day. ([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html), [error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html))
- **How many.** The AWS tracker README says "a few (10) diversified configurations". The SpotLake paper reports 50 per account. Plan for 10 or fewer. ([README.md](https://github.com/aws-solutions-library-samples/guidance-for-ec2-spot-placement-score-tracker/blob/433c22eebc26f85a84bea05cae46282ab3476888/README.md), [SpotLake paper](https://arxiv.org/abs/2202.02973))
- **Errors.** `MaxConfigLimitExceededException` and `TargetCapacityLimitExceededException` in the error list. The README log shows `MaxConfigLimitExceeded` with no suffix. A poller must handle both forms. ([error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html))
- **Default GPU Spot quota.** "All P Spot Instance Requests" and "All G and VT Spot Instance Requests" have a default of 0. The target capacity limit for a new account is "aligned with your Spot request limit". Thus a request at target capacity 1 can fail in a new account (not verified). ([Spot quotas](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-limits.html))

## US GPU scores on 2026-10-11

Highest single-type score in any US AZ, from the SpotLake snapshot:

| Instance type | GPU | Highest US score |
|---|---|---|
| p5.48xlarge | 8x H100 | 9 |
| p5e.48xlarge | 8x H200 | 1 |
| p5en.48xlarge | 8x H200 | 1 |
| p6-b200.48xlarge | 8x B200 | 1 |
| p6-b300.48xlarge | 8x B300 | 9 |

Inference: a 3-type group of p5, p5e, and p5en scores mostly the H100 type. Do not use such a group as an H200 signal.

## Design implications for a heat map

- Use 1 instance type per row for accelerated types. Use regional scores at target capacity 1.
- Keep 10 or fewer configurations, and use the same set every day. Do not add a new configuration during the run.
- Add 2 controls to each cycle:
  - 1 CPU type alone. It must stay at 3 or lower. A higher score shows that the rule changed for all types.
  - 1 GPU trio that contains a single-type row. The single type must not score above the trio.
- Label the rows "Spot slack (1 instance)". The score measures spare Spot capacity. It does not show On-Demand capacity.
- Do not use leave-one-out designs, filler types, capacity sweeps, or weights that combine signals. Each one costs configurations, and none is backed by AWS.

## Not yet tested

- The exception in a new account.
- The onset dates.
- The size and window of the configuration limit in a new account.
- Whether `RegionNames` counts as part of a configuration. The 2022 SpotLake paper reports that Regions are part of query uniqueness, but AWS does not say so.
- The target capacity limit of a new account.
- Why no score of 10 appears.
- What a score of 1 means.

## Sources

- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-sps-works.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-limits.html>
- <https://docs.aws.amazon.com/cli/latest/reference/ec2/get-spot-placement-scores.html>
- <https://docs.aws.amazon.com/boto3/latest/reference/services/ec2/client/get_spot_placement_scores.html>
- <https://aws.amazon.com/blogs/compute/identifying-optimal-locations-for-flexible-workloads-with-spot-placement-score/>
- <https://github.com/aws-solutions-library-samples/guidance-for-ec2-spot-placement-score-tracker>
- <https://d2krkjqajp4l0e.cloudfront.net/latest_aws.json>
- <https://github.com/ddps-lab/spotlake>
- <https://arxiv.org/abs/2202.02973>
- <https://github.com/ccam80/spot-watch>

Sources of single facts in the text:

- [Commit 4d76fa0 added that line on 2026-02-09](https://github.com/aws-solutions-library-samples/guidance-for-ec2-spot-placement-score-tracker/commit/4d76fa0f9a7b56bb6672cec3377c431750ddf8de.patch)
- [The sample code has no logic for the P-family case](https://github.com/aws-solutions-library-samples/guidance-for-ec2-spot-placement-score-tracker/blob/433c22eebc26f85a84bea05cae46282ab3476888/spot_placement_score_lambda/spot_placement_score_lambda_v2.py)
- [SpotLake is from the Kookmin University DDPS lab](https://github.com/ddps-lab/spotlake/blob/fd1388c3adb066a07db2d2fbf81aff2c0fd09141/utility/monthly_drive_dataset_uploader/DATASET_README.md)
- [Commit e0308fe (2026-01-23) changed the code to handle scores above 3](https://github.com/ddps-lab/spotlake/commit/e0308fe293939bb8386da13ab7c12a0fc3dfe7f3.patch)
- [Commit 1424fb2 (2026-08-19) says "P-family instances return raw 1-10 scores (2025-09-25 onward)"](https://github.com/ddps-lab/spotlake/commit/1424fb2447c713ca11e454d9982b1be3a011eb69.patch)
- [spot-watch polls g5.xlarge alone and a g5/g4dn/g6 trio every hour at target capacity 1](https://github.com/ccam80/spot-watch/blob/c7b7ca9566f4eef4c0af5e19eb079a78aed75610/config.json)
- [The single type never scored above the trio](https://github.com/ccam80/spot-watch/blob/c7b7ca9566f4eef4c0af5e19eb079a78aed75610/REPORT.md)
- [The collector config did not change after 2026-08-27](https://github.com/ccam80/spot-watch/commits/main/config.json)
- [A re:Post question shows single CPU types returning 3](https://repost.aws/questions/QUqMiEE17ER_C23VUiowuddQ/spot-score-is-always-the-same-value-3)
- [p5.48xlarge = 8x H100; highest US score 9; p5e.48xlarge = 8x H200; highest US score 1](https://aws.amazon.com/ec2/instance-types/p5/)
- [p6-b200.48xlarge = 8x B200; highest US score 1; p6-b300.48xlarge = 8x B300; highest US score 9](https://aws.amazon.com/ec2/instance-types/p6/)
