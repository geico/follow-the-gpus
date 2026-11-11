---
title: "Azure docs do not say that What-If or validate check on-demand GPU capacity"
date: 2026-10-14
---

# Azure docs do not say that What-If or validate check on-demand GPU capacity

## What the docs say

Microsoft names 2 separate checks for a VM deployment: quota and capacity.
The vCPU quota page states: "Azure checks quota and capacity separately.
Quota is your subscription's permission to deploy a certain number of
vCPUs or VMs. Capacity is the actual compute resources available in the
selected region or availability zone. Even with sufficient quota,
deployments can still fail if Azure doesn't have enough capacity for your
requested VM size in the target region or zone."

Source: [vCPU quotas](https://learn.microsoft.com/en-us/azure/virtual-machines/quotas#quota-and-capacity)
(Microsoft Learn).

No Microsoft Learn page uses a 3-layer model. This brief adds a 3rd layer,
the offer or SKU restriction, because a subscription can be blocked from a
VM size apart from both quota and physical capacity. The Spot Placement
Score page gives an example: "**Restricted SKU** is returned if the Spot
VM SKU isn't available for the subscription." The 3-layer split (quota,
offer restriction, physical capacity) used in this brief is this project's
own framing, not a term Microsoft uses.

Source: [Spot Placement Score](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)
(Microsoft Learn).

**What-If and validate both run the same ARM preflight check, by
default.** Microsoft's ARM preflight page states this directly: "Preflight
validation runs automatically when you use deployment validate or what-if
style commands." The same page states: "What-if includes preflight checks
before calculating changes unless you configure it to skip the preflight."
It lists what preflight checks: "Resource name conflicts," "Scope
correctness," "RBAC permissions (whether you can deploy those resource
types)," and "Basic provider and API compatibility." The page also gives a
limit on this check: "Preflight validation is a best-effort process and
does not catch all deployment-time errors."

Source: [Preflight: Server-side validation before deployment](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/deploy-preflight)
(Microsoft Learn).

Both commands also expose a `--validation-level` (or `-ValidationLevel`)
setting, with 3 values. The CLI reference for `az deployment group`
states: "The deployment validation level. May be set to 'Provider' (the
default), 'Template', or 'ProviderNoRbac'... With a validation level of
'template', only static validation will be performed: preflight and
permissions checks will be skipped." So the default `Provider` level runs
the preflight check above, and `Template` skips it.

Source: [az deployment group](https://learn.microsoft.com/en-us/cli/azure/deployment/group)
(Microsoft Learn).

**A separate Azure page states that a preflight call can catch a quota
violation, for the AzAPI Terraform provider's own preflight call, not
named for `validate` or What-If.** The AzAPI preflight page states:
"Preflight catches configuration errors early—such as invalid address
prefixes, unsupported property combinations, or quota violations—without
incurring the cost of a failed deployment." Under "What preflight
validates," it lists: "Subscription-level quota and capacity constraints
for supported resource types." This page documents the ARM preflight
endpoint as the AzAPI Terraform provider calls it during `terraform plan`,
not `az deployment group validate` or `az deployment group what-if` by
name. The page does not define "capacity constraints," and it does not
say whether that phrase includes physical capacity, as opposed to quota.

Source: [Enable preflight validation in the AzAPI Terraform provider](https://learn.microsoft.com/en-us/azure/developer/terraform/how-to-use-azapi-preflight-validation)
(Microsoft Learn).

**For a Spot VM, the docs suggest a capacity signal at validation, but do
not state it.** The SKU-not-available page lists this as 1 cause of the error: "If
you're deploying an Azure Spot VM or Spot scale set instance, and there
isn't any capacity for Azure Spot in this location." Separately, the same
page states: "Azure CLI and Azure PowerShell deployment commands display
an error message that the requested size isn't available in the location
or zone. In the Azure portal activity log, you'll see error codes
SkuNotAvailable or InvalidTemplateDeployment." Its own worked example
shows only 1 of those 2 codes: "In this example, New-AzVM specified the
-Size parameter for a SKU that's not available. The error code
SkuNotAvailable is shown in the portal's activity log." For a template or
Bicep deployment, the page states separately: "a validation error occurs.
The error code `InvalidTemplateDeployment` and error message are
displayed… The deployment doesn't start." The page never joins these
statements into 1 worked example. That a Spot VM's template fails at
validation specifically for a lack of Spot capacity, under the code
`InvalidTemplateDeployment`, is this project's inference from those
separate statements, not a case the page states directly. No Microsoft
Learn page states that `validate` or What-If can return `AllocationFailed`,
`ZonalAllocationFailed`, `OverconstrainedAllocationRequest`, or
`OverconstrainedZonalAllocationRequest`. No cited page shows these codes
from validate or What-If.

Source: [Resolve errors for SKU not available](https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/error-sku-not-available)
and [Troubleshooting Azure VM allocation failures](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machines/windows/allocation-failure)
(Microsoft Learn).

**A SKU list is a separate tool from validate. It shows SKU and zone
support and subscription restrictions, not live physical capacity.** The same SKU-not-available page states:
"a size (SKU) shows as available in the Azure portal or via CLI commands,
but attempts to deploy or start resources... result in a 'SKU not
available' error." This sentence is about the SKU list (`az vm list-skus`,
the portal size picker, or `Get-AzComputeResourceSku`), not about
`validate`. A scale-set troubleshooting page confirms the same limit for
the CLI command: "This diagnostic shows SKU and zone support and
subscription restrictions, not live physical capacity."

Source: [Resolve errors for SKU not available](https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/error-sku-not-available)
and [Troubleshoot allocation failures on virtual machine scale sets](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machine-scale-sets/allocationfailed-or-zonalallocationfailed)
(Microsoft Learn).

## What we saw

Not yet observed by this project through a live call. On 2026-10-14
(UTC), this project read the Microsoft Learn pages cited above and
recorded the same finding in this repo's own research note, before
writing this brief. This project has made no `az deployment group
validate` and no `az deployment group what-if` call.

Source: [research/azure-capacity-signals.md](../azure-capacity-signals.md),
the "2026-10-14: No Microsoft page says What-If or validate checks
on-demand physical capacity" section.

This project's own prober spec classifies Azure's VM error codes at
allocation time, not at validate time or What-If time. Spec section 6,
"Outcome classification," heads its table "`outcome` when fewer than N VMs
are placed," so every row in the table is a result read after a real
allocation attempt:

> | Code | `outcome` when fewer than N VMs are placed |
> |---|---|
> | `AllocationFailed`, `ZonalAllocationFailed`, `OverconstrainedAllocationRequest`, `OverconstrainedZonalAllocationRequest` | `no_capacity` |
> | `SkuNotAvailable` | `restricted_or_no_capacity` |
> | `ResourceQuotaExceeded` | `quota` |
> | `OperationNotAllowed` with "quota" in the message | `quota` |
> | any other code, or no code | `error` |

The spec's quota codes come from a different Microsoft page than the ones
this brief cites for the preflight check: "The quota codes come from
Microsoft's page on quota errors. It shows `Code=OperationNotAllowed` with
'Operation results in exceeding quota limits of Core', and
`Code=ResourceQuotaExceeded`." The spec maps `OverconstrainedAllocationRequest`
and `OverconstrainedZonalAllocationRequest` to `no_capacity` by its own
decision, not by a documented Microsoft rule. Its decision log states why:
"Both codes can also come from constraints with capacity present, so the
mapping is a decision."

The spec gives `SkuNotAvailable` its own, mixed outcome,
`restricted_or_no_capacity`, because the code can mean either a
subscription restriction or a lack of physical Spot capacity. The spec's
own source list states this directly: "`SkuNotAvailable` means size not
available in the location or zone, or no Spot capacity." The spec does not
fold `SkuNotAvailable` into the `no_capacity` row with `AllocationFailed`.

The same spec also reads a separate SKU restriction list, keyed to a
`reasonCode`. That field can take 2 values: a SKU restriction has "a
`reasonCode` of `QuotaId` or `NotAvailableForSubscription`." Compute's
Resource Skus - List reference page gives no description for either enum
value. The Storage provider's own SKU reference defines the same 2 values:
"As of now this can be 'QuotaId' or 'NotAvailableForSubscription'. Quota
Id is set when the SKU has requiredQuotas parameter as the subscription
does not belong to that quota. The 'NotAvailableForSubscription' is
related to capacity at DC." No Compute page confirms that this meaning
applies to VM sizes. On the Storage page, this ties
`NotAvailableForSubscription` to capacity. If the same meaning holds for
Compute, spec 0010's pre-check, which records this code as `restricted`,
files a capacity-related code as a restriction.

Source: [spec/0010-azure-provisioning-probes-spec.md](../../spec/0010-azure-provisioning-probes-spec.md),
section 4, step 4 (the pre-check), section 6 ("Outcome
classification"), and decision Z16.
[Resource Skus - List](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list),
the `ResourceSkuRestrictionsReasonCode` enum, and
[Skus - List](https://learn.microsoft.com/en-us/rest/api/storagerp/skus/list)
(Storage), the `Reason Code` enum.

A later live run can use:

```bash
az deployment group validate --resource-group <rg> --template-file <gpu-vm-template> --validation-level Provider
az deployment group what-if --resource-group <rg> --template-file <gpu-vm-template> --validation-level Provider
```

## Example

This project's inference, not a documented case. The SKU-not-available
page states 2 facts separately: (a) a listed cause of the error, "there
isn't any capacity for Azure Spot in this location," for a Spot VM or Spot
scale set instance, and (b) the error code for a template deployment of an
unavailable SKU, `InvalidTemplateDeployment`, where "a validation error
occurs... The deployment doesn't start." Joining them, so that a Spot VM's
template fails at validation specifically for a lack of capacity, is this
project's inference. The page does not state that case directly.
Microsoft's docs give no stated case for an on-demand (non-Spot) VM.

Source: [Resolve errors for SKU not available](https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/error-sku-not-available)
(Microsoft Learn).

## Why it matters

A reader can mistake What-If or validate for a free check of GPU capacity.
Both commands run the same documented ARM preflight check by default: the
checks the preflight page lists, resource name conflicts, scope
correctness, RBAC permissions, and basic provider and API compatibility.
Per the SKU page, a template deployment of an unavailable SKU fails with
a validation error. Microsoft
itself calls the preflight check "best-effort" and says it "does not catch
all deployment-time errors." A quota check is documented for the ARM
preflight endpoint, on a page about the AzAPI Terraform provider, not by
name for `validate` or What-If, and the page never defines "capacity
constraints." For a Spot VM, this project can only infer a
physical-capacity signal from 2 separate statements on 1 page, not from a
stated case. For an on-demand VM, no cited page shows the error codes of a
real allocation failure from `validate` or What-If. Do not treat either
command as a confirmed substitute for a real allocation attempt.

## Sources

- [vCPU quotas](https://learn.microsoft.com/en-us/azure/virtual-machines/quotas#quota-and-capacity) — Microsoft Learn
- [Spot Placement Score](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score) — Microsoft Learn
- [Preflight: Server-side validation before deployment](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/deploy-preflight) — Microsoft Learn
- [az deployment group](https://learn.microsoft.com/en-us/cli/azure/deployment/group) — Microsoft Learn
- [Enable preflight validation in the AzAPI Terraform provider](https://learn.microsoft.com/en-us/azure/developer/terraform/how-to-use-azapi-preflight-validation) — Microsoft Learn
- [Resolve errors for SKU not available](https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/error-sku-not-available) — Microsoft Learn
- [Troubleshooting Azure VM allocation failures](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machines/windows/allocation-failure) — Microsoft Learn
- [Troubleshoot allocation failures on virtual machine scale sets](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machine-scale-sets/allocationfailed-or-zonalallocationfailed) — Microsoft Learn
- [Resource Skus - List](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list) — Microsoft Learn
- [Skus - List (Storage)](https://learn.microsoft.com/en-us/rest/api/storagerp/skus/list) — Microsoft Learn
- [spec/0010-azure-provisioning-probes-spec.md](../../spec/0010-azure-provisioning-probes-spec.md) — this repo
- [research/azure-capacity-signals.md](../azure-capacity-signals.md) — this repo
