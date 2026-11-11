---
title: "A longer Capacity Block started up to 15 days later, never earlier"
date: 2026-10-13
---

# A longer Capacity Block started up to 15 days later, never earlier

## What the docs say

The AWS Capacity Block docs describe the allowed lengths of a block: 1
to 14 days, or a multiple of 7 days up to 182 days
([`DescribeCapacityBlockOfferings`](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html)).
The docs do not say how the soonest start changes with the length of
the block you ask for.

## What we saw

A bisection search for the earliest Capacity Block start, at more than
1 block length, for several instance types and Regions, showed the
same shape each time: the earliest start moves later, not earlier, as
the requested length grows. This repo's spec records the pattern:

> The earliest start depends on the duration: a live search found a
> start in 1 day for a 1-day block, but weeks later for a 7-day
> block, and none in 8 weeks for a 14-day block.

([spec/0008-aws-probes-spec.md](../../spec/0008-aws-probes-spec.md),
section 3A.2.) The stock of offerings can also change within a few
hours.

## Example

For one instance type in one Region, the earliest start by block
length can look like this (the dates are an example of the shape):

| Length | 1 day | 2 days | 3 days | 7 days | 14 days |
|---|---|---|---|---|---|
| Earliest start | day 5 | day 15 | day 20 | day 20 | day 20 |

## Why it matters

A search for "the soonest Capacity Block" must state the duration it
asked for. The same type, in the same Region, at the same search
time, can give a start in 5 to 17 days for a short block, and a start
up to 15 days later than that for a long one. A tool that searches only 1 duration and
reports "no capacity" can be wrong about every other duration.

## Sources

- [AWS: `DescribeCapacityBlockOfferings`](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html)
- [spec/0008-aws-probes-spec.md, section 3A](../../spec/0008-aws-probes-spec.md)
