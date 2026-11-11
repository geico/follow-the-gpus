---
title: "az vm list-skus leaves out restricted sizes unless you pass --all"
date: 2026-10-16
---

# az vm list-skus leaves out restricted sizes unless you pass --all

## What the docs say

The `az vm list-skus` reference describes `--all` as "Show all information
including vmSizes not available under the current subscription."
([az vm list-skus](https://learn.microsoft.com/en-us/cli/azure/vm?view=azure-cli-latest#az-vm-list-skus))
The page does not say that a size with a `Location` restriction is missing
from the default output.

## What we saw

We ran `az vm list-skus --location eastus --resource-type
virtualMachines -o json` for one subscription, read-only. The command
exited 0, wrote nothing to stderr, and printed 1380 entries. Only `Zone`
restrictions appear in them. `Standard_NC24ads_A100_v4` was not in the
output.

The Resource SKUs REST call for the same Region, with `$filter=location eq
'eastus'`, listed `Standard_NC24ads_A100_v4` with a restriction of
`NotAvailableForSubscription`. See the
[Resource SKUs API](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list).

## Example

| Source | `Standard_NC24ads_A100_v4` in eastus |
|---|---|
| `az vm list-skus` (no `--all`) | absent |
| Resource SKUs REST call | present, `NotAvailableForSubscription` |

## Why it matters

A check that reads "absent from `az vm list-skus`" as "not offered" rejects
a size that is only restricted. Read the REST list, or pass `--all`, and
test the `restrictions` field. The default CLI call also took about 69
seconds for one Region in this test.

## Sources

- [az vm list-skus (Microsoft Learn)](https://learn.microsoft.com/en-us/cli/azure/vm?view=azure-cli-latest#az-vm-list-skus)
- [Resource SKUs - List (Microsoft Learn)](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list)
