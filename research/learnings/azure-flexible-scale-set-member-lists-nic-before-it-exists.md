---
title: "A Flexible scale-set member lists its NIC before the NIC exists"
date: 2026-10-16
---

# A Flexible scale-set member lists its NIC before the NIC exists

## What the docs say

The Tags - Update At Scope call takes a resource ID and a tag operation, for
example `Merge`.
([Tags - Update At Scope](https://learn.microsoft.com/en-us/rest/api/resources/tags/update-at-scope))
The Flexible orchestration page describes the scale set and its VMs.
It does not say when the NIC of a new VM exists.
([Orchestration modes](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes))
Not documented.

## What we saw

We scaled a Flexible scale set to 2 VMs and tagged each VM,
OS disk, and NIC with `begin_update_at_scope` and `Merge`.
The VM list showed the NIC ID of a VM in `Creating`.
The first tag call on 1 NIC returned `ResourceNotFound` (HTTP 404).
The Azure activity log shows that the NIC write started about 2 seconds
after the failed tag call. The tag call at the next poll, 10 seconds later,
succeeded.

## Example

| Time (UTC) | Event |
|---|---|
| 18:02:56.3 | Tag write on the NIC fails with 404 |
| 18:02:58.0 | NIC write starts |
| 18:03:06.9 | Tag write on the NIC succeeds |

## Why it matters

A 404 on a tag call for a NIC of a member in `Creating` is not a lost
resource. Tag again at the next poll. Count the failed calls, not the
untagged resources.

## Sources

- [Tags - Update At Scope (Microsoft Learn)](https://learn.microsoft.com/en-us/rest/api/resources/tags/update-at-scope)
- [Orchestration modes for scale sets (Microsoft Learn)](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes)
