# Tests of the AWS sweeper (spec 0013, ADR 0019) with mock providers. They pin the IAM scope (the
# tag condition, the Regions, no logs:*), the schedule, and the Regions. Run: make test/aws

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

  # Not a real account ID.
  override_data {
    target = data.aws_caller_identity.current
    values = {
      account_id = "test-account"
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
    target = aws_sns_topic.alerts
    values = {
      arn = "arn:aws:sns:us-east-1:aws:ftg-alerts"
    }
  }

  override_resource {
    target = aws_lambda_function.sweeper
    values = {
      arn = "arn:aws:lambda:us-east-1:aws:function:ftg-sweeper"
    }
  }
}

mock_provider "archive" {}

mock_provider "random" {
  override_resource {
    target = random_id.bucket_suffix
    values = {
      hex = "0123abcd"
    }
  }
}

variables {
  vpc_id       = "vpc-0123456789abcdef0"
  subnet_id    = "subnet-0123456789abcdef0"
  targets_file = "tests/fixtures/sweeper_regions.yaml"
}

run "sweeper_function_schedule_and_regions" {
  command = apply

  assert {
    condition     = aws_lambda_function.sweeper.runtime == "python3.12" && aws_lambda_function.sweeper.handler == "handler.lambda_handler" && aws_lambda_function.sweeper.timeout == 120
    error_message = "The sweeper must run handler.lambda_handler on python3.12 with a 120 s timeout (spec 0013 section 3)."
  }

  assert {
    condition     = length(aws_lambda_function.sweeper.vpc_config) == 0
    error_message = "The sweeper must run outside the VPC: the VPC subnets have no internet path (ADR 0019)."
  }

  assert {
    condition = aws_lambda_function.sweeper.environment[0].variables == tomap({
      FTG_EXPERIMENT      = "ftg-20261015-sweeper"
      FTG_REGIONS         = "eu-west-1,us-east-1,us-west-2"
      FTG_DATA_BUCKET     = "ftg-data-0123abcd"
      FTG_MAX_AGE_SECONDS = "180"
    })
    error_message = "The sweeper environment must hold the file's experiment, the sorted Regions of its jobs and provisioning_probes plus the stack Region, the data bucket, and 180 s."
  }

  assert {
    condition     = aws_scheduler_schedule.sweeper.schedule_expression == "rate(1 minute)" && aws_scheduler_schedule.sweeper.flexible_time_window[0].mode == "OFF"
    error_message = "The schedule must start the sweeper every minute, with no flexible time window."
  }

  assert {
    condition     = aws_scheduler_schedule.sweeper.target[0].arn == aws_lambda_function.sweeper.arn && aws_scheduler_schedule.sweeper.target[0].role_arn == aws_iam_role.sweeper_scheduler.arn
    error_message = "The schedule must target the sweeper function with its own role."
  }

  assert {
    condition     = aws_scheduler_schedule.sweeper.target[0].retry_policy[0].maximum_retry_attempts == 0
    error_message = "A failed delivery must not be retried: the next run starts 1 minute later."
  }

  assert {
    condition = jsondecode(aws_iam_role_policy.sweeper_scheduler.policy).Statement == [{
      Sid      = "InvokeSweeper"
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = "arn:aws:lambda:us-east-1:aws:function:ftg-sweeper"
    }]
    error_message = "The scheduler role must invoke the sweeper function only."
  }

  assert {
    condition     = jsondecode(aws_iam_role.sweeper.assume_role_policy).Statement[0].Principal.Service == "lambda.amazonaws.com"
    error_message = "Only Lambda may assume the sweeper role."
  }
}

run "sweeper_iam_scope" {
  command = apply

  assert {
    condition     = [for s in jsondecode(aws_iam_role_policy.sweeper.policy).Statement : s.Sid] == ["ListReservations", "CancelExperimentReservations", "WriteLogs"]
    error_message = "The sweeper role must have exactly 3 statements: list, cancel, and write logs."
  }

  assert {
    condition = alltrue(flatten([
      for p in [aws_iam_role_policy.sweeper.policy, aws_iam_role_policy.sweeper_scheduler.policy] : [
        for s in jsondecode(p).Statement : [for a in flatten([s.Action]) : !startswith(lower(a), "logs:")]
      ]
    ]))
    error_message = "No sweeper policy may grant a logs:* action: the sweeper writes its log to the store, never to CloudWatch Logs."
  }

  assert {
    condition = [for s in jsondecode(aws_iam_role_policy.sweeper.policy).Statement : s if s.Sid == "CancelExperimentReservations"][0] == {
      Sid    = "CancelExperimentReservations"
      Effect = "Allow"
      Action = "ec2:CancelCapacityReservation"
      Resource = [
        "arn:aws:ec2:eu-west-1:test-account:capacity-reservation/*",
        "arn:aws:ec2:us-east-1:test-account:capacity-reservation/*",
        "arn:aws:ec2:us-west-2:test-account:capacity-reservation/*",
      ]
      Condition = { StringEquals = { "aws:ResourceTag/experiment" = "ftg-20261015-sweeper" } }
    }
    error_message = "The cancel statement must allow only CancelCapacityReservation, on the reservations of the targets file's Regions, with the experiment resource tag."
  }

  assert {
    condition = [for s in jsondecode(aws_iam_role_policy.sweeper.policy).Statement : s if s.Sid == "ListReservations"][0] == {
      Sid       = "ListReservations"
      Effect    = "Allow"
      Action    = "ec2:DescribeCapacityReservations"
      Resource  = "*"
      Condition = { StringEquals = { "ec2:Region" = ["eu-west-1", "us-east-1", "us-west-2"] } }
    }
    error_message = "The list statement must allow only DescribeCapacityReservations, in the targets file's Regions."
  }

  assert {
    condition = [for s in jsondecode(aws_iam_role_policy.sweeper.policy).Statement : [s.Action, s.Resource] if s.Sid == "WriteLogs"][0] == [
      "s3:PutObject", "arn:aws:s3:::ftg-data-test/experiment=ftg-20261015-sweeper/logs/*"
    ]
    error_message = "The sweeper may write only under the logs prefix of the experiment."
  }
}

# The grant controls creation, never deletion (ADR 0015 section 1 item 3a): the sweeper and its
# cancel right are deployed with grant_billable_provisioning false and true alike.
run "sweeper_is_deployed_without_the_grant" {
  command = apply

  variables {
    grant_billable_provisioning = false
  }

  assert {
    condition     = aws_lambda_function.sweeper.function_name == "ftg-sweeper" && length([for s in jsondecode(aws_iam_role_policy.sweeper.policy).Statement : s if s.Sid == "CancelExperimentReservations"]) == 1
    error_message = "The sweeper and its cancel right must exist with grant_billable_provisioning = false."
  }
}

run "sweeper_is_deployed_with_the_grant" {
  command = apply

  variables {
    grant_billable_provisioning = true
  }

  assert {
    condition     = aws_lambda_function.sweeper.function_name == "ftg-sweeper" && length([for s in jsondecode(aws_iam_role_policy.sweeper.policy).Statement : s if s.Sid == "CancelExperimentReservations"]) == 1
    error_message = "The sweeper and its cancel right must exist with grant_billable_provisioning = true."
  }
}

run "a_file_with_no_aws_regions_gives_the_stack_region" {
  command = plan

  variables {
    targets_file = "tests/fixtures/demo_run.yaml"
    region       = "eu-central-1"
  }

  assert {
    condition     = aws_lambda_function.sweeper.environment[0].variables.FTG_REGIONS == "eu-central-1"
    error_message = "With no Region under aws in the file, the sweeper must still sweep the stack's own Region."
  }
}

run "no_alert_email_no_topic_no_alarm" {
  command = apply

  assert {
    condition     = length(aws_sns_topic.alerts) == 0 && length(aws_sns_topic_subscription.alert_email) == 0 && length(aws_cloudwatch_metric_alarm.sweeper_stopped) == 0
    error_message = "With alert_email empty the stack must make no topic, no subscription, and no alarm (spec 0004 section 4.8)."
  }

  assert {
    condition     = output.sweeper_stopped_alarm_name == null
    error_message = "The alarm output must be null without alert_email."
  }
}

run "alert_email_builds_the_sweeper_stopped_alarm" {
  command = apply

  variables {
    alert_email = "ops@example.com"
  }

  assert {
    condition     = aws_sns_topic_subscription.alert_email[0].protocol == "email" && aws_sns_topic_subscription.alert_email[0].endpoint == "ops@example.com" && aws_sns_topic_subscription.alert_email[0].topic_arn == aws_sns_topic.alerts[0].arn
    error_message = "The topic must have 1 email subscription for alert_email."
  }

  assert {
    condition     = aws_cloudwatch_metric_alarm.sweeper_stopped[0].alarm_name == "ftg-sweeper-stopped" && aws_cloudwatch_metric_alarm.sweeper_stopped[0].comparison_operator == "LessThanThreshold" && aws_cloudwatch_metric_alarm.sweeper_stopped[0].threshold == 1
    error_message = "The alarm must fire when successful runs per minute are below 1."
  }

  assert {
    condition     = aws_cloudwatch_metric_alarm.sweeper_stopped[0].evaluation_periods == 10 && aws_cloudwatch_metric_alarm.sweeper_stopped[0].datapoints_to_alarm == 10 && aws_cloudwatch_metric_alarm.sweeper_stopped[0].treat_missing_data == "breaching"
    error_message = "The alarm must need 10 of 10 one-minute periods, and treat missing data as breaching (10 minutes, spec 0013 section 13)."
  }

  assert {
    condition     = contains(aws_cloudwatch_metric_alarm.sweeper_stopped[0].alarm_actions, aws_sns_topic.alerts[0].arn) && contains(aws_cloudwatch_metric_alarm.sweeper_stopped[0].ok_actions, aws_sns_topic.alerts[0].arn)
    error_message = "The alarm must notify the topic on ALARM and on OK."
  }

  assert {
    condition = (
      length(aws_cloudwatch_metric_alarm.sweeper_stopped[0].metric_query) == 3
      && length([for q in aws_cloudwatch_metric_alarm.sweeper_stopped[0].metric_query : q if q.id == "successes" && q.return_data && q.expression == "FILL(invocations, 0) - FILL(errors, 0)"]) == 1
      && length([for q in aws_cloudwatch_metric_alarm.sweeper_stopped[0].metric_query : q if q.id == "invocations" && q.metric[0].namespace == "AWS/Lambda" && q.metric[0].metric_name == "Invocations" && q.metric[0].stat == "Sum" && q.metric[0].period == 60 && q.metric[0].dimensions.FunctionName == "ftg-sweeper"]) == 1
      && length([for q in aws_cloudwatch_metric_alarm.sweeper_stopped[0].metric_query : q if q.id == "errors" && q.metric[0].namespace == "AWS/Lambda" && q.metric[0].metric_name == "Errors" && q.metric[0].stat == "Sum" && q.metric[0].period == 60 && q.metric[0].dimensions.FunctionName == "ftg-sweeper"]) == 1
    )
    error_message = "The alarm must compute FILL(Invocations, 0) - FILL(Errors, 0) from the AWS/Lambda metrics of the sweeper function, Sum, 60 s."
  }

  assert {
    condition     = !strcontains(aws_iam_role_policy.sweeper.policy, "logs:") && output.sweeper_stopped_alarm_name == "ftg-sweeper-stopped"
    error_message = "The alarm adds no logs:* right to the sweeper role."
  }
}

run "a_bad_alert_email_fails_validation" {
  command = plan

  variables {
    alert_email = "not-an-email"
  }

  expect_failures = [var.alert_email]
}
