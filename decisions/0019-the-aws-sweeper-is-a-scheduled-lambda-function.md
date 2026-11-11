# 0019. The AWS sweeper is a scheduled Lambda function

- Status: accepted
- Date: 2026-10-15

## Context

The `aws_odcr` provisioning probe creates On-Demand Capacity Reservations ([spec 0002](../spec/0002-gpu-capacity-tracker-spec.md) section 4.9). A reservation is charged at the On-Demand rate whether instances use it or not ([Capacity Reservation pricing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html)). The probe cancels its own reservation. At the start of its next call, it also cancels the older leftovers of its `experiment` tag. Thus nothing cleans up while the prober is stopped or broken:

- A crash leaves a reservation until its `EndDate`, about 220 s after the create, and AWS can end it up to 1 hour after that `EndDate` (spec 0002 section 4.9, "Leak cost").
- A reservation that a crash leaves `pending` is not cancelled by the probe's cleanup.
- A probe bug that sets `EndDate` far in the future leaks the reservation until that date. Nothing in the AWS stack notices.

The Azure stack has a sweeper that does not trust the prober: spec 0002 D9 and D10, [spec 0007](../spec/0007-sweeper-spec.md). The project wants the same architecture on both clouds (2026-10-15).

The AWS VPC subnets of a deployment can have no internet path ([spec 0004](../spec/0004-aws-stack-spec.md) section 4.4, `enable_s3_gateway_endpoint`). the project's rule for logs: our own code writes its logs to the store under `experiment=<name>/logs/`, never to CloudWatch Logs ([spec 0003](../spec/0003-prober-core-spec.md) section 8.2).

## Options

1. **A Lambda function on an EventBridge Scheduler schedule, every minute.** The same shape as the Azure Function: a scheduled function, independent of the prober VM (D9). It needs a role, a schedule, and a small zip with no packages: the Lambda Python runtime includes boto3 ([Lambda Python runtimes](https://docs.aws.amazon.com/lambda/latest/dg/lambda-python.html)). A run lasts about 1 s at 128 MB, so the cost is in the free grant, as for Azure (spec 0002 section 5).
2. **A systemd timer on the prober VM.** No new service: the VM already runs systemd units ([spec 0004](../spec/0004-aws-stack-spec.md) section 6). But it fails together with the prober: a stopped, replaced, or broken VM also stops the timer. That is the exact gap this decision must close. D9 rejects it: "independent of the prober VM".
3. **Nothing: keep the probe's own cleanup and the `EndDate` backstop.** No new code. The gaps of the context stay. Also, the 2 clouds then have different safety models, against the project's request.

## Decision

Option 1. The AWS sweeper is a Lambda function in `sweeper/aws/`, deployed by the AWS stack ([spec 0013](../spec/0013-aws-sweeper-spec.md)):

1. EventBridge Scheduler starts it with `rate(1 minute)`. AWS calls scheduled rules a legacy feature and recommends Scheduler ([scheduled rules](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-create-rule-schedule.html)).
2. In each Region of the stack's targets file, it lists the Capacity Reservations with the `experiment` tag, and it cancels each one in a cancellable state whose age (`CreateDate` to now) is more than 180 s, the Azure threshold (D10).
3. It runs outside the VPC. By default a Lambda function has access to the public internet; a function attached to a VPC "can only access resources available within that VPC" ([Lambda and VPCs](https://docs.aws.amazon.com/lambda/latest/dg/configuration-vpc.html#configuration-vpc-internet-access)). Outside the VPC it reaches the EC2 endpoint of every Region and the S3 endpoint with no NAT gateway or VPC endpoint.
4. Its role can call `ec2:DescribeCapacityReservations` (condition `ec2:Region`, the Regions of the targets file) and `ec2:CancelCapacityReservation` (resource: the capacity reservations of those Regions; condition `aws:ResourceTag/experiment`). The EC2 service reference lists `aws:ResourceTag/${TagKey}` for the `capacity-reservation` resource of `CancelCapacityReservation`, and `ec2:Region` as the only condition key of `DescribeCapacityReservations`, which has no resource type ([EC2 service reference](https://servicereference.us-east-1.amazonaws.com/v1/ec2/ec2.json)).
5. Its role has no `logs:*` action and no managed policy. Without these permissions "the function still runs but it is unable to log any output to the CloudWatch service" ([access to CloudWatch Logs](https://docs.aws.amazon.com/lambda/latest/dg/access-logs.html)). The function writes its own log, in the record format of spec 0003 section 8.2, to the store under `experiment=<name>/logs/provider=aws/`, with `s3:PutObject` on that prefix only.
6. The stack deploys the sweeper whether or not `grant_billable_provisioning` is set. The grant controls creation, never deletion ([ADR 0015](0015-two-independent-controls-replace-the-terraform-allocation-guard.md) section 1 item 3a, the same rule as the Azure `sweeper_cleanup` assignment).
7. The probe's own start-of-call cleanup stays. The 2 are independent, and a cancel is safe to repeat.

The store log handler is a copy of the Azure sweeper's (`sweeper/azure/sweeper/log_store.py`), with 1 S3 `PutObject` in place of the Blob upload. A shared module was rejected: each stack builds its zip from 1 folder with `archive_file`, so a shared module needs a build step that copies it into both zips. A test keeps the 2 copies of the record format the same.

## Consequences

- The next AWS apply adds 6 resources: the sweeper role and its policy, the function, the scheduler role and its policy, and the schedule. It also builds a 3rd zip under `.terraform/ftg-build/`.
- A leaked reservation in a Region of the targets file is cancelled within about 180 s plus 1 minute, also while the prober VM is stopped. A `pending` reservation still waits for its `EndDate`: `CancelCapacityReservation` does not accept that state ([CancelCapacityReservation](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CancelCapacityReservation.html)). The sweeper logs it every run.
- A Region that leaves the targets file is no longer swept. The probe's cleanup has the same limit.
- There is no CloudWatch Logs record of a run. A run that fails before it attaches the store handler (a bad environment variable) leaves no log. The "sweeper stopped" alert of spec 0002 section 6 item 5 is a CloudWatch alarm on the built-in Lambda metrics ([ADR 0024](0024-the-aws-sweeper-alarm-reads-the-lambda-metrics.md)).
