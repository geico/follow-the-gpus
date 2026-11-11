output "store_url" {
  description = "Store URL of the prober and the dashboard: az://<account>/data/"
  value       = local.store_url
}

output "data_storage_account" {
  description = "Name of the data storage account."
  value       = azurerm_storage_account.data.name
}

output "probe_resource_group" {
  description = "Name of the resource group that holds the probe resources: resource_group_name."
  value       = var.resource_group_name
}

output "control_resource_group" {
  description = "Name of the resource group that holds the prober VM and the sweeper: resource_group_name."
  value       = var.resource_group_name
}

output "function_app_name" {
  description = "Name of the sweeper Function App."
  value       = azurerm_linux_function_app.sweeper.name
}

output "prober_vm_id" {
  description = "Resource ID of the prober VM."
  value       = azurerm_linux_virtual_machine.prober.id
}

output "experiment" {
  description = "Experiment name: the data/experiment=<name>/ prefix that holds the logs."
  value       = local.experiment
}

output "sweeper_code_sha" {
  description = "Hash of the sweeper code that the last apply deployed. The running sweeper logs the same value (code_sha); make verify/azure-sweeper compares them."
  value       = local.sweeper_src_sha
}
