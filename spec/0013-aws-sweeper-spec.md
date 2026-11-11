---
title: "AWS sweeper spec"
date: 2026-10-15
---

# AWS sweeper spec

The AWS sweeper is the runaway-cost brake of [spec 0002](0002-gpu-capacity-tracker-spec.md), D9 and D10, on AWS. It is a Lambda function that EventBridge Scheduler starts every minute. It cancels each probe Capacity Reservation that is older than a threshold. It mirrors the Azure sweeper ([spec 0007](0007-sweeper-spec.md)). The decision and the options are in [ADR 0019](../decisions/0019-the-aws-sweeper-is-a-scheduled-lambda-function.md). A person must be able to build it again from this file and spec 0002.

The code lives in `sweeper/aws/`. The AWS stack ([spec 0004](0004-aws-stack-spec.md)) deploys it in `terraform/aws/sweeper.tf` and gives it its role and its schedule.

## 1. Scope

The sweeper must:

1. Run every minute, independent of the prober VM.
2. In each Region of `FTG_REGIONS`, find the Capacity Reservations with the `experiment` tag of `FTG_EXPERIMENT`.
3. Cancel each one that is older than the threshold and in a state that `CancelCapacityReservation` accepts.
4. Never cancel a reservation without the `experiment` tag of `FTG_EXPERIMENT`. The sweeper reads no other tag and no zone, so the probe's per-zone reservations (`target` = `<instance type>-<zone ID>`) are all found and cancelled by the same rule, in any zone. The query (section 5) and the age rule (section 6, check 1) both require the tag, and the role (section 10) allows a cancel only on a reservation with the tag.
5. Log 1 line per reservation, and 1 health line per run, to the store (section 9).

The only AWS resource that a probe creates is a Capacity Reservation ([spec 0002](0002-gpu-capacity-tracker-spec.md) section 4.9; the probe never calls `RunInstances`). Thus the sweeper handles 1 type, not the 4 of the Azure sweeper, and it needs no delete order and no scale set fallback.

The sweeper must not create resources, must not read the data store, and must not hold state between runs. The probe's own start-of-call cleanup (spec 0002 section 4.9) stays. The 2 are independent.

## 2. Folder layout and the deployment zip

```text
sweeper/aws/
  handler.py          # the Lambda entry point: lambda_handler(event, context)
  sweeper/            # the logic; it gets an EC2 client per Region from handler.py
    settings.py       # the environment variables
    rules.py          # the age rule
    run.py            # 1 run: list, decide, cancel, health line
    log_store.py      # the store log handler (section 9)
  tests/              # pytest with botocore Stubber; no network, no credentials
  pyproject.toml      # dev project for uv: ruff, pytest, and boto3 for the tests
  uv.lock
```

The AWS stack makes the zip with `archive_file` from `sweeper/aws/`, into `terraform/aws/.terraform/ftg-build/sweeper.zip`, with the excludes of the prober zip (`.venv`, `__pycache__`, `tests`, `.pytest_cache`, `.ruff_cache`, and the others of spec 0004 section 4.5). The zip holds `handler.py` and the `sweeper/` package at its root. The zip holds no packages: the Python runtime of Lambda includes the SDK for Python (Boto3) ([Lambda Python runtimes](https://docs.aws.amazon.com/lambda/latest/dg/lambda-python.html)). The code uses only `DescribeCapacityReservations`, `CancelCapacityReservation`, and `PutObject` with no new parameter, because the version of that included SDK "depends on the runtime version and your AWS Region" (same page).

The runtime is `python3.12` on `arm64`. The dev project pins Python to `>=3.12,<3.13`.

## 3. Trigger

| Setting | Value | Note |
|---|---|---|
| Schedule | `aws_scheduler_schedule`, `rate(1 minute)` | AWS calls scheduled rules a legacy feature and recommends EventBridge Scheduler ([scheduled rules](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-create-rule-schedule.html)). The minimum precision of a schedule is 1 minute (same page). |
| Flexible time window | `OFF` | The run starts at the scheduled minute. |
| Target | The function ARN, with the role `<name_prefix>-sweeper-scheduler` | The role needs `lambda:InvokeFunction` ([templated targets](https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-targets-templated.html)). |
| Retry policy | `maximum_retry_attempts = 0` | A failed delivery is not retried: the next run starts 1 minute later. |
| Function timeout | 120 s | The EC2 and S3 clients use a 5 s connect timeout, a 15 s read timeout, and 3 attempts in the standard retry mode, so a slow Region cannot use up the timeout before the log flush. |
| Memory | 128 MB | |

2 runs can overlap when 1 run is slow. That is safe: a cancel is safe to repeat (section 7).

## 4. Settings

The function reads these environment variables. The AWS stack sets all 4.

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `FTG_EXPERIMENT` | yes | none | The value of the `experiment` tag that marks a probe reservation. The stack fills it from the targets file's own `experiment` key, the only source ([ADR 0010](../decisions/0010-one-experiment-file-of-modules-and-jobs-with-a-billable-guard.md) section 2). |
| `FTG_REGIONS` | yes | none | The Regions to sweep, separated by commas. Each must match `^[a-z]{2}(-[a-z]+)+-\d+$`. The stack fills it with the sorted, distinct Regions that the targets file names under `aws`: the `regions` of each `aws.jobs` entry and the `region` of each `aws.provisioning_probes` entry, plus the stack's own `region`, so the list is never empty. |
| `FTG_DATA_BUCKET` | yes | none | The data bucket (spec 0004 section 4.2). The store log handler writes here (section 9). |
| `FTG_MAX_AGE_SECONDS` | no | `180` | The age threshold in seconds. A whole number, 0 or more. It must be 30 s or more above the probe timeout plus the teardown timeout of `aws_odcr` (90 s and 60 s by default, spec 0002 section 6 item 4), so the sweeper never cancels a reservation that a probe attempt still uses. The stack sets `180`. |

A missing or bad value stops the run with an error before any call.

## 5. Discovery

In each Region of `FTG_REGIONS`, in order, the run calls `DescribeCapacityReservations` with 2 filters ([DescribeCapacityReservations](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityReservations.html)):

```python
Filters=[
    {"Name": "tag:experiment", "Values": [FTG_EXPERIMENT]},
    {"Name": "state", "Values": ["pending", "assessing", "active", "delayed"]},
]
```

Rules:

1. The run follows `NextToken` and reads every page.
2. The page of `DescribeCapacityReservations` documents the `state` filter with 5 states, and no `tag:<key>` filter. The `CapacityReservation` type lists more states, among them `assessing` and `delayed` ([CapacityReservation](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityReservation.html)), and the probe's own cleanup already uses the `tag:experiment` filter (spec 0002 section 4.9). If the service ignored the tag filter, check 1 of section 6 and the role (section 10) still keep every other reservation safe; open item 4.
3. The state filter leaves out the end states (`cancelled`, `expired`, `failed`), so an old reservation does not log a line every minute. It keeps `pending`, which cannot be cancelled, so a reservation stuck in it shows in the log every run (section 6, check 4).
4. When the call fails in a Region, the run logs `sweeper query failed: region=<r> error=<the error as returned>`, counts 1 error, and goes on with the next Region. After the health line, the run raises, so the invocation fails.

## 6. The age rule

For each reservation, the run makes 1 decision. The first check that applies gives the decision.

| # | Check | Decision | Reason text |
|---|---|---|---|
| 1 | The `experiment` tag is not `FTG_EXPERIMENT` | `skip` | `experiment tag does not match` |
| 2 | `CreateDate` is missing | `keep` | `no CreateDate` |
| 3 | `now - CreateDate` is `FTG_MAX_AGE_SECONDS` or less | `keep` | `young` |
| 4 | The state is not `assessing`, `active`, or `delayed` | `keep` | `state <state> cannot be cancelled` |
| 5 | Any other | `cancel` | `older than <N> s` |

Notes:

- `now` is the UTC clock of the run, read once per Region. A reservation at the threshold is young: the comparison is strict.
- Check 1 repeats the tag filter of the query. The query is the first guard, the rule is the second, and the role (section 10) is the third.
- Check 4: `CancelCapacityReservation` accepts `assessing`, `active` with no commitment duration, and `delayed` with no quote. `scheduled`, and `active` during a commitment duration, need a cancellation quote ([CancelCapacityReservation](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CancelCapacityReservation.html)). A probe reservation has no commitment duration and is never future-dated, so the sweeper asks for no quote. A `pending` reservation waits for its `EndDate` backstop (spec 0002 section 4.9).
- The age comes from `CreateDate`, the platform's own time (D10), not from a tag. `EndDate` plays no part, so a reservation with a wrong, far `EndDate` is cancelled too.

## 7. Cancels

1. Each cancel is `CancelCapacityReservation(CapacityReservationId=<id>)`. The call changes the state to `cancelled` ([CancelCapacityReservation](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CancelCapacityReservation.html)). The response element `Return` is `true` when the request succeeds; any other response counts as an error.
2. The call is synchronous. The run does not wait or poll after it.
3. An error counts as 1 error. The run logs the action `error` with the error text as boto3 returns it, for example `An error occurred (UnauthorizedOperation) when calling the CancelCapacityReservation operation: ...`, and goes on with the other reservations and Regions. It never filters or rewrites the text ([ADR 0006](../decisions/0006-store-raw-provider-values-normalize-separately.md)). The next run tries again.
   After the health line, the run raises `SweepError` when any cancel failed (the same as for a failed Region query, section 5 item 4), so the invocation fails and the function's `Errors` metric counts it. Reason: a run that only counted the error succeeded, so a role that is denied every cancel left the "sweeper stopped" alarm (`Invocations` minus `Errors`) at OK while leaked reservations kept billing. A run with nothing to cancel, or with every cancel done, succeeds. A reservation that is kept (`keep` or `skip`) is not an error.
   The raise and the schedule's `maximum_retry_attempts = 0` (section 3) may interact. The Scheduler documentation says that a retry policy covers a failure to deliver an event to a target and retryable errors from the target API ([managing a schedule](https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-schedule.html), [RetryPolicy](https://docs.aws.amazon.com/scheduler/latest/APIReference/API_RetryPolicy.html), [dead-letter queue](https://docs.aws.amazon.com/scheduler/latest/UserGuide/configuring-schedule-dlq.html)). It does not say whether an error that the function raises is retried. That behavior is unconfirmed; see open item 3. A repeat is safe (rule 4), and each failed attempt adds to `Errors`, so a retry cannot hide a failure.
4. A cancel is safe to repeat. A reservation that is cancelled already is in an end state, which the query of section 5 leaves out. The probe's own teardown or cleanup can cancel the same reservation; each side treats the result as its own.

## 8. No fallback

The Azure sweeper's scale set fallback (spec 0007 section 8) has no AWS counterpart: the AWS probe makes no instance and no Auto Scaling group (ADR 0009 section 3.2).

## 9. Log lines

The code logs through the Python `logging` module, logger name `sweeper`, at level `INFO`. `handler.py` sets that level on the `sweeper` logger, so the lines reach the store handler whatever level the runtime gives the root logger.

**The store handler (spec 0003 section 8.2).** `sweeper/log_store.py` is a copy of the Azure sweeper's handler (spec 0007 section 9), with 1 S3 `PutObject` in place of the Blob upload: an `S3LogHandler` buffers every record that reaches the root logger, ours and the SDK loggers, as 1 JSON object mapped to the OpenTelemetry log data model and writes the buffer as 1 JSONL object to `experiment=<tag>/logs/provider=aws/date=<YYYY-MM-DD>/<ts>_<host>_<uuid>.jsonl` in `FTG_DATA_BUCKET`. `<host>` is the function name from `AWS_LAMBDA_FUNCTION_NAME`, which Lambda sets ([Lambda environment variables](https://docs.aws.amazon.com/lambda/latest/dg/configuration-envvars.html#configuration-envvars-runtime)), so the sweeper's objects stand apart from the prober's under the same prefix. The fields, the `kv()` parse, no logger-name filter (a `botocore` or `boto3` record at the configured level reaches the store, and no message is filtered, redacted, or rewritten), the caps (10,000 records, 8 MiB, 1 record at most 64 KiB), and the truncation are those of spec 0007 section 9. A test checks that the code of the record format is the same in the 2 copies (decision S7).

The `PutObject` sends no `IfNoneMatch`: the runtime's SDK version is not ours to pin (section 2), and the key ends in a new UUID, so it never names an existing object. This is the 1 difference from the S3 store of spec 0003 section 9.

`handler.py` attaches the handler once, at cold start, to the root logger (so that SDK records reach it too; the `sweeper` logger still propagates), and flushes it once at the end of every invocation, in a `finally` block, also when the run raises. If the attach fails, `handler.py` prints 1 line and the sweep still runs. A failed flush prints 1 line, keeps the records for the next flush, and never raises.

**No CloudWatch Logs.** The role has no `logs:*` action (section 10). Without these permissions "the function still runs but it is unable to log any output to the CloudWatch service" ([access to CloudWatch Logs](https://docs.aws.amazon.com/lambda/latest/dg/access-logs.html)). Thus the printed lines and the runtime's own lines go nowhere; the store is the only log.

One line per reservation:

```text
sweeper decision: action=<action> age_s=<age> region=<region> state=<state> id=<id> reason=<reason>
```

- `action` is one of `keep`, `skip`, `cancelled`, or `error`.
- `age_s` is the age in whole seconds, or `unknown`.
- `reason` is the reason text of section 6, or `cancel failed: <error as returned>`.

One health line at the end of every run, also when a Region fails:

```text
sweeper run: scanned N, cancelled N, errors N
```

- `scanned` is the number of reservations that the queries returned, in all Regions.
- `cancelled` is the number of actions `cancelled`.
- `errors` is the number of actions `error`, plus 1 for each failed Region query.

## 10. Identity rights

The function role `<name_prefix>-sweeper` trusts `lambda.amazonaws.com`. Its inline policy has exactly 3 statements. It has no managed policy attachment.

| Sid | Action | Resource | Condition |
|---|---|---|---|
| `ListReservations` | `ec2:DescribeCapacityReservations` | `*` | `ec2:Region` is 1 of the Regions of `FTG_REGIONS` |
| `CancelExperimentReservations` | `ec2:CancelCapacityReservation` | `arn:aws:ec2:<region>:<account>:capacity-reservation/*`, 1 per Region of `FTG_REGIONS` | `aws:ResourceTag/experiment` = the targets file's `experiment` value |
| `WriteLogs` | `s3:PutObject` | `arn:aws:s3:::<bucket>/experiment=<experiment>/logs/*` | none |

- The [EC2 service reference](https://servicereference.us-east-1.amazonaws.com/v1/ec2/ec2.json) lists no resource type for `DescribeCapacityReservations` and `ec2:Region` as its only condition key, so the resource is `*` and the Regions are the condition. For `CancelCapacityReservation`, it lists the `capacity-reservation` resource (ARN `arn:${Partition}:ec2:${Region}:${Account}:capacity-reservation/${CapacityReservationId}`) with the condition key `aws:ResourceTag/${TagKey}`.
- The prober's own `CancelAllocationProbeReservation` statement (spec 0004 section 4.3) uses the same tag condition.
- No `logs:*` action, on this role or on the scheduler role (section 9).
- The scheduler role `<name_prefix>-sweeper-scheduler` trusts `scheduler.amazonaws.com`, with the condition `aws:SourceAccount` = the account. Its 1 statement allows `lambda:InvokeFunction` on the sweeper function only.

The stack deploys the function, both roles, and the schedule whether or not `grant_billable_provisioning` is set. The grant controls creation, never deletion ([ADR 0015](../decisions/0015-two-independent-controls-replace-the-terraform-allocation-guard.md) section 1 item 3a). With the grant at `false`, the sweeper still cancels a reservation that an earlier run leaked.

## 11. Network

The function has no `vpc_config`. By default a Lambda function has access to the public internet; a function attached to a VPC "can only access resources available within that VPC" ([Lambda and VPCs](https://docs.aws.amazon.com/lambda/latest/dg/configuration-vpc.html#configuration-vpc-internet-access)). The VPC subnets of a deployment can have no internet path (spec 0004 section 4.4), and the sweeper calls the EC2 endpoint of each Region in `FTG_REGIONS`.

## 12. Make targets and tests

`tasks/Makefile.sweeper` gives 2 targets. Both run `uv` through `mise exec --`, with `--locked`. `make lint` and `make test` run them.

| Target | What it runs |
|---|---|
| `make sweeper/aws/test` | `pytest` in `sweeper/aws` |
| `make sweeper/aws/lint` | `ruff check .` and `ruff format --check .` in `sweeper/aws` |

The tests use real boto3 EC2 and S3 clients behind a botocore `Stubber`. The real client checks each request against the service model before the `Stubber` answers it, and the `Stubber` checks each fake response against the service model ([botocore Stubber](https://docs.aws.amazon.com/botocore/latest/reference/stubber.html)): a request or a fake response with a wrong field fails with `ParamValidationError` (author check, 2026-10-15, botocore of boto3 1.43.111). So a fake cannot drift from the real API shape. The `Stubber` also fails a test when a queued response is not used, which is how the cancel test fails without the cancel call. They run with no network and no credentials. They cover at least: a tagged reservation older than the threshold is cancelled (this test fails without the cancel call); a young one, 1 at the threshold, an untagged one, and 1 with another tag value are not; an old `pending` one is kept and logged; every page is read; a failed cancel logs the error as returned, does not stop the cancel of the other reservations, and fails the invocation, while a run with nothing to cancel succeeds; a failed Region does not stop the other Regions and fails the invocation; the health line; the settings; the store log object key, fields, and failed-flush path; the record-format code is the same as the Azure copy; and an end-to-end `lambda_handler` run whose decision and health lines land in 1 object under the logs prefix.

`terraform/aws/tests/sweeper.tftest.hcl` (spec 0004 section 9) pins the function settings and its environment, no `vpc_config`, the schedule and its target, the 3 statements of the role and their exact conditions and resources, no `logs:*` action, the Regions from a targets file, the stack Region with a file that names none, and the sweeper with `grant_billable_provisioning` both `false` and `true`.

## 13. Decisions and open items

| # | Decision |
|---|---|
| S1 | A Lambda function on an EventBridge Scheduler schedule, outside the VPC ([ADR 0019](../decisions/0019-the-aws-sweeper-is-a-scheduled-lambda-function.md)). |
| S2 | 1 resource type, the Capacity Reservation: it is the only resource an AWS probe creates. |
| S3 | The Regions come from the targets file under `aws`, plus the stack Region. A Region that leaves the file is no longer swept. |
| S4 | `pending` is listed and kept, not cancelled: `CancelCapacityReservation` does not accept it. |
| S5 | The threshold comparison is strict: a reservation at exactly `FTG_MAX_AGE_SECONDS` is young. |
| S6 | A failed Region logs the health line and then fails the invocation. A failed cancel does the same. |
| S7 | The store log handler is a copy of the Azure one, kept in step by a test, not a shared module: each zip is built from 1 folder. |
| S8 | The zip ships no packages and uses the runtime's boto3; the code avoids API parameters newer than the runtime's SDK. |

Open items:

1. Closed: the "sweeper stopped" alert of spec 0002 section 6A item 5 is a CloudWatch alarm on the built-in `Invocations` and `Errors` metrics of the function, with an SNS email topic, only when `alert_email` is set ([ADR 0024](../decisions/0024-the-aws-sweeper-alarm-reads-the-lambda-metrics.md), [spec 0004](0004-aws-stack-spec.md) section 4.8). It reads metrics, not logs, so section 9 holds.
2. The first deploy must confirm that a `sweeper run:` line lands under `experiment=<name>/logs/provider=aws/` every minute, and that the function's role has no `logs:*` permission from any other source.
3. Lambda retries a function error of an asynchronous invocation ([asynchronous invocation](https://docs.aws.amazon.com/lambda/latest/dg/invocation-async-error-handling.html)). Whether a Scheduler invocation counts as asynchronous, and so whether a failed run runs again in the same minute, is to confirm on the first deploy. A repeated run is safe (section 7, rule 4).
4. The first deploy must confirm that the `tag:experiment` and `state` filters of section 5 return the tagged `pending`, `assessing`, `active`, and `delayed` reservations: the API page does not document the tag filter or the last 2 states as filter values.

## Sources

- [Lambda Python runtimes, runtime-included SDK versions](https://docs.aws.amazon.com/lambda/latest/dg/lambda-python.html)
- [Access to CloudWatch Logs](https://docs.aws.amazon.com/lambda/latest/dg/access-logs.html)
- [Lambda and VPCs, internet access](https://docs.aws.amazon.com/lambda/latest/dg/configuration-vpc.html#configuration-vpc-internet-access)
- [Asynchronous invocation, error handling](https://docs.aws.amazon.com/lambda/latest/dg/invocation-async-error-handling.html)
- [EventBridge scheduled rules (legacy)](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-create-rule-schedule.html)
- [EventBridge Scheduler templated targets](https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-targets-templated.html)
- [EC2 service reference](https://servicereference.us-east-1.amazonaws.com/v1/ec2/ec2.json)
- [DescribeCapacityReservations](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityReservations.html)
- [CancelCapacityReservation](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CancelCapacityReservation.html)
- [CapacityReservation](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityReservation.html)
- [botocore Stubber](https://docs.aws.amazon.com/botocore/latest/reference/stubber.html)
