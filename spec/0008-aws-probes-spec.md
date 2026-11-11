---
title: "AWS probes spec: Capacity Block offerings, Spot placement score, Spot price history, Spot Instance Advisor"
date: 2026-10-11
---

# 0008. AWS probes spec

**Status:** Phase 1 build · 2026-10-11
**Parent:** [spec 0002](0002-gpu-capacity-tracker-spec.md), sections 4.1, 4.2, 4.8, 7.2, 7.3, D2, D13. The probe contract is [spec 0003](0003-prober-core-spec.md). The `details` contract is [spec 0003](0003-prober-core-spec.md), section 4.4.
**Code:** `prober/ftg_prober/aws/` (1 module per probe, plus `common.py`), `prober/tests/aws/`.

A person must be able to build the 6 AWS probes again from this file, spec 0002, and spec 0003. Each probe is a plugin of the prober core. It returns rows and does not write them (spec 0003, section 5).

| `probe_type` | Module | Class | `default_cadence` | AWS call |
|---|---|---|---|---|
| `aws_cb_offering` | `cb_offerings.py` | `CapacityBlockOfferingsProbe` | `2h` | `DescribeCapacityBlockOfferings` |
| `aws_cb_earliest` | `cb_earliest.py` | `CapacityBlockEarliestProbe` | `2h` | `DescribeCapacityBlockOfferings` |
| `aws_cb_longest` | `cb_longest.py` | `CapacityBlockLongestProbe` | `2h` | `DescribeCapacityBlockOfferings` |
| `aws_spot_score` | `spot_score.py` | `SpotPlacementScoreProbe` | `5m` | `GetSpotPlacementScores` |
| `aws_spot_price` | `spot_price.py` | `SpotPriceProbe` | `5m` | `DescribeSpotPriceHistory` |
| `aws_spot_advisor` | `spot_advisor.py` | `SpotAdvisorProbe` | `24h` | HTTPS GET of the Spot Instance Advisor feed |

The cadences are those of spec 0002, D2 and section 4.1. The config `cadence` entry wins over `default_cadence` (spec 0003, section 6).

## 1. Scope and rules

1. Scope of the experiment: continental US commercial Regions, GPU only, as in spec 0003, section 1. Spot signals are the core data (spec 0002, D1).
2. Each probe sets `probe_type`, `provider = "aws"`, and `default_cadence` as class fields.
3. A probe reads its targets from `ctx.config`, the `aws:` section of `config/targets.yaml` (spec 0003, sections 3 and 5.2).
4. A probe gets each EC2 client from `ctx.clients.boto3("ec2", region)`. It takes no secrets.
5. A probe module must not import `boto3`, `botocore`, or `requests` at import time. The loader imports every module, and a test environment has no `aws` extra.
6. Simulated: the scheduler does not call these probes. It writes the synthetic rows of spec 0003, section 7.2.
7. These probes have no entry-level SKU distinction (unlike the Azure provisioning probes), so there is no `cloud-cpu`/`cloud-gpu` split to make: all 5 calls only read data, simulated or not.
8. The Spot price probe keeps no state between calls (spec 0002, D13). The Spot placement score probe keeps 2 values for the process lifetime (section 4.4). The other probes keep no state.

## 2. Shared rules

### 2.1 GPU types

1. An instance type is a GPU type when its family starts with `p` or `g`. The family is the part before the first dot, for example `g4dn` in `g4dn.xlarge`.
2. The request GPU types are each GPU type in any `spot_score_requests` entry, 1 time each, in request order. A type that is only in a mix entry counts too.
3. With the repo config, the request GPU types are `p5.4xlarge`, `g7e.2xlarge`, `g6e.xlarge`, `g5.xlarge`, `g4dn.xlarge`, and `g6.xlarge`. Spec 0002, section 4.2 gives the reason for each entry.
4. The Spot price probe and the Spot Advisor probe use the request GPU types.

### 2.2 Row fields

Each row is a `Record` of spec 0003, section 4.1. These fields are the same on all 6 probes:

| Field | Value |
|---|---|
| `ts_utc` | `ctx.timestamp()` when the row is made |
| `run_id` | `ctx.run_id` |
| `simulated` | `ctx.simulate` (ADR 0011, replacing the earlier `run_mode`). |
| `provider` | `aws` |
| `placement_group_mode` | `n/a` |
| `n_requested` | `1`, except `aws_cb_offering`, `aws_cb_earliest`, and `aws_cb_longest`: `InstanceCount` of the call: the `capacity_blocks.instance_counts` value of the type for that call, else `capacity_blocks.instance_count` |
| `n_placed` | null |
| `error_code` | null, except on `error` and `cap` rows |

### 2.3 API calls, pages, and latency

1. A helper `paginate(operation, kwargs, list_key)` calls the operation. It follows `NextToken` until the token is empty or missing. Each next page sends the same parameters plus `NextToken`.
2. `latency_ms` of a row is the time of the API call that made the row, with all its pages. The clock is `time.monotonic`. Each row of 1 call has the same `latency_ms`.
3. The helper catches each exception of the call. It returns the items of the pages so far, the latency, and an error code. It never raises.

### 2.4 Error mapping

1. The error code is `response["Error"]["Code"]` of a botocore `ClientError`. For any other exception, the code is the class name, for example `EndpointConnectionError`.
2. The probe writes the raw code into `error_code`. It does not change or shorten the code.
3. A failed call gives `outcome = error`, except the codes that section 4.5 maps to `cap`.
4. One failed call does not stop the other calls of the same probe run.
5. A row never holds the error message. Each failed call logs 1 `provider.error` warning (spec 0003, section 8.1, item 6) with `probe_type`, `region`, `code`, `status`, and `message`, where `message` is `response["Error"]["Message"]` as AWS returned it, else the text of the exception. `region` is the Region of the call; a call for several Regions (`aws_spot_score`, `aws_spot_advisor`) joins them with commas. A page of a paginated call that fails gives 1 line for the call. `aws_odcr` also logs a failed pre-check, create, poll, describe, and cancel call.

### 2.5 Retry rule

1. The probes add no retry of their own. Each client of `ctx.clients` has a connect timeout of 5 seconds, a read timeout of 30 seconds, and the botocore `standard` retry mode with 3 attempts ([botocore Config](https://botocore.amazonaws.com/v1/documentation/api/latest/reference/config.html), [retries](https://docs.aws.amazon.com/boto3/latest/guide/retries.html)). The botocore defaults are 60 seconds for each timeout, and a hung call would block the single scheduler thread. The S3 client of the prober store has the same settings. The Capacity Block probes use a separate client with 1 attempt, because the queue owns the throttle handling (section 3.3, rule 6) and a botocore retry would spend tokens that the model bucket does not count.
2. The next call at the cadence is the retry. A failed call leaves an `error` row, so that the gap is visible.

### 2.6 IAM

| `probe_type` | IAM action |
|---|---|
| `aws_cb_offering` | `ec2:DescribeCapacityBlockOfferings` |
| `aws_cb_earliest` | `ec2:DescribeCapacityBlockOfferings` |
| `aws_cb_longest` | `ec2:DescribeCapacityBlockOfferings` |
| `aws_spot_score` | `ec2:GetSpotPlacementScores` |
| `aws_spot_price` | `ec2:DescribeSpotPriceHistory` |
| `aws_spot_advisor` | none: the feed is public HTTPS (spec 0002, 4.8) |
| `aws_odcr` (spec 0002, 4.9) | `ec2:CreateCapacityReservation`, `ec2:CancelCapacityReservation`, `ec2:DescribeCapacityReservations`, `ec2:DescribeInstanceTypeOfferings`, `ec2:DescribeAvailabilityZones` (zone ID to zone name; the probe covers every zone of the Region), `ec2:DescribeInstanceTypes`, `servicequotas:GetServiceQuota` (the `n_ceiling` from the On-Demand vCPU quota) |

The 3 EC2 actions (`aws_cb_offering`, `aws_cb_earliest`, and `aws_cb_longest` use the same action) are in the EC2 service reference of the Service Authorization Reference ([EC2 service reference JSON](https://servicereference.us-east-1.amazonaws.com/v1/ec2/ec2.json)). The AWS stack of [spec 0004](0004-aws-stack-spec.md) grants all 3 in `terraform/aws/iam.tf`.

## 3. `aws_cb_offering`: Capacity Block offerings

### 3.1 Request

`DescribeCapacityBlockOfferings` describes the Capacity Block offerings for purchase in the Region of the client ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html)). The call only reads. Buying a block is a separate action (spec 0003, ledger P01).

The probe makes 1 call for each Region in `regions`, each type in `capacity_blocks.instance_types`, each count of that type, and each value in `capacity_blocks.duration_hours`. The counts of a type are `capacity_blocks.instance_counts[type]` (a list of integers, 1 or more, with no duplicate) when the optional map has the type, else the 1 value `capacity_blocks.instance_count`. A (type, count) pair is a separate series: the same type with 2 counts asks AWS 2 different questions. `now` is `ctx.now()` at the start of the call.

| Parameter | Value |
|---|---|
| `InstanceType` | the type |
| `InstanceCount` | the count of the call (section 3.3) |
| `CapacityDurationHours` | the duration value |
| `StartDateRange` | `now` |
| `EndDateRange` | `now` + `start_window_days` days + the duration in hours |
| `MaxResults` | `1000` |
| `NextToken` | from the previous page |

API facts ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html)):

- `CapacityDurationHours` is the only required parameter. The duration must be in 1-day steps up to 14 days, and in 7-day steps up to 182 days.
- `StartDateRange` is "the earliest start date" and `EndDateRange` is "the latest end date" for the offering. Thus the end of the range adds the duration to the start window.
- `MaxResults` takes 1 to 1000.

### 3.2 Row mapping

1 row per offering in `CapacityBlockOfferings`, from all pages:

| Field | Value |
|---|---|
| `region` | the Region of the call |
| `zone` | `AvailabilityZone` |
| `sku` | `InstanceType` of the offering, else the requested type |
| `outcome` | `success` |
| `details.start` | `StartDate` as a `ts_utc` string |
| `details.end` | `EndDate` as a `ts_utc` string |
| `details.duration_hours` | `CapacityBlockDurationHours` |
| `details.duration_minutes` | `CapacityBlockDurationMinutes`, raw. Null when the offering has no such field. |
| `details.instance_count` | `InstanceCount` |
| `details.upfront_fee` | `UpfrontFee` as a number. The API gives it as a string. |
| `details.currency` | `CurrencyCode` |
| `details.offering_id` | `CapacityBlockOfferingId` |
| `details.tenancy` | `Tenancy`, raw string. Null when the offering has no such field. |
| `details.zone_type` | `ZoneType`, raw string. Null when the offering has no such field. |
| `details.ultraserver_type` | `UltraserverType`, raw string. Null when the offering has no such field. |
| `details.ultraserver_count` | `UltraserverCount`, a number. Null when the offering has no such field. |

The offering fields are those of the `CapacityBlockOffering` data type ([CapacityBlockOffering](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityBlockOffering.html)). The length of an offering is `duration_hours` + `duration_minutes` / 60, because a Capacity Block ends at 11:30 UTC and an offering that starts at another time has a partial first period ([considerations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html#capacity-blocks-considerations)). A null `duration_minutes` counts as 0. A reader that computes a price per hour must use this length. The probe stores no derived price.

No offering: when the call returns no offering, the probe writes 1 row with `outcome = none`, `zone` = null, `sku` = the requested type, and `details = {duration_hours, start_window_days}`. Absence is data (spec 0002, 4.1).

Error: 1 row with `outcome = error`, `zone` = null, the raw code, and the same `details` as the `none` row. An unsupported type or Region gives this row. The probe does not check the type or the Region before the call.

### 3.3 Request plan and limits

The request plan of 1 run is the list of calls of section 3.1, in this order: Region, then type, then duration. A function `request_plan(config)` returns the plan as `(region, instance_type, duration_hours)` tuples. The probe makes the calls of the plan and no other calls.

API facts ([request throttling](https://docs.aws.amazon.com/ec2/latest/devguide/ec2-api-throttling.html)):

- Amazon EC2 throttles API requests for each account in each Region. It uses a token bucket for each API action. Each request takes 1 token.
- A throttled request gets the error code `RequestLimitExceeded`.
- By default, the bucket of `DescribeCapacityBlockOfferings` holds 10 tokens. It refills at 0.15 tokens per second. An account can have other values (Service Quotas).
- A search for a Capacity Block returns the details of up to 6 available blocks ([Find and purchase Capacity Blocks](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-purchase.html)).

Rules:

1. The plan must have 200 or fewer worst-case calls per Region and run. The count is the number of (type, count) pairs (the sum, over `instance_types`, of the number of counts of the type) times (the number of `duration_hours`, plus the number of `earliest_duration_hours` (section 3A.3), plus 19, the worst case of 1 `aws_cb_longest` search (section 3B.5)). The config check rejects a larger plan ([spec 0003](0003-prober-core-spec.md), section 3). The limit is our own rule, not an AWS limit. It keeps the pacing of rule 6 short. The limit was 40 before `aws_cb_longest` (section 3B), then 120 before the instance counts of a type.
2. The User Guide says that a search returns up to 6 blocks, and `MaxResults` is `1000`. Thus each call is expected to be 1 page and to take 1 token. The probe follows `NextToken`, and each extra page takes 1 token. The model bucket of rule 6 takes 1 token for each page: the worker takes 1 token before a call, and the scheduler takes 1 more for each extra page after it (`ctx.api_calls` counts the requests of a call).
3. With the default values, an empty bucket refills in about 67 seconds. At the `2h` cadence, each run starts with a full bucket, unless another client of the account uses the same bucket.
4. The repo config has 7 (type, count) pairs (`p5.4xlarge` with counts 1, 4, and 8; 4 other types with 1 count). It makes 28 calls per Region and 84 calls per run for `aws_cb_offering` (7 pairs x 4 durations), and 14 calls per Region and 42 calls per run for `aws_cb_earliest` (7 pairs x 2 durations; section 3A.3), plus up to 133 calls per Region worst case for `aws_cb_longest` (7 pairs x 19, section 3B.5): 175 worst-case calls per Region, inside the 200 limit.
5. A throttled call gives the `error` row of section 3.2 with `RequestLimitExceeded`, with 1 botocore attempt: the Capacity Block client makes no retry, and the queue handles the throttling (section 2.5, section 3.3 rule 6). The probe adds no pause and no retry of its own. The other calls of the run still run (section 2.4).
6. Queue. The 3 Capacity Block probes do not make their calls in one burst. At the start of each cadence cycle the scheduler adds each call of the plan to the queue of its Region. A worker takes the calls from the queues ([spec 0003](0003-prober-core-spec.md), section 6A). The worker wakes every 30 seconds and makes at most 1 call per Region per wake. Each sleep is a random value, uniform in 0.8 to 1.2 times the interval (24 to 36 seconds), and each Region starts at a random offset of 0 to 30 seconds. Thus the average rate stays the same, and the Regions and the prober instances do not call at the same moment. It never makes more calls than the tokens of the bucket of the Region allow. The model bucket takes 1 token for each request, and starts with 5 tokens, not 10, because the real bucket can be partly drained by other clients when the prober starts. The bucket is a model of the AWS bucket: 10 tokens, refill 0.15 tokens per second, on a monotonic clock ([request throttling](https://docs.aws.amazon.com/ec2/latest/devguide/ec2-api-throttling.html)). There is one bucket for each Region, shared by all 3 probes, in the prober process. All values are configurable, with these defaults: `aws.queue_wake_seconds` (30), `aws.queue_max_calls_per_wake` (1), `aws.queue_jitter` (0.2; it must be 0 or more and less than 1), `aws.queue_backoff_base_seconds` (30), `aws.queue_backoff_cap_seconds` (600), and `aws.queue_max_throttle_attempts` (5). The random source is a constructor argument of the scheduler, so a test is deterministic.
7. Why these values. The refill is 0.15 tokens per second, 1 token each 6.7 seconds. 1 call per 30 seconds is 0.033 calls per second, 22% of the refill. Thus the bucket stays nearly full for other clients of the account. Other clients can still drain the real bucket. A throttled call (`RequestLimitExceeded`, with 1 botocore attempt, section 2.5) is put back at the end of the queue of its Region, and the attempt writes no row. It gets a not-before time: now + a random value, uniform in 0 to min(cap, base x 2^attempts) seconds, with base 30 and cap 600. This is exponential backoff with full jitter ([AWS Builders' Library](https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/); the AWS EC2 guide tells clients to retry a throttled request with backoff, [request throttling](https://docs.aws.amazon.com/ec2/latest/devguide/ec2-api-throttling.html)). The wait before a retry is thus at most 30, 60, 120, and 240 seconds. After 5 throttled attempts in 1 cycle, the 5th attempt writes its `error` row with `RequestLimitExceeded` and the call is dropped until the next cycle. Any other error writes the usual `error` row at once, and the call is done.
8. Drain time. The Regions have separate queues and buckets, thus they drain in parallel. 15 calls in a Region drain in about 7.5 minutes after the start offset of up to 30 seconds. The repo config has 42 calls per Region (28 for `aws_cb_offering`, 14 for `aws_cb_earliest`), plus up to 133 worst case for `aws_cb_longest`: 175 worst-case calls drain in about 88 minutes. The limit of rule 1 is 200 calls per Region, so a cycle drains in about 100 minutes at most, inside the `2h` cadence. A call that waits when the next cycle starts is not added again (section 6A of spec 0003): it keeps its place, its attempts, and its backoff, so the queue does not grow. `aws_cb_longest` sets `Call.key` to the pair (instance type, instance count) for this: a search's `args` change at every step, but its queue identity does not, so a 2nd cycle's `earliest0` call does not join the queue beside a search of the same (Region, type, count) still running from the 1st. The count is in the key because 2 searches of 1 type with different counts are 2 searches, and one must not drop the other (spec 0003, section 6A, rule 1; section 3B.5).
9. Row times. A row has the time of its call (`ts_utc`), not the time of the start of the cycle. The rows of 1 cycle span the drain time. A reader that groups rows by cycle must allow for it. `--once` runs all calls of a probe in a row, with no queue and no pacing.

### 3.4 Account limit: `CapacityBlockDescribeLimitExceeded`

The EC2 error list describes this code as "You've reached the limit for this account. The returned message provides details." ([Error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html)). On 2026-10-12, a call from the account of the rig returned this message (author test): "Your existing service limits for this AWS account are not sufficient to use EC2 Capacity Blocks."

The live rows do not fit a request rate or a cap per time window that only the prober uses (author test, 2026-10-12):

- The first call of the first live run failed with this code. The prober had made no earlier call.
- Each failed call took 116 to 394 ms. Thus botocore made no retry with a pause. botocore does not list this code as a code to retry ([botocore `_retry.json`](https://github.com/boto/botocore/blob/develop/botocore/data/_retry.json)).
- The config has 1 duration, and the error comes on the first page. Thus each run made 4 calls per Region, as planned.

1. This code is an account gate, not a request rate. The request plan does not cause it and cannot remove it.
2. The probe writes the `error` row of section 3.2 with the raw code, as for each other code. The other calls of the run still run.
3. An account without Capacity Block access gets this error on each call. The `error` rows stay as the signal. Check the access on each new account.

## 3A. `aws_cb_earliest`: the earliest Capacity Block start

`aws_cb_offering` (section 3) searches a window of `start_window_days`. AWS returns "the lowest-priced Capacity Block offering available" for a range search ([pricing and billing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-pricing-billing.html)). Thus its rows show the cheapest block in the window, not the earliest. This probe asks a different question: can a block start at `first` or at `boundary` (section 3A.2)? The soonest start, when it is later, comes from `aws_cb_longest` (`details.earliest_start`, section 3B.4). It is free, like `aws_cb_offering`. It uses the same API call, the same row mapping, and the same code for both (`offering_rows` in `cb_offerings.py`).

### 3A.1 What AWS does with the start (observed)

Observed live on 2026-10-13 with `CapacityDurationHours` = 24. This is not documented by AWS, and AWS can change it:

- A `StartDateRange` at or before now + 30 minutes is moved to now + 30 minutes.
- A later `StartDateRange` is rounded up to the next 11:30 UTC.
- `EndDateRange` must be at least 1 day after the start that AWS uses. Else AWS returns `InvalidParameterValue`: "End date ... must not be less then 1 days after start date ...".

A Capacity Block ends at 11:30 UTC ([considerations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html#capacity-blocks-considerations)). Thus a block can start at now + 30 minutes with a partial first period, or at an 11:30 UTC boundary (section 3.2, `duration_minutes`).

### 3A.2 Request and window

The probe makes 1 call for each Region, each type in `capacity_blocks.instance_types`, and each value in the duration list of section 3A.3. The parameters are those of section 3.1, except the range:

| Parameter | Value |
|---|---|
| `StartDateRange` | `now` |
| `EndDateRange` | the larger of: the next 11:30 UTC at or after `now` + 30 minutes, plus the duration, plus 1 hour; and `now` + 30 minutes + 1 day |

Let `first` = `now` + 30 minutes. This is the start that AWS uses for `StartDateRange` = `now`. Let `boundary` be the next 11:30 UTC at or after `first`. The window admits the 2 possible earliest starts: a block that starts at `first`, and a block that starts at `boundary`. A block that starts after `boundary` ends after `boundary` + the duration, so it does not fit. The 1 hour of slack covers a rounding of seconds. It admits no other start, because a block starts at `first` or at an 11:30 boundary. The second term keeps the 1-day minimum of section 3A.1 for a duration of less than 24 hours.

Examples (UTC, duration 24 hours): `now` 10:00 gives `boundary` 11:30 on the same day. `now` 11:00 gives `first` = 11:30, which is the `boundary`. `now` 11:10 gives `first` = 11:40, so `boundary` is 11:30 on the next day. `now` 11:31 gives the same `boundary` as 11:10.

The earliest start depends on the duration: a live search found a start in 1 day for a 1-day block, but weeks later for a 7-day block, and none in 8 weeks for a 14-day block. Thus the probe runs each duration of section 3A.3 on its own, and a `none` row means no block starts at `first` or at `boundary`. The search returns the lowest-priced offering in the window. The window has at most these 2 starts. Thus the row with the earlier `start` is the soonest start inside the window, and a row with the `boundary` start means that no block starts at `first`. A reader compares `details.start` with `ts_utc` + 30 minutes.

### 3A.3 Plan and limits

`earliest_plan(config)` returns the calls of 1 run, as `(region, instance_type, duration_hours, instance_count)`, in the order of section 3.3 (a type with several counts gives each count in turn). The durations come from the optional key `capacity_blocks.earliest_duration_hours`. When it is not set, they are `duration_hours`. An empty list turns the probe off. The earliest start depends on the duration, so the probe runs each duration. The limit of section 3.3, rule 1 is: (type, count) pairs x (`duration_hours` + `earliest_duration_hours` + 19) is 200 or fewer. The config check reports a larger plan. The calls wait in the queue of their Region (section 3.3, rule 6). The repo config sets `earliest_duration_hours: [24, 168]`: 7 x (4 + 2) = 42 calls per Region for `aws_cb_offering` and this probe, plus up to 133 worst case for `aws_cb_longest` (section 3B.5). Add no other `DescribeCapacityBlockOfferings` client in the account.

### 3A.4 Row mapping

The rows are those of section 3.2, with `probe_type` = `aws_cb_earliest`: 1 row per offering with the same raw `details` keys (`start`, `end`, `duration_hours`, `duration_minutes`, `instance_count`, `upfront_fee`, `currency`, `offering_id`, `tenancy`, `zone_type`, `ultraserver_type`, `ultraserver_count`) and `zone`. The `none` row and the `error` row have `details = {duration_hours}`. The `error` row has the raw code. A bad window gives `InvalidParameterValue` here. The probe does not filter or retry. `n_placed` is null.

The probe has no panel in the dashboard (section 7).

### 3A.5 `none` is the expected row while stock starts later than the window

The window of section 3A.2 admits only a start at `first` (`now` + 30 minutes) or at `boundary` (the next 11:30 UTC at or after `first`), that is, a start within about 25 hours (`boundary` is at most about 1.02 days after now). The end date is that start plus the block length plus 1 hour, so for 168 hours it is about 7 to 8 days after `now` (for 24 hours, 1.06 to 2.06 days after `now`), but the starts it admits are still only these 2. A block with a later start is outside the window, and the call returns no offering. On 2026-10-16 the store held 300 `aws_cb_earliest` rows: 30 series (5 types x 3 Regions x 2 durations, 24 and 168) x 10 runs = 300 rows, all `none` with no error. On the same day the `aws_cb_offering` and `aws_cb_longest` rows had a smallest lead of 1.7 days between `ts_utc` and the earliest `start`. One read-only check call (`p5.48xlarge`, 24 hours, us-east-1) with the window of section 3A.2 returned no offering. The same call with an end date 35 days later returned 1 offering that started on 2026-10-31. Thus `none` is the true answer ("no block starts at `first` or at `boundary`"), not a fault. The probe gives a `success` row only when the stock has a block that starts at `first` or at `boundary`. A reader that wants a later earliest start reads `aws_cb_longest` (`details.earliest_start`, section 3B.4). The probe does not widen its window: a wider window would answer the question of `aws_cb_offering`, not this one. See [the learning](../research/learnings/aws-capacity-block-earliest-window-found-no-start.md).

## 3B. `aws_cb_longest`: the first free day, and the longest block there

`aws_cb_earliest` (section 3A) answers "is a block of a given length for sale at the next possible start?" for the fixed durations of `earliest_duration_hours`. This probe asks a broader question, per Region and instance type: on the first day a block is for sale at all, what is the longest block that starts that day? It is free, like the other 2 Capacity Block probes.

### 3B.1 Evidence and design

Observed live with `DescribeCapacityBlockOfferings` on 2026-10-13 (188 calls, 0 errors, 0 throttles):

- The earliest start moves by weeks as the block length grows, and never moves earlier as the length grows, in every (Region, type) measured. The gallop and bisect of section 3B.2 depend on this: once a duration is confirmed at the earliest start `S`, a longer duration is tried before a shorter one is ruled out.
- A wide (56-day) window and a bisection agreed on the earliest start in 30 of 30 searches (19 on 2026-10-13, 11 on the previous run). AWS documents "lowest-priced", not "earliest", for a range search ([pricing and billing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-pricing-billing.html)). The probe does not rely on a single wide-window call to find `S`: it bisects on whether an offer exists at all inside a window that ends at a given day (section 3B.2), which is monotone in the window's end because `StartDateRange` is fixed at `now` and only `EndDateRange` grows.
- Stock changes within hours: a 1-day offer can disappear and a 14-day offer can appear between 2 calls a few hours apart. A search is a point-in-time snapshot of the queue drain, not a guarantee.

### 3B.2 Steps, request, and state

Each step is 1 `DescribeCapacityBlockOfferings` call, made with the Capacity Block client (1 botocore attempt; section 2.5). `InstanceType` is the type, `InstanceCount` is the count of the search (section 3.3). `StartDateRange` is `now` of the call. `B0` = the next 11:30 UTC at or after `now` + 30 minutes (`next_boundary`, section 3A.2) of the search's first call (`earliest.0`), fixed for the whole search and carried in `Call.args`, not recomputed at each call: a fresh `B0` on every step would move the earliest-bisect window (step 2) by a day whenever `now` crosses 11:00 UTC between 2 steps of the same search, which breaks the bisection's monotone assumption.

The search runs on the call queue (spec 0003, section 6A): `aws_cb_longest` has a `plan`, and each step's `run_call` returns a row whose `details["_next"]` holds the `Call` of the next step, or no such key when the search is done. The scheduler's `follow_up` hook reads only that key (spec 0003, section 6A, rule 5): the probe instance keeps no state between calls, and a search's whole state, the type, the current phase, the phase's own small state, the earliest offer found so far, the longest offer found so far, and the steps taken, travels in `Call.args`.

A call can return more than 1 offering (up to 6; section 3.3). An offering with no `StartDate` answers neither phase's question and is dropped before either rule below runs; with none left, the step reads as `none`, the same as an empty result. The earliest phases (1 and 2 below) ask "what is the earliest start in this window", so they take the offering with the smallest `StartDate`, not simply the first of the list: the API's order is by price (section 3A), not by date. The gallop and bisect phases (3 and 4) ask "is there still an offer at `S`", so they take an offering that is **at `S`** if 1 exists; with none at `S`, the step is a stop (section 3B.2, steps 3 and 4) and its own `result` reads `none`, not `offer`, even though the call did return 1 or more offerings.

**At `S`.** 2 starts are at `S` when the next 11:30 UTC boundary at or after each (`B0` of that moment, section 3A.2) falls on the same calendar day: the same side of the same boundary. This is not exact equality, because AWS's "first" kind of start (now + 30 minutes, not an 11:30 UTC boundary; section 3A.1) is a moving target: a later call's own "first" offering is later than an earlier call's, simply because its `now` is later, even though both are the earliest possible start of the same (Region, type) and so the same target for the gallop and bisect. Comparing `StartDate` for exact equality would read this drift as a different start and stop the gallop or bisect too early, every time `S` happens to be a "first" start rather than an 11:30 boundary.

Because of this drift, when `S` is a "first" start, a gallop or bisect step confirmed as "at `S`" can have its own `StartDate` later than `S` itself: AWS can round a "first" request up to the next 11:30 UTC boundary, the same way `aws_cb_earliest` observes (section 3A.1), even though the offer is still the same target by the rule above. The longest confirmed block can thus start at a different instant than the earliest block does. `details.longest_start` (section 3B.4) carries the longest offer's own raw `StartDate`, so a reader sees where it really starts, instead of assuming it is `earliest_start`.

1. **Earliest, step 0** (`purpose = "earliest.0"`). `CapacityDurationHours` = 24. `EndDateRange` = the `search_window` end of `aws_cb_earliest` for 24 h (section 3A.2). An offer gives `S` = its `StartDate` (the smallest, if more than 1): go to gallop (step 3) with the longest confirmed so far = this 24 h offer. No offer: go to step 2.
2. **Earliest, bisect** (`purpose = "earliest_bisect"`). Binary search for the smallest `j` in 1..56 (days) such that an offer exists with `EndDateRange` = `B0` + `j` days + 1 day + 1 hour (`CapacityDurationHours` stays 24). The search keeps `(lo, hi)`, days with no offer and with an offer, starting at `(0, 57)`; each step tests `j` = the midpoint. No offer narrows `lo` to `j`; an offer narrows `hi` to `j` and keeps that offer (the smallest `StartDate`, if more than 1). At most 6 steps (`log2(56)` rounded up). When `hi - lo <= 1`: `hi = 57` (the sentinel) means no offer in 1..56 days, and the search ends with `outcome = none`; else `S` = the kept offer's `StartDate`, and the search goes to gallop (step 3) with the longest confirmed so far = this 24 h offer.
3. **Longest, gallop** (`purpose = "gallop"`). With `S` known, try `D` in 2, 4, 8, 14, 28, 56, 112, 182 days in turn (the API's allowed durations: 1-day steps to 14 days, 7-day steps to 182, section 3.1), `CapacityDurationHours` = `D` x 24, `EndDateRange` = `S` + `D` days + 1 hour. An offer at `S` raises the longest confirmed to `D`; if `D` was 182, the search ends with `outcome = success`. No offer, or no offer at `S` among those returned, stops the gallop: if the next allowed duration above the longest confirmed is the `D` that just failed (no allowed duration strictly between them), the search ends with `outcome = success` at the duration already confirmed; else go to step 4.
4. **Longest, bisect** (`purpose = "bisect"`). Binary search, over the allowed durations between the longest confirmed and the `D` that stopped the gallop, for the longest duration with an offer at `S`. At most 4 steps: the gallop sequence's widest gap, 112 to 182 days, spans 10 allowed durations and needs `ceil(log2(10))` = 4; its narrowest gaps need 1 to 3. Same request shape as step 3, with the midpoint duration. An offer at `S` raises the longest confirmed; otherwise the upper bound narrows. When the bounds meet, the search ends with `outcome = success`.

A search never makes more than 19 calls (`MAX_STEPS` in `cb_longest.py`), the true worst case of the 4 phases: 1 + 6 + 8 + 4 (the widest gap of the gallop sequence, between 112 and 182 days, needs 4 bisect steps, not the 3 of the narrowest gap). The cap is defensive, not an expected path: a search that reaches 19 calls has already run every phase to its own end, so the cap never cuts a call a phase still needed, and the reported `earliest` and `longest` are never short of what the search would have found without it.

### 3B.3 Throttle, error, and the write-no-row rule

A throttled step (`RequestLimitExceeded`) gives an `error` row with that code and the full `details` of section 3B.4 (`earliest`, `longest`, and `steps`) built from the search's state as it stood before this attempt: a throttled attempt is not a step, so `steps` does not grow and the phase does not advance. The scheduler's throttle check (spec 0003, section 6A, rule 4) runs on this row before `follow_up` is called, so it is never passed to `follow_up` either way. Before `aws.queue_max_throttle_attempts` attempts, the scheduler puts the row's call back on the queue with backoff and writes no row for it. On the last allowed attempt, the scheduler writes this same row: the search ends there, with a full `error` row that keeps the `earliest` start and the longest duration already confirmed, not an empty one. The next cycle's `plan` call restarts the search from `earliest.0`.

Any other error ends the search at once: `outcome = error`, `error_code` = the raw code, and `details.steps` includes the failed step. The search makes no more calls for that (Region, type) in the cycle; the next cycle's `plan` call restarts it from `earliest.0`.

A step that is not the end of a search returns 1 row with `outcome = none` and `details = {"_next": <the next Call>}`; this row is never persisted, because the scheduler writes no row for a call whose `follow_up` adds another call (spec 0003, section 6A, rule 5, amended by this probe). The last step of a search returns the 1 row with the final `outcome` and no `_next` key, which the scheduler does write. `--once` runs each search to its end with no queue, by calling `run_call` in a loop and following `details["_next"]` itself (`CapacityBlockLongestProbe.run`); `sim` mode writes 1 plausible final row per Region and type, like the other 2 Capacity Block probes (spec 0003, section 7).

### 3B.4 Row mapping

1 row per search, with `probe_type` = `aws_cb_longest`, `zone` = the zone of the earliest offering (null when none was found), `sku` = the instance type, `n_requested` = the count of the search (section 3.3), `outcome` = `success`, `none`, or `error`:

| `details` key | Value |
|---|---|
| `earliest_start`, `earliest_end` | `StartDate`, `EndDate` of the earliest offering, as `ts_utc` strings; null when none was found |
| `earliest_duration_hours`, `earliest_duration_minutes`, `earliest_offering_id`, `earliest_upfront_fee`, `earliest_currency` | `CapacityBlockDurationHours` (always 24), `CapacityBlockDurationMinutes` (raw; null when the offering has no such field), `CapacityBlockOfferingId`, `UpfrontFee` as a number, `CurrencyCode` of the earliest offering; null when none was found |
| `b0` | the search's `B0` (fixed at `earliest.0`, section 3B.2), as a `ts_utc` string |
| `longest_start`, `longest_duration_hours`, `longest_duration_minutes`, `longest_end`, `longest_offering_id`, `longest_upfront_fee` | the longest confirmed offer at `S`: its own raw `StartDate` (section 3B.2; not always equal to `earliest_start`), its duration in hours, `CapacityBlockDurationMinutes` (raw), `EndDate`, `CapacityBlockOfferingId`, `UpfrontFee` as a number; null when no offer was ever confirmed |
| `steps` | 1 object per call made: `purpose`, `duration_hours`, `start`, `end` (the request range), `result` (`offer`, `none`, or `error`), `start_date`, `end_date`, `zone`, `offering_id`, `upfront_fee`, `currency`, `duration_minutes` (raw `CapacityBlockDurationMinutes`), `error_code`, `ts` (when the call was made), `b0` |
| `calls` | the number of steps (API calls) the search made |
| `planned_at` | `ts_utc` when the search's first step (`earliest.0`) ran |

No derived value: no price per day, no "days until". Every `details` value above is a raw field of a `CapacityBlockOffering` or a raw request value, except `calls` (a count of the probe's own steps) and `planned_at` (a timestamp the probe itself sets).

### 3B.5 Plan and limits

`plan(config)` returns 1 `Call` per Region and (type, count) pair, each starting the search at `earliest.0` with no history and `Call.key` = (the instance type, the count) (spec 0003, section 6A, rule 1). `Call.args` holds the type and the count first. The limit of section 3.3, rule 1 counts the worst case of 19 calls per pair: pairs x 19, added to the `aws_cb_offering` and `aws_cb_earliest` calls, must be 200 or fewer. The repo config's 7 pairs give a worst case of 133 calls per Region for this probe (175 total with the other 2 probes), draining in about 88 minutes at 1 call per 30 seconds, inside the `2h` cadence.

A follow-up `Call` has other `args` than the `earliest0` call that `plan` makes, but the same `key`: if a search of 1 (Region, type) still runs when the next cadence cycle starts, the queue already has a call for that `key` (the search's own next step), so the new `earliest0` call is not added (spec 0003, section 6A, rule 1; section 3.3, rule 8). A search does not restart or double up mid-flight; it keeps running until it ends on its own.

The probe has no panel in the dashboard (section 7).

## 4. `aws_spot_score`: Spot placement score

### 4.1 Request

The probe makes 1 `GetSpotPlacementScores` call per Spot score request, in request order. The client is in the first Region of `regions`.

| Parameter | Value |
|---|---|
| `InstanceTypes` | the `instance_types` of the entry |
| `TargetCapacity` | `1` |
| `TargetCapacityUnitType` | `units` |
| `SingleAvailabilityZone` | `false` |
| `RegionNames` | all of `regions` |
| `NextToken` | from the previous page |

API facts ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html)):

- `RegionNames` takes up to 10 Regions. `TargetCapacityUnitType` takes `vcpu`, `memory-mib`, or `units`.
- With `SingleAvailabilityZone` = `false`, the response is a list of scored Regions.
- The response holds the scores of the top 10 Regions or Availability Zones, on a scale from 1 to 10.
- Each `SpotPlacementScore` item has `Region`, `AvailabilityZoneId`, and `Score` ([SpotPlacementScore](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_SpotPlacementScore.html)).

The config has 10 or fewer Regions, thus the top 10 hold all of them.

### 4.2 Row mapping

1 row per Region in `regions` per Spot score request. The Region rows of 1 entry share the latency of its call.

| Field | Value |
|---|---|
| `region` | the Region |
| `zone` | null |
| `sku` | the instance type. For an entry with 2 or more types, the types joined with a comma, in request order (spec 0003, section 4.1, rule 7). |
| `outcome` | `success` when the response has an item for the Region without `AvailabilityZoneId`; else `none` |
| `details.score` | `Score`, an integer 1 to 10 (spec 0003, section 4.4) |
| `details.instance_types` | the types of the entry, an array of strings |
| `details.request` | the entry `name`. Old rows (written before 2026-10-15) have this value under `details.panel`; a reader accepts both keys, and the prober writes only `request`. |
| `details.label` | the entry `label` |
| `details.control` | only on control rows (section 4.3) |

### 4.3 Controls

Spec 0002 section 4.2 names 3 controls. After all calls of a run, the probe writes `details.control` on each row of a control entry.

| Control entry | Rule | `details.control` |
|---|---|---|
| An entry with no GPU type and fewer than 3 types (the CPU control) | Its score must be 3 or lower. AWS scores a request of fewer than 3 types low, so this rule also covers a 2-type CPU entry. | `{"rule": "cpu_score_max", "max": 3, "ok": <bool or null>}` |
| An entry with no GPU type and 3 or more types (the CPU mix control) | Its score must be above 3. A score of 4 or more is always true. A score below 4 gives `ok` null, not false, when a single-type CPU request whose type is in the mix scores 1 in the same Region and run: the Region is short of that capacity, the low score is real Spot availability data, and the control says nothing. | `{"rule": "cpu_mix_min", "min": 4, "singles": {<type>: <score or null>}, "ok": <bool or null>}`. `singles` holds the score of each single-type request whose type is in the mix, so a null is auditable. |
| An entry with a GPU type and 2 or more types (the GPU mix control) | No single-type request whose type is in the mix may score above the mix, in the same Region. | `{"rule": "single_not_above_mix", "singles": {<type>: <score or null>}, "ok": <bool or null>}` |

1. `ok` is null when a score that the rule needs is missing in this run.
2. The repo request list compares `g5.xlarge` with the `g5-family` mix. The mix names `g5.xlarge`, `g5.2xlarge`, and `g5.4xlarge` (spec 0002, section 4.2). The single rows `g5-2x` and `g5-4x` are not in the list.
3. A failed control logs `spot_score.control_failed` with the request name, the Region, and the rule.
4. Each single-type request has a type of its own: config validation rejects two single-type entries of one instance type. So `singles` holds one score for each type, and `ok` does not depend on the order of the entries.

### 4.4 Configuration limit: the freeze

AWS can limit new request configurations within a 24-hour period if it detects patterns not associated with the intended use of the feature. A configuration used within the last 24 hours can still get a score ([User Guide, Spot placement score](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html)). The EC2 error list has `MaxConfigLimitExceededException` ([Error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html)). An AWS sample logs it as `MaxConfigLimitExceeded`, with no suffix ([research note](../research/aws-spot-placement-score-per-gpu-type.md)).

The probe keeps 2 values for the process lifetime:

- `live`: the names of the entries that got a successful response in this process.
- `frozen`: false at start.

Rules:

1. An error code that starts with `MaxConfigLimitExceeded` sets `frozen` to true.
2. On the first freeze, the probe logs `spot_score.frozen` with the request name and `live_configs` = the count of `live`.
3. The rows of that call have `outcome = error`, the raw code, and `details.frozen = true` and `details.live_configs` = the count.
4. While `frozen` is true, the probe skips each entry that is not in `live`. It makes no call and no row for that entry. It logs `spot_score.skipped`.
5. The freeze ends only when the process stops.

### 4.5 Other errors

1. An error code that starts with `TargetCapacityLimitExceeded` gives `outcome = cap` on each Region row of the entry, with the raw code. The EC2 error list has `TargetCapacityLimitExceededException` ([Error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html)).
2. Each other error gives `outcome = error` on each Region row of the entry, with the raw code.

## 5. `aws_spot_price`: Spot price history

### 5.1 Request

1 `DescribeSpotPriceHistory` call per Region, with all request GPU types.

| Parameter | Value |
|---|---|
| `StartTime` | `now` minus the cadence. `now` is `ctx.now()`. The cadence is `ctx.cadence("aws_spot_price")`, else `default_cadence`. |
| `EndTime` | `now` |
| `Filters` | `instance-type` = the request GPU types; `product-description` = `Linux/UNIX` |
| `MaxResults` | `1000` (not verified: the API reference gives no valid range for this action) |
| `NextToken` | from the previous page |

API facts ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeSpotPriceHistory.html)):

- With a start and an end time, the call returns the prices in that range. It also returns the last price change before the start time, which is the effective price at the start time.
- `instance-type` and `product-description` are filter names. `Linux/UNIX` is a valid `product-description` value.
- The `NextToken` of the response is an empty string or null when no more items exist.
- Each `SpotPrice` item has `AvailabilityZone`, `InstanceType`, `ProductDescription`, `SpotPrice` (a string), and `Timestamp` ([SpotPrice](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_SpotPrice.html)).

### 5.2 Row mapping

The probe groups the items by (`InstanceType`, `AvailabilityZone`). It drops items with another `ProductDescription`. For each group:

- A record is new when `Timestamp` >= `StartTime` (spec 0002, 4.8).
- The effective record is the latest record with `Timestamp` < `StartTime`.

| Case | Rows |
|---|---|
| 1 or more new records | 1 `success` row per new record, oldest first |
| No new record | 1 `none` row. With an effective record, `details.effective_price` and `details.effective_price_ts`; else empty `details`. |
| No new record, and the poll is the hourly poll (section 5.3), and an effective record exists | The `none` row, plus 1 `success` row from the effective record with `details.effective = true` |
| A request GPU type with no record in the Region | 1 `none` row with `zone` = null and empty `details` |
| The call fails | 1 `error` row per request GPU type, `zone` = null, the raw code |

Fields of a `success` row:

| Field | Value |
|---|---|
| `region`, `zone` | the Region of the call, `AvailabilityZone` |
| `sku` | `InstanceType` |
| `details.price` | `SpotPrice` as a number, USD per hour (spec 0003, section 4.4) |
| `details.price_ts` | `Timestamp` as a `ts_utc` string, the AWS record time (spec 0003, section 4.4) |
| `details.currency` | `USD`, the unit that spec 0003, section 4.4 binds. The `SpotPrice` data type has no currency field. |
| `details.product_description` | `ProductDescription` |
| `details.effective` | `true` only on the hourly effective row |

Rows are in request GPU type order, then zone name order.

### 5.3 The effective price: decision on an open item that spec 0006 no longer lists

The old page of spec 0006 left open whether the prober writes the price in effect at the start (that open item is gone from spec 0006; the board now reads the daily mean, spec 0006 section 5.4 item 5). Without it, a price that never changes has no `success` row.

Decision: the probe writes the effective price once per UTC hour, as a `success` row with `details.effective = true`.

Reading the rows (2026-10-16): AWS writes a price record about every 6 hours per zone ([the learning](../research/learnings/aws-spot-price-records-were-on-the-hour-in-our-window.md)), so most 5-minute polls find no new record and write `none`. The store held 87 `success` rows in each UTC hour of that day (1 for each (type, zone) series). A reader that takes only the last row of a series (`make probes/status`) sees `none` most of the time. This is correct and not a fault: read the `success` rows, or `details.effective_price` of a `none` row, for the price.

1. The hourly poll is the poll with `StartTime` < `HH:00:00` <= `now` for some full UTC hour `HH`. With polls at the cadence, 1 poll per hour matches.
2. The rule uses only `StartTime` and `now`. Thus the probe keeps no state (spec 0002, D13).
3. The old dashboard bucket of 1 hour is gone (spec 0006 now takes the daily mean of the rows with a price, section 5.4 item 5). Each UTC hour still gets a `success` row, so a price series has a row even when the price never changes.
4. A reader that counts price changes must skip rows with `details.effective = true`.

Options not taken: once at process start, because it needs state; once per day, because a new deploy shows no price for up to 24 hours.

## 6. `aws_spot_advisor`: Spot Instance Advisor feed

### 6.1 Request

1 HTTPS GET of `https://spot-bid-advisor.s3.amazonaws.com/spot-advisor-data.json` per call, with a timeout of 30 seconds. The Spot Instance Advisor page loads this file. A search found no AWS document for it (spec 0002, 4.8).

1. The HTTP client is injectable: `SpotAdvisorProbe(http=...)`. It is any object with `get(url, timeout=...)` that returns `status_code` and `content`. The default is the `requests` module, imported on the first call.
2. `latency_ms` is the time of the GET. All rows of 1 call share it.
3. The cadence is `24h` (spec 0002, D2). The scheduler runs the probe at that cadence. The probe has no timer of its own.

### 6.2 Feed format

The probe reads 2 top-level keys. This is the format of the file on 2026-10-11 (author test):

- `ranges`: a list of objects with `index` (0 to 4), `label` (`<5%`, `5-10%`, `10-15%`, `15-20%`, `>20%`), and `max`, a number.
  - In the file of 2026-10-12, the `max` values are 5, 11, 16, 22, and 100 (author test). They differ from the upper numbers of the labels: 5, 10, 15, and 20.
  - No AWS document defines `max`. This spec does not say what it means.
- `spot_advisor`: an object keyed by Region, then by OS (`Linux`, `Windows`), then by instance type. Each value has `r` and `s`. The author test reads `r` as the range index and `s` as the savings in percent. No AWS document defines the 2 keys.

The ranges match the interruption ranges of the Spot Instance Advisor (spec 0002, 4.8).

### 6.3 Row mapping

1. Fetch. When the GET raises or the status is not 200, the probe returns the error rows of rule 5.
2. Parse. The body must be JSON with `spot_advisor` (an object) and `ranges` (a list of `index` and `label`). Else the probe returns the error rows of rule 5.
3. Raw object. After the parse, the probe writes the body with `ctx.put_raw("aws_spot_advisor", body)`. The key is that of spec 0002, 4.8: `experiment=<name>/provider=aws/probe_type=aws_spot_advisor/date=<YYYY-MM-DD>/<ts>_raw_<uuid>.json` (ADR 0011 section 6, replacing the earlier `run_mode=<m>/`).
4. Rows. 1 row per Region in `regions` per request GPU type, from the `Linux` values:

| Case | `outcome` | `details` |
|---|---|---|
| The type is in the feed with a valid `r`, a string label, and a number `s` | `success` | the keys of the table below |
| The Region or the type is not in the feed | `none` | empty |
| The entry has an `r` with no range, a label that is not a string, or no number `s` | `error`, `error_code = FeedFormat` | empty |

The `details` of a `success` row ([ADR 0006](../decisions/0006-store-raw-provider-values-normalize-separately.md), rule 3):

| Key | Type | Value |
|---|---|---|
| `interruption_range` | string | the `label` of the range with `index` = `r`, as returned |
| `interruption_range_min_pct` | number or null | the lower bound of the label, from the table below |
| `interruption_range_max_pct` | number or null | the upper bound of the label, from the table below; null for an open bound |
| `feed_max_pct` | number or null | the `max` of the range, as returned; null when the range has no number `max` |
| `savings_pct` | number | `s` |
| `range_index` | integer | `r` |

The label bounds come from this table. A label that is not in the table gives a `success` row with the label as returned. Its label bounds are null.

| Label | `interruption_range_min_pct` | `interruption_range_max_pct` |
|---|---|---|
| `<5%` | 0 | 5 |
| `5-10%` | 5 | 10 |
| `10-15%` | 10 | 15 |
| `15-20%` | 15 | 20 |
| `>20%` | 20 | null |

**Rule 5, error rows.** 1 row per request GPU type per Region, `outcome = error`, `zone` = null. `error_code` is the exception class name for a failed GET, `HTTP<status>` for a status that is not 200, and `FeedFormat` for a body that does not parse.

All rows have `zone` = null and `sku` = the type.

## 7. Dashboard contract

The `details` keys of [spec 0003](0003-prober-core-spec.md), section 4.4, and where this spec writes them:

| `probe_type` | Keys of spec 0003 | Section |
|---|---|---|
| `aws_spot_score` | `score` (integer 1 to 10), `instance_types` (array of strings) | 4.2 |
| `aws_spot_price` | `price` (number, USD per hour), `price_ts` (string, the AWS record time) | 5.2 |
| `aws_spot_advisor` | `interruption_range` (`<5%`, `5-10%`, `10-15%`, `15-20%`, `>20%`, or another string as returned), `savings_pct` (number) | 6.3 |
| `aws_cb_offering` | `start`, `end`, `duration_hours`, `upfront_fee` | 3.2 |
| `aws_cb_earliest` | none. No panel reads this probe yet. | 3A.4 |
| `aws_cb_longest` | none. No panel reads this probe yet. | 3B.4 |

The dashboard reads the label `interruption_range`. It reads no numeric bound of the range. A reader that needs a number must use the label bounds, not `feed_max_pct` (decision A18).

## 8. Tests

Tests are under `prober/tests/aws/`. They use hand-written fakes: a fake EC2 client with a queue of answers per operation, a fake `ClientError` with the `response` shape, and a fake HTTP client. No test uses the network or a credential.

Each probe has tests for: the request parameters, the happy path, the empty result, each named error code, and the `NextToken` loop. More tests:

1. The request list freeze: the first run sets `frozen` and logs the count. The next run skips the entry with no score.
2. The controls: a pass, a fail, and a missing score, for the CPU control, the CPU mix control, and the GPU mix control.
3. The hourly effective price, and the hour rule on 24 polls.
4. The feed cadence: a scheduler with a fake clock runs the Spot Advisor probe 2 times in 47 hours.
5. The loader finds the 6 probes with the cadences of the table above.
6. The Capacity Block request plan: the repo plan has 5 calls per Region, and a run makes only the calls of the plan. The queue is tested in `prober/tests/test_callqueue.py` (spec 0003, section 6A). A throttled call and `CapacityBlockDescribeLimitExceeded` each give 1 `error` row, and the other calls still run.
7. The Spot Advisor bounds: a recorded copy of the feed in `prober/tests/aws/fixtures/` gives the label bounds and `feed_max_pct` in different keys. An unknown label gives a `success` row with null label bounds.
8. `aws_cb_earliest` with a fake client: an offering, no offering, an error code, and the exact request. The window is tested with fixed clocks before 11:00, at 11:00, between 11:00 and 11:30, just after 11:30, and for a duration of less than 24 hours. The earliest calls count toward the limit.
9. `test_api_shapes.py` checks each request against the botocore EC2 model. botocore comes with boto3 in the `aws` extra, so `make prober/test` skips it. Run it with `uv run --extra aws pytest tests/aws`.
10. `aws_cb_longest` (`prober/tests/aws/test_cb_longest.py`) with a fake client and a fixed clock: an offer at `earliest.0` that skips straight to the gallop; the earliest bisection finding the smallest `j` with an offer; `none` when no offer exists in 1 to 56 days; the gallop running to 14 days and stopping at 28; the gallop stopping sooner and the bisect narrowing between the bounds, with the exact longest duration; an error mid-search, with the raw code and the steps so far kept; the 19-call cap, keeping the offer of the capping call itself; a throttled step put back by the queue, excluded from `details.steps` and `details.calls`; the 5th throttle writing a full error row that keeps the earliest start, the zone, and the steps so far; a new cycle's `plan` not duplicating a search already in the queue; `B0` not moving when `now` crosses 11:00 UTC between 2 bisect steps of the same search; the gallop not stopping when a "first" `S` drifts later between 2 calls, still at `S` by section 3B.2's rule; the earliest phases picking the smallest `StartDate` of more than 1 offering, not `items[0]`; the gallop picking an offering at `S` among more than 1, even when it is not the first of the list; an offering with no `StartDate` not raising and not being read as an offer; a gallop stop reading `result = "none"`, not `"offer"`, when the call did return an offering but none at `S`; and the exact request arguments (`CapacityDurationHours`, `StartDateRange`, `EndDateRange`) of an `earliest.0`, an `earliest_bisect`, a `gallop`, and a `bisect` step. The queue's `Call.key` dedupe is tested in `prober/tests/test_callqueue.py` (spec 0003, section 6A); the config check's limit matches the probe's own cap in `prober/tests/test_config.py`. `prober/tests/test_sim.py` checks that the `sim` row of each outcome (`success`, `none`, `error`) has the same `details` keys as a real row from the fake client.

## 9. Decisions

Decisions of this spec that an earlier build plan and spec 0002 do not state.

| # | Decision | Reason |
|---|---|---|
| A1 | `sku` of a mix entry joins the types with a comma, not `+` as an earlier build plan said. | Spec 0003, section 4.1, rule 7 and the core `sim` generator use a comma. Real and simulated rows must match. |
| A2 | The request GPU types include a type that is only in a mix entry. The repo request list has none now, but the rule stays. | Spec 0002, 4.8 says "the GPU types of the §4.2 request list". The core `sim` generator uses single-type entries only (spec 0003, section 7.2). |
| A3 | The effective price is a `success` row with `details.effective = true`, once per UTC hour (section 5.3). | Closes the open item of the old page of spec 0006 (gone from spec 0006), with no state. |
| A4 | A request GPU type with no record in a Region gives 1 `none` row with `zone` = null. | Absence is data. The row shows that the probe asked. |
| A5 | `EndTime` = `now` in the Spot price call. | The next window starts at its own `now` minus the cadence, so a record is not counted 2 times. |
| A6 | `EndDateRange` = `now` + the start window + the duration. | `EndDateRange` limits the end date, not the start date. |
| A7 | `n_placed` is null on Capacity Block rows. | An offering places nothing. The core `sim` generator also sets it to null. |
| A8 | The `none` and `error` rows of `aws_cb_offering` carry `duration_hours` and `start_window_days`. | A reader can tell which query gave no offering. |
| A9 | The freeze keeps the entries with a score in this process. It skips the others with no row. | Spec 0002, 4.2 says to freeze the request list. Rows for skipped entries would not come from AWS. |
| A10 | The GPU mix control compares each single-type request whose type is in the mix. | It holds `g5.xlarge` of spec 0002. A mix that names more single-type rows applies the same reason to each. |
| A11 | The error code of an exception that is not a `ClientError` is its class name. | The row keeps a code that a reader can group. |
| A12 | The probes add no retry. The next call at the cadence is the retry. | A retry inside a call hides the failure and delays the other probes. |
| A13 | The Spot placement score client is in the first configured Region. | The call scores the Regions of `RegionNames`, not the Region of the client. |
| A14 | The raw feed object is written only after the body parses. | A broken body would be stored under a `.json` key. |
| A15 | The Spot Advisor GET has a timeout of 30 seconds. | The feed is about 1 MB. A hung call must not block the loop. |
| A16 | The config check rejects more than 200 Capacity Block calls per Region (section 3.3). | The queue drains 1 call per 30 seconds in a Region, so 200 calls take 100 minutes, inside the `2h` cadence. One bucket per Region, shared by both probes, in the prober process (section 3.3, rule 6). Other clients in the account can still drain the real bucket, which then gives an `error` row. |
| A17 | The probe makes each call of the plan after `CapacityBlockDescribeLimitExceeded` (section 3.4). | Each run then shows the gate in each Region. A skip would hide the end of the gate. |
| A18 | The row stores the label bounds and the feed `max` in different keys. A reader that needs a number uses the label bounds. | The label is the range that the Advisor page shows. No document defines `max`. ADR 0006 keeps both. |
| A19 | The lower bound of `<5%` is 0, not null. | A rate is never below 0, so the range has no open lower bound. |
| A20 | An unknown label gives a `success` row. An `r` with no range still gives an `error` row. | ADR 0006 keeps an unknown string. An `r` with no range has no label to keep. |
| A21 | `aws_cb_earliest` shares the call, the row mapping, and the plan limit with `aws_cb_offering`, and has its own `probe_type` and cadence key (section 3A). | The 2 probes ask 2 questions of 1 API. The shared bucket of section 3.3 is the limit. |
| A22 | The window ends at the next 11:30 UTC boundary + the duration + 1 hour, and at least 1 day after `now` + 30 minutes (section 3A.2). | It admits the start AWS allows and the next boundary, and no later start. The observed rounding of section 3A.1 is not documented. |

Implementer's choice: the log event names other than those in sections 4.3 and 4.4; the extra `details` keys `offering_id`, `product_description`, `request`, and `label`; the module `common.py` for the shared helpers.

## 10. Open

- Open: P9 and P12 of spec 0002 test the GPU scores and the `p6-b300.48xlarge` price coverage in the rig's account.
- Access to Capacity Blocks for the account of the rig, section 3.4: an account without access gets the error of this section. Check it on each new account.

## 11. Sources

- [AWS EC2 API reference, DescribeCapacityBlockOfferings](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html)
- [AWS EC2 API reference, CapacityBlockOffering](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityBlockOffering.html)
- [AWS EC2 API reference, GetSpotPlacementScores](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html)
- [AWS EC2 API reference, SpotPlacementScore](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_SpotPlacementScore.html)
- [AWS EC2 API reference, DescribeSpotPriceHistory](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeSpotPriceHistory.html)
- [AWS EC2 API reference, SpotPrice](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_SpotPrice.html)
- [AWS EC2 API reference, error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html)
- [Amazon EC2 User Guide, Spot placement score](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html)
- [Amazon EC2 User Guide, Find and purchase Capacity Blocks](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-purchase.html)
- [Amazon EC2 Developer Guide, Request throttling for the Amazon EC2 API](https://docs.aws.amazon.com/ec2/latest/devguide/ec2-api-throttling.html)
- [Service Authorization Reference, EC2 service reference JSON](https://servicereference.us-east-1.amazonaws.com/v1/ec2/ec2.json)

Sources of single facts in the text:

- [Response list key `CapacityBlockOfferings`; offering fields AvailabilityZone, InstanceType, StartDate, EndDate, CapacityBlockDurationHours, InstanceCount, UpfrontFee, CurrencyCode, CapacityBlockOfferingId](https://docs.aws.amazon.com/boto3/latest/reference/services/ec2/client/describe_capacity_block_offerings.html)
- [IAM actions ec2:DescribeCapacityBlockOfferings, ec2:GetSpotPlacementScores, ec2:DescribeSpotPriceHistory exist for EC2](https://awspolicygen.s3.amazonaws.com/js/policies.js)
- [IAM actions ec2:DescribeCapacityBlockOfferings, ec2:GetSpotPlacementScores, ec2:DescribeSpotPriceHistory exist for EC2](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/sps-iam-permission.html)
