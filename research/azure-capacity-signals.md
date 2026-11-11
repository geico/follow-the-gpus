---
title: "Azure GPU capacity signals: a survey of public sources"
date: 2026-10-11
---

# Azure GPU capacity signals: a survey of public sources

This note surveys what public Azure documentation says about signals for live GPU capacity, before an allocation attempt.

## Real-time capacity, Resource Graph, and Spot signals

- **SpotResources table in Azure Resource Graph.** The docs describe this table as the source for Spot eviction rates (28-day trailing) and Spot price history (90-day). Example query shape: `SpotResources | where type =~ 'microsoft.compute/skuspotevictionrate/location'`. Which scope and permissions the table needs is not confirmed here. Sources: [Azure Spot VMs overview](https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms), [Build workloads with Azure Spot VMs (architecture guide)](https://learn.microsoft.com/en-us/azure/architecture/guide/spot/spot-eviction).
- **Spot Placement Score.** The API is documented as a pre-deployment hint, but Microsoft explicitly disclaims it. A High or Medium score does **not** guarantee allocation success. A score is valid only at the time of the request. It is valid only for the exact SKU, zone, count, and region combination that was scored. It is not a general capacity gauge. Source: [Spot Placement Score docs](https://github.com/MicrosoftDocs/azure-compute-docs/blob/main/articles/virtual-machine-scale-sets/spot-placement-score.md).
- **Capacity Reservation groups** (`Microsoft.Compute/capacityReservationGroups`). These reserve capacity ahead of time. They need no term commitment, but you pay the pay-as-you-go rate for reserved capacity, used or not. A reservation request fails if Azure has no capacity for it. They do not support ND-series or NCads H100 v5 VMs. Not explored further. Source: [On-demand capacity reservation overview](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-overview).
- **Azure Compute Fleet** (`Microsoft.AzureFleet`, CLI command group `az compute-fleet`). `az compute-fleet` is a core Azure CLI command group, and most of its commands are in preview. Per the docs, one API call can request VMs across a list of VM sizes and zones. Multi-region fleets (up to 3 regions) are in preview and need a sign-up. The fleet picks VM sizes by an allocation strategy. The default is lowest price, with fallback to the next lowest price until the fleet reaches the target capacity. Spot fleets can also use capacity-optimized strategies. This is the closest thing Azure has to "ask for capacity and let the platform find it". The docs name Launch mode (preview) for external orchestrators, such as Karpenter. Launch mode needs API version 2026-04-01-preview and 2 feature flags. That makes Launch mode a plausible integration point for Kubernetes node autoscaling. Not tested here. Sources: [Azure Compute Fleet overview](https://learn.microsoft.com/en-us/azure/azure-compute-fleet/overview), [Azure Compute Fleet allocation strategies](https://learn.microsoft.com/en-us/azure/azure-compute-fleet/allocation-strategies), [Multi-Region Compute Fleet](https://learn.microsoft.com/en-us/azure/azure-compute-fleet/multi-region-compute-fleet), [Launch mode](https://learn.microsoft.com/en-us/azure/azure-compute-fleet/launch-mode), [az compute-fleet CLI reference](https://learn.microsoft.com/en-us/cli/azure/compute-fleet?view=azure-cli-latest).

## 2026-10-14: No Microsoft page says What-If or validate checks on-demand physical capacity

Read on 2026-10-14 (UTC). No `az deployment group validate` or `az
deployment group what-if` call was made. This is a documentation review
only.

- Microsoft names 2 checks for a VM deployment, quota and capacity, not a
  3-layer model: "Azure checks quota and capacity separately. Quota is
  your subscription's permission to deploy a certain number of vCPUs or
  VMs. Capacity is the actual compute resources available in the selected
  region or availability zone." Source: [vCPU quotas](https://learn.microsoft.com/en-us/azure/virtual-machines/quotas#quota-and-capacity).
- What-If and validate both run the same ARM preflight check, by default.
  Microsoft's ARM preflight page states: "Preflight validation runs
  automatically when you use deployment validate or what-if style
  commands," and "What-if includes preflight checks before calculating
  changes unless you configure it to skip the preflight." It lists what
  preflight checks: "Resource name conflicts," "Scope correctness,"
  "RBAC permissions," and "Basic provider and API compatibility," and
  calls it "a best-effort process" that "does not catch all
  deployment-time errors." The CLI reference for `az deployment group`
  gives the default: "'Provider' (the default), 'Template', or
  'ProviderNoRbac'... With a validation level of 'template', only static
  validation will be performed: preflight and permissions checks will be
  skipped." Source: [Preflight: Server-side validation before
  deployment](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/deploy-preflight),
  [az deployment group](https://learn.microsoft.com/en-us/cli/azure/deployment/group).
- A separate page, for the AzAPI Terraform provider's own preflight call
  (not named for `validate` or What-If), states that preflight can catch
  "quota violations" and checks "Subscription-level quota and capacity
  constraints for supported resource types." That page does not define
  "capacity constraints," or say whether it covers physical capacity.
  Source: [Enable preflight validation in the AzAPI Terraform
  provider](https://learn.microsoft.com/en-us/azure/developer/terraform/how-to-use-azapi-preflight-validation).
- For a Spot VM, the SKU-not-available page states 2 facts separately: a
  listed cause, "there isn't any capacity for Azure Spot in this
  location," and the codes for CLI/PowerShell deployment commands in
  general, "SkuNotAvailable or InvalidTemplateDeployment." Its own
  worked example shows only `SkuNotAvailable`, for `New-AzVM`. For a
  template or Bicep deployment, the page states separately: "a validation
  error occurs. The error code `InvalidTemplateDeployment` and error
  message are displayed… The deployment doesn't start." The page never
  joins these into 1 worked example; doing so is this project's
  inference, not a stated case. Source: [Resolve errors for SKU not
  available](https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/error-sku-not-available).
- No Microsoft Learn page states that `validate` or What-If can return
  `AllocationFailed`, `ZonalAllocationFailed`,
  `OverconstrainedAllocationRequest`, or
  `OverconstrainedZonalAllocationRequest`. No cited page shows these
  codes from validate or What-If. Source: [Troubleshooting Azure
  VM allocation failures](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machines/windows/allocation-failure).
- A SKU list (`az vm list-skus`, the portal size picker, or
  `Get-AzComputeResourceSku`) is a separate tool from validate. It shows
  only subscription and zone restrictions: "This diagnostic shows SKU and
  zone support and subscription restrictions, not live physical
  capacity." A size can show as available there and still fail to
  deploy or start. Source: [Troubleshoot allocation failures on virtual
  machine scale sets](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machine-scale-sets/allocationfailed-or-zonalallocationfailed),
  [Resolve errors for SKU not available](https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/error-sku-not-available).
- A SKU restriction's `reasonCode` can be `QuotaId` or
  `NotAvailableForSubscription`. Compute's Resource Skus - List reference
  page gives no description for either enum value. The Storage provider's
  own SKU reference defines the same 2 values: "As of now this can be
  'QuotaId' or 'NotAvailableForSubscription'. Quota Id is set when the SKU
  has requiredQuotas parameter as the subscription does not belong to
  that quota. The 'NotAvailableForSubscription' is related to capacity at
  DC." No Compute page confirms that this meaning applies to VM sizes.
  On the Storage page, this ties `NotAvailableForSubscription` to
  capacity. If the same meaning holds for Compute, spec 0010's pre-check
  ([spec 0010](../spec/0010-azure-provisioning-probes-spec.md), section 4,
  step 4), which records this code as `restricted`, files a
  capacity-related code as a restriction. Source:
  [Resource Skus - List](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list),
  [Skus - List (Storage)](https://learn.microsoft.com/en-us/rest/api/storagerp/skus/list).

## Sources

- [Azure Compute Fleet overview](https://learn.microsoft.com/en-us/azure/azure-compute-fleet/overview)
- [Azure Compute Fleet allocation strategies](https://learn.microsoft.com/en-us/azure/azure-compute-fleet/allocation-strategies)
- [Multi-Region Compute Fleet](https://learn.microsoft.com/en-us/azure/azure-compute-fleet/multi-region-compute-fleet)
- [Launch mode for Azure Compute Fleet](https://learn.microsoft.com/en-us/azure/azure-compute-fleet/launch-mode)
- [az compute-fleet CLI reference](https://learn.microsoft.com/en-us/cli/azure/compute-fleet?view=azure-cli-latest)
- [On-demand capacity reservation overview](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-overview)
- [Troubleshoot ZonalAllocationFailed / AllocationFailed / OverconstrainedAllocationRequest](https://learn.microsoft.com/en-us/troubleshoot/azure/azure-kubernetes/error-codes/zonalallocation-allocationfailed-error)
- [AKS Engineering Blog: Navigating Capacity Challenges on AKS with NAP or VM node pools](https://blog.aks.azure.com/2025/12/06/node-auto-provisioning-capacity-management)
- [AKS flex nodes preview (cross-region/on-prem node pools)](https://blog.aks.azure.com/2026/09/22/flex-nodes-for-aks)
- [Azure Spot VMs overview](https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms)
- [Build workloads with Azure Spot VMs (architecture guide)](https://learn.microsoft.com/en-us/azure/architecture/guide/spot/spot-eviction)
- [Spot Placement Score docs](https://github.com/MicrosoftDocs/azure-compute-docs/blob/main/articles/virtual-machine-scale-sets/spot-placement-score.md)
- [Azure VM Quota vs Regional Capacity](https://oneuptime.com/blog/post/2026-07-29-azure-vm-quota-vs-regional-capacity/view) (secondary)
- [vCPU quotas overview](https://learn.microsoft.com/en-us/azure/virtual-machines/quotas)
- [Azure capacity planning: quotas, reservations, vmss instance mix, compute fleet](https://techcommunity.microsoft.com/blog/startupsatmicrosoftblog/azure-capacity-planning-using-quotas-reservations-vmss-instance-mix-and-compute-/4464893)
- [az deployment group](https://learn.microsoft.com/en-us/cli/azure/deployment/group)
- [Preflight: Server-side validation before deployment](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/deploy-preflight)
- [Enable preflight validation in the AzAPI Terraform provider](https://learn.microsoft.com/en-us/azure/developer/terraform/how-to-use-azapi-preflight-validation)
- [Resolve errors for SKU not available](https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/error-sku-not-available)
- [Troubleshooting Azure VM allocation failures](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machines/windows/allocation-failure)
- [Troubleshoot allocation failures on virtual machine scale sets](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machine-scale-sets/allocationfailed-or-zonalallocationfailed)
- [Resource Skus - List](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list)
- [Skus - List (Storage)](https://learn.microsoft.com/en-us/rest/api/storagerp/skus/list)

Sources of single facts in the text:

- [Spot Placement Score is a pre-deployment hint; High or Medium score does not guarantee allocation](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)
- [Resource type `Microsoft.Compute/capacityReservationGroups`](https://learn.microsoft.com/en-us/azure/templates/microsoft.compute/capacityreservationgroups)
- [Resource provider `Microsoft.AzureFleet`](https://learn.microsoft.com/en-us/azure/azure-compute-fleet/quickstart-create-azure-cli)
