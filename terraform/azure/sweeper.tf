# Sweeper: a timer-triggered Function App, Python 3.12. The plan is Linux consumption (Y1) by
# default, or a dedicated plan (sweeper_plan_sku) for a subscription with no Y1 quota.
# The code is sweeper/azure/ (function_app.py, requirements.txt, host.json).

locals {
  # A dedicated plan idles the Functions runtime without Always on, and the timer stops.
  sweeper_dedicated_plan = var.sweeper_plan_sku != "Y1"

  # The sweeper resources can sit in another region than var.location. The sweeper reaches the
  # probe resources through ARM, which does not depend on the region of the caller.
  sweeper_location = coalesce(var.sweeper_location, var.location)

  # The region is in each sweeper resource name. A deleted Log Analytics workspace keeps its name
  # for 14 days in its old region. A new workspace of the same name in another region fails.
  # The storage account name has no room for the region (24 characters), so it holds a hash.
  sweeper_region_code    = replace(lower(local.sweeper_location), "/[^a-z0-9]/", "")
  sweeper_storage_suffix = substr(sha256("${data.azurerm_client_config.current.subscription_id}${local.sweeper_region_code}"), 0, 8)
}

# The zip path holds a hash of the sweeper source. zip_deploy_file is not ForceNew and the provider
# deploys the zip only when its value (the path) changes
# (internal/services/appservice/linux_function_app_resource.go, v4.81.0: "if
# metadata.ResourceData.HasChange("zip_deploy_file")"). A constant path would give no plan diff
# for a code change, and the new code would never deploy. When the deploy fails, the SDK sets the
# new path into state anyway (read from the source, not seen live; see
# research/learnings/azure-failed-zip-deploy-can-leave-a-clean-plan.md), so a clean plan does not
# prove that the code runs. The sweeper computes the same hash over its own files at run time and logs it
# (sweeper/azure/sweeper/code_sha.py); `make verify/azure-sweeper` compares the log with the
# sweeper_code_sha output. The two must list the same files, in the same way:
#   function_app.py, host.json, requirements.txt, and every .py file of the sweeper/ package;
#   the hash is sha256 of "<path>:<sha256 of the file>" entries joined by ",", sorted by path.
# Other files of the zip (pyproject.toml, uv.lock) are not used at run time, and the remote build
# adds .python_packages (documented in the same brief), so they stay out of the hash.
locals {
  sweeper_src_dir = "${path.module}/../../sweeper/azure"
  sweeper_src_files = sort([
    for f in fileset(local.sweeper_src_dir, "**") : f
    if can(regex("^(function_app\\.py|host\\.json|requirements\\.txt|sweeper/([^/]+/)*[^/]+\\.py)$", f))
    && !can(regex("(^|/)(\\.venv|__pycache__|tests|\\.pytest_cache|\\.ruff_cache|\\.python_packages)(/|$)", f))
  ])
  sweeper_src_sha = sha256(join(",", [for f in local.sweeper_src_files : "${f}:${filesha256("${local.sweeper_src_dir}/${f}")}"]))
}

resource "azurerm_storage_account" "func" {
  name                            = "${var.name_prefix}func${local.sweeper_storage_suffix}"
  resource_group_name             = var.resource_group_name
  location                        = local.sweeper_location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false
  tags                            = local.stack_tags
}

resource "azurerm_service_plan" "sweeper" {
  name                = "${var.name_prefix}-sweeper-${local.sweeper_region_code}-plan"
  resource_group_name = var.resource_group_name
  location            = local.sweeper_location
  os_type             = "Linux"
  sku_name            = var.sweeper_plan_sku
  tags                = local.stack_tags
}

resource "azurerm_log_analytics_workspace" "sweeper" {
  name                = "${var.name_prefix}-sweeper-${local.sweeper_region_code}-law"
  resource_group_name = var.resource_group_name
  location            = local.sweeper_location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.stack_tags
}

resource "azurerm_application_insights" "sweeper" {
  name                = "${var.name_prefix}-sweeper-${local.sweeper_region_code}-ai"
  resource_group_name = var.resource_group_name
  location            = local.sweeper_location
  application_type    = "web"
  workspace_id        = azurerm_log_analytics_workspace.sweeper.id
  tags                = local.stack_tags
}

data "archive_file" "sweeper" {
  type       = "zip"
  source_dir = local.sweeper_src_dir
  # local.code_build_dir (control.tf) is under .terraform/, not the git-ignored dist/, for the
  # same reason as the prober and config zips: a copy of the checkout cannot reach it.
  output_path      = "${local.code_build_dir}/sweeper-${local.sweeper_src_sha}.zip"
  output_file_mode = "0644"
  # Each name is left out at every depth. The zip root holds function_app.py, host.json,
  # requirements.txt, and the sweeper package. The remote build installs requirements.txt.
  excludes = flatten([
    for n in [".venv", "__pycache__", "tests", ".pytest_cache", ".ruff_cache", ".python_packages", "local.settings.json"] :
    ["**/${n}", "**/${n}/**"]
  ])
}

resource "azurerm_linux_function_app" "sweeper" {
  name                = "${var.name_prefix}-sweeper-${local.sweeper_region_code}-${local.suffix}"
  resource_group_name = var.resource_group_name
  location            = local.sweeper_location
  service_plan_id     = azurerm_service_plan.sweeper.id
  tags                = local.stack_tags

  storage_account_name       = azurerm_storage_account.func.name
  storage_account_access_key = azurerm_storage_account.func.primary_access_key

  functions_extension_version = "~4"
  builtin_logging_enabled     = false
  https_only                  = true
  zip_deploy_file             = data.archive_file.sweeper.output_path

  identity {
    type = "SystemAssigned"
  }

  site_config {
    always_on                              = local.sweeper_dedicated_plan
    application_insights_connection_string = azurerm_application_insights.sweeper.connection_string

    application_stack {
      python_version = "3.12"
    }
  }

  app_settings = {
    FTG_PROBE_RG = var.resource_group_name
    # The Resource Graph query is scoped to this subscription; without it the sweeper fails.
    AZURE_SUBSCRIPTION_ID = data.azurerm_client_config.current.subscription_id
    FTG_EXPERIMENT        = local.experiment
    # Must stay 30 s or more above the provisioning probe timeout plus the teardown timeout, 90 s + 60 s
    # (config/targets.yaml; spec 0010, decision Z17).
    FTG_MAX_AGE_SECONDS = "180"
    # The sweeper writes its own log objects here too (spec 0003 section 8.2, spec 0007
    # section 9): same account as the prober's store_url (control.tf), container "data".
    FTG_DATA_STORAGE_ACCOUNT = azurerm_storage_account.data.name
    # Remote build on a Linux consumption or dedicated plan: pip installs requirements.txt during
    # the zip deploy. Do not set WEBSITE_RUN_FROM_PACKAGE with a remote build.
    SCM_DO_BUILD_DURING_DEPLOYMENT = "true"
    ENABLE_ORYX_BUILD              = "true"
  }

  lifecycle {
    # On Linux Consumption, the zip deploy sets WEBSITE_RUN_FROM_PACKAGE to the blob URL of the
    # built package. Without this, each plan would try to remove it.
    ignore_changes = [app_settings["WEBSITE_RUN_FROM_PACKAGE"]]
  }
}
