# Tests of the existing network: the required resource_group_name and subnet_id, the resource
# placement, and the experiment tag, which only probe resources carry. The providers are mocks.
# Run: make test/azure

mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      subscription_id = "mock-subscription"
      tenant_id       = "mock-tenant"
    }
  }
  mock_data "azurerm_subscription" {
    defaults = {
      id              = "/subscriptions/mock-subscription"
      subscription_id = "mock-subscription"
    }
  }
  mock_data "azurerm_resource_group" {
    defaults = {
      id       = "/subscriptions/mock-subscription/resourceGroups/platform-rg"
      location = "eastus"
    }
  }
}
mock_provider "azapi" {}
mock_provider "archive" {}
mock_provider "tls" {}

variables {
  targets_file        = "tests/fixtures/real_entries.yaml"
  resource_group_name = "platform-rg"
  subnet_id           = "/subscriptions/mock-subscription/resourceGroups/platform-net/providers/Microsoft.Network/virtualNetworks/platform-vnet/subnets/workloads"
}

# These computed values feed other resources' plan-time ID or key format validation, so they
# get fixed mock values. OpenTofu resolves mocked computed attributes at plan time (unlike
# Terraform's default apply-time generation), so a random mock string fails that validation.
override_resource {
  target = azurerm_storage_account.data
  values = {
    id = "/subscriptions/mock-subscription/resourceGroups/platform-rg/providers/Microsoft.Storage/storageAccounts/mockdata"
  }
}

override_resource {
  target = tls_private_key.prober
  values = {
    public_key_openssh = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBmPBpUPJwK1fAC4jY/ZyFIIlplHI7a35fvhXjsX1V1y mock@ftg"
  }
}

override_resource {
  target = azurerm_network_interface.prober
  values = {
    id = "/subscriptions/mock-subscription/resourceGroups/platform-rg/providers/Microsoft.Network/networkInterfaces/ftg-prober-nic"
  }
}

override_resource {
  target = azurerm_log_analytics_workspace.sweeper
  values = {
    id = "/subscriptions/mock-subscription/resourceGroups/platform-rg/providers/Microsoft.OperationalInsights/workspaces/ftg-sweeper-law"
  }
}

override_resource {
  target = azurerm_storage_container.data
  values = {
    id = "/subscriptions/mock-subscription/resourceGroups/platform-rg/providers/Microsoft.Storage/storageAccounts/mockdata/blobServices/default/containers/data"
  }
}

override_resource {
  target = azurerm_storage_container.code
  values = {
    id = "/subscriptions/mock-subscription/resourceGroups/platform-rg/providers/Microsoft.Storage/storageAccounts/mockdata/blobServices/default/containers/code"
  }
}

override_resource {
  target = azurerm_service_plan.sweeper
  values = {
    id = "/subscriptions/mock-subscription/resourceGroups/platform-rg/providers/Microsoft.Web/serverFarms/ftg-sweeper-plan"
  }
}

override_resource {
  target = azurerm_orchestrated_virtual_machine_scale_set.allocation
  values = {
    id = "/subscriptions/mock-subscription/resourceGroups/platform-rg/providers/Microsoft.Compute/virtualMachineScaleSets/mock-vmss"
  }
}

run "every_resource_in_the_given_group_and_subnet" {
  command = plan

  variables {
    grant_billable_provisioning = true
    alert_email                 = "ops@example.com"
  }

  override_resource {
    target = azurerm_application_insights.sweeper
    values = {
      id = "/subscriptions/mock-subscription/resourceGroups/platform-rg/providers/Microsoft.Insights/components/ftg-sweeper-ai"
    }
  }

  override_resource {
    target = azurerm_monitor_action_group.alerts
    values = {
      id = "/subscriptions/mock-subscription/resourceGroups/platform-rg/providers/Microsoft.Insights/actionGroups/ftg-alerts"
    }
  }

  assert {
    condition = alltrue(concat([
      azurerm_storage_account.data.resource_group_name == "platform-rg",
      azurerm_network_interface.prober.resource_group_name == "platform-rg",
      azurerm_linux_virtual_machine.prober.resource_group_name == "platform-rg",
      azurerm_storage_account.func.resource_group_name == "platform-rg",
      azurerm_service_plan.sweeper.resource_group_name == "platform-rg",
      azurerm_log_analytics_workspace.sweeper.resource_group_name == "platform-rg",
      azurerm_application_insights.sweeper.resource_group_name == "platform-rg",
      azurerm_linux_function_app.sweeper.resource_group_name == "platform-rg",
      azurerm_monitor_action_group.alerts[0].resource_group_name == "platform-rg",
      azurerm_monitor_scheduled_query_rules_alert_v2.sweeper_stopped[0].resource_group_name == "platform-rg",
      ],
      [for g in azurerm_capacity_reservation_group.allocation : g.resource_group_name == "platform-rg"],
      [for s in azurerm_orchestrated_virtual_machine_scale_set.allocation : s.resource_group_name == "platform-rg"],
    ))
    error_message = "Every resource of the stack must go into resource_group_name."
  }

  assert {
    condition     = azurerm_network_interface.prober.ip_configuration[0].subnet_id == var.subnet_id
    error_message = "The prober VM NIC must go into subnet_id."
  }

  assert {
    condition = alltrue([
      for s in azurerm_orchestrated_virtual_machine_scale_set.allocation :
      s.network_interface[0].ip_configuration[0].subnet_id == var.subnet_id && s.network_interface[0].network_security_group_id == null
    ])
    error_message = "Each scale set must use subnet_id and set no network security group of its own."
  }

  assert {
    condition = alltrue([
      azurerm_consumption_budget_resource_group.stack[0].resource_group_id == "/subscriptions/mock-subscription/resourceGroups/platform-rg",
      azurerm_role_definition.capacity_allocator.scope == "/subscriptions/mock-subscription/resourceGroups/platform-rg",
      azurerm_role_assignment.prober_capacity_allocator[0].scope == "/subscriptions/mock-subscription/resourceGroups/platform-rg",
      azurerm_role_definition.cleanup.scope == "/subscriptions/mock-subscription/resourceGroups/platform-rg",
      azurerm_role_assignment.sweeper_cleanup.scope == "/subscriptions/mock-subscription/resourceGroups/platform-rg",
    ])
    error_message = "The budget and the capacity allocator and cleanup roles must use the given resource group as scope."
  }

  assert {
    condition     = output.probe_resource_group == "platform-rg" && output.control_resource_group == "platform-rg"
    error_message = "Both group outputs must give resource_group_name."
  }
}

# The sweeper deletes only resources with the experiment tag (spec 0007). The stack resources
# share the group with the probe resources, thus they must not carry the tag.
run "only_probe_resources_carry_the_experiment_tag" {
  command = plan

  variables {
    grant_billable_provisioning = true
    alert_email                 = "ops@example.com"
    tags                        = { owner = "platform" }
  }

  override_resource {
    target = azurerm_application_insights.sweeper
    values = {
      id = "/subscriptions/mock-subscription/resourceGroups/platform-rg/providers/Microsoft.Insights/components/ftg-sweeper-ai"
    }
  }

  override_resource {
    target = azurerm_monitor_action_group.alerts
    values = {
      id = "/subscriptions/mock-subscription/resourceGroups/platform-rg/providers/Microsoft.Insights/actionGroups/ftg-alerts"
    }
  }

  assert {
    condition = alltrue([
      for t in [
        azurerm_storage_account.data.tags,
        azurerm_network_interface.prober.tags,
        azurerm_linux_virtual_machine.prober.tags,
        azurerm_storage_account.func.tags,
        azurerm_service_plan.sweeper.tags,
        azurerm_log_analytics_workspace.sweeper.tags,
        azurerm_application_insights.sweeper.tags,
        azurerm_linux_function_app.sweeper.tags,
        azurerm_monitor_action_group.alerts[0].tags,
        azurerm_monitor_scheduled_query_rules_alert_v2.sweeper_stopped[0].tags,
      ] : !contains([for k in keys(t) : lower(k)], "experiment") && t["owner"] == "platform"
    ])
    error_message = "No stack resource may carry the experiment tag, so the sweeper never selects it. Each carries var.tags."
  }

  assert {
    condition = length(azurerm_orchestrated_virtual_machine_scale_set.allocation) == 1 && alltrue(concat(
      [for s in azurerm_orchestrated_virtual_machine_scale_set.allocation : s.tags["experiment"] == "ftg-20261014-example" && s.tags["owner"] == "platform"],
      [for g in azurerm_capacity_reservation_group.allocation : g.tags["experiment"] == "ftg-20261014-example" && g.tags["owner"] == "platform"],
    ))
    error_message = "The scale sets and the capacity reservation groups must carry the experiment tag and var.tags."
  }
}

run "tags_reject_the_experiment_key" {
  command = plan

  variables {
    tags = { Experiment = "ftg" }
  }

  expect_failures = [var.tags]
}

run "resource_group_name_rejects_empty" {
  command = plan

  variables {
    resource_group_name = ""
  }

  expect_failures = [var.resource_group_name]
}

run "resource_group_name_rejects_a_final_period" {
  command = plan

  variables {
    resource_group_name = "platform-rg."
  }

  expect_failures = [var.resource_group_name]
}

run "resource_group_name_rejects_a_quote" {
  command = plan

  variables {
    resource_group_name = "rg'x"
  }

  expect_failures = [var.resource_group_name]
}

run "subnet_id_rejects_empty" {
  command = plan

  variables {
    subnet_id = ""
  }

  expect_failures = [var.subnet_id]
}

run "subnet_id_rejects_a_network_id" {
  command = plan

  variables {
    subnet_id = "/subscriptions/mock-subscription/resourceGroups/platform-net/providers/Microsoft.Network/virtualNetworks/platform-vnet"
  }

  expect_failures = [var.subnet_id]
}

run "subnet_id_rejects_a_name" {
  command = plan

  variables {
    subnet_id = "workloads"
  }

  expect_failures = [var.subnet_id]
}

# The example has 2 cloud-cpu scale set entries in eastus. With the subnet in westus2, both must fail.
run "scale_set_outside_the_subnet_region_fails" {
  command = plan

  variables {
    grant_billable_provisioning = true
    location                    = "westus2"
  }

  expect_failures = [azurerm_orchestrated_virtual_machine_scale_set.allocation]
}

# Reservations need no subnet, so a reservation entry in another region still plans.
run "reservation_groups_in_any_region" {
  command = plan

  variables {
    grant_billable_provisioning = true
    location                    = "westus2"
    targets_file                = "tests/fixtures/reservation_only.yaml"
  }

  assert {
    condition     = length(azurerm_orchestrated_virtual_machine_scale_set.allocation) == 0 && sort(keys(azurerm_capacity_reservation_group.allocation)) == sort(["eastus", "eastus2"])
    error_message = "Without scale sets, the reservation groups must plan in each region of az_capacity_reservation entries."
  }
}

# The scale-set NICs join the subnet. A subnet in another resource group needs a role of the
# prober identity at the scope of that subnet (spec 0005, section 5.1).
run "subnet_in_the_same_group_gets_no_role_assignment" {
  command = plan

  variables {
    grant_billable_provisioning = true
    subnet_id                   = "/subscriptions/mock-subscription/resourceGroups/Platform-RG/providers/Microsoft.Network/virtualNetworks/platform-vnet/subnets/workloads"
  }

  assert {
    condition     = length(azurerm_role_assignment.prober_probe_subnet_joiner) == 0
    error_message = "A subnet in resource_group_name, in any letter case, must get no role assignment."
  }
}

run "control_subnet_in_another_group_gets_no_role_assignment" {
  command = plan

  variables {
    grant_billable_provisioning = true
  }

  assert {
    condition     = length(azurerm_role_assignment.prober_probe_subnet_joiner) == 0 && length(azurerm_orchestrated_virtual_machine_scale_set.allocation) == 1
    error_message = "The control subnet (subnet_id) is in another group and has real scale sets, and must still get no role assignment: it is the control subnet (spec 0005, 5.1.1)."
  }
}

# grant_billable_provisioning defaults to false: a subnet in another group still gets no role
# assignment, even with an active, consented provisioning entry in the file (ADR 0015 section 3,
# the default-false row).
run "subnet_in_another_group_gets_no_role_assignment_without_the_grant" {
  command = plan

  assert {
    condition     = length(azurerm_role_assignment.prober_probe_subnet_joiner) == 0
    error_message = "A subnet in another group, with grant_billable_provisioning left at its default, must get no role assignment."
  }
}

# A probe subnet of another region (an isolated network, spec 0005 section 4.3). The fixture has
# 1 scale set entry in eastus (the control region) and 1 in southcentralus.
run "scale_set_in_a_second_region_uses_that_regions_subnet" {
  command = plan

  variables {
    grant_billable_provisioning = true
    targets_file                = "tests/fixtures/two_regions.yaml"
    probe_subnet_ids = {
      southcentralus = "/subscriptions/mock-subscription/resourceGroups/ftg-probe-scus/providers/Microsoft.Network/virtualNetworks/probe-vnet/subnets/probe"
    }
  }

  assert {
    condition = alltrue([
      length(azurerm_orchestrated_virtual_machine_scale_set.allocation) == 2,
      alltrue([
        for k, s in azurerm_orchestrated_virtual_machine_scale_set.allocation :
        s.network_interface[0].ip_configuration[0].subnet_id == (s.location == "southcentralus" ? var.probe_subnet_ids["southcentralus"] : var.subnet_id)
      ]),
      length([for s in azurerm_orchestrated_virtual_machine_scale_set.allocation : s if s.location == "southcentralus"]) == 1,
    ])
    error_message = "Each scale set must use the subnet of its own region: subnet_id in the control region, the probe_subnet_ids entry elsewhere."
  }
}

run "scale_set_in_a_region_with_no_subnet_fails" {
  command = plan

  variables {
    grant_billable_provisioning = true
    targets_file                = "tests/fixtures/two_regions.yaml"
  }

  expect_failures = [azurerm_orchestrated_virtual_machine_scale_set.allocation]
}

# With an empty probe_subnet_ids, the control region falls back to subnet_id.
run "control_region_falls_back_to_subnet_id" {
  command = plan

  variables {
    grant_billable_provisioning = true
  }

  assert {
    condition = alltrue([
      length(azurerm_orchestrated_virtual_machine_scale_set.allocation) == 1,
      alltrue([for s in azurerm_orchestrated_virtual_machine_scale_set.allocation : s.network_interface[0].ip_configuration[0].subnet_id == var.subnet_id]),
    ])
    error_message = "With no probe_subnet_ids, every scale set in the control region must use subnet_id."
  }
}

# The joiner follows the probe subnet of each region with a real scale set, when that subnet is in
# another group: the control region (eastus, subnet_id) gets none, the southcentralus subnet
# (ftg-probe-scus) gets 1, and a real-scale-set region whose subnet is in the stack's own group
# (any letter case) gets none. westus2 has a subnet but no entry, so it gets none either.
run "subnet_joiner_follows_the_probe_subnet_of_each_region_with_a_scale_set" {
  command = plan

  variables {
    grant_billable_provisioning = true
    targets_file                = "tests/fixtures/two_regions.yaml"
    probe_subnet_ids = {
      southcentralus = "/subscriptions/mock-subscription/resourceGroups/ftg-probe-scus/providers/Microsoft.Network/virtualNetworks/probe-vnet/subnets/probe"
      westus2        = "/subscriptions/mock-subscription/resourceGroups/ftg-probe-w2/providers/Microsoft.Network/virtualNetworks/probe-vnet/subnets/probe"
    }
  }

  assert {
    condition = alltrue([
      length(azurerm_role_assignment.prober_probe_subnet_joiner) == 1,
      azurerm_role_assignment.prober_probe_subnet_joiner["southcentralus"].scope == var.probe_subnet_ids["southcentralus"],
      alltrue([for a in azurerm_role_assignment.prober_probe_subnet_joiner : a.scope != var.subnet_id]),
    ])
    error_message = "The joiner must plan 1 assignment, on the southcentralus probe subnet, none for the control subnet, and none for a region without a scale set."
  }

  assert {
    condition = alltrue([
      sort(azurerm_role_definition.subnet_joiner.permissions[0].actions) == sort(["Microsoft.Network/virtualNetworks/subnets/read", "Microsoft.Network/virtualNetworks/subnets/join/action"]),
      length(coalesce(azurerm_role_definition.subnet_joiner.permissions[0].not_actions, [])) == 0,
      length(coalesce(azurerm_role_definition.subnet_joiner.permissions[0].data_actions, [])) == 0,
    ])
    error_message = "The subnet-joiner role must hold only the subnet read and join actions."
  }
}

# Only eastus2 has a real scale set (eastus is simulated): exactly 1 assignment, on the eastus2
# probe subnet, with the narrow custom role. The eastus probe subnet gets none.
run "only_eastus2_real_gives_exactly_one_assignment_on_the_eastus2_subnet" {
  command = plan

  variables {
    grant_billable_provisioning = true
    targets_file                = "tests/fixtures/eastus2_real.yaml"
    probe_subnet_ids = {
      eastus  = "/subscriptions/mock-subscription/resourceGroups/ftg-probe-east/providers/Microsoft.Network/virtualNetworks/probe-vnet/subnets/probe"
      eastus2 = "/subscriptions/mock-subscription/resourceGroups/ftg-probe-east2/providers/Microsoft.Network/virtualNetworks/probe-vnet/subnets/probe"
    }
  }

  assert {
    condition = alltrue([
      length(azurerm_role_assignment.prober_probe_subnet_joiner) == 1,
      azurerm_role_assignment.prober_probe_subnet_joiner["eastus2"].scope == var.probe_subnet_ids["eastus2"],
      azurerm_role_assignment.prober_probe_subnet_joiner["eastus2"].principal_type == "ServicePrincipal",
      azurerm_role_definition.subnet_joiner.name == "ftg-subnet-joiner",
    ])
    error_message = "With only eastus2 real, the joiner must plan exactly 1 assignment, on the eastus2 probe subnet, with the subnet-joiner role."
  }
}

# Every entry simulated: no scale set plans, so no assignment does either, even with probe subnets.
run "all_simulated_gives_no_assignment" {
  command = plan

  variables {
    grant_billable_provisioning = true
    targets_file                = "../../prober/tests/fixtures/simulated_entry.yaml"
    probe_subnet_ids = {
      eastus2 = "/subscriptions/mock-subscription/resourceGroups/ftg-probe-east2/providers/Microsoft.Network/virtualNetworks/probe-vnet/subnets/probe"
    }
  }

  assert {
    condition     = length(azurerm_role_assignment.prober_probe_subnet_joiner) == 0
    error_message = "With every entry simulated, no subnet joiner may plan."
  }
}

run "probe_subnet_ids_rejects_a_bad_id" {
  command = plan

  variables {
    probe_subnet_ids = { southcentralus = "probe" }
  }

  expect_failures = [var.probe_subnet_ids]
}

run "probe_subnet_ids_rejects_a_duplicate_subnet" {
  command = plan

  variables {
    probe_subnet_ids = {
      southcentralus = "/subscriptions/mock-subscription/resourceGroups/ftg-probe-scus/providers/Microsoft.Network/virtualNetworks/probe-vnet/subnets/probe"
      westus2        = "/SUBSCRIPTIONS/mock-subscription/resourceGroups/ftg-probe-scus/providers/Microsoft.Network/virtualNetworks/probe-vnet/subnets/PROBE"
    }
  }

  expect_failures = [var.probe_subnet_ids]
}

run "probe_subnet_ids_rejects_subnet_id_for_another_region" {
  command = plan

  variables {
    probe_subnet_ids = {
      southcentralus = "/subscriptions/mock-subscription/resourceGroups/platform-net/providers/Microsoft.Network/virtualNetworks/platform-vnet/subnets/workloads"
    }
  }

  expect_failures = [var.probe_subnet_ids]
}

# An entry of probe_subnet_ids for var.location replaces the subnet_id fallback for the scale
# sets. That entry is a probe subnet, not the control subnet, so it gets the joiner.
run "probe_subnet_ids_entry_for_the_control_region_overrides_subnet_id" {
  command = plan

  variables {
    grant_billable_provisioning = true
    probe_subnet_ids = {
      eastus = "/subscriptions/mock-subscription/resourceGroups/ftg-probe-east/providers/Microsoft.Network/virtualNetworks/probe-vnet/subnets/probe"
    }
  }

  assert {
    condition = alltrue([
      length(azurerm_orchestrated_virtual_machine_scale_set.allocation) == 1,
      alltrue([for s in azurerm_orchestrated_virtual_machine_scale_set.allocation : s.network_interface[0].ip_configuration[0].subnet_id == var.probe_subnet_ids["eastus"]]),
      length(azurerm_role_assignment.prober_probe_subnet_joiner) == 1,
      azurerm_role_assignment.prober_probe_subnet_joiner["eastus"].scope == var.probe_subnet_ids["eastus"],
    ])
    error_message = "An eastus entry in probe_subnet_ids must replace subnet_id for the eastus scale sets and get the joiner."
  }
}

# Without the grant, a non-empty probe_subnet_ids in another group plans no joiner.
run "probe_subnet_ids_get_no_joiner_without_the_grant" {
  command = plan

  variables {
    targets_file = "tests/fixtures/two_regions.yaml"
    probe_subnet_ids = {
      southcentralus = "/subscriptions/mock-subscription/resourceGroups/ftg-probe-scus/providers/Microsoft.Network/virtualNetworks/probe-vnet/subnets/probe"
    }
  }

  assert {
    condition     = length(azurerm_role_assignment.prober_probe_subnet_joiner) == 0
    error_message = "With grant_billable_provisioning left at false, no probe subnet joiner may plan."
  }
}

# Region keys that differ only in letter case would collapse into 1 in local.probe_subnets.
run "probe_subnet_ids_rejects_keys_that_differ_only_in_case" {
  command = plan

  variables {
    probe_subnet_ids = {
      southcentralus = "/subscriptions/mock-subscription/resourceGroups/ftg-probe-scus/providers/Microsoft.Network/virtualNetworks/probe-vnet/subnets/probe-a"
      SouthCentralUS = "/subscriptions/mock-subscription/resourceGroups/ftg-probe-scus/providers/Microsoft.Network/virtualNetworks/probe-vnet/subnets/probe-b"
    }
  }

  expect_failures = [var.probe_subnet_ids]
}
