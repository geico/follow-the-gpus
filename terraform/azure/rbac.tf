# Roles. The prober identity works in the resource group and writes to the data container.
# The sweeper identity deletes in the resource group only. The group also holds the stack
# resources, which carry no experiment tag. The sweeper deletes only tagged resources (spec 0007).

locals {
  subscription_scope = data.azurerm_subscription.current.id
  prober_principal   = azurerm_linux_virtual_machine.prober.identity[0].principal_id
  sweeper_principal  = azurerm_linux_function_app.sweeper.identity[0].principal_id
  # The ID is /subscriptions/<id>/resourceGroups/<group>/..., so the group is the 5th segment.
  # Azure compares resource group names without regard to case.
  # The probe subnets that need a join assignment. A region needs one only when it has a real scale
  # set (local.vmss_targets, which is empty without the grant). The subnet must be in another
  # resource group than the stack's (the capacity-allocator role covers the stack's own group), and
  # it must not be subnet_id: the control subnet is the control subnet and never gets an
  # assignment. Keyed by lower-case region.
  vmss_regions = toset([for k, p in local.vmss_targets : lower(p.region)])
  probe_subnets_to_join = {
    for region, id in local.probe_subnets :
    region => id if contains(local.vmss_regions, region) && lower(id) != lower(var.subnet_id) && lower(split("/", id)[4]) != lower(var.resource_group_name)
  }
}

# Prober: resource group (capacity allocator: this role creates billable capacity)

resource "azurerm_role_definition" "capacity_allocator" {
  name        = "${var.name_prefix}-capacity-allocator"
  scope       = data.azurerm_resource_group.stack.id
  description = "Provisioning probes: create and delete capacity reservations, scale the allocation scale sets out and in (this creates billable VMs, disks, and NICs), tag them, and delete them. Resource group only."

  permissions {
    actions = [
      "Microsoft.Compute/virtualMachineScaleSets/read",
      "Microsoft.Compute/virtualMachineScaleSets/write",
      "Microsoft.Compute/virtualMachineScaleSets/delete",
      "Microsoft.Compute/virtualMachineScaleSets/scale/action",
      "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/read",
      "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/delete",
      "Microsoft.Compute/capacityReservationGroups/read",
      "Microsoft.Compute/capacityReservationGroups/capacityReservations/read",
      "Microsoft.Compute/capacityReservationGroups/capacityReservations/write",
      "Microsoft.Compute/capacityReservationGroups/capacityReservations/delete",
      "Microsoft.Compute/virtualMachines/read",
      "Microsoft.Compute/virtualMachines/instanceView/read",
      "Microsoft.Compute/virtualMachines/delete",
      "Microsoft.Compute/disks/read",
      "Microsoft.Compute/disks/delete",
      "Microsoft.Network/networkInterfaces/read",
      "Microsoft.Network/networkInterfaces/delete",
      # Scale-out of a Flexible scale set creates standard VMs and NICs in the caller's name.
      "Microsoft.Compute/virtualMachines/write",
      "Microsoft.Network/networkInterfaces/write",
      "Microsoft.Network/networkInterfaces/join/action",
      "Microsoft.Network/virtualNetworks/subnets/join/action",
      "Microsoft.Network/networkSecurityGroups/join/action",
      # Resources do not inherit tags. The prober tags the VMs, disks, and NICs of a scale-out.
      "Microsoft.Resources/tags/write",
    ]
  }

  assignable_scopes = [data.azurerm_resource_group.stack.id]
}

resource "azurerm_role_assignment" "prober_capacity_allocator" {
  # var.grant_billable_provisioning alone decides this (ADR 0015); the targets file plays no
  # part, so a missing grant never depends on parsing the file for consent.
  count                            = var.grant_billable_provisioning ? 1 : 0
  scope                            = data.azurerm_resource_group.stack.id
  role_definition_id               = azurerm_role_definition.capacity_allocator.role_definition_resource_id
  principal_id                     = local.prober_principal
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

# Prober: the probe subnet of each region that has a real scale set, when the subnet is in another
# resource group. The scale-set NICs join the subnet, and the capacity-allocator role covers only
# resource_group_name. Without this role, the join fails with LinkedAuthorizationFailed. The scope is
# that one subnet and the role is custom: the join action and the subnet read, not Network
# Contributor, which can also change or delete a subnet. The control subnet (subnet_id) never gets an
# assignment (spec 0005, section 5.1.1).
# Subnet actions: https://learn.microsoft.com/en-us/azure/virtual-network/virtual-network-manage-subnet#permissions
# NIC create: https://learn.microsoft.com/en-us/azure/virtual-network/virtual-network-network-interface#permissions
# (the NIC write, NIC join, and NSG join actions are in the capacity-allocator role, at the group).

resource "azurerm_role_definition" "subnet_joiner" {
  name        = "${var.name_prefix}-subnet-joiner"
  scope       = local.subscription_scope
  description = "Provisioning probes: read a subnet and join a scale-set NIC to it. Nothing else; no subnet write or delete."

  permissions {
    actions = [
      "Microsoft.Network/virtualNetworks/subnets/read",
      "Microsoft.Network/virtualNetworks/subnets/join/action",
    ]
  }

  assignable_scopes = [local.subscription_scope]
}

resource "azurerm_role_assignment" "prober_probe_subnet_joiner" {
  for_each                         = local.probe_subnets_to_join
  scope                            = each.value
  role_definition_id               = azurerm_role_definition.subnet_joiner.role_definition_resource_id
  principal_id                     = local.prober_principal
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

# Data writers (spec 0005 section 5.1 and 5.2). One custom role holds only the two blob write
# data actions: no read and no delete (the dashboard readers have their own read role below).
# Each assignment adds an ABAC condition on the blob path, so the identity writes only under
# its own prefix. The path attribute holds the blob name without the container name or a
# leading slash:
# https://learn.microsoft.com/en-us/azure/storage/blobs/storage-auth-abac-attributes
# Both actions support conditions, and Put Blob can need either one. The condition has the
# multiple-actions form of the "Condition format" section, with the attribute name from the
# Blob Storage attribute list (Resource attribute "Blob path"):
# https://learn.microsoft.com/en-us/azure/role-based-access-control/conditions-format
# The condition is true for every action it does not name, and for a named action only when
# the path matches. The experiment name matches ^[a-z0-9]+(-[a-z0-9]+)*$, so it holds no
# StringLike wildcard.
locals {
  blob_write_action      = "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write"
  blob_add_action        = "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/add/action"
  blob_path              = "Microsoft.Storage/storageAccounts/blobServices/containers/blobs:path"
  prober_data_condition  = "((!(ActionMatches{'${local.blob_write_action}'}) AND !(ActionMatches{'${local.blob_add_action}'})) OR (@Resource[${local.blob_path}] StringLike 'experiment=${local.experiment}/*'))"
  sweeper_data_condition = "((!(ActionMatches{'${local.blob_write_action}'}) AND !(ActionMatches{'${local.blob_add_action}'})) OR (@Resource[${local.blob_path}] StringLike 'experiment=${local.experiment}/logs/*'))"
}

resource "azurerm_role_definition" "data_writer" {
  name        = "${var.name_prefix}-data-writer"
  scope       = azurerm_storage_account.data.id
  description = "Create and write blobs. No read, no delete."

  permissions {
    data_actions = [local.blob_write_action, local.blob_add_action]
  }

  assignable_scopes = [azurerm_storage_account.data.id]
}

resource "azurerm_role_assignment" "prober_data_writer" {
  scope                            = azurerm_storage_container.data.id
  role_definition_id               = azurerm_role_definition.data_writer.role_definition_resource_id
  principal_id                     = local.prober_principal
  principal_type                   = "ServicePrincipal"
  condition_version                = "2.0"
  condition                        = local.prober_data_condition
  skip_service_principal_aad_check = true

  # No create_before_destroy here. OpenTofu applies it to every resource this one
  # depends on, so it reached the identity's owner, a fixed-name resource, and its create
  # failed because the old one still existed. A replace of this assignment now deletes the old
  # one first, so a write can get a 403 for a short time during that apply.
}

# Prober: read and list the data container, for `ftg-prober compact` (ADR 0025). The built-in
# Storage Blob Data Reader has 1 data action, blobs/read (a read and a list). No write and no
# delete, and no condition. The prober's writes keep the `prober_data_writer` assignment above.
resource "azurerm_role_assignment" "prober_data_reader" {
  scope                            = azurerm_storage_container.data.id
  role_definition_name             = "Storage Blob Data Reader"
  principal_id                     = local.prober_principal
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "prober_code_reader" {
  scope                            = azurerm_storage_container.code.id
  role_definition_name             = "Storage Blob Data Reader"
  principal_id                     = local.prober_principal
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

# Data readers: the deployer, and any extra Entra principals in var.data_reader_principal_ids.
# The deployer gets read access to the data container and the code container with no
# configuration. The code container holds the manifest, so a reader can verify a deployed
# release. toset drops the duplicate when the deployer is also in the list, which avoids a 409 on
# a duplicate assignment. The IDs are trimmed and lower-cased first, so a padded or an upper-case
# duplicate of the deployer's ID still dedupes. Both assignments use the same set.

locals {
  data_readers = toset([
    for id in concat([data.azurerm_client_config.current.object_id], var.data_reader_principal_ids) : lower(trimspace(id))
  ])
}

resource "azurerm_role_assignment" "data_reader" {
  for_each             = local.data_readers
  scope                = azurerm_storage_container.data.id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = each.value
}

resource "azurerm_role_assignment" "code_reader" {
  for_each             = local.data_readers
  scope                = azurerm_storage_container.code.id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = each.value
}

# Prober: subscription readers (Resource Graph, SKU and quota reads, Spot Placement Score)

resource "azurerm_role_assignment" "prober_subscription_reader" {
  scope                            = local.subscription_scope
  role_definition_name             = "Reader"
  principal_id                     = local.prober_principal
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_definition" "placement_score_reader" {
  name        = "${var.name_prefix}-placement-score-reader"
  scope       = local.subscription_scope
  description = "Generate placement scores and read location data."

  permissions {
    actions = [
      "Microsoft.Compute/locations/placementScores/generate/action",
      "Microsoft.Compute/locations/*/read",
    ]
  }

  assignable_scopes = [local.subscription_scope]
}

resource "azurerm_role_assignment" "prober_placement_score_reader" {
  scope                            = local.subscription_scope
  role_definition_id               = azurerm_role_definition.placement_score_reader.role_definition_resource_id
  principal_id                     = local.prober_principal
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

# Sweeper: resource group only, plus Reader for Resource Graph

resource "azurerm_role_definition" "cleanup" {
  name        = "${var.name_prefix}-cleanup"
  scope       = data.azurerm_resource_group.stack.id
  description = "Cleanup: delete old allocation VMs, disks, NICs, and capacity reservations, and set an allocation scale set to capacity 0. Resource group only."

  permissions {
    actions = [
      "Microsoft.Compute/virtualMachines/read",
      "Microsoft.Compute/virtualMachines/instanceView/read",
      "Microsoft.Compute/virtualMachines/delete",
      # Microsoft lists VM write as a right for Flexible scale set writes. Spec 0005 open item.
      "Microsoft.Compute/virtualMachines/write",
      "Microsoft.Compute/virtualMachineScaleSets/read",
      "Microsoft.Compute/virtualMachineScaleSets/write",
      "Microsoft.Compute/virtualMachineScaleSets/scale/action",
      "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/read",
      "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/delete",
      "Microsoft.Compute/disks/read",
      "Microsoft.Compute/disks/delete",
      "Microsoft.Network/networkInterfaces/read",
      "Microsoft.Network/networkInterfaces/delete",
      "Microsoft.Compute/capacityReservationGroups/read",
      "Microsoft.Compute/capacityReservationGroups/capacityReservations/read",
      "Microsoft.Compute/capacityReservationGroups/capacityReservations/delete",
    ]
  }

  assignable_scopes = [data.azurerm_resource_group.stack.id]
}

# An earlier revision of this PR gated sweeper_cleanup with count. Keep the address stable for any
# state that has the [0] instance; with no such state, this block does nothing.
moved {
  from = azurerm_role_assignment.sweeper_cleanup[0]
  to   = azurerm_role_assignment.sweeper_cleanup
}

resource "azurerm_role_assignment" "sweeper_cleanup" {
  # Unconditional, on purpose, unlike the other 2 allocation role assignments below
  # (ADR 0015): var.grant_billable_provisioning gates the rights that create billable capacity
  # (prober_capacity_allocator, prober_probe_subnet_joiner), not the right
  # to read and delete it. The cleanup role holds 3 write rights (VM write, scale set write, scale set scale action) only so
  # the sweeper can scale a set to 0 and delete; the sweeper code never creates or scales up
  # (sweeper/azure/sweeper/azure_clients.py:111-113). Trade-off, accepted (spec 0005 R7): the
  # sweeper identity could in principle scale up; removing the rights would strand leaked
  # capacity. Gating this assignment on the same variable would strand a leaked VM,
  # disk, NIC, or prober-made reservation the moment an operator turns the grant off, with
  # nothing left able to delete it.
  scope                            = data.azurerm_resource_group.stack.id
  role_definition_id               = azurerm_role_definition.cleanup.role_definition_resource_id
  principal_id                     = local.sweeper_principal
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "sweeper_subscription_reader" {
  scope                            = local.subscription_scope
  role_definition_name             = "Reader"
  principal_id                     = local.sweeper_principal
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

# Sweeper: the data container, to write its own log objects (spec 0003 section 8.2). The
# sweeper has no row prefix of its own; this is its only write. The ABAC condition limits it
# to experiment=<name>/logs/ (same custom role as the prober, narrower path).
resource "azurerm_role_assignment" "sweeper_data_writer" {
  scope                            = azurerm_storage_container.data.id
  role_definition_id               = azurerm_role_definition.data_writer.role_definition_resource_id
  principal_id                     = local.sweeper_principal
  principal_type                   = "ServicePrincipal"
  condition_version                = "2.0"
  condition                        = local.sweeper_data_condition
  skip_service_principal_aad_check = true

  # No create_before_destroy here. OpenTofu applies it to every resource this one
  # depends on, so it reached the identity's owner, a fixed-name resource, and its create
  # failed because the old one still existed. A replace of this assignment now deletes the old
  # one first, so a write can get a 403 for a short time during that apply.
}
