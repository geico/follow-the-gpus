# Tests of the targets file to resource mapping. The providers are mocks, so the tests
# need no credentials, no network, and no prober or sweeper folder.
# Run: make test/azure

mock_provider "azurerm" {
  # The data sources are read at plan time, so their values must look like real IDs.
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

run "scale_set_per_cloud_target" {
  command = plan

  variables {
    grant_billable_provisioning = true
  }

  assert {
    condition     = length(azurerm_orchestrated_virtual_machine_scale_set.allocation) == 1
    error_message = "Expected 1 scale set per non-simulated entry (1), simulated entries get none."
  }

  assert {
    condition     = azurerm_orchestrated_virtual_machine_scale_set.allocation["nc40ads-h100-v5-eastus-multi"].name == "ftg-nc40ads-h100-v5-eastus-${substr(sha256("ftg-20261014-example"), 0, 8)}"
    error_message = "Expected the name <prefix>-<sku slug>-<region>-<first 8 characters of the hash of the experiment>."
  }

  assert {
    condition = (
      azurerm_orchestrated_virtual_machine_scale_set.allocation["nc40ads-h100-v5-eastus-multi"].tags["sku"] == "Standard_NC40ads_H100_v5" &&
      azurerm_orchestrated_virtual_machine_scale_set.allocation["nc40ads-h100-v5-eastus-multi"].tags["region"] == "eastus" &&
      azurerm_orchestrated_virtual_machine_scale_set.allocation["nc40ads-h100-v5-eastus-multi"].tags["target"] == "nc40ads-h100-v5-eastus-multi" &&
      azurerm_orchestrated_virtual_machine_scale_set.allocation["nc40ads-h100-v5-eastus-multi"].tags["experiment"] == "ftg-20261014-example"
    )
    error_message = "A scale set must carry the tags experiment, sku (exact), region (lower case), and target: the prober selects it by them (ADR 0022)."
  }

  assert {
    condition     = azurerm_orchestrated_virtual_machine_scale_set.allocation["nc40ads-h100-v5-eastus-multi"].single_placement_group == false
    error_message = "Every scale set must set single_placement_group to false."
  }

  assert {
    condition     = alltrue([for s in azurerm_orchestrated_virtual_machine_scale_set.allocation : s.instances == 0])
    error_message = "Each scale set must start at capacity 0."
  }

  assert {
    condition     = alltrue([for s in azurerm_orchestrated_virtual_machine_scale_set.allocation : s.tags["experiment"] == "ftg-20261014-example" && s.tags["run_id"] == "none"])
    error_message = "Each scale set must carry the experiment tag and a run_id placeholder."
  }

  assert {
    condition     = length(azapi_update_resource.allocation_vmss_policy) == 1
    error_message = "Each scale set must get the resiliency and delete option update."
  }

  assert {
    condition = alltrue([
      for u in azapi_update_resource.allocation_vmss_policy :
      u.body.properties.virtualMachineProfile.storageProfile.osDisk.deleteOption == "Delete" &&
      u.body.properties.virtualMachineProfile.networkProfile.networkInterfaceConfigurations[0].name == "primary" &&
      u.body.properties.virtualMachineProfile.networkProfile.networkInterfaceConfigurations[0].properties.deleteOption == "Delete" &&
      u.body.properties.resiliencyPolicy.resilientVMDeletionPolicy.enabled == true
    ])
    error_message = "Each scale set must delete its OS disk and NIC with the VM and use resilient deletion."
  }

  assert {
    condition     = sort(keys(azurerm_capacity_reservation_group.allocation)) == sort(["eastus", "eastus2"])
    error_message = "Expected 1 capacity reservation group per region in provisioning_probes."
  }

  assert {
    condition     = azurerm_capacity_reservation_group.allocation["eastus"].name == "ftg-crg-eastus-${substr(sha256("ftg-20261014-example"), 0, 8)}"
    error_message = "Expected the name <prefix>-crg-<region>-<first 8 characters of the hash of the experiment>."
  }

  assert {
    condition = alltrue([
      for r, g in azurerm_capacity_reservation_group.allocation :
      g.tags["experiment"] == "ftg-20261014-example" && g.tags["region"] == r
    ])
    error_message = "A reservation group must carry the tags experiment and region: the prober selects it by them (ADR 0022)."
  }

  assert {
    condition     = length(azurerm_consumption_budget_resource_group.stack) == 0
    error_message = "No budget when alert_email is empty."
  }

  assert {
    condition     = length(azurerm_monitor_action_group.alerts) == 0 && length(azurerm_monitor_scheduled_query_rules_alert_v2.sweeper_stopped) == 0
    error_message = "No action group and no sweeper alert when alert_email is empty."
  }
}

run "az_capacity_reservation_entries_get_no_scale_set" {
  command = plan

  variables {
    grant_billable_provisioning = true
    targets_file                = "tests/fixtures/reservation_only.yaml"
  }

  assert {
    condition     = length(azurerm_orchestrated_virtual_machine_scale_set.allocation) == 0 && sort(keys(azurerm_capacity_reservation_group.allocation)) == sort(["eastus", "eastus2"])
    error_message = "An az_capacity_reservation entry gets a reservation group in its Region and no scale set, whatever its size."
  }
}

run "an_az_vmss_flex_entry_gets_a_scale_set_whatever_the_size" {
  command = plan

  variables {
    grant_billable_provisioning = true
    targets_file                = "tests/fixtures/same_size_both_types.yaml"
  }

  assert {
    condition     = sort(keys(azurerm_orchestrated_virtual_machine_scale_set.allocation)) == sort(["b2s-eastus-multi"]) && sort(keys(azurerm_capacity_reservation_group.allocation)) == sort(["eastus"])
    error_message = "The same size and Region under both probe types gets 1 scale set and 1 reservation group."
  }

  assert {
    condition     = length(local.targets_faults) == 0
    error_message = "The same size and Region under both probe types is allowed."
  }
}

run "the_names_hold_the_hash_of_the_experiment" {
  command = plan

  variables {
    grant_billable_provisioning = true
    targets_file                = "tests/fixtures/two_regions.yaml"
    probe_subnet_ids = {
      southcentralus = "/subscriptions/mock-subscription/resourceGroups/ftg-probe-scus/providers/Microsoft.Network/virtualNetworks/probe-vnet/subnets/probe"
    }
  }

  assert {
    condition = alltrue(concat(
      [for s in azurerm_orchestrated_virtual_machine_scale_set.allocation : endswith(s.name, "-${substr(sha256("ftg-20261014-two-regions"), 0, 8)}") && length(s.name) <= 64],
      [for g in azurerm_capacity_reservation_group.allocation : endswith(g.name, "-${substr(sha256("ftg-20261014-two-regions"), 0, 8)}")],
    ))
    error_message = "Another experiment gets other names, so 2 experiments never collide in 1 resource group."
  }
}

run "no_grant_plans_nothing_regardless_of_the_file" {
  command = plan

  # The default: grant_billable_provisioning is false. examples/targets.example.yaml (the
  # default targets_file here) has 2 active, consented provisioning_probes entries; none of that
  # matters, because the variable alone decides whether anything plans (ADR 0015).
  assert {
    condition     = length(azurerm_capacity_reservation_group.allocation) == 0 && length(azurerm_orchestrated_virtual_machine_scale_set.allocation) == 0 && length(azapi_update_resource.allocation_vmss_policy) == 0
    error_message = "grant_billable_provisioning defaults to false, so no provisioning resource plans, no matter what the file lists."
  }

  assert {
    condition     = length(azurerm_role_assignment.prober_capacity_allocator) == 0 && length(azurerm_role_assignment.prober_probe_subnet_joiner) == 0
    error_message = "grant_billable_provisioning defaults to false, so neither of the 2 billable-rights role assignments plans."
  }

  assert {
    condition     = azurerm_role_assignment.sweeper_cleanup.scope == data.azurerm_resource_group.stack.id
    error_message = "sweeper_cleanup is unconditional: the sweeper must be able to delete and scale to 0 a leaked resource, so it keeps cleanup rights even with grant_billable_provisioning at its default false."
  }
}

run "grant_true_plans_rights_with_no_active_entry" {
  command = plan

  # simulated_entry.yaml's 1 entry is simulate: true (no active entry at all), yet the grant
  # alone still plans the role assignments: the variable does not read the file (ADR 0015).
  variables {
    grant_billable_provisioning = true
    targets_file                = "../../prober/tests/fixtures/simulated_entry.yaml"
  }

  assert {
    condition     = length(azurerm_role_assignment.prober_capacity_allocator) == 1 && length(azurerm_role_assignment.prober_probe_subnet_joiner) == 0
    error_message = "grant_billable_provisioning = true must plan the capacity allocator even with no active provisioning entry, and no subnet joiner, since no scale set plans."
  }

  assert {
    condition     = azurerm_role_assignment.sweeper_cleanup.scope == data.azurerm_resource_group.stack.id
    error_message = "sweeper_cleanup is unconditional and plans regardless of grant_billable_provisioning or the file."
  }

  assert {
    condition     = length(azurerm_orchestrated_virtual_machine_scale_set.allocation) == 0
    error_message = "A simulated entry still gets no scale set: the grant decides the rights, not whether a scale set plans for this 1 entry."
  }
}

run "budget_with_alert_email" {
  command = plan

  variables {
    alert_email = "ops@example.com"
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
    condition     = length(azurerm_consumption_budget_resource_group.stack) == 1
    error_message = "A budget is created when alert_email is set."
  }

  assert {
    condition     = sort([for n in azurerm_consumption_budget_resource_group.stack[0].notification : tostring(n.threshold)]) == sort(["80", "100"])
    error_message = "The budget must alert at 80 and 100 percent."
  }

  assert {
    condition     = azurerm_consumption_budget_resource_group.stack[0].amount == 50
    error_message = "The default budget amount is 50."
  }
}

run "sweeper_alert_with_alert_email" {
  command = plan

  variables {
    alert_email = "ops@example.com"
  }

  # The IDs are known only after apply. These overrides make them known at plan time.
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
    condition     = length(azurerm_monitor_action_group.alerts) == 1 && length(azurerm_monitor_scheduled_query_rules_alert_v2.sweeper_stopped) == 1
    error_message = "An action group and a sweeper alert are created when alert_email is set."
  }

  assert {
    condition = (
      length(azurerm_monitor_action_group.alerts[0].email_receiver) == 1 &&
      azurerm_monitor_action_group.alerts[0].email_receiver[0].email_address == "ops@example.com" &&
      length(azurerm_monitor_action_group.alerts[0].short_name) <= 12
    )
    error_message = "The action group sends email to alert_email and has a short name of 12 characters or fewer."
  }

  assert {
    condition     = azurerm_monitor_scheduled_query_rules_alert_v2.sweeper_stopped[0].scopes == tolist([azurerm_application_insights.sweeper.id])
    error_message = "The rule must have the sweeper Application Insights resource as its only scope."
  }

  assert {
    condition     = azurerm_monitor_scheduled_query_rules_alert_v2.sweeper_stopped[0].action[0].action_groups == tolist([azurerm_monitor_action_group.alerts[0].id])
    error_message = "The rule must send to the action group."
  }

  assert {
    condition = (
      azurerm_monitor_scheduled_query_rules_alert_v2.sweeper_stopped[0].evaluation_frequency == "PT5M" &&
      azurerm_monitor_scheduled_query_rules_alert_v2.sweeper_stopped[0].window_duration == "PT10M" &&
      azurerm_monitor_scheduled_query_rules_alert_v2.sweeper_stopped[0].auto_mitigation_enabled == true
    )
    error_message = "The rule runs every 5 minutes over 10 minutes and resolves itself."
  }

  assert {
    condition = (
      azurerm_monitor_scheduled_query_rules_alert_v2.sweeper_stopped[0].criteria[0].time_aggregation_method == "Count" &&
      azurerm_monitor_scheduled_query_rules_alert_v2.sweeper_stopped[0].criteria[0].operator == "GreaterThan" &&
      azurerm_monitor_scheduled_query_rules_alert_v2.sweeper_stopped[0].criteria[0].threshold == 0
    )
    error_message = "The rule fires when the query returns 1 row or more."
  }

  assert {
    condition = (
      strcontains(azurerm_monitor_scheduled_query_rules_alert_v2.sweeper_stopped[0].criteria[0].query, "message startswith \"sweeper run:\"") &&
      strcontains(azurerm_monitor_scheduled_query_rules_alert_v2.sweeper_stopped[0].criteria[0].query, "ago(10m)") &&
      strcontains(azurerm_monitor_scheduled_query_rules_alert_v2.sweeper_stopped[0].criteria[0].query, "requests") &&
      strcontains(azurerm_monitor_scheduled_query_rules_alert_v2.sweeper_stopped[0].criteria[0].query, "where runs == 0 or failed > 0")
    )
    error_message = "The query must check the health line in the last 10 minutes and the failed requests."
  }
}

run "names_and_settings" {
  command = plan

  assert {
    condition     = azurerm_linux_function_app.sweeper.app_settings["FTG_MAX_AGE_SECONDS"] == "180" && azurerm_linux_function_app.sweeper.app_settings["FTG_PROBE_RG"] == "platform-rg"
    error_message = "The sweeper app settings must match the contract."
  }

  assert {
    condition     = azurerm_linux_function_app.sweeper.app_settings["FTG_DATA_STORAGE_ACCOUNT"] == azurerm_storage_account.data.name
    error_message = "The sweeper must get the data storage account name, to write its own log objects (spec 0003 section 8.2)."
  }

  assert {
    condition = (
      azurerm_role_assignment.sweeper_data_writer.scope == azurerm_storage_container.data.id &&
      azurerm_role_assignment.sweeper_data_writer.role_definition_id == azurerm_role_definition.data_writer.role_definition_resource_id &&
      azurerm_role_assignment.sweeper_data_writer.condition_version == "2.0" &&
      azurerm_role_assignment.sweeper_data_writer.condition == "((!(ActionMatches{'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write'}) AND !(ActionMatches{'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/add/action'})) OR (@Resource[Microsoft.Storage/storageAccounts/blobServices/containers/blobs:path] StringLike 'experiment=ftg-20261014-example/logs/*'))"
    )
    error_message = "The sweeper must write only under experiment=<name>/logs/, by an ABAC path condition on the data container."
  }

  assert {
    condition = (
      azurerm_role_assignment.prober_data_writer.scope == azurerm_storage_container.data.id &&
      azurerm_role_assignment.prober_data_writer.role_definition_id == azurerm_role_definition.data_writer.role_definition_resource_id &&
      azurerm_role_assignment.prober_data_writer.condition_version == "2.0" &&
      azurerm_role_assignment.prober_data_writer.condition == "((!(ActionMatches{'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write'}) AND !(ActionMatches{'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/add/action'})) OR (@Resource[Microsoft.Storage/storageAccounts/blobServices/containers/blobs:path] StringLike 'experiment=ftg-20261014-example/*'))"
    )
    error_message = "The prober must write only under experiment=<name>/, by an ABAC path condition on the data container."
  }

  assert {
    condition = (
      toset(one(azurerm_role_definition.data_writer.permissions).data_actions) == toset([
        "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write",
        "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/add/action",
      ]) &&
      length(coalesce(one(azurerm_role_definition.data_writer.permissions).actions, [])) == 0 &&
      length(coalesce(one(azurerm_role_definition.data_writer.permissions).not_data_actions, [])) == 0
    )
    error_message = "The data writer role must hold only blob write and add: no read and no delete."
  }

  assert {
    condition     = length(azurerm_storage_management_policy.logs.rule) == 1 && contains(azurerm_storage_management_policy.logs.rule[0].filters[0].prefix_match, "data/experiment=ftg-20261014-example/logs/") && azurerm_storage_management_policy.logs.rule[0].actions[0].base_blob[0].delete_after_days_since_modification_greater_than == 90
    error_message = "The logs prefix must expire after 90 days, apart from the rows."
  }

  assert {
    condition     = azurerm_linux_function_app.sweeper.site_config[0].application_stack[0].python_version == "3.12"
    error_message = "The sweeper runs Python 3.12."
  }

  assert {
    condition     = azurerm_storage_account.data.account_replication_type == "LRS" && azurerm_storage_account.data.min_tls_version == "TLS1_2" && azurerm_storage_account.data.allow_nested_items_to_be_public == false
    error_message = "The data account is LRS, TLS 1.2, no public blob access."
  }

  assert {
    condition = length(setsubtract([
      "Microsoft.Compute/virtualMachines/write",
      "Microsoft.Network/networkInterfaces/write",
      "Microsoft.Network/networkInterfaces/join/action",
      "Microsoft.Network/virtualNetworks/subnets/join/action",
      "Microsoft.Network/networkSecurityGroups/join/action",
      "Microsoft.Resources/tags/write",
    ], azurerm_role_definition.capacity_allocator.permissions[0].actions)) == 0
    error_message = "The prober role must hold the VM, NIC, subnet, and NSG rights that a Flexible scale-out needs."
  }

  assert {
    condition     = startswith(azurerm_storage_blob.prober.name, "prober-") && startswith(azurerm_storage_blob.config.name, "config-") && endswith(azurerm_storage_blob.config.name, ".zip")
    error_message = "The code container holds 2 zips: prober-<sha>.zip and config-<sha>.zip."
  }
}

run "sweeper_consumption_plan_by_default" {
  command = plan

  assert {
    condition     = azurerm_service_plan.sweeper.sku_name == "Y1"
    error_message = "The default sweeper plan is Y1 (Linux consumption)."
  }

  assert {
    condition     = azurerm_linux_function_app.sweeper.site_config[0].always_on == false
    error_message = "Always on must be off on a consumption plan."
  }
}

run "sweeper_dedicated_plan" {
  command = plan

  variables {
    sweeper_plan_sku = "B1"
  }

  assert {
    condition     = azurerm_service_plan.sweeper.sku_name == "B1"
    error_message = "The sweeper plan must use sweeper_plan_sku."
  }

  assert {
    condition     = azurerm_linux_function_app.sweeper.site_config[0].always_on == true
    error_message = "Always on must be on for a dedicated plan, else the timer stops when the runtime idles."
  }

  assert {
    condition = (
      !contains(keys(azurerm_linux_function_app.sweeper.app_settings), "WEBSITE_RUN_FROM_PACKAGE") &&
      azurerm_linux_function_app.sweeper.app_settings["SCM_DO_BUILD_DURING_DEPLOYMENT"] == "true" &&
      azurerm_linux_function_app.sweeper.app_settings["ENABLE_ORYX_BUILD"] == "true"
    )
    error_message = "A dedicated plan keeps the remote build and does not set WEBSITE_RUN_FROM_PACKAGE."
  }

  assert {
    condition     = can(regex("/\\.terraform/ftg-build/sweeper-[0-9a-f]{64}\\.zip$", azurerm_linux_function_app.sweeper.zip_deploy_file))
    error_message = "A dedicated plan keeps the zip deploy."
  }
}

run "sweeper_plan_sku_rejects_other_values" {
  command = plan

  variables {
    sweeper_plan_sku = "P1v3"
  }

  expect_failures = [var.sweeper_plan_sku]
}

run "sweeper_same_region_by_default" {
  command = plan

  assert {
    condition = alltrue([
      azurerm_service_plan.sweeper.location == "eastus",
      azurerm_linux_function_app.sweeper.location == "eastus",
      azurerm_storage_account.func.location == "eastus",
      azurerm_application_insights.sweeper.location == "eastus",
      azurerm_log_analytics_workspace.sweeper.location == "eastus",
    ])
    error_message = "With sweeper_location null, each sweeper resource must use var.location."
  }

  assert {
    condition = alltrue([
      azurerm_service_plan.sweeper.name == "ftg-sweeper-eastus-plan",
      azurerm_log_analytics_workspace.sweeper.name == "ftg-sweeper-eastus-law",
      azurerm_application_insights.sweeper.name == "ftg-sweeper-eastus-ai",
      startswith(azurerm_linux_function_app.sweeper.name, "ftg-sweeper-eastus-"),
    ])
    error_message = "Each sweeper resource name must carry the sweeper region."
  }

  assert {
    condition     = azurerm_storage_account.func.name == "ftgfunc${substr(sha256("${data.azurerm_client_config.current.subscription_id}eastus"), 0, 8)}"
    error_message = "The function storage account name must hold a hash of the subscription and the sweeper region."
  }
}

run "sweeper_in_another_region" {
  command = plan

  variables {
    sweeper_location = "centralus"
    alert_email      = "ops@example.com"
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
      azurerm_service_plan.sweeper.location == "centralus",
      azurerm_linux_function_app.sweeper.location == "centralus",
      azurerm_storage_account.func.location == "centralus",
      azurerm_application_insights.sweeper.location == "centralus",
      azurerm_log_analytics_workspace.sweeper.location == "centralus",
    ])
    error_message = "A set sweeper_location must be the region of each sweeper resource."
  }

  assert {
    condition = alltrue([
      azurerm_service_plan.sweeper.name == "ftg-sweeper-centralus-plan",
      azurerm_log_analytics_workspace.sweeper.name == "ftg-sweeper-centralus-law",
      azurerm_application_insights.sweeper.name == "ftg-sweeper-centralus-ai",
      startswith(azurerm_linux_function_app.sweeper.name, "ftg-sweeper-centralus-"),
    ])
    error_message = "A set sweeper_location must be in each sweeper resource name."
  }

  assert {
    condition = alltrue([
      azurerm_storage_account.func.name == "ftgfunc${substr(sha256("${data.azurerm_client_config.current.subscription_id}centralus"), 0, 8)}",
      azurerm_storage_account.func.name != "ftgfunc${substr(sha256("${data.azurerm_client_config.current.subscription_id}eastus"), 0, 8)}",
      can(regex("^[a-z0-9]{3,24}$", azurerm_storage_account.func.name)),
    ])
    error_message = "The function storage account name must change with sweeper_location and keep the storage name rules."
  }

  assert {
    condition = alltrue([
      azurerm_service_plan.sweeper.resource_group_name == "platform-rg",
      azurerm_linux_function_app.sweeper.resource_group_name == "platform-rg",
      azurerm_storage_account.func.resource_group_name == "platform-rg",
      azurerm_application_insights.sweeper.resource_group_name == "platform-rg",
      azurerm_log_analytics_workspace.sweeper.resource_group_name == "platform-rg",
    ])
    error_message = "The sweeper resources must stay in resource_group_name."
  }

  assert {
    condition = alltrue([
      azurerm_storage_account.data.location == "eastus",
      azurerm_linux_virtual_machine.prober.location == "eastus",
    ])
    error_message = "The data storage and the prober VM must stay in var.location."
  }

  assert {
    condition     = azurerm_linux_function_app.sweeper.app_settings["FTG_PROBE_RG"] == "platform-rg"
    error_message = "The sweeper must still sweep resource_group_name."
  }

  assert {
    condition     = azurerm_monitor_scheduled_query_rules_alert_v2.sweeper_stopped[0].location == "centralus"
    error_message = "The alert rule must be in the region of Application Insights."
  }
}

run "sweeper_location_rejects_empty" {
  command = plan

  variables {
    sweeper_location = " "
  }

  expect_failures = [var.sweeper_location]
}

# This run pins the plan of the repo's real config/targets.yaml, with the grant on: the
# capacity reservation groups plan 1 per region, and every entry's simulate is true, so no
# scale set plans.
run "reservation_groups_plan_in_all_matrix_regions_with_the_repo_targets_file" {
  command = plan

  variables {
    targets_file                = "../../config/targets.yaml"
    grant_billable_provisioning = true
  }

  assert {
    condition     = sort(keys(azurerm_capacity_reservation_group.allocation)) == sort(["eastus", "eastus2", "southcentralus", "westus2", "westus3", "centralus", "northcentralus", "westus"])
    error_message = "Expected 1 capacity reservation group per region of the repo file's provisioning_probes (the 8 regions of the provisioning matrix that have an entry, ADR 0018; westcentralus offers no matrix size)."
  }

  assert {
    condition     = length(azurerm_orchestrated_virtual_machine_scale_set.allocation) == 0
    error_message = "Every provisioning_probes entry of the repo file is simulate: true; no scale set should plan."
  }
}

# The live, default deploy: the repo's real config/targets.yaml, with the grant at its default
# (false). Nothing plans, even though the file lists 66 provisioning_probes entries.
run "reservation_groups_need_the_grant_with_the_repo_targets_file" {
  command = plan

  variables {
    targets_file = "../../config/targets.yaml"
  }

  assert {
    condition     = length(azurerm_capacity_reservation_group.allocation) == 0 && length(azurerm_orchestrated_virtual_machine_scale_set.allocation) == 0
    error_message = "grant_billable_provisioning defaults to false, so the repo's own targets file plans no provisioning resource today."
  }
}

# An old key must fail the plan, not give an empty list that plans the removal of the reservation
# groups and scale sets. The message is local.targets_faults, the text of `check-config`.
run "old_section_key_fails_the_plan" {
  command = plan

  variables {
    targets_file                = "tests/fixtures/old_key.yaml"
    grant_billable_provisioning = true
  }

  expect_failures = [terraform_data.targets_check]
}

run "old_cadence_key_fails_the_plan" {
  command = plan

  variables {
    targets_file = "tests/fixtures/old_cadence_key.yaml"
  }

  expect_failures = [terraform_data.targets_check]
}

# The old list shape, the removed `route` key, an unknown probe type, and 2 az_vmss_flex entries for
# 1 size and Region (any letter case) fail the plan with the text of `check-config` (ADR 0022).
run "old_list_shape_fails_the_plan" {
  command = plan

  variables {
    targets_file                = "tests/fixtures/old_list_shape.yaml"
    grant_billable_provisioning = true
  }

  expect_failures = [terraform_data.targets_check]
}

run "route_key_fails_the_plan" {
  command = plan

  variables {
    targets_file                = "tests/fixtures/route_key.yaml"
    grant_billable_provisioning = true
  }

  expect_failures = [terraform_data.targets_check]
}

run "unknown_probe_type_fails_the_plan" {
  command = plan

  variables {
    targets_file                = "tests/fixtures/unknown_probe_type.yaml"
    grant_billable_provisioning = true
  }

  expect_failures = [terraform_data.targets_check]
}

# An empty `az_vmss_flex:` is null, not a list: it must fail the plan, not plan the destroy of the scale sets.
run "a_probe_type_with_no_list_fails_the_plan" {
  command = plan

  variables {
    targets_file                = "tests/fixtures/null_probe_type.yaml"
    grant_billable_provisioning = true
  }

  expect_failures = [terraform_data.targets_check]

  # `expect_failures` does not check the text, so assert it on the fault list.
  assert {
    condition     = contains(local.targets_faults, "azure.provisioning_probes.az_vmss_flex must be a list of entries")
    error_message = "A null probe type must give the 'must be a list of entries' fault."
  }
}

run "two_az_vmss_flex_entries_for_1_size_and_region_fail_the_plan" {
  command = plan

  variables {
    targets_file                = "tests/fixtures/duplicate_vmss.yaml"
    grant_billable_provisioning = true
  }

  expect_failures = [terraform_data.targets_check]
}

# `simulate: "false"` is a string, not a boolean: it must fail the plan, not count as a real probe.
run "a_non_boolean_simulate_fails_the_plan" {
  command = plan

  variables {
    targets_file                = "tests/fixtures/string_simulate.yaml"
    grant_billable_provisioning = true
  }

  expect_failures = [terraform_data.targets_check]
}

# The `single` placement group mode is removed (ADR 0021): the plan fails with the check-config text.
run "single_placement_group_mode_fails_the_plan" {
  command = plan

  variables {
    targets_file                = "tests/fixtures/single_mode.yaml"
    grant_billable_provisioning = true
  }

  expect_failures = [terraform_data.targets_check]
}

run "repo_targets_file_passes_the_key_check" {
  command = plan

  variables {
    targets_file = "../../config/targets.yaml"
  }

  assert {
    condition     = length(local.targets_faults) == 0
    error_message = "The repo targets file has an unknown key."
  }
}

# An empty `cadence:` or an empty `azure:` section is null, not a mapping. The key check must
# read it as empty, not crash the plan with "keys: argument must not be null".
run "null_cadence_passes_the_key_check" {
  command = plan

  variables {
    targets_file = "tests/fixtures/null_cadence.yaml"
  }

  assert {
    condition     = length(local.targets_faults) == 0
    error_message = "An empty cadence must not give a key-check fault."
  }
}

run "null_section_passes_the_key_check" {
  command = plan

  variables {
    targets_file = "tests/fixtures/null_section.yaml"
  }

  assert {
    condition     = length(local.targets_faults) == 0
    error_message = "An empty azure section must not give a key-check fault."
  }
}

run "string_section_fails_the_plan" {
  command = plan

  variables {
    targets_file = "tests/fixtures/string_section.yaml"
  }

  expect_failures = [terraform_data.targets_check]
}

run "string_cadence_fails_the_plan" {
  command = plan

  variables {
    targets_file = "tests/fixtures/string_cadence.yaml"
  }

  expect_failures = [terraform_data.targets_check]
}
