# The prober VM, its security group, and the code zips. The VPC and the subnet exist already.

# The AMI ID comes from the public SSM parameter of Canonical.
data "aws_ssm_parameter" "ubuntu" {
  name = var.ami_ssm_parameter
}

# The Availability Zones that offer the instance type. Not every zone offers every type.
data "aws_ec2_instance_type_offerings" "prober" {
  location_type = "availability-zone"

  filter {
    name   = "instance-type"
    values = [var.instance_type]
  }
}

# The given subnet. The plan reads its VPC and zone for the preconditions on the instance.
data "aws_subnet" "prober" {
  id = var.subnet_id
}

locals {
  # The source folders, relative to this module.
  prober_dir = "${path.module}/../../prober"

  targets_path = startswith(var.targets_file, "/") ? var.targets_file : "${path.module}/${var.targets_file}"
  targets      = yamldecode(file(local.targets_path))
  experiment   = local.targets.experiment

  # A key that the aws section does not know must fail the plan. `try(..., [])` below reads an
  # absent section as empty, so a misspelled or old key (for example `allocation_probes`, now
  # `provisioning_probes`) would otherwise give an empty list and plan the removal of stateful
  # resources. The messages match `check-config` (prober/ftg_prober/config.py,
  # `KNOWN_CLOUD_KEYS` and `RENAMED_KEYS`). Keep the 2 lists in step with that file;
  # `test_hcl_known_keys_match_the_python_list` and `test_hcl_renamed_keys_match_the_python_map`
  # (prober/tests/test_config.py) fail when they differ.
  known_aws_keys = ["modules", "jobs", "cadence", "provisioning_probes", "quota", "probe_timeout_seconds", "poll_interval_seconds", "teardown_timeout_seconds", "queue_wake_seconds", "queue_max_calls_per_wake", "queue_max_throttle_attempts", "queue_jitter", "queue_backoff_base_seconds", "queue_backoff_cap_seconds"]
  renamed_keys = {
    allow_allocation  = "`allow_allocation` is now `allow_billable_provisioning` (ADR 0016)."
    allocation_probes = "`allocation_probes` is now `provisioning_probes`."
    az_allocation     = "`az_allocation` is now `az_provisioning`."
  }
  targets_faults = concat(
    [
      for k in try(keys(local.targets.aws), []) :
      "aws.${k} is not a known key. See spec 0003 section 3 for the file shape.${contains(keys(local.renamed_keys), k) ? " ${local.renamed_keys[k]}" : ""}"
      if !contains(local.known_aws_keys, k)
    ],
    [
      for k in try(keys(local.targets.aws.cadence), []) :
      "aws.cadence.${k} is not a known key. ${local.renamed_keys[k]}"
      if contains(keys(local.renamed_keys), k)
    ],
    # Only null reads as empty. A string, a list, or a number is not a mapping and must fail, not
    # plan the section as empty.
    try(local.targets.aws, null) != null && !can(keys(local.targets.aws)) ? ["`aws` must be a mapping"] : [],
    try(local.targets.aws.cadence, null) != null && !can(keys(local.targets.aws.cadence)) ? ["aws.cadence must be a mapping"] : [],
  )

  # The config zip holds the folder of the targets file, so the VM reads the file the deployer
  # chose. The default is ../../config/targets.yaml. Same pattern as terraform/azure/control.tf.
  config_dir  = dirname(local.targets_path)
  config_file = basename(local.targets_path)

  # The zip lands under .terraform/, not the git-ignored dist/. A tool that copies the repo
  # copies the whole checkout into its cache, ignored files included, and it can run that copy
  # again between `plan -out=tfplan` and `apply tfplan`. A file under dist/ survives that second
  # copy and can overwrite the zip the plan just built with a stale one from an earlier local
  # build, so the apply uploads old bytes under the new hash key (spec 0004, section 4.5).
  # Such a copy skips dot-directories, so a zip built under .terraform/ cannot be
  # replaced by a copy of the source checkout.
  code_dir = "${path.module}/.terraform/ftg-build"

  # Patterns that the prober zip leaves out. The provider matches each pattern against the path
  # inside the source folder, so "**/<name>" is needed to match at every depth. A change in a
  # tool cache must not change the zip hash, because a new hash replaces the VM.
  prober_exclude_names = [".venv", "__pycache__", "tests", "dist", ".pytest_cache", ".ruff_cache", "*.egg-info"]
  prober_excludes      = concat(flatten([for n in local.prober_exclude_names : ["**/${n}", "**/${n}/**"]]))
}

# The archive provider takes 1 source folder per zip. Thus the code has 2 zips.
data "archive_file" "prober" {
  type             = "zip"
  source_dir       = local.prober_dir
  output_path      = "${local.code_dir}/prober.zip"
  output_file_mode = "0644"
  excludes         = local.prober_excludes
}

data "archive_file" "config" {
  type             = "zip"
  source_dir       = local.config_dir
  output_path      = "${local.code_dir}/config.zip"
  output_file_mode = "0644"
}

# The key holds the hash of the zip. A new zip is a new object; create_before_destroy
# deletes the old one after the apply. No lifecycle rule expires the current one (s3.tf).
# ADR 0012 section 3: create_before_destroy defers the old key's destroy until after
# aws_s3_object.manifest (which depends_on both zips) has already been rewritten to the new
# keys, so a live manifest never names a key that is already gone.
resource "aws_s3_object" "prober" {
  bucket       = aws_s3_bucket.data.id
  key          = "code/prober-${data.archive_file.prober.output_sha256}.zip"
  source       = data.archive_file.prober.output_path
  source_hash  = data.archive_file.prober.output_md5
  content_type = "application/zip"

  # Empty maps match the refreshed state. Unset, the plan shows `{} -> null` on every run.
  tags     = {}
  metadata = {}

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_s3_object" "config" {
  bucket       = aws_s3_bucket.data.id
  key          = "code/config-${data.archive_file.config.output_sha256}.zip"
  source       = data.archive_file.config.output_path
  source_hash  = data.archive_file.config.output_md5
  content_type = "application/zip"

  # Empty maps match the refreshed state. Unset, the plan shows `{} -> null` on every run.
  tags     = {}
  metadata = {}

  lifecycle {
    create_before_destroy = true
  }
}

# ADR 0012 section 3: a fixed key at the bucket root, outside code/ (the current code/ keys
# do not expire either, s3.tf). `bucket` and `key` are the only ForceNew fields of
# this resource, and neither changes here, so Terraform updates it in place: there is no window
# where manifest.json does not exist on AWS.
resource "aws_s3_object" "manifest" {
  bucket = aws_s3_bucket.data.id
  key    = "manifest.json"
  content = jsonencode({
    prober_key = aws_s3_object.prober.key
    config_key = aws_s3_object.config.key
    sync_key   = aws_s3_object.sync_script.key
  })
  content_type = "application/json"

  tags     = {}
  metadata = {}

  depends_on = [aws_s3_object.prober, aws_s3_object.config, aws_s3_object.sync_script]
}

# sync.sh (ADR 0012 section 4), 1 file shared with the Azure stack (terraform/sync.sh). Hash-named
# and verified the same way as the 2 code zips above, under code/, so it is deleted the same way
# when a new one replaces it and needs no IAM entry of its own beyond the existing code/* grants.
# manifest.json's own sync_key field names the current one; sync.sh re-reads that field on every
# tick and replaces its own installed copy when the hash differs (section 4), so a sync.sh fix
# alone also reaches a running VM with no VM replacement and no separate fixed key to fetch at
# boot.
resource "aws_s3_object" "sync_script" {
  bucket       = aws_s3_bucket.data.id
  key          = "code/sync-${filesha256("${path.module}/../sync.sh")}.sh"
  source       = "${path.module}/../sync.sh"
  source_hash  = filemd5("${path.module}/../sync.sh")
  content_type = "text/x-shellscript"

  tags     = {}
  metadata = {}

  lifecycle {
    create_before_destroy = true
  }
}

# The VPC. The plan reads it to find out whether it has an IPv6 CIDR block.
data "aws_vpc" "prober" {
  id = var.vpc_id
}

locals {
  # The VPC has an IPv6 CIDR block when 1 IPv6 association has the state "associated".
  vpc_has_ipv6 = length([for a in data.aws_vpc.prober.ipv6_cidr_block_associations : a if a.state == "associated"]) > 0
}

# The security group that the stack creates. No inbound rule. Outbound: all traffic, so that the
# VM works in any VPC, also with a custom DHCP DNS server or a proxy on any port. The network
# owner can add more groups with var.vpc_security_group_ids.
resource "aws_security_group" "prober" {
  count = var.create_security_group ? 1 : 0

  name        = "${var.name_prefix}-prober"
  description = "Prober VM: no inbound, all outbound"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.name_prefix}-prober"
  }
}

resource "aws_vpc_security_group_egress_rule" "all_ipv4" {
  count = var.create_security_group ? 1 : 0

  security_group_id = aws_security_group.prober[0].id
  description       = "All outbound IPv4 traffic"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "all_ipv6" {
  count = var.create_security_group && local.vpc_has_ipv6 ? 1 : 0

  security_group_id = aws_security_group.prober[0].id
  description       = "All outbound IPv6 traffic"
  ip_protocol       = "-1"
  cidr_ipv6         = "::/0"
}

locals {
  # These hosts never go through the proxy. var.no_proxy adds to them.
  no_proxy = join(",", concat(["169.254.169.254", "localhost", "127.0.0.1"], var.no_proxy))
}

resource "aws_instance" "prober" {
  ami                    = data.aws_ssm_parameter.ubuntu.insecure_value
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = concat(aws_security_group.prober[*].id, var.vpc_security_group_ids)
  iam_instance_profile   = aws_iam_instance_profile.prober.name

  # Outbound internet access from the given subnet is a deploy prerequisite, for example a NAT
  # gateway, or a public IP and an internet gateway. The created security group has no inbound
  # rule. The stack never uses the default security group of the VPC.

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    bucket            = aws_s3_bucket.data.bucket
    region            = var.region
    config_file       = local.config_file
    proxy_url         = var.egress_proxy_url == null ? "" : var.egress_proxy_url
    no_proxy          = local.no_proxy
    sync_service_unit = file("${path.module}/../ftg-prober-sync.service")
    sync_timer_unit   = file("${path.module}/../ftg-prober-sync.timer")
  })

  # ADR 0012 section 3: the user data now names only the fixed manifest.json key, which
  # Terraform updates in place (main.tf, aws_s3_object.manifest), so a code or a config change
  # no longer changes the rendered user data, and this no longer forces a replacement for that
  # reason. It still fires for an unrelated user data change, for example a new proxy URL.
  user_data_replace_on_change = true

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 8
    encrypted   = true
  }

  tags = {
    Name = "${var.name_prefix}-prober"
  }

  # The role policy must exist before the first boot downloads the code. ADR 0012 section 3:
  # the manifest must exist, naming the 2 zips that already exist, before the first sync.
  depends_on = [
    aws_iam_role_policy.prober,
    aws_iam_role_policy_attachment.ssm,
    aws_s3_object.manifest,
    aws_s3_object.sync_script,
  ]

  # the AMI is read when the instance is created. A new Canonical image later
  # changes the SSM parameter, and without this the next plan replaces the instance, even for
  # a code-only apply. To move to a new image on purpose, plan with -replace=aws_instance.prober.
  lifecycle {
    ignore_changes = [ami]

    precondition {
      condition     = var.create_security_group || length(var.vpc_security_group_ids) > 0
      error_message = "create_security_group is false and vpc_security_group_ids is empty. Give at least 1 security group in vpc_security_group_ids, or set create_security_group to true. The stack never uses the default security group."
    }

    precondition {
      condition     = data.aws_subnet.prober.vpc_id == var.vpc_id
      error_message = "subnet_id ${var.subnet_id} is not in vpc_id ${var.vpc_id}."
    }

    precondition {
      condition     = contains(data.aws_ec2_instance_type_offerings.prober.locations, data.aws_subnet.prober.availability_zone)
      error_message = "The zone of subnet_id does not offer ${var.instance_type}. Use a subnet in another zone, or change instance_type."
    }
  }
}

# Fails the plan when the targets file has an old or unknown key (see local.targets_faults).
resource "terraform_data" "targets_check" {
  lifecycle {
    precondition {
      condition     = length(local.targets_faults) == 0
      error_message = "The targets file ${local.targets_path} is not valid: ${join("; ", local.targets_faults)}"
    }
  }
}
