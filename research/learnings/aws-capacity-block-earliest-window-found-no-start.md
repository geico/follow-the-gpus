---
title: "No Capacity Block started within about 25 hours, so a narrow earliest-start search found nothing"
date: 2026-10-16
---

# No Capacity Block started within about 25 hours, so a narrow earliest-start search found nothing

## What the docs say

`DescribeCapacityBlockOfferings` returns "the lowest-priced Capacity Block offering available" for a date range
([pricing and billing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-pricing-billing.html)).
The docs do not say how far in the future the earliest block starts.

## What we saw

A search for a 24-hour or 168-hour block of GPU instance types, with a
window that admitted only a start at now + 30 minutes or at the next
11:30 UTC (a start within about 25 hours), returned no offering and no
error on every call. On the same day, calls with a 14-day window found
offerings, and the smallest lead between the call and the start of an
offering was well over 1 day. One check call with the narrow window
returned nothing. The same call with an end date 35 days later
returned 1 offering that started about 15 days later, at 11:30 UTC.

## Why it matters

A search that admits only a start within about 25 hours is a yes or no question: does any block
start that soon? When stock starts later, the answer is always no. To measure how far the earliest
start is, search a wider window. The data is small, so check your own data.

## Sources

- [DescribeCapacityBlockOfferings (AWS API Reference)](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html)
- [Capacity Blocks pricing and billing (AWS User Guide)](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-pricing-billing.html)
