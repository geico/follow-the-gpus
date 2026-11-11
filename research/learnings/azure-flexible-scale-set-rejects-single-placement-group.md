---
title: "Azure Flexible scale sets reject singlePlacementGroup=true with OperationNotAllowed"
date: 2026-10-16
---

# Azure Flexible scale sets reject singlePlacementGroup=true with OperationNotAllowed

## What the docs say

The orchestration modes page lists the setting under "Unsupported
parameters" for Flexible: "Single placement group - this can be set to
`null` and the platform will select the correct value".
([orchestration modes](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes#unsupported-parameters))
The page gives no error code for a request that sets it to `true`.

## What we saw

An apply created a Flexible scale set with
`singlePlacementGroup` set to `true`, for a GPU size. The create call
failed with the code `OperationNotAllowed`. The source is the earlier
apply failure that [spec 0005](../../spec/0005-azure-stack-spec.md),
section 4.5, records. This project made no other call to test it.

## Example

| Scale set mode | `singlePlacementGroup` | Result |
|---|---|---|
| Flexible | `true` | `OperationNotAllowed` at create |
| Flexible | `false` or `null` | The platform picks the value |
| Uniform | `true` | The mode that the docs list as supporting a single placement group |

## Why it matters

`OperationNotAllowed` is also the code that this project reads as a quota
error, when the message names the quota
([spec 0010](../../spec/0010-azure-provisioning-probes-spec.md), section
6). Read the message, not only the code. This project dropped the `single` entries
([ADR 0021](../../decisions/0021-provisioning-probes-measure-available-capacity-not-single-cluster-capacity.md)):
the probes measure available capacity per size and Region, and use Flexible
scale sets with `singlePlacementGroup` set to `false`.

## Sources

- [Orchestration modes for Virtual Machine Scale Sets (Microsoft Learn)](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes#unsupported-parameters)
- [spec/0005-azure-stack-spec.md](../../spec/0005-azure-stack-spec.md)
- [spec/0010-azure-provisioning-probes-spec.md](../../spec/0010-azure-provisioning-probes-spec.md)
