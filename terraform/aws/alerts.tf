# "Sweeper stopped" alarm: spec 0002 section 6A item 5, spec 0013 section 13.
# It reads the built-in CloudWatch metrics that Lambda publishes for every function: a metric, not a
# log, so it does not touch the rule that our logs go to the store. The alarm fires when no sweeper
# run succeeds for 10 minutes. There is no alarm without an alert email, the same as the Azure stack.

locals {
  alerts_enabled = var.alert_email != ""
}

resource "aws_sns_topic" "alerts" {
  count = local.alerts_enabled ? 1 : 0
  name  = "${var.name_prefix}-alerts"
}

# The address stays "pending confirmation" until its owner follows the link in the email that SNS
# sends. https://docs.aws.amazon.com/sns/latest/dg/sns-email-notifications.html
resource "aws_sns_topic_subscription" "alert_email" {
  count     = local.alerts_enabled ? 1 : 0
  topic_arn = aws_sns_topic.alerts[0].arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Successful runs per minute = Invocations - Errors. Lambda sends no Invocations data point for a
# minute with no invocation (a stopped schedule, a disabled function, a throttle), so FILL(.., 0)
# makes the empty minute a 0. Invocations "aren't recorded if the invocation request is throttled",
# and Errors counts "invocations that result in a function error":
# https://docs.aws.amazon.com/lambda/latest/dg/monitoring-metrics-types.html
# 10 of 10 one-minute periods below 1 is the 10 minutes of the Azure rule. Missing data counts as
# breaching, for the case that CloudWatch has no data point at all:
# https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/alarms-and-missing-data.html
# A metric alarm may watch "the result of a math expression based on CloudWatch metrics":
# https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Alarms.html
resource "aws_cloudwatch_metric_alarm" "sweeper_stopped" {
  count               = local.alerts_enabled ? 1 : 0
  alarm_name          = "${var.name_prefix}-sweeper-stopped"
  alarm_description   = "No successful sweeper run in 10 minutes. Probe resources can be left to bill."
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  evaluation_periods  = 10
  datapoints_to_alarm = 10
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts[0].arn]
  ok_actions          = [aws_sns_topic.alerts[0].arn]

  metric_query {
    id          = "successes"
    expression  = "FILL(invocations, 0) - FILL(errors, 0)"
    label       = "Successful sweeper runs"
    return_data = true
  }

  metric_query {
    id = "invocations"
    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Invocations"
      period      = 60
      stat        = "Sum"
      dimensions  = { FunctionName = aws_lambda_function.sweeper.function_name }
    }
  }

  metric_query {
    id = "errors"
    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Errors"
      period      = 60
      stat        = "Sum"
      dimensions  = { FunctionName = aws_lambda_function.sweeper.function_name }
    }
  }
}
