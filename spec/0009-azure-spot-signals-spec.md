---
title: "Azure spot signals spec: Spot Placement Score and Resource Graph probes"
date: 2026-10-11
---

# Azure spot signals spec

**Status:** Phase 1 build · 2026-10-11
**Parent:** [spec 0002](0002-gpu-capacity-tracker-spec.md), sections 4.6, 7.2, 7.3, D2, and pilot P11. The core is [spec 0003](0003-prober-core-spec.md).
**Code:** `prober/ftg_prober/azure/spot_score.py`, `prober/ftg_prober/azure/spot_resource_graph.py`, `prober/tests/azure/spot_*`.
**Background:** [Azure Spot Placement Score per VM size](../research/azure-spot-placement-score-per-vm-size.md).

A person must be able to build the 2 Azure spot signal probes again from this file, spec 0002, and spec 0003. The probes are plugins of the prober core (spec 0003, section 5).

## 1. Scope and rules

1. Scope of the experiment: continental US commercial regions, GPU only. Spot signals are the core analyzed data (spec 0002, D1).
2. Each probe is 1 module and 1 `Probe` subclass. The module must not edit a core file (spec 0003, 5.1).
3. `run(ctx)` returns the rows of 1 call. It does not write them. The scheduler writes them as 1 object (spec 0003, 6).
4. The targets come from `ctx.config`, the `azure` section: `regions` and `spot_sizes` (spec 0003, 3).
5. The Spot Placement Score probe reads the subscription from `AZURE_SUBSCRIPTION_ID`, else `ARM_SUBSCRIPTION_ID` (spec 0003, 2). The Resource Graph probe is tenant-scoped and reads neither.
6. For a simulated job, the scheduler does not call these probes. It writes the synthetic rows of spec 0003 section 7.2. The probes have no simulated-path code of their own; the signal probes have no entry-level SKU distinction, so there is no `cloud-cpu`/`cloud-gpu` split to make here (spec 0003, 7.1).
7. A module imports `requests` and the Azure SDK inside a function, not at the top. Thus the core tests run without the `azure` extra.
8. The tests use fakes for the HTTP call, the token, and the Resource Graph client. They use no network and no credential.

| Class | `probe_type` | `provider` | `default_cadence` | Module |
|---|---|---|---|---|
| `SpotScoreProbe` | `az_spot_score` | `azure` | `15m` | `spot_score.py` |
| `SpotResourceGraphProbe` | `az_spot` | `azure` | `5m` | `spot_resource_graph.py` |

The cadences come from spec 0002 D2. The job `interval` sets the cadence. The config `cadence` keys `az_spot_score` and `az_spot` are rejected (`_cadence_conflict_faults` in `config.py`; [ADR 0010](../decisions/0010-one-experiment-file-of-modules-and-jobs-with-a-billable-guard.md)).

## 2. Spot Placement Score probe (`az_spot_score`)

### 2.1 Request

The probe calls the REST API with `requests`. The Python SDK is not used for this call.

```text
POST https://management.azure.com/subscriptions/{subscriptionId}/providers/Microsoft.Compute/locations/{location}/placementScores/spot/generate?api-version=2025-06-05
Authorization: Bearer <token>
Content-Type: application/json
```

The REST reference gives this request line ([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)). The how-to names 2025-06-05 as the supported version ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).

Request body:

```json
{
  "desiredLocations": ["eastus", "eastus2"],
  "desiredSizes": [{"sku": "Standard_ND96isr_H100_v5"}, {"sku": "Standard_NC40ads_H100_v5"}],
  "desiredCount": 1,
  "availabilityZones": false
}
```

Rules:

1. `desiredLocations` is the region group (2.2). `desiredSizes` is the size group, 1 `{"sku": <size>}` per size.
2. `desiredCount` is 1, to match the AWS Spot score requests (spec 0002, 4.6).
3. `availabilityZones` is `false` for a regional request and `true` for a zonal request (2.5).
4. `{location}` in the URI is the first region of the group. The body lists every region of the group.
5. The HTTP timeout of 1 attempt is 30 s.

Facts about the body, from the REST reference ([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)):

- `availabilityZones` is a boolean that "Defines if the scope is zonal or regional".
- `desiredCount` is the "Desired instance count per region/zone based on the scope". If it is not set, it defaults to 1.
- If `desiredLocations` is not set, the API uses the location of the request URI.
- `desiredSizes` is a list of `ResourceSize` objects, each with a `sku` string.

### 2.2 Grouping

1. Split `spot_sizes` into groups of 5, in config order. Split `regions` into groups of 8, in config order.
2. Send 1 regional request for each pair of a size group and a region group.
3. The how-to gives the limits: "up to eight regions and five VM sizes" ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).

Example: the repo config has 5 sizes and 9 regions. That gives 1 size group and 2 region groups (8 regions and 1 region), thus 2 regional requests per call. The 5 sizes follow the quota plan: 1-GPU or smallest sizes of H100, A100, A10, T4, and `Standard_NC144ds_xl_RTXPRO6000BSE_v6` (RTX PRO 6000), the last one added to match the quota plan of spec 0011. The probe asks for each size in every region. A size that a region does not offer gives a `none` or a `RestrictedSkuNotAvailable` row, which is data.

### 2.3 Token

1. Get the credential with `ctx.clients.azure("credential")`. It is a `DefaultAzureCredential` (spec 0003, 5.3).
2. Call `credential.get_token("https://management.azure.com/.default")` before each attempt. Send `.token` as the bearer token.
3. `get_token` takes 1 or more scopes and returns an `AccessToken` ([azure-identity, DefaultAzureCredential](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.defaultazurecredential)). The `/.default` scope refers to a resource generically, without naming specific permissions ([Microsoft identity platform, scopes](https://learn.microsoft.com/en-us/entra/identity-platform/scopes-oidc)).

### 2.4 Response and the value to outcome map

A 200 response has a `placementScores` list. Each item has `sku`, `region`, `availabilityZone`, `isQuotaAvailable`, and `score`. In 2025-06-05, `score` is a string ([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)).

Match an item to a (size, region) pair by `sku` and `region`, with no regard to case. The row keeps the size and the region as the config spells them.

The `outcome` of a row comes from the raw `score` value (spec 0002, 4.6):

| `score` | `outcome` | `error_code` |
|---|---|---|
| `High`, `Medium`, `Low` | `success` | null |
| `RestrictedSkuNotAvailable` | `restricted` | `RestrictedSkuNotAvailable` |
| `InsufficientQuota` (listed in 2026-10-05-preview) | `quota` | `InsufficientQuota` |
| `DataNotFoundOrStale` | `none` | null |
| Any other value, or not a string | `error` | the raw value |
| No item for the pair (regional request) | `none` | null |

The match of a value is exact and case-sensitive. `details.score` keeps the raw value as returned, also for `error`.

Facts about the values: the how-to gives the levels High, Medium, and Low ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)). The REST sample returns `DataNotFoundOrStale` for 1 zone ([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)). The PowerShell examples return `RestrictedSkuNotAvailable` ([PowerShell reference](https://learn.microsoft.com/en-us/powershell/module/az.compute/invoke-azspotplacementscore)). The 2026-10-05-preview version lists `InsufficientQuota` as a value ([REST 2026-10-05-preview](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)).

### 2.5 Zonal requests (pilot P11)

Spec 0002 P11 records 1 zonal request per region to compare with the regional scores.

1. After the regional requests, send zonal requests: for each region, 1 request per size group, with `desiredLocations` = that 1 region and `availabilityZones` = `true`.
2. With 5 sizes or fewer, this is 1 zonal request per region per call.
3. Write 1 row per returned item. `zone` is the `availabilityZone` of the item, as a string.
4. For a pair with no item in a zonal response, write no row. The zones of the pair are not known.

The REST sample shows a zonal response: 1 item per size, region, and zone, with zones `1`, `2`, and `3` ([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)).

### 2.6 Retry and errors

The how-to lists status 429, "Unable to generate placement score due to hitting a rate limit" ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)). The API "is part of your GET call quota" ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).

Rules for 1 request:

1. On a 429, read the wait that it asks for (rule 10). If the wait is 60 s or less, or unknown, wait 60 s, then send the same request 1 more time.
2. If the second attempt fails too, write 1 `error` row for each (size, region) pair of the request.
3. A status other than 200 and 429 gets no retry. It gives `error` rows at once.
4. An exception from the token call or the HTTP call gives `error` rows. The scheduler sees no exception.
5. A 200 response whose body is not a JSON object gives `error` rows with `error_code` = `InvalidResponse`.
6. `error_code` of a failed request: `error.code` of the ARM error body, else `HTTP <status>`, else the exception class name.
7. A failed zonal request gives 1 `error` row per pair with `zone` = `unknown`.
8. Do not store the error message in a row. Write it to the log as returned, 1 line for each failed call, with `probe_type`, `region` (the Regions of the request, joined by commas), `code`, `status`, and `message` (spec 0003, section 8.1, item 6). ARM messages can hold IDs of the subscription or the identity, and a row can become public data.
9. The sleep, the clock, and the HTTP call are constructor parameters, so that a test fakes them.
10. The wait of a 429 is, in this order: (a) the `Retry-After` header, if it is a whole number of seconds; (b) the number N in the text "try again after N seconds" (any case) in the body. The body can be JSON or plain text. The probe searches the raw text, so it needs no `error.code`. An HTTP date, or no match, gives an unknown wait.
11. A wait longer than 60 s (the retry budget) is a long wait. Do not retry. Keep the 429 `error` rows of rule 2, with `error_code` of rule 6. Add `details.retry_after_s` = the wait as read, in seconds.
12. After a long wait starts, the probe pauses until the wait ends. The probe sends no request while it pauses. This covers the rest of the run and the next runs. The end of the pause is the time of the 429 plus the wait, on the monotonic clock.
13. For each request that the probe skips in a pause, write 1 `error` row for each (size, region) pair. Use `error_code` = `RateLimited`, `zone` = `unknown` for a zonal request, `latency_ms` = 0, and `details.retry_after_s` = the seconds that remain, rounded up. These rows have no `http_status`.
14. The pause is in the memory of the probe object. A new process starts with no pause, and its first request can get a 429 again. This costs 1 request, not 2.

Decision A16 gives the reason.

The ARM error body has an `error` object with `code` and `message` ([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)).

The 60 s wait blocks the probe call. A stop signal during the wait takes effect when the call returns. With the repo config, the worst case is 11 requests, each with 2 attempts and 1 wait.

### 2.7 Row mapping

One row per (size, region) for each regional request, and 1 row per returned item for each zonal request. Order: the requests in the order of 2.2 and 2.5; in 1 request, region, then size, in config order. Fields, in the order of spec 0002 table 7.2:

| Field | Value |
|---|---|
| `ts_utc` | `ctx.timestamp()` at the time of the row |
| `run_id` | `ctx.run_id` |
| `simulated` | `ctx.simulate` (ADR 0011, replacing the earlier `run_mode`). |
| `provider` | `azure` |
| `region` | the config region |
| `zone` | null for a regional row; `availabilityZone` for a zonal row; `unknown` for a failed zonal request |
| `probe_type` | `az_spot_score` |
| `sku` | the config size |
| `placement_group_mode` | `n/a` |
| `n_requested` | 1 (`desiredCount`) |
| `n_placed` | null |
| `outcome` | section 2.4 or 2.6 |
| `error_code` | section 2.4 or 2.6 |
| `latency_ms` | the time of the request, with its retry and its wait |
| `details` | the table below |

| `details` key | Type | Value |
|---|---|---|
| `score` | string or null | the raw `score`; null when no item or the request failed |
| `desired_count` | integer | 1 |
| `is_quota_available` | boolean or null | `isQuotaAvailable` of the item; absent on `error` and missing-item rows |
| `scope` | string | `regional` or `zonal` |
| `http_status` | integer | the last HTTP status; only on `error` rows of a request that got a response |
| `retry_after_s` | integer | the wait of a long 429 in seconds (2.6 rules 11 and 13); only on those `error` rows |

## 3. Resource Graph probe (`az_spot`)

### 3.1 Queries

Spot price history and eviction rates are in the `SpotResources` table ([Spot VMs](https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms)). The probe sends 2 queries per call. They are the Microsoft sample queries, with the config sizes and regions in place of the sample values and without `order by` ([Spot VMs](https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms)). The price query also projects 1 added column, `latestSpotPriceEffectiveDate`.

Eviction rate query:

```kusto
SpotResources
| where type =~ 'microsoft.compute/skuspotevictionrate/location'
| where sku.name in~ ('Standard_ND96isr_H100_v5', 'Standard_NC40ads_H100_v5')
| where location in~ ('eastus', 'eastus2')
| project skuName = tostring(sku.name), location, spotEvictionRate = tostring(properties.evictionRate)
```

Price query:

```kusto
SpotResources
| where type =~ 'microsoft.compute/skuspotpricehistory/ostype/location'
| where sku.name in~ ('Standard_ND96isr_H100_v5', 'Standard_NC40ads_H100_v5')
| where properties.osType =~ 'linux'
| where location in~ ('eastus', 'eastus2')
| project skuName = tostring(sku.name), osType = tostring(properties.osType), location, latestSpotPriceUSD = todouble(properties.spotPrices[0].priceUSD), latestSpotPriceEffectiveDate = tostring(properties.spotPrices[0].effectiveDate)
```

Rules:

1. The `in~` lists hold every config size and every config region, each in single quotes, joined with a comma and 1 space.
2. A size or a region with a character outside `[A-Za-z0-9_.-]` is refused with a `ValueError`. The probe does not escape KQL. The scheduler logs the exception (spec 0003, 6).
3. The OS type is `linux`.
4. The Microsoft sample takes the latest price as `properties.spotPrices[0].priceUSD` ([Spot VMs](https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms)).
5. `latestSpotPriceEffectiveDate` is the `effectiveDate` of the same `spotPrices` entry.

The Spot VMs page says that Resource Graph gives "pricing history in the last 90 days and eviction rates for the last 28 trailing days" ([Spot VMs](https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms)). The same page says, for the portal, that "Eviction rates are quoted per hour" ([Spot VMs](https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms)).

Item shapes in the live check of 3.2 (2026-10-12):

- The `properties` of an eviction rate item had only `evictionRate` and `skuName`. The `properties` had no date or time window field. Thus the probe stores no time window for the eviction rate. The window is the 28 trailing days of the Spot VMs page.
- The eviction rate values were `0-5`, `5-10`, `10-15`, `15-20`, and `20+`.
- Each `spotPrices` entry had `effectiveDate` and `priceUSD`. In each of the 42 price items, the entries were in order from newest to oldest `effectiveDate`.

### 3.2 Client, scope, and paging

1. Get the client with `ctx.clients.azure("resourcegraph")`. It is a `ResourceGraphClient` (spec 0003, 5.3).
2. Send each query with `client.resources(QueryRequest(...))`. Import `QueryRequest` and `QueryRequestOptions` from `azure.mgmt.resourcegraph.models` inside the function.
3. Each query is a tenant-scope query. The request must not have `subscriptions` or `managementGroups`, also when a subscription variable is set. To query the tenant, omit both `managementGroups` and `subscriptions` ([Resource Graph REST quickstart](https://learn.microsoft.com/en-us/azure/governance/resource-graph/first-query-rest-api)).
4. `options`: `top` = 1000, `result_format` = `objectArray`, and `skip_token` of the previous page.
5. Read pages until the response has no `skip_token`.
6. The request builder is a constructor parameter, so that a test runs without the SDK. It takes the query and the `skip_token`.

Facts ([Resource Graph REST, Resources](https://learn.microsoft.com/en-us/rest/api/azureresourcegraph/resourcegraph/resources/resources?view=rest-azureresourcegraph-resourcegraph-2024-04-01)):

- The request has `query` (required), and the optional `subscriptions`, `managementGroups`, `options`, and `facets`.
- `$top` is "The maximum number of rows that the query should return", from 1 to 1000.
- A response `$skipToken` "can be passed to a subsequent query call (together with the same query and scopes used in the current request) to retrieve the next page of data".
- `resultFormat` is `table` or `objectArray`.

Why tenant scope:

- A feature request for another tool says: "Some queries don't work on subscription scope. For example, any queries using the `SpotResources` table." ([Grafana issue 77378](https://github.com/grafana/grafana/issues/77378)).
- The first deploy sent subscription-scope queries. In a sampled run, all 45 rows were `none`, with `price` and `eviction_rate` null (live check below).
- Live check: with the test subscription in `subscriptions`, `SpotResources | summarize count() by type` returned no rows. The same query at tenant scope returned rows. The caller had a role at subscription scope only.
- The 2 queries of 3.1 at tenant scope, for the earlier 5-size list (it had `Standard_ND96isr_H100_v5` and not the RTX PRO 6000 size) and the 9 regions of `config/targets.yaml`, returned 33 eviction items and 42 price items. 3 pairs had no item. The responses are recorded in `prober/tests/azure/fixtures/`.

### 3.3 Row mapping

One row per (size, region) of the config, for each call. Order: region, then size, in config order. Match a result item to a pair by `skuName` and `location`, with no regard to case. When a query returns more than 1 item for a pair, use the first.

| Field | Value |
|---|---|
| `ts_utc`, `run_id`, `simulated` | as in 2.7 |
| `provider` | `azure` |
| `region`, `zone` | the config region; null |
| `probe_type` | `az_spot` |
| `sku` | the config size |
| `placement_group_mode` | `n/a` |
| `n_requested`, `n_placed` | 1; null |
| `outcome` | `error` when a query failed; else `none` when the price item fails the price rule below; else `success` when the pair has an eviction item or a price item; else `none` |
| `error_code` | null; on `error`, the error code of the first failed query (the eviction rate query runs first) |
| `latency_ms` | the time of both queries |
| `details` | the table below |

| `details` key | Type | Value |
|---|---|---|
| `eviction_rate` | string or null | `spotEvictionRate` as Resource Graph returns it, for example `5-10`; null when no item or an empty string |
| `eviction_rate_min_pct` | number or null | the lower bound of `eviction_rate` in percent; see the parse rules below |
| `eviction_rate_max_pct` | number or null | the upper bound of `eviction_rate` in percent; null for an open bound; see the parse rules below |
| `price` | number or null | `latestSpotPriceUSD`; null when no item or not a number; also kept when the price rule fails |
| `price_effective_date` | string or null | `latestSpotPriceEffectiveDate` as Resource Graph returns it, for example `2026-10-01T00:00:00Z`; null when no item or an empty string |
| `currency` | string | `USD` |
| `os_type` | string | `osType` of the price item, else `linux` |
| `eviction_error_code` | string or null | the error code of the eviction rate query, as 3.4 defines it; null when the query did not fail |
| `price_error_code` | string or null | the error code of the price query, as 3.4 defines it; null when the query did not fail |

`eviction_rate` and `price` are on every row, also on `none` and `error` rows. The 3 added bound and date keys are also on every row. The 2 error code keys are also on every row.

Parse rules for the eviction rate bounds:

1. `<a>-<b>`, for example `5-10`: min = a, max = b.
2. `<a>+`, for example `20+`: min = a, max = null (an open bound).
3. a and b are decimal numbers. White space around a number or a sign is allowed. An integer value is written as an integer.
4. Any other value, or a null `eviction_rate`: both bounds are null. `eviction_rate` keeps the raw string.

Thus a pair with no item in both queries is a `none` row with both keys null. A pair with 1 item or 2 items is a `success` row, unless the price rule fails. A `success` row can have 1 null key. The live check of 3.2 had 9 pairs with a price item and no eviction item.

Price rule. A price item is no signal when 1 of these is true:

1. `price` is 0 or less.
2. `price_effective_date` is more than `spot_price_max_age_days` days before `ctx.now()` (section 3.5). The default is 540 days.

When the rule fails, the row is `none`, also when the pair has an eviction item. The row keeps the raw `price`, `price_effective_date`, and eviction keys ([ADR 0006](../decisions/0006-store-raw-provider-values-normalize-separately.md)). The board takes a Spot price by the rule of [spec 0006](0006-dashboard-spec.md) section 5.4, item 5.

Details of the rule:

- The date is ISO 8601. A date with no time zone is UTC.
- A null date, or a date that does not parse, is not old. A date in the future is not old.
- A null `price` with a price item does not fail the rule.
- An age of exactly the limit is not old.

The live check of 3.2 had 1 price item with `latestSpotPriceUSD` 0.0, for `Standard_NC24ads_A100_v4` in `westcentralus`. Its `latestSpotPriceEffectiveDate` was 2025-01-01, 649 days before the check. The pair had no eviction item. The oldest price above 0 in the same response had a `latestSpotPriceEffectiveDate` of 2025-09-01, 406 days before the check. 540 days is between the 2 ages.

### 3.4 Errors

1. Each query runs in its own `try`. A failed query logs 1 `az_spot.query_failed` line with `probe_type`, `region`, `code`, `status`, `message` (the SDK `error.message` as returned, else the text of the exception), and `query`; the row never holds the message (decision A7). Its error code is the `error.code` of the SDK exception, else the exception class name. The probe stores the code of each failed query in `details` (3.3), so the code of the second query is not lost.
2. If 1 query fails, every row of the call is `error`. The values of the other query stay in `details`.
3. The probe adds no retry of its own.

### 3.5 Config

Keys of the `azure` section that the `az_spot` probe reads, next to `regions` and `spot_sizes`. The core schema allows them (spec 0003, section 3).

| Key | Default | Rule |
|---|---|---|
| `spot_price_max_age_days` | 540 | Integer, 1 or more. The age limit of the price rule in 3.3. |

A bad value raises a `ValueError` before the queries. The scheduler logs the exception (spec 0003, 6). A higher limit keeps more old prices as signals. A lower limit drops more of them.

## 4. RBAC

| Call | Action | Granted by (spec 0005, 5.1) |
|---|---|---|
| Spot Placement Score `POST` | `Microsoft.Compute/locations/placementScores/generate/action` | Custom `<prefix>-placement-score-reader`, subscription scope |
| Resource Graph `resources` query | `Microsoft.ResourceGraph/resources/read` | Built-in `Reader`, subscription scope |
| Token from the managed identity | none | No role. Code on a VM can get tokens for the managed identities of the VM ([managed identity tokens](https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/how-to-use-vm-token)). |

Facts:

- The Compute operations list has `Microsoft.Compute/locations/placementScores/generate/action`, "Create a request for generating Placement Scores" ([Compute permissions](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/compute)).
- The how-to says to add the RBAC role "Compute Recommendations Role" for the subscription that runs the API ([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)). Spec 0005 uses a custom role with the action above.
- The Management and governance operations list has `Microsoft.ResourceGraph/resources/read`, "Submits a query on resources within specified subscriptions, management groups or tenant scope" ([Management and governance permissions](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/management-and-governance)).
- The built-in `Reader` role has the action `*/read` ([built-in roles, general](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/general)). Thus it includes `Microsoft.ResourceGraph/resources/read`.

The prober identity of spec 0005 has every action in this table. These probes need no change to spec 0005.

## 5. Dashboard contract

[Spec 0003](0003-prober-core-spec.md) section 4.4 binds the `details` keys of these rows:

| `probe_type` | Key | Type in spec 0003 | This spec |
|---|---|---|---|
| `az_spot_score` | `details.score` | string: `High`, `Medium`, `Low`, or a raw value | 2.4, 2.7 |
| `az_spot_score` | `details.desired_count` | integer | 2.7 |
| `az_spot` | `details.eviction_rate` | string range, as Resource Graph returns it; null when no value | 3.3 |
| `az_spot` | `details.price` | number, USD per hour; null when no value | 3.3 |
| `az_spot` | `details.os_type` | string | 3.3 |

The other `details` keys of sections 2.7 and 3.3 are extra. The dashboard does not read them.

## 6. Tests

`prober/tests/azure/spot_score_test.py` and `prober/tests/azure/spot_resource_graph_test.py` must show:

1. The loader finds both probes with their `probe_type`, `provider`, and `default_cadence`.
2. 7 sizes and 9 regions give 4 regional requests of 5 or fewer sizes and 8 or fewer regions. Each pair is in exactly 1 regional request.
3. Each documented `score` value, an unknown value, and a missing pair give the `outcome` of section 2.4.
4. A 429 and then a 200: 1 wait of 60 s, 1 retry, `success` rows.
5. A 429 twice: 1 wait, 2 attempts, `error` rows with the error code.
6. A 400 gives no retry. An exception gives `error` rows.
7. Zonal rows carry the zone. A failed zonal request gives `zone` = `unknown`.
8. The 2 query texts, the refusal of an unsafe name, paging with `skip_token`, a missing pair (`none`, null keys), and a failed query (`error`).
9. Each row passes `Record.validate()`.
10. The SDK request has no `subscriptions`. A recorded live response for the config sizes and regions gives `success` rows with values and separate `none` rows. The recorded responses are in `prober/tests/azure/fixtures/`.
11. The bounds of each parse rule of 3.3, and an unknown eviction rate string that is kept with null bounds.
12. Each case of the price rule of 3.3. A zero price and an old price give `none` rows with the raw values. A failed query still gives `error`. The recorded live response gives a `none` row for the zero price.
13. `spot_price_max_age_days` changes the age limit. A bad value raises a `ValueError`.
14. A 429 with a wait over 60 s (a plain-text body, a `Retry-After` header, or a JSON body with the text) gives no sleep and no retry. The rest of the run sends no request.
15. The `Retry-After` header wins over the body text.
16. A wait of 60 s or less, or an unreadable wait, still gives the 60 s wait and 1 retry.
17. The next runs send no request until the wait ends. Their rows have `error_code` = `RateLimited`. The probe sends again after the wait.

## 7. Decisions

Decisions of this spec that an earlier build plan and spec 0002 did not state.

| # | Decision | Reason |
|---|---|---|
| A1 | A regional request sends `"availabilityZones": false`. Spec 0002 4.6 also sends `"availabilityZones": false`; an earlier text said to omit it. | The REST reference gives no default for the field. The how-to's regional example uses False. |
| A2 | `{location}` in the URI is the first region of the group. | The body lists every region. The URI needs 1 valid region. |
| A3 | A regional pair with no item gives a `none` row with `score` null. | Spec 0002 7.2: `none` when the provider returns no value. |
| A4 | A zonal pair with no item gives no row. A failed zonal request gives rows with `zone` = `unknown`. | A zonal row needs a zone. A null zone would mix with the regional row of the same cell. |
| A5 | With more than 5 sizes, a region gets 1 zonal request per size group. | 1 request scores 5 sizes or fewer. |
| A6 | `error_code` holds the raw `score` for `restricted`, `quota`, and `error` rows. | Spec 0002 7.2: `error_code` is the raw provider code. |
| A7 | A row stores no error message, only the code and the status. The message goes to the log as returned, 1 line for each failed call (2.6, rule 8). | Logs are logs, metrics are metrics: a row is a measurement. ARM messages can hold IDs, and rows can become public data. |
| A8 | The HTTP timeout of 1 attempt is 30 s. | It bounds the worst case of 1 call (2.6). |
| A9 | `az_spot`: 2 queries per call, 1 row per pair. A failed query makes all rows `error`. | Spec 0006 needs both keys on every row. 1 outcome per row must not hide a half result. |
| A10 | Resource Graph scope: always the tenant. | A subscription-scope query of `SpotResources` returned 0 rows (3.2). |
| A11 | A size or a region outside `[A-Za-z0-9_.-]` stops the call. | The values go into KQL text. The sizes and regions of `config/targets.yaml` fit this set. |
| A12 | `details.currency` = `USD` and `details.scope` are extra keys. | The price field is `priceUSD`. The scope separates the zonal rows. |
| A13 | `az_spot` stores the eviction rate bounds as numbers in percent, next to the raw string. An open bound is null. | Analysis needs numbers. The raw string keeps the provider value. |
| A14 | `az_spot` stores the `effectiveDate` of the latest price. It stores no time window for the eviction rate. | The price item gives the date. The `properties` of the eviction item have no date or window field (3.1). |
| A15 | A price of 0 or less, or a price older than `spot_price_max_age_days` (default 540), makes the row `none`. The row keeps the raw values. | Project decision. 1 outcome per row (A9): a `success` row would show the price in the dashboard. The default is between the ages of the live check (3.3). |
| A16 | After a 429 with a wait over 60 s, the probe skips the retry and pauses until the wait ends (2.6, rules 10 to 14). Skipped requests give `error` rows with `error_code` = `RateLimited`. | A public 429 asks for 3600 s in a plain-text body and has no `Retry-After` ([report](https://github.com/Azure/azure-powershell/issues/28765)). A retry after 60 s would fail and add 1 request. Rows keep the schema: 1 row per pair, as for any failed request. |

## 8. Open items and implementer's choice

- Open (P11): the rate of 429 responses, and whether a zonal request for a region with no availability zones returns an error. The probe writes the result as rows.
- Open (P11): whether the built-in "Compute Recommendations Role" exists. Spec 0005 keeps the custom role.
- Open: the refresh rate of the `SpotResources` data. Spec 0002 4.6 says that 5-minute polls can return repeated values.
- Implementer's choice: log event names and the log level of a failed request.

## 9. Sources

- [Spot Placement Score how-to (Microsoft Learn)](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)
- [Spot Placement Scores - Post, REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)
- [Spot Placement Scores - Post, REST 2026-10-05-preview](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2026-10-05-preview)
- [Invoke-AzSpotPlacementScore (PowerShell reference)](https://learn.microsoft.com/en-us/powershell/module/az.compute/invoke-azspotplacementscore)
- [About Azure Spot Virtual Machines](https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms)
- [Resource Graph REST, Resources, 2024-04-01](https://learn.microsoft.com/en-us/rest/api/azureresourcegraph/resourcegraph/resources/resources?view=rest-azureresourcegraph-resourcegraph-2024-04-01)
- [Resource Graph REST quickstart](https://learn.microsoft.com/en-us/azure/governance/resource-graph/first-query-rest-api)
- [azure-mgmt-resourcegraph, QueryRequest](https://learn.microsoft.com/en-us/python/api/azure-mgmt-resourcegraph/azure.mgmt.resourcegraph.models.queryrequest)
- [azure-mgmt-resourcegraph, QueryRequestOptions](https://learn.microsoft.com/en-us/python/api/azure-mgmt-resourcegraph/azure.mgmt.resourcegraph.models.queryrequestoptions)
- [azure-mgmt-resourcegraph, QueryResponse](https://learn.microsoft.com/en-us/python/api/azure-mgmt-resourcegraph/azure.mgmt.resourcegraph.models.queryresponse)
- [azure-core, AccessToken](https://learn.microsoft.com/en-us/python/api/azure-core/azure.core.credentials.accesstoken)
- [Managed identity tokens on a VM](https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/how-to-use-vm-token)
- [azure-identity, DefaultAzureCredential](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.defaultazurecredential)
- [Microsoft identity platform, scopes and permissions](https://learn.microsoft.com/en-us/entra/identity-platform/scopes-oidc)
- [Azure permissions for Compute](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/compute)
- [Azure permissions for Management and governance](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/management-and-governance)
- [Azure built-in roles, General](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/general)
- [Grafana issue 77378, tenant scope for Resource Graph queries](https://github.com/grafana/grafana/issues/77378)
