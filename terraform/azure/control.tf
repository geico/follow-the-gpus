# Data storage and prober VM, in the existing resource group and subnet.
# The sweeper is in sweeper.tf. The roles are in rbac.tf. The probe resources are in probe.tf.

locals {
  # Storage account names are global. The suffix is the first 8 hex digits of the
  # SHA-256 of the subscription ID, so the names are stable and need no state.
  suffix = substr(sha256(data.azurerm_client_config.current.subscription_id), 0, 8)

  # The stack resources do not carry the experiment tag. The sweeper deletes only resources with
  # that tag, and the stack resources share the group with the probe resources (spec 0007).
  stack_tags = var.tags
  probe_tags = merge(var.tags, { experiment = local.experiment })

  # Empty means the feature is off. The cloud-init template tests for "".
  egress_proxy_url = var.egress_proxy_url == null ? "" : var.egress_proxy_url

  # The proxy must never carry these: IMDS and the wire server are link-local, and the managed
  # identity token call goes to IMDS. login.microsoftonline.com goes direct (spec 0005, section 3.4).
  no_proxy_all = concat(["169.254.169.254", "168.63.129.16", "localhost", "127.0.0.1", "login.microsoftonline.com"], var.no_proxy)

  # ADR 0012: runcmd's own fetch of sync.sh runs outside any systemd unit, so, like the apt
  # proxy above, it needs the proxy exported directly, not through a unit's own Environment=.
  # Empty when there is no proxy, so cloud_init.yaml.tftpl's runcmd needs no `%{ if }` of its
  # own. No quoting: var.egress_proxy_url is already validated to look like http://host:port
  # (no space, no quote), and no_proxy_all is a comma-joined list of hostnames, so plain
  # NAME=value assignments are safe and avoid nesting quotes inside the YAML runcmd string.
  proxy_export_cmd = local.egress_proxy_url == "" ? "" : "export HTTP_PROXY=${local.egress_proxy_url} HTTPS_PROXY=${local.egress_proxy_url} NO_PROXY=${join(",", local.no_proxy_all)} http_proxy=${local.egress_proxy_url} https_proxy=${local.egress_proxy_url} no_proxy=${join(",", local.no_proxy_all)}; "

  targets_path = startswith(var.targets_file, "/") ? var.targets_file : "${path.module}/${var.targets_file}"

  # The config zip holds the folder of the targets file, so the VM reads the same file that
  # Terraform maps to resources. The default is ../../config/targets.yaml.
  config_dir  = dirname(local.targets_path)
  config_file = basename(local.targets_path)

  # The prober zip leaves out these names at every depth. The provider matches each pattern
  # against the path inside the source folder. A tool cache must not change the zip hash,
  # because a new hash replaces the VM.
  prober_exclude_names = [".venv", "__pycache__", "tests", "dist", ".pytest_cache", ".ruff_cache", "*.egg-info"]
  prober_excludes      = concat(flatten([for n in local.prober_exclude_names : ["**/${n}", "**/${n}/**"]]))

  prober_blob = "prober-${data.archive_file.prober.output_sha256}.zip"
  config_blob = "config-${data.archive_file.config.output_sha256}.zip"
  sync_blob   = "sync-${filesha256("${path.module}/../sync.sh")}.sh"
  store_url   = "az://${azurerm_storage_account.data.name}/data/"
}

# The group exists. The stack reads it for the role and budget scopes, and never changes it.
data "azurerm_resource_group" "stack" {
  name = var.resource_group_name
}

# Data store: 1 account, container "data" for the records and "code" for the prober package.

resource "azurerm_storage_account" "data" {
  name                            = "${var.name_prefix}data${local.suffix}"
  resource_group_name             = var.resource_group_name
  location                        = var.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false
  tags                            = local.stack_tags
}

resource "azurerm_storage_container" "data" {
  name                  = "data"
  storage_account_id    = azurerm_storage_account.data.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "code" {
  name                  = "code"
  storage_account_id    = azurerm_storage_account.data.id
  container_access_type = "private"
}

# The logs under data/experiment=<tag>/logs/ get their own lifecycle rule: 90 days, apart from
# the rows, which keep no rule (spec 0003 section 8.2). Azure Storage lifecycle management
# filters match a literal prefix, not a glob, so the rule names the configured experiment, the
# same way local.experiment already names it elsewhere (for example rbac.tf).
resource "azurerm_storage_management_policy" "logs" {
  storage_account_id = azurerm_storage_account.data.id

  rule {
    name    = "expire-logs"
    enabled = true

    filters {
      prefix_match = ["data/experiment=${local.experiment}/logs/"]
      blob_types   = ["blockBlob"]
    }

    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = 90
      }
    }
  }
}

# Code: 2 zips, because archive_file takes 1 source folder. tofu plan needs both folders.

locals {
  # The zip lands under .terraform/, not the git-ignored dist/. A tool that copies the repo
  # copies the whole checkout into its cache, ignored files included, and it can run that copy
  # again between `plan -out=tfplan` and `apply tfplan`. A file under dist/ survives that second
  # copy and can overwrite the zip the plan just built with a stale one from an earlier local
  # build, so the apply uploads old bytes under the new hash key (spec 0005, section 3.1).
  # Such a copy skips dot-directories, so a zip built under .terraform/ cannot be
  # replaced by a copy of the source checkout.
  code_build_dir = "${path.module}/.terraform/ftg-build"
}

data "archive_file" "prober" {
  type             = "zip"
  source_dir       = "${path.module}/../../prober"
  output_path      = "${local.code_build_dir}/prober.zip"
  output_file_mode = "0644"
  excludes         = local.prober_excludes
}

data "archive_file" "config" {
  type             = "zip"
  source_dir       = local.config_dir
  output_path      = "${local.code_build_dir}/config.zip"
  output_file_mode = "0644"
}

resource "azurerm_storage_blob" "prober" {
  name                 = local.prober_blob
  storage_container_id = azurerm_storage_container.code.id
  type                 = "Block"
  source               = data.archive_file.prober.output_path
  content_md5          = data.archive_file.prober.output_md5

  # ADR 0012 section 3: a blob name cannot change in place, so a hash change still replaces the
  # blob; this only changes the order to create-then-destroy, so the manifest below (which
  # depends_on both blobs) is never rewritten to point at a key already destroyed.
  lifecycle {
    create_before_destroy = true
  }
}

resource "azurerm_storage_blob" "config" {
  name                 = local.config_blob
  storage_container_id = azurerm_storage_container.code.id
  type                 = "Block"
  source               = data.archive_file.config.output_path
  content_md5          = data.archive_file.config.output_md5

  lifecycle {
    create_before_destroy = true
  }
}

# ADR 0012 section 3: a fixed name, manifest.json, in the same code container. source_content
# "forces a new resource to be created" on every change
# (https://github.com/hashicorp/terraform-provider-azurerm/blob/main/website/docs/r/storage_blob.html.markdown),
# so unlike AWS this blob is briefly absent on every apply that changes the code, the config, or
# sync.sh, not only the first switchover apply. sync.sh (terraform/sync.sh) treats a BlobNotFound
# on this blob the same as "no change, try again next tick," not as an error.
resource "azurerm_storage_blob" "manifest" {
  name                 = "manifest.json"
  storage_container_id = azurerm_storage_container.code.id
  type                 = "Block"
  source_content = jsonencode({
    prober_blob = local.prober_blob
    config_blob = local.config_blob
    sync_blob   = local.sync_blob
    store_url   = local.store_url
  })

  depends_on = [azurerm_storage_blob.prober, azurerm_storage_blob.config, azurerm_storage_blob.sync_script]
}

# sync.sh (ADR 0012 section 4), 1 file shared with the AWS stack (terraform/sync.sh). Hash-named
# and verified the same way as the 2 code zips above, in the same code container, so it needs no
# RBAC entry of its own. manifest.json's own sync_blob field names the current one; sync.sh
# re-reads that field on every tick and replaces its own installed copy when the hash differs
# (section 4), so a sync.sh fix alone also reaches a running VM with no VM replacement and no
# separate fixed name to fetch at boot.
resource "azurerm_storage_blob" "sync_script" {
  name                 = local.sync_blob
  storage_container_id = azurerm_storage_container.code.id
  type                 = "Block"
  source_content       = file("${path.module}/../sync.sh")

  lifecycle {
    create_before_destroy = true
  }
}

# Prober VM: Ubuntu 24.04 LTS Gen2, system-assigned identity, no public IP.

resource "azurerm_network_interface" "prober" {
  name                = "${var.name_prefix}-prober-nic"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = local.stack_tags

  ip_configuration {
    name                          = "primary"
    subnet_id                     = var.subnet_id
    private_ip_address_allocation = "Dynamic"
    primary                       = true
  }
}

# The VM has no inbound port, so nobody logs in. Azure still requires a key.
# The key lives only in the local state file.
resource "tls_private_key" "prober" {
  algorithm = "ED25519"
}

resource "azurerm_linux_virtual_machine" "prober" {
  name                  = "${var.name_prefix}-prober"
  resource_group_name   = var.resource_group_name
  location              = var.location
  size                  = var.prober_vm_size
  admin_username        = "ftgadmin"
  network_interface_ids = [azurerm_network_interface.prober.id]
  tags                  = local.stack_tags

  disable_password_authentication = true

  admin_ssh_key {
    username   = "ftgadmin"
    public_key = tls_private_key.prober.public_key_openssh
  }

  identity {
    type = "SystemAssigned"
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }

  # ADR 0012 section 3: custom_data no longer names the prober or the config blob at all, hashed
  # or fixed; cloud-init only stages sync.sh and the 2 units, and sync.sh itself reads
  # manifest.json at runtime (first boot and every later tick). A code, a config, or a manifest
  # change no longer changes custom_data, so none of them replace the VM any more. The 1 apply
  # that first switches a stack to this design still replaces it once, because that apply itself
  # changes custom_data from the old template (which took prober_blob and config_blob) to this
  # one. After that, custom_data changes only for an unrelated reason, for example a new proxy
  # URL.
  custom_data = base64encode(templatefile("${path.module}/cloud_init.yaml.tftpl", {
    blob_endpoint   = azurerm_storage_account.data.primary_blob_endpoint
    config_file     = local.config_file
    store_url       = local.store_url
    probe_rg        = var.resource_group_name
    subscription_id = data.azurerm_client_config.current.subscription_id

    egress_proxy_url = local.egress_proxy_url
    no_proxy         = join(",", local.no_proxy_all)
    proxy_export_cmd = local.proxy_export_cmd

    sync_service_unit = file("${path.module}/../ftg-prober-sync.service")
    sync_timer_unit   = file("${path.module}/../ftg-prober-sync.timer")
  }))

  depends_on = [
    azurerm_storage_blob.prober,
    azurerm_storage_blob.config,
    azurerm_storage_blob.manifest,
    azurerm_storage_blob.sync_script,
  ]
}
