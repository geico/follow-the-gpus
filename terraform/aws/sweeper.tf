# The AWS sweeper (spec 0013, ADR 0019): a Lambda function that EventBridge Scheduler starts every
# minute. It cancels each Capacity Reservation with the experiment tag that is older than 180 s,
# in each Region of the targets file. It does not depend on grant_billable_provisioning: the
# grant controls creation, never deletion (ADR 0015 section 1 item 3a).

locals {
  sweeper_dir = "${path.module}/../../sweeper/aws"

  # The Regions the targets file names under aws: every job's regions and every
  # provisioning_probes entry's region, plus the stack's own Region, so the list is never empty.
  sweeper_regions = sort(distinct(concat(
    [var.region],
    [for r in flatten([for j in flatten([try(local.targets.aws.jobs, null)]) : flatten([try(j.regions, null)]) if j != null]) : r if r != null],
    [for r in flatten([for p in flatten([try(local.targets.aws.provisioning_probes, null)]) : flatten([try(p.region, null)]) if p != null]) : r if r != null],
  )))
}

# Same build folder and excludes as the prober zip (main.tf): no tests, caches, or venv.
data "archive_file" "sweeper" {
  type             = "zip"
  source_dir       = local.sweeper_dir
  output_path      = "${local.code_dir}/sweeper.zip"
  output_file_mode = "0644"
  excludes         = local.prober_excludes
}

resource "aws_iam_role" "sweeper" {
  name = "${var.name_prefix}-sweeper"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# No logs:* action, and no managed policy attachment: the function writes its own log to the
# store (spec 0013 section 9). Without the CloudWatch Logs permissions the function still runs,
# but it cannot log any output to CloudWatch Logs:
# https://docs.aws.amazon.com/lambda/latest/dg/access-logs.html
resource "aws_iam_role_policy" "sweeper" {
  name = "${var.name_prefix}-sweeper"
  role = aws_iam_role.sweeper.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # DescribeCapacityReservations has no resource type, so the resource is "*"; its only
        # condition key is ec2:Region, which keeps the call to the Regions of the targets file.
        # https://servicereference.us-east-1.amazonaws.com/v1/ec2/ec2.json
        Sid      = "ListReservations"
        Effect   = "Allow"
        Action   = "ec2:DescribeCapacityReservations"
        Resource = "*"
        Condition = {
          StringEquals = { "ec2:Region" = local.sweeper_regions }
        }
      },
      {
        # Cancel only a reservation that carries the experiment tag, in a Region of the targets
        # file. aws:ResourceTag/${TagKey} is a condition key of the capacity-reservation resource
        # of CancelCapacityReservation in the EC2 service reference (link above).
        Sid      = "CancelExperimentReservations"
        Effect   = "Allow"
        Action   = "ec2:CancelCapacityReservation"
        Resource = [for r in local.sweeper_regions : "arn:aws:ec2:${r}:${data.aws_caller_identity.current.account_id}:capacity-reservation/*"]
        Condition = {
          StringEquals = { "aws:ResourceTag/experiment" = local.experiment }
        }
      },
      {
        # The sweeper's own log objects, under the logs prefix only (spec 0003 section 8.2).
        Sid      = "WriteLogs"
        Effect   = "Allow"
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.data.arn}/experiment=${local.experiment}/logs/*"
      },
    ]
  })
}

# Outside the VPC on purpose: no vpc_config. The VPC subnets have no internet path, and a function
# attached to a VPC "can only access resources available within that VPC"; by default a function
# has access to the public internet, so it reaches the EC2 and S3 endpoints of every Region.
# https://docs.aws.amazon.com/lambda/latest/dg/configuration-vpc.html#configuration-vpc-internet-access
resource "aws_lambda_function" "sweeper" {
  function_name    = "${var.name_prefix}-sweeper"
  role             = aws_iam_role.sweeper.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.sweeper.output_path
  source_code_hash = data.archive_file.sweeper.output_base64sha256
  memory_size      = 128
  timeout          = 120

  environment {
    variables = {
      FTG_EXPERIMENT      = local.experiment
      FTG_REGIONS         = join(",", local.sweeper_regions)
      FTG_DATA_BUCKET     = aws_s3_bucket.data.bucket
      FTG_MAX_AGE_SECONDS = "180"
    }
  }

  # The role policy must exist before the first scheduled run.
  depends_on = [aws_iam_role_policy.sweeper]
}

resource "aws_iam_role" "sweeper_scheduler" {
  name = "${var.name_prefix}-sweeper-scheduler"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })
}

# https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-targets-templated.html
resource "aws_iam_role_policy" "sweeper_scheduler" {
  name = "${var.name_prefix}-sweeper-scheduler"
  role = aws_iam_role.sweeper_scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "InvokeSweeper"
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.sweeper.arn
    }]
  })
}

# EventBridge Scheduler, not a scheduled rule: AWS calls scheduled rules legacy and recommends
# Scheduler. https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-create-rule-schedule.html
resource "aws_scheduler_schedule" "sweeper" {
  name                = "${var.name_prefix}-sweeper"
  schedule_expression = "rate(1 minute)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.sweeper.arn
    role_arn = aws_iam_role.sweeper_scheduler.arn

    # The next run starts 1 minute later anyway; a retry would only overlap it.
    retry_policy {
      maximum_retry_attempts = 0
    }
  }

  depends_on = [aws_iam_role_policy.sweeper_scheduler]
}
