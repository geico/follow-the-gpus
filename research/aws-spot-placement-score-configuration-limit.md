---
title: "AWS Spot placement score configuration limit"
date: 2026-10-12
---

# AWS Spot placement score configuration limit

## Summary

AWS limits the number of new `GetSpotPlacementScores` request configurations in 24 hours. AWS gives no number, no key, and no window type. This note collects the public evidence.

- **Size.** Public reports give 50 (the SpotLake and SpotVista papers, which share an author). They also give 28 to 39 (1 open-source project, 1 commit, 1 account). The "a few (10)" sentence in the AWS tracker sample is guidance, not a measurement.
- **Per-account size: open.** The AWS text says that the limit applies "if we detect patterns not associated with the intended use". A count from one account does not carry over to another account.
- **Key: partly reported.** The SpotLake paper names Regions, instance types, and the number of instances. No source tests `SingleAvailabilityZone`, `TargetCapacityUnitType`, `IncludeLocalZones`, list order, or `MaxResults`.
- **Window: open.** The AWS texts use 2 wordings. Counting by project, 3 report "rolling" (AWS GCO, OptiTrain, and argus) and 0 report "fixed". No source shows a timed measurement.
- **Scope: open.** Third-party sources say "per account". One public issue shows the error from 1 endpoint Region and scores from another endpoint Region, for the same account and request.

## Scope and method

This note extends [API rate limits of the signal probes](api-rate-limits-of-signal-probes.md) and [AWS Spot placement score per GPU instance type](aws-spot-placement-score-per-gpu-type.md). It does not repeat their content.

Status of each value:

- **Documented**: an AWS page states it.
- **Reported**: a paper, a repo, or an issue states it. The text says if the source made a measurement.
- **Observed**: a read-only call showed it.

## What AWS states

- **The limit (documented).** "Request configurations limit – We can limit the number of new request configurations within a 24-hour period if we detect patterns not associated with the intended use of the Spot placement score feature. If you reach the limit, you can retry the request configurations that you've already used, but you can't specify new request configurations until the next 24-hour period." ([Spot placement score](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html#sps-limitations))
- **The error (documented).** `MaxConfigLimitExceededException`: "You've exceeded your maximum allowed Spot placement configurations. You can retry configurations that you used within the last 24 hours, or wait for 24 hours before specifying a new configuration." ([error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html))
- **No definition of "configuration" for the limit (documented by absence).** The API reference lists the request fields: `InstanceTypes` or `InstanceRequirementsWithMetadata`, `RegionNames`, `SingleAvailabilityZone`, `TargetCapacity`, `TargetCapacityUnitType`, `IncludeLocalZones`, `MaxResults`, `NextToken`, and `DryRun`. It says nothing about the limit. ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html))
- **"Spot request configuration" in another sense (documented).** The "How Spot placement score works" page uses the words for "target capacity, target capacity unit, and instance types or instance attributes". That page describes which Spot request a score applies to. It does not define the key of the limit. ([How Spot placement score works](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-sps-works.html))

## Size of the limit

| Source | Date | Value | Kind |
|---|---|---|---|
| [SpotLake paper](https://arxiv.org/abs/2202.02973), section 3.1 | 2022 | "an account can issue a maximum of 50 unique queries in 24 hours" | Reported from the authors' "empirical analysis". No method is given. |
| [SpotVista paper](https://arxiv.org/abs/2604.24548), section 3 | 2026-04 | "within a 24-hour window, only 50 distinct query scenarios are allowed" | Reported. Same research group as SpotLake. |
| [SpotLake collector](https://github.com/ddps-lab/spotlake/blob/fd1388c3adb066a07db2d2fbf81aff2c0fd09141/collector/spot-dataset/aws/ec2/sps/sps_query_api.py#L18) | current | A code comment says that each set of scenarios "must be a bundle of 50 query scenarios" (Korean in the source). 1 account sends 1 bundle. On `MaxConfigLimitExceeded`, the collector moves to the next account ([collect_sps.py](https://github.com/ddps-lab/spotlake/blob/fd1388c3adb066a07db2d2fbf81aff2c0fd09141/collector/spot-dataset/aws/ec2/sps/collect_sps.py#L200-L208)). | Design based on 50 per account |
| [OptiTrain spot watcher](https://github.com/miguelaenlle/OptiTrain-Distributed-LLM-Training/blob/865e9dc39dc99bd26d276537584f6841193ed3be/src/orchestrator/lambda_spotwatch.py) | 2026-08 | "Measured on this account: 28 distinct configurations succeeded and every further one failed" | Measurement on 1 account. Training runs used the same account. The file does not say if another caller used `GetSpotPlacementScores`. |
| [OptiTrain commit 79d1072](https://github.com/miguelaenlle/OptiTrain-Distributed-LLM-Training/commit/79d1072035c39dc64e8278a65e8498cfa4e6e58c), branch `phase1/spotwatch` | 2026-08-03 | A matrix of 456 configurations had 417 refusals, thus 39 accepted (arithmetic). | Measurement on the same account. The project does not explain the difference from 28. |
| [AWS GCO sample](https://github.com/aws-solutions-library-samples/global-capacity-orchestrator-on-aws/blob/b994f7dbe901217a1ec3d44c706e8496db41b854/lambda/capacity-poller/README.md) | 2026-08 | "13 pools x 3 target capacities is ~39 distinct configurations per cycle" | A design budget, not a limit |
| [AWS tracker sample](https://github.com/aws-solutions-library-samples/guidance-for-ec2-spot-placement-score-tracker/blob/433c22eebc26f85a84bea05cae46282ab3476888/README.md) | 2023 | "a few (10) diversified configurations" | Guidance. No measurement. |
| [argus](https://github.com/codingCoffee/argus/blob/3006a167c5cd5e87f2f43e962efe07e22bbe553a/README.md) | 2026-07-17 | "`GetSpotPlacementScores` budgets *new* request configurations (instance types + regions + capacity) over a rolling 24 hours; repeating an already-used configuration is free." | Reported. No number given, but states the key fields and the rolling window as a design fact. |
| [OptiTrain, `docs/shared-account-coexistence.md`](https://github.com/miguelaenlle/OptiTrain-Distributed-LLM-Training/blob/a23635dcec427909bf78ef1a1db31291c35e703e/docs/shared-account-coexistence.md) | 2026-08-08 | "an undocumented distinct-configuration cap per rolling 24h (~28–39 observed on this account) that is not in service-quotas" | Reported. Same project and same account as the 2 OptiTrain rows above. Both 28 and 39 come from [commit 79d1072](https://github.com/miguelaenlle/OptiTrain-Distributed-LLM-Training/commit/79d1072035c39dc64e8278a65e8498cfa4e6e58c). This file restates that range as a standing design fact, not a new measurement. It also says the cap is not in Service Quotas. (Interpretation, not stated by the source: this suggests no self-service path to raise the cap.) |

Interpretation (not documented by AWS): the limit is not 10 for every account. The AWS wording "if we detect patterns" allows a limit that differs by account or by time. No source in this note shows the configuration limit listed in AWS Service Quotas.

## Which fields make a new configuration

| Field | Evidence | Status |
|---|---|---|
| `InstanceTypes` | The SpotLake paper: "The uniqueness of a query is determined by the combination of regions, instance types, and the number of desired instances." | Reported |
| `RegionNames` | The SpotLake paper, as above. | Reported |
| `TargetCapacity` | The SpotLake paper, as above. The SpotVista paper: "queries for the same configuration with different node counts are treated as separate requests." | Reported |
| `TargetCapacityUnitType` | No source tests it. | Open |
| `SingleAvailabilityZone` | No source tests it. The SpotLake collector always sets it to true. | Open |
| `InstanceRequirementsWithMetadata` | No source tests it. | Open |
| `IncludeLocalZones` | No source tests it. | Open |
| `MaxResults`, list order of types and Regions | No source tests it. | Open |

The OptiTrain project counts its budget with a key of instance types, target capacity, unit type, `SingleAvailabilityZone`, and Regions. It says this key is "What AWS counts against the 24h budget". It does not show a test of each field. ([lambda_spotwatch.py](https://github.com/miguelaenlle/OptiTrain-Distributed-LLM-Training/blob/865e9dc39dc99bd26d276537584f6841193ed3be/src/orchestrator/lambda_spotwatch.py))

Planning rule: treat a change of any request field as a new configuration. This rule is safe under each key above.

## Rolling or fixed window

- The User Guide says "until the next 24-hour period". The error text says "used within the last 24 hours" and "wait for 24 hours". The first wording fits a fixed period. The second wording fits a rolling window. (Interpretation.)
- The AWS GCO sample says "a rolling 24-hour window". It adds that refused configurations can work again only after old configurations "age out of the rolling window". The OptiTrain project also says "rolling 24h". Neither source shows a timed measurement. ([GCO README](https://github.com/aws-solutions-library-samples/global-capacity-orchestrator-on-aws/blob/b994f7dbe901217a1ec3d44c706e8496db41b854/lambda/capacity-poller/README.md))
- The SpotLake collector changes to the other half of its account pool when the UTC date of its workload changes ([collect_sps.py](https://github.com/ddps-lab/spotlake/blob/fd1388c3adb066a07db2d2fbf81aff2c0fd09141/collector/spot-dataset/aws/ec2/sps/collect_sps.py#L138-L147)). This fits a daily budget. It does not show which window type AWS uses.
- The [argus README](https://github.com/codingCoffee/argus/blob/3006a167c5cd5e87f2f43e962efe07e22bbe553a/README.md) says "rolling 24 hours" as a stated design fact. No timed measurement backs it.
- The [OptiTrain coexistence doc](https://github.com/miguelaenlle/OptiTrain-Distributed-LLM-Training/blob/a23635dcec427909bf78ef1a1db31291c35e703e/docs/shared-account-coexistence.md) (2026-08-08) also says "rolling 24h". This is the same project as the OptiTrain Lambda comment above, on the same account, so it is not a second, independent source.
- Counting by project, not by file, 3 projects report "rolling" (AWS GCO, OptiTrain, and argus) and 0 report "fixed". The User Guide wording can fit either reading.

Status: open. Plan for a rolling window, because it is the stricter case: a configuration stays in the count for 24 hours after its first use.

## Scope of the counter

- The SpotLake paper says "an account". The GCO sample says "an account-level limit". (Reported.)
- The mapt issue [#617](https://github.com/redhat-developer/mapt/issues/617) (2025-10-02) shows 1 account and 1 request with no `RegionNames`. Through the us-east-2 endpoint, the call failed with `MaxConfigLimitExceeded`. Through the sa-east-1 endpoint, the call returned scores. (Reported, 1 case.)
- Interpretation: the counter can be per account and per endpoint Region. Without `RegionNames`, the 2 calls can also differ in their default Regions. Thus the case does not prove it.
- No source tests if 2 principals in 1 account share the counter.
- [SpotLake's open-data README](https://github.com/ddps-lab/spotlake-open-data/blob/ee46e471ea3357c94bd7e52425bb0ac3a68444e2/README.md) (2026-10-06) says its collector groups calls "into batches of up to 50, and the batches are distributed across multiple AWS accounts." The collector spreads batches across accounts and does not wait out a window. Interpretation: this design assumes a separate counter per account. (Reported. This is a design choice, not a test of 2 accounts side by side.)
- The [OptiTrain coexistence doc](https://github.com/miguelaenlle/OptiTrain-Distributed-LLM-Training/blob/a23635dcec427909bf78ef1a1db31291c35e703e/docs/shared-account-coexistence.md) says "the budget is account-wide". This matches the per-account scope that the other sources above report, on the same account that gives the 28 to 39 figures in the size table. (Reported.)

Status: open.

## Safety rules for any future measurement

No measurement of the limit is planned. If you run one, follow these rules:

- The size of the limit: measure it only on the account where the prober runs. A count from another account does not answer it.
- Hold every input of the live request. The inputs are the `aws.regions` values and their order, `spot_score_requests`, and the `aws_spot_score` probe code. Hold them from the first new set until 24 hours after the last new set.
- Confirm before the first new set that each Spot score request got a score in the last 24 hours. Do not start if 1 entry did not.
- Do not restart or redeploy the prober during the hold. A restart clears the list of entries that already have a score.
- Never pause the live request list.
- Stop the run at the first `MaxConfigLimitExceeded`. Keep the hold in force.
- Report the size as a range, not an exact count. Other callers on the account, and the edges of the window, make an exact count hard.

## Effect on the request budget

The budget in [API rate limits of the signal probes](api-rate-limits-of-signal-probes.md#request-budget-for-a-single-type-signal-experiment) plans for 10 or fewer configurations. The evidence above shows accounts that accepted more than 10. It does not show a floor for your account. Keep that budget until you measure your account.

## Open items

- The size of the limit on a given account.
- Whether `TargetCapacityUnitType`, `SingleAvailabilityZone`, `InstanceRequirementsWithMetadata`, `IncludeLocalZones`, `MaxResults`, and list order are part of the key.
- Whether the 24-hour window rolls or resets at a fixed time.
- Whether the counter is per account, per endpoint Region, or per principal.

## Sources

- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-sps-works.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html>
- <https://arxiv.org/abs/2202.02973>
- <https://arxiv.org/abs/2604.24548>
- <https://github.com/ddps-lab/spotlake>
- <https://github.com/miguelaenlle/OptiTrain-Distributed-LLM-Training>
- <https://github.com/aws-solutions-library-samples/global-capacity-orchestrator-on-aws>
- <https://github.com/aws-solutions-library-samples/guidance-for-ec2-spot-placement-score-tracker>
- <https://github.com/redhat-developer/mapt/issues/617>
- <https://github.com/codingCoffee/argus>
- <https://github.com/ddps-lab/spotlake-open-data>
