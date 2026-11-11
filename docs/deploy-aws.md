# Deploy to AWS

This page shows how to deploy the AWS stack: one small prober VM, its role, and one data bucket. Do the steps in order.

## Before you start

Read [prerequisites](prerequisites.md). Read [config](config.md) for the settings. If a step fails, read [troubleshooting](troubleshooting.md).

Copy the example settings once, and put in your values. A deploy needs `vpc_id` and `subnet_id`. Log in with the AWS CLI.

```sh
cp terraform/aws/terraform.tfvars.example terraform/aws/terraform.tfvars
```

## 1. Ask for GPU quota (first time only)

```sh
make quota/aws
```

This is a dry run. It prints the On-Demand and Spot requests and sends none. To send them, run `make quota/aws QUOTA_ARGS=--apply`. The Regions come from `aws.quota` in the experiment file, and the account from your AWS CLI login. Approval can take days. The probes run in the meantime, and the rows record the cap.

## 2. Preflight

```sh
make preflight/aws
```

The preflight only reads. If it finds a gap, it fails and gives the fix.

## 3. Plan and apply

Use the two-step way. You then apply only the plan that you read.

```sh
make plan/aws
make apply/aws
```

`plan/aws` writes `terraform/aws/tfplan`. `apply/aws` applies that file and nothing else. If you change `terraform.tfvars` after the plan, OpenTofu stops the apply with `Mismatch between input and plan variable value`. Make a new plan.

For one step with a prompt, run `make deploy/aws`. `AUTO_APPROVE=1` skips the prompt.

## 4. Verify

```sh
make verify/aws
```

It shows the newest row object and the newest log object, the release on the VM, and the state of the service. It exits 1 unless the release matches the manifest and the service is active. If it fails, read [troubleshooting](troubleshooting.md). For a look at the VM state, leaked Capacity Reservations, and the newest object keys, run `make status/aws`.

## State backend

By default the state is a local file, `terraform/aws/terraform.tfstate`. Git ignores it. The state stays in this folder. For shared state, use `backend.hcl`. See [Backend](config.md#backend).

## Cost guards

- `grant_billable_provisioning` is `false` by default. The role gets no right to create reservations until you set it to `true`.
- Set `alert_email` for the sweeper-stopped alarm. With an empty `alert_email` there is no alarm, and nobody is told when the sweeper stops. Confirm the email subscription after the first apply.
- The sweeper removes leaked capacity.

Read [cost and consent](cost-and-consent.md).

## Destroy

You run destroy yourself.

1. Copy the data first. The destroy cannot keep it: `aws s3 sync s3://<data bucket> ./backup`. If you want to keep the logs, copy them too. They are under the `logs/` prefix.
2. Run `make status/aws`. The line "leaked resources" must show `0`. A Capacity Reservation made seconds before the destroy ends at its end date, about 4 minutes later.
3. Empty the data bucket. OpenTofu does not delete a bucket that holds objects.
4. Run `make destroy/aws`.
5. Check for leftovers: `aws ec2 describe-capacity-reservations --filters Name=tag:experiment,Values=<name>` in each Region.
