# 0024. The AWS sweeper-stopped alarm reads the built-in Lambda metrics

- Status: accepted
- Date: 2026-10-17

## Context

[Spec 0002](../spec/0002-gpu-capacity-tracker-spec.md) section 6 item 5 asks for an alert when the sweeper stops. The Azure stack has one: a log search alert on Application Insights ([spec 0005](../spec/0005-azure-stack-spec.md) section 3.6). The AWS sweeper has no CloudWatch Logs by design ([ADR 0019](0019-the-aws-sweeper-is-a-scheduled-lambda-function.md)), so that design does not carry over. Two candidates exist: a CloudWatch alarm on the `Errors` and `Invocations` metrics, and a check of the newest log object under the logs prefix.

Rules that apply:

- Decision D14 of spec 0002: "No CloudWatch or other provider-specific metrics services for telemetry." The rule of [spec 0003](../spec/0003-prober-core-spec.md) section 8.2: our code writes its logs to the store, never to CloudWatch Logs.
- Spec 0013 open item 1 names the alarm on the Lambda metrics as the candidate.

This follows spec 0013. The alarm does not break D14 or section 8.2: it publishes no telemetry and writes no log. It reads two metrics that Lambda sends by itself, and Lambda sends them without the CloudWatch Logs permissions that the role lacks. The data of the experiment still goes only to the store.

## Decision

1. **One metric alarm, `<name_prefix>-sweeper-stopped`,** on the expression `FILL(invocations, 0) - FILL(errors, 0)`, with `invocations` and `errors` the `Sum` of `AWS/Lambda` `Invocations` and `Errors` of the sweeper function, period 60 s. It fires when the value is below 1 in 10 of 10 periods: no run succeeded for 10 minutes. Missing data counts as breaching.
2. **Notification:** one SNS topic `<name_prefix>-alerts` with one email subscription to the new variable `alert_email`. The alarm notifies on `ALARM` and on `OK`. With `alert_email` empty, the stack makes no topic, no subscription, and no alarm, the same as the Azure stack.
3. **No extra permission** on the sweeper role, and no topic policy: the alarm and the topic are in the same account.

## Options

| Option | Result |
|---|---|
| Alarm on `Errors` only | Rejected. A stopped schedule, a disabled function, or a throttle gives no `Errors` data point, so the alarm would stay quiet. |
| Alarm on `Invocations` only | Rejected. A function that runs and fails every minute would not fire. |
| Alarm on `Invocations - Errors`, 10 of 10 periods | Chosen. It covers a stopped schedule, a throttle, and a failing function with one alarm. Both metrics are free built-in Lambda metrics. |
| Check the newest `_ftg-sweeper_` log object | Rejected. It needs a second scheduled component that can itself stop, and it reads the store. |

## Sources

- Lambda invocation metrics: "`Invocations` ... aren't recorded if the invocation request is throttled or otherwise results in an invocation error"; "`Errors` – The number of invocations that result in a function error" ([types of metrics](https://docs.aws.amazon.com/lambda/latest/dg/monitoring-metrics-types.html)).
- Missing data: `breaching` – "Missing data points are treated as 'bad' and breaching the threshold" ([missing data](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/alarms-and-missing-data.html)).
- M of N evaluation and the 1-minute evaluation interval ([alarm evaluation](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/alarm-evaluation.html)).
- A metric alarm "watches a single CloudWatch metric or the result of a math expression based on CloudWatch metrics" ([using alarms](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Alarms.html)).

## Consequences

- The next AWS apply with `alert_email` set adds 3 resources: the topic, the subscription, and the alarm. The owner of the address must confirm the subscription from the email that SNS sends ([SNS email notifications](https://docs.aws.amazon.com/sns/latest/dg/sns-email-notifications.html)); until then no email arrives.
- Differences from the Azure rule: the alarm fires when no run succeeds for 10 minutes. It does not fire on a single failed run among successful ones, because the next run starts a minute later. A run that fails on one Region logs the health line and fails the invocation, so a Region that fails every run for 10 minutes does fire.
- A new alarm probably goes to `ALARM` within minutes of its creation, not to `INSUFFICIENT_DATA`. The docs say that when all data points are missing and missing data is `breaching`, the alarm goes to `ALARM` ([missing data](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/alarms-and-missing-data.html)); with `FILL(..., 0)` the 10 empty periods become 0 and breach. The Note in the `FILL` section of the metric-math doc covers a missing latest minute and names the M-of-N alarm as the workaround ([metric math](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/using-metric-math.html)); it does not say how FILL behaves for a metric with no data at all, so the first state is unverified until the first deploy. After the first successful runs the alarm goes to `OK`.
- A pending email subscription does not receive topic messages until its owner confirms it ([Amazon SNS email notifications](https://docs.aws.amazon.com/sns/latest/dg/sns-email-notifications.html): "You must confirm the subscription before the email address can start to receive messages"). SNS sends the confirmation request to the address when the subscription is created, and deletes an unconfirmed subscription after 48 hours. Confirm it right after the apply. After that, the alarm emails on ALARM and on OK (`alarm_actions` and `ok_actions` in `terraform/aws/alerts.tf`).
- The first deploy also confirms the first state and that a test notification arrives.
- A run in which any cancel failed ends as a failed invocation, so the alarm counts it. A single failed minute (for example a race with the probe's own cancel) does not fire the 10-of-10 alarm.
