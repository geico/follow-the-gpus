variable "name_prefix" {
  description = "Prefix of each resource name. Lower case letters and digits, 2 to 10 characters."
  type        = string
  default     = "ftg"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,9}$", var.name_prefix))
    error_message = "name_prefix must match ^[a-z][a-z0-9]{1,9}$. Storage account names allow only lower case letters and digits."
  }
}

variable "subscription_id" {
  description = "ID of the Azure subscription of the deploy. Null means the provider reads ARM_SUBSCRIPTION_ID."
  type        = string
  default     = null
}

variable "resource_group_name" {
  description = "Name of an existing resource group. Every resource of the stack goes here: the data storage, the prober VM, the sweeper, and the probe resources. The stack does not create or delete the group."
  type        = string
  nullable    = false

  # The Azure rules for a group name, limited to ASCII. The sweeper query accepts only these characters.
  validation {
    condition     = can(regex("^[A-Za-z0-9._()-]{0,89}[A-Za-z0-9_()-]$", var.resource_group_name))
    error_message = "resource_group_name must be 1 to 90 characters: letters, digits, '.', '_', '-', '(', ')'. It must not end with '.'."
  }
}

variable "subnet_id" {
  description = "Resource ID of an existing subnet in var.location. The prober VM NIC and the probe scale sets go here. The stack creates no virtual network, subnet, or network security group."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("(?i)^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\\.Network/virtualNetworks/[^/]+/subnets/[^/]+$", var.subnet_id))
    error_message = "subnet_id must be a subnet resource ID: /subscriptions/<id>/resourceGroups/<group>/providers/Microsoft.Network/virtualNetworks/<network>/subnets/<subnet>."
  }
}

variable "probe_subnet_ids" {
  description = "Subnets for the scale-set probes in other regions, as { region = subnet resource ID }. The region is the lower-case region name, for example southcentralus. The control region needs no entry: it falls back to subnet_id. A scale set uses the subnet of its own region, because a NIC joins only a virtual network of its region. These subnets hold probe VMs only. The prober VM makes every Azure call from subnet_id, so a probe subnet needs no outbound path: use an isolated virtual network with no peering, no route table, and no NAT gateway. The stack creates no network. The plan cannot check the region of a subnet from its ID, so a subnet in the wrong region passes the plan and fails at scale-out with an Azure NIC error."
  type        = map(string)
  default     = {}
  nullable    = false

  validation {
    condition     = alltrue([for id in values(var.probe_subnet_ids) : can(regex("(?i)^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\\.Network/virtualNetworks/[^/]+/subnets/[^/]+$", id))])
    error_message = "Each probe_subnet_ids value must be a subnet resource ID: /subscriptions/<id>/resourceGroups/<group>/providers/Microsoft.Network/virtualNetworks/<network>/subnets/<subnet>."
  }

  validation {
    condition     = length(distinct([for r in keys(var.probe_subnet_ids) : lower(r)])) == length(var.probe_subnet_ids)
    error_message = "probe_subnet_ids has 2 keys that differ only in letter case (for example SouthCentralUS and southcentralus). Use 1 lower-case region name per entry."
  }

  validation {
    condition = (
      length(distinct([for id in values(var.probe_subnet_ids) : lower(id)])) == length(var.probe_subnet_ids) &&
      alltrue([for region, id in var.probe_subnet_ids : lower(id) != lower(var.subnet_id) || lower(region) == lower(var.location)])
    )
    error_message = "Each probe_subnet_ids subnet must be used by 1 region only (compared without regard to case), and none may equal subnet_id unless its region is var.location."
  }
}

variable "location" {
  description = "The control region. The prober VM and the data storage live here, and the subnet in subnet_id must be here. The sweeper does too, unless sweeper_location is set. Probe scale sets run in this region with subnet_id, and in another region only with an entry of probe_subnet_ids."
  type        = string
  default     = "eastus"
}

variable "sweeper_location" {
  description = "Region of the sweeper service plan, Function App, function storage account, Application Insights, and Log Analytics workspace. Null means var.location. Set it when the subscription has no App Service quota in the control region. These resources stay in resource_group_name. The prober VM and the data storage stay in var.location."
  type        = string
  default     = null

  validation {
    condition     = var.sweeper_location == null ? true : trimspace(var.sweeper_location) != ""
    error_message = "sweeper_location must be null or a region name."
  }
}

variable "prober_vm_size" {
  description = "VM size of the prober VM."
  type        = string
  default     = "Standard_B2ats_v2"
}

variable "sweeper_plan_sku" {
  description = "SKU of the sweeper service plan. Y1 is the Linux consumption plan. B1, B2, B3, and S1 are dedicated plans for a subscription with no Y1 quota."
  type        = string
  default     = "Y1"

  validation {
    condition     = contains(["Y1", "B1", "B2", "B3", "S1"], var.sweeper_plan_sku)
    error_message = "sweeper_plan_sku must be Y1, B1, B2, B3, or S1."
  }
}

variable "budget_amount" {
  description = "Monthly budget of resource_group_name, in the billing currency."
  type        = number
  default     = 50
}

variable "alert_email" {
  description = "Email address for the budget alerts and the sweeper-stopped alert. Empty means no budget and no alert."
  type        = string
  default     = ""
}

variable "targets_file" {
  description = "Path of the targets file. The path is relative to this folder, or absolute."
  type        = string
  default     = "../../config/targets.yaml"
}

variable "grant_billable_provisioning" {
  description = "Grant the prober the rights to create billable capacity: the capacity-allocator role (scale sets out and in, capacity reservations) and, for the probe subnet of each region with a real scale set that is in another resource group (never subnet_id), the subnet-joiner role. This is the only thing that decides whether the prober gets these 2 rights, and whether the stack plans the capacity reservation groups and scale sets that provisioning_probes entries call for; it does not read the targets file (ADR 0015). It does not gate the sweeper's own cleanup role, which stays unconditional: the sweeper keeps its delete and scale-to-0 rights regardless, so it can clean up a leaked resource (spec 0005 R7). Set true only when you also want provisioning_probes entries in the targets file to run for real: the targets file's own allow_billable_provisioning key is a separate control the prober checks, not this variable."
  type        = bool
  default     = false
  nullable    = false
}

variable "fault_domain_count" {
  description = "platformFaultDomainCount of each probe scale set. 1 is max spreading. Pilot P0 can change it."
  type        = number
  default     = 1
}

variable "tags" {
  description = "Tags on each resource. The key experiment is not allowed: only probe resources carry it, because the sweeper deletes by it."
  type        = map(string)
  default     = {}

  validation {
    condition     = !contains([for k in keys(var.tags) : lower(k)], "experiment")
    error_message = "tags must not hold the key experiment, in any case. The stack sets it on probe resources only, from the targets file's experiment key."
  }
}

variable "egress_proxy_url" {
  description = "URL of an HTTP egress proxy, for example http://proxy.example.internal:80. Null means no proxy. The prober VM then uses the proxy for apt, curl, pip, and the prober service. Use a host name, not an IP address. Put no user name or password in the URL. The URL goes into the VM custom_data."
  type        = string
  default     = null

  validation {
    condition     = var.egress_proxy_url == null ? true : can(regex("^https?://[A-Za-z0-9.-]+:[0-9]{1,5}/?$", var.egress_proxy_url))
    error_message = "egress_proxy_url must look like http://host:port or https://host:port, with no user name and no path."
  }

  validation {
    condition     = var.egress_proxy_url == null ? true : !can(regex("^https?://([0-9]{1,3}\\.){3}[0-9]{1,3}(:|/|$)|^https?://\\[", var.egress_proxy_url))
    error_message = "egress_proxy_url must use a host name. An IPv4 or IPv6 address as the host is not allowed."
  }
}

variable "no_proxy" {
  description = "Extra hosts that bypass egress_proxy_url, added to the fixed list (169.254.169.254, 168.63.129.16, localhost, 127.0.0.1, login.microsoftonline.com). If the data storage account uses a private endpoint, add .blob.core.windows.net. Used only when egress_proxy_url is set."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for h in var.no_proxy : can(regex("^[A-Za-z0-9.*:/_-]+$", h))])
    error_message = "Each no_proxy entry must hold only letters, digits, and the characters . * : / _ -. It must not hold a comma or a space."
  }
}

variable "data_reader_principal_ids" {
  description = "Entra object IDs that get read access to the data container and the code container, besides the deployer. A group object ID is preferred over a user or a service principal object ID (Azure RBAC best practices: assign roles to groups, not users)."
  type        = list(string)
  default     = []
  nullable    = false

  validation {
    condition     = alltrue([for id in var.data_reader_principal_ids : can(trimspace(id)) && trimspace(id) != ""])
    error_message = "Each data_reader_principal_ids entry must be a non-null string, and not empty or made only of whitespace."
  }
}
