---
title: "The Azure capacity reservation docs give no billing granularity, so we do not know the minimum charge"
date: 2026-10-16
---

# The Azure capacity reservation docs give no billing granularity, so we do not know the minimum charge

## What the docs say

The capacity reservation overview says that a reservation is "priced at the same rate as the underlying VM size"
([overview](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-overview)).
It does not say how finely Azure bills a reservation. Billing granularity: not documented.
The Azure VM pricing FAQ says that VMs bill in full minutes
([Linux VM pricing FAQ](https://azure.microsoft.com/en-us/pricing/details/virtual-machines/linux/)).
AWS states its rule for a capacity reservation: per-second billing with a 60-second minimum
([AWS pricing and billing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html)).

## What we saw

This project creates a capacity reservation, reads its state, and deletes it, in a few minutes. Its design asks for up to 16 instances in
one reservation. We found no Azure page that gives the minimum charge for such a short reservation. We have not yet seen a billing export of these reservations.

## Why it matters

A reader who sizes the cost of short Azure reservations cannot take the AWS rule or the VM rule as proven. We assume at most full minutes, as for VMs.
The billing export of the first day of real runs will confirm the real granularity.

## Sources

- [Capacity reservation overview (Microsoft Learn)](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-overview)
- [Linux virtual machines pricing FAQ (Azure)](https://azure.microsoft.com/en-us/pricing/details/virtual-machines/linux/)
- [Capacity Reservation pricing and billing (AWS User Guide)](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html)
