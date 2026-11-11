---
title: Azure stack spec
date: 2026-10-11
---

# Azure stack spec

**Status:** Phase 1 build · 2026-10-11
**Scope:** the OpenTofu stack in `terraform/azure/`, the make targets in `tasks/Makefile.azure`, and `make preflight/azure` (section 11). The product requirements are in [spec 0002](0002-gpu-capacity-tracker-spec.md): sections 3, 4.5, 6, 8.1, 8.3, and decisions D5, D9, D12, D13, D15.

A person must be able to build the stack again from this file and spec 0002.

## 1. Shape

The stack deploys into 1 existing resource group and 1 existing subnet in 1 subscription. The required variables `resource_group_name` and `subnet_id` name them. The stack creates no resource group, virtual network, subnet, or network security group. It never changes or deletes the group. All names are lower case. The prefix is `ftg` by default.

| Part | Holds | Who writes here |
|---|---|---|
| Control resources (section 3) | Data storage account, prober VM and its NIC, sweeper Function App and its storage, Log Analytics, Application Insights, the sweeper-stopped alert rule and its action group | OpenTofu only |
| Probe resources (section 4) | 1 capacity reservation group per region, 1 scale set per provisioning probe target, the budget | OpenTofu, the prober (capacity, reservations, tags), the sweeper (deletes) |

Both parts are in `resource_group_name`. The group can also hold resources of other owners. The prober VM NIC and the scale sets use `subnet_id`.

Tags: only the probe resources carry the `experiment` tag. The control resources carry only `tags`. The sweeper deletes only resources with the `experiment` tag (spec 0007), thus it never selects a control resource or a resource of another owner. The `tags` variable must not hold the key `experiment` in any case. A validation block enforces it.

Upgrade: an older version of the stack created the groups `<prefix>-control` and `<prefix>-probe`. An apply of this version over that state deletes both groups with their contents, because `prevent_deletion_if_contains_resources` is false, and it replaces the data storage account. Thus the upgrade needs a fresh deploy: copy the `data` container, destroy with the old code, then deploy this version. The README states the steps.

The subscription comes from the variable `subscription_id` in the git-ignored `terraform/azure/terraform.tfvars` ([spec 0014](0014-config-and-deploy-spec.md) section 3). Both the `azurerm` and the `azapi` provider blocks set `subscription_id = var.subscription_id`. When it is `null`, the provider reads `ARM_SUBSCRIPTION_ID`. The provider doc says: "The `subscription_id` property is required when performing a plan or apply operation, but is not required to run `terraform validate`" (OpenTofu's `tofu validate` is the same check) ([azurerm provider](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs)). No tracked file holds a subscription ID, a tenant ID, or a hostname. State is local by default. An optional git-ignored `backend.hcl` and `backend_override.tf` select a remote backend ([spec 0014](0014-config-and-deploy-spec.md) section 5).

Providers: `azurerm ~> 4.81` (the lock file resolves 4.81.0; the doc links below are for that version), `Azure/azapi ~> 2.9`, `hashicorp/archive ~> 2.4`, `hashicorp/tls ~> 4.0`. OpenTofu `>= 1.10`. The lock file `.terraform.lock.hcl` pins each provider's hashes for every platform `tofu providers lock` resolved them for.

## 2. Variables

| Variable | Type | Default | Meaning |
|---|---|---|---|
| `subscription_id` | string | `null` | The subscription of the deploy. Both provider blocks use it. `null` means the provider reads `ARM_SUBSCRIPTION_ID` (section 1). |
| `resource_group_name` | string | none (required) | The existing resource group. Every resource of the stack goes here. Must match `^[A-Za-z0-9._()-]{0,89}[A-Za-z0-9_()-]$`: the Azure name rules, limited to the characters that the sweeper query accepts (spec 0007, section 4). A validation block enforces it. |
| `subnet_id` | string | none (required) | The resource ID of the existing subnet, in `location`. Outbound internet access from it is a deploy prerequisite (section 3.2). Must match `(?i)^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.Network/virtualNetworks/[^/]+/subnets/[^/]+$`. A validation block enforces it. |
| `name_prefix` | string | `ftg` | Prefix of each name. Must match `^[a-z][a-z0-9]{1,9}$`, because storage account names allow only lower case letters and digits ([azurerm_storage_account](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/storage_account)). |
| `probe_subnet_ids` | map(string) | `{}` | Subnets for scale-set probes in other regions, as `{ region = subnet resource ID }`. Each value must match the pattern of `subnet_id`. A validation block enforces it. The control region needs no entry: it falls back to `subnet_id` (section 4.3). The stack creates no network. A validation block rejects a subnet ID used by 2 regions. The plan cannot check the region of a subnet from its ID, so a subnet in the wrong region passes the plan and fails at scale-out with an Azure NIC error. |
| `location` | string | `eastus` | The control region. The prober VM, the data storage, and the `subnet_id` subnet are here. The sweeper resources are here too, unless `sweeper_location` is set. A scale set in this region uses `subnet_id`. A scale set in another region needs an entry of `probe_subnet_ids` (section 4.1, R2). |
| `sweeper_location` | string | `null` | The region of the sweeper resources (section 3.5, "Sweeper region"). `null` means `location`. Must be `null` or a non-empty region name. The region is in the names of the sweeper resources (section 3.5, "Sweeper region", rule 7). Set it when the subscription has no App Service quota in `location`. |
| `prober_vm_size` | string | `Standard_B2ats_v2` | Size of the prober VM. |
| `sweeper_plan_sku` | string | `Y1` | SKU of the sweeper service plan. Must be `Y1`, `B1`, `B2`, `B3`, or `S1`. `Y1` is the Linux consumption plan. The others are dedicated plans for a subscription with no `Y1` quota (section 3.5). |
| `budget_amount` | number | `50` | Monthly budget of `resource_group_name`. |
| `alert_email` | string | `""` | Address of the budget alerts and the sweeper-stopped alert. Empty: no budget, no action group, no alert rule. |
| `targets_file` | string | `../../config/targets.yaml` | The targets file. Relative to `terraform/azure/`, or absolute. The file's own `experiment` key is the only source of the `experiment` tag (the prober VM gets no `FTG_EXPERIMENT`, ADR 0012; [ADR 0010](../decisions/0010-one-experiment-file-of-modules-and-jobs-with-a-billable-guard.md) section 2): there is no `experiment_tag` variable. |
| `grant_billable_provisioning` | bool | `false` | Grant the prober the rights to create and remove billable capacity (the sweeper's cleanup role is separate and always on, R7), and plan the capacity reservation groups and scale sets that `provisioning_probes` entries call for (section 4.1, R7). This is the only thing that decides either of those; it does not read the targets file ([ADR 0015](../decisions/0015-two-independent-controls-replace-the-terraform-allocation-guard.md)). Set it to `true` only when you also want `provisioning_probes` entries in the targets file to run for real; the targets file's own `allow_billable_provisioning` key is a separate control the prober checks, not this variable. |
| `fault_domain_count` | number | `1` | `platformFaultDomainCount` of each scale set. |
| `tags` | map(string) | `{}` | Extra tags on each resource. Must not hold the key `experiment` in any case (section 1). |
| `egress_proxy_url` | string | `null` | URL of an HTTP egress proxy for the prober VM, for example `http://proxy.example.internal:80`. `null` means no proxy. Must match `^https?://[A-Za-z0-9.-]+:[0-9]{1,5}/?$`: a host name and a port, no user name, no path. A validation block enforces it. The value must be a host name. A second validation block rejects an IPv4 address or a bracketed IPv6 address as the host, because a pinned address breaks when the proxy moves. Section 3.4 states what it changes. |
| `no_proxy` | list(string) | `[]` | Extra hosts that bypass the proxy. The VM always bypasses the fixed hosts (section 3.4). Each entry must match `^[A-Za-z0-9.*:/_-]+$`, so it holds no comma and no space. A validation block enforces it. It has an effect only when `egress_proxy_url` is set. If the data storage account uses a private endpoint, the deployer must add `.blob.core.windows.net` here. |
| `data_reader_principal_ids` | list(string) | `[]` | Extra Entra object IDs that get read access to the data container and the code container, besides the deployer (section 5.3). A group object ID is preferred over a user or a service principal object ID. Not nullable (`nullable = false`). A validation block refuses a null entry and an entry that is empty or only whitespace. |

Private values go into the git-ignored `terraform/azure/terraform.tfvars`, never into the repo ([spec 0014](0014-config-and-deploy-spec.md) section 2). The user copies it from `terraform.tfvars.example`. `tofu test` also reads it.

## 3. Control resources

### 3.1 Data store

| Resource | Name | Settings |
|---|---|---|
| Storage account | `<prefix>data<suffix>` | Standard, LRS, StorageV2, TLS 1.2 minimum, HTTPS only, nested items cannot be public |
| Container | `data` | private. The prober writes records here. The store URL is `az://<account>/data/`. The prober and the sweeper write their own logs under `data/experiment=<tag>/logs/` too (spec 0003 section 8.2). |
| Container | `code` | private. OpenTofu uploads the prober zip and the config zip here. |

The `data/experiment=<tag>/logs/` prefix gets its own lifecycle rule, `azurerm_storage_management_policy.logs`: `delete_after_days_since_modification_greater_than = 90`, filtered to `blockBlob` with `prefix_match = ["data/experiment=<tag>/logs/"]`. The rows keep no rule. Azure Storage lifecycle management matches a literal prefix, not a glob ([Configure a lifecycle management policy](https://learn.microsoft.com/en-us/azure/storage/blobs/lifecycle-management-policy-configure)), so the rule names the stack's own `experiment_tag`, the same way `rbac.tf` already does elsewhere. A later experiment name needs its own apply to get the rule.

`<suffix>` is the first 8 hex digits of the SHA-256 of the subscription ID. Storage account names are global, so the suffix makes the name unique and stable without state.

OpenTofu uploads 2 blobs to `code`:

- `prober-<sha256>.zip`: an `archive_file` zip of `../../prober`. The excludes are `.venv`, `__pycache__`, `tests`, `dist`, `.pytest_cache`, `.ruff_cache`, and `*.egg-info`, each as `**/<name>` and `**/<name>/**`, so they match at every depth. `excludes` "Supports glob file matching patterns including doublestar/globstar (`**`) patterns" ([archive_file](https://registry.terraform.io/providers/hashicorp/archive/latest/docs/data-sources/file)). The `<sha256>` is the zip checksum.
- `config-<sha256>.zip`: an `archive_file` zip of the folder of `targets_file`. The default folder is `../../config`. It excludes only the file below. The `<sha256>` is the zip checksum.

Each of these 2 blobs adds `lifecycle { create_before_destroy = true }` ([ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md) section 3). A blob name cannot change in place, so a hash change still replaces the blob; this setting only changes the replace order to create-then-destroy, so `azurerm_storage_blob.manifest` (below), which `depends_on` both, is never rewritten to name a blob Terraform has already destroyed.

2 more blobs land in `code`: `manifest.json`, at a fixed name, and `sync.sh`, hash-named like the 2 zips above:

- `manifest.json`, `source_content = jsonencode({ prober_blob = ..., config_blob = ..., sync_blob = ..., store_url = ... })`, the current blob names of the 2 zips above and of `sync.sh` below, and the store URL of `ftg-prober.service` (`az://<account>/data/`), which `sync.sh` writes into `ftg-compact.service` (section 3.4; `/etc/ftg/sync-env` has no account name, and a change of `custom_data` replaces the VM). `source_content` "forces a new resource to be created" on every change ([`storage_blob.html.markdown`](https://github.com/hashicorp/terraform-provider-azurerm/blob/main/website/docs/r/storage_blob.html.markdown)), so, unlike the AWS manifest ([spec 0004](0004-aws-stack-spec.md) section 4.5), this blob is briefly absent on every apply that changes the code, the config, or `sync.sh`, not only the first switchover apply. `sync.sh` (section 3.4) treats a `BlobNotFound` on this blob the same as "no change, try again next tick," not as an error: this gap is routine on Azure, on every such apply.
- `sync.sh`, hash-named by `filesha256("../sync.sh")` (`terraform/sync.sh`, shared with the AWS stack), with `create_before_destroy` like the 2 zips above: `sync-<sha256>.sh`. `custom_data` (3.3) does not name this blob at all, hashed or fixed; it reads `manifest.json`'s own `sync_blob` field instead (section 3.4), the same way `sync.sh` itself does on every later tick, so a `sync.sh` change alone does not change `custom_data`.

The blob name of the 2 zips holds the zip checksum, so a changed zip gets a new blob name; `custom_data` (3.3) no longer names either zip blob directly, only the fixed `manifest.json` name, so a changed zip no longer changes `custom_data` for that reason. The prober, config, and sweeper (section 3.5) zips land in `terraform/azure/.terraform/ftg-build/`, not the git-ignored `dist/`. A stale file under `dist/` must not replace the zip that the plan built: a saved-plan apply does not rebuild the zip, so it would upload the stale bytes under the blob name (or, for the sweeper, the `zip_deploy_file` path) that the plan computed from the correct build.

### 3.2 Network

The network exists. The stack creates no virtual network, subnet, or network security group, and it does not change the settings of the subnet. The prober VM NIC uses `subnet_id`. The subnet must be in `location`, because a NIC must be in the region of its virtual network.

Outbound access is a prerequisite. The prober VM needs apt, PyPI, the Azure APIs, and the storage account. The network owner sets up outbound internet access from `subnet_id` before the deploy, like the network itself, for example through a NAT gateway or a firewall. The stack creates no NAT gateway and no public IP. Microsoft says: "For the API version released after March 31, 2026, subnets in new virtual networks have `defaultOutboundAccess` set to `false` by default" ([default outbound access](https://learn.microsoft.com/en-us/azure/virtual-network/ip-services/default-outbound-access)). Thus a new subnet can have no default outbound access. The same page recommends a NAT gateway "for most scenarios". That choice belongs to the network owner, because the subnet is not part of the stack.

An egress proxy is optional (`egress_proxy_url`, section 3.4). It changes the settings of the VM only. The stack creates no network security group, so the proxy needs no network rule change from the stack. The network owner must allow the VM to reach the proxy.

Inbound access: the stack adds no inbound rule. Any network security group on the subnet belongs to the network owner.

### 3.3 Prober VM

| Setting | Value | Source |
|---|---|---|
| Name | `<prefix>-prober` | |
| Image | `Canonical:ubuntu-24_04-lts:server:latest` (Gen2, x64) | [Canonical: find Ubuntu images](https://ubuntu.com/azure/docs/en/latest/azure-how-to/instances/find-ubuntu-images/) |
| Identity | system-assigned | |
| NIC | `<prefix>-prober-nic`, in `subnet_id`, dynamic private IP, no public IP | |
| OS disk | Standard SSD, image size, ReadWrite cache | |
| Admin | `ftgadmin`, SSH key only. OpenTofu makes an ED25519 key with `tls_private_key`. The key stays in the local state. Nobody logs in. | |
| `custom_data` | the rendered `cloud_init.yaml.tftpl`, base64 | "Changing this forces a new resource to be created" ([azurerm_linux_virtual_machine](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/linux_virtual_machine)) |

[ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md): the template no longer takes `prober_blob`, `config_blob`, or any `sync.sh` blob name at all, hashed or fixed. It takes the blob endpoint, the file name of `targets_file`, the store URL, `resource_group_name`, the subscription ID, the proxy settings (3.4), and the 2 systemd unit files `sync.sh` runs under (`file("../ftg-prober-sync.service")`, `file("../ftg-prober-sync.timer")`, read raw and substituted once, with no further interpolation of their own content). It no longer takes the experiment tag either: the prober reads its experiment name from the config file only (ADR 0010, section 11), so `custom_data` carries no `FTG_EXPERIMENT` value. Neither a code, a config, nor a `sync.sh` change by itself changes a value this template takes, so none of them change `custom_data`, and the VM no longer replaces for any of those reasons. The 1 exception: the apply that first moves an existing stack to this design still replaces the VM once, because that apply itself changes `custom_data`, from the old template (which took `prober_blob` and `config_blob`) to this one. The VM holds no data (spec 0002, D13).

### 3.4 Cloud-init flow

[ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md): cloud-init no longer pulls the prober code or builds the venv itself. It stages `/opt/ftg/bin/sync.sh` and 2 systemd units, then starts the sync unit once; every later code, config, or `sync.sh` change reaches the VM through that same script, on a timer, with no VM replacement and no 2nd code path for the first install ([spec 0003](0003-prober-core-spec.md) section 6A has the full mechanism).

1. `packages`: `python3`, `python3-venv`, `unzip`, `curl`, `jq`. Ubuntu 24.04 ships `python3` version 3.12.3 ([packages.ubuntu.com, noble](https://packages.ubuntu.com/noble/python3)). `sync.sh` uses `jq` to read `manifest.json`'s fields.
2. `write_files`:
   - `/etc/ftg/env`: `FTG_PROBE_RG=<resource_group_name>`, `AZURE_SUBSCRIPTION_ID=<id>`. No experiment value: the prober reads its experiment name from the targets file only (ADR 0010, section 11), never from an environment variable, so a new `experiment:` in a config update is an ordinary config change, not a reason this file, the gate, or the health check would need to change or fail ([ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md)).
   - `/etc/ftg/sync-env`: `FTG_PROVIDER=azure`, `FTG_CONFIG_FILE=<file name of targets_file>`, `FTG_PIP_EXTRA=azure`. `sync.sh` sources this file directly with a plain shell `.`, not through a systemd `EnvironmentFile=`.
   - `/opt/ftg/bin/fetch-blob.sh`: gets a token and downloads 1 blob from `code`. Exit 0: fetched. Exit 3: the blob does not exist (HTTP 404, `BlobNotFound`), returned at once with no retry, because a `sync.sh` poll treats this as routine (section 3.1, section below), not a failure to retry. Exit 1: every other failure, after `FTG_FETCH_ATTEMPTS` tries (default 40), 15 seconds apart, because a new role assignment can take minutes to apply. Every `curl` has a `--max-time` (30 s for the token, 300 s for the blob), so 1 hung connection cannot stall a try.
   - `/etc/systemd/system/ftg-prober.service`, `ftg-prober-sync.service`, `ftg-prober-sync.timer`: the units below.
3. `runcmd`: creates `/opt/ftg/releases` (mode 0755); fetches `manifest.json` with `fetch-blob.sh`, reads its `sync_blob` field with `jq`, then fetches that hash-named blob (the pair retried for up to 30 rounds, 10 seconds apart, with no new round after 1800 s, and with `FTG_FETCH_ATTEMPTS=1` so each `fetch-blob.sh` call makes 1 try, because `manifest.json` can briefly not exist while it is re-created; if the loop ends without success, cloud-init shows the failure and the sync unit fails visibly on the missing file) to `/opt/ftg/bin/sync.sh` and `chmod 0755`s it (`sync.sh` itself is not embedded in `cloud_init.yaml.tftpl`: it is 1 file shared with the AWS stack, `terraform/sync.sh`, and every later tick re-reads the same manifest field and replaces its own installed copy when the hash changes, [spec 0003](0003-prober-core-spec.md) section 6A); `systemctl daemon-reload`; enables and starts `ftg-prober-sync.timer`; `systemctl start --no-block ftg-prober-sync.service` once. This first run builds the first release, gates it, swaps `/opt/ftg/current` onto it, enables and starts `ftg-prober.service` itself, and health-checks it ([spec 0003](0003-prober-core-spec.md) section 6A). cloud-init does not `enable --now ftg-prober.service` itself; `sync.sh` does, once that first build passes its gate.

Token and download, from the Microsoft docs:

- Token: `GET http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fstorage.azure.com%2F` with the header `Metadata: true`. The `Metadata` header "must be set to "true", in all lower case" ([acquire a token on a VM](https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/how-to-use-vm-token)).
- Resource: "To request tokens for Azure Storage, specify the value `https://storage.azure.com/` for the resource ID" ([authorize with Microsoft Entra ID](https://learn.microsoft.com/en-us/rest/api/storageservices/authorize-with-azure-active-directory)).
- Download: `GET <blob endpoint>code/<blob>` with `Authorization: Bearer <token>` and `x-ms-version: 2021-08-06`. The service version must be "2017-11-09 ... or higher" ([same page](https://learn.microsoft.com/en-us/rest/api/storageservices/authorize-with-azure-active-directory)). `fetch-blob.sh` reads the HTTP status code of this call (`curl -w '%{http_code}'`) to tell a 404 apart from every other failure.

Optional proxy. The variables are empty by default. With the defaults, the rendered cloud-init has none of the content below, and it is the same as before the variables existed.

1. When `egress_proxy_url` is set, `runcmd`'s own fetch of `sync.sh` (step 3 above) exports `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY`, and the same 3 names in lower case, directly in that same command, because it runs outside any systemd unit, the same reason `install.sh` used to export them directly. `control.tf`'s `local.proxy_export_cmd` builds this prefix; it is empty when there is no proxy, so the `runcmd` line needs no template `%{ if }` of its own.
2. When `egress_proxy_url` is set, cloud-init also gets an `apt:` block with `http_proxy` and `https_proxy` ([cloud-init apt configuration](https://cloudinit.readthedocs.io/en/latest/reference/yaml_examples/apt.html)).
3. Each of `ftg-prober.service` and `ftg-prober-sync.service` gets its own drop-in only when `egress_proxy_url` is set ([systemd `Environment=`](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html)): `.service.d/proxy.conf` sets the 6 proxy variables. `sync.sh` itself calls `fetch-blob.sh` and `pip install` through the same proxy.
4. The cloud-init must not write `/etc/environment`.
5. `NO_PROXY` always holds `169.254.169.254` (the instance metadata service, [IMDS](https://learn.microsoft.com/en-us/azure/virtual-machines/instance-metadata-service)), `168.63.129.16` (the Azure wire server, [what is 168.63.129.16](https://learn.microsoft.com/en-us/azure/virtual-network/what-is-ip-address-168-63-129-16)), `localhost`, `127.0.0.1`, and `login.microsoftonline.com`, followed by the entries of `no_proxy`. The token call and the sign-in go direct. The Azure Resource Manager API (`management.azure.com`) goes through the proxy.
6. A storage account with a private endpoint is reached by its private address. A proxied call to it fails. In that case the deployer must add `.blob.core.windows.net` to `no_proxy`.

`ftg-prober.service`: `EnvironmentFile=/etc/ftg/env`, `ExecStart=/opt/ftg/current/venv/bin/ftg-prober run --provider azure --config /opt/ftg/current/config/<file name of targets_file> --store az://<account>/data/` (the default file name is `targets.yaml`), `Restart=always`, `RestartSec=30`, `TimeoutStopSec=180` (the floor; before each restart, `sync.sh` computes `max(180, probe_timeout_seconds + 2 x teardown_timeout_seconds + 30)` from the `azure` section of the release's config: on a stop, each Region lane finishes only its attempt in progress, all lanes at once ([ADR 0020](../decisions/0020-provisioning-probes-run-in-region-lanes-off-the-signal-thread.md)), and the slowest attempt is a scale set attempt: a pre-teardown, the probe, and the teardown (spec 0010, sections 5 and 7.2, `prober/ftg_prober/azure/prechecks.py` and `vmss_flex.py`: 60 s + 90 s + 60 s at the defaults, so 240 s), writes it to the drop-in `/etc/systemd/system/ftg-prober.service.d/stop-timeout.conf`, and runs `systemctl daemon-reload`, so a restart does not `SIGKILL` a probe mid-teardown; the value is not in the custom data, so it never replaces the VM), `DynamicUser=yes`, `After=network-online.target`. `ExecStart` names `/opt/ftg/current`, never a release path directly, so this unit file never changes across a release; `sync.sh` runs `systemctl enable ftg-prober.service` just before every restart it makes, idempotently.

`ftg-prober-sync.service` and `ftg-prober-sync.timer` (`terraform/ftg-prober-sync.service`, `terraform/ftg-prober-sync.timer`, shared with the AWS stack) run `sync.sh` as root, every 5 minutes and once at first boot (`runcmd`, above):

```ini
[Unit]
Description=follow-the-gpus prober sync (ADR 0012)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
TimeoutStartSec=3600
ExecStartPre=/bin/bash -c '<copy sync.trial to sync.trial.start, or remove sync.trial.start>'
ExecStart=/opt/ftg/bin/sync.sh
ExecStopPost=/bin/bash -c '<revert a failed, timed-out, or unproven trial copy; spec 0003 section 6A>'
```

The file `terraform/ftg-prober-sync.service` holds the full `ExecStartPre` and `ExecStopPost` lines. `TimeoutStartSec=3600` is 2 health windows of 900 s plus 1800 s of build time. The revert is an `ExecStopPost` because the start timeout kills the `ExecStart` cgroup.

```ini
[Unit]
Description=Run ftg-prober-sync.service on a timer (ADR 0012)

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

The interval is a setting of the timer unit, not a Terraform variable. `sync.sh` (root) builds each release under `/opt/ftg/releases/` and owns `/opt/ftg/current`; `ftg-prober.service`'s `DynamicUser=yes` already implies `ProtectSystem=strict` and `ProtectHome=read-only` ([systemd.exec(5)](https://manpages.ubuntu.com/manpages/noble/man5/systemd.exec.5.html)), so it cannot write under `/opt/ftg` even if its dynamically allocated user were granted the bit, because the unit lists no `ReadWritePaths=`.

`ftg-compact.service` and `ftg-compact.timer` run `ftg-prober compact` ([spec 0003](0003-prober-core-spec.md) section 9B, [ADR 0025](../decisions/0025-daily-compaction-runs-on-the-prober-vm.md)). They are the units of [spec 0004](0004-aws-stack-spec.md) section 6, with these differences: `DynamicUser=yes` in place of `User=ftg` (the same as `ftg-prober.service`), `--provider azure`, and `--store` from the `store_url` field of `manifest.json` (section 3.1). `sync.sh` writes both units, and the proxy drop-in of `ftg-prober.service` when it exists, each time their text differs from the file on the VM, then runs `systemctl daemon-reload` and, when the timer is not enabled, `systemctl enable --now ftg-compact.timer`. They are not in `custom_data`, because a `custom_data` change replaces the VM (section 3.3). `sync.sh` installs them from its `EXIT` trap, after the release outcome, and a failure there is logged and never changes the exit code or the prober (spec 0004 section 6). `MemoryMax=384M` matters more here: the default `Standard_B2ats_v2` has 1 GiB of memory ([Basv2 series](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/basv2-series)). The peak of `compact_day` on the largest real day and probe type, with the SDK loaded, measured as macOS RSS, was 179 MiB on Azure and about 190 MiB on AWS; the full job on the VM is not measured yet.

`sync.sh` also runs `systemctl disable --now apt-daily-upgrade.timer apt-daily.timer` when either timer is enabled, from the same `EXIT` trap and under the same rules. Reason: the daily OS upgrade made `needrestart` restart `ftg-prober.service` at a random time near 06:00 UTC, which drains the in-flight probes. The VM now gets OS updates only when it is re-created. `disable --now` stops the timers and removes their enable links; it is idempotent, and a later tick does nothing ([systemctl](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html), [Ubuntu automatic updates](https://documentation.ubuntu.com/server/how-to/software/automatic-updates/)). The command is not in `custom_data`, so no instance or VM is replaced.

### 3.5 Sweeper

| Resource | Name | Settings |
|---|---|---|
| Storage account | `<prefix>func<storage suffix>` | Standard, LRS, TLS 1.2, HTTPS only, no public nested items. Only the Function App uses it. The Function App connects with the account access key (`storage_account_access_key`). |
| Service plan | `<prefix>-sweeper-<region>-plan` | Linux, `sku_name = sweeper_plan_sku` (default `Y1`). "Elastic and Consumption SKUs (`Y1`, `FC1`, `EP1`, `EP2`, and `EP3`) are for use with Function Apps" ([azurerm_service_plan](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/service_plan)). |
| Log Analytics workspace | `<prefix>-sweeper-<region>-law` | `PerGB2018`, 30 days retention |
| Application Insights | `<prefix>-sweeper-<region>-ai` | type `web`, linked to the workspace |
| Function App | `<prefix>-sweeper-<region>-<suffix>` | Linux, runtime `~4`, Python `3.12`, system-assigned identity, HTTPS only, built-in logging off, `site_config.always_on` true only when `sweeper_plan_sku` is not `Y1` |

The Log Analytics workspace and Application Insights are the Functions host's own operational telemetry: invocation success and duration, and the "sweeper stopped" alert of section 3.6, which the host already builds on a trace of the health line (spec 0007 section 9). They are not our log of record under the logs/metrics rule (spec 0003 section 8.2, [CONTRIBUTING.md](../CONTRIBUTING.md)): the sweeper's own log, like the prober's, goes to the data storage account's logs prefix, through the handler of spec 0007 section 9. The `sweeper` logger keeps propagating to the root logger so the host still has the health line for the alert; that propagation is host health monitoring, kept as is, not a 2nd copy of our log.

Deployment: `zip_deploy_file` points to an `archive_file` zip of `../../sweeper/azure`, built under `.terraform/ftg-build/` with the prober and config zips (section 3.1) and named `sweeper-<sha256>.zip`, where `<sha256>` is a hash of the names and contents of the files that the sweeper runs: `function_app.py`, `host.json`, `requirements.txt`, and every `.py` file of the `sweeper/` package (the entry is `<path>:<sha256 of the file>`, joined by `,`, sorted by path). The zip also holds `pyproject.toml` and `uv.lock`, which the runtime does not use, so they stay out of the hash. The archive excludes `.venv`, `__pycache__`, `tests`, `.pytest_cache`, `.ruff_cache`, `.python_packages`, `local.settings.json`, each at every depth. Redeploy rule: `zip_deploy_file` is not `ForceNew`, and the provider publishes the zip only when the value of this argument changes (`HasChange("zip_deploy_file")` in [`linux_function_app_resource.go`, v4.81.0](https://github.com/hashicorp/terraform-provider-azurerm/blob/v4.81.0/internal/services/appservice/linux_function_app_resource.go)). A constant path would give no plan diff for a code change, and the new code would never deploy. With the hash in the path, a code change is an in-place update of the Function App: no replacement, the identity and its role assignments stay. `scripts/check-sweeper-redeploy.sh`, run by `make test`, proves it (section 8). Post-apply check: the zip deploy is a push to the Kudu `api/zipdeploy` endpoint of the app ([Zip deployment](https://learn.microsoft.com/en-us/azure/azure-functions/deployment-zip-push)), and, by the source of azurerm 4.81.0 and plugin SDK 2.40.1, a failed push still leaves the new `zip_deploy_file` in state, so the next plan is clean and the old code keeps running (read from the source, not seen in a live apply: [research/learnings/azure-failed-zip-deploy-can-leave-a-clean-plan.md](../research/learnings/azure-failed-zip-deploy-can-leave-a-clean-plan.md)). The stack therefore cannot rely on the plan. The sweeper computes the same hash over its own files at run time (`sweeper/azure/sweeper/code_sha.py`) and logs `code_sha=<hash>` in every invocation (spec 0007, section 9). The output `sweeper_code_sha` holds the hash that state expects, and `make verify/azure-sweeper` (`terraform/azure/scripts/verify-sweeper.sh`) fails unless the newest `code_sha` in the store logs equals it, waiting up to `FTG_VERIFY_WAIT_SECONDS` (600) for the first run of the new code. Set `APPLY_END_UTC` (for example `2026-01-31T23:59:59Z`) to the end time of the apply: the check then also needs the matching log object to be modified at or after that time, and says "not proven after the apply" otherwise, so a log from before the apply or a same-hash redeploy cannot pass. Run it after every apply that changes the sweeper. A failed check is an incident: the sweeper, the cost brake, runs old code. The stack does not taint the app by itself: a taint would replace the app and its identity on a transient Kudu timeout. The azurerm doc says: "Using this value requires either `WEBSITE_RUN_FROM_PACKAGE=1` or `SCM_DO_BUILD_DURING_DEPLOYMENT=true` to be set on the App in `app_settings`" ([azurerm_linux_function_app](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/linux_function_app)). The stack uses the remote build. Microsoft says: "To enable remote build on Linux Consumption, Elastic Premium, and App Service plans, set these application settings: `ENABLE_ORYX_BUILD=true`, `SCM_DO_BUILD_DURING_DEPLOYMENT=true`", and "Don't set `WEBSITE_RUN_FROM_PACKAGE` when you request a remote build" ([deployment technologies](https://learn.microsoft.com/en-us/azure/azure-functions/functions-deployment-technologies)). "Remote build installs all dependencies listed in `requirements.txt`" and "Remote build has a 60-second timeout" ([Python build options](https://learn.microsoft.com/en-us/azure/azure-functions/python-build-options)). ZIP deployment is "Supported" for the Consumption plan, which the same page labels "Consumption plan (legacy)". On Linux Consumption, ZIP deployment depends on the remote build: "External package URL is the only supported deployment method for apps running on the Linux Consumption plan when you don't want a remote build to occur" ([deployment technologies](https://learn.microsoft.com/en-us/azure/azure-functions/functions-deployment-technologies)). The azurerm doc adds: "Hosting Azure Functions on Linux using the Consumption plan will be retired after September 30, 2028" ([azurerm_service_plan](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/service_plan)). On the deployment technologies page, ZIP deployment is "Not supported" for Flex Consumption. Flex uses "Flex Consumption package deployment" and a different azurerm resource, [`azurerm_function_app_flex_consumption`](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/function_app_flex_consumption). The stack stays on Consumption by default, because `azurerm_linux_function_app` with `zip_deploy_file` and the remote build works there.

Package URL: "In Linux Consumption, the app content is instead persisted on a blob in the storage account specified by the `AzureWebJobsStorage` app setting, and the app setting `WEBSITE_RUN_FROM_PACKAGE` takes on the value of the blob URL" ([deployment technologies](https://learn.microsoft.com/en-us/azure/azure-functions/functions-deployment-technologies)). The Function App has `lifecycle.ignore_changes = [app_settings["WEBSITE_RUN_FROM_PACKAGE"]]`, so a plan does not remove the setting.

App settings: `FTG_PROBE_RG=<resource_group_name>`, `FTG_EXPERIMENT=<experiment>`, `FTG_MAX_AGE_SECONDS=180`, `AZURE_SUBSCRIPTION_ID=<the subscription of the deploy>` (the Resource Graph query is scoped to it, and the sweeper fails without it, spec 0007 section 4), `FTG_DATA_STORAGE_ACCOUNT=<the data storage account's name>` (so the sweeper can write its own log objects there, spec 0003 section 8.2, spec 0007 section 9), `SCM_DO_BUILD_DURING_DEPLOYMENT=true`, `ENABLE_ORYX_BUILD=true`. The stack sets the sweeper's `FTG_EXPERIMENT` app setting from `local.experiment`, the `experiment` value of the targets file, the only source; there is no separate tag input. The Application Insights connection string goes through `site_config.application_insights_connection_string`. The python version allowed values include `3.12` ([azurerm_linux_function_app](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/linux_function_app)).

Plan SKU: `sweeper_plan_sku` selects the plan. The rules:

1. `Y1` (default): the Linux consumption plan. `always_on` is false.
2. `B1`, `B2`, `B3`, or `S1`: a dedicated (App Service) plan. `always_on` is true.
3. Any other value fails the variable validation.
4. On each plan, the app settings, the zip deploy, and the remote build are the same. The stack does not put `WEBSITE_RUN_FROM_PACKAGE` in `app_settings`. On `Y1` the platform sets it (Package URL above).
5. A change between `Y1` and a dedicated SKU after a deploy replaces the plan and the Function App. The operator runs `tofu apply -replace=azurerm_service_plan.sweeper -replace=azurerm_linux_function_app.sweeper`. A change between 2 dedicated SKUs is an in-place update. Microsoft says about plan migration of a function app: "Direct migration to a Dedicated (App Service) plan isn't supported" and "Migration isn't supported on Linux" ([plan migration](https://learn.microsoft.com/en-us/azure/azure-functions/functions-how-to-use-azure-function-app-settings#plan-migration)).

Sweeper region: `sweeper_location` selects the region of the sweeper resources. The rules:

1. The local value `sweeper_location` is `coalesce(var.sweeper_location, var.location)`. With the default `null`, the sweeper is in `location`.
2. The function storage account, the service plan, the Log Analytics workspace, Application Insights, and the Function App use that region.
3. These resources stay in `resource_group_name`. The prober VM, the data storage account, and the data container stay in `location`.
4. The sweeper reads no region setting. It finds the probe resources by `FTG_PROBE_RG` and the experiment tag, and it acts on each by its resource ID. Thus its app settings and its roles (section 5) are the same for each value.
5. The sweeper-stopped alert rule follows the region of Application Insights (section 3.6).
6. A change of `sweeper_location` after a deploy replaces each sweeper resource in rule 2. The alert rule follows the new Application Insights resource. Run the change when no probe runs, as for a change of plan SKU. The old Log Analytics workspace goes to the soft-delete state and keeps its logs for 14 days. The stack does not delete it permanently.
7. The names of the sweeper resources carry the sweeper region, so a new resource does not collide with a soft-deleted resource of the same name in the old region. `<region>` is the sweeper region in lower case with every character other than a letter or a digit removed, for example `centralus`. The service plan, the Log Analytics workspace, Application Insights, and the Function App use `<region>` in the name, as the table shows. The storage account name is limited to 24 lower-case letters and digits, and `<prefix>` can be 10 characters, so it cannot hold the region. Its `<storage suffix>` is the first 8 hex digits of the SHA-256 of the subscription ID followed by `<region>`. Each name stays inside the name rules of its type ([naming rules](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/resource-name-rules)). A deleted Log Analytics workspace keeps its name reserved for 14 days ([Delete and recover a workspace](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/delete-workspace)).
8. This rule renames the sweeper resources, also with the default `sweeper_location`. The next apply after an upgrade replaces the sweeper resources in `location`.

Facts for these rules:

- The azurerm `sku_name` "Possible values include `B1`, `B2`, `B3`" and also `S1` and `Y1` ([azurerm_service_plan](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/service_plan)).
- "The dedicated App Service plans supported for function app hosting include Basic, Standard, Premium, and Isolated SKUs" ([Dedicated hosting](https://learn.microsoft.com/en-us/azure/azure-functions/dedicated-plan)).
- "When you run your app on an App Service plan, you should enable the **Always on** setting so that your function app runs correctly. On an App Service plan, the Functions runtime goes idle after a few minutes of inactivity. The **Always on** setting is available only on an App Service plan" ([Dedicated hosting](https://learn.microsoft.com/en-us/azure/azure-functions/dedicated-plan)). The sweeper is a timer trigger, so an idle runtime stops the cost brake.
- The azurerm `always_on` argument "Defaults to `false`". The doc adds: "when running in a Consumption or Premium Plan, `always_on` feature should be turned off" ([azurerm_linux_function_app](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/linux_function_app)).
- ZIP deployment is "Supported" for the Dedicated plan. "Don't set `WEBSITE_RUN_FROM_PACKAGE` when you request a remote build. For Linux Consumption, Elastic Premium, and Dedicated plan apps, instead enable remote build by using the deployment settings described in the **Linux** tab" ([deployment technologies](https://learn.microsoft.com/en-us/azure/azure-functions/functions-deployment-technologies)).
- Cost: for the dedicated compute tiers, which include Basic and Standard, "*each VM instance* is charged" ([App Service plans](https://learn.microsoft.com/en-us/azure/app-service/overview-hosting-plans)). Consumption plan pricing "includes a monthly free grant of 1 million requests and 400,000 GB-s of resource consumption", per subscription and pay-as-you-go only ([Functions pricing](https://azure.microsoft.com/en-us/pricing/details/functions/)). Thus a dedicated plan adds a charge that does not depend on the number of sweeper runs.

The sweeper code (`function_app.py`, `requirements.txt`, `host.json`) is in [spec 0007](0007-sweeper-spec.md). The Function App does not set `WEBSITE_RUN_FROM_PACKAGE`, `AZURE_CLIENT_ID`, or `AZURE_CLIENT_SECRET` (spec 0007, sections 2 and 4).

### 3.6 Sweeper-stopped alert

This is the "sweeper stopped" alert of spec 0002, section 6, item 5. [Spec 0007](0007-sweeper-spec.md), section 9, defines the signal: no `sweeper run:` trace for 10 minutes, or failed requests of the function. The stack makes both resources below only when `alert_email` is not empty, the same as the budget (section 4.7). The file is `terraform/azure/alerts.tf`.

| Resource | Name | Settings |
|---|---|---|
| `azurerm_monitor_action_group` | `<prefix>-alerts` | In `resource_group_name`. `short_name` = the first 12 characters of `<prefix>-alerts`. 1 `email_receiver`: name `alert-email`, `email_address = alert_email`, `use_common_alert_schema = true`. `location` stays at the provider default. |
| `azurerm_monitor_scheduled_query_rules_alert_v2` | `<prefix>-sweeper-stopped` | In `resource_group_name`, in the region of Application Insights. `scopes` = the sweeper Application Insights resource only. `severity = 1`. `evaluation_frequency = PT5M`, `window_duration = PT10M`. `auto_mitigation_enabled = true`. Criteria: the query below, `time_aggregation_method = Count`, `operator = GreaterThan`, `threshold = 0`. No `failing_periods` block, no dimensions, no identity. `action.action_groups` = the action group. |

The query:

```kusto
traces
| where timestamp > ago(10m) and message startswith "sweeper run:"
| summarize runs = count()
| extend failed = toscalar(requests | where timestamp > ago(10m) and tostring(success) =~ "false" | count)
| where runs == 0 or failed > 0
```

The query returns 1 row when the sweeper has a problem, and no row when it is healthy. The rule fires on 1 row or more.

Facts behind the rule:

1. The data. The Functions host writes user logs to the `traces` table. Category `Function.<YOUR_FUNCTION_NAME>.User` holds "User-generated logs". Category `Worker` holds "Logs generated by the language worker process for non-.NET languages". The page says that custom logs go "under the Worker category" by default. Category `Host.Results` goes to the `requests` table: "These runtime-generated logs indicate success or failure of a function" ([configure monitoring](https://learn.microsoft.com/en-us/azure/azure-functions/configure-monitoring#configure-categories)). The `Host.Results` logs "are written at the `Information` level". The sweeper `host.json` sets the default level to `Information` (spec 0007, section 9).
2. The names. A query on the Application Insights resource uses the table names `traces` and `requests`. A query on the Log Analytics workspace uses `AppTraces` and `AppRequests`. The trace text is the field `message`, and the request result is the field `success` ([telemetry data model](https://learn.microsoft.com/en-us/azure/azure-monitor/app/data-model)). `tostring(success) =~ "false"` matches the bool value `false` and the text `False` alike, because `=~` is not case-sensitive ([string operators](https://learn.microsoft.com/en-us/kusto/query/datatypes-string-operators)).
3. The empty case. For `summarize` with no group expression, "the output is a single (empty) row" when the input is empty, and the default value of `count()` is 0 ([summarize operator](https://learn.microsoft.com/en-us/kusto/query/summarize-operator)). Thus `runs` is 0 when no health line arrived, and the query returns 1 row.
4. The measure. A log search alert can measure "Table rows": "You can use the number of returned rows". Log search alert queries support `ago()` "with timespan literals only" ([create a log search alert rule](https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-create-log-alert-rule)). The provider says: "`metric_measure_column` can not be specified if `time_aggregation_method` is `Count`" ([azurerm_monitor_scheduled_query_rules_alert_v2](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/monitor_scheduled_query_rules_alert_v2)).
5. The time settings. `evaluation_frequency` allows `PT5M`. When the frequency is not `PT1M`, `window_duration` allows `PT10M`. The provider adds: "`number_of_evaluation_periods` must be `1` for queries that do not project timestamp column". This query projects no timestamp column. For `scopes`: "Currently, the API supports exactly 1 resource ID in the scopes list" ([azurerm_monitor_scheduled_query_rules_alert_v2](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/monitor_scheduled_query_rules_alert_v2)).
6. The resolve. `auto_mitigation_enabled` has the default `false` in the provider ([same page](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/monitor_scheduled_query_rules_alert_v2)). The stack sets `true`. With a frequency of 5 to 15 minutes, a stateful alert resolves when "The alert condition isn't met for three frequency periods" ([create a log search alert rule](https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-create-log-alert-rule)).
7. The delay. For the Monitor tab of a function, "It can take up to five minutes for the list to appear while the telemetry client batches data for transmission to the server" ([analyze Functions telemetry](https://learn.microsoft.com/en-us/azure/azure-functions/analyze-telemetry-data)). The sweeper runs every minute, so a 10-minute window holds about 10 health lines. Microsoft adds: "Log search alerts work best when you're trying to detect specific data in the logs, as opposed to when you're trying to detect a lack of data in the logs" ([alert types](https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-types)). Thus late telemetry can cause a false alert.
8. The identity. "If you don't use a managed identity, the alert rule will inherit the permissions of the last user or service principal who edited it" ([create a log search alert rule](https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-create-log-alert-rule)). The deployer has the Owner role, or Contributor plus User Access Administrator, on the subscription (README prerequisites). Both can read Application Insights: Owner and Contributor allow `*`, and User Access Administrator allows `*/read` ([privileged roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/privileged)).
9. The action group. The ARM template sample of Microsoft describes the short name as "Short name (maximum 12 characters) for the Action group". For an email receiver: "Email addresses must be verified through a one-time passcode (OTP) within 30 minutes of saving the action group" ([action groups](https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/action-groups)). The provider `location` "Defaults to `global`" ([azurerm_monitor_action_group](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/monitor_action_group)).
10. The cost. "Each log search alert rule is billed based on the interval at which the log query is evaluated" ([alert types](https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-types)).

## 4. Probe resources

### 4.1 Targets file to resources

Input: `yamldecode(file(targets_file))`, section `azure.provisioning_probes` (spec 0003 section 3): a mapping from a probe type (`az_capacity_reservation` or `az_vmss_flex`) to a list of entries ([ADR 0022](../decisions/0022-azure-provisioning-probes-are-listed-by-probe-type-and-find-their-resources-by-tags.md)). Each entry has `sku`, `region`, `zone` (null or a zone), `n`, `placement_group_mode` (`multi`; `single` is removed, [ADR 0021](../decisions/0021-provisioning-probes-measure-available-capacity-not-single-cluster-capacity.md)), and an optional `simulate` (boolean, default `false`). There is no `run_mode` key (ADR 0011, final).

`local.reservation_probes` and `local.vmss_probes` read `azure.provisioning_probes.az_capacity_reservation` and `azure.provisioning_probes.az_vmss_flex` with `try(..., [])`, with no fallback to an earlier shape. `terraform_data.targets_check` fails the plan, with the message of `check-config`, when the `azure` section has a key outside `KNOWN_CLOUD_KEYS`, `azure.cadence` has a renamed key (for example `provisioning_probes`, `az_allocation`), `azure.provisioning_probes` is the old list, a probe type key is unknown, an entry has the removed `route` key or `placement_group_mode: single`, or two `az_vmss_flex` entries have the same size and Region (any letter case, any zone). So a misspelled or old key cannot give an empty list that plans the removal of stateful resources.

| Rule | Resource | Count |
|---|---|---|
| R1 | An entry is *active* when its own `simulate` key is not `true` (`try(p.simulate, false) != true`, ADR 0011 section 2). A simulated entry calls no Azure API and gets no resource. | |
| R2 | A scale set needs a subnet in its own region (section 4.3). The stack builds a map of lower-case region to subnet: `location` maps to `subnet_id`, and each entry of `probe_subnet_ids` adds its region, or replaces the `subnet_id` fallback for `location`. Each entry that gets a scale set (R4) must have its `region` in this map (case-insensitive). A `precondition` on the scale set fails the plan otherwise, with "no subnet for <region>". A capacity reservation needs no subnet, so R3 has no such limit. | |
| R3 | Each region in the `az_capacity_reservation` entries gets 1 capacity reservation group. Its zones are the distinct non-null zones of the entries in that region, or none. The stack does not support zonal and regional (`zone: null`) entries in 1 region: the group takes the zones of the zonal entries, and the regional entries then share a zonal group. | 1 per region |
| R4 | Each active `az_vmss_flex` entry, of any size, gets 1 scale set. The triple `sku`, `region`, `placement_group_mode` is the key (the address of the resource). At most 1 `az_vmss_flex` entry per size and Region is allowed (any letter case, any zone): the prober selects the scale set by its tags, and a second entry would match the same scale set. A duplicate fails the plan. | 1 per entry |
| R5 | A change from `simulate: true` to not simulated needs `make plan/azure` and `make apply/azure`, because R4 adds resources. | |

**R6 is superseded. [ADR 0015](../decisions/0015-two-independent-controls-replace-the-terraform-allocation-guard.md) removes the targets-file-reading guard this section used to describe** (`terraform_data.allocation_guard`, its 2 `lifecycle { precondition }` blocks, and every `depends_on` on it). Re-parsing the targets file's `allow_billable_provisioning` key in HCL, to agree with PyYAML's own reading of the same YAML, had no principled stopping point: a quoted key, a JSON-style flow mapping, an explicit `!!bool` tag, an inline anchor, and an alias each needed their own raw-text regex, because `yamldecode`'s bool resolver (`zclconf/go-cty-yaml`'s own implementation, following the full [YAML 1.1 bool type](https://yaml.org/type/bool.html)) reads a bare `y`/`Y`/`n`/`N` as a real boolean, indistinguishable from `true`/`false` by the time any HCL expression can see the decoded value, while PyYAML (the prober's own loader) does not. The per-job `allow_billable_provisioning` key stays exactly where it already was, checked only by the prober's own config guard ([spec 0003](0003-prober-core-spec.md), section 3); this stack does not read it at all.

**R7 (ADR 0015 section 1).** `var.grant_billable_provisioning` (bool, default `false`) alone decides whether this stack is capable of provisioning real capacity. It reads nothing from the targets file:

- `local.crg_regions` (R3) and `local.vmss_targets` (R4) are each empty unless `var.grant_billable_provisioning` is `true`; `false` (the default) plans no capacity reservation group and no scale set, no matter what `provisioning_probes` lists.
- `true` still follows the file's shape for which regions and SKUs plan (R1 to R4 are otherwise unchanged): a capacity reservation group per region, 1 scale set per active `az_vmss_flex` entry.
- `azurerm_role_assignment.prober_capacity_allocator` plans only when `var.grant_billable_provisioning` is `true`, `count = var.grant_billable_provisioning ? 1 : 0`. `azurerm_role_assignment.prober_probe_subnet_joiner` (`for_each` over `local.probe_subnets_to_join`, section 5.1.1) needs the variable too, because it reads `local.vmss_targets`, which is empty without it; it also needs a real scale set in the region of the subnet. Neither reads the targets file for consent. These 2 are the rights that create billable capacity.
- `azurerm_role_assignment.sweeper_cleanup` is unconditional, deliberately not gated the same way. `<prefix>-cleanup` holds 3 write rights (`virtualMachines/write`, `virtualMachineScaleSets/write`, `virtualMachineScaleSets/scale/action`) only so that the sweeper can set a scale set to capacity 0 and delete; the sweeper code never creates a resource and never scales up (`sweeper/azure/sweeper/azure_clients.py:111-113` sets `sku.capacity` to 0, the only write call, from `sweeper/azure/sweeper/run.py:209`; every other call is a delete, `azure_clients.py:87`). The trade-off: the sweeper identity could, in principle, scale a set up. The project accepts that, because removing these rights would strand leaked capacity that only a scale to 0 can stop. Gating it on `var.grant_billable_provisioning` would strand a leaked VM, disk, NIC, or prober-made reservation the moment an operator turns the grant off, with nothing left able to delete it.

The 2 controls (this variable, and the prober's own `allow_billable_provisioning` guard) are independent and can disagree. With `grant_billable_provisioning = false` and an active, consented entry in the file, the prober's job still runs (its own guard passes), but the probe fails. When the grant was off at plan time, no scale set or capacity reservation group exists, so the tag selection finds none and the probe writes an `error` row (`no scale set for this experiment entry` or `no reservation group for this experiment entry`) with no Azure write ([ADR 0022](../decisions/0022-azure-provisioning-probes-are-listed-by-probe-type-and-find-their-resources-by-tags.md)). When a resource exists but the role lacks the right, the call fails with the provider's error, written to the log as returned (for example [`AuthorizationFailed`](https://learn.microsoft.com/en-us/azure/role-based-access-control/troubleshooting), when a resource exists but the role lacks the right). This is the intended, visible outcome of that disagreement, not a bug: it tells the operator the stack's grant and the file's consent do not match, instead of silently creating capacity nobody meant to grant rights for. With `grant_billable_provisioning = true` and no active entry (or none at all), the role assignments still plan; the rights sit unused until the file also lists an active, consented entry. See ADR 0015 for the full trade-off table.

**Turning the grant off removes the prober's own `prober_capacity_allocator` right and the capacity reservation groups in 1 apply (R7). If an entry is still active, that apply also removes its scale set (R7 empties `local.vmss_targets`): the prober loses the rights it needs to tear its own allocation down cleanly, and only the (always-on) sweeper is left able to delete what remains, on its own schedule (spec 0007, `FTG_MAX_AGE_SECONDS`).** Before switching `grant_billable_provisioning` from `true` to `false`, set every entry in the targets file to `simulate: true` first, deploy that change, and wait until `make status/azure` shows 0 leaked resources (the line `leaked resources: 0`) before applying the grant change itself. In this order, the `simulate: true` apply removes the scale sets and their `prober_probe_subnet_joiner` assignments, because a simulated entry gets no scale set (R1, R4). The grant apply then removes the capacity reservation groups and `prober_capacity_allocator` (R3, R7). 1 apply that flips the grant with active entries still in the file can leave a VM, disk, NIC, or reservation that only the sweeper can still reach, and only after its own age threshold passes.

Spec 0002 section 1 requires a CPU size that supports capacity reservations. `config/targets.yaml` uses `Standard_D2as_v4` under `az_capacity_reservation`, so it gets a reservation group and no scale set. To exercise the scale set path with a non-simulated entry, list the entry under `az_vmss_flex`: the stack does not read SKU capabilities, and the file decides.

### 4.2 Names

| Resource | Name | Example |
|---|---|---|
| Capacity reservation group | `<prefix>-crg-<region>-<experiment hash>` | `ftg-crg-eastus-1a2b3c4d` |
| Scale set | `<prefix>-<sku slug>-<region>-<experiment hash>` | `ftg-nd96isr-h100-v5-eastus-1a2b3c4d` |

The SKU slug: drop `Standard_`, replace `_` with `-`, lower case. `Standard_B2s` gives `b2s`. The experiment hash is the first 8 characters of the SHA-256 of the experiment name, so 2 experiments in 1 resource group get different names. A scale set name is 1 to 64 characters for a Linux scale set ([Azure naming rules](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/resource-name-rules), `Microsoft.Compute/virtualMachineScaleSets`: 1-15 Windows, 1-64 Linux); a `precondition` checks the 64. The `target` tag and the OpenTofu key are `<sku slug>-<region>-multi`, for example `b2s-eastus-multi`; the key is the address of the resource and does not hold the hash.

**Tags the prober selects by (ADR 0022).** The prober never computes a name. It lists the scale sets or the reservation groups of the probe resource group and selects by tags:

| Resource | Tags the prober selects by | Other tags |
|---|---|---|
| Scale set | `experiment`, `sku` (the exact size name of the entry), `region` (lower case) | `target`, `run_id`, plus `tags` |
| Capacity reservation group | `experiment`, `region` (lower case) | plus `tags` |

A policy cannot change these tags only if no Azure Policy assignment adds, changes, or removes tags on the probe resource group. Check this before you deploy.

### 4.3 Network

Each scale set uses the subnet of its own region: `subnet_id` for `location`, and the entry of `probe_subnet_ids` for another region. Microsoft: "You can assign a NIC only to a virtual network in the same subscription and location as the NIC" ([network interfaces](https://learn.microsoft.com/en-us/azure/virtual-network/virtual-network-network-interface)). So each region needs its own subnet. The stack creates no network for the probes. A probe VM takes 1 private address of the subnet while it exists. The network owner must size each subnet for the largest `n` of the entries in its region. The plan cannot check the region of a subnet from its ID, so a subnet in the wrong region passes the plan and fails at scale-out with an Azure NIC error.

Why an isolated subnet is enough: the prober VM, in `location` and in `subnet_id`, makes every Azure API call through that subnet. A provisioning-probe VM (a scale-set VM) is only created, counted, and deleted by those calls. It never runs a workload and never calls anything itself. Thus a subnet in another region needs no outbound path and holds only provisioning-probe VMs. The intended shape is an isolated virtual network in the stack's own resource group: 1 small subnet, no peering, no route table, no NAT gateway, no public IP, `default_outbound_access_enabled = false`, and a network security group that denies all inbound traffic. A group on the subnet also meets the Defender for Cloud recommendation "Subnets should be associated with a network security group" ([Defender for Cloud networking recommendations](https://learn.microsoft.com/en-us/azure/defender-for-cloud/recommendations-reference-networking); Microsoft marks it "Enable if required", so it is off until the policy is enabled), and the [network security group overview](https://learn.microsoft.com/en-us/azure/virtual-network/network-security-groups-overview) describes the rules. The stack does not create this network (decision A31). The user brings these subnets as a prerequisite and lists them in `probe_subnet_ids` in `terraform/azure/terraform.tfvars` (section 11.1). Microsoft: "Flexible scale sets are secure by default. Any instances created via Flexible scale sets don't have the default outbound access IP associated with them" ([default outbound access](https://learn.microsoft.com/en-us/azure/virtual-network/ip-services/default-outbound-access)).

### 4.4 Capacity reservation group

`azurerm_capacity_reservation_group` with `zones` and tags. "Changing this forces a new resource to be created" applies to `zones` ([azurerm_capacity_reservation_group](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/capacity_reservation_group)). The prober creates and deletes reservations inside the group (spec 0002, 4.4). A reservation bills at the VM rate: "Capacity reservations are priced at the same rate as the underlying VM size" ([capacity reservation overview](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-overview)). A group without a reservation reserves no VM: a group "doesn't reserve any capacity. It just acts as a container for reservations" ([create a capacity reservation](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-create)). Billing is per reservation, so the group has no VM-rate charge.

### 4.5 Scale set profile

The resource is `azurerm_orchestrated_virtual_machine_scale_set`. Since provider v2.86.0 "this resource will only create Virtual Machine Scale Sets with the **Flexible** Orchestration Mode" ([azurerm_orchestrated_virtual_machine_scale_set](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/orchestrated_virtual_machine_scale_set)).

| Setting | Value | Note |
|---|---|---|
| `instances` | `0` | Capacity 0 (spec 0002, D5). |
| `sku_name` | the entry `sku` | |
| `location` | the entry `region` | |
| `zones` | `[zone]` when the entry has a zone, else none | |
| `platform_fault_domain_count` | `fault_domain_count` (default 1) | Required by the resource. Microsoft: 1 is "Max Spreading"; with `platformFaultDomainCount=1` the Flexible limit is 1000 instances ([orchestration modes](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes)). |
| `single_placement_group` | `false` | See the note below. |
| `priority` | `Regular` | |
| `extension_operations_enabled` | `false` | No extension. |
| `source_image_reference` | `Canonical:ubuntu-24_04-lts:server:latest` | Gen2. Spec 0002 4.5 requires Gen2 for ND H100 v5. |
| `os_disk` | `StandardSSD_LRS`, ReadOnly cache, image size | Smallest Standard SSD that fits the image. |
| `network_interface` | 1 NIC, primary, no network security group of its own, 1 IP configuration on `subnet_id`, no public IP | |
| `os_profile` | Linux, `ftgadmin`, the same SSH key as the prober VM, password off | |
| `tags` | `experiment=<tag>`, `sku=<exact size name>`, `region=<lower-case region>`, `target=<key>`, `run_id=none`, plus `tags` | `lifecycle.ignore_changes = [tags["run_id"]]`: the prober sets `run_id` before each capacity change. `instances` is not ignored, so a `make apply/azure` during a probe sets the capacity back to 0 and ends that probe. |

Member tags: the tags above are on the scale set resource only. The VM profile of a scale set has no `tags` property in the Compute API 2024-11-01 (the `VirtualMachineScaleSetVMProfile` definition in [ComputeRP.json](https://github.com/Azure/azure-rest-api-specs/blob/main/specification/compute/resource-manager/Microsoft.Compute/Compute/stable/2024-11-01/ComputeRP.json)). Microsoft says: "Resources don't inherit the tags you apply to a resource group or a subscription" ([tag resources](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/tag-resources)). The profile property `timeCreated` "will be added to VMSS Flex VM tags" (same definition), so the platform writes at least that tag on a Flexible VM. No Microsoft page says that a Flexible scale set copies its own tags to the VMs, disks, and NICs it creates (not verified). The sweeper finds VMs, disks, and NICs by the `experiment` tag (spec 0007, section 5). Thus:

1. The prober must tag each VM, OS disk, and NIC of a scale-out with `experiment`, `target`, and `run_id`, at once after the scale-out request (requirement for spec 0010).
2. The prober role has `Microsoft.Resources/tags/write`. Microsoft: write access to `Microsoft.Resources/tags` "lets you tag any resource, even if you don't have access to the resource itself" ([tag resources](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/tag-resources)).
3. Pilot P0 of spec 0002 reads the tags of the members of a CPU-sized scale-out with Resource Graph (open item).
4. Until P0 passes, `config/targets.yaml` lists no size under `az_vmss_flex` that is not simulated.

Single placement group: the azurerm doc says the setting "behaves differently for Flexible orchestration" and recommends not to set it, because "the service will determine what this value should be" ([azurerm_orchestrated_virtual_machine_scale_set](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/orchestrated_virtual_machine_scale_set)). Microsoft lists it under "Unsupported parameters" for Flexible: "this can be set to `null` and the platform will select the correct value" ([orchestration modes](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes)). The ARM reference adds: "If singlePlacementGroup is true, it may be modified to false. However, if singlePlacementGroup is false, it may not be modified to true" ([ARM reference, virtualMachineScaleSets](https://learn.microsoft.com/en-us/azure/templates/microsoft.compute/virtualmachinescalesets)). At the apply of 2026-10-15, a request with `true` failed at create with `OperationNotAllowed` ([learning brief](../research/learnings/azure-flexible-scale-set-rejects-single-placement-group.md)). The project dropped the `single` mode ([ADR 0021](../decisions/0021-provisioning-probes-measure-available-capacity-not-single-cluster-capacity.md)), so the stack sets `false` for every scale set, and the plan fails for an entry with `placement_group_mode: single`. Placement groups apply only to Uniform scale sets ([Availability Zones](https://learn.microsoft.com/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-use-availability-zones)); the stack has no Uniform path.

### 4.6 Delete option and resilient deletion

The azurerm resource exposes neither the disk and NIC delete options nor the resiliency policy. An `azapi_update_resource` sets them on each scale set after creation. azapi "can manage a subset of any existing Azure resource manager resource's properties" ([azapi_update_resource](https://registry.terraform.io/providers/Azure/azapi/latest/docs/resources/update_resource)). The type is `Microsoft.Compute/virtualMachineScaleSets@2024-11-01`. The body:

```json
{"properties": {
  "resiliencyPolicy": {
    "resilientVMCreationPolicy": {"enabled": false},
    "resilientVMDeletionPolicy": {"enabled": true}},
  "virtualMachineProfile": {
    "storageProfile": {"osDisk": {"deleteOption": "Delete"}},
    "networkProfile": {"networkInterfaceConfigurations": [
      {"name": "primary", "properties": {"deleteOption": "Delete"}}]}}}}
```

Sources:

- `resiliencyPolicy.resilientVMDeletionPolicy.enabled`: "Specifies whether resilient VM deletion should be enabled on the virtual machine scale set. The default value is false" ([ARM reference](https://learn.microsoft.com/en-us/azure/templates/microsoft.compute/virtualmachinescalesets)).
- Resilient delete "automatically retries VM deletions that fail during scale set deletion or scale-in operations. It addresses all transient platform errors". "Resilient delete makes up to five retry attempts". "Resilient create and delete is supported for Compute API version 2024-11-01 or higher" ([resilient create and delete](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/resilient-vm-create-delete)). The REST example on that page uses `api-version=2023-07-01`; the stack follows the prerequisite.
- Resilient create stays off. It retries only `OSProvisioningTimedOut` and `VMStartTimedOut` ([same page](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/resilient-vm-create-delete)). Both happen after allocation, while the VM bills (spec 0002, 4.5; not verified).
- `osDisk.deleteOption`: "Specifies whether OS Disk should be deleted or detached upon VMSS Flex deletion ... The default value is set to **Delete**" ([ARM reference](https://learn.microsoft.com/en-us/azure/templates/microsoft.compute/virtualmachinescalesets)). The stack sets it anyway.
- NIC `deleteOption`: the network configuration has the property "Specify what happens to the network interface when the VM is deleted", with the values `Delete` and `Detach` ([ARM reference](https://learn.microsoft.com/en-us/azure/templates/microsoft.compute/virtualmachinescalesets)). Microsoft documents `Delete` as the Flexible default: "For Flexible Virtual Machine Scale Set the disks, NICs, and PublicIPs have `deleteOption` set to `Delete` by default" ([delete a VM and attached resources](https://learn.microsoft.com/en-us/azure/virtual-machines/delete)). The ARM reference row gives no default. The stack sets it to `Delete` anyway, on the NIC configuration `primary`, the `network_interface` name of the azurerm resource.
- List merge: azapi reads the scale set, merges the body into it with `MergeObjectWithOption`, and writes the result with `CreateOrUpdate` ([azapi source](https://github.com/Azure/terraform-provider-azapi/blob/main/internal/services/azapi_update_resource.go)). For a list, "When not set, list items are matched by a `name` property (if present) or by list ordering" ([azapi_update_resource](https://registry.terraform.io/providers/Azure/azapi/latest/docs/resources/update_resource)). Thus the entry `primary` merges into the existing NIC configuration and keeps its IP configuration and security group. The azapi changelog adds `list_unique_id_property` in v2.9.0 ([azapi changelog](https://github.com/Azure/terraform-provider-azapi/blob/main/CHANGELOG.md)), thus the pin is `~> 2.9`.

When `azapi_update_resource` is destroyed, "no operation will be performed, and these properties will stay unchanged" ([azapi_update_resource](https://registry.terraform.io/providers/Azure/azapi/latest/docs/resources/update_resource)). That is fine: the scale set is destroyed with it.

### 4.7 Budget

`azurerm_consumption_budget_resource_group` on `resource_group_name`, only when `alert_email` is not empty. `amount = budget_amount`, `time_grain = Monthly`, `start_date` = the first day of the current month, 2 notifications: thresholds 80 and 100, operator `GreaterThanOrEqualTo`, type `Actual`, `contact_emails = [alert_email]`. The resource requires a notification, and "A `notification` block cannot have all of `contact_emails`, `contact_roles`, and `contact_groups` empty" ([azurerm_consumption_budget_resource_group](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/consumption_budget_resource_group)). Thus no email means no budget. `start_date` "must be first of the month" ([same page](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/consumption_budget_resource_group)). `lifecycle.ignore_changes = [time_period]` stops a new plan each month. The budget counts every resource in the group: the control resources, the probe resources, and the resources of other owners.

## 5. RBAC

Two identities. The custom roles are on `resource_group_name`, which also holds the control resources. Thus the prober role and the sweeper role can also act on the prober VM and its disk and NIC. The sweeper deletes only resources with the `experiment` tag, and the control resources do not carry it (section 1, spec 0007). Each custom role has `assignable_scopes` equal to its scope. The role definitions get a generated GUID; the repo holds none.

`azurerm_role_assignment.prober_capacity_allocator` plans only when `var.grant_billable_provisioning` is `true` (`count = var.grant_billable_provisioning ? 1 : 0`). `azurerm_role_assignment.prober_probe_subnet_joiner` (`for_each`; section 5.1.1) plans one assignment per probe subnet of a region that has a real (non-simulated) scale set, when that subnet is in another resource group and is not `subnet_id`; with the variable `false`, `local.vmss_targets` is empty and none plans (section 4.1, R7; [ADR 0015](../decisions/0015-two-independent-controls-replace-the-terraform-allocation-guard.md)). `azurerm_role_assignment.sweeper_cleanup` is unconditional, deliberately not gated on the variable: its write rights exist only to scale to 0 (R7), and gating it would strand a leaked resource the moment the grant turns off (section 4.1, R7). The 3 role definitions themselves (`<prefix>-capacity-allocator`, `<prefix>-cleanup`, `<prefix>-subnet-joiner`) always plan; a role definition grants nothing on its own.

### 5.1 Prober identity

| Role | Scope | Actions |
|---|---|---|
| Custom `<prefix>-capacity-allocator` | `resource_group_name` | The 23 actions in the table below. |
| Custom `<prefix>-data-writer`, with an ABAC condition | container `data` | Create and write blobs only (`Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write` and `.../blobs/add/action`): no read, no delete. The assignment carries the condition `blobs:path StringLike 'experiment=<experiment>/*'` on both actions. The path holds no container name and no leading slash. Both actions support conditions on the blob path attribute ([actions and attributes for role assignment conditions](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-auth-abac-attributes); [condition format](https://learn.microsoft.com/en-us/azure/role-based-access-control/conditions-format)). The prober's store uses `upload_blob(overwrite=False)` only, so a raw row or a log object is never overwritten. The compaction job's daily files under `experiment=<experiment>/daily/v1/` are the only objects that are replaced ([spec 0003](0003-prober-core-spec.md) section 9B). Changing the role or the condition re-creates the assignment on the next apply. |
| Built-in `Storage Blob Data Reader` | container `code` | Download of the prober zip, the config zip, `manifest.json`, and the hash-named `sync.sh` blob. |
| Built-in `Storage Blob Data Reader` | container `data` | Read and list blobs, for `ftg-prober compact` ([ADR 0025](../decisions/0025-daily-compaction-runs-on-the-prober-vm.md)). Resource `azurerm_role_assignment.prober_data_reader`. Plain read, no condition, no write, no delete. The job writes its daily files with the existing `<prefix>-data-writer` assignment, under `experiment=<experiment>/daily/v1/` only ([spec 0003](0003-prober-core-spec.md) section 9B). |
| Built-in `Reader` | subscription | Resource Graph, SKU and quota reads. |
| Custom `<prefix>-placement-score-reader` | subscription | `Microsoft.Compute/locations/placementScores/generate/action`, `Microsoft.Compute/locations/*/read` |
| Custom `<prefix>-subnet-joiner` (`Microsoft.Network/virtualNetworks/subnets/read` and `Microsoft.Network/virtualNetworks/subnets/join/action`) | the probe subnet of each region with a real scale set, only when it is in another resource group and is not `subnet_id` | Join the subnet from the scale sets (section 5.1.1). |

[ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md) section 5: RBAC does not change. `Storage Blob Data Reader` already grants read on the whole `code` container, which already covers the 2 new objects, the fixed-name `manifest.json` and the hash-named `sync.sh`. Unlike AWS, no explicit `Deny` is needed either: the prober's write role (`<prefix>-data-writer`) is scoped to the `data` container only, a separate container from `code`, so the prober's write role has no grant on `code` at all, scoped or not.

The role assignment scope of a container is `azurerm_storage_container.id`. The containers use `storage_account_id`, so "the resource will use the Resource Manager API, rather than the Data Plane API" ([azurerm_storage_container](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/storage_container)). On that path, the provider sets the ID to a Resource Manager storage container ID (`commonids.NewStorageContainerID`), and marks `resource_manager_id` as "deprecated in favour of `id`" ([provider source, v4.81.0](https://github.com/hashicorp/terraform-provider-azurerm/blob/v4.81.0/internal/services/storage/storage_container_resource.go)). The changelog adds "support for the `storage_account_id` property" to `azurerm_storage_container` in 4.9.0 ([azurerm changelog, v4.81.0](https://github.com/hashicorp/terraform-provider-azurerm/blob/v4.81.0/CHANGELOG.md)). The pin `~> 4.81` excludes the older versions.

Actions of `<prefix>-capacity-allocator`, each with the description from the [Compute permissions](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/compute) or the [Networking permissions](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/networking) page:

| Action | Description on the page | Why |
|---|---|---|
| `Microsoft.Compute/virtualMachineScaleSets/read` | Get the properties of a Virtual Machine Scale Set | read capacity |
| `Microsoft.Compute/virtualMachineScaleSets/write` | Creates a new Virtual Machine Scale Set or updates an existing one | set capacity, set `run_id` tag |
| `Microsoft.Compute/virtualMachineScaleSets/delete` | Deletes the Virtual Machine Scale Set | recovery from a broken scale set |
| `Microsoft.Compute/virtualMachineScaleSets/scale/action` | Verify if an existing Virtual Machine Scale Set can Scale In/Scale Out to specified instance count | scale check |
| `Microsoft.Compute/virtualMachineScaleSets/virtualMachines/read` | Retrieves the properties of a Virtual Machine in a VM Scale Set | list members |
| `Microsoft.Compute/virtualMachineScaleSets/virtualMachines/delete` | Delete a specific Virtual Machine in a VM Scale Set. | delete members |
| `Microsoft.Compute/capacityReservationGroups/read` | Get the properties of a capacity reservation group | find the group |
| `Microsoft.Compute/capacityReservationGroups/capacityReservations/read` | Get the properties of a capacity reservation | verify deletion |
| `Microsoft.Compute/capacityReservationGroups/capacityReservations/write` | Creates a new capacity reservation or updates an existing capacity reservation | the reservation probe |
| `Microsoft.Compute/capacityReservationGroups/capacityReservations/delete` | Deletes the capacity reservation | the reservation probe |
| `Microsoft.Compute/virtualMachines/read` | Get the properties of a virtual machine | poll members |
| `Microsoft.Compute/virtualMachines/instanceView/read` | Gets the detailed runtime status of the virtual machine and its resources | power and provisioning state |
| `Microsoft.Compute/virtualMachines/delete` | Deletes the virtual machine | tear down members |
| `Microsoft.Compute/disks/read` | Get the properties of a Disk | find orphans |
| `Microsoft.Compute/disks/delete` | Deletes the Disk | delete orphans |
| `Microsoft.Network/networkInterfaces/read` | Gets a network interface definition. | find orphans |
| `Microsoft.Network/networkInterfaces/delete` | Deletes a network interface | delete orphans |
| `Microsoft.Compute/virtualMachines/write` | Creates a new virtual machine or updates an existing virtual machine | scale-out creates standard VMs |
| `Microsoft.Network/networkInterfaces/write` | Creates a network interface or updates an existing network interface. | scale-out creates NICs |
| `Microsoft.Network/networkInterfaces/join/action` | Joins a Virtual Machine to a network interface. Not Alertable. | scale-out attaches the NICs |
| `Microsoft.Network/virtualNetworks/subnets/join/action` | Joins a virtual network. Not Alertable. | a scale set write carries its network profile |
| `Microsoft.Network/networkSecurityGroups/join/action` | Joins a network security group. Not Alertable. | kept from the earlier design; the NIC configuration now sets no network security group |
| `Microsoft.Resources/tags/write` | Updates the tags on a resource by replacing or merging existing tags with a new set of tags, or removing existing tags. ([Management and governance permissions](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/management-and-governance)) | tag the members of a scale-out (section 4.5) |

A Flexible scale set creates standard VMs, NICs, and disks. The orchestration modes page lists the "RBAC Permissions Required" for Flexible as "Compute Virtual Machine Scale Sets Write, Compute VM Write, Network" ([orchestration modes](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes)). The page does not list the network actions. The stack grants the NIC, subnet, and NSG actions above. Pilot P0 of spec 0002 confirms the set with a CPU-sized scale-out (open item).

The placement score action: `Microsoft.Compute/locations/placementScores/generate/action`, "Create a request for generating Placement Scores" ([Compute permissions](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/compute)). The Spot Placement Score how-to says: "You need to add the Role-Based Access Control (RBAC) role "Compute Recommendations Role"" ([Spot Placement Score](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)). The built-in roles reference for Compute lists no role with that name ([built-in roles, compute](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/compute)). The stack uses the custom role, as spec 0002 8.3 allows. If the named role exists in a tenant, the custom role is redundant but harmless (open item).

#### 5.1.1 Subnet in another resource group

The prober role is on `resource_group_name` only. A scale set scale-out creates NICs that join the subnet of its region (`subnet_id` or an entry of `probe_subnet_ids`). When a subnet is in another resource group, the prober role does not cover it, and the scale-out is likely to fail with `LinkedAuthorizationFailed`. This is not yet checked on a live account.

Rule:

1. The stack takes the regions that have a real scale set: the regions of `local.vmss_targets` (a non-simulated `az_vmss_flex` entry: the probe type decides). For each, it takes the subnet of that region from the region map (R2: `subnet_id` or an entry of `probe_subnet_ids`) and compares its resource group with `resource_group_name`, without regard to case. The stack takes the group from the 5th segment of the subnet ID.
2. For each such subnet whose group differs, **and** that is not `subnet_id` (compared without regard to case), the stack creates 1 `azurerm_role_assignment.prober_probe_subnet_joiner` for the prober identity, with `for_each` keyed by lower-case region. The scope is that one subnet. `subnet_id` is the control subnet: it never gets an assignment, in any case. A scale set in the control region with no `probe_subnet_ids` entry therefore joins `subnet_id` with no assignment of this stack; the network owner grants the join there, or the operator adds a `probe_subnet_ids` entry for `location`. A variable validation on `probe_subnet_ids` rejects a subnet ID (compared without regard to case) used by 2 regions, or equal to `subnet_id` for a region other than `location`, because 2 assignments at 1 scope fail with a conflict.
3. For a region with no real scale set, for a subnet in the same group, or when `var.grant_billable_provisioning` is `false`, the stack creates no such assignment.
4. The role is the custom role `<prefix>-subnet-joiner` (`azurerm_role_definition.subnet_joiner`, defined at the subscription like `placement_score_reader`), with these actions only: `Microsoft.Network/virtualNetworks/subnets/read` and `Microsoft.Network/virtualNetworks/subnets/join/action` ([subnet permissions](https://learn.microsoft.com/en-us/azure/virtual-network/virtual-network-manage-subnet#permissions)). The NIC create of a Flexible scale set also needs `Microsoft.Network/networkInterfaces/write` ([network interface permissions](https://learn.microsoft.com/en-us/azure/virtual-network/virtual-network-network-interface#permissions)); that right, the NIC join, and the NSG join are in the `<prefix>-capacity-allocator` role, at the resource group, because the NIC is created in `resource_group_name`. The role holds no subnet write or delete, which Network Contributor would give. The earlier built-in `Network Contributor` assignments are replaced: the role changes the `role_definition_id` of the assignment, so OpenTofu destroys and re-creates the assignment, and `prober_subnet_joiner[0]` (the control subnet) is destroyed and not re-created.

The deploy user must be able to create this assignment: `Microsoft.Authorization/roleAssignments/write` on the subnet (section 11.3).

### 5.2 Sweeper identity

| Role | Scope | Actions |
|---|---|---|
| Custom `<prefix>-cleanup` | `resource_group_name` | The 16 actions below. |
| Built-in `Reader` | subscription | Resource Graph. |
| Custom `<prefix>-data-writer`, with an ABAC condition | container `data` | Write the sweeper's own log objects (spec 0003 section 8.2). The sweeper has no row prefix of its own; this is its only write. The same custom role as the prober's writer (section 5.1), with the condition `blobs:path StringLike 'experiment=<experiment>/logs/*'` ([attributes](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-auth-abac-attributes)): a write elsewhere in the container, such as a row, fails with HTTP 403. No read, no delete. Changing the condition re-creates the assignment on the next apply. |

Actions of `<prefix>-cleanup`: `Microsoft.Compute/virtualMachines/read`, `Microsoft.Compute/virtualMachines/instanceView/read`, `Microsoft.Compute/virtualMachines/delete`, `Microsoft.Compute/virtualMachines/write`, `Microsoft.Compute/virtualMachineScaleSets/read`, `Microsoft.Compute/virtualMachineScaleSets/write`, `Microsoft.Compute/virtualMachineScaleSets/scale/action`, `Microsoft.Compute/virtualMachineScaleSets/virtualMachines/read`, `Microsoft.Compute/virtualMachineScaleSets/virtualMachines/delete`, `Microsoft.Compute/disks/read`, `Microsoft.Compute/disks/delete`, `Microsoft.Network/networkInterfaces/read`, `Microsoft.Network/networkInterfaces/delete`, `Microsoft.Compute/capacityReservationGroups/read`, `Microsoft.Compute/capacityReservationGroups/capacityReservations/read`, `Microsoft.Compute/capacityReservationGroups/capacityReservations/delete`. The descriptions are in the table of 5.1. The scale set `write` changes the capacity for the capacity-0 fallback of spec 0002, section 6. The `scale/action` is the check "Verify if an existing Virtual Machine Scale Set can Scale In/Scale Out to specified instance count" ([Compute permissions](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/compute)). The VM `write` is there because Microsoft lists "Compute VM Write" for Flexible scale set writes (P32). The minimum set of [spec 0007](0007-sweeper-spec.md), section 10, is a subset of this role.

Each role assignment sets `principal_type = "ServicePrincipal"` and `skip_service_principal_aad_check = true`. The latter is for "a newly provisioned `Service Principal`", to "skip the `Azure Active Directory` check which may fail due to replication lag" ([azurerm_role_assignment](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/role_assignment)).

### 5.3 Data readers

| Role | Scope | Principal |
|---|---|---|
| Built-in `Storage Blob Data Reader` | container `data` | The deployer (the identity that runs `tofu plan`, or a plain `tofu apply` with no saved plan), plus each ID in `data_reader_principal_ids`. |
| Built-in `Storage Blob Data Reader` | container `code` | The same principal set. Resource `azurerm_role_assignment.code_reader`. A reader can then download the deployed `manifest.json` to verify a release. |

An Owner of the subscription holds no blob data rights: "When you create an Azure Storage account, you aren't automatically assigned permissions to access data via Microsoft Entra ID. You must explicitly assign yourself an Azure role for Azure Storage." ([Assign a role for blob data access](https://learn.microsoft.com/en-us/azure/storage/blobs/assign-azure-role-data-access)). The stack now assigns `Storage Blob Data Reader` to the deployer with no configuration, and to each extra ID in `data_reader_principal_ids`, a `list(string)` variable with default `[]`. A group object ID is preferred over a user or a service principal object ID ([Best practices for Azure RBAC](https://learn.microsoft.com/en-us/azure/role-based-access-control/best-practices): "Assign roles to groups, not users"). `local.data_readers` is `toset([for id in concat([data.azurerm_client_config.current.object_id], var.data_reader_principal_ids) : lower(trimspace(id))])`, and `azurerm_role_assignment.data_reader` (scope `data`) and `azurerm_role_assignment.code_reader` (scope `code`) both use it as `for_each`; `trimspace`, `lower`, then `toset` remove the duplicate when the deployer's object ID is also in the list, in any letter case and with any padding, so no assignment is created twice (a duplicate assignment fails with a 409). The scopes are `azurerm_storage_container.data.id` and `azurerm_storage_container.code.id` only, not the account (least privilege). The `code` container holds no secret: it holds the manifest, the prober zip, the config zip (`config-<sha256>.zip`, the folder of the targets file), and the sync script. Unlike the prober and sweeper assignments of sections 5.1 and 5.2, `data_reader` and `code_reader` set neither `principal_type` nor `skip_service_principal_aad_check`, because the principal can be a user, a group, or a service principal, and the "newly provisioned Service Principal" exception does not apply to a user or a group.

`data.azurerm_client_config.current.object_id`, verified for an `az login` user: equal to the ID from `az ad signed-in-user show`, checked 2026-10-14. The service principal and the managed identity cases are not verified.

The read access of the deployer's own object ID lasts only until a later `apply` by another identity: `for_each` then removes the first deployer's assignment and creates one for the new caller. A reader who needs the role to last goes in `data_reader_principal_ids` instead, preferably a group.

**Upgrade note.** Before this change, spec 0006 section 4.2 told each reader to assign `Storage Blob Data Reader` "on the container or the account" by hand. If a hand-made assignment of that role already exists for the deployer's object ID, or for any ID in `data_reader_principal_ids`, at the `data` container scope, or at the `code` container scope (for `azurerm_role_assignment.code_reader`, added on 2026-10-16), the first `apply` of this change fails with `RoleAssignmentExists` (HTTP 409), because `azurerm_role_assignment.data_reader` or `azurerm_role_assignment.code_reader` tries to create the same role, principal, and scope again; `trimspace`, `lower`, and `toset` only remove a duplicate inside this config, not a duplicate against an assignment Azure already holds ([Assign Azure roles using ARM templates](https://learn.microsoft.com/en-us/azure/role-based-access-control/role-assignments-template): a deployment that makes a new assignment for the same principal, role, and scope "will fail with a Conflict: RoleAssignmentExists error"). Before the first `apply` after this upgrade, find a hand-made assignment at each container scope with:

```sh
az role assignment list --scope <data container resource ID> --role "Storage Blob Data Reader"
az role assignment list --scope <code container resource ID> --role "Storage Blob Data Reader"
```

Then either remove it (`az role assignment delete`) or bring it under OpenTofu with `tofu import 'azurerm_role_assignment.data_reader["<object id, trimmed and lower case>"]' <the assignment's resource ID>` (for the `code` container: `azurerm_role_assignment.code_reader[...]`). A hand-made assignment at the storage *account* scope does not conflict: it is a different scope from the container scope this resource uses.

**Do not list the prober identity.** `azurerm_role_assignment.prober_code_reader` (section 5.1) already gives the prober identity `Storage Blob Data Reader` on `code`. If `data_reader_principal_ids` holds the object ID of that identity, `azurerm_role_assignment.code_reader` tries to make the same role, principal, and scope a second time, and the apply fails with `RoleAssignmentExists` (HTTP 409). The identity is created by the same apply, so its object ID is not known before the apply, and the stack has no validation for this. The variable is for people and groups that read the store.

## 6. Outputs

| Output | Value |
|---|---|
| `store_url` | `az://<account>/data/` |
| `data_storage_account` | the data account name |
| `probe_resource_group` | `resource_group_name` |
| `control_resource_group` | `resource_group_name` |
| `function_app_name` | the sweeper Function App name |
| `prober_vm_id` | the prober VM resource ID |

## 7. Make targets

`tasks/Makefile.azure`, with `$(MISE_EXEC) tofu -chdir=terraform/azure`. The stack reads its values from `terraform/azure/terraform.tfvars`, and `az login` gives the credentials. No variable is exported. `plan/azure`, `apply/azure`, and `deploy/azure` first run the settings check of [spec 0014](0014-config-and-deploy-spec.md) section 6: with no `terraform/azure/terraform.tfvars`, they print 1 line and exit 2. Their `init` adds `-backend-config=backend.hcl` when `terraform/azure/backend.hcl` exists.

| Target | Does |
|---|---|
| `plan/azure` | `init`, then `plan -input=false -out=tfplan`. |
| `apply/azure` | `apply -input=false tfplan`: the saved plan only. It fails when there is no `tfplan`. |
| `destroy/azure` | `init`, then `destroy`. Same flag. The help text starts with "WARNING!!!". |
| `status/azure` | `terraform/azure/scripts/status.sh`: VM power state, Function App state, the `leaked resources: N` line (experiment-tagged VMs, scale set instances, disks, NICs and capacity reservations in the probe resource group; it must be 0 before the grant is switched off), newest 5 blobs in `data` (read with `--auth-mode login`). Every `az` call is scoped to the target subscription (`AZURE_SUBSCRIPTION_ID`, else `ARM_SUBSCRIPTION_ID`, else the subscription of the prober VM id); the script fails with no `az` call when there is none or it differs from the VM id. Needs the `az` CLI. |
| `verify/azure-sweeper` | `terraform/azure/scripts/verify-sweeper.sh`: fails unless the newest `code_sha` in the sweeper logs equals the `sweeper_code_sha` output. Same subscription scoping as `status/azure`; blob reads use `--auth-mode login`. It fails with exit 2 (unknown) when a blob row is not exactly `name<TAB>modified` (a tab in a blob name) or when a `lastModified` does not end in `+00:00` or `Z`. |
| `verify/azure` | Reads `data_storage_account`, `prober_vm_id`, `experiment`, and `sweeper_code_sha` with `tofu output -raw` ([spec 0014](0014-config-and-deploy-spec.md) section 4). Prints the newest row blob and the newest log blob under `experiment=<name>/`, the sha256 of the store manifest, the release that runs on the VM, and the state and start time of `ftg-prober`. Exits 1 unless the release equals the manifest hash and the service is active. Then it runs `verify/azure-sweeper`, which reads the same outputs and has no environment overrides ([spec 0014](0014-config-and-deploy-spec.md) section 6). |
| `lint/azure` | `fmt -check -recursive`, `init -backend=false`, `validate`. No credentials. |
| `test/azure` | `init -backend=false`, `tofu test`. No credentials. |

## 8. Tests

`terraform/azure/tests/targets_mapping.tftest.hcl` and `terraform/azure/tests/existing_network.tftest.hcl` run `tofu test` with `mock_provider` blocks for azurerm, azapi, archive, and tls, against `examples/targets.example.yaml`. The 3 data sources get ID-shaped mock values. A file-level `variables` block sets `resource_group_name` and `subnet_id`. Most runs set `grant_billable_provisioning = true`; 3 runs (2 in the first file, 1 in the second) instead prove the default (`false`) plans no provisioning resource and neither of the 2 billable-rights role assignments, regardless of the file, but still plans the unconditional `sweeper_cleanup`; and that `true` plans the capacity allocator, and no subnet joiner, with no active entry in the file at all (ADR 0015). The runs check: 1 scale set per active entry with the right names and modes, the OS disk and NIC delete options and resilient deletion in each azapi body, no scale set for an eligible size, 1 reservation group each for `eastus` and `eastus2`, no budget without an email, 2 notifications with one, no action group and no alert rule without an email, the action group and the alert rule settings with one, `FTG_PROBE_RG` = `resource_group_name`, the sweeper settings, the storage settings, the scale-out actions of the prober role, the 2 code zip names, the sweeper resources in `location` by default with the region in their names, the sweeper resources in the region of `sweeper_location` with that region in their names and in the hash of the storage account name and in `resource_group_name` while the data storage and the prober VM stay in `location`, the alert rule in the region of Application Insights, the validation error for an empty `sweeper_location`, the `Y1` plan with `always_on` false by default, the `B1` plan with `always_on` true, the remote build settings, the zip deploy, and no `WEBSITE_RUN_FROM_PACKAGE` with `B1`, and the validation error for another SKU. The second file checks: each resource in `resource_group_name`, the prober NIC and each scale set on `subnet_id`, no network security group on a scale set, the budget and role scopes on the group, no subnet role assignment for a subnet in `resource_group_name` (also with other letter case), no subnet joiner for the control subnet in any case (also with real scale sets in the control region), no such assignment for a subnet in another group with `grant_billable_provisioning` left at its default (ADR 0015), both group outputs, no `experiment` tag on any control resource and the tag on each scale set and reservation group, the validation errors for an `experiment` key in `tags`, for an empty, a quoted, or a period-ended `resource_group_name`, and for an empty, a network, or a bare-name `subnet_id`, the precondition error for a scale set entry in a region with no subnet, and reservation groups in each region without scale sets. The second file also checks `probe_subnet_ids`: with 2 regions (a fixture file, `terraform/azure/tests/fixtures/two_regions.yaml`) each scale set uses the subnet of its own region; a region with no entry fails the plan; with an empty map the control region falls back to `subnet_id`; the subnet joiner plans 1 assignment per probe subnet of a region with a real scale set in another resource group (with the fixture `terraform/azure/tests/fixtures/eastus2_real.yaml`, with only eastus2 real: exactly 1, on the eastus2 subnet; with every entry simulated: 0) with a role of only the 2 subnet actions, and none for a subnet in the stack's group; an entry for `location` replaces the `subnet_id` fallback for the scale sets and gets the joiner; with the default `grant_billable_provisioning`, a non-empty map plans no joiner of either kind; and a bad ID, or 2 keys that differ only in letter case, fails validation. A third file, `terraform/azure/tests/egress_proxy.tftest.hcl`, fixes the blob endpoint and the zip hashes with overrides and decodes the `custom_data` of the prober VM. Its runs check: with the defaults, no proxy or drop-in content, the fixed-name `manifest.json` blob (content and name) naming the hash-named `sync.sh` blob, and the hash-named `sync.sh` blob itself (content and name); with `egress_proxy_url` and `no_proxy`, the `apt:` block, `runcmd`'s own proxy export for the `sync.sh` fetch (ADR 0012; `control.tf`'s `local.proxy_export_cmd`) with the fixed and the extra `NO_PROXY` hosts, the drop-in for both `ftg-prober.service` and `ftg-prober-sync.service`, and no `/etc/environment`; that `ExecStart` runs `/opt/ftg/current` and cloud-init fetches `sync.sh` via `manifest.json`'s own `sync_blob` field, and that the env carries no `FTG_EXPERIMENT`; and the validation errors for a proxy URL with no scheme, a proxy URL with a path, a proxy URL with an IPv4 host, a proxy URL with an IPv6 host, and a `no_proxy` entry with a comma. A variable with no default stops `tofu test` before the plan when no value is set, thus the tests check the validation of empty and wrong values.

A fourth file, `terraform/azure/tests/code_zip_location.tftest.hcl`, does not mock the `archive` provider, so its `zip_builds_outside_dist` run builds the prober, config, and sweeper zips for real from `../../prober`, the targets folder, and `../../sweeper/azure`, and asserts that each `output_path` holds `.terraform/`, not `dist/` (section 3.1). `scripts/check-stale-build-artifact.sh`, run by `make test`, plants a stale `dist/prober.zip`, `dist/config.zip`, and `dist/sweeper.zip`, runs that test, and checks that the real build output under `.terraform/ftg-build/` is not the stale file for any of the 3.

A fifth file, `terraform/azure/tests/data_readers.tftest.hcl`, mocks the client config `object_id` as a placeholder string, not GUID-shaped text. Its runs check: with the default `data_reader_principal_ids`, only the deployer's object ID gets `Storage Blob Data Reader` on the `data` container; and with 2 extra IDs plus duplicates of the deployer (the same string, an upper-case copy, and a copy padded with spaces), the deployer and each extra ID get exactly 1 role assignment each, with the duplicates dropped; the same checks hold for `code_reader` on the `code` container; and `empty_entry_fails_validation` checks that a whitespace-only entry fails the variable validation.

`scripts/check-sweeper-redeploy.sh`, run by `make test` (`test/sweeper-redeploy`), builds the real zip in a temporary copy of the repo and reads the planned `zip_deploy_file` from `tofu test -verbose`. It also checks that the hash in the zip name equals the hash that `code_sha.py` computes over the same folder. `terraform/tests/verify.test.sh` (`test/verify`) runs `scripts/verify.sh` against a fake `aws`, a fake `az`, and a fake `tofu` (the fakes follow the documented CLI formats; one recorded real run is still open). `terraform/tests/verify-sweeper.test.sh` (`test/verify-sweeper`) runs `verify-sweeper.sh` over a fake `tofu` and a fake `az`: it passes for a matching hash and fails for an old hash, for a log with no `code_sha`, and for no log. A change to a sweeper source file must change it, and a change to a file that the zip leaves out (`tests/`, `__pycache__/`) must not (section 3.5, redeploy rule).

There is no sixth, guard-only test file any more ([ADR 0015](../decisions/0015-two-independent-controls-replace-the-terraform-allocation-guard.md)): `var.grant_billable_provisioning` needs no targets-file fixture to test, only a `true`/`false` value, so its tests live in the first and second files above (the `no_grant_...`, `grant_true_...`, and `reservation_groups_need_the_grant_...` runs). The fixtures of `prober/tests/fixtures/` (`provisioning_guard.yaml` and friends) still exist and still load in `prober/tests/test_config.py`, but they test only the prober's own, unchanged per-job consent guard now; nothing in `terraform/azure/tests/` reads them.
`terraform/tests/sync.test.sh` (`make test/sync`) tests `sync.sh` itself, shared with the AWS stack, including the Azure-specific case: a `BlobNotFound` manifest poll is routine, not a failure. [spec 0003](0003-prober-core-spec.md) section 6A has the full list of cases it covers.

## 9. Decisions

| # | Decision | Reason |
|---|---|---|
| A1 | The storage name suffix is a hash of the subscription ID, not a random string. | Stable names with no state. |
| A2 | The code goes to `code` as 2 zips, `prober-<sha256>.zip` and `config-<sha256>.zip`. The config zip holds the folder of `targets_file`, and the unit reads `/opt/ftg/current/config/<file name of targets_file>` ([ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md); superseded: the first layout read `/opt/ftg/config/`). | `archive_file` zips 1 folder. Same layout as the AWS stack ([spec 0004](0004-aws-stack-spec.md)). The VM reads the file that OpenTofu maps to resources. The default gives `/opt/ftg/current/config/targets.yaml`. |
| A3 | The prober identity also gets `Storage Blob Data Reader` on `code`. | It downloads the package. The plan listed only `data`. |
| A4 | A code or config change replaces the prober VM. | `custom_data` forces a new VM. The VM holds no data. Simpler than an update agent. Superseded by [ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md): `custom_data` no longer names the prober blob, the config blob, or any `sync.sh` blob at all, so a code, a config, or a `sync.sh` change no longer forces a replacement; a VM-side timer pulls and restarts instead (section 3.4). |
| A5 | azapi sets the OS disk and NIC delete options and the resiliency policy. | azurerm has no argument for them. |
| A6 | The azapi body names the NIC configuration `primary` and sets only its `deleteOption`. | azapi merges list items by `name`, so the rest of the NIC configuration stays as azurerm wrote it. The sweeper also deletes orphan NICs (spec 0002, section 6). |
| A7 | `single_placement_group` is `false` for every scale set. | Spec 0002 D6 probes regional headroom only ([ADR 0021](../decisions/0021-provisioning-probes-measure-available-capacity-not-single-cluster-capacity.md)). |
| A8 | `fault_domain_count` is a variable, default 1. | P0 can need another value. |
| A9 | Capacity reservation groups exist for every region in `provisioning_probes`. | A group without reservations has no VM-rate charge. |
| A10 | No budget without an email. | The resource needs a contact. |
| A11 | Outbound internet access from the given subnet is a deploy prerequisite. The stack creates no NAT gateway. | No public IP. The network and its outbound path belong to the network owner, who sets them up before the deploy. |
| A12 | The admin SSH key is generated and kept in the local state. | Azure needs a key. Nobody logs in. |
| A13 | The service runs with `DynamicUser=yes`. | Least privilege without a user to manage. |
| A14 | Resilient create stays off. | It retries only timeouts after allocation, which add billed time. |
| A15 | The sweeper uses a remote build, not run-from-package. | The zip has no built dependencies. |
| A16 | `test/azure` uses mock providers. | The plan rule: tests run with no credentials and no network. |
| A17 | The lock file pins provider hashes for darwin and linux, amd64 and arm64. | A fresh clone on either platform runs `init` without a change to the lock file. |
| A18 | The quota family names in the README are marked "not verified". | No Microsoft quota page lists them. The README gives the `az vm list-skus` command, whose `family` field is "The Family of this particular SKU" ([Resource SKUs - List](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list)). |
| A19 | The prober role holds VM write, NIC write and join, and NSG join on `resource_group_name`. | A Flexible scale-out creates standard VMs and NICs with the rights of the caller. |
| A20 | The Function App ignores changes to `WEBSITE_RUN_FROM_PACKAGE`. | The zip deploy sets it to a blob URL on Linux Consumption. Else each plan removes it. |
| A21 | The stack does not set member tags. The prober tags the VMs, disks, and NICs of a scale-out, with `Microsoft.Resources/tags/write`. | The scale set VM profile has no tags property. Resources do not inherit tags. |
| A22 | The azurerm provider block sets `features.resource_group.prevent_deletion_if_contains_resources = false`. | Kept from the earlier design. The stack creates no resource group now, so the setting has no effect on a fresh deploy. On an upgrade over the 2-group state, it lets the apply delete the old groups (section 1, Upgrade). The destroy steps still require 0 probe VMs first. |
| A23 | 1 alert rule covers both signals of spec 0007, section 9: no health line, and failed requests. | 1 rule, 1 kind of email, 1 cost item. The query result shows `runs` and `failed`, so the reader sees which signal fired. |
| A24 | The query returns a row when the sweeper has a problem. The rule fires on a row count greater than 0. | `summarize` with no group gives a row also on empty input (section 3.6, fact 3). The rule does not depend on how the alert service treats an empty result. |
| A25 | The alert rule has no managed identity. | The query reads only Application Insights, and the deployer can read it. A managed identity needs 1 more role assignment. |
| A26 | Query validation stays on (`skip_query_validation` is not set). | A typing error in the query then fails the apply, not the alert. |
| A27 | The alert uses `alert_email`, the same variable as the budget. | 1 address for both cost alerts. |
| A28 | The sweeper plan SKU is the variable `sweeper_plan_sku`, default `Y1`. The validation allows `Y1`, `B1`, `B2`, `B3`, and `S1`. | The stack must deploy on a subscription with no `Y1` quota. Basic and Standard host Functions (section 3.5). The stack does not need Premium or Isolated for 1 timer. |
| A29 | `always_on` is `sweeper_plan_sku != "Y1"`. | A dedicated plan idles the runtime, and the timer stops. The azurerm doc says to turn it off on a consumption plan. |
| A30 | The sweeper region is the variable `sweeper_location`, default `null`. A null value means `location`. | The first deploy on a new subscription had no App Service quota in the control region. Only the sweeper plan needs that quota, so only the sweeper resources move. (Whether App Service quota is per region is not verified.) The prober, the data, and the probe resources stay with the targets. |
| A31 | The stack deploys into an existing resource group and subnet, `resource_group_name` and `subnet_id`, with no default. It creates no resource group, virtual network, subnet, or network security group. The provisioning probes use the same group and subnet. | The network and the group belong to their owner. |
| A32 | Only the probe resources carry the `experiment` tag. `tags` must not hold the key. | The control resources share the group with the probe resources. The sweeper deletes by the tag, so the tag must not reach a control resource. |
| A44 | `manifest.json` is 1 more fixed-name blob in `code`; `sync.sh` is a 3rd hash-named blob, like the 2 zips, not a 2nd fixed name. | A fixed name updates in place for `manifest.json` (A2's hashed names cannot). `sync.sh` stays hash-verified the same way as the 2 zips; a fixed name for it was rejected because `custom_data` would then still need to name a key somewhere, which a `sync.sh`-only change would then also replace the VM for. The manifest's own `sync_blob` field, read by `sync.sh` on every tick, is what lets it self-update with no fixed name of its own at all. |
| A45 | `manifest.json`'s own `source_content` forces a replace on every apply that changes the code or the config, not only the 1 switchover apply (A4's superseding note), unlike AWS's in-place `manifest.json`. | `azurerm_storage_blob` has no in-place content update; a fixed name cannot have 2 blobs at once, so `create_before_destroy` cannot help the way it helps the 2 zip blobs. `sync.sh` treats the resulting gap as routine ([ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md) section 3). |
| A46 | RBAC gets no new role or assignment for ADR 0012. | `Storage Blob Data Reader` on `code` already covers any blob in that container; the prober's write role is scoped to the separate `data` container, so no explicit Deny is needed the way AWS needs 1 (section 5.1). |
| A33 | A scale set entry in a region with no subnet (no entry of `probe_subnet_ids`, and not `location`) fails the plan. | A scale set needs a subnet in its own region. A failed plan is clearer than a probe that fails at each run. |
| A33a | A probe subnet in another region is an isolated one, with no outbound path (section 4.3). The stack creates none. | The prober VM makes every call from `subnet_id`, and the probe VMs are only created, counted, and deleted. Only provisioning-probe VMs land there. |
| A34 | `preflight/azure` is a separate Python project in `preflight/`. It calls the `az` CLI. | The stack already needs the `az` CLI (`status/azure`). The checks must not change the prober zip: a new zip still uploads under a new blob name and rewrites `manifest.json` (A33, A4's superseding note), even though it no longer by itself replaces the VM. |
| A35 | The permission check reads the role assignments of the caller in the subscription and the role definitions, matches the actions with their wildcards, and counts an assignment only at its scope and below. | It needs only read access. It finds Contributor without the rights to write role assignments, and a group-level role without the join right on a subnet in another group. It does not apply deny assignments or conditions. |
| A36 | A restricted Spot size or provisioning probe size is a warning. An unregistered provider of the azurerm set is a warning. | The deploy works. The rows record `restricted`. azurerm registers its set at the start of a deploy. |
| A37 | The outbound check reads only the subnet and is never a `FAIL`. | Outbound access belongs to the network owner (section 3.2). A firewall route can reach the internet, and the check cannot prove that it does. |
| A38 | The egress proxy and its bypass list are 2 optional variables. Both are empty by default, and the rendered cloud-init is then unchanged. | Some networks allow outbound traffic only through a proxy. A default of empty keeps the deploy the same for other networks. |
| A39 | `NO_PROXY` always holds the IMDS address, the wire server address, `localhost`, `127.0.0.1`, and `login.microsoftonline.com`. | The managed identity token call goes to IMDS, and the VM agent uses the wire server. Neither is reachable through a proxy. Sign-in goes direct. ARM goes through the proxy. |
| A40 | The proxy is a host name in a variable. The stack pins no proxy IP and accepts no credentials in the URL. | An IP changes. The URL goes into `custom_data`, thus a password in it would be stored in the VM definition. |
| A42 | The settings go into the apt config, `runcmd`'s own export before it fetches `sync.sh` ([ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md) superseded `install.sh`; section 3.4), and 2 systemd drop-ins. The stack does not write `/etc/environment`. | The settings reach only the processes that need them. `/etc/environment` would also reach every other process of the VM. |
| A43 | A subnet in another resource group gets the custom role `<prefix>-subnet-joiner` (subnet read and join) for the prober identity, at the scope of that one subnet, only for the probe subnet of a region with a real scale set, never for `subnet_id`. | The prober role covers only `resource_group_name`. Network Contributor, chosen first, "lets you manage networks" ([built-in roles, networking](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/networking#network-contributor)): it can change or delete a subnet, and it was assigned on the shared control subnet. The join needs only the actions in section 5.1.1. |

## 10. Open items

| Item | State |
|---|---|
| `single_placement_group = true` on a Flexible scale set | Closed. Azure rejects it (section 4.5); the `single` mode is dropped ([ADR 0021](../decisions/0021-provisioning-probes-measure-available-capacity-not-single-cluster-capacity.md)). |
| `fault_domain_count` | Open. P0 decides. |
| The exact quota family names | Open. Confirm with `az vm list-skus` before the quota requests. |
| Query validation of the sweeper-stopped rule on a new, empty Application Insights resource | Open. Confirm on the first deploy that the create succeeds. If Azure rejects the query because a table has no data, set `skip_query_validation = true` (A26). |
| False sweeper-stopped alerts from late telemetry (section 3.6, fact 7) | Open. Watch the first weeks. If a false alert comes, set `window_duration` to `PT15M` and both `ago()` values to `15m`. |
| Tags on the members of a Flexible scale-out | Open. P0 of spec 0002 reads the member tags of a CPU-sized scale-out. If members carry no `experiment` tag, the prober tags them (section 4.5). A member that the prober did not tag is not visible to the sweeper query of spec 0007. |
| The sweeper scale to 0 on a Flexible scale set | Open. The sweeper role has VM write but no network rights. Confirm on the first deploy that the capacity-0 `PATCH` succeeds (spec 0007, open item 4). |
| The exact network actions for a Flexible scale-out | Open. P0 of spec 0002 runs a CPU-sized scale-out with the prober identity. Add an action if Azure returns `AuthorizationFailed` or `LinkedAuthorizationFailed`. |
| "Compute Recommendations Role" | Open. The how-to names it; the built-in roles reference does not list it. If it exists, it can replace the custom role. |
| The prober name prefix in an existing group | The prober takes the prefix from `FTG_PROBE_RG` (spec 0010, Z8). With `name_prefix` other than `ftg`, or a group name that ends in `-probe`, it reads the wrong scale set and reservation group names. |
| The name of the App Service quota row of a plan SKU | Open. The check expects the `Microsoft.Quota` row name for `Microsoft.Web` to equal the SKU name, for example `Y1` (author test, 2026-10-12, in the build of `sweeper_location`). Confirm on the first live run. |

## 11. Prerequisites and preflight

The README section "Before you deploy" gives the same list for the operator, with the cost.

### 11.1 Prerequisites

| Item | Requirement | If it is missing |
|---|---|---|
| Login | The `az` CLI, signed in. `subscription_id` in `terraform/azure/terraform.tfvars` (or `ARM_SUBSCRIPTION_ID`) selects the subscription. | The deploy fails. |
| Network inputs | `resource_group_name` and `subnet_id` are set in `terraform/azure/terraform.tfvars` (section 2). | The plan stops: the variables are required and have no default. |
| Resource group | The group of `resource_group_name` exists in the subscription. | The plan fails on the data source of the group. |
| Subnet | The subnet of `subnet_id` exists, in the subscription of the deploy and in `location` (section 3.2). Microsoft: "You can assign a NIC only to a virtual network in the same subscription and location as the NIC" ([network interfaces](https://learn.microsoft.com/en-us/azure/virtual-network/virtual-network-network-interface)). | The NIC create fails. |
| Outbound access | Outbound internet access from the subnet (section 3.2). The network owner sets it up before the deploy. | The deploy succeeds, but the first boot cannot install the prober. |
| Probe subnets | Only for a real scale-set probe in a region other than `location`: 1 subnet in that region, listed in `probe_subnet_ids` in `terraform/azure/terraform.tfvars`. The user brings it. Its shape is in section 4.3: an isolated virtual network with 1 subnet, no peering, no route table, no NAT gateway, no public IP, no default outbound access, and a network security group that denies all inbound traffic. Size the subnet for the largest `n` of the entries in its region. | The scale-out of that region fails with an Azure NIC error. A signal probe needs no probe subnet. |
| Rights | The actions of 11.3, each at its scope. The built-in roles that give them: Owner, or User Access Administrator plus Contributor, on the subscription. The stack creates 5 custom roles (the `azurerm_role_definition` resources in `terraform/azure/rbac.tf`) and 8 fixed role assignments (`prober_data_writer`, `prober_data_reader`, `prober_code_reader`, `prober_subscription_reader`, `prober_placement_score_reader`, `sweeper_subscription_reader`, `sweeper_cleanup`, `sweeper_data_writer`), 2 more for the deployer (`data_reader`, `code_reader`) and 2 more for each ID in `data_reader_principal_ids` (section 5.3). 2 more role assignments plan only when `var.grant_billable_provisioning` is `true`: `prober_capacity_allocator` unconditionally once it is, and `prober_probe_subnet_joiner` (1 per probe subnet of a region with a real scale set) only when that subnet is also in another resource group and is not `subnet_id` (section 4.1, R7; section 5.1.1). | The deploy fails at the first role definition or role assignment, or at the NIC on the subnet. |
| Resource providers | Registered, or registered by azurerm at the start of the deploy. azurerm 4.x registers its `legacy` set by default ([azurerm provider](https://raw.githubusercontent.com/hashicorp/terraform-provider-azurerm/v4.81.0/website/docs/index.html.markdown)). That set holds `Microsoft.Compute`, `Microsoft.Network`, `Microsoft.Storage`, `Microsoft.Web`, `microsoft.insights`, `Microsoft.OperationalInsights`, `Microsoft.ManagedIdentity`, `Microsoft.Authorization`, and `Microsoft.AlertsManagement` ([required.go, v4.81.0](https://github.com/hashicorp/terraform-provider-azurerm/blob/v4.81.0/internal/resourceproviders/required.go)). It does not hold `Microsoft.Consumption`, which the budget needs when `alert_email` is set, or `Microsoft.Quota`, which the sweeper plan check reads. | The registration needs the register right, or the budget create fails. |
| Prober VM | `prober_vm_size` (default `Standard_B2ats_v2`, family `standardBasv2Family`, 2 vCPUs) has no `Location` restriction for the subscription in `location`, and its family and the regional total have free vCPUs for it. A fallback is `Standard_D2as_v4` (family `standardDASv4Family`). | The VM create fails with a quota error or `SkuNotAvailable`. On 2026-10-11, a new subscription had 0 `Basv2` cores, and `Standard_B2s` gave `SkuNotAvailable` (author test). |
| Sweeper plan | The App Service quota of `sweeper_plan_sku` in the sweeper region (`sweeper_location`, or `location`) is 1 or more. The fallbacks are `sweeper_location` (section 3.5, "Sweeper region") and a dedicated `sweeper_plan_sku`. | The plan create fails. |
| Spot sizes | Each size of `azure.spot_sizes` has no `Location` restriction in each region of `azure.regions`. | No deploy gap. The `az_spot_score` rows show `RestrictedSkuNotAvailable` for that pair (spec 0009, section 2.4). |
| Provisioning probe sizes | Each entry of `azure.provisioning_probes`, simulated or not, is in the SKU list of its region (offered). A non-simulated entry also has no `Location` restriction there. | An entry that is not offered: the entry is not a probe that can run, so the preflight gives a `FAIL`; a real entry can also break the apply. A restricted entry: no deploy gap; its rows show `restricted` (spec 0010). |
| Spot Placement Score | The prober identity can run `Microsoft.Compute/locations/placementScores/generate/action`. The stack gives it with a custom role (section 5.1). The how-to names the "Compute Recommendations Role" instead (section 10). | No deploy gap. The rows show the authorization error. |

### 11.2 `make preflight/azure`

The inputs come from `terraform/azure/terraform.tfvars` through `tofu console`. The target runs `tofu -chdir=terraform/azure init -input=false`, which reads the backend (spec 0004, section 12.2), then `uv run --locked python -m ftg_preflight azure` in `preflight/`.

Inputs:

1. The variables `location`, `prober_vm_size`, `sweeper_plan_sku`, `alert_email`, `targets_file`, `resource_group_name`, `subnet_id`, and `sweeper_location`, from `tofu console` with `jsonencode(var.<name>)`. A missing `sweeper_location` variable counts as `null`. A required variable with no value prints `(known after apply)` and counts as not set (spec 0004, section 12.2).
2. `azure.regions` (every job's `regions`, ADR 0010 section 1; they must all agree, spec 0003 section 3), `azure.spot_sizes` (the `az_spot_score` or `az_spot` job's module setting), and `azure.provisioning_probes`, from the targets file of `targets_file`.
3. `ARM_SUBSCRIPTION_ID`. When it is set, each `az` call names that subscription.
4. `tofu state list`. When the state holds `azurerm_linux_virtual_machine.prober`, the vCPU check passes, because the usage already counts the prober VM.

The rules of spec 0004, section 12.2 apply: read-only calls, the 3 levels, the exit codes 0, 1, and 2, and the output format. A message names no subscription ID, and no `subnet_id`, because it holds the subscription ID. The login check prints the kind of the principal only.

Checks, in order:

| Check | Calls | Result |
|---|---|---|
| `azure/network-inputs` | none | `FAIL` when `resource_group_name` or `subnet_id` is not set. Then the group, subnet, and outbound checks do not run. |
| `azure/login` | `az account show` | `FAIL` on an error, or when the state is not `Enabled`. The other checks do not run. |
| `azure/providers` | `az provider list` | `WARN` for a namespace of the azurerm set that is not `Registered`. `FAIL` when `alert_email` is set and `Microsoft.Consumption` is not `Registered`. `WARN` when `Microsoft.Quota` is not `Registered`. Each fix is `az provider register --namespace <name>`. |
| `azure/permissions` | `az role assignment list --assignee <caller> --all --include-groups`, `az role definition list --scope /subscriptions/<id>` | An assignment counts for an action when its scope is the scope of the action in 11.3, or a parent of it. `FAIL` when no counted role allows role definition write or role assignment write on the subscription, or role assignment write on the subnet when it is in another resource group (the group names are compared without regard to case). `FAIL` when another action of 11.3 is not allowed at its scope. `WARN` when an assignment has a condition, when an assignment names a role that the definition list does not hold, or when the reads fail. |
| `azure/resource-group` | `az group show --name <resource_group_name>` | `FAIL` on `ResourceGroupNotFound`. `WARN` on another error. |
| `azure/subnet` | `az network vnet show --ids <virtual network of subnet_id>` | `FAIL` when the subscription in `subnet_id` is not the subscription of the deploy (no call), on `ResourceNotFound`, when the network has no subnet of that name, or when the region of the network is not `location`. `WARN` on another error. |
| `azure/outbound` | none; the subnet from `azure/subnet` | `PASS` when the subnet has a NAT gateway. Else `WARN`: a route table (the check does not read the routes), `defaultOutboundAccess` set to `false`, or neither. Never `FAIL`, because the check cannot prove a path to the internet. |
| `azure/prober-sku` | `az rest` GET `/subscriptions/<id>/providers/Microsoft.Compute/skus?api-version=2021-07-01&$filter=location eq '<region>'`, with each `nextLink` | `FAIL` when the size is missing, or has a `Location` restriction for `location`. A `Zone` restriction does not block, because the prober VM has no zone. |
| `azure/prober-quota` | `az vm list-usage --location <location>` | `FAIL` when the family row or the `cores` row has fewer free vCPUs than the size needs. Free is `limit` minus `currentValue`. The check evaluates only these 2 rows. `WARN` with no quota row evaluated when the size has a `GPUs` capability above 0: the check evaluates no GPU quota. |
| `azure/sweeper-plan` | `az rest` GET `/subscriptions/<id>/providers/Microsoft.Web/locations/<region>/providers/Microsoft.Quota/quotas?api-version=2023-02-01`, with each `nextLink` | `FAIL` when the limit of the row of `sweeper_plan_sku` is under 1. `WARN` when the call fails or no row matches. |
| `azure/spot-sizes`, `azure/allocation-probes` | The same SKU call for each region of `azure.regions` and of `azure.provisioning_probes`, 1 call for each region | `WARN` for each restricted size (a `Location` restriction): it is offered, and its rows will show `restricted`. A simulated entry (its own `simulate: true`) gets no restricted warning. `FAIL`, 1 line for each entry, when the size of an `azure.provisioning_probes` entry is absent from the SKU list of its region (not offered). Rule: the experiment file lists only probes that can run, so a size that a region does not offer to the subscription does not belong in it, also in a simulated entry (decided on 2026-10-16). The message says whether the entry is real or simulated. A Region name is compared in lower case with no spaces. The list is the REST list above, which holds the sizes that the subscription cannot use. `az vm list-skus` without `--all` leaves out a size that has a `Location` restriction (2026-10-16 test: `Standard_NC24ads_A100_v4` in `eastus` was missing from its output and present, with `NotAvailableForSubscription`, in the REST list), so its output would show a restricted size as not offered. The check reads no quota of a GPU family. |
| `azure/api` | `az provider list`, the SKU list, and `az vm list-usage` | `FAIL` on a read error that a check does not handle. The checks after it do not run. |

Facts behind the checks:

- The Quota API lists "current quota limits of all resources for the specified scope", at `GET https://management.azure.com/{scope}/providers/Microsoft.Quota/quotas?api-version=2023-02-01`. Each item has `properties.limit.value` and `properties.name.value`. `nextLink` is null "when there are no more pages" ([Quota - List](https://learn.microsoft.com/en-us/rest/api/quota/quota/list?view=rest-quota-2023-02-01)).
- A SKU restriction has a `type` of `Location` or `Zone` and a `reasonCode` such as `NotAvailableForSubscription`. The `$filter` of the SKU list takes a location: "Only **location** filter is supported currently" ([Resource SKUs - List](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list)). In a test, `az vm list-skus` took more than 120 seconds for 1 region (author test, 2026-10-12). Thus the check uses the REST call with the filter.
- `--include-groups` adds "extra assignments to the groups of which the user is a member (transitively)". By default the command shows only the assignments at the subscription scope; `--all` shows "all assignments under the current subscription", also on groups and resources ([az role assignment list](https://learn.microsoft.com/en-us/cli/azure/role/assignment?view=azure-cli-latest#az-role-assignment-list)). The REST list for a subscription with a principal filter returns "all role assignments at, above or below the scope" ([Role Assignments - List For Subscription](https://learn.microsoft.com/en-us/rest/api/authorization/role-assignments/list-for-subscription)). That `--all` also returns the management group assignments is not verified on a live run.
- The action match is case-insensitive, `*` matches any text, and the `notActions` of a permission block remove actions from the `actions` of the same block. The check does not apply deny assignments or conditions.

### 11.3 Deploy actions

The stack creates no resource group and no network, so the list has no group, virtual network, or network security group write.

| Scope | Actions |
|---|---|
| The subscription | `Microsoft.Authorization/roleDefinitions/write`, `Microsoft.Authorization/roleAssignments/write`. The stack creates 2 custom roles and 3 role assignments on the subscription (section 5). Subscription scope also covers the subnet and the group. |
| `subnet_id` and each `probe_subnet_ids` subnet | `Microsoft.Network/virtualNetworks/subnets/join/action`. "Joins a virtual network" ([networking permissions](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/networking)). The prober VM NIC and the scale sets join the subnet. The subnet can be in another resource group of the subscription, for example a network group. |
| each probe subnet of a region with a real scale set, only when it is in another resource group and is not `subnet_id` | `Microsoft.Authorization/roleAssignments/write`. The stack assigns `<prefix>-subnet-joiner` to the prober identity at the subnet (section 5.1.1). |
| `resource_group_name` | `Microsoft.Compute/virtualMachines/write`, `Microsoft.Compute/virtualMachineScaleSets/write`, `Microsoft.Compute/capacityReservationGroups/write`, `Microsoft.Network/networkInterfaces/write`, `Microsoft.Storage/storageAccounts/write`, `Microsoft.Storage/storageAccounts/listKeys/action`, `Microsoft.Web/serverfarms/write`, `Microsoft.Web/sites/write`, `Microsoft.OperationalInsights/workspaces/write`, and `Microsoft.Insights/components/write`. When `alert_email` is set, also `Microsoft.Consumption/budgets/write`, `Microsoft.Insights/actionGroups/write`, and `Microsoft.Insights/scheduledQueryRules/write`. |

When `resource_group_name` is not set, the check uses the subscription scope for the group actions. When `subnet_id` is not set, it uses the subscription scope for the join. The list holds the main create actions, not each action of the providers. Implementer's choice: more actions.

### 11.4 Tests

`make preflight/test` runs pytest in `preflight/`, with no network and no credentials. A fake `az` function replays the recorded output in `preflight/tests/fixtures/azure.json`. The tests cover each `PASS`, `WARN`, and `FAIL` path of the checks table, the wildcard and `notActions` match, an inherited group assignment, the scope match of an assignment, a Contributor on the resource group with no join right on a subnet in another group, the role assignment write on a subnet in another group (also with other letter case for the same group), an Owner of the group only, the `nextLink` loop of the SKU list and of the quota list, the Azure error code of an `az` error, `ARM_SUBSCRIPTION_ID`, a deployed prober VM, a skipped simulated entry, and a probe entry that its region does not offer (real and simulated, one `FAIL` line each; recorded `az vm list-skus` output).

Open: the first run against a real subscription.

## 12. Sources

- <https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs>
- <https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/orchestrated_virtual_machine_scale_set>
- <https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/capacity_reservation_group>
- <https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/linux_virtual_machine>
- <https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/linux_function_app>
- <https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/service_plan>
- <https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/function_app_flex_consumption>
- <https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/storage_account>
- <https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/storage_container>
- <https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/subnet>
- <https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/role_assignment>
- <https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/consumption_budget_resource_group>
- <https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/monitor_scheduled_query_rules_alert_v2>
- <https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/monitor_action_group>
- <https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-create-log-alert-rule>
- <https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-types>
- <https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/action-groups>
- <https://learn.microsoft.com/en-us/azure/azure-monitor/app/data-model>
- <https://learn.microsoft.com/en-us/azure/azure-functions/configure-monitoring>
- <https://learn.microsoft.com/en-us/azure/azure-functions/analyze-telemetry-data>
- <https://learn.microsoft.com/en-us/kusto/query/summarize-operator>
- <https://learn.microsoft.com/en-us/kusto/query/datatypes-string-operators>
- <https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/privileged>
- <https://registry.terraform.io/providers/Azure/azapi/latest/docs/resources/update_resource>
- <https://github.com/Azure/terraform-provider-azapi/blob/main/CHANGELOG.md>
- <https://github.com/Azure/terraform-provider-azapi/blob/main/internal/services/azapi_update_resource.go>
- <https://github.com/hashicorp/terraform-provider-azurerm/blob/v4.81.0/internal/services/storage/storage_container_resource.go>
- <https://github.com/hashicorp/terraform-provider-azurerm/blob/v4.81.0/CHANGELOG.md>
- <https://registry.terraform.io/providers/hashicorp/archive/latest/docs/data-sources/file>
- <https://learn.microsoft.com/en-us/azure/templates/microsoft.compute/virtualmachinescalesets>
- <https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-create>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/delete>
- <https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/resilient-vm-create-delete>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-overview>
- <https://learn.microsoft.com/en-us/azure/virtual-network/ip-services/default-outbound-access>
- <https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/how-to-use-vm-token>
- <https://learn.microsoft.com/en-us/rest/api/storageservices/authorize-with-azure-active-directory>
- <https://learn.microsoft.com/en-us/azure/azure-functions/functions-deployment-technologies>
- <https://learn.microsoft.com/en-us/azure/azure-functions/dedicated-plan>
- <https://learn.microsoft.com/en-us/azure/azure-functions/functions-how-to-use-azure-function-app-settings>
- <https://learn.microsoft.com/en-us/azure/app-service/manage-scale-up>
- <https://learn.microsoft.com/en-us/azure/app-service/overview-hosting-plans>
- <https://azure.microsoft.com/en-us/pricing/details/functions/>
- <https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/compute>
- <https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/networking>
- <https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/storage>
- <https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list>
- <https://learn.microsoft.com/en-us/rest/api/compute/capacity-reservations/create-or-update>
- <https://learn.microsoft.com/en-us/azure/virtual-network/network-security-groups-overview>
- <https://learn.microsoft.com/en-us/azure/virtual-network/virtual-networks-faq>
- <https://learn.microsoft.com/en-us/azure/azure-functions/python-build-options>
- <https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score>
- <https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/compute>
- <https://ubuntu.com/azure/docs/en/latest/azure-how-to/instances/find-ubuntu-images/>
- <https://packages.ubuntu.com/noble/python3>
- <https://raw.githubusercontent.com/hashicorp/terraform-provider-azurerm/v4.81.0/website/docs/index.html.markdown>
- <https://github.com/hashicorp/terraform-provider-azurerm/blob/v4.81.0/internal/resourceproviders/required.go>
- <https://learn.microsoft.com/en-us/rest/api/quota/quota/list?view=rest-quota-2023-02-01>
- <https://github.com/hashicorp/terraform-provider-azurerm/blob/main/website/docs/r/storage_blob.html.markdown>
- <https://manpages.ubuntu.com/manpages/noble/man5/systemd.exec.5.html>
- <https://learn.microsoft.com/en-us/cli/azure/role/assignment?view=azure-cli-latest#az-role-assignment-list>
- <https://learn.microsoft.com/en-us/cli/azure/vm?view=azure-cli-latest#az-vm-list-skus>

Sources of single facts in the text:

- [azurerm provider has no data source for SKU capabilities](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs)
- [Entry `primary` merges into the existing NIC config and keeps its IP config and NSG](https://github.com/Azure/terraform-provider-azapi/blob/main/internal/services/utils/json.go)
- [`~> 4.81` excludes older versions](https://developer.hashicorp.com/terraform/language/expressions/version-constraints)
- [`Standard_D2as_v4` is a size of the Dasv4 series](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/dasv4-series)
- [`coalesce` returns the first argument that is not null or an empty string](https://developer.hashicorp.com/terraform/language/functions/coalesce)
- [resources in a resource group can be in other regions than the group](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/manage-resource-groups-portal)
- [a change of `location` forces a new `azurerm_storage_account`](https://raw.githubusercontent.com/hashicorp/terraform-provider-azurerm/main/website/docs/r/storage_account.html.markdown)
- [a change of `location` forces a new `azurerm_service_plan`](https://raw.githubusercontent.com/hashicorp/terraform-provider-azurerm/main/website/docs/r/service_plan.html.markdown)
- [a change of `location` forces a new `azurerm_log_analytics_workspace`](https://raw.githubusercontent.com/hashicorp/terraform-provider-azurerm/main/website/docs/r/log_analytics_workspace.html.markdown)
- [a change of `location` forces a new `azurerm_application_insights`](https://raw.githubusercontent.com/hashicorp/terraform-provider-azurerm/main/website/docs/r/application_insights.html.markdown)
- [a change of `location` forces a new `azurerm_linux_function_app`](https://raw.githubusercontent.com/hashicorp/terraform-provider-azurerm/main/website/docs/r/linux_function_app.html.markdown)
- [`tofu test` supports `mock_provider` blocks](https://opentofu.org/docs/cli/commands/test/)
- [App Service quota is per region](https://learn.microsoft.com/en-us/answers/a/12313992)
