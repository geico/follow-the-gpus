---
title: "A wide Capacity Block search returned the earliest offer every time: AWS promises only the lowest price"
date: 2026-10-13
---

# A wide Capacity Block search returned the earliest offer every time: AWS promises only the lowest price

## What the docs say

AWS states what a Capacity Block range search returns: "When you
search for a Capacity Block across a range of dates, the lowest-priced
Capacity Block offering available is returned."
([Capacity Blocks pricing and billing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-pricing-billing.html)).
A reader of this sentence expects the cheapest offer in the window.
AWS does not promise the soonest offer.

## What we saw

For each instance type, Region, and block length, a wide-window
search and a bisection search built to find only the true earliest
start returned the same offer every time. In that data, Capacity Block
prices were flat per instance type across the Regions and block
lengths. So the data did not tell "earliest" apart from "cheapest" by
price alone.

## Example

For a 1-day block of one instance type in one Region, a wide-window
search (`EndDateRange` 56 days out) and a bisection search for the
true earliest start both returned an offer with the same `StartDate`.

## Why it matters

A caller that reads "lowest-priced" can expect AWS to also return the
soonest offer. AWS does not document that rule. This project saw it
hold with flat prices. Treat "earliest equals cheapest" as
an observation, not a documented AWS rule. Re-test the claim
before you depend on it in a market with uneven Capacity Block
prices.

## Sources

- [AWS: Capacity Blocks pricing and billing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-pricing-billing.html)
- [AWS: `DescribeCapacityBlockOfferings`](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html)
