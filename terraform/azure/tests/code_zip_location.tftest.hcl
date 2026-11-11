# The archive provider runs for real here (not mocked), so the zips are built from the real
# ../../prober and ../../config folders. This lets scripts/check-stale-build-artifact.sh plant a
# stale file at the old dist/ output path and show that a real plan never reads or writes it.
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
mock_provider "tls" {}

variables {
  targets_file        = "examples/targets.example.yaml"
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

run "zip_builds_outside_dist" {
  command = plan

  assert {
    condition = alltrue([
      !strcontains(data.archive_file.prober.output_path, "/dist/"),
      !strcontains(data.archive_file.config.output_path, "/dist/"),
      !strcontains(data.archive_file.sweeper.output_path, "/dist/"),
    ])
    error_message = "The zips must not land under dist/: a tool that re-copies the checkout between plan and apply can overwrite a file there with a stale one, so the uploaded bytes would not match the planned hash key or the planned zip_deploy_file."
  }

  assert {
    condition = alltrue([
      strcontains(data.archive_file.prober.output_path, "/.terraform/"),
      strcontains(data.archive_file.config.output_path, "/.terraform/"),
      strcontains(data.archive_file.sweeper.output_path, "/.terraform/"),
    ])
    error_message = "The zips must land under .terraform/, which a copy of the checkout does not reach."
  }
}
