# Inputs of the AWS stack. Put private values in the ignored terraform.tfvars file, never in the repo.

variable "name_prefix" {
  description = "Prefix of each resource name. Lower case letters, digits, and hyphens."
  type        = string
  default     = "ftg"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,19}$", var.name_prefix))
    error_message = "name_prefix must start with a letter and use only lower case letters, digits, and hyphens (1 to 20 characters)."
  }

  # S3 rejects bucket names that start with these prefixes.
  validation {
    condition     = !startswith(var.name_prefix, "xn--") && !startswith(var.name_prefix, "sthree-") && !startswith(var.name_prefix, "amzn-s3-demo-")
    error_message = "name_prefix must not start with xn--, sthree-, or amzn-s3-demo-. S3 reserves these bucket name prefixes."
  }
}

variable "vpc_id" {
  description = "ID of an existing VPC in var.region. The security group of the prober VM goes here. The stack creates no VPC."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^vpc-([0-9a-f]{8}|[0-9a-f]{17})$", var.vpc_id))
    error_message = "vpc_id must be a VPC ID: vpc- and 8 or 17 hex digits."
  }
}

variable "subnet_id" {
  description = "ID of an existing subnet in vpc_id. The prober VM goes here. Its zone must offer instance_type, and it must give the VM an outbound path."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^subnet-([0-9a-f]{8}|[0-9a-f]{17})$", var.subnet_id))
    error_message = "subnet_id must be a subnet ID: subnet- and 8 or 17 hex digits."
  }
}

variable "region" {
  description = "AWS Region of the stack. The prober VM and the data bucket live here."
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "Instance type of the prober VM. The default is a small arm64 type. If you change the CPU architecture, change ami_ssm_parameter too, and plan with -replace=aws_instance.prober, because the instance ignores AMI changes."
  type        = string
  default     = "t4g.small"
}

variable "ami_ssm_parameter" {
  description = "Public SSM parameter that holds the AMI ID of Ubuntu 24.04 LTS. The default is the arm64 image."
  type        = string
  default     = "/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id"
}

variable "tags" {
  description = "Extra tags for each resource."
  type        = map(string)
  default     = {}
}

variable "targets_file" {
  description = "Path of the targets file. The path is relative to this folder, or absolute."
  type        = string
  default     = "../../config/targets.yaml"
}

variable "enable_ssm" {
  description = "Attach the AmazonSSMManagedInstanceCore policy, so that you can open a shell with SSM Session Manager. Set false for the smallest IAM role."
  type        = bool
  default     = true
}

variable "grant_billable_provisioning" {
  description = "Grant the role the aws_odcr provisioning probe's IAM actions (spec 0002 section 4.9): create, cancel, and describe On-Demand Capacity Reservations, and tag a reservation on create. This is the only thing that decides whether the stack grants these rights; it does not read the targets file (ADR 0015). Set true only when you also want provisioning_probes entries in the targets file to run for real: the targets file's own allow_billable_provisioning key is a separate control the prober checks, not this variable."
  type        = bool
  default     = false
  nullable    = false
}

variable "enable_s3_gateway_endpoint" {
  description = "Create an S3 gateway endpoint in vpc_id and attach it to the route table of the subnet (the main route table when the subnet has no own table). It adds an S3 route only. It does not replace the outbound path that the VM needs for the EC2, STS, and SSM APIs, package mirrors, and the AWS CLI download."
  type        = bool
  default     = false
}

variable "egress_proxy_url" {
  description = "URL of an HTTP egress proxy, for example http://proxy.example.internal:80. The VM sends apt, curl, pip, and AWS API traffic through it. null (the default) means no proxy. Use a host name, never an IP address."
  type        = string
  default     = null

  validation {
    condition     = var.egress_proxy_url == null || can(regex("^https?://[A-Za-z0-9.-]+:[0-9]{1,5}$", var.egress_proxy_url)) && tonumber(regex(":([0-9]+)$", var.egress_proxy_url)[0]) >= 1 && tonumber(regex(":([0-9]+)$", var.egress_proxy_url)[0]) <= 65535
    error_message = "egress_proxy_url must look like http://host:port or https://host:port, with a port from 1 to 65535, no user name, no path, and no quote."
  }

  validation {
    condition     = var.egress_proxy_url == null ? true : !can(regex("^https?://([0-9]{1,3}\\.){3}[0-9]{1,3}(:|/|$)|^https?://\\[", var.egress_proxy_url))
    error_message = "egress_proxy_url must use a host name. An IPv4 or IPv6 address as the host is not allowed."
  }
}

variable "no_proxy" {
  description = "Extra hosts and domains that bypass the proxy. The stack always adds 169.254.169.254, localhost, and 127.0.0.1."
  type        = list(string)
  default     = []
  nullable    = false

  validation {
    condition     = alltrue([for h in var.no_proxy : can(regex("^[A-Za-z0-9._:/*-]+$", h))])
    error_message = "Each no_proxy entry must use only letters, digits, and the characters . _ : / * -."
  }
}

variable "vpc_security_group_ids" {
  description = "IDs of existing security groups in vpc_id for the prober VM. They add to the group that the stack creates. The network owner can use them to allow outbound paths, for example to a DNS server or a proxy."
  type        = list(string)
  default     = []
  nullable    = false

  validation {
    condition     = alltrue([for g in var.vpc_security_group_ids : can(regex("^sg-([0-9a-f]{8}|[0-9a-f]{17})$", g))])
    error_message = "Each vpc_security_group_ids entry must be a security group ID: sg- and 8 or 17 hex digits."
  }
}

variable "create_security_group" {
  description = "Create a security group for the prober VM: no inbound rule, all outbound IPv4 traffic, and all outbound IPv6 traffic when the VPC has an IPv6 CIDR block. Set false to use only vpc_security_group_ids, which must then hold at least 1 group."
  type        = bool
  default     = true
  nullable    = false
}

variable "alert_email" {
  description = "Email address for the sweeper-stopped alarm. Empty means no topic and no alarm."
  type        = string
  default     = ""

  validation {
    condition     = var.alert_email == "" || can(regex("^[^@[:space:]]+@[^@[:space:]]+$", var.alert_email))
    error_message = "alert_email must be empty or an email address."
  }
}
