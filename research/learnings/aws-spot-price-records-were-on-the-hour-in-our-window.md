---
title: "AWS Spot price records were stamped on the hour, about 6 hours apart per zone"
date: 2026-10-15
---

# AWS Spot price records were stamped on the hour, about 6 hours apart per zone

## What the docs say

AWS states: "Spot Instance prices are set by Amazon EC2 and adjust
gradually based on long-term trends in supply and demand for Spot
Instance capacity."
([View Spot Instance pricing history](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-instances-history.html))
Not documented: when a record gets its timestamp, how often a zone gets
a record, or whether a record means that the price changed.

## What we saw

Polling `DescribeSpotPriceHistory` every 5 minutes for GPU instance
types, per (type, zone) series, gave this pattern:

- Most polls returned no new record.
- Every record was on the hour: minute 00, second 00. The one
  exception in a longer pull was the first record of a new series.
- A series got a new record every few hours, about 6 hours on average.
- About a quarter of consecutive record pairs repeated the price of the
  record before.

## Example

For one type and zone, 4 records at 01:00:00, 09:00:00, 16:00:00, and
20:00:00 UTC can all carry the same price.

## Why it matters

In the 2-day sample, a poll faster than hourly found no new record. A new
record does not mean a new price, so compare the value, not the count of
records. The Spot placement score moved within 5 minutes in the same
window, so the price did not track capacity right now. The sample is 2
days long, so check your own data before you rely on the hour stamp.

## Sources

- [View Spot Instance pricing history (AWS User Guide)](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-instances-history.html)
