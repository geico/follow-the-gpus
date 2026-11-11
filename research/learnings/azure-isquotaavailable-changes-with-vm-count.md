---
title: "Azure's isQuotaAvailable field can flip from true to false as you raise the VM count"
date: 2026-10-12
---

# Azure's isQuotaAvailable field can flip from true to false as you raise the VM count

## What the docs say

The Azure Spot Placement Score API (version 2025-06-05) returns an
`isQuotaAvailable` field on each scored item, alongside `sku`,
`region`, `availabilityZone`, and `score`
([REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)).
The how-to says that you must check or request Spot VM quota
separately from the score
([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
The docs do not state at what VM count a subscription's quota runs
out, because that count depends on the subscription.

## What we saw

A regional request for 5 VM sizes together (4 GPU sizes and the CPU
control `Standard_D2s_v5`) at several desired counts, from 1 to 512,
across several US regions, gave these results. No
size's `score` field changed at any count. Of the 5 sizes, only the
CPU control's `isQuotaAvailable` field changed: true at count 1, and
false from count 4 upward. The count at which it flips depends on the
quota of the subscription.

## Example

A regional Spot Placement Score request (not zonal), 5 VM sizes
together, in several US regions. The CPU control, `Standard_D2s_v5`:

| `desiredCount` | `isQuotaAvailable` | `score` |
|---|---|---|
| 1 | true | unchanged |
| 4 | false | unchanged |
| 512 | false | unchanged |

## Why it matters

The `score` field alone does not tell you whether a subscription can
place the VM count you asked for. Read `isQuotaAvailable` on every
response, at the VM count you plan to request, not only at count 1.
The how-to page says you must check or request Spot VM quota
separately from the score
([how-to](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
A High or Medium score with `isQuotaAvailable` = false is this
project's own sign to check quota first, before it reads the score as
a capacity signal.

## Sources

- [Spot Placement Scores - Post, REST 2025-06-05](https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05)
- [Spot Placement Score how-to (Microsoft Learn)](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)
