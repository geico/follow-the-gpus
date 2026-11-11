# Azure stack of the GPU capacity tracker. The spec is spec/0005-azure-stack-spec.md.
# State is local by default. The subscription comes from var.subscription_id, else ARM_SUBSCRIPTION_ID.

terraform {
  required_version = ">= 1.10"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81"
    }
    # azapi sets the scale set properties that azurerm does not expose: the OS disk and NIC
    # delete options and the resilient VM deletion policy. 2.9 added the list merge options.
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.9"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  subscription_id = var.subscription_id

  features {
    resource_group {
      # The stack creates no resource group, so this setting has no effect on destroy.
      prevent_deletion_if_contains_resources = false
    }
  }
}

provider "azapi" {
  subscription_id = var.subscription_id
}

data "azurerm_client_config" "current" {}

data "azurerm_subscription" "current" {}
