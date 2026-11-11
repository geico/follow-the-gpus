# Tests of the optional egress proxy (spec 0005, sections 3.4 and 8). They decode the
# rendered cloud-init of the prober VM. The providers are mocks. Run: make test/azure

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
  targets_file        = "examples/targets.example.yaml"
  resource_group_name = "platform-rg"
  subnet_id           = "/subscriptions/mock-subscription/resourceGroups/platform-net/providers/Microsoft.Network/virtualNetworks/platform-vnet/subnets/workloads"
}

# The VM custom_data must be known at plan time, so these values get fixed mock values.
override_resource {
  target = azurerm_storage_account.data
  values = {
    id                    = "/subscriptions/mock-subscription/resourceGroups/platform-rg/providers/Microsoft.Storage/storageAccounts/mockdata"
    primary_blob_endpoint = "https://mockdata.blob.core.windows.net/"
  }
}

# These feed other resources' plan-time ID or key format validation. OpenTofu resolves mocked
# computed attributes at plan time (unlike Terraform's default apply-time generation), so a
# random mock string fails that validation.
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

override_data {
  target = data.archive_file.prober
  values = {
    output_sha256 = "0000"
  }
}

override_data {
  target = data.archive_file.config
  values = {
    output_sha256 = "1111"
  }
}

run "defaults_render_no_proxy" {
  command = plan

  assert {
    condition = alltrue([
      for s in ["apt:", "HTTP_PROXY", "NO_PROXY", "http_proxy", "ftg-prober.service.d"] :
      !strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), s)
    ])
    error_message = "With the defaults, the cloud-init must hold no proxy or drop-in content."
  }

  assert {
    condition = azurerm_storage_blob.manifest.name == "manifest.json" && jsondecode(azurerm_storage_blob.manifest.source_content) == {
      prober_blob = azurerm_storage_blob.prober.name
      config_blob = azurerm_storage_blob.config.name
      sync_blob   = azurerm_storage_blob.sync_script.name
      store_url   = "az://${azurerm_storage_account.data.name}/data/"
    }
    error_message = "ADR 0012, ADR 0025: manifest.json must be a fixed-name blob in the code container naming the 2 current code blobs, the current hash-named sync.sh blob, and the store URL."
  }

  assert {
    condition     = startswith(azurerm_storage_blob.sync_script.name, "sync-") && endswith(azurerm_storage_blob.sync_script.name, ".sh") && azurerm_storage_blob.sync_script.source_content == file("${path.module}/../sync.sh")
    error_message = "ADR 0012: sync.sh must be a hash-named blob in the code container holding terraform/sync.sh verbatim."
  }

  assert {
    condition = alltrue([
      strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), "ExecStart=/opt/ftg/current/venv/bin/ftg-prober"),
      strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), "fetch-blob.sh manifest.json /tmp/ftg-manifest.json"),
      strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), "sync_blob=$(jq -r .sync_blob /tmp/ftg-manifest.json)"),
      strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), "fetch-blob.sh $sync_blob /opt/ftg/bin/sync.sh"),
      !strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), "FTG_EXPERIMENT"),
    ])
    error_message = "ADR 0012: the prober unit must run /opt/ftg/current, cloud-init must fetch sync.sh via manifest.json's own sync_blob field, and the env must carry no FTG_EXPERIMENT."
  }
}

# the first-boot fetch loop is bounded, and every curl in fetch-blob.sh has a timeout.
run "first_boot_fetch_loop_is_bounded" {
  command = plan

  assert {
    condition = alltrue([
      # 30 rounds, 10 s apart, no new round after 1800 s, and 1 try per fetch-blob.sh call.
      strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), "export FTG_FETCH_ATTEMPTS=1; for n in $(seq 1 30); do /opt/ftg/bin/fetch-blob.sh manifest.json"),
      strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), "[ $SECONDS -ge 1800 ] && break; sleep 10; done; exit 1"),
      strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), "attempts=\"$${FTG_FETCH_ATTEMPTS:-40}\""),
    ])
    error_message = "The first-boot retry loop must run at most 30 rounds and stop after 1800 s, and its fetch-blob.sh calls must make 1 try each."
  }

  assert {
    condition = alltrue([
      strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), "curl -s --max-time 30 -H Metadata:true"),
      strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), "curl -s --max-time 300 -o"),
    ])
    error_message = "Each curl in fetch-blob.sh must have --max-time."
  }
}

# a config-only change (a new targets file content, so new zip hashes) must not change
# the rendered custom_data (ADR 0012 section 3). The zip hashes go into manifest.json only.
run "custom_data_holds_no_code_hash" {
  command = plan

  assert {
    condition = alltrue([
      !strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), data.archive_file.prober.output_sha256),
      !strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), data.archive_file.config.output_sha256),
      !strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), azurerm_storage_blob.prober.name),
      !strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), azurerm_storage_blob.config.name),
      !strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), azurerm_storage_blob.sync_script.name),
      strcontains(azurerm_storage_blob.manifest.source_content, data.archive_file.prober.output_sha256),
    ])
    error_message = "A code, config, or sync.sh change must reach the VM only through manifest.json, never through custom_data."
  }
}

run "custom_data_unchanged_by_new_zip_hashes" {
  command = plan

  override_data {
    target = data.archive_file.prober
    values = {
      output_sha256 = "2222"
    }
  }

  override_data {
    target = data.archive_file.config
    values = {
      output_sha256 = "3333"
    }
  }

  assert {
    condition = alltrue([
      !strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), "2222"),
      !strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), "3333"),
      strcontains(azurerm_storage_blob.manifest.source_content, "prober-2222.zip"),
      strcontains(azurerm_storage_blob.manifest.source_content, "config-3333.zip"),
    ])
    error_message = "New zip hashes must change manifest.json and leave custom_data without them."
  }
}

# the prober runs unprivileged and the sync unit has its start timeout and its
# revert outside ExecStart (spec 0003 section 6A).
run "units_in_custom_data" {
  command = plan

  assert {
    condition = alltrue([
      strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), "DynamicUser=yes"),
      !strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), "ReadWritePaths"),
      strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), "TimeoutStartSec=3600"),
      strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), "ExecStopPost=/bin/bash -c"),
      strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), "\"$$SERVICE_RESULT\""),
    ])
    error_message = "custom_data must hold DynamicUser with no ReadWritePaths, the sync unit's TimeoutStartSec, and its ExecStopPost revert."
  }
}

run "proxy_in_apt_install_and_service" {
  command = plan

  variables {
    egress_proxy_url = "http://proxy.example.internal:80"
    no_proxy         = [".blob.core.windows.net", "10.0.0.0/8"]
  }

  assert {
    condition     = strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), "apt:\n  http_proxy: http://proxy.example.internal:80\n  https_proxy: http://proxy.example.internal:80\n")
    error_message = "cloud-init must set the apt proxy."
  }

  assert {
    condition = alltrue([
      for s in [
        # ADR 0012: runcmd's own fetch of sync.sh runs outside any systemd unit (control.tf
        # local.proxy_export_cmd), the same reason install.sh used to export these directly.
        "export HTTP_PROXY=http://proxy.example.internal:80 HTTPS_PROXY=http://proxy.example.internal:80 NO_PROXY=169.254.169.254,168.63.129.16,localhost,127.0.0.1,login.microsoftonline.com,.blob.core.windows.net,10.0.0.0/8 http_proxy=http://proxy.example.internal:80 https_proxy=http://proxy.example.internal:80 no_proxy=169.254.169.254,168.63.129.16,localhost,127.0.0.1,login.microsoftonline.com,.blob.core.windows.net,10.0.0.0/8;",
        "/etc/systemd/system/ftg-prober.service.d/proxy.conf",
        "/etc/systemd/system/ftg-prober-sync.service.d/proxy.conf",
        "\"https_proxy=http://proxy.example.internal:80\" \"no_proxy=169.254.169.254,",
      ] :
      strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), s)
    ])
    error_message = "cloud-init must export the proxy variables for runcmd's own fetch of sync.sh and in both service drop-ins, with the fixed and the extra no_proxy hosts."
  }

  assert {
    condition = alltrue([
      !strcontains(base64decode(azurerm_linux_virtual_machine.prober.custom_data), "/etc/environment"),
    ])
    error_message = "The cloud-init must never write /etc/environment."
  }
}

run "proxy_url_must_look_like_http_host_port" {
  command = plan

  variables {
    egress_proxy_url = "proxy.example.internal"
  }

  expect_failures = [var.egress_proxy_url]
}

run "proxy_url_rejects_a_path" {
  command = plan

  variables {
    egress_proxy_url = "http://proxy.example.internal:80/path"
  }

  expect_failures = [var.egress_proxy_url]
}

run "no_proxy_entry_rejects_a_comma" {
  command = plan

  variables {
    no_proxy = ["a.example.internal,b.example.internal"]
  }

  expect_failures = [var.no_proxy]
}

run "rejects_a_proxy_url_with_an_ipv4_host" {
  command = plan

  variables {
    egress_proxy_url = "http://10.1.2.3:3128"
  }

  expect_failures = [var.egress_proxy_url]
}

run "rejects_a_proxy_url_with_an_ipv6_host" {
  command = plan

  variables {
    egress_proxy_url = "http://[2001:db8::1]:3128"
  }

  expect_failures = [var.egress_proxy_url]
}

