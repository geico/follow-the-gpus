# Tests of the data-container readers (spec 0005, section 5). The providers are
# mocks. The client config object_id is a placeholder, ID-shaped mock value, like the other
# mock_data defaults in this folder, never a real one. It is not GUID-shaped text.
# Run: make test/azure

mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      subscription_id = "mock-subscription"
      tenant_id       = "mock-tenant"
      object_id       = "mock-deployer-oid"
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
  targets_file        = "examples/targets.example.yaml"
  resource_group_name = "platform-rg"
  subnet_id           = "/subscriptions/mock-subscription/resourceGroups/platform-net/providers/Microsoft.Network/virtualNetworks/platform-vnet/subnets/workloads"
}

override_resource {
  target = azurerm_storage_container.data
  values = {
    id = "/subscriptions/mock-subscription/resourceGroups/platform-rg/providers/Microsoft.Storage/storageAccounts/mockdata/blobServices/default/containers/data"
  }
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
  target = azurerm_log_analytics_workspace.sweeper
  values = {
    id = "/subscriptions/mock-subscription/resourceGroups/platform-rg/providers/Microsoft.OperationalInsights/workspaces/ftg-sweeper-law"
  }
}

run "deployer_alone_gets_the_role" {
  command = plan

  assert {
    condition     = toset(keys(azurerm_role_assignment.data_reader)) == toset(["mock-deployer-oid"])
    error_message = "With no extra readers, only the deployer's object_id must get the role assignment."
  }

  assert {
    condition     = alltrue([for r in azurerm_role_assignment.data_reader : r.role_definition_name == "Storage Blob Data Reader"])
    error_message = "The role must be the built-in Storage Blob Data Reader."
  }

  assert {
    condition     = alltrue([for r in azurerm_role_assignment.data_reader : r.scope == azurerm_storage_container.data.id])
    error_message = "The scope must be the data container only."
  }

  assert {
    condition     = toset(keys(azurerm_role_assignment.code_reader)) == toset(["mock-deployer-oid"])
    error_message = "With no extra readers, only the deployer's object_id must get the role assignment on the code container."
  }

  assert {
    condition     = alltrue([for r in azurerm_role_assignment.code_reader : r.role_definition_name == "Storage Blob Data Reader" && r.scope == azurerm_storage_container.code.id])
    error_message = "The code readers must hold the built-in Storage Blob Data Reader on the code container."
  }
}

run "deployer_plus_extra_readers_with_a_duplicate" {
  command = plan

  variables {
    data_reader_principal_ids = [
      "mock-reader-oid-2",
      "mock-reader-oid-3",
      # Duplicate of the deployer's object_id (mocked above). toset must drop it.
      "mock-deployer-oid",
      # Same duplicate, upper case. lower() must fold it onto the same key too, so this must
      # not create a 4th or a 5th assignment.
      "MOCK-DEPLOYER-OID",
      # Same duplicate, with leading and trailing whitespace. trimspace() must fold it onto
      # the same key too, so this must not create a 6th assignment.
      "  mock-deployer-oid  ",
    ]
  }

  assert {
    condition = toset(keys(azurerm_role_assignment.data_reader)) == toset([
      "mock-deployer-oid",
      "mock-reader-oid-2",
      "mock-reader-oid-3",
    ])
    error_message = "The deployer and each extra reader must get exactly 1 role assignment each, with the lower-case, upper-case, and padded duplicates of the deployer dropped."
  }

  assert {
    condition     = length(azurerm_role_assignment.data_reader) == 3
    error_message = "A lower-case, upper-case, or padded duplicate of the deployer in data_reader_principal_ids must not create a 4th, 5th, or 6th assignment."
  }
}

run "code_container_readers_match_the_data_readers" {
  command = plan

  variables {
    data_reader_principal_ids = [
      "mock-reader-oid-2",
      "mock-reader-oid-3",
      "MOCK-DEPLOYER-OID",
      "  mock-deployer-oid  ",
    ]
  }

  assert {
    condition = toset(keys(azurerm_role_assignment.code_reader)) == toset([
      "mock-deployer-oid",
      "mock-reader-oid-2",
      "mock-reader-oid-3",
    ])
    error_message = "The deployer and each extra reader must get exactly 1 role assignment each on the code container, with the duplicates dropped."
  }

  assert {
    condition     = length(azurerm_role_assignment.code_reader) == 3
    error_message = "A duplicate of the deployer must not create a 4th assignment on the code container."
  }

  assert {
    condition     = alltrue([for r in azurerm_role_assignment.code_reader : r.scope == azurerm_storage_container.code.id])
    error_message = "The code readers must be scoped to the code container."
  }
}

run "empty_entry_fails_validation" {
  command = plan

  variables {
    data_reader_principal_ids = ["mock-reader-oid-2", "   "]
  }

  expect_failures = [var.data_reader_principal_ids]
}
# The prober identity reads and lists the data container, for `ftg-prober compact` (ADR 0025,
# ). It gets read only: no write, no delete.
run "prober_reads_the_data_container" {
  command = plan

  assert {
    condition = (
      azurerm_role_assignment.prober_data_reader.role_definition_name == "Storage Blob Data Reader"
      && azurerm_role_assignment.prober_data_reader.scope == azurerm_storage_container.data.id
    )
    error_message = "The prober must hold the built-in Storage Blob Data Reader on the data container only."
  }

  assert {
    condition = (
      length(azurerm_role_definition.data_writer.permissions[0].data_actions) == 2
      && alltrue([for a in azurerm_role_definition.data_writer.permissions[0].data_actions : !strcontains(a, "delete") && !strcontains(a, "read")])
    )
    error_message = "ADR 0025: the data-writer role must stay write and add only: no read, no delete."
  }
}
