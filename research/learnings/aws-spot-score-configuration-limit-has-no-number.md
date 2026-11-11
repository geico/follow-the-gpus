---
title: "AWS gives no number for the Spot placement score configuration limit"
date: 2026-10-12
---

# AWS gives no number for the Spot placement score configuration limit

## What the docs say

AWS documents a limit on the Spot placement score API: "We can limit
the number of new request configurations within a 24-hour period if
we detect patterns not associated with the intended use of the Spot
placement score feature. If you reach the limit, you can retry the
request configurations that you've already used, but you can't
specify new request configurations until the next 24-hour period."
([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html#sps-limitations))
The error for this limit is `MaxConfigLimitExceededException`
([error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html)).
AWS does not state a count, does not define "configuration" for this
limit, and does not say if the 24-hour period is a rolling window or a
fixed daily reset.

A call for a configuration that was already used in the last 24 hours
is not affected by the limit: the quoted sentence above says you "can
retry" it. AWS does not document whether a retried call gets a fresh
score or a cached one.

## What we saw

No public source gives 1 agreed number, and the reports span a wide
range:

- An AWS sample README gives "a few (10)" as guidance, not as a
  measured limit.
- 1 commit of 1 open-source project gives 28 and 39 accepted
  configurations, both from the same account, with no explanation for
  the difference between the 2 numbers.
- A 2022 research paper on the SpotLake dataset reports 50 queries per
  account from its own measurement. A 2026 paper from the same
  research group repeats 50. Neither paper says if the 24 hours roll
  or reset.

Counting by project, 3 sources call the 24 hours a "rolling" window:
an AWS sample (a different sample from the "a few (10)" one above,
called GCO), and 2 unrelated open-source projects (OptiTrain and
argus). No source times a reset to show it.

AWS uses 2 forms of the error code. The API reference names it
`MaxConfigLimitExceededException`. An AWS sample logs it with no
`Exception` suffix, `MaxConfigLimitExceeded`. A caller must handle
both forms. The full table of sources and dates is in this repo's
research note
([research/aws-spot-placement-score-configuration-limit.md](../aws-spot-placement-score-configuration-limit.md)).

## Example

Request: `GetSpotPlacementScores` with an instance-type set that the
account has not used in the last 24 hours.

Answer when the account has reached the limit: the documented result
is: a new instance-type set gets the error
`MaxConfigLimitExceededException` (or `MaxConfigLimitExceeded`, no
suffix), and a set used within the last 24 hours still gets a score on
retry.

## Why it matters

A tool cannot plan a fixed daily budget of Spot placement score calls
from the AWS docs alone, because AWS states that the limit depends on
detected usage patterns, not on a published count. Measure the limit
on the account you plan to use, and design for a limit that can be
smaller or larger than the numbers that other projects report.

## Sources

- [AWS User Guide: Spot placement score, Limitations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html#sps-limitations)
- [AWS API reference: error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html)
- [research/aws-spot-placement-score-configuration-limit.md](../aws-spot-placement-score-configuration-limit.md)
