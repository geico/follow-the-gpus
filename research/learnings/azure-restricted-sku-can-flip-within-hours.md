---
title: "Azure's RestrictedSkuNotAvailable can flip within hours"
date: 2026-10-15
---

# Azure's RestrictedSkuNotAvailable can flip within hours

## What the docs say

The Spot Placement Score how-to defines the value: "**Restricted SKU** is
returned if the Spot VM SKU isn't available for the subscription."
([Spot Placement Score](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score))
The page does not say that this value can change from 1 request to the
next. It does say that a score is "only valid at the time when it's
requested".

## What we saw

Polling regional Spot Placement Scores every 15 minutes for GPU VM
sizes in US regions with `desiredCount` 1 showed that many
(size, region) pairs flipped between `RestrictedSkuNotAvailable` and a
real score within the first days. Other pairs never moved: they
returned `RestrictedSkuNotAvailable` in every poll.

## Example

A flip is a change between the 2 states from one poll to the next.
For one VM size in one region, the sequence of polls can read: score
`Low`, `Low`, `RestrictedSkuNotAvailable`, `Low`. That is 2 flips.

## Why it matters

1 `RestrictedSkuNotAvailable` answer does not prove that a subscription
can never use a size in a region: many pairs changed state within 2 days.
Poll the pair more than once before you drop it from a plan. To learn if
the subscription is blocked, read the `restrictions` of the SKU list, not
the Spot score.

A longer check split the 2 kinds. The pairs that returned
`RestrictedSkuNotAvailable` in every poll matched the SKU list
(Resource SKUs - List): they had a `NotAvailableForSubscription`
restriction, or the size was absent from the region. The pairs that
flipped had no restriction in the SKU list.

Thus, for the flipping pairs, a flip of the Spot score was not a change
of the SKU restriction. Hypothesis, not tested: a flip is a Spot
capacity or Spot offer limit that the API reports with the same value.

## Sources

- [Spot Placement Score how-to (Microsoft Learn)](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)
