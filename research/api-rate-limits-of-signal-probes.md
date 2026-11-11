---
title: "API rate limits of the signal probes"
date: 2026-10-12
---

# API rate limits of the signal probes

## Summary

This note gives the rate limits of each API that the signal probes call, with a request budget for an experiment.

- No probe is near a rate limit at the cadences in `config/targets.yaml`.
- The `aws_cb_offering` probe can fail on each call without a rate limit. The error `CapacityBlockDescribeLimitExceeded` comes with HTTP 400 and this message: "Your existing service limits for this AWS account are not sufficient to use EC2 Capacity Blocks." A "Concurrent ... Capacity Blocks per account" quota of 0 is a likely cause (see below).
- The tightest limit for a Spot score experiment is not a rate. AWS can limit new Spot placement score configurations in 24 hours when it detects use outside the intended purpose. AWS gives no number for it.
- The Azure Spot Placement Score API counts as an Azure Resource Manager (ARM) write. Its response carries the subscription write headers, not the read headers.
- 1 `DescribeSpotPriceHistory` call with a 5-minute time window and 7 instance types sent 35 requests, because the API returns sparse pages with a `NextToken`.

## Scope and method

The probes and their cadences come from [config/targets.yaml](../config/targets.yaml) and the prober code in [prober/](../prober/README.md). The note covers the APIs below.

- AWS: `GetSpotPlacementScores`, `DescribeSpotPriceHistory`, `DescribeCapacityBlockOfferings`, and the Spot Instance Advisor data file.
- Azure: the Spot Placement Score API, ARM reads, the Microsoft.Compute throttling policies, Azure Resource Graph, and the Azure Retail Prices API.

Status of each value:

- **Documented**: an AWS or Microsoft page states it.
- **Observed**: a call showed it. The evidence is given with the value.
- **Reported**: only a third-party page or a forum answer states it.

## AWS

### EC2 API throttling, all actions

- **Algorithm and scope (documented).** "Amazon EC2 throttles EC2 API requests for each AWS account on a per-Region basis." "Amazon EC2 uses the token bucket algorithm to implement API throttling." A set number of tokens "is added back to it every second". ([EC2 API throttling](https://docs.aws.amazon.com/ec2/latest/devguide/ec2-api-throttling.html))
- **Default buckets (documented).** Non-mutating actions (`Describe*`, `List*`, `Search*`, `Get*`): maximum 100, refill 20 per second. Unfiltered and unpaginated non-mutating actions: 50 and 10. Mutating actions: 50 and 5. ([EC2 API throttling](https://docs.aws.amazon.com/ec2/latest/devguide/ec2-api-throttling.html))
- **Error (documented).** "If you exceed an API throttling limit, you get the `RequestLimitExceeded` error code." ([EC2 API throttling](https://docs.aws.amazon.com/ec2/latest/devguide/ec2-api-throttling.html)) The error codes page describes it as "The maximum request rate permitted by the Amazon EC2 APIs has been exceeded for your account." ([error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html))
- **HTTP status.** The error codes page lists `RequestLimitExceeded` in the server error table, and says that server errors come with a 500-series status. The same page also names it `Client.RequestLimitExceeded`. The botocore retry file expects HTTP 503. ([error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html), [botocore _retry.json](https://github.com/boto/botocore/blob/1.43.111/botocore/data/_retry.json))
- **Headers.** The throttling page documents no response header that reports the remaining tokens. The `DescribeCapacityBlockOfferings` response had no such header (observed).
- **Per-action values in Service Quotas (documented).** Service Quotas shows the bucket of each action with the quota names "{API_NAME} request bucket maximum capacity" and "{API_NAME} request bucket refill rate". ([EC2 API throttling](https://docs.aws.amazon.com/ec2/latest/devguide/ec2-api-throttling.html))
- **Increase (documented).** "You can request an increase for API throttling limits for your AWS account." ([EC2 API throttling](https://docs.aws.amazon.com/ec2/latest/devguide/ec2-api-throttling.html))

### Per-API table

| API | Limit | Scope | Algorithm | Throttle error | Remaining-quota header | Status |
|---|---|---|---|---|---|---|
| `GetSpotPlacementScores`, rate | Bucket 100, refill 20 per second | Account and Region | Token bucket | `RequestLimitExceeded` | None | Observed in Service Quotas. The docs do not name the action; it matches the `Get*` category. |
| `GetSpotPlacementScores`, configurations | No number | Not stated | Not a rate. New configurations in a 24-hour period. | `MaxConfigLimitExceededException` | None | Documented |
| `GetSpotPlacementScores`, target capacity | "based on your recent Spot usage" | Account | Not a rate | `TargetCapacityLimitExceededException` | None | Documented |
| `DescribeSpotPriceHistory` | Bucket 100, refill 20 per second | Account and Region | Token bucket | `RequestLimitExceeded` | None | Observed in Service Quotas. The docs do not name the action; it matches the `Describe*` category. |
| `DescribeCapacityBlockOfferings`, rate | Bucket 10, refill 0.15 per second | Account and Region | Token bucket | `RequestLimitExceeded` | None | Documented, and the same in Service Quotas |
| `DescribeCapacityBlockOfferings`, account | Account must have Capacity Block limits | Account | Not a rate | `CapacityBlockDescribeLimitExceeded`, HTTP 400 | None | Documented code; status and message observed |
| Spot Advisor data file | At least 5,500 GET or HEAD per second per S3 prefix | S3 prefix | Not stated | HTTP 503 "Slow Down" | None | Documented for S3. No limit is documented for the file. |

### GetSpotPlacementScores

- **Configuration limit (documented).** "We can limit the number of new request configurations within a 24-hour period if we detect patterns not associated with the intended use of the Spot placement score feature. If you reach the limit, you can retry the request configurations that you've already used, but you can't specify new request configurations until the next 24-hour period." The page gives no number. ([Spot placement score](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html))
- **How many configurations (reported).** The AWS tracker sample README says "a few (10) diversified configurations". The 2022 SpotLake paper reports a limit of 50 per account. ([tracker README](https://github.com/aws-solutions-library-samples/guidance-for-ec2-spot-placement-score-tracker), [SpotLake paper](https://arxiv.org/abs/2202.02973))
- **Target capacity limit (documented).** "Your Spot placement score target capacity limit is based on your recent Spot usage, while accounting for potential usage growth. If you have no recent Spot usage, we provide you with a low default limit aligned with your Spot request limit." ([Spot placement score](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html))
- **Errors (documented).** `MaxConfigLimitExceededException`: "You've exceeded your maximum allowed Spot placement configurations. You can retry configurations that you used within the last 24 hours, or wait for 24 hours before specifying a new configuration." `TargetCapacityLimitExceededException`: "The value for targetCapacity exceeds your limit on the amount of Spot placement target capacity you can explore." Both are in the client error table, which uses 400-series status codes. ([error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html))
- **Request size (documented).** `RegionNames`: up to 10. `InstanceTypes`: up to 1,000. `MaxResults`: 10 to 1,000. `TargetCapacity`: 1 to 2,000,000,000. ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html))

### DescribeSpotPriceHistory

- **History (documented).** The start time can go "up to the past 90 days". The response includes "the last price change before the start time". ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeSpotPriceHistory.html))
- **Pages (observed).** A call with 7 instance types, the `Linux/UNIX` product filter, a 5-minute window, and `MaxResults` 1000 returned sparse pages. The boto3 client followed `NextToken` through 35 pages to get 33 items, and most pages had 0 to 3 items. Thus 1 probe call can cost about 35 tokens, not 1.

### DescribeCapacityBlockOfferings

- **Rate (documented).** The "Uncategorized actions" table lists `DescribeCapacityBlockOfferings` with bucket maximum capacity 10 and refill rate 0.15. ([EC2 API throttling](https://docs.aws.amazon.com/ec2/latest/devguide/ec2-api-throttling.html))
- **The live error is a different limit.** `CapacityBlockDescribeLimitExceeded` is in the client error table: "You've reached the limit for this account. The returned message provides details." ([error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html)) A live check got HTTP 400 with this message: "Your existing service limits for this AWS account are not sufficient to use EC2 Capacity Blocks. Contact AWS Support if you have any questions." botocore did not retry it (observed).
- **Likely cause (not documented).** On an account where the "Concurrent ... Capacity Blocks per account" quotas are 0, the AWS default is also 0. AWS does not document that a quota of 0 blocks the describe call. On such an account, the `error` rows stay as the signal.
- **Request limits (documented).** `MaxResults`: 1 to 1,000. Durations: "in 1-day increments up 14 days, and in 7-day increments up to 182 days". Capacity Blocks can start "up to 8 weeks in the future". The response has "up to 6 available blocks". ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html), [Capacity Blocks](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html), [find and purchase](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-purchase.html))
- **A sample retries it.** The AWS sample `sample-capacity-block-offering-search` retries on `CapacityBlockDescribeLimitExceeded` with a wait of 20 seconds, and on `RequestLimitExceeded` with a wait of 10 seconds. ([query_capacity.py](https://github.com/aws-samples/sample-capacity-block-offering-search/blob/main/backend/query_capacity.py))

### Spot Instance Advisor data file

- **No documented limit.** AWS documents no API or rate limit for `spot-advisor-data.json`. The file is an S3 object.
- **S3 request rate (documented).** "your application can achieve at least 3,500 PUT/COPY/POST/DELETE or 5,500 GET/HEAD requests per second per partitioned Amazon S3 prefix." During scaling, "you may see some 503 (Slow Down) errors". ([S3 performance](https://docs.aws.amazon.com/AmazonS3/latest/userguide/optimizing-performance.html))
- **Freshness (observed).** The file had `Last-Modified: Fri, 20 Mar 2026 23:38:55 GMT` and no `Cache-Control` header. The JSON has no timestamp field. A daily fetch is more than enough.

## Azure

### Azure Resource Manager

- **Algorithm and scope (documented).** "Throttling limits are now applied per region rather than per instance of Azure Resource Manager. This new architecture uses a token bucket algorithm to manage API throttling." "The subscription limits apply per subscription, per service principal, and per operation type. There are also global subscription limits that are equivalent to 15 times the individual service principal limits for each operation type." ([ARM throttling](https://learn.microsoft.com/azure/azure-resource-manager/management/request-limits-and-throttling))
- **Buckets (documented).** Subscription reads: bucket 250, refill 25 per second. Subscription writes: 200 and 10. Subscription deletes: 200 and 10. Tenant reads: 250 and 25. Tenant writes and deletes: 200 and 10. "The limits might be smaller for free or trial customers." ([ARM throttling](https://learn.microsoft.com/azure/azure-resource-manager/management/request-limits-and-throttling))
- **Error (documented).** "you receive the HTTP status code 429 Too many requests. The response includes a Retry-After value". ([ARM throttling](https://learn.microsoft.com/azure/azure-resource-manager/management/request-limits-and-throttling))
- **Headers (documented).** `x-ms-ratelimit-remaining-subscription-reads`, `-writes`, `-deletes`, and `x-ms-ratelimit-remaining-tenant-reads`, `-writes`. ([ARM throttling](https://learn.microsoft.com/azure/azure-resource-manager/management/request-limits-and-throttling))
- **Global headers (observed).** The live responses also had `x-ms-ratelimit-remaining-subscription-global-reads: 3749` and `x-ms-ratelimit-remaining-subscription-global-writes: 2999`. These values are 1 below 15 times 250 and 15 times 200. The ARM page does not list these headers.

### Microsoft.Compute throttling policies

- **Algorithm and scope (documented).** "Microsoft Compute uses Token Bucket Algorithm." "All Compute throttling policies are implemented on a per-region basis." Most policies have a resource level and a subscription level. Some, for example VM High Cost Get, have only a subscription level. ([Compute throttling limits](https://learn.microsoft.com/azure/virtual-machines/compute-throttling-limits))
- **Header (documented).** `x-ms-ratelimit-remaining-resource`, with values such as `Microsoft.Compute/HighCostGet;159`. A request can get 1 header for each policy that applies. ([throttling errors](https://learn.microsoft.com/troubleshoot/azure/virtual-machines/windows/troubleshooting-throttling-errors))
- **Error (documented).** HTTP 429 with a `Retry-After` header. The body code is `OperationNotAllowed`, and the detail code is `TooManyRequests`. ([throttling errors](https://learn.microsoft.com/troubleshoot/azure/virtual-machines/windows/troubleshooting-throttling-errors))
- **Policies (documented).** The limits page gives per-minute values for VM and VMSS operations, for example VM High Cost Get (List): subscription refill 300 and maximum 900 per minute. It gives no policy for Resource SKUs, Usage, or Spot Placement Score. ([Compute throttling limits](https://learn.microsoft.com/azure/virtual-machines/compute-throttling-limits))
- **Live headers (observed).** The Usage list and the Resource SKUs list in eastus returned no `x-ms-ratelimit-remaining-resource` header. They returned only the ARM read headers (`subscription-reads: 249`).

### Spot Placement Score API

- **Request size (documented).** "lists of up to eight regions and five VM sizes". The OpenAPI spec of version 2025-06-05 has no `maxItems` on these lists. ([Spot Placement Score](https://learn.microsoft.com/azure/virtual-machine-scale-sets/spot-placement-score), [spec](https://github.com/Azure/azure-rest-api-specs/blob/main/specification/compute/resource-manager/Microsoft.Compute/Recommender/stable/2025-06-05/RecommenderRP.json))
- **Cache guidance (documented).** "We recommend that the placement scores for each combination of subscription, desired count, region, zone, and VM size are cached, to avoid calling the API with the same configuration frequently within a short period of time. The suggested cache TTL is a minimum of 15 minutes and a maximum of 30 minutes." ([Spot Placement Score](https://learn.microsoft.com/azure/virtual-machine-scale-sets/spot-placement-score))
- **Rate limit (documented, no number).** The status table has "429 | Too many requests | Unable to generate placement score due to hitting a rate limit." The page also says: "Spot Placement Score API internally calls other GET APIs and is part of your GET call quota." It gives no number. ([Spot Placement Score](https://learn.microsoft.com/azure/virtual-machine-scale-sets/spot-placement-score))
- **Counts as an ARM write (observed).** The API is a POST. The live response had `x-ms-ratelimit-remaining-subscription-writes: 199` and `x-ms-ratelimit-remaining-subscription-global-writes: 2999`. It had no read header and no `x-ms-ratelimit-remaining-resource` header. Thus the ARM write bucket (200, refill 10 per second) applies to each request.

### Azure Resource Graph

- **Quota (documented).** "Azure Resource Graph allocates a quota number for each user based on a time window. For example, a user can send at most 15 queries within every 5-second window without being throttled. The quota value is determined by many factors and is subject to change." ([Resource Graph throttling](https://learn.microsoft.com/azure/governance/resource-graph/concepts/guidance-for-throttled-requests))
- **Headers (documented).** `x-ms-user-quota-remaining` and `x-ms-user-quota-resets-after` (hh:mm:ss). ([Resource Graph throttling](https://learn.microsoft.com/azure/governance/resource-graph/concepts/guidance-for-throttled-requests))
- **Error (documented).** The error code is `RateLimiting`. The page does not give the HTTP status. ([Resource Graph throttling](https://learn.microsoft.com/azure/governance/resource-graph/concepts/guidance-for-throttled-requests))
- **Pages (documented).** "Azure Resource Graph returns a maximum of 1,000 entries in a single query response". Each page "takes one query quota". ([Resource Graph throttling](https://learn.microsoft.com/azure/governance/resource-graph/concepts/guidance-for-throttled-requests))
- **Live headers (observed).** After 1 query: `x-ms-user-quota-remaining: 14`, `x-ms-user-quota-resets-after: 00:00:05`, `x-ms-ratelimit-remaining-tenant-reads: 249`, and `x-ms-ratelimit-remaining-tenant-resource-requests: 14`. The quota of 15 per 5 seconds applied.

### Azure Retail Prices API

- **Access and pages (documented).** "This API gives you an unauthenticated experience to get retail rates for all Azure services." "a maximum of 1,000 records are returned" per request, with a next page link. The page gives no rate limit. ([Retail Prices API](https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices))
- **Live headers (observed).** A request returned HTTP 200 with `x-ms-ratelimit-remaining-retailprices-requests: 9` and `x-ms-ratelimit-retailprices-retry-after: 60`.
- **Throttle (reported).** In a 2023 Microsoft Q&A thread, a moderator answered on 2023-09-07: "If in case the throttling happens, you will be notified 429 status with a re-try time instead of giving empty response in future". A comment of 2023-09-15 said: "Kindly re-try the same page after 60 seconds till that fix go live". ([Q&A](https://learn.microsoft.com/answers/a/1312424))
- **Use.** The prober does not call this API today.

## Retries in the SDKs

The prober uses default SDK clients. The pinned versions are in [prober/uv.lock](../prober/uv.lock): botocore 1.43.111, azure-core 1.41.0, azure-mgmt-core 1.6.0, and requests 2.34.2.

- **boto3 default mode (documented and in code).** "Legacy mode is the default mode used by any Boto3 client you create." Legacy mode makes up to 5 attempts, including the first request. Standard mode makes up to 3. ([boto3 retries](https://docs.aws.amazon.com/boto3/latest/guide/retries.html), [args.py](https://github.com/boto/botocore/blob/1.43.111/botocore/args.py#L641-L644), [_retry.json](https://github.com/boto/botocore/blob/1.43.111/botocore/data/_retry.json#L93))
- **What legacy mode retries for EC2 (code).** HTTP 429, 500, 502, 503, 504, and 509 with any error code, and `RequestLimitExceeded` with HTTP 503. The delay before retry n is a random value from 0 to 2^(n-1) seconds: up to 1 second before retry 1, and up to 8 seconds before retry 4. ([_retry.json](https://github.com/boto/botocore/blob/1.43.111/botocore/data/_retry.json#L91-L185), [retryhandler.py](https://github.com/boto/botocore/blob/1.43.111/botocore/retryhandler.py#L43-L64))
- **What it does not retry (code and observed).** `CapacityBlockDescribeLimitExceeded` and `MaxConfigLimitExceeded` are not in botocore. EC2 returns `CapacityBlockDescribeLimitExceeded` with HTTP 400, so botocore makes 1 attempt. The live latency of these calls is 109 to 402 ms, with no retry delay (observed).
- **Retries are silent (code).** botocore logs retries only at DEBUG level. The caller sees retries only in `ResponseMetadata.RetryAttempts`. It is on a success and also on the `ClientError` of a failure, with `MaxAttemptsReached` when retries ran out. A connection error raises only the last exception. ([retryhandler.py](https://github.com/boto/botocore/blob/1.43.111/botocore/retryhandler.py#L276-L300), [endpoint.py](https://github.com/boto/botocore/blob/1.43.111/botocore/endpoint.py#L221-L229))
- **azure-core default RetryPolicy (code).** It retries status codes 408, 429, 500, 502, 503, 504, and 506 or higher, with at most 3 status retries. It retries a POST on a status code only for 500, 503, and 504, or when the response has a `Retry-After` header. It sleeps for the full `Retry-After` value. ([_retry.py](https://github.com/Azure/azure-sdk-for-python/blob/azure-core_1.41.0/sdk/core/azure-core/azure/core/pipeline/policies/_retry.py#L77-L94))
- **Resource Graph calls (code).** A `resources()` query is a POST. Thus a 429 with no `Retry-After` header is not retried. ([operations](https://github.com/Azure/azure-sdk-for-python/blob/azure-mgmt-resourcegraph_8.0.1/sdk/resources/azure-mgmt-resourcegraph/azure/mgmt/resourcegraph/operations/_resource_graph_client_operations.py#L59))
- **Header logs (code).** azure-mgmt-core logs the ARM headers `x-ms-ratelimit-remaining-subscription-*`, `-tenant-*`, and `-resource`, but not the `-global-reads` and `-global-writes` headers. It redacts these and the Resource Graph headers `x-ms-user-quota-remaining` and `x-ms-user-quota-resets-after`. ([azure-mgmt-core policies](https://github.com/Azure/azure-sdk-for-python/blob/azure-mgmt-core_1.6.0/sdk/core/azure-mgmt-core/azure/mgmt/core/policies/__init__.py#L40-L58))
- **Spot Placement Score calls (code).** The probe calls the API with `requests.post`. requests makes no retry by default (`DEFAULT_RETRIES = 0`). The probe itself waits 60 seconds after a 429 and tries 1 more time. ([adapters.py](https://github.com/psf/requests/blob/v2.34.2/src/requests/adapters.py#L81), [spot_score.py](../prober/ftg_prober/azure/spot_score.py))

## Request budget for a single-type signal experiment

These budgets are for the companion experiment in [Single GPU type signal from Spot score requests](single-gpu-type-signal-from-spot-scores.md). They are the room that is left after the probes of today. Each budget assumes the same accounts and the same principals as the prober.

| API | Binding limit | Used today | Budget for the experiment | Note |
|---|---|---|---|---|
| AWS `GetSpotPlacementScores`, configurations | New configurations each 24 hours. No number. Plan for 10 or fewer (reported). | 8 configurations each day | 2 new configurations each day, with no change to the request list | The docs do not define "configuration". Treat each change of instance types, target capacity, unit type, Regions, or `SingleAvailabilityZone` as a new configuration. A repeat of a configuration from the last 24 hours is always permitted. A ladder of 4 target capacities is 4 configurations. To run more, remove Spot score requests, or use a second account. |
| AWS `GetSpotPlacementScores`, rate | Bucket 100, refill 20 per second, us-east-1 | 8 calls each 5 minutes | Up to 90 calls in 1 burst, then up to 20 per second | The rate is not the binding limit. |
| AWS `GetSpotPlacementScores`, target capacity | "low default limit aligned with your Spot request limit" | Target capacity 1 unit | Not known. It follows the Spot quota of the account. | A ladder step above the limit gets `TargetCapacityLimitExceededException`. Record it as the result of that step; do not retry it. |
| AWS `DescribeCapacityBlockOfferings` | Bucket 10, refill 0.15 per second, each Region | 5 calls each 2 hours in each Region | 0 useful calls on an account without Capacity Block access, because each call fails with the account limit | After the account qualifies: up to 10 calls in each Region in a burst, then 1 call each 7 seconds. A grid of 5 types, 3 counts, and 2 durations is 30 calls in each Region, about 135 seconds with pacing (10 in a burst, then 20 at 1 each 6.7 seconds). |
| Azure Spot Placement Score | 15 to 30 minute cache TTL for each configuration; ARM writes bucket 200, refill 10 per second | 11 requests each 15 minutes | 1 request for each configuration each 15 minutes or more. Up to about 150 requests in 1 burst (inference: 200 less the probes and a margin). | Each request holds 1 `desiredCount` and 1 scope, up to 8 Regions, and up to 5 sizes. A ladder of 4 `desiredCount` values for 1 size, with 2 regional requests (9 Regions, 8 at most in each request) and 9 zonal requests (1 for each Region) for each step, is 44 requests each 15 minutes. Keep the 60-second retry after a 429, and record each 429. |
| Azure Resource Graph | 15 queries per 5 seconds per user | 2 queries each 5 minutes | 13 queries in each 5-second window | The experiment needs no new Resource Graph query. |
| Azure Retail Prices | Not documented; header shows 9 remaining requests and a 60-second retry | 0 | Not known. The headers suggest about 9 requests each 60 seconds, but a 2023 report showed the same values, so they can be fixed values. | Fetch 1 time each day, and wait 60 seconds after a 429 or an empty page. |

## Open items

- The size, key, and window of the Spot placement score configuration limit. [AWS Spot placement score configuration limit](aws-spot-placement-score-configuration-limit.md) collects the public evidence.
- The rate limit of the Azure Spot Placement Score API itself. Microsoft documents a 429 but no number. See the [Azure rate-limit note](azure-spot-placement-score-rate-limit.md).
- Whether a Capacity Block quota above 0 removes `CapacityBlockDescribeLimitExceeded`. An account without Capacity Block access gets the error on each call.

## Sources

- <https://docs.aws.amazon.com/ec2/latest/devguide/ec2-api-throttling.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeSpotPriceHistory.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-purchase.html>
- <https://docs.aws.amazon.com/AmazonS3/latest/userguide/optimizing-performance.html>
- <https://github.com/boto/botocore>
- <https://github.com/aws-solutions-library-samples/guidance-for-ec2-spot-placement-score-tracker>
- <https://github.com/aws-samples/sample-capacity-block-offering-search>
- <https://arxiv.org/abs/2202.02973>
- <https://learn.microsoft.com/azure/azure-resource-manager/management/request-limits-and-throttling>
- <https://learn.microsoft.com/azure/virtual-machines/compute-throttling-limits>
- <https://learn.microsoft.com/troubleshoot/azure/virtual-machines/windows/troubleshooting-throttling-errors>
- <https://learn.microsoft.com/azure/virtual-machine-scale-sets/spot-placement-score>
- <https://github.com/Azure/azure-rest-api-specs>
- <https://learn.microsoft.com/azure/governance/resource-graph/concepts/guidance-for-throttled-requests>
- <https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices>
- <https://github.com/Azure/azure-sdk-for-python>

Sources of single facts in the text:

- [Spot Advisor data file is an S3 object; no documented limit for it; Spot Advisor file: Last-Modified Fri, 20 Mar 2026 23:38:55 GMT; no Cache-Control](https://spot-bid-advisor.s3.amazonaws.com/spot-advisor-data.json)
- [Probe got scores for p5.48xlarge (192 vCPUs) at target capacity 1 unit](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html)
- [2023 Q&A answer: 429 with retry time; "re-try the same page after 60 seconds"](https://learn.microsoft.com/en-us/answers/a/1312424)
