---
title: "Azure Retail Prices returns Spot and Low Priority items under the same armSkuName"
date: 2026-10-17
---

# Azure Retail Prices returns Spot and Low Priority items under the same armSkuName

## What the docs say

The Retail Prices API page lists `armSkuName` as a field of each item and
shows `$filter` queries on it.
([Azure Retail Prices REST API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices))
The page does not say that one `armSkuName` returns Spot and Low Priority
items next to the normal item.

## What we saw

The preflight `spend` command filters on `armRegionName`, `armSkuName`,
`serviceName eq 'Virtual Machines'`, and `priceType eq 'Consumption'`. The
recorded answer for `Standard_D2as_v4` in `eastus` holds 6 items, all with
the same `armSkuName`. The `skuName` field tells them apart: `D2as v4`,
`D2as v4 Spot`, and `D2as v4 Low Priority`. The `productName` field ends in
`Windows` for the Windows items. The Spot and Low Priority items cost far
less than the normal item. We recorded the answer on 2026-10-16.

## Example

| `skuName` | `productName` ends in | Is the Linux list price? |
|---|---|---|
| `D2as v4` | `Series` | Yes |
| `D2as v4` | `Series Windows` | No |
| `D2as v4 Spot` | `Series` | No |
| `D2as v4 Low Priority` | `Series` | No |

## Why it matters

A price lookup that only filters on `armSkuName` finds more than 1 price and
cannot pick the right one. The `spend` command drops the Windows items and
the items whose `skuName` ends in `Spot` or `Low Priority`. It fails the
lookup when the result is not exactly 1 price.
([preflight/ftg_preflight/spend.py](../../preflight/ftg_preflight/spend.py))

## Sources

- [Azure Retail Prices REST API (Microsoft Learn)](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices)
