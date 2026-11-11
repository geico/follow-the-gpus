# 0016. Rename the per-job consent key to `allow_billable_provisioning`

- Status: accepted, amends [ADR 0011](0011-store-and-per-job-simulate-replace-run-mode.md)
- Date: 2026-10-14

## Context

[ADR 0011](0011-store-and-per-job-simulate-replace-run-mode.md) section 3 added the per-job consent key `allow_allocation`. A job that is not simulated needs `allow_allocation: true` before it creates real capacity. [ADR 0015](0015-two-independent-controls-replace-the-terraform-allocation-guard.md) added the stack input `grant_billable_provisioning`, the second control. The 2 controls guard one action, but their names do not match.

The word "allocation" does not say what is allocated. It can mean a Capacity Reservation, a scale set, a cost allocation, or a placement. The word does not say that the action costs money.

A bare "billable" was rejected earlier: it covers every call, because a write to the results bucket is billable too. The key must name the one action that needs consent.

## Decision

1. The per-job key `allow_allocation` is renamed `allow_billable_provisioning`. The name says the action (provisioning VMs or reservations) and the risk (cost). It pairs with the stack input `grant_billable_provisioning` of ADR 0015.
2. The class of probes that provision capacity is called "provisioning probes" in text, not "allocation probes".
3. The old key is not accepted as an alias. The existing unknown-key fault refuses it, and the message names `allow_billable_provisioning`.
4. The meaning of the key does not change. It is a boolean, it defaults to `false`, and `simulate: true` still replaces it, as ADR 0011 section 3 says.
5. Names that are not text keep their value in this change: the `allocation_probes` section key, the `az_allocation` cadence key, code module names, test file names, and spec file names. A follow-up change tracks them.

## Consequences

- A targets file with `allow_allocation` fails `check-config` until it uses the new key.
- ADR 0011 keeps its text. It is not edited, apart from the status line.

## Update

The follow-up renamed the names that section 5 left. The section key `allocation_probes` is now `provisioning_probes`, and the cadence key `az_allocation` is now `az_provisioning`. The module, the test files, the fixtures, and the file of spec 0010 changed name too. Like `allow_allocation`, the old keys are not aliases: `check-config` refuses each one with a fault that names the new key. The ADR file names 0009 and 0015 stay as they are.
