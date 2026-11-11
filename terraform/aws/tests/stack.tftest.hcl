# Tests of the AWS stack with mock providers. They need no credentials, no network, and no
# ../../prober folder. Run them with `make test/aws`.

mock_provider "aws" {
  override_data {
    target = data.aws_ec2_instance_type_offerings.prober
    values = {
      locations = ["us-east-1a", "us-east-1b"]
    }
  }

  override_data {
    target = data.aws_subnet.prober
    values = {
      vpc_id            = "vpc-0123456789abcdef0"
      availability_zone = "us-east-1b"
    }
  }

  override_data {
    target = data.aws_vpc.prober
    values = {
      ipv6_cidr_block_associations = []
    }
  }

  override_data {
    target = data.aws_ssm_parameter.ubuntu
    values = {
      insecure_value = "ami-0test"
    }
  }

  override_data {
    target = data.aws_route_tables.subnet
    values = {
      ids = ["rtb-0aaaaaaaaaaaaaaaa"]
    }
  }

  override_data {
    target = data.aws_route_tables.main
    values = {
      ids = ["rtb-0bbbbbbbbbbbbbbbb"]
    }
  }

  override_resource {
    target = aws_s3_bucket.data
    values = {
      arn = "arn:aws:s3:::ftg-data-test"
    }
  }

  # The provider checks these ARNs at plan time (the Lambda role, the schedule target).
  override_resource {
    target = aws_iam_role.sweeper
    values = {
      arn = "arn:aws:iam::aws:role/ftg-sweeper"
    }
  }

  override_resource {
    target = aws_iam_role.sweeper_scheduler
    values = {
      arn = "arn:aws:iam::aws:role/ftg-sweeper-scheduler"
    }
  }

  override_resource {
    target = aws_lambda_function.sweeper
    values = {
      arn = "arn:aws:lambda:us-east-1:aws:function:ftg-sweeper"
    }
  }
}

mock_provider "archive" {
  override_data {
    target = data.archive_file.prober
    values = {
      output_sha256 = "aaaa"
      output_md5    = "bbbb"
    }
  }

  override_data {
    target = data.archive_file.config
    values = {
      output_sha256 = "cccc"
      output_md5    = "dddd"
    }
  }
}

mock_provider "random" {
  override_resource {
    target = random_id.bucket_suffix
    values = {
      hex = "0123abcd"
    }
  }
}

variables {
  vpc_id    = "vpc-0123456789abcdef0"
  subnet_id = "subnet-0123456789abcdef0"
}

run "defaults" {
  command = apply

  assert {
    condition     = aws_s3_bucket.data.bucket == "ftg-data-0123abcd"
    error_message = "The bucket name must be <name_prefix>-data-<random suffix>."
  }

  assert {
    condition     = aws_s3_bucket.data.force_destroy == false
    error_message = "The data bucket must not be destroyed with its objects."
  }

  assert {
    condition     = aws_s3_bucket_public_access_block.data.block_public_acls && aws_s3_bucket_public_access_block.data.block_public_policy && aws_s3_bucket_public_access_block.data.ignore_public_acls && aws_s3_bucket_public_access_block.data.restrict_public_buckets
    error_message = "All 4 public access blocks must be on."
  }

  assert {
    condition     = aws_s3_bucket_versioning.data.versioning_configuration[0].status == "Disabled"
    error_message = "Versioning must be off."
  }

  assert {
    condition     = [for r in aws_s3_bucket_server_side_encryption_configuration.data.rule : one(r.apply_server_side_encryption_by_default).sse_algorithm] == ["AES256"]
    error_message = "The bucket must use SSE-S3."
  }

  assert {
    condition     = length(aws_s3_bucket_lifecycle_configuration.data.rule) == 1 && alltrue([for r in aws_s3_bucket_lifecycle_configuration.data.rule : !startswith(r.filter[0].prefix, "code/") && r.id != "expire-code"])
    error_message = "No rule may expire code/. The objects that manifest.json names must stay for a first boot at any age."
  }

  assert {
    condition     = aws_s3_bucket_lifecycle_configuration.data.rule[0].filter[0].prefix == "experiment=ftg-20261014-example/logs/" && aws_s3_bucket_lifecycle_configuration.data.rule[0].expiration[0].days == 90
    error_message = "1 rule must expire the logs prefix after 90 days, apart from the rows."
  }

  assert {
    condition = join(",", sort(flatten([
      for s in jsondecode(aws_iam_role_policy.prober.policy).Statement : s.Action if s.Sid == "ReadCapacitySignals"
    ]))) == "ec2:DescribeCapacityBlockOfferings,ec2:DescribeSpotPriceHistory,ec2:GetSpotPlacementScores"
    error_message = "The EC2 statement must allow exactly the 3 read-only actions."
  }

  assert {
    condition = [
      for s in jsondecode(aws_iam_role_policy.prober.policy).Statement : [s.Action, s.Resource] if s.Sid == "WriteRecords"
    ] == [["s3:PutObject", "arn:aws:s3:::ftg-data-test/experiment=ftg-20261014-example/*"]]
    error_message = "The write statement must allow s3:PutObject on the experiment prefix only, not the whole bucket."
  }

  assert {
    condition = [
      for s in jsondecode(aws_iam_role_policy.prober.policy).Statement : [s.Action, s.Resource] if s.Sid == "ReadCode"
    ] == [["s3:GetObject", ["arn:aws:s3:::ftg-data-test/code/*", "arn:aws:s3:::ftg-data-test/manifest.json"]]]
    error_message = "The read statement must allow s3:GetObject on code/* (which covers the hash-named sync.sh too) and manifest.json only (ADR 0012)."
  }

  assert {
    condition = [
      for s in jsondecode(aws_iam_role_policy.prober.policy).Statement : [s.Action, s.Effect, s.Resource] if s.Sid == "DenyWriteCode"
    ] == [[["s3:PutObject", "s3:DeleteObject"], "Deny", ["arn:aws:s3:::ftg-data-test/manifest.json", "arn:aws:s3:::ftg-data-test/code/*"]]]
    error_message = "ADR 0012 section 5: an explicit Deny must block s3:PutObject and s3:DeleteObject on manifest.json and code/* (which covers the hash-named sync.sh too), as a second guard (WriteRecords no longer matches them)."
  }

  assert {
    condition     = length(jsondecode(aws_iam_role_policy.prober.policy).Statement) == 6
    error_message = "The role policy must have exactly 6 base statements (ADR 0012 adds DenyWriteCode; ADR 0025 adds the 2 compaction read statements)."
  }

  # ADR 0025: read of this experiment's objects and list of the bucket.
  assert {
    condition = [
      for s in jsondecode(aws_iam_role_policy.prober.policy).Statement : [s.Effect, s.Action, s.Resource] if s.Sid == "ReadRowsForCompaction"
    ] == [["Allow", "s3:GetObject", "arn:aws:s3:::ftg-data-test/experiment=ftg-20261014-example/*"]]
    error_message = "ReadRowsForCompaction must allow s3:GetObject on the experiment prefix only (ADR 0025)."
  }

  assert {
    condition = [
      for s in jsondecode(aws_iam_role_policy.prober.policy).Statement : [s.Effect, s.Action, s.Resource] if s.Sid == "ListBucketForCompaction"
    ] == [["Allow", "s3:ListBucket", "arn:aws:s3:::ftg-data-test"]]
    error_message = "ListBucketForCompaction must allow s3:ListBucket on the bucket (ADR 0025)."
  }

  # Hard rule of no Allow statement may delete.
  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.prober.policy).Statement :
      s.Effect == "Deny" || !anytrue([for a in flatten([s.Action]) : a == "s3:DeleteObject" || a == "s3:*" || a == "*" || startswith(a, "s3:Delete")])
    ])
    error_message = "No Allow statement may grant a delete."
  }


  assert {
    condition     = length(aws_iam_role_policy_attachment.ssm) == 1
    error_message = "enable_ssm defaults to true, so the SSM policy is attached."
  }

  assert {
    condition     = aws_s3_object.prober.key == "code/prober-aaaa.zip" && aws_s3_object.config.key == "code/config-cccc.zip"
    error_message = "The code keys must hold the sha256 of each zip."
  }

  assert {
    condition = aws_s3_object.manifest.key == "manifest.json" && jsondecode(aws_s3_object.manifest.content) == {
      prober_key = "code/prober-aaaa.zip"
      config_key = "code/config-cccc.zip"
      sync_key   = aws_s3_object.sync_script.key
    }
    error_message = "ADR 0012: manifest.json must be a fixed key at the bucket root naming the 2 current code keys and the current hash-named sync.sh key."
  }

  assert {
    condition     = startswith(aws_s3_object.sync_script.key, "code/sync-") && endswith(aws_s3_object.sync_script.key, ".sh") && aws_s3_object.sync_script.source == "${path.module}/../sync.sh"
    error_message = "ADR 0012: sync.sh must be hash-named under code/, verified and replaced the same way as the 2 code zips."
  }

  assert {
    condition     = aws_instance.prober.ami == "ami-0test" && aws_instance.prober.instance_type == "t4g.small" && aws_instance.prober.subnet_id == "subnet-0123456789abcdef0"
    error_message = "The VM must use the SSM AMI, the default instance type, and subnet_id."
  }

  assert {
    condition     = aws_security_group.prober[0].vpc_id == "vpc-0123456789abcdef0"
    error_message = "The security group must be in vpc_id."
  }

  assert {
    condition     = length(aws_security_group.prober) == 1 && length(aws_security_group.prober[0].ingress) == 0 && length(aws_vpc_security_group_egress_rule.all_ipv6) == 0
    error_message = "The stack must create 1 security group with no ingress rule and no IPv6 rule when the VPC has no IPv6 CIDR."
  }

  assert {
    condition     = length(aws_instance.prober.vpc_security_group_ids) == 1
    error_message = "The VM must have the created security group only."
  }

  assert {
    condition     = aws_instance.prober.metadata_options[0].http_tokens == "required"
    error_message = "IMDSv2 must be required."
  }

  assert {
    condition     = aws_instance.prober.user_data_replace_on_change == true
    error_message = "A new user data must replace the VM."
  }

  assert {
    condition = alltrue([
      # ADR 0012: the user data no longer names either hashed zip key; it fetches the fixed
      # sync.sh key, writes a sync-env naming the aws pip extra, and starts the sync timer and
      # unit, which do the rest (the first build, the gate, the swap, and the restart).
      strcontains(aws_instance.prober.user_data, "--key manifest.json \"$work/manifest.json\""),
      strcontains(aws_instance.prober.user_data, "sync_key=$(jq -r .sync_key \"$work/manifest.json\")"),
      strcontains(aws_instance.prober.user_data, "--key \"$sync_key\" /opt/ftg/bin/sync.sh"),
      strcontains(aws_instance.prober.user_data, "FTG_BUCKET=ftg-data-0123abcd"),
      strcontains(aws_instance.prober.user_data, "FTG_PIP_EXTRA=aws"),
      !strcontains(aws_instance.prober.user_data, "FTG_EXPERIMENT"),
      strcontains(aws_instance.prober.user_data, "ExecStart=/opt/ftg/current/venv/bin/ftg-prober run --provider aws --config /opt/ftg/current/config/targets.yaml --store s3://ftg-data-0123abcd/"),
      strcontains(aws_instance.prober.user_data, "Restart=on-failure"),
      strcontains(aws_instance.prober.user_data, "systemctl enable ftg-prober-sync.timer"),
      strcontains(aws_instance.prober.user_data, "systemctl start --no-block ftg-prober-sync.service"),
    ])
    error_message = "The user data must fetch sync.sh, write sync-env, write the ftg-prober.service unit pointed at /opt/ftg/current, and start the sync timer and unit (ADR 0012)."
  }

  assert {
    condition     = length(aws_vpc_security_group_egress_rule.all_ipv4) == 1 && aws_vpc_security_group_egress_rule.all_ipv4[0].ip_protocol == "-1" && aws_vpc_security_group_egress_rule.all_ipv4[0].cidr_ipv4 == "0.0.0.0/0"
    error_message = "The egress rule must allow all IPv4 traffic."
  }

  assert {
    condition     = output.store_url == "s3://ftg-data-0123abcd/" && output.bucket_name == "ftg-data-0123abcd" && output.region == "us-east-1"
    error_message = "The outputs must give the bucket, the store URL, and the Region."
  }
}

run "no_ssm_and_custom_prefix" {
  command = apply

  variables {
    name_prefix  = "demo"
    enable_ssm   = false
    targets_file = "tests/fixtures/demo_run.yaml"
    region       = "us-west-2"
  }

  assert {
    condition     = length(aws_iam_role_policy_attachment.ssm) == 0
    error_message = "enable_ssm = false must not attach the SSM policy."
  }

  assert {
    condition     = aws_iam_role.prober.name == "demo-prober" && aws_security_group.prober[0].name == "demo-prober"
    error_message = "name_prefix must name the role and the security group."
  }

  assert {
    condition     = strcontains(aws_instance.prober.user_data, "REGION=\"us-west-2\"")
    error_message = "The user data must carry the Region."
  }
}

run "custom_targets_file_is_read_on_the_vm" {
  command = apply

  variables {
    targets_file = "examples/targets.example.yaml"
  }

  assert {
    condition     = strcontains(aws_instance.prober.user_data, "ExecStart=/opt/ftg/current/venv/bin/ftg-prober run --provider aws --config /opt/ftg/current/config/targets.example.yaml --store s3://ftg-data-0123abcd/")
    error_message = "The unit must read the file name of targets_file, not a hardcoded targets.yaml, and must run /opt/ftg/current (ADR 0012)."
  }
}

# a config-only change (new zip hashes) must not change the rendered user_data
# (ADR 0012 section 3): the hashes reach the VM only through manifest.json.
run "user_data_holds_no_code_hash" {
  command = apply

  assert {
    condition = alltrue([
      !strcontains(aws_instance.prober.user_data, data.archive_file.prober.output_sha256),
      !strcontains(aws_instance.prober.user_data, data.archive_file.config.output_sha256),
      !strcontains(aws_instance.prober.user_data, aws_s3_object.prober.key),
      !strcontains(aws_instance.prober.user_data, aws_s3_object.config.key),
      !strcontains(aws_instance.prober.user_data, aws_s3_object.sync_script.key),
      strcontains(aws_s3_object.manifest.content, data.archive_file.prober.output_sha256),
    ])
    error_message = "A code, config, or sync.sh change must reach the VM only through manifest.json, never through user_data."
  }
}

run "user_data_unchanged_by_new_zip_hashes" {
  command = apply

  override_data {
    target = data.archive_file.prober
    values = {
      output_sha256 = "2222"
      output_md5    = "bbbb"
    }
  }

  override_data {
    target = data.archive_file.config
    values = {
      output_sha256 = "3333"
      output_md5    = "dddd"
    }
  }

  assert {
    condition = alltrue([
      !strcontains(aws_instance.prober.user_data, "2222"),
      !strcontains(aws_instance.prober.user_data, "3333"),
      strcontains(aws_s3_object.manifest.content, "prober-2222.zip"),
      strcontains(aws_s3_object.manifest.content, "config-3333.zip"),
    ])
    error_message = "New zip hashes must change manifest.json and leave user_data without them."
  }
}

# a new Canonical image changes the SSM parameter, and the instance keeps the AMI it
# was created with. The state of the earlier runs (ami-0test) carries to this run. The mock
# provider applies an in-place update, so a missing ignore_changes shows as ami-1new here (a
# replacement would also give ami-1new). The plan must also not replace the instance.
run "new_image_does_not_change_the_instance" {
  command = apply

  override_data {
    target = data.aws_ssm_parameter.ubuntu
    values = {
      insecure_value = "ami-1new"
    }
  }

  assert {
    condition     = aws_instance.prober.ami == "ami-0test"
    error_message = "A new value of the AMI parameter must not change the AMI of the existing instance."
  }
}

# the prober runs as the unprivileged ftg user, and the sync unit has its start
# timeout and its revert outside ExecStart (spec 0003 section 6A).
run "units_in_user_data" {
  command = apply

  assert {
    condition = alltrue([
      strcontains(aws_instance.prober.user_data, "User=ftg"),
      !strcontains(aws_instance.prober.user_data, "ReadWritePaths"),
      strcontains(aws_instance.prober.user_data, "TimeoutStartSec=3600"),
      strcontains(aws_instance.prober.user_data, "ExecStopPost=/bin/bash -c"),
      strcontains(aws_instance.prober.user_data, "\"$$SERVICE_RESULT\""),
    ])
    error_message = "user_data must hold User=ftg with no ReadWritePaths, the sync unit's TimeoutStartSec, and its ExecStopPost revert."
  }
}

# ADR 0025: the compaction grant is an in-place update of the role policy and the units
# are installed by sync.sh, so neither changes the instance. user_data is the only input whose
# change replaces the instance (user_data_replace_on_change), and it must name neither the new
# statements nor the new units. The role policy keeps its address and name, so it is not replaced.
run "compaction_changes_no_instance_input" {
  command = apply

  assert {
    condition = alltrue([
      !strcontains(aws_instance.prober.user_data, "ftg-compact"),
      !strcontains(aws_instance.prober.user_data, "ForCompaction"),
      !strcontains(aws_instance.prober.user_data, "daily/"),
      !strcontains(aws_instance.prober.user_data, aws_iam_role_policy.prober.policy),
    ])
    error_message = "The compaction units and grants must not appear in user_data: a user_data change replaces the instance (ADR 0025)."
  }

  assert {
    condition     = aws_iam_role_policy.prober.name == "ftg-prober" && aws_iam_role_policy.prober.role == aws_iam_role.prober.id && aws_iam_role.prober.name == "ftg-prober"
    error_message = "The compaction grant must change the existing inline policy of the existing role, not add a replacement (ADR 0025)."
  }

  assert {
    condition     = aws_instance.prober.iam_instance_profile == aws_iam_instance_profile.prober.name
    error_message = "The instance keeps its instance profile."
  }
}

run "allocation_probe_iam_is_off_by_default" {
  command = apply

  assert {
    condition     = length(jsondecode(aws_iam_role_policy.prober.policy).Statement) == 6
    error_message = "grant_billable_provisioning defaults to false, so the role policy must have exactly the 6 base statements (ADR 0012 adds DenyWriteCode; ADR 0025 adds the 2 compaction read statements)."
  }
}

# The targets file plays no part in this decision (ADR 0015): an active, consented entry with
# the grant still false must not add the 5 allocation statements.
run "allocation_probe_iam_stays_off_with_an_active_consented_entry" {
  command = apply

  variables {
    targets_file = "../../prober/tests/fixtures/provisioning_guard_aws_allowed.yaml"
  }

  assert {
    condition     = length(jsondecode(aws_iam_role_policy.prober.policy).Statement) == 6
    error_message = "grant_billable_provisioning defaults to false, so the role policy must stay at the 6 base statements (ADR 0012 adds DenyWriteCode; ADR 0025 the compaction reads), even with an active, consented aws.provisioning_probes entry in the file."
  }
}

run "allocation_probe_iam" {
  command = apply

  variables {
    grant_billable_provisioning = true
    targets_file                = "../../prober/tests/fixtures/provisioning_guard_aws_allowed.yaml"
  }

  assert {
    condition     = length(jsondecode(aws_iam_role_policy.prober.policy).Statement) == 11
    error_message = "grant_billable_provisioning = true must add the 5 provisioning probe statements to the 4 base statements (ADR 0012 adds DenyWriteCode), with a provisioning_probes entry that names a Region (ADR 0015; the no-entry case is its own run)."
  }

  assert {
    condition = [
      for s in jsondecode(aws_iam_role_policy.prober.policy).Statement : s.Action if s.Sid == "CreateAllocationProbeReservation"
    ][0] == ["ec2:CreateCapacityReservation"]
    error_message = "The create statement must allow only CreateCapacityReservation."
  }

  assert {
    condition = [
      for s in jsondecode(aws_iam_role_policy.prober.policy).Statement : s.Condition if s.Sid == "CreateAllocationProbeReservation"
      ][0] == {
      StringEquals = {
        "aws:RequestTag/experiment" = "ftg-20261014-example"
        "ec2:EndDateType"           = "limited"
        "aws:RequestedRegion"       = ["us-east-1"]
      }
    }
    error_message = "The create statement must require the experiment request tag, a limited EndDateType, and a Region of the provisioning_probes entries."
  }

  assert {
    condition = [
      for s in jsondecode(aws_iam_role_policy.prober.policy).Statement : s.Action if s.Sid == "TagAllocationProbeReservationOnCreate"
    ][0] == ["ec2:CreateTags"]
    error_message = "The tag-on-create statement must allow only CreateTags."
  }

  assert {
    condition = [
      for s in jsondecode(aws_iam_role_policy.prober.policy).Statement : s.Condition if s.Sid == "TagAllocationProbeReservationOnCreate"
      ][0] == {
      StringEquals = {
        "ec2:CreateAction"          = "CreateCapacityReservation"
        "aws:RequestTag/experiment" = "ftg-20261014-example"
        "aws:RequestedRegion"       = ["us-east-1"]
      }
    }
    error_message = "The tag-on-create statement must restrict CreateTags to a CreateCapacityReservation call, require the experiment request tag, and require a Region of the provisioning_probes entries."
  }

  assert {
    condition = [
      for s in jsondecode(aws_iam_role_policy.prober.policy).Statement : s.Action if s.Sid == "CancelAllocationProbeReservation"
    ][0] == ["ec2:CancelCapacityReservation"]
    error_message = "The cancel statement must allow only ec2:CancelCapacityReservation."
  }

  assert {
    condition = [
      for s in jsondecode(aws_iam_role_policy.prober.policy).Statement : s.Condition if s.Sid == "CancelAllocationProbeReservation"
      ][0] == {
      StringEquals = {
        "aws:ResourceTag/experiment" = "ftg-20261014-example"
        "aws:RequestedRegion"        = ["us-east-1"]
      }
    }
    error_message = "The cancel statement must require the experiment resource tag and a Region of the provisioning_probes entries."
  }

  assert {
    condition = join(",", sort(flatten([
      for s in jsondecode(aws_iam_role_policy.prober.policy).Statement : s.Action if s.Sid == "ReadAllocationProbeCapacity"
    ]))) == "ec2:DescribeAvailabilityZones,ec2:DescribeCapacityReservations,ec2:DescribeInstanceTypeOfferings,ec2:DescribeInstanceTypes"
    error_message = "The read statement must allow exactly the 4 describe actions, with no condition."
  }

  assert {
    condition = join(",", sort(flatten([
      for s in jsondecode(aws_iam_role_policy.prober.policy).Statement : s.Action if s.Sid == "ReadAllocationProbeQuota"
    ]))) == "servicequotas:GetServiceQuota"
    error_message = "The quota statement must allow exactly servicequotas:GetServiceQuota."
  }
}

# the create, tag, and cancel statements name every provisioning_probes Region, sorted
# and without duplicates, and skip an entry that has no region.
run "allocation_probe_iam_pins_the_provisioning_regions" {
  command = apply

  variables {
    grant_billable_provisioning = true
    targets_file                = "tests/fixtures/sweeper_regions.yaml"
  }

  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.prober.policy).Statement :
      s.Condition.StringEquals["aws:RequestedRegion"] == ["eu-west-1", "us-west-2"]
      if contains(["CreateAllocationProbeReservation", "TagAllocationProbeReservationOnCreate", "CancelAllocationProbeReservation"], s.Sid)
    ])
    error_message = "Each mutating provisioning statement must carry aws:RequestedRegion = the provisioning_probes Regions."
  }
}

# with the grant and no provisioning_probes entry, there is no Region to pin. The
# create, tag, and cancel statements are absent (no empty aws:RequestedRegion list); the 2 reads stay.
run "allocation_probe_iam_has_no_mutating_statement_without_an_entry" {
  command = apply

  variables {
    grant_billable_provisioning = true
    targets_file                = "tests/fixtures/demo_run.yaml"
  }

  assert {
    condition = length([
      for s in jsondecode(aws_iam_role_policy.prober.policy).Statement : s
      if contains(["CreateAllocationProbeReservation", "TagAllocationProbeReservationOnCreate", "CancelAllocationProbeReservation"], s.Sid)
    ]) == 0
    error_message = "With no aws.provisioning_probes entry, the create, tag, and cancel statements must be absent."
  }

  assert {
    condition     = length(jsondecode(aws_iam_role_policy.prober.policy).Statement) == 8
    error_message = "With the grant and no entry, the role policy must hold the 6 base statements and the 2 read statements."
  }
}

run "rejects_upper_case_prefix" {
  command = plan

  variables {
    name_prefix = "FTG"
  }

  expect_failures = [var.name_prefix]
}

run "rejects_reserved_s3_prefix" {
  command = plan

  variables {
    name_prefix = "sthree-ftg"
  }

  expect_failures = [var.name_prefix]
}

run "vpc_id_rejects_empty" {
  command = plan

  variables {
    vpc_id = ""
  }

  expect_failures = [var.vpc_id]
}

run "vpc_id_rejects_a_subnet_id" {
  command = plan

  variables {
    vpc_id = "subnet-0123456789abcdef0"
  }

  expect_failures = [var.vpc_id]
}

run "subnet_id_rejects_empty" {
  command = plan

  variables {
    subnet_id = ""
  }

  expect_failures = [var.subnet_id]
}

run "subnet_id_rejects_a_vpc_id" {
  command = plan

  variables {
    subnet_id = "vpc-0123456789abcdef0"
  }

  expect_failures = [var.subnet_id]
}

run "subnet_in_another_vpc_fails" {
  command = plan

  variables {
    vpc_id = "vpc-0aaaaaaaaaaaaaaaa"
  }

  expect_failures = [aws_instance.prober]
}

run "subnet_zone_without_the_type_fails" {
  command = plan

  override_data {
    target = data.aws_ec2_instance_type_offerings.prober
    values = {
      locations = ["us-east-1c"]
    }
  }

  expect_failures = [aws_instance.prober]
}

run "s3_endpoint_is_off_by_default" {
  command = plan

  assert {
    condition     = length(aws_vpc_endpoint.s3) == 0
    error_message = "The stack must create no S3 endpoint by default."
  }
}

run "s3_endpoint_on_uses_the_subnet_route_table" {
  command = plan

  variables {
    enable_s3_gateway_endpoint = true
  }

  assert {
    condition     = length(aws_vpc_endpoint.s3) == 1
    error_message = "The stack must create 1 S3 endpoint."
  }

  assert {
    condition     = aws_vpc_endpoint.s3[0].service_name == "com.amazonaws.us-east-1.s3" && aws_vpc_endpoint.s3[0].vpc_endpoint_type == "Gateway"
    error_message = "The endpoint must be a gateway endpoint for S3 in the Region."
  }

  assert {
    condition     = aws_vpc_endpoint.s3[0].vpc_id == var.vpc_id
    error_message = "The endpoint must be in vpc_id."
  }

  assert {
    condition     = aws_vpc_endpoint.s3[0].route_table_ids == toset(["rtb-0aaaaaaaaaaaaaaaa"])
    error_message = "The endpoint must join the route table of the subnet."
  }
}

run "s3_endpoint_falls_back_to_the_main_route_table" {
  command = plan

  variables {
    enable_s3_gateway_endpoint = true
    region                     = "us-west-2"
  }

  override_data {
    target = data.aws_route_tables.subnet
    values = {
      ids = []
    }
  }

  assert {
    condition     = aws_vpc_endpoint.s3[0].route_table_ids == toset(["rtb-0bbbbbbbbbbbbbbbb"])
    error_message = "A subnet with no own route table must use the main route table."
  }

  assert {
    condition     = aws_vpc_endpoint.s3[0].service_name == "com.amazonaws.us-west-2.s3"
    error_message = "The service name must use the Region."
  }
}

run "code_objects_set_empty_tags_and_metadata" {
  command = plan

  assert {
    condition = alltrue([
      aws_s3_object.prober.tags != null && length(aws_s3_object.prober.tags) == 0,
      aws_s3_object.prober.metadata != null && length(aws_s3_object.prober.metadata) == 0,
      aws_s3_object.config.tags != null && length(aws_s3_object.config.tags) == 0,
      aws_s3_object.config.metadata != null && length(aws_s3_object.config.metadata) == 0,
    ])
    error_message = "Both code objects must set tags and metadata to empty maps."
  }
}

run "proxy_option_is_off_by_default" {
  command = plan

  assert {
    condition = alltrue([
      !strcontains(aws_instance.prober.user_data, "PROXY_URL"),
      !strcontains(aws_instance.prober.user_data, "99proxy"),
      !strcontains(aws_instance.prober.user_data, "proxy.conf"),
      !strcontains(aws_instance.prober.user_data, "amazon-ssm-agent"),
      !strcontains(aws_instance.prober.user_data, "%%{"),
      strcontains(aws_instance.prober.user_data, "# 1. System packages"),
    ])
    error_message = "With the defaults, the user data must hold no proxy and no drop-in line."
  }
}

run "proxy_url_sets_apt_env_and_dropin" {
  command = plan

  variables {
    egress_proxy_url = "http://proxy.example.internal:80"
    no_proxy         = [".example.internal"]
  }

  assert {
    condition = alltrue([
      strcontains(aws_instance.prober.user_data, "PROXY_URL=\"http://proxy.example.internal:80\""),
      strcontains(aws_instance.prober.user_data, "NO_PROXY_LIST=\"169.254.169.254,localhost,127.0.0.1,.example.internal\""),
      strcontains(aws_instance.prober.user_data, "/etc/apt/apt.conf.d/99proxy"),
      strcontains(aws_instance.prober.user_data, "Acquire::http::Proxy \"$PROXY_URL\";"),
      strcontains(aws_instance.prober.user_data, "Acquire::https::Proxy \"$PROXY_URL\";"),
      strcontains(aws_instance.prober.user_data, "export HTTP_PROXY=\"$PROXY_URL\" HTTPS_PROXY=\"$PROXY_URL\" NO_PROXY=\"$NO_PROXY_LIST\""),
      strcontains(aws_instance.prober.user_data, "export http_proxy=\"$PROXY_URL\" https_proxy=\"$PROXY_URL\" no_proxy=\"$NO_PROXY_LIST\""),
      strcontains(aws_instance.prober.user_data, "/etc/systemd/system/ftg-prober.service.d/proxy.conf"),
      strcontains(aws_instance.prober.user_data, "\"HTTPS_PROXY=$PROXY_URL\""),
    ])
    error_message = "The proxy URL must reach the apt file, the environment, and the unit drop-in, and NO_PROXY must hold the fixed hosts and var.no_proxy."
  }

  assert {
    condition = alltrue([
      !strcontains(aws_instance.prober.user_data, "/etc/environment"),
      !strcontains(aws_instance.prober.user_data, ",.amazonaws.com"),
    ])
    error_message = "The proxy alone must not write /etc/environment or bypass the AWS endpoints."
  }
}

run "proxy_url_sets_ssm_agent_dropin" {
  command = plan
  variables {
    egress_proxy_url = "http://proxy.example.internal:80"
    no_proxy         = [".example.internal"]
  }
  assert {
    condition = alltrue([
      strcontains(aws_instance.prober.user_data, "/etc/systemd/system/snap.amazon-ssm-agent.amazon-ssm-agent.service.d/override.conf"),
      strcontains(aws_instance.prober.user_data, "Environment=\"http_proxy=$PROXY_URL\""),
      strcontains(aws_instance.prober.user_data, "Environment=\"https_proxy=$PROXY_URL\""),
      strcontains(aws_instance.prober.user_data, "Environment=\"no_proxy=$NO_PROXY_LIST\""),
      strcontains(aws_instance.prober.user_data, "NO_PROXY_LIST=\"169.254.169.254,"),
      strcontains(aws_instance.prober.user_data, "systemctl restart snap.amazon-ssm-agent.amazon-ssm-agent.service"),
    ])
    error_message = "With a proxy, the user data must write the SSM agent drop-in and restart the agent."
  }
}

run "rejects_a_bad_proxy_url" {
  command = plan

  variables {
    egress_proxy_url = "proxy.example.internal"
  }

  expect_failures = [var.egress_proxy_url]
}

run "rejects_a_proxy_url_with_a_path" {
  command = plan

  variables {
    egress_proxy_url = "http://proxy.example.internal:80/x"
  }

  expect_failures = [var.egress_proxy_url]
}

run "rejects_a_proxy_port_of_zero" {
  command = plan

  variables {
    egress_proxy_url = "http://proxy.example.internal:0"
  }

  expect_failures = [var.egress_proxy_url]
}

run "rejects_a_proxy_port_above_65535" {
  command = plan

  variables {
    egress_proxy_url = "http://proxy.example.internal:70000"
  }

  expect_failures = [var.egress_proxy_url]
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

run "ipv6_vpc_adds_the_ipv6_egress_rule" {
  command = plan

  override_data {
    target = data.aws_vpc.prober
    values = {
      ipv6_cidr_block_associations = [{
        association_id         = "vpc-cidr-assoc-0123456789abcdef0"
        ip_source              = "amazon"
        ipv6_address_attribute = "public"
        ipv6_cidr_block        = "2001:db8::/56"
        ipv6_pool              = ""
        network_border_group   = "us-east-1"
        state                  = "associated"
      }]
    }
  }

  assert {
    condition     = length(aws_vpc_security_group_egress_rule.all_ipv6) == 1 && aws_vpc_security_group_egress_rule.all_ipv6[0].ip_protocol == "-1" && aws_vpc_security_group_egress_rule.all_ipv6[0].cidr_ipv6 == "::/0"
    error_message = "A VPC with an IPv6 CIDR must get 1 all-traffic IPv6 egress rule."
  }

  assert {
    condition     = length(aws_vpc_security_group_egress_rule.all_ipv4) == 1
    error_message = "The IPv4 rule must stay."
  }
}

run "supplied_groups_join_the_created_one" {
  command = plan

  variables {
    vpc_security_group_ids = ["sg-0123456789abcdef0", "sg-0fedcba9876543210"]
  }

  assert {
    condition     = length(aws_security_group.prober) == 1 && length(aws_instance.prober.vpc_security_group_ids) == 3 && contains(aws_instance.prober.vpc_security_group_ids, "sg-0123456789abcdef0") && contains(aws_instance.prober.vpc_security_group_ids, "sg-0fedcba9876543210")
    error_message = "The VM must have the created group and the 2 given groups."
  }
}

run "no_created_group_uses_only_the_given_groups" {
  command = plan

  variables {
    create_security_group  = false
    vpc_security_group_ids = ["sg-0123456789abcdef0"]
  }

  assert {
    condition     = length(aws_security_group.prober) == 0 && length(aws_vpc_security_group_egress_rule.all_ipv4) == 0 && length(aws_vpc_security_group_egress_rule.all_ipv6) == 0
    error_message = "The stack must create no group and no rule."
  }

  assert {
    condition     = aws_instance.prober.vpc_security_group_ids == toset(["sg-0123456789abcdef0"])
    error_message = "The VM must have only the given group."
  }
}

run "no_created_group_and_no_given_group_fails" {
  command = plan

  variables {
    create_security_group = false
  }

  expect_failures = [aws_instance.prober]
}

run "rejects_a_bad_security_group_id" {
  command = plan

  variables {
    vpc_security_group_ids = ["vpc-0123456789abcdef0"]
  }

  expect_failures = [var.vpc_security_group_ids]
}

# make status/aws reads the experiment and the Regions from the outputs, so it uses the
# targets file that the stack uses.
run "outputs_follow_the_targets_file" {
  command = apply

  variables {
    targets_file = "tests/fixtures/status_regions.yaml"
  }

  assert {
    condition     = output.experiment == "ftg-status-fixture"
    error_message = "The experiment output must be the experiment of targets_file."
  }

  assert {
    condition     = output.target_regions == tolist(["ap-south-1", "eu-west-1", "us-east-2", "us-west-2"])
    error_message = "The target_regions output must be the sorted, unique Regions of jobs, provisioning_probes, and quota in targets_file."
  }
}

# An old key must fail the plan, not give an empty list. The message is
# local.targets_faults, the text of `check-config`.
run "old_section_key_fails_the_plan" {
  command = plan

  variables {
    targets_file = "tests/fixtures/old_key.yaml"
  }

  expect_failures = [terraform_data.targets_check]
}

run "repo_targets_file_passes_the_key_check" {
  command = plan

  assert {
    condition     = length(local.targets_faults) == 0
    error_message = "The repo targets file has an unknown key."
  }
}

# An empty `cadence:` or an empty `aws:` section is null, not a mapping. The key check must
# read it as empty, not crash the plan with "keys: argument must not be null".
run "null_cadence_passes_the_key_check" {
  command = plan

  variables {
    targets_file = "tests/fixtures/null_cadence.yaml"
  }

  assert {
    condition     = length(local.targets_faults) == 0
    error_message = "An empty cadence must not give a key-check fault."
  }
}

run "null_section_passes_the_key_check" {
  command = plan

  variables {
    targets_file = "tests/fixtures/null_section.yaml"
  }

  assert {
    condition     = length(local.targets_faults) == 0
    error_message = "An empty aws section must not give a key-check fault."
  }
}

run "string_section_fails_the_plan" {
  command = plan

  variables {
    targets_file = "tests/fixtures/string_section.yaml"
  }

  expect_failures = [terraform_data.targets_check]
}

run "string_cadence_fails_the_plan" {
  command = plan

  variables {
    targets_file = "tests/fixtures/string_cadence.yaml"
  }

  expect_failures = [terraform_data.targets_check]
}

# a null regions, region, or quota.regions must not fail the apply with
# "Iteration over null value".
run "null_values_give_partial_target_regions" {
  command = apply

  variables {
    targets_file = "tests/fixtures/null_values.yaml"
  }

  assert {
    condition     = output.target_regions == tolist(["us-west-2"])
    error_message = "Null regions, region, and quota.regions must count as no Region."
  }
}
