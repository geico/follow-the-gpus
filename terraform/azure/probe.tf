# Probe resources in the existing resource group: the targets file maps to capacity reservation
# groups and scale sets. The prober changes the scale set capacity. The sweeper deletes leftovers.
# The prober finds each resource by its tags, never by its name (ADR 0022): the name holds a hash
# of the experiment, so 2 experiments in 1 resource group never collide.

locals {
  targets    = yamldecode(file(local.targets_path))
  experiment = local.targets.experiment

  # `azure.provisioning_probes` maps a probe type to a list of entries (ADR 0022). An old list
  # shape is not a mapping: it plans nothing and `targets_faults` refuses it.
  probe_types             = ["az_capacity_reservation", "az_vmss_flex"]
  provisioning_raw        = try(local.targets.azure.provisioning_probes, {})
  provisioning_is_mapping = can(keys(local.provisioning_raw))
  reservation_probes      = try([for p in local.provisioning_raw.az_capacity_reservation : p], [])
  vmss_probes             = try([for p in local.provisioning_raw.az_vmss_flex : p], [])

  # The first 8 characters of the hash of the experiment name, in the name of each probe resource.
  experiment_hash = substr(sha256(local.experiment), 0, 8)

  # A key that the azure section does not know must fail the plan. `try(..., [])` below reads an
  # absent section as empty, so a misspelled or old key (for example `allocation_probes`, now
  # `provisioning_probes`) would otherwise give an empty list and plan the removal of stateful
  # resources. The messages match `check-config` (prober/ftg_prober/config.py,
  # `KNOWN_CLOUD_KEYS` and `RENAMED_KEYS`). Keep the 2 lists in step with that file;
  # `test_hcl_known_keys_match_the_python_list` and `test_hcl_renamed_keys_match_the_python_map`
  # (prober/tests/test_config.py) fail when they differ.
  known_azure_keys = ["modules", "jobs", "cadence", "provisioning_probes", "quota", "probe_timeout_seconds", "poll_interval_seconds", "teardown_timeout_seconds"]
  renamed_keys = {
    allow_allocation  = "`allow_allocation` is now `allow_billable_provisioning` (ADR 0016)."
    allocation_probes = "`allocation_probes` is now `provisioning_probes`."
    az_allocation     = "`az_allocation` is now `az_provisioning`."
  }
  targets_faults = concat(
    [
      for k in try(keys(local.targets.azure), []) :
      "azure.${k} is not a known key. See spec 0003 section 3 for the file shape.${contains(keys(local.renamed_keys), k) ? " ${local.renamed_keys[k]}" : ""}"
      if !contains(local.known_azure_keys, k)
    ],
    [
      for k in try(keys(local.targets.azure.cadence), []) :
      "azure.cadence.${k} is not a known key. ${local.renamed_keys[k]}"
      if contains(keys(local.renamed_keys), k)
    ],
    # Only null reads as empty. A string, a list, or a number is not a mapping and must fail, not
    # plan the section as empty.
    try(local.targets.azure, null) != null && !can(keys(local.targets.azure)) ? ["`azure` must be a mapping"] : [],
    try(local.targets.azure.cadence, null) != null && !can(keys(local.targets.azure.cadence)) ? ["azure.cadence must be a mapping"] : [],
    local.provisioning_is_mapping ? [] : [
      can([for p in local.provisioning_raw : p])
      ? "azure.provisioning_probes is a list. It is now a mapping from probe type (${join(", ", local.probe_types)}) to a list of entries (ADR 0022)"
      : "azure.provisioning_probes must be a mapping from probe type to a list of entries"
    ],
    [
      for k in(local.provisioning_is_mapping ? keys(local.provisioning_raw) : []) :
      "azure.provisioning_probes.${k} is not a known probe type. Use ${join(" or ", local.probe_types)} (ADR 0022)"
      if !contains(local.probe_types, k)
    ],
    [
      for t in local.probe_types :
      "azure.provisioning_probes.${t} must be a list of entries"
      if try(contains(keys(local.provisioning_raw), t) && (local.provisioning_raw[t] == null || can(keys(local.provisioning_raw[t])) || !can([for p in local.provisioning_raw[t] : p])), false)
    ],
    flatten([
      for t, entries in {
        az_capacity_reservation = local.reservation_probes
        az_vmss_flex            = local.vmss_probes
        } : [
        for i, p in entries : concat(
          try(p.route, null) == null ? [] : ["azure.provisioning_probes.${t}[${i}].route is removed (ADR 0022); the probe type that lists the entry decides what the probe does"],
          # `==` does not convert types, so a string, a number, or null is neither true nor false.
          # An absent key is fine (the default is false). Same rule as `check-config` and the preflight.
          can(p.simulate) && !(p.simulate == true || p.simulate == false) ? ["azure.provisioning_probes.${t}[${i}].simulate must be true or false"] : [],
          try(p.placement_group_mode, "") == "single" ? ["azure.provisioning_probes.${t}[${i}].placement_group_mode must be `multi`. The `single` mode is removed (ADR 0021)."] : [],
        )
      ]
    ]),
    [
      for key, entries in local.vmss_entries_by_size_region :
      "azure.provisioning_probes.az_vmss_flex lists ${key} ${length(entries)} times. Use one az_vmss_flex entry per size and Region: the scale set is selected by its tags (ADR 0022)."
      if length(entries) > 1
    ],
  )

  # The az_vmss_flex entries by size and Region, in lower case. More than 1 per key is a fault.
  vmss_entries_by_size_region = {
    for p in local.vmss_probes :
    "${lower(p.sku)} in ${lower(p.region)}" => p...
  }

  # Entries that call Azure. A simulated entry never calls Azure and gets no resource (ADR 0011
  # section 2). `try()` treats a missing `simulate` key as `false`, the config default.
  active_vmss_probes = [for p in local.vmss_probes : p if try(p.simulate, false) != true]

  # The provisioning resources below follow the file's shape (which regions, which SKUs), but
  # plan only when var.grant_billable_provisioning is true: the stack's own, explicit grant is
  # the only thing that decides whether it is capable of provisioning real capacity at all (ADR
  # 0015). This replaces the earlier `terraform_data.allocation_guard`, which re-parsed the
  # targets file's per-job `allow_billable_provisioning` key in HCL; that key stays, checked only by the
  # prober's own config guard (unchanged), and plays no part here.

  # Probe subnets by lower-case region. Why this is enough: the prober VM sits in the control
  # region and makes every Azure API call through subnet_id (the control subnet, with its
  # outbound path). A scale-set probe VM only gets created, counted, and deleted by those calls.
  # It never calls anything itself, so a subnet in another region needs no egress at all: an
  # isolated subnet is enough (no peering, no route table, no NAT gateway, no public IP). Only
  # provisioning-probe VMs (the scale sets below) land in these subnets. A NIC can join only a
  # virtual network of its own region, so each region needs its own subnet. The control region
  # falls back to subnet_id. An entry of var.probe_subnet_ids for that region replaces it.
  probe_subnets = merge(
    { (lower(var.location)) = var.subnet_id },
    { for region, id in var.probe_subnet_ids : lower(region) => id },
  )

  # 1 capacity reservation group per region of the az_capacity_reservation entries, with the
  # zones of its entries.
  crg_regions = var.grant_billable_provisioning ? toset([for p in local.reservation_probes : p.region]) : toset([])
  crg_zones = {
    for r in local.crg_regions :
    r => distinct([for p in local.reservation_probes : tostring(p.zone) if p.region == r && p.zone != null])
  }

  # 1 scale set per active az_vmss_flex entry, of any size.
  # Key: <sku slug>-<region>-multi. The slug drops "Standard_" and turns "_" into "-". The key is
  # the address of the resource; the name below is <prefix>-<sku slug>-<region>-<experiment hash>.
  vmss_targets = var.grant_billable_provisioning ? {
    for p in local.active_vmss_probes :
    "${lower(replace(replace(p.sku, "Standard_", ""), "_", "-"))}-${lower(p.region)}-${p.placement_group_mode}" => p
  } : {}
  vmss_names = {
    for k, p in local.vmss_targets :
    k => "${var.name_prefix}-${lower(replace(replace(p.sku, "Standard_", ""), "_", "-"))}-${lower(p.region)}-${local.experiment_hash}"
  }
}

# Capacity reservation groups. A group without reservations has no cost.

resource "azurerm_capacity_reservation_group" "allocation" {
  for_each            = local.crg_regions
  name                = "${var.name_prefix}-crg-${each.key}-${local.experiment_hash}"
  resource_group_name = var.resource_group_name
  location            = each.key
  zones               = length(local.crg_zones[each.key]) > 0 ? local.crg_zones[each.key] : null

  # The prober selects the group by these tags: experiment and region (ADR 0022).
  tags = merge(local.probe_tags, { region = lower(each.key) })
}

# Scale sets at capacity 0. The profile is spec 0002 section 4.5.

resource "azurerm_orchestrated_virtual_machine_scale_set" "allocation" {
  for_each                     = local.vmss_targets
  name                         = local.vmss_names[each.key]
  resource_group_name          = var.resource_group_name
  location                     = each.value.region
  sku_name                     = each.value.sku
  instances                    = 0
  platform_fault_domain_count  = var.fault_domain_count
  single_placement_group       = false
  zones                        = each.value.zone != null ? [tostring(each.value.zone)] : null
  priority                     = "Regular"
  extension_operations_enabled = false

  # The prober selects the scale set by the tags experiment, sku, and region (ADR 0022).
  tags = merge(local.probe_tags, {
    sku    = each.value.sku
    region = lower(each.value.region)
    target = each.key
    run_id = "none"
  })

  os_profile {
    linux_configuration {
      admin_username                  = "ftgadmin"
      disable_password_authentication = true
      computer_name_prefix            = substr(replace(each.key, "-", ""), 0, 9)

      admin_ssh_key {
        username   = "ftgadmin"
        public_key = tls_private_key.prober.public_key_openssh
      }
    }
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }

  os_disk {
    caching              = "ReadOnly"
    storage_account_type = "StandardSSD_LRS"
  }

  # The subnet of the scale set's own region (local.probe_subnets). A region with no subnet
  # fails the precondition below, and the plan never reads this null. Its network security group,
  # if any, belongs to the network owner.
  network_interface {
    name    = "primary"
    primary = true

    ip_configuration {
      name      = "primary"
      primary   = true
      subnet_id = lookup(local.probe_subnets, lower(each.value.region), null)
    }
  }

  lifecycle {
    # The prober writes the run_id tag before each capacity change.
    ignore_changes = [tags["run_id"]]

    # A scale set needs a subnet in its own region (local.probe_subnets).
    precondition {
      condition     = contains(keys(local.probe_subnets), lower(each.value.region))
      error_message = "Provisioning probe ${each.key} needs a scale set in ${each.value.region}, but there is no subnet for ${each.value.region}. Add ${lower(each.value.region)} to probe_subnet_ids, move the entry to az_capacity_reservation, or set simulate: true."
    }

    # The name of a Linux scale set is 1 to 64 characters:
    # https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/resource-name-rules
    precondition {
      condition     = length(local.vmss_names[each.key]) <= 64
      error_message = "The scale set name ${local.vmss_names[each.key]} is longer than 64 characters. Shorten name_prefix."
    }
  }
}

# azurerm does not expose the delete options or the resiliency policy. azapi reads the scale
# set, merges the body, and writes it back. It matches list items by "name", so the NIC entry
# "primary" merges into the NIC configuration of the scale set above.
resource "azapi_update_resource" "allocation_vmss_policy" {
  for_each    = local.vmss_targets
  type        = "Microsoft.Compute/virtualMachineScaleSets@2024-11-01"
  resource_id = azurerm_orchestrated_virtual_machine_scale_set.allocation[each.key].id

  body = {
    properties = {
      resiliencyPolicy = {
        resilientVMCreationPolicy = { enabled = false }
        resilientVMDeletionPolicy = { enabled = true }
      }
      virtualMachineProfile = {
        storageProfile = {
          osDisk = { deleteOption = "Delete" }
        }
        networkProfile = {
          networkInterfaceConfigurations = [
            { name = "primary", properties = { deleteOption = "Delete" } },
          ]
        }
      }
    }
  }
}

# Budget on the resource group. It is the last backstop (spec 0002 D11). Cost data lags by hours.
# There is no budget without an alert email: Azure requires a contact on each notification.

resource "azurerm_consumption_budget_resource_group" "stack" {
  count             = var.alert_email != "" ? 1 : 0
  name              = "${var.name_prefix}-group-budget"
  resource_group_id = data.azurerm_resource_group.stack.id
  amount            = var.budget_amount
  time_grain        = "Monthly"

  time_period {
    # The start date must be the first day of a month.
    start_date = formatdate("YYYY-MM-01'T'00:00:00Z", plantimestamp())
  }

  dynamic "notification" {
    for_each = [80, 100]
    content {
      enabled        = true
      threshold      = notification.value
      operator       = "GreaterThanOrEqualTo"
      threshold_type = "Actual"
      contact_emails = [var.alert_email]
    }
  }

  lifecycle {
    ignore_changes = [time_period]
  }
}

# Fails the plan when the targets file has an old or unknown key (see local.targets_faults).
resource "terraform_data" "targets_check" {
  lifecycle {
    precondition {
      condition     = length(local.targets_faults) == 0
      error_message = "The targets file ${local.targets_path} is not valid: ${join("; ", local.targets_faults)}"
    }
  }
}
