# The archive provider runs for real here (not mocked), so the zips are built from the real
# ../../prober and ../../config folders. This lets scripts/check-stale-build-artifact.sh plant a
# stale file at the old dist/ output path and show that a real plan never reads or writes it.
# Run: make test/aws

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

run "zip_builds_outside_dist" {
  command = plan

  assert {
    condition     = !strcontains(data.archive_file.prober.output_path, "/dist/") && !strcontains(data.archive_file.config.output_path, "/dist/") && !strcontains(data.archive_file.sweeper.output_path, "/dist/")
    error_message = "The zips must not land under dist/: a tool that re-copies the checkout between plan and apply can overwrite a file there with a stale one, so the uploaded bytes would not match the planned hash key."
  }

  assert {
    condition     = strcontains(data.archive_file.prober.output_path, "/.terraform/") && strcontains(data.archive_file.config.output_path, "/.terraform/") && strcontains(data.archive_file.sweeper.output_path, "/.terraform/")
    error_message = "The zips must land under .terraform/, which a copy of the checkout does not reach."
  }

  assert {
    condition     = basename(data.archive_file.config.source_dir) == "config"
    error_message = "The default targets_file (../../config/targets.yaml) must zip the repo's config/ folder, unchanged from before targets_file existed."
  }
}

run "custom_targets_file_zips_its_own_folder" {
  command = plan

  variables {
    targets_file = "examples/targets.example.yaml"
  }

  assert {
    condition     = basename(data.archive_file.config.source_dir) == "examples"
    error_message = "A custom targets_file must zip its own folder, not the repo's config/ folder."
  }
}
