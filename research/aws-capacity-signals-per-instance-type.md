---
title: "AWS capacity signals per instance type"
date: 2026-10-11
---

# AWS capacity signals per instance type

## Summary

AWS publishes several signals that give data for 1 instance type at a time. Each signal measures a different pool or a different quantity. The quantities are Spot price, Spot interruption rate, Capacity Block offerings, SageMaker training plan offerings, the catalog, and the usage of your own account.

- No signal in this note measures free On-Demand capacity. [AWS capacity signals](aws-capacity-signals.md) gives that result and its sources.
- Spot placement scores for 1 GPU type are in [AWS Spot placement score per GPU instance type](aws-spot-placement-score-per-gpu-type.md). This note does not repeat them.
- 2 feeds are undocumented: the JSON feed behind the Spot pricing page and the JSON feed behind the Spot Instance Advisor page. A poller must treat them as unstable.
- The best documented per-type signals for large GPU types are Capacity Block offerings and SageMaker training plan offerings. Both show a reserved, prepaid product. Neither shows Spot or On-Demand capacity.
- AWS documents no formula that relates these signals. This note gives no weights to combine them.

## Scope

This note covers commercial Regions in the continental US, GPU instance types, and Spot or capacity signals that a poller can read or analyze. Trainium, Inferentia, and other non-GPU accelerator types are out of scope. GovCloud, Regions outside the US, and Local Zones are also out of scope.

## Testing status

No live test ran. No AWS API was called. The 2 undocumented feed files were not downloaded. All statements come from public documentation, public web pages, and public repositories.

## Signal table

| Signal | Granularity | Refresh | Cost and limits | Read-only | Documented | GPU coverage |
|---|---|---|---|---|---|---|
| `DescribeSpotPriceHistory` | Instance type, AZ, product description (OS) | 1 record for each price change: stated by neither the API reference nor the EC2 User Guide pricing-history page (not verified: absence claim). The API reference says only that a call returns the prices in the given range, plus the last change before the start time. History goes back 90 days. | No fee found. Default throttle bucket for `Describe*` calls. | Yes | Yes | The valid-values list includes g4dn, g5, g6, g6e, p4d, p5, p5e, p5en, p6-b200. It omits p6-b300. |
| Spot pricing page and its feed | Instance type, Region (lowest price in the Region) | Page: "every 5 minutes". Third party: "Hourly". | Free public page | Yes | Page yes, feed no | Not checked |
| Spot Instance Advisor page and its feed | Instance type, Region. Price history data is averaged across AZs. | Last 30 days. Third party: feed is "Irregular". | Free public page | Yes | Page yes, feed no | Not checked |
| `DescribeCapacityBlockOfferings` | Instance type, AZ, start date, instance count, duration | Not documented | Own throttle bucket: 10 tokens, refill 0.15 per second | Yes. A purchase is a separate call. | Yes | 8 GPU instance types |
| SageMaker `SearchTrainingPlanOfferings` | `ml.` instance type, AZ, start and end time (CLI reference), instance count, duration | Not documented | No fee found | Yes. A plan is a separate call. | Yes | 9 GPU `ml.` types in the API |
| `DescribeInstanceTypeOfferings` | Instance type, Region, AZ, AZ ID, or Outpost | Not documented | Default throttle bucket for `Describe*` calls | Yes | Yes | All types. Catalog only. |
| Spot placement score | See the [Spot placement score note](aws-spot-placement-score-per-gpu-type.md) | | | | | |
| On-Demand vCPU quotas | Instance family group, account, Region. Not per type. | Not applicable | No charge for Service Quotas. CloudWatch alarms cost extra. | Yes | Yes | P group, G and VT group |
| EC2 Capacity Manager | Your usage, grouped by instance type, AZ ID, Region, and more | Hourly | No additional cost | Yes | Yes | Your own instances only |
| Spot Instance data feed | Your Spot usage and prices | Typically once an hour | Not checked | Yes | Yes | Your own instances only |
| Future-dated Capacity Reservation | Instance type, AZ | Assessment typically within 5 days | Free to cancel while assessing. Charges apply later. | No | Yes | Minimum 32 vCPUs |
| `RunInstances` with `DryRun` | Permissions only | Not applicable | Not applicable | Yes | Yes | Not a capacity signal |

"No fee found" means that the API reference page states no fee. This review also found no source that states a fee or no fee.

## Spot price history (`DescribeSpotPriceHistory`)

- The call filters by `availability-zone`, `availability-zone-id`, `instance-type`, and `product-description` ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeSpotPriceHistory.html)).
- `StartTime` goes back "up to the past 90 days". The call "also returns the last price change before the start time, which is the effective price as of the start time" ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeSpotPriceHistory.html)).
- "Spot Instance prices are set by Amazon EC2 and adjust gradually based on long-term trends in supply and demand for Spot Instance capacity." ([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-instances-history.html))
- Interpretation (not documented by AWS): the price is a slow signal. It does not show hour-to-hour changes in free Spot capacity.
- The valid-values list for `InstanceType.N` includes GPU types such as g4dn, g5, g6, g6e, p4d, p4de, p5.4xlarge, p5.48xlarge, p5e, p5en, p6-b200, and p6e-gb200. It does not include p6-b300.48xlarge ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeSpotPriceHistory.html)).
- Interpretation: the valid-values list is not a reliable list of Spot coverage. The Capacity Blocks page lists p6-b300.48xlarge as a current type ([Capacity Blocks](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html)). Test a type with a real call before a poller depends on it.
- The throttling page puts "The Describe\*, List\*, Search\*, and Get\* API actions that are not included in another category" in 1 bucket: 100 tokens, refill 20 per second. The page does not list `DescribeSpotPriceHistory` in another category ([throttling](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/throttling.html)).

## Spot pricing page and its feed

- The page says: "The following table displays the Spot price for each region and instance type (updated every 5 minutes)." ([Spot pricing](https://aws.amazon.com/ec2/spot/pricing/))
- The page also says: "The prices displayed below are the lowest prices per instance type in the region." Thus the page gives 1 price per type and Region, not per AZ ([Spot pricing](https://aws.amazon.com/ec2/spot/pricing/)).
- The HTML source of the page references `https://website.spot.ec2.aws.a2z.com/spot.json` ([Spot pricing](https://aws.amazon.com/ec2/spot/pricing/), page source).
- **Undocumented.** This review found no AWS documentation for this feed. The `spotinfo` project calls it "an undocumented endpoint, not a published AWS API". It gives the update frequency as "Hourly", against the "5 minutes" on the AWS page ([spotinfo data sources](https://github.com/alexei-led/spotinfo/blob/a1306dc745a82450ecd89878980372ccb03e8317/docs/data-sources.md#L145-L156), third party).

## Spot Instance Advisor page and its feed

- The page "shows the average interruption frequency and savings over on-demand rates over last 30 days for various instance pools" ([Spot Instance Advisor](https://aws.amazon.com/ec2/spot/instance-advisor/)).
- "Frequency of interruption represents the rate at which Spot has reclaimed capacity during the trailing month. They are in ranges of < 5%, 5-10%, 10-15%, 15-20% and >20%." ([Spot Instance Advisor](https://aws.amazon.com/ec2/spot/instance-advisor/))
- The page says that its price history data is averaged across Availability Zones and can be delayed ([Spot Instance Advisor](https://aws.amazon.com/ec2/spot/instance-advisor/)).
- The page points to Spot placement score for "a near real-time likelihood of your Spot request succeeding in a Region or Availability Zone" ([Spot Instance Advisor](https://aws.amazon.com/ec2/spot/instance-advisor/)).
- The HTML source of the page references `https://spot-bid-advisor.s3.amazonaws.com/spot-advisor-data.json` ([Spot Instance Advisor](https://aws.amazon.com/ec2/spot/instance-advisor/), page source).
- **Undocumented.** This review found no AWS documentation for this feed. The `spotinfo` project gives its update frequency as "Irregular — AWS republishes it every few months" ([spotinfo data sources](https://github.com/alexei-led/spotinfo/blob/a1306dc745a82450ecd89878980372ccb03e8317/docs/data-sources.md#L132-L141), third party).
- Interpretation: the Advisor data is a slow prior with 5 levels and no AZ split. It does not show current capacity.

## Capacity Block offerings (`DescribeCapacityBlockOfferings`)

The [AWS capacity signals](aws-capacity-signals.md) note covers durations, the 8-week window, the 30-minute start, and price reports. This section adds the per-type fields, the coverage, and the limits.

- The call describes the Capacity Block offerings that you can buy in the current Region. `CapacityDurationHours` is required. `InstanceType` and `InstanceCount` are optional ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html)).
- With `AllAvailabilityZones`, the results include all AZs and Local Zones, "regardless of your opt-in status" ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html)).
- Each offering has `availabilityZone`, `instanceType`, `instanceCount`, `startDate`, `endDate`, `upfrontFee`, `currencyCode`, and `zoneType` ([CapacityBlockOffering](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityBlockOffering.html)).
- Interpretation: a poller in scope can use `zoneType` to drop Local Zone offerings.
- A search returns "the details of up to 6 available blocks". Offerings are available "on a first-come, first-served basis" ([purchase](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-purchase.html)).
- "The price of a Capacity Block depends on available supply and demand for Capacity Blocks at the time of purchase." Across a range of dates, "the lowest-priced Capacity Block offering available is returned" ([pricing and billing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-pricing-billing.html)).
- The supported-types table lists 8 GPU instance types. This table shows the 4 commercial Regions in the continental US ([Capacity Blocks](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html)):

  | Instance type | us-east-1 | us-east-2 | us-west-1 | us-west-2 |
  |---|---|---|---|---|
  | p6-b300.48xlarge | ✓ | | | ✓ |
  | p6-b200.48xlarge | ✓ | ✓ | | ✓ |
  | p5.4xlarge | ✓ | ✓ | | ✓ |
  | p5.48xlarge | ✓ | ✓ | ✓ | ✓ |
  | p5e.48xlarge | ✓ | ✓ | ✓ | ✓ |
  | p5en.48xlarge | ✓ | ✓ | ✓ | ✓ |
  | p4d.24xlarge | ✓ | ✓ | | ✓ |
  | p4de.24xlarge | ✓ | | | ✓ |

- The table also lists Trainium types and UltraServer types. They are out of scope. The only GPU UltraServer type (P6e-GB200) is listed only for a Local Zone ([Capacity Blocks](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html)).
- The throttling page gives `DescribeCapacityBlockOfferings` its own bucket: 10 tokens, refill 0.15 per second ([throttling](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/throttling.html)).
- Interpretation: at 0.15 tokens per second, a poller can make about 9 calls per minute after the first 10. Each call needs 1 duration and returns offerings for 1 Region.
- Interpretation: Capacity Block offerings measure the Capacity Block pool. This review found no AWS source that links this pool to Spot or On-Demand capacity.

## SageMaker training plan offerings (`SearchTrainingPlanOfferings`)

- The call "Searches for available training plan offerings based on specified criteria". It takes `InstanceType`, `InstanceCount`, `DurationHours`, and `TargetResources` ([API reference](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_SearchTrainingPlanOfferings.html)).
- The CLI reference also lists `--start-time-after` and `--end-time-before`. The API reference page does not list them ([CLI reference](https://docs.aws.amazon.com/cli/latest/reference/sagemaker/search-training-plan-offerings.html)).
- The CLI reference gives each reserved capacity offering `InstanceType`, `InstanceCount`, `AvailabilityZone`, `StartTime`, `EndTime`, and duration fields. Each plan offering has `UpfrontFee` and `CurrencyCode`. The API reference type `ReservedCapacityOffering` does not list `StartTime` or `EndTime` ([CLI reference](https://docs.aws.amazon.com/cli/latest/reference/sagemaker/search-training-plan-offerings.html), [ReservedCapacityOffering](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_ReservedCapacityOffering.html)).
- The `InstanceType` valid values include 9 GPU types: ml.p4d.24xlarge, ml.p4de.24xlarge, ml.p5.4xlarge, ml.p5.48xlarge, ml.p5e.48xlarge, ml.p5en.48xlarge, ml.p6-b200.48xlarge, ml.p6-b300.48xlarge, and ml.p6e-gb200.36xlarge. The other valid values are Trainium types, which are out of scope ([API reference](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_SearchTrainingPlanOfferings.html)).
- The Developer Guide also lists ml.g6.xlarge and ml.g6.4xlarge, with the note "reach out to your account manager" ([training plans](https://docs.aws.amazon.com/sagemaker/latest/dg/reserve-capacity-with-training-plans.html)).
- Training plans return "up to three offerings of one or two segments". If no continuous block matches, the search tries 2 separate blocks ([training plans](https://docs.aws.amazon.com/sagemaker/latest/dg/reserve-capacity-with-training-plans.html)).
- A plan can start up to 8 weeks (56 days) ahead. Durations are 1 to 182 days in 1-day steps. Instance counts are 1, 2, 4, 8, 16, 32, or 64 ([training plans](https://docs.aws.amazon.com/sagemaker/latest/dg/reserve-capacity-with-training-plans.html)).
- For HyperPod clusters, offerings are in 1 AZ. For training jobs, offerings can span several AZs ([training plans](https://docs.aws.amazon.com/sagemaker/latest/dg/reserve-capacity-with-training-plans.html)).
- This review found no AWS source that says whether training plans and EC2 Capacity Blocks use the same capacity (not verified: absence claim). Keep the 2 signals separate.
- This review did not find the throttle limits for this call.

## Catalog (`DescribeInstanceTypeOfferings`)

- The call "Lists the instance types that are offered for the specified location". `LocationType` is `region`, `availability-zone`, `availability-zone-id`, or `outpost` ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeInstanceTypeOfferings.html)).
- It is a catalog fact, not a capacity signal. See [AWS capacity signals](aws-capacity-signals.md).
- Interpretation: use it as a mask. If a type is not offered in an AZ, no other signal for that type and AZ has meaning.

## On-Demand quotas

- On-Demand quotas apply "per AWS account per Region". They count vCPUs "regardless of the instance type". "Each quota type specifies the maximum number of vCPUs for one or more instance families." ([On-Demand Instances](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-on-demand-instances.html))
- The GPU quota groups are "Running On-Demand P instances" and "Running On-Demand G and VT instances". Each has a default of 0 ([On-Demand Instances](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-on-demand-instances.html)).
- "Service Quotas is available free of cost – you will only be charged for the CloudWatch alarms you set up." ([AWS blog, 2019-06-24](https://aws.amazon.com/blogs/mt/introducing-service-quotas-view-and-manage-your-quotas-for-aws-services-from-one-central-location/))
- Thus a quota gives no data for 1 instance type. It gives an account limit for a family group.

## Own-account signals

These signals show only your own usage. A poller with no running instances gets no supply data from them.

- **EC2 Capacity Manager.** It "aggregates your usage data with hourly refreshes". Without AWS Organizations integration, it monitors only the resources in the account where you enable it. You can enable it in 1 Region per account ([Capacity Manager](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-manager.html)).
- The `GetCapacityManagerMetricData` call can group by `instance-type`, `instance-family`, `availability-zone-id`, `resource-region`, and other dimensions ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetCapacityManagerMetricData.html)).
- AWS announced Capacity Manager on 2025-10-17. It is "available in all commercial AWS Regions enabled by default at no additional cost" ([What's New](https://aws.amazon.com/about-aws/whats-new/2025/10/amazon-ec2-capacity-manager/)).
- **Spot Instance data feed.** The feed "describes your Spot Instance usage and pricing". Files arrive "typically once an hour". If no Spot Instance runs in an hour, no file arrives for that hour ([Spot data feed](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-data-feeds.html)).

## Signals that are not read-only or not about capacity

- **Future-dated Capacity Reservation.** AWS assesses the request against factors that include "Forecasted capacity supply". The assessment "is typically completed within 5 days" ([concepts](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/cr-concepts.html)).
- You can request it "between 5 and 120 days in advance". The minimum commitment is 14 days for most types. The minimum instance count is 32 vCPUs ([concepts](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/cr-concepts.html)).
- A supported request goes to `scheduled`. An unsupported request goes to `unsupported` and is not delivered ([concepts](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/cr-concepts.html)).
- "If the Capacity Reservation is assessing or delayed, cancellation is free." A cancellation 8 or more weeks before the start date has no charge. Later cancellations cost 25% or 50% of the commitment ([cancel](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-release.html)).
- Interpretation: the request creates a resource and can create a commitment. Thus it is not a read-only signal. The [AWS capacity signals](aws-capacity-signals.md) note lists it as an open question.
- **`RunInstances` with `DryRun`.** The flag "Checks whether you have the required permissions for the operation, without actually making the request" ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_RunInstances.html)). The docs do not say that it checks capacity.

## Helper calls

- `GetInstanceTypesFromInstanceRequirements` previews the types that match a set of attributes. "Note that the response does not consider capacity." ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetInstanceTypesFromInstanceRequirements.html))
- The `GpuInfo` data type gives `Gpus` and `totalGpuMemoryInMiB` for an instance type ([GpuInfo](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GpuInfo.html)). Use it to convert instance counts to GPU counts.

## Third-party archive

- SpotLake collects Spot placement scores, Spot interruption frequency, and Spot prices "Every 10 minutes". It gets the interruption data from the `spotinfo` tool and the prices from `describe_spot_price_history` ([SpotLake collector README](https://github.com/ddps-lab/spotlake/blob/fd1388c3adb066a07db2d2fbf81aff2c0fd09141/collector/spot-dataset/aws/batch/README.md), third party).
- Interpretation: SpotLake is a ready-made join of the Spot signals per type. It does not include Capacity Block or training plan offerings.

## What a combination of signals cannot claim

- AWS documents no formula that maps a Spot price, an interruption level, or a Capacity Block price to free capacity (not verified: absence claim).
- AWS documents only a qualitative link: Spot uses "spare EC2 capacity", and EC2 interrupts Spot when it "needs the capacity back" ([Spot Instances](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-instances.html)). AWS documents no shared pool with Capacity Blocks or training plans (not verified: absence claim).
- Keep each signal on its own axis, with its own unit and its own refresh rate. Label any combined value as a model estimate, not an AWS measurement.

## Open questions

- Does the Spot pricing feed update every 5 minutes or every hour? A test can fetch it every minute for 2 hours and log the changes.
- Does `DescribeSpotPriceHistory` return data for p6-b300.48xlarge, g7, and g7e when you name them in a filter?
- What are the throttle limits of `SearchTrainingPlanOfferings`?
- Do `DescribeCapacityBlockOfferings` and `DescribeSpotPriceHistory` have a fee? This review found no statement either way.

## Sources

- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeSpotPriceHistory.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-instances-history.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-instances.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/throttling.html>
- <https://aws.amazon.com/ec2/spot/pricing/>
- <https://aws.amazon.com/ec2/spot/instance-advisor/>
- <https://github.com/alexei-led/spotinfo/blob/a1306dc745a82450ecd89878980372ccb03e8317/docs/data-sources.md> (third party)
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityBlockOffering.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-purchase.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-pricing-billing.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html>
- <https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_SearchTrainingPlanOfferings.html>
- <https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_ReservedCapacityOffering.html>
- <https://docs.aws.amazon.com/cli/latest/reference/sagemaker/search-training-plan-offerings.html>
- <https://docs.aws.amazon.com/sagemaker/latest/dg/reserve-capacity-with-training-plans.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeInstanceTypeOfferings.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-on-demand-instances.html>
- <https://aws.amazon.com/blogs/mt/introducing-service-quotas-view-and-manage-your-quotas-for-aws-services-from-one-central-location/>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-manager.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetCapacityManagerMetricData.html>
- <https://aws.amazon.com/about-aws/whats-new/2025/10/amazon-ec2-capacity-manager/>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-data-feeds.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/cr-concepts.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-release.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_RunInstances.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetInstanceTypesFromInstanceRequirements.html>
- <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GpuInfo.html>
- <https://github.com/ddps-lab/spotlake/blob/fd1388c3adb066a07db2d2fbf81aff2c0fd09141/collector/spot-dataset/aws/batch/README.md> (third party)
