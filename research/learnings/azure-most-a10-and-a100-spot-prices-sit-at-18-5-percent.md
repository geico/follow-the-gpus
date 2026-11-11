---
title: "Most Azure A10 and A100 GPU Spot prices sat at 18.5 % of pay-as-you-go"
date: 2026-10-15
---

# Most Azure A10 and A100 GPU Spot prices sat at 18.5 % of pay-as-you-go

## What the docs say

Microsoft states: "Pricing for Azure Spot Virtual Machines is variable,
based on region and SKU."
([About Azure Spot Virtual Machines](https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms))
The same page says that Resource Graph gives "pricing history in the last
90 days". Not documented: a lowest Spot price, or how often a Spot price
can change.

## What we saw

Reading the Spot price of GPU VM sizes in US regions from the Resource
Graph `SpotResources` table, and the public pay-as-you-go Linux price
from the Azure Retail Prices API, gave this pattern:

- Most Spot prices were 18.5 % of the pay-as-you-go price, to 3 digits.
- The A10 size and the A100 size were at 18.5 % in every region with a
  price.
- The T4 size was well above that share in every region.
- The RTX PRO 6000 and H100 sizes were at 18.5 % in some regions and
  above it in others.
- Every price had an effective date on the 1st of a month, and no price
  changed during a 2-day read.
- A price of `0.0` is not a price. Drop it.

These plateaus look like per-family price tiers that change at most
monthly. The data does not prove that.

## Example

`Standard_NC40ads_H100_v5`, Linux:

| Region | Spot (USD per hour) | Pay-as-you-go (USD per hour) | Spot share |
|---|---|---|---|
| eastus | 1.289904 | 6.98 | 18.5 % |
| southcentralus | 2.139414 | 8.38 | 25.5 % |
| eastus2 | 3.216384 | 6.98 | 46.1 % |
| westus3 | 3.954868 | 6.98 | 56.7 % |

## Why it matters

A Spot price at 18.5 % of pay-as-you-go tells you little about demand,
because most A10 and A100 prices sit there. Hypothesis, not tested: a
Spot price above the 18.5 % plateau is a demand signal. The rows show a
price level, not its cause. Poll the Azure Spot price daily, not every 5
minutes: it changed at most once a month in this data.

## Sources

- [About Azure Spot Virtual Machines (Microsoft Learn)](https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms)
- [Azure Retail Prices API (Microsoft Learn)](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices)
