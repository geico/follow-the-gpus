---
title: "A Capacity Block search rounds its start to now + 30 minutes or to 11:30 UTC"
date: 2026-10-13
---

# A Capacity Block search rounds its start to now + 30 minutes or to 11:30 UTC

## What the docs say

AWS Capacity Blocks let you reserve GPU instances for a fixed start and
end time ([Capacity Blocks](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html)).
You request whole days, from 1 to 14 days, or a multiple of 7 days up
to 182 days ([`DescribeCapacityBlockOfferings`](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html)).
Every Capacity Block ends at 11:30 UTC
([considerations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html#capacity-blocks-considerations)).

AWS documents part of the rounding rule. The considerations page says:
"You can describe Capacity Block offerings that can start in as soon
as 30 minutes"
([considerations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html#capacity-blocks-considerations)).
The purchase page covers the same-day, sub-day case: "All Capacity
Blocks end at 11:30AM UTC, so the blocks starting on the same day
will have durations that are the closest match ... One block will
have a duration that is slightly less than your desired duration,
while the other will have a duration slightly greater than your
desired duration"
([Find and purchase Capacity Blocks](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-purchase.html)).
This means a block that starts at now + 30 minutes has a partial
first period: its length is not a whole number of days (slightly less
or slightly greater than asked), because it still ends at 11:30 UTC.
This project's spec records the same length rule: "The length of an offering is `duration_hours` +
`duration_minutes` / 60, because a Capacity Block ends at 11:30 UTC
and an offering that starts at another time has a partial first
period" ([spec/0008-aws-probes-spec.md](../../spec/0008-aws-probes-spec.md),
section 3.2).

The part AWS does not document is the rounding of a later
`StartDateRange`: it does not say that a start later than now + 30
minutes is rounded up to the next 11:30 UTC, rather than kept at the
requested time (see "What we saw" below).

## What we saw

This project searched for the soonest Capacity Block start with
`DescribeCapacityBlockOfferings`, live, in one test. The result did
not depend only on the search window. It depended on the time of the
search:

- A search near now, with `StartDateRange` at or before now + 30
  minutes, got back a start of now + 30 minutes.
- A search for a later start got back the next 11:30 UTC time at or
  after that start.
- A search whose end date was less than 1 day after the start that AWS
  picked got the error `InvalidParameterValue`, with the text "End
  date ... must not be less then 1 days after start date ...".

This repo's spec for the probe that runs this search records the same
pattern:
"A `StartDateRange` at or before now + 30 minutes is moved to now + 30
minutes. A later `StartDateRange` is rounded up to the next 11:30 UTC."
([spec/0008-aws-probes-spec.md](../../spec/0008-aws-probes-spec.md),
section 3A.1). This behavior is not documented by AWS, and AWS can
change it.

## Example

This project's probe searches a window built around 2 candidate
starts: `first` (now + 30 minutes) and `boundary` (the next 11:30 UTC
at or after `first`). A block can start at either one, so the probe
checks both
([spec/0008-aws-probes-spec.md](../../spec/0008-aws-probes-spec.md),
section 3A.2). The spec gives these UTC examples, for a 24-hour block:

| Search time (`now`) | `first` (now + 30 min) | `boundary` (next 11:30 UTC) |
|---|---|---|
| 10:00 | 10:30 | 11:30, same day |
| 11:00 | 11:30 | 11:30, same day (`first` equals `boundary`) |
| 11:10 | 11:40 | 11:30, next day (`first` is past 11:30) |
| 11:31 | 12:01 | 11:30, next day (same case as 11:10) |

([spec/0008-aws-probes-spec.md](../../spec/0008-aws-probes-spec.md),
section 3A.2.) Separately, a live call showed
the rounding rule itself: a `StartDateRange` at or before now + 30
minutes came back moved to now + 30 minutes, and a later
`StartDateRange` came back rounded up to the next 11:30 UTC
(section 3A.1, described in "What we saw" above).

## Why it matters

A tool that searches for the soonest Capacity Block must not assume
that the start it gets back is the true earliest slot for every
search time. Near the 11:30 UTC boundary, a search a few minutes
apart can return `boundary` values about a day apart, as the table
above shows for 11:00 and 11:10. A caller that wants "now + 30
minutes, no later" must search again close to the boundary.

## Sources

- [AWS: Capacity Blocks](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html)
- [AWS: Capacity Blocks, considerations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html#capacity-blocks-considerations)
- [AWS: Find and purchase Capacity Blocks](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-purchase.html)
- [AWS: `DescribeCapacityBlockOfferings`](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html)
- [spec/0008-aws-probes-spec.md, section 3A](../../spec/0008-aws-probes-spec.md)
