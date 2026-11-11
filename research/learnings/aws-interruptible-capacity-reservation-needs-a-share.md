---
title: "An interruptible Capacity Reservation is visible only to a shared account"
date: 2026-10-12
---

# An interruptible Capacity Reservation is visible only to a shared account

## What the docs say

AWS interruptible Capacity Reservations "help you make unused capacity
temporarily available for other workloads within your account"
([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/interruptible-capacity-reservations.html)).
The owner creates the reservation from their own On-Demand Capacity
Reservation, then shares it through AWS Resource Access Manager
([User Guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/interruptible-capacity-reservations.html)).
A consumer account finds an interruptible reservation with
`DescribeCapacityReservations`. The consumer-procedures page says the
call shows reservations "in your account"
([consumer procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-consumer-procedures.html)).
The API reference says the call "describe[s] only the Capacity
Reservations in the AWS Region that you're currently using", and the
call takes an `owner-id` filter
([`DescribeCapacityReservations`](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityReservations.html)).

## What we saw

This repo's research note reports that none of the AWS pages it read
(the owner side, the consumer side, the sharing rules, and the API
reference for this feature) describe a marketplace, a public listing,
or an API call that lists the interruptible capacity of accounts that
have not shared it with you
([research/aws-interruptible-capacity-reservations.md](../aws-interruptible-capacity-reservations.md)).
The note labels this as its own inference, from the
consumer-procedures page ("in your account") and the `owner-id`
filter of `DescribeCapacityReservations`, not from a sentence that
states it directly. It is also an absence claim: the note found no
page that describes such a listing, but did not call AWS to confirm
it.

The owner's own account can always see its own interruptible
reservation through `DescribeCapacityReservations`, with no share
needed
([owner procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-owner-procedures.html)). The question above is only about an account that is not the
owner.

## Example

Call: `DescribeCapacityReservations`, from an account with no
interruptible reservation shared with it.

Answer: the API reference says only that the call "Describes one or
more of your Capacity Reservations", in the current Region
([`DescribeCapacityReservations`](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityReservations.html)).
It does not say how a shared reservation appears. This repo's
research note infers from this text and the `owner-id` filter that
the call does not return a reservation that no owner has shared with
the caller
([research/aws-interruptible-capacity-reservations.md](../aws-interruptible-capacity-reservations.md)).

## Why it matters

An interruptible Capacity Reservation is not a signal of free AWS
capacity. It is the unused part of 1 specific reservation that its
owner chose to share. A tool that looks for spare AWS GPU capacity
gets nothing from this API unless an owner has already shared a
reservation with the account that calls it.

## Sources

- [AWS User Guide: Interruptible Capacity Reservations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/interruptible-capacity-reservations.html)
- [AWS User Guide: consumer procedures](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-consumer-procedures.html)
- [AWS API reference: `DescribeCapacityReservations`](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityReservations.html)
- [research/aws-interruptible-capacity-reservations.md](../aws-interruptible-capacity-reservations.md)
