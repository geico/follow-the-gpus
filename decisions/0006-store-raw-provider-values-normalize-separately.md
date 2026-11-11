# 0006. Probe rows keep raw provider values; normalization is a separate layer

- Status: accepted
- Date: 2026-10-12

## Context

AWS and Azure give the same kind of signal in different forms. AWS gives a Spot placement score from 1 to 10 ([SpotPlacementScore](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_SpotPlacementScore.html)). Azure gives a category such as `High`, `Medium`, or `Low`. The Spot Instance Advisor gives an interruption range such as `<5%` ([spec 0002](../spec/0002-gpu-capacity-tracker-spec.md), section 4.8).

A heat map needs one scale for all providers. A probe that maps a value to that scale while it writes the row loses the raw value. A later change to the scale then needs a new run of the experiment. The rows cannot be rebuilt.

[Spec 0002](../spec/0002-gpu-capacity-tracker-spec.md) section 4.6 already says "Store every value as returned, in `details`" for the Azure score. The project made this the rule for every probe on 2026-10-12.

## Decision

1. **Numbers.** A probe stores each numeric value that a provider returns as a JSON number. It does not round, bucket, or replace the number with a category.
   - If the provider writes a number as a string, for example the AWS `SpotPrice`, the probe converts it to a number and loses no digits.
2. **Strings.** A probe stores each provider-specific string exactly as returned: same case, same spelling, no mapping to a new name.
   - Examples: `High`, `Medium`, `Low`, `RestrictedSkuNotAvailable`, `DataNotFoundOrStale`, and `<5%`.
   - A value that the probe does not know is stored too. It must not become null.
3. **Ranges.** If a provider returns a range, the probe stores the string as returned and the numeric bounds.
   - Name the bounds `<name>_min_<unit>` and `<name>_max_<unit>`, for example `eviction_rate_min_pct`. An open bound is null.
   - If the provider gives the bounds as numbers, store them as returned.
   - If the provider gives only the string, parse the bounds from it. A string that does not parse gives null bounds.
   - If the numbers and the string disagree, store both in different keys.
4. **Normalization.** A scale that spans providers is a separate layer. It reads the raw fields and writes its own output.
   - It never replaces or edits a raw field.
   - The design of the layer is open. This ADR does not set it. The project decided on 2026-10-12: no normalization across providers for now.
5. **Derived values.** A probe can store a derived value, for example `outcome` or `n_ceiling`. The raw fields must stay next to it.
6. **Scope.** The rule covers each value that a probe reads as a signal, and each error code.
   - It does not cover identifiers and request parameters.
   - It does not change the rules for error message text. [Spec 0009](../spec/0009-azure-spot-signals-spec.md) decision A7 keeps messages out of the rows because they can hold identifiers.
7. **Spec.** [Spec 0002](../spec/0002-gpu-capacity-tracker-spec.md) section 7.2 states the rule. A probe spec states the keys of its `details`.

## Audit

The audit reads the probe code on `main` on 2026-10-12, and one recent live object of each probe type that has objects. A live object was read for the AWS Spot score, Spot price, Spot Instance Advisor, and Azure Spot score probes. The live Resource Graph rows were null before the fix to tenant-scope queries (spec 0009, section 3.2), so the `az_spot` table follows the code on `main` and [spec 0009](../spec/0009-azure-spot-signals-spec.md). The Capacity Block rows were all errors, so no live offering was read. The allocation probes and the pre-check have no live objects, because the dev deploy has no GPU quota. Their rows come from the code and from [spec 0010](../spec/0010-azure-provisioning-probes-spec.md).

"Raw" means stored as returned. "Number" means stored as a JSON number. "n/a" means that the field is not numeric. A gap that says "Not stored" has an open follow-up. A gap marked "Fixed after the audit" is done. The fix of the Capacity Block describe limit and the fix to tenant-scope Resource Graph queries existed before this audit.

### `aws_cb_offering`

API: [CapacityBlockOffering](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityBlockOffering.html).

| Provider field | Raw | Number | Gap |
|---|---|---|---|
| `startDate`, `endDate` | yes | n/a | None. Stored as ISO 8601 strings. |
| `capacityBlockDurationHours` | yes | yes | None. |
| `capacityBlockDurationMinutes` | no | no | Not stored.. |
| `instanceCount` | yes | yes | None. |
| `upfrontFee` | yes | yes | None. The API gives a string. The probe stores a number. |
| `currencyCode` | yes | n/a | None. |
| `capacityBlockOfferingId`, `availabilityZone`, `instanceType` | yes | n/a | None. |
| `tenancy`, `zoneType`, `ultraserverType` | no | n/a | Not stored.. |
| `ultraserverCount` | no | no | Not stored.. |
| Error code | yes | n/a | None. The live rows have no offerings, because the account gate stops the call. An account without Capacity Block access gets this error (spec 0008, section 3.4). |

### `aws_spot_score`

API: [SpotPlacementScore](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_SpotPlacementScore.html).

| Provider field | Raw | Number | Gap |
|---|---|---|---|
| `score` | yes | yes | None. Stored as an integer from 1 to 10. |
| `region` | yes | n/a | None. |
| Error code | yes | n/a | None. |

### `aws_spot_price`

API: [SpotPrice](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_SpotPrice.html).

| Provider field | Raw | Number | Gap |
|---|---|---|---|
| `spotPrice` | yes | yes | None. The API gives a string. The probe stores a number. |
| `timestamp` | yes | n/a | None. |
| `productDescription`, `availabilityZone`, `instanceType` | yes | n/a | None. |

### `aws_spot_advisor`

Source: the Spot Instance Advisor feed ([spec 0002](../spec/0002-gpu-capacity-tracker-spec.md), section 4.8). The raw feed object is also stored under its own key.

| Provider field | Raw | Number | Gap |
|---|---|---|---|
| Range label (`<5%`, `5-10%`, and so on) | yes | n/a | A label that the probe does not know makes an `error` row and loses the label. Fixed after the audit: an unknown label gives a `success` row with the label as returned (spec 0008, decision A20). |
| Range index `r` | yes | yes | None. |
| Range `max` | no | no | The row has no numeric bounds. The feed `max` values differ from the label numbers. Fixed after the audit: the row stores the label bounds and `feed_max_pct` (spec 0008). |
| Savings `s` | yes | yes | None. |

### `az_spot_score`

API: [Spot placement scores, 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05).

| Provider field | Raw | Number | Gap |
|---|---|---|---|
| `score` | yes | n/a | None. The live values were `High`, `Medium`, `Low`, and `RestrictedSkuNotAvailable`. |
| `isQuotaAvailable` | yes | n/a | None. A boolean. |
| `placementPreference` | no | no | The API version in use does not return it.. |
| Error code | yes | n/a | None. |

### `az_spot`

Source: Azure Resource Graph, `SpotResources` table ([spec 0009](../spec/0009-azure-spot-signals-spec.md), section 3).

| Provider field | Raw | Number | Gap |
|---|---|---|---|
| `spotEvictionRate` | yes | n/a | None. The query runs at tenant scope. |
| Bounds of the eviction rate | yes | yes | None. Stored as `eviction_rate_min_pct` and `eviction_rate_max_pct`. An open bound is null. |
| `priceUSD` | yes | yes | None. A price of 0 or less, or a stale price, makes the row `none`, and the row keeps the raw values (spec 0009, decision A15). |
| `effectiveDate` of the price | yes | n/a | None. Stored as `price_effective_date`, a string. |
| Error code | yes | n/a | None. `error_code` keeps the code of the first failed query. `details` keeps the code of each failed query. Fixed after the audit (spec 0009, section 3.3). |

### `az_precheck`

APIs: [Resource SKUs](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list) and [Usage](https://learn.microsoft.com/en-us/rest/api/compute/usage/list).

| Provider field | Raw | Number | Gap |
|---|---|---|---|
| SKU `family`, `CapacityReservationSupported`, `vCPUs` | yes | yes for `vCPUs` | None. |
| Usage `currentValue` and `limit` | no | no | The row has only the difference. Fixed after the audit (spec 0010, section 4.1, decision Z18). |
| SKU restrictions (`type`, `zones`, `locations`) | no | n/a | The row has a probe-written `reason`. Fixed after the audit (spec 0010, section 4.1, decision Z18). |
| SKU restriction `reasonCode` | yes | n/a | Stored in `error_code`, only for a restriction that applies. Fixed after the audit (spec 0010, section 4.1, decision Z18). |
| SKU zones | no | n/a | Not stored. Fixed after the audit (spec 0010, section 4.1, decision Z18). |

### `az_capacity_reservation`

| Provider field | Raw | Number | Gap |
|---|---|---|---|
| Reservation `provisioningState` | yes | n/a | None. |
| Reservation `capacity` | no | no | The adapter reads it. The row does not store it.. |
| Error codes | partly | n/a | `error_code` keeps the primary code. The other codes are dropped.. |

### `az_vmss_flex`

| Provider field | Raw | Number | Gap |
|---|---|---|---|
| Member `provisioningState` and `powerState` | no | n/a | The row has counts only.. |
| Member error codes | yes | n/a | None. Stored sorted in `member_codes`. |
| Scale-out call error codes | partly | n/a | `error_code` keeps the primary code. The other codes are dropped.. |
| Operation error codes | yes | n/a | None. They join `member_codes`. `error_code` keeps the primary code. |

## Consequences

- A change to the cross-provider scale needs no new data. A new layer reads the raw rows.
- Some rows get more keys. The row size grows a little.
- The open gaps in the tables change the rows and the specs. Until they are closed, their gaps in the audit stay.
- The audit has a date. A new probe or a new probe field must follow the rule.
- The design of the normalization layer is open. The project decided on 2026-10-12: no normalization across providers for now. Raw values stay as this ADR says.
