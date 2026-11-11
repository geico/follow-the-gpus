---
title: AWS stack spec
date: 2026-10-11
---

# AWS stack spec

The AWS part of the rig: 1 prober VM, its IAM role, 1 S3 data bucket, and the sweeper, all in OpenTofu. The design is in [spec/0002](0002-gpu-capacity-tracker-spec.md): the architecture (section 3), the AWS stack (section 8.2), 1 small VM per cloud with native identity (D12), 1 object store per cloud (D13), and all infrastructure in OpenTofu (D15). The spec gives each requirement in full. A person must be able to build the stack again from this file and spec/0002.

The code is in `terraform/aws/`. The make targets are in `tasks/Makefile.aws`.

## 1. Scope

The stack creates these resources and no others:

| Resource | Purpose |
|---|---|
| S3 bucket | The data store. The prober writes 1 JSONL object per probe call. OpenTofu uploads the code zips under `code/`. |
| IAM role, role policy, instance profile | The native identity of the VM (spec/0002 D12). |
| Security group with 1 egress rule for all IPv4 traffic, and 1 for all IPv6 traffic when the VPC has an IPv6 CIDR block (default; `create_security_group`, section 4.4) | No inbound rule. |
| EC2 instance | The prober VM. |
| 2 S3 objects | The prober zip and the config zip. |
| 1 fixed-key S3 object, and 1 more hash-keyed like the 2 code zips | `manifest.json` (the current zip and `sync.sh` keys), fixed, at the bucket root; `sync.sh` (the VM-side pull-and-restart script), hash-named under `code/`. [ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md); section 4.5, section 5, [spec 0003](0003-prober-core-spec.md) section 6A. |
| S3 gateway VPC endpoint (optional, section 4.4) | The S3 path for a subnet with no internet access. |
| Lambda function, its IAM role and role policy, and an EventBridge Scheduler schedule with its own IAM role and role policy (section 4.7) | The sweeper: it cancels leaked probe Capacity Reservations every minute ([spec 0013](0013-aws-sweeper-spec.md)). |

**Why the stack has a sweeper.** The AWS signal probes create no billable resource. The `aws_odcr` provisioning probe does: it creates On-Demand Capacity Reservations when consented (`grant_billable_provisioning`, section 4.3), and a reservation is charged at the On-Demand rate whether instances use it or not ([spec 0002](0002-gpu-capacity-tracker-spec.md), section 5). The probe cancels its own reservation, and at the start of each call it cancels the older leftovers of its `experiment` tag (spec 0002 section 4.9), but it does neither while the prober VM is stopped, replaced, or broken. Thus the stack deploys a sweeper outside the VM, the same architecture as the Azure stack (spec 0002 D9, section 6A; [ADR 0019](../decisions/0019-the-aws-sweeper-is-a-scheduled-lambda-function.md); [spec 0013](0013-aws-sweeper-spec.md)). The stack has no probe resource group: the sweeper finds the reservations by the `experiment` tag.

The stack deploys into an existing VPC and an existing subnet, given by the required variables `vpc_id` and `subnet_id`. It creates no VPC, subnet, route, gateway, or network ACL, except the optional S3 gateway endpoint and its route (section 4.4). It does not read the default VPC.

## 2. Files

| File | Content |
|---|---|
| `terraform/aws/versions.tf` | OpenTofu `>= 1.10`. Providers `hashicorp/aws ~> 6.0`, `hashicorp/archive ~> 2.7`, `hashicorp/random ~> 3.7`. The `aws` provider block with `default_tags`. No backend block: the state is local by default. An optional git-ignored `backend.hcl` and `backend_override.tf` select a remote backend ([spec 0014](0014-config-and-deploy-spec.md) section 5). |
| `terraform/aws/terraform.tfvars.example` | The example settings, with made-up values ([spec 0014](0014-config-and-deploy-spec.md) section 2). The user copies it to the git-ignored `terraform.tfvars`. |
| `terraform/aws/backend.hcl.example` | The example `s3` backend settings for optional remote state ([spec 0014](0014-config-and-deploy-spec.md) section 5). |
| `terraform/aws/variables.tf` | The variables of section 3. |
| `terraform/aws/main.tf` | The data sources, the code zips, the `manifest.json` and `sync.sh` objects (section 4.5), the security group, and the instance. |
| `terraform/aws/network.tf` | The optional S3 gateway endpoint and the route table lookups (4.4). |
| `terraform/aws/iam.tf` | The role, its policy, the optional SSM attachment, and the instance profile. |
| `terraform/aws/s3.tf` | The bucket and its settings. |
| `terraform/aws/outputs.tf` | The outputs of section 7. |
| `terraform/aws/user_data.sh.tftpl` | The first-boot script of section 5. |
| `terraform/sync.sh` | The VM-side pull-and-restart script, shared with the Azure stack. [ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md), [spec 0003](0003-prober-core-spec.md) section 6A. |
| `terraform/ftg-prober-sync.service`, `terraform/ftg-prober-sync.timer` | The 2 systemd units `sync.sh` runs under, shared with the Azure stack (section 6). |
| `terraform/tests/sync.test.sh` | The tests of `sync.sh`, shared with the Azure stack ([spec 0003](0003-prober-core-spec.md) section 6A). `make test/sync`. |
| `terraform/aws/status.py` | The script of `make status/aws`. |
| `terraform/aws/sweeper.tf` | The sweeper zip, the function, its role, and its schedule (section 4.7, [spec 0013](0013-aws-sweeper-spec.md)). |
| `terraform/aws/tests/stack.tftest.hcl` | The mock-provider tests of section 9. |
| `terraform/aws/alerts.tf` | The sweeper-stopped alarm, its topic, and its email subscription (section 4.8). |
| `terraform/aws/tests/sweeper.tftest.hcl` | The mock-provider tests of the sweeper and its alarm (section 9). |
| `terraform/aws/README.md` | Operator notes: prerequisites, quota request, cost, logs, destroy. |
| `terraform/aws/.terraform.lock.hcl` | The provider lock file. Commit it. |
| `tasks/Makefile.aws` | The make targets of section 8. |
| `tasks/Makefile.preflight`, `preflight/` | `make preflight/aws` and its code (section 12). |

Git ignores `terraform/aws/.terraform/` (the zips land under `terraform/aws/.terraform/ftg-build/`, 4.5), `*.tfstate*`, `terraform.tfvars`, `*.auto.tfvars`, `backend.hcl`, `tfplan`, `backend_override.tf`, and `backend_override.tf.json`.

## 3. Variables

| Variable | Type | Default | Meaning |
|---|---|---|---|
| `vpc_id` | string | none (required) | The ID of an existing VPC in `region`. The security group goes here. Must match `^vpc-([0-9a-f]{8}\|[0-9a-f]{17})$`. A validation block enforces it. |
| `subnet_id` | string | none (required) | The ID of an existing subnet in `vpc_id`. The instance goes here. Outbound internet access from it is a deploy prerequisite (4.4). Must match `^subnet-([0-9a-f]{8}\|[0-9a-f]{17})$`. A validation block enforces it. |
| `name_prefix` | string | `ftg` | Prefix of each resource name. Must match `^[a-z][a-z0-9-]{0,19}$` and must not start with `xn--`, `sthree-`, or `amzn-s3-demo-`, which S3 rejects in bucket names. Validation blocks enforce both. |
| `region` | string | `us-east-1` | The Region of the stack. |
| `instance_type` | string | `t4g.small` | The instance type of the VM. |
| `ami_ssm_parameter` | string | `/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id` | The public SSM parameter that holds the AMI ID of Ubuntu 24.04 LTS. |
| `tags` | map(string) | `{}` | Extra tags on each resource. |
| `targets_file` | string | `../../config/targets.yaml` | The targets file. Relative to `terraform/aws/`, or absolute. Same pattern as the Azure stack ([spec 0005](0005-azure-stack-spec.md)). The file's own `experiment` key is the only source of the `experiment` tag (the VM gets no `FTG_EXPERIMENT`, ADR 0012; [ADR 0010](../decisions/0010-one-experiment-file-of-modules-and-jobs-with-a-billable-guard.md) section 2): there is no `experiment_tag` variable. |
| `enable_ssm` | bool | `true` | Attach the `AmazonSSMManagedInstanceCore` managed policy to the role. |
| `vpc_security_group_ids` | list(string) | `[]` | IDs of existing security groups in `vpc_id`. The instance gets them in addition to the created group (4.4). Each ID must match `^sg-([0-9a-f]{8}\|[0-9a-f]{17})$`. A validation block enforces it. |
| `create_security_group` | bool | `true` | Create the security group of the instance (4.4). With `false`, `vpc_security_group_ids` must not be empty. |
| `enable_s3_gateway_endpoint` | bool | `false` | Create an S3 gateway endpoint in `vpc_id` (4.4). Set it to `true` in `terraform.tfvars` when the subnet has no internet path. |
| `egress_proxy_url` | string | `null` | The URL of an HTTP egress proxy, `http://host:port` or `https://host:port`, with a host name (never an IP address) and a port from 1 to 65535, with no user name, no path, and no quote. A validation block enforces it. With `null`, the VM uses no proxy and the user data is unchanged. Sections 4.6, 5, 9 (tests 12, 13, 13a, 15), and 11 (A19) use it. |
| `no_proxy` | list(string) | `[]` | Extra hosts and domains that bypass the proxy. Each entry uses only letters, digits, and `. _ : / * -`. The stack always adds `169.254.169.254`, `localhost`, and `127.0.0.1`. |
| `alert_email` | string | `""` | Address of the sweeper-stopped alarm (4.8). Empty: no topic, no subscription, no alarm. Must be empty or match `^[^@[:space:]]+@[^@[:space:]]+$`. A validation block enforces it. |
| `grant_billable_provisioning` | bool | `false` | Grant the role the `aws_odcr` provisioning probe's IAM actions (4.3): create, cancel, and describe On-Demand Capacity Reservations, and tag a reservation on create. This is the only thing that decides whether the role gets these rights; it does not read the targets file ([ADR 0015](../decisions/0015-two-independent-controls-replace-the-terraform-allocation-guard.md)). Set it to `true` only when you also want an `aws.provisioning_probes` entry to run for real; the targets file's own `allow_billable_provisioning` key is a separate control the prober checks, not this variable. |

Canonical publishes the AMI ID of each Ubuntu release in public SSM parameters with the path `/aws/service/canonical/ubuntu/server/<release>/stable/current/<arch>/hvm/ebs-gp3/ami-id` ([Canonical: find Ubuntu images on AWS](https://ubuntu.com/aws/docs/aws-how-to/instances/find-ubuntu-images/)). The default is the arm64 image, because the default instance type is arm64. A user who selects an x86 instance type must also select the `amd64` parameter, and must plan with `-replace=aws_instance.prober`, because the instance ignores changes of the AMI (section 4.6; [`tofu plan -replace`](https://opentofu.org/docs/cli/commands/plan/#replace-address)). Without it, the plan changes only `instance_type` on the old image, and the change fails ([AWS: compatibility for resizing instances](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/resize-limitations.html): "AMIs are specific to the architecture of the processor, so you must select an instance type with the same processor architecture as the current instance type").

Private values go into the git-ignored `terraform/aws/terraform.tfvars`, never into the repo ([spec 0014](0014-config-and-deploy-spec.md) section 2). The user copies it from `terraform.tfvars.example`. `tofu test` also reads it.

## 4. Resources

### 4.1 Provider and data sources

- The `aws` provider takes `region = var.region` and `default_tags` with `merge(var.tags, { experiment = local.experiment })`, where `local.experiment` is the targets file's own `experiment` key. Thus every resource carries the `experiment` tag.
- `data "aws_ssm_parameter"` reads `var.ami_ssm_parameter`. The instance uses its `insecure_value`, so the AMI ID shows in the plan.
- `data "aws_ec2_instance_type_offerings"` with `location_type = "availability-zone"` and the filter `instance-type = var.instance_type` lists the zones that offer the type. The API is `DescribeInstanceTypeOfferings` ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeInstanceTypeOfferings.html)).
- `data "aws_subnet"` reads `var.subnet_id` for its `vpc_id` and `availability_zone`.
- 2 `precondition` blocks on the instance fail the plan with a clear message: when the `vpc_id` of the subnet is not `var.vpc_id`, and when the zone of the subnet is not in the zones from the offerings.

### 4.2 S3 bucket

| Setting | Value |
|---|---|
| Name | `<name_prefix>-data-<suffix>`. The suffix is the hex of a `random_id` with `byte_length = 4` (8 hex characters). General purpose bucket names must be unique across all AWS accounts in a partition, 3 to 63 characters, lower case ([bucket naming rules](https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucketnamingrules.html)). |
| `force_destroy` | `false`. The bucket holds the experiment data. Destroy fails until the operator empties the bucket (README). |
| Public access block | All 4 settings `true`: `block_public_acls`, `block_public_policy`, `ignore_public_acls`, `restrict_public_buckets`. |
| Versioning | `Disabled`. |
| Encryption | SSE-S3: `sse_algorithm = "AES256"`. Amazon S3 applies SSE-S3 to each new bucket by default; the resource makes it explicit ([default encryption](https://docs.aws.amazon.com/AmazonS3/latest/userguide/default-bucket-encryption.html)). |
| Lifecycle | 1 rule. `expire-logs`: filter prefix `experiment=<experiment>/logs/`, expiration after 90 days (spec 0003 section 8.2). The row prefix, `experiment=*/` ([ADR 0010](../decisions/0010-one-experiment-file-of-modules-and-jobs-with-a-billable-guard.md) section 5, item 4; before this change, `run_mode=*/`), apart from its `logs/` sub-prefix, never expires. |

S3 lifecycle filters match a literal prefix, not a glob ([S3 lifecycle configuration elements](https://docs.aws.amazon.com/AmazonS3/latest/userguide/intro-lifecycle-rules.html)), so `expire-logs` names the experiment, read from the targets file's own `experiment` key (`local.experiment`), the same value `WriteRecords` and the provisioning probe conditions already use (section 4.3). A later experiment name needs its own apply to get the rule.

`terraform_data.targets_check` fails the plan, with the message of `check-config`, when the `aws` section of the targets file has a key outside `KNOWN_CLOUD_KEYS` or `aws.cadence` has a renamed key (for example `provisioning_probes`), so a misspelled or old key cannot silently empty a list that the stack reads with `try(..., [])`.

The object layout of the data is in spec/0002 section 7.3 and in [spec 0003](0003-prober-core-spec.md), section 4 (record and key formats). The logs layout is spec 0003 section 8.2.

This stack does not read the targets file for consent at all ([ADR 0015](../decisions/0015-two-independent-controls-replace-the-terraform-allocation-guard.md), which supersedes [ADR 0010](../decisions/0010-one-experiment-file-of-modules-and-jobs-with-a-billable-guard.md) section 7 and [ADR 0011](../decisions/0011-store-and-per-job-simulate-replace-run-mode.md) section 3 rule 4's Terraform half and section 7 item 7). `var.grant_billable_provisioning` alone decides whether the role gets the 4 `aws_odcr` statements below (4.3); the targets file's own per-job `allow_billable_provisioning` key stays exactly where it already was, checked only by the prober's own config guard ([spec 0003](0003-prober-core-spec.md), section 3). The 2 controls are independent and can disagree: with `grant_billable_provisioning = false` and an active, consented entry in the file, the prober's job still runs (its own guard passes), but the call fails with the provider's error, written to the log as returned (for example `UnauthorizedOperation` on EC2 ([Error codes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html))), because the role was never given the rights. This is the intended, visible outcome of that disagreement, not a bug: it tells the operator the stack's grant and the file's consent do not match, instead of silently creating capacity nobody meant to grant rights for.

### 4.3 IAM

The role `<name_prefix>-prober` trusts `ec2.amazonaws.com`. Its inline policy has exactly 6 statements (`DenyWriteCode`, ADR 0012, is the 4th; `ReadRowsForCompaction` and `ListBucketForCompaction`, ADR 0025, are the 5th and 6th), plus 5 more when `var.grant_billable_provisioning` is `true` (the `aws_odcr` provisioning probe, spec 0002 section 4.9), or only 2 more when no `aws.provisioning_probes` entry names a Region (the 2 read statements; see the bullets below). This replaces the earlier `enable_allocation_probe` variable with the same shape, under a new name shared with the Azure stack's own grant variable (ADR 0015); the targets file's `aws.provisioning_probes` entries and their own `simulate`/`allow_billable_provisioning` keys play no part in whether these 6 statements plan.

| Sid | Effect | Actions | Resource | Condition |
|---|---|---|---|---|
| `ReadCapacitySignals` | Allow | `ec2:DescribeCapacityBlockOfferings`, `ec2:GetSpotPlacementScores`, `ec2:DescribeSpotPriceHistory` | `*` | none |
| `WriteRecords` | Allow | `s3:PutObject` | `arn:aws:s3:::<bucket>/experiment=<experiment>/*` | none |
| `ReadCode` | Allow | `s3:GetObject` | `arn:aws:s3:::<bucket>/code/*`, `arn:aws:s3:::<bucket>/manifest.json` | none |
| `DenyWriteCode` | Deny | `s3:PutObject`, `s3:DeleteObject` | `arn:aws:s3:::<bucket>/manifest.json`, `arn:aws:s3:::<bucket>/code/*` | none |
| `ReadRowsForCompaction` | Allow | `s3:GetObject` | `arn:aws:s3:::<bucket>/experiment=<experiment>/*` | none |
| `ListBucketForCompaction` | Allow | `s3:ListBucket` | `arn:aws:s3:::<bucket>` | none |
| `CreateAllocationProbeReservation` (`grant_billable_provisioning` only) | Allow | `ec2:CreateCapacityReservation` | `arn:aws:ec2:*:<account>:capacity-reservation/*` | `aws:RequestTag/experiment` = the targets file's `experiment` value, `ec2:EndDateType` = `limited`, and `aws:RequestedRegion` = the Regions of the `aws.provisioning_probes` entries |
| `TagAllocationProbeReservationOnCreate` (`grant_billable_provisioning` only) | Allow | `ec2:CreateTags` | `arn:aws:ec2:*:<account>:capacity-reservation/*` | `ec2:CreateAction` = `CreateCapacityReservation`, `aws:RequestTag/experiment` = the targets file's `experiment` value, and `aws:RequestedRegion` = the same Regions |
| `CancelAllocationProbeReservation` (`grant_billable_provisioning` only) | Allow | `ec2:CancelCapacityReservation` | `arn:aws:ec2:*:<account>:capacity-reservation/*` | `aws:ResourceTag/experiment` = the targets file's `experiment` value, and `aws:RequestedRegion` = the same Regions |
| `ReadAllocationProbeCapacity` (`grant_billable_provisioning` only) | Allow | `ec2:DescribeAvailabilityZones`, `ec2:DescribeCapacityReservations`, `ec2:DescribeInstanceTypeOfferings`, `ec2:DescribeInstanceTypes` | `*` | none |
| `ReadAllocationProbeQuota` (`grant_billable_provisioning` only) | Allow | `servicequotas:GetServiceQuota` | `*` | none |

- The 3 EC2 actions of `ReadCapacitySignals` are the IAM actions of the 3 APIs ([EC2 service reference](https://servicereference.us-east-1.amazonaws.com/v1/ec2/ec2.json), [GetSpotPlacementScores IAM permission](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/sps-iam-permission.html)). The EC2 actions take no resource-level restriction here, so the resource is `*`.
- `PutObject` needs `s3:PutObject` on the object ([PutObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html)). The prober writes new row and log keys only. The resource is the experiment prefix of the targets file's `experiment` value, not the whole bucket, so a key outside it (another experiment, `code/*`, `manifest.json`) gets no write. The prefix also covers the logs prefix (`experiment=<experiment>/logs/`, spec 0003 section 8.2): the prober needs no separate grant to write its own log objects next to its rows. IAM resource ARNs for S3 objects may name a key prefix ([S3 actions, resources, and condition keys](https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazons3.html)). The prefix also covers the daily files of the compaction job under `experiment=<experiment>/daily/v1/` ([spec 0003](0003-prober-core-spec.md) sections 4.3 and 9B); only the job's code keeps its writes there ([ADR 0025](../decisions/0025-daily-compaction-runs-on-the-prober-vm.md)).
- `GetObject` needs `s3:GetObject` on the object ([GetObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html)). The first boot reads `manifest.json` to learn the current `sync_key`, then fetches `sync.sh` at that hash-named key under `code/`; `sync.sh` itself then re-fetches `manifest.json` and the 2 zips, and re-checks its own `sync_key`, on every later tick ([ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md) section 5; [spec 0003](0003-prober-core-spec.md) section 6A). `code/*` already covers `sync.sh`, hash-named like the 2 zips, so it needs no `Resource` entry of its own. `sync.sh` does not need `s3:ListBucket`: `manifest.json` is never a missing key on AWS (section 4.5), so it has no 404 case to tell apart from any other fetch error on this cloud. The role has `s3:ListBucket` since ADR 0025, for the compaction job only (`ListBucketForCompaction`, below).
- `ReadRowsForCompaction` and `ListBucketForCompaction` ([ADR 0025](../decisions/0025-daily-compaction-runs-on-the-prober-vm.md)) let `ftg-prober compact` on the VM list the bucket and read the objects of the experiment: plain read and list, no condition. `ListObjectsV2` needs `s3:ListBucket` on the bucket ([ListObjectsV2](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html)). The role still has no `s3:DeleteObject`, and the change adds no lifecycle rule and no versioning or retention change. The policy change is an in-place update of `aws_iam_role_policy.prober`; it replaces no resource.
- `DenyWriteCode` is a second guard. Earlier, `WriteRecords` covered the whole bucket, including `manifest.json` and every key under `code/` (which includes `sync.sh`), and the VM's own sync timer installs and runs those keys as root. `WriteRecords` now names only the experiment prefix, so it no longer matches them; the explicit `Deny` stays, because it always overrides an `Allow` in the same policy ([IAM policy evaluation logic](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic.html)) and keeps working if the data key layout changes.
- `CreateAllocationProbeReservation` and `TagAllocationProbeReservationOnCreate` are 2 statements, not 1, because `CreateTags` and `CreateCapacityReservation` are different actions with different condition keys in the EC2 IAM action reference: `CreateCapacityReservation` supports no `ec2:CreateAction` key, so a single statement that required it on both actions would deny the create call too. `aws:RequestTag/<tag-key>` is the documented global condition key for a create action whose request can tag the resource it creates (`CreateCapacityReservation` takes `TagSpecifications`) ([access control with tags](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_tags.html#access_tags_control-tag-keys)). `ec2:CreateAction` restricts `CreateTags` to a tagging call that is part of a `CreateCapacityReservation` call, so the role cannot use `CreateTags` on its own against another resource ([supported resource-level permissions for tagging](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/supported-iam-actions-tagging.html)). `ec2:EndDateType` = `limited` enforces the `EndDate` backstop of section 4.9 at the policy level, from the [EC2 service reference](https://servicereference.us-east-1.amazonaws.com/v1/ec2/ec2.json)'s own condition keys, not only in the probe's own request: a reservation this role creates is always `limited`, never open-ended.
- The Region segment of the 3 `capacity-reservation` ARNs is `*`, not `var.region`: an `aws.provisioning_probes` entry can name any AWS Region ([spec 0003](0003-prober-core-spec.md), section 3), and the stack's `var.region` is only the Region of the VM and the bucket. The `aws:RequestedRegion` condition limits the Regions to those of the entries ([global condition keys](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_condition-keys.html#condition-keys-requestedregion)).
- With `var.grant_billable_provisioning` true and no `aws.provisioning_probes` entry with a Region, the list of Regions is empty. IAM may reject an empty `aws:RequestedRegion` list, and no entry means no right to create, tag, or cancel a reservation, so the stack does not plan `CreateAllocationProbeReservation`, `TagAllocationProbeReservationOnCreate`, or `CancelAllocationProbeReservation` at all. The 2 read statements and the sweeper's rights do not change.
- `CancelAllocationProbeReservation` uses `aws:ResourceTag/<tag-key>`, the documented condition key for restricting an action to a resource that already carries a tag ([supported resource-level permissions for tagging](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/supported-iam-actions-tagging.html)), so the role can cancel only a reservation tagged with the targets file's `experiment` value, never another reservation in the account.
- `ReadAllocationProbeCapacity` is read-only. `DescribeAvailabilityZones` (zone ID to zone name), `DescribeCapacityReservations`, `DescribeInstanceTypeOfferings`, and `DescribeInstanceTypes` support no resource-level permissions, so the resource is `*`, the same pattern as `ReadCapacitySignals`.
- The policy is a `jsonencode` of a Terraform object, not an `aws_iam_policy_document` data source, so the mock-provider tests can read it.
- When `enable_ssm` is true, the role also has the AWS managed policy `AmazonSSMManagedInstanceCore` ([policy reference](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/AmazonSSMManagedInstanceCore.html)). It lets the SSM Agent register the VM and lets the operator open a shell with Session Manager. AWS lists Ubuntu Server 24.04 LTS among the AMIs that usually have the SSM Agent preinstalled, as a snap, and says to check the agent status before first use ([AMIs with SSM Agent preinstalled](https://docs.aws.amazon.com/systems-manager/latest/userguide/ami-preinstalled-agent.html)).
- The instance profile `<name_prefix>-prober` holds the role.

### 4.4 Network

- The stack follows the module [terraform-aws-modules/ec2-instance](https://github.com/terraform-aws-modules/terraform-aws-ec2-instance) (`variables.tf`, `main.tf`): the variable `vpc_security_group_ids` (default `[]`), the variable `create_security_group` (default `true`), and a created group with open egress.
- When `create_security_group` is `true`, the stack creates 1 security group `<name_prefix>-prober` in `var.vpc_id`. It has no ingress rule. It has these egress rules, as `aws_vpc_security_group_egress_rule` resources:
  - All traffic (`ip_protocol` `-1`) to `0.0.0.0/0`.
  - All traffic (`-1`) to `::/0`, only when the VPC has an IPv6 CIDR block. `data "aws_vpc"` reads the VPC; its `ipv6_cidr_block_associations` holds an entry with the state `associated` when the VPC has the block. Without the block, the stack creates no IPv6 rule.
- The instance gets the created group (if any) and the groups of `var.vpc_security_group_ids`. When `create_security_group` is `false` and the list is empty, a precondition on the instance fails the plan with a message that names both variables. The stack never uses the default security group of the VPC ([default security group](https://docs.aws.amazon.com/vpc/latest/userguide/default-security-group.html)): its rules allow traffic between its members, and the network owner may have changed them.
- The VM has no SSH key: the instance sets no `key_name`.
- The instance does not set `associate_public_ip_address`, so the subnet setting decides the public IP. Outbound internet access from the subnet to the endpoints above is a deploy prerequisite. The network owner sets it up before the deploy, like the network itself: for example a route to a NAT gateway, or a route to an internet gateway and a public IP that the subnet assigns. The stack does not change the network, with 1 exception: when `enable_s3_gateway_endpoint` is true, the stack adds an S3 route to the route table of the subnet. Every other subnet on that table gets the route too. The created security group blocks all inbound traffic.
- The S3 gateway endpoint is optional. The variable `enable_s3_gateway_endpoint` turns it on. The default is `false`, because an endpoint changes the route tables of the network owner. With `false`, the stack creates no endpoint and reads no route table. With `true`, the stack creates 1 `aws_vpc_endpoint` named `<name_prefix>-s3`:
  - `vpc_endpoint_type` is `Gateway`, and `service_name` is `com.amazonaws.<region>.s3`. A gateway endpoint for S3 has no hourly charge ([gateway endpoints](https://docs.aws.amazon.com/vpc/latest/privatelink/gateway-endpoints.html)).
  - `route_table_ids` holds the route table that is associated with `var.subnet_id`. When the subnet has no own table, it holds the main route table of `var.vpc_id`. A subnet with no association uses the main table ([route table concepts](https://docs.aws.amazon.com/vpc/latest/userguide/VPC_Route_Tables.html)). `data "aws_route_tables"` finds the tables with the filters `association.subnet-id` and `association.main`. The stack takes no list of route table IDs as input, because it already knows the subnet.
  - The endpoint adds a route for the S3 prefix list to each of these tables. Other subnets that use the same table also get the route.
  - The endpoint makes the VM reach S3 only. A VM with no internet path still cannot reach the EC2 and STS APIs, the SSM service, the package mirrors, and the Spot Advisor feed. Thus `true` alone does not make the stack work without internet access. The network owner must add the other paths.
- With a proxy and `enable_s3_gateway_endpoint` both on, add the regional S3 hosts (`s3.<region>.amazonaws.com` and `.s3.<region>.amazonaws.com`) to `no_proxy`, so that S3 calls use the endpoint.
- Security groups do not filter the traffic to the instance metadata service, the Amazon DNS server, and the Amazon Time Sync Service ([security groups](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-security-groups.html)). The open egress of the created group also covers a custom DNS server from DHCP options and a proxy on any port.

### 4.5 Code zips

- `data "archive_file"` takes 1 source folder per zip ([archive_file](https://registry.terraform.io/providers/hashicorp/archive/latest/docs/data-sources/file)). Thus the code has 2 zips: `prober.zip` from `../../prober` and `config.zip` from the folder of `targets_file` (default `../../config`). This replaces the single zip of the Phase 1 plan contract.
- Both zips use `output_file_mode = "0644"`, so the hash does not depend on the file modes of the machine that runs OpenTofu.
- The prober zip excludes `.venv`, `__pycache__`, `tests`, `dist`, `.pytest_cache`, `.ruff_cache`, and `*.egg-info` at every depth. `excludes` takes doublestar globs and matches each pattern against the path inside the source folder, so each name is written as `**/<name>` and `**/<name>/**`. The config zip excludes nothing.
- The zips land in `terraform/aws/.terraform/ftg-build/`, not the git-ignored `dist/`. A saved-plan apply does not rebuild the zip. A stale file under `dist/` must not replace the zip that the plan built, because the apply would upload the stale bytes under the hash key of the correct build.
- `aws_s3_object` uploads each zip with the key `code/prober-<sha256>.zip` or `code/config-<sha256>.zip`, where `<sha256>` is the hex SHA-256 of the zip. `source_hash` is the MD5 of the zip. A new zip is a new key; `create_before_destroy` deletes the old object later in the same apply (versioning is off). No lifecycle rule expires `code/`.
- Both zip `aws_s3_object` resources add `lifecycle { create_before_destroy = true }` ([ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md) section 3). An S3 key cannot change in place, so a hash change still replaces the object; this setting only changes the replace order to create-then-destroy, so `aws_s3_object.manifest` (below), which `depends_on` both zips, is never rewritten to name a key that Terraform has already destroyed ([`create_before_destroy` and a dependent resource](https://github.com/hashicorp/terraform/blob/main/docs/destroying.md)).
- Both `aws_s3_object` resources set `tags = {}` and `metadata = {}`, so the plan matches the refreshed state ([aws_s3_object](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_object) arguments). Without them, every plan showed `{} -> null` for both on the two objects (observed in the live plans, 2026-10-13). The provider `default_tags` still reach `tags_all`, because the provider merges them into `tags_all` apart from `tags` ([default_tags](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/guides/resource-tagging)).
- The `archive_file` data source reads the folder at plan time. `tofu validate` and the mock tests do not need the folders.
- `aws_s3_object.manifest` writes a small JSON object with a fixed key, `manifest.json`, at the bucket root, not under `code/`, and, like every `code/` key, it does not expire (section 4.2). Its content is `jsonencode({ prober_key = ..., config_key = ..., sync_key = ... })`, the current hashed keys of the 2 zips above and of `sync.sh` below. `bucket` and `key` are the only `ForceNew` fields of this resource ([`internal/service/s3/object.go`](https://github.com/hashicorp/terraform-provider-aws/blob/main/internal/service/s3/object.go)), and neither changes here, so Terraform updates it in place: there is no window where `manifest.json` does not exist on AWS. It `depends_on` the 2 zips and `aws_s3_object.sync_script`, so it is rewritten only after all 3 already exist at their new key, and, with `create_before_destroy` on all 3, only before any of their old keys is destroyed.
- `aws_s3_object.sync_script` writes `terraform/sync.sh` (shared with the Azure stack) under `code/`, hash-named by `filesha256("../sync.sh")`, the same way as the 2 code zips above, including `create_before_destroy`: `code/sync-<sha256>.sh`. It does not expire, the same as the 2 zips (section 4.2). The user data (section 4.6) does not name this key at all, hashed or fixed; it reads `manifest.json`'s own `sync_key` field instead (section 5, step 4), the same way `sync.sh` itself does on every later tick, so a `sync.sh` change alone does not change the user data.

The VM-side mechanism that reads `manifest.json` and installs `sync.sh` is [spec 0003](0003-prober-core-spec.md) section 6A, shared by both clouds.

### 4.6 Instance

| Setting | Value |
|---|---|
| AMI | The value of the SSM parameter when the instance is created. `lifecycle { ignore_changes = [ami] }` (and nothing else) makes a later value of the parameter, for example a new Canonical image, leave the instance as it is, so a code-only apply never replaces it ([`lifecycle` meta-argument](https://opentofu.org/docs/language/meta-arguments/lifecycle/)). To move to a new image on purpose, run a plan with `-replace=aws_instance.prober` and apply it ([`tofu plan -replace`](https://opentofu.org/docs/cli/commands/plan/#replace-address)). |
| Instance type | `var.instance_type`. |
| Subnet | `var.subnet_id`. Its zone must offer the type (4.1). |
| Security group | 4.4. |
| Instance profile | 4.3. |
| User data | `templatefile("user_data.sh.tftpl", {...})` with `bucket`, `region`, `config_file`, `proxy_url`, `no_proxy`, `sync_service_unit`, and `sync_timer_unit`. `config_file` is the file name of `targets_file` (default `targets.yaml`). `sync_service_unit` and `sync_timer_unit` are `file("../ftg-prober-sync.service")` and `file("../ftg-prober-sync.timer")` (section 6), read raw and substituted once, with no further interpolation of their own content. `proxy_url` is an empty string by default, and `no_proxy` always holds the 3 fixed hosts. With the defaults the rendered script has no proxy line and no drop-in, also none for the SSM agent. ADR 0012 section 3: the user data no longer takes `prober_key`, `config_key`, or any `sync.sh` key at all, hashed or fixed; it names only the fixed `manifest.json` key (section 4.5), and reads `sync_key` from its content at boot (section 5, step 4). Neither a code, a config, nor a `sync.sh` change by itself changes the user data. |
| `user_data_replace_on_change` | `true`. A code or a config change no longer changes the rendered user data (it was the 2 hashed zip keys that did, before ADR 0012), so this no longer forces a replacement for that reason; it still fires for an unrelated user data change, for example a new proxy URL ([aws_instance](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/instance)). The 1 exception: the apply that first moves an existing stack to this design still replaces the VM once, because that apply itself changes the user data, from the hashed keys to the fixed ones. |
| Metadata options | `http_endpoint = "enabled"`, `http_tokens = "required"` (IMDSv2 only), `http_put_response_hop_limit = 1` ([IMDS options](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-options.html)). |
| Root disk | gp3, 8 GB, encrypted. |
| Tags | `Name = <name_prefix>-prober`, plus the default tags. |
| `depends_on` | The role policy, the SSM attachment, `aws_s3_object.manifest`, and `aws_s3_object.sync_script`, so the first boot can read a manifest whose `sync_key` already names a `sync.sh` object that already exists. |

### 4.7 Sweeper

`terraform/aws/sweeper.tf` deploys the sweeper of [spec 0013](0013-aws-sweeper-spec.md). It does not depend on `grant_billable_provisioning` ([ADR 0015](../decisions/0015-two-independent-controls-replace-the-terraform-allocation-guard.md) section 1 item 3a).

- `data "archive_file" "sweeper"` zips `../../sweeper/aws` into `.terraform/ftg-build/sweeper.zip`, with the excludes of the prober zip (section 4.5).
- `aws_lambda_function.sweeper`, `<name_prefix>-sweeper`: `python3.12`, `arm64`, `handler.lambda_handler`, 128 MB, 120 s, `filename` and `source_code_hash` from the zip, no `vpc_config`. Its environment: `FTG_EXPERIMENT` (`local.experiment`), `FTG_REGIONS` (the sorted Regions of the targets file under `aws`, plus `var.region`), `FTG_DATA_BUCKET`, `FTG_MAX_AGE_SECONDS = 180` (spec 0013 section 4).
- `aws_iam_role.sweeper` and `aws_iam_role_policy.sweeper`: the 3 statements of spec 0013 section 10. No `logs:*` action and no managed policy.
- `aws_scheduler_schedule.sweeper`: `rate(1 minute)`, no flexible time window, no retry, with `aws_iam_role.sweeper_scheduler` and its 1-statement policy (`lambda:InvokeFunction` on the function).

### 4.8 Sweeper-stopped alarm

`terraform/aws/alerts.tf` builds the "sweeper stopped" alert of spec 0002 section 6A item 5 ([ADR 0024](../decisions/0024-the-aws-sweeper-alarm-reads-the-lambda-metrics.md)). It makes the 3 resources below only when `alert_email` is not empty, the same as the Azure stack ([spec 0005](0005-azure-stack-spec.md) section 3.6). It reads 2 built-in metrics and writes no log, so the logs rule of spec 0003 section 8.2 and decision D14 of spec 0002 hold.

| Resource | Settings |
|---|---|
| `aws_sns_topic.alerts` | `<name_prefix>-alerts`. |
| `aws_sns_topic_subscription.alert_email` | Protocol `email`, endpoint `alert_email`. The address must confirm the subscription ([SNS email notifications](https://docs.aws.amazon.com/sns/latest/dg/sns-email-notifications.html)). |
| `aws_cloudwatch_metric_alarm.sweeper_stopped` | `<name_prefix>-sweeper-stopped`. One metric query `successes` = `FILL(invocations, 0) - FILL(errors, 0)` (`return_data`), from `invocations` and `errors`: namespace `AWS/Lambda`, metric `Invocations` and `Errors`, dimension `FunctionName` = the sweeper function, `Sum`, period 60. `LessThanThreshold`, threshold 1, `evaluation_periods` 10, `datapoints_to_alarm` 10, `treat_missing_data = breaching`. `alarm_actions` and `ok_actions` = the topic. |

Facts: Lambda does not record `Invocations` for a throttled request, and `Errors` counts invocations that end in a function error ([types of metrics](https://docs.aws.amazon.com/lambda/latest/dg/monitoring-metrics-types.html)). A stopped schedule gives no data point, which `FILL(.., 0)` turns into 0 and `breaching` covers. An alarm evaluates every minute ([alarm evaluation](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/alarm-evaluation.html)). The alarm needs no extra right on the sweeper role. The alarm and the topic are in the same account, so the stack adds no topic policy; the first deploy confirms that a test notification arrives.

## 5. User data flow

cloud-init runs a user data shell script once, at the first boot of the instance ([run commands at launch](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/user-data.html)). The output goes to `/var/log/cloud-init-output.log`. The script runs with `set -euo pipefail` and a `retry` function (30 tries, 10 s apart) around each network step.

[ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md): this script no longer pulls the prober code or builds the venv itself. It stages `/opt/ftg/bin/sync.sh` and the 2 systemd units below, then starts the sync unit once; every later code or config change reaches the VM through that same script, on a timer, with no VM replacement and no 2nd code path for the first install ([spec 0003](0003-prober-core-spec.md) section 6A has the full mechanism).

0. Optional step, before step 1. It runs only when its variable is set.
   - 0a. When `egress_proxy_url` is set: write `/etc/apt/apt.conf.d/99proxy` with `Acquire::http::Proxy` and `Acquire::https::Proxy`. Export `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, and the 3 lower case names for the rest of the script (curl, the AWS CLI installer and `aws`, pip). `NO_PROXY` is `169.254.169.254,localhost,127.0.0.1` followed by `no_proxy`. The script does not add `.amazonaws.com`, so the EC2, STS, and S3 calls go through the proxy. The script does not write `/etc/environment`. The proxy URL holds a host name; the stack pins no proxy IP address. A validation block rejects an IPv4 address or a bracketed IPv6 address as the host.
   - 0b. When `egress_proxy_url` is set, directly after 0a: write the drop-in `/etc/systemd/system/snap.amazon-ssm-agent.amazon-ssm-agent.service.d/override.conf` with `Environment=` lines for `http_proxy`, `https_proxy`, and `no_proxy`. The values are the same as in 0a, so `no_proxy` holds `169.254.169.254`. Then run `systemctl daemon-reload` and `systemctl restart snap.amazon-ssm-agent.amazon-ssm-agent.service`. The SSM agent on Ubuntu is a snap and does not read the proxy variables of the script. AWS gives this procedure for a snap ([configure SSM Agent to use a proxy](https://docs.aws.amazon.com/systems-manager/latest/userguide/configure-proxy-ssm-agent.html)). If the restart fails, the script writes a message and continues, because the prober does not need the agent.
1. `apt-get update` and `apt-get install -y unzip python3-venv jq`. Ubuntu 24.04 LTS ships Python 3.12 ([Noble Numbat release notes](https://discourse.ubuntu.com/t/noble-numbat-release-notes/39890)). The script asserts `sys.version_info >= (3, 12)`. `jq` is new (ADR 0012): `sync.sh` uses it to read `manifest.json`'s fields.
2. Install the AWS CLI v2 from `https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip` with `unzip` and `./aws/install` ([install the AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)). The installer URLs use the names `aarch64` and `x86_64`; `uname -m` prints these names on Ubuntu (not verified). `sync.sh` uses `aws s3api` too, so the CLI must already be on `PATH` before the sync unit's first run (step 6).
3. Create `/opt/ftg`, `/opt/ftg/bin`, `/opt/ftg/releases`, and `/etc/ftg` (mode 0755), and the system user `ftg` (no home directory, shell `/usr/sbin/nologin`) if it does not exist. `sync.sh` (root) builds each release under `/opt/ftg/releases/` and owns `/opt/ftg/current`; `ftg-prober.service` runs as `ftg`, which gets read and execute only on these paths, never write ([spec 0003](0003-prober-core-spec.md) section 6A).
4. `sync.sh` is hash-named under `code/` (section 4.5), not a fixed key, so this step reads the manifest first: `aws s3api get-object --bucket <bucket> --key manifest.json <tmp>`, reads its `sync_key` field with `jq`, then `aws s3api get-object --bucket <bucket> --key <sync_key> /opt/ftg/bin/sync.sh`, then `chmod 0755`. EC2 user data has a 16 KiB limit ([`aws_instance` schema validation](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/instance)), too small to embed `sync.sh` (`terraform/sync.sh`) inline, so the script fetches it this way instead; every later tick, `sync.sh` re-reads the same manifest field itself and replaces its own installed copy when the hash changes (section 5, step 6, below; [spec 0003](0003-prober-core-spec.md) section 6A).
5. Write `/etc/ftg/env` (mode 0644):

   ```text
   AWS_DEFAULT_REGION=<region>
   ```

   The prober reads its experiment name from the targets file only (ADR 0010, section 11), never from an environment variable, so this file carries no experiment value at all: a new `experiment:` in a config update is an ordinary config change, not a reason this env file, or the gate, or the health check, would ever need to change or fail.

   Also write `/etc/ftg/sync-env` (mode 0644), `ftg-prober-sync.service`'s own `EnvironmentFile` ([spec 0003](0003-prober-core-spec.md) section 6A):

   ```text
   FTG_PROVIDER=aws
   FTG_BUCKET=<bucket>
   FTG_CONFIG_FILE=<file name of targets_file>
   FTG_PIP_EXTRA=aws
   AWS_DEFAULT_REGION=<region>
   ```

6. Write `/etc/systemd/system/ftg-prober.service` (section 6), `ftg-prober-sync.service`, and `ftg-prober-sync.timer` (`terraform/ftg-prober-sync.service` and `terraform/ftg-prober-sync.timer`, read raw with `file()` and substituted once into the user data). When `egress_proxy_url` is set, also write a `.service.d/proxy.conf` drop-in for both `ftg-prober.service` and `ftg-prober-sync.service`, each with `Environment=` lines for the 6 proxy variables: `sync.sh` itself calls `aws s3api` and `pip install` through the same proxy. The drop-ins exist before `daemon-reload`. Then `systemctl daemon-reload`, `systemctl enable ftg-prober-sync.timer`, `systemctl start ftg-prober-sync.timer`, and `systemctl start --no-block ftg-prober-sync.service`: this first run builds the first release, gates it, swaps `/opt/ftg/current` onto it, enables and starts `ftg-prober.service` itself, and health-checks it ([spec 0003](0003-prober-core-spec.md) section 6A). The script does not `enable --now ftg-prober.service` itself; `sync.sh` does, once that first build passes its gate.

The template is also valid bash, so `bash -n` checks its syntax. A literal bash `${x}` is written `$${x}` in the template.

## 6. The systemd units

```ini
[Unit]
Description=follow-the-gpus prober (AWS)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ftg
EnvironmentFile=/etc/ftg/env
ExecStart=/opt/ftg/current/venv/bin/ftg-prober run --provider aws --config /opt/ftg/current/config/<file name of targets_file> --store s3://<bucket>/
Restart=on-failure
RestartSec=30
RestartPreventExitStatus=2
TimeoutStopSec=180
NoNewPrivileges=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
```

- `Restart=on-failure` restarts the service after an unclean exit, and `RestartPreventExitStatus` lists exit codes that do not cause a restart ([systemd.service, Ubuntu 24.04 man page](https://manpages.ubuntu.com/manpages/noble/man5/systemd.service.5.html)). The prober exits with 2 on a config error ([spec 0003](0003-prober-core-spec.md), section 2). A restart does not correct a config error, so the unit stays stopped and the journal shows the error.
- The service runs as the system user `ftg`, not as root. `/opt/ftg/current` is a symlink `sync.sh` (root) maintains; `ftg` has read and execute only on everything under `/opt/ftg`, never write ([ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md) section 4; [spec 0003](0003-prober-core-spec.md) section 6A).
- `ExecStart` names `/opt/ftg/current`, never a release path directly, so this unit file never changes across a release; a code or a config update replaces only what `current` points to.
- `TimeoutStopSec=180` in the unit file is the floor. `sync.sh` raises it, with a systemd drop-in, when the release's config needs more. The slowest teardown an in-flight probe can need is `aws_odcr`'s worst case (spec 0002 section 4.9, `prober/ftg_prober/aws/odcr.py` `DEFAULTS`): `probe_timeout_seconds` (default 90s, to find out whether a create placed enough instances), then, on a failure, `teardown_timeout_seconds` (default 60s, cancel retries): 150s at the defaults, not a single instant call. On a stop, each Region lane of the prober finishes only its attempt in progress, and all lanes drain at once ([ADR 0020](../decisions/0020-provisioning-probes-run-in-region-lanes-off-the-signal-thread.md)); the slowest attempt of either cloud is a scale set attempt with a pre-teardown, so the formula counts the teardown timeout twice. Before it restarts the prober, `sync.sh` computes `max(180, probe_timeout_seconds + 2 x teardown_timeout_seconds + 30)` (240 s at the defaults) from the `aws` section of the release's config (it reads the config with the release's own `ftg_prober.config`), writes `TimeoutStopSec=<that value>` to `/etc/systemd/system/ftg-prober.service.d/stop-timeout.conf`, and runs `systemctl daemon-reload`, so that the stop the restart starts does not `SIGKILL` a probe mid-teardown. The value is not in the user data: a config change ships through the manifest and never changes the user data or replaces the VM ([ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md)). Tests: the "stop timeout" and "drop-in" checks in `terraform/tests/sync.test.sh`.
- The prober takes no secrets. boto3 uses the instance profile through IMDSv2.
- This unit is written but not enabled at first boot (section 5, step 6): `ExecStart` names a `current` path that does not exist yet. `sync.sh` runs `systemctl enable ftg-prober.service` just before every restart it makes, idempotently, so the unit is enabled after its first successful build, and a later plain reboot of the VM starts it again on its own, without waiting on the timer below.

`ftg-prober-sync.service` and `ftg-prober-sync.timer` (`terraform/ftg-prober-sync.service`, `terraform/ftg-prober-sync.timer`, shared with the Azure stack) run `sync.sh` as root, the mechanism of [spec 0003](0003-prober-core-spec.md) section 6A:

```ini
[Unit]
Description=follow-the-gpus prober sync (ADR 0012)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
TimeoutStartSec=3600
ExecStartPre=/bin/bash -c '<copy sync.trial to sync.trial.start, or remove sync.trial.start>'
ExecStart=/opt/ftg/bin/sync.sh
ExecStopPost=/bin/bash -c '<revert a failed, timed-out, or unproven trial copy; spec 0003 section 6A>'
```

The file `terraform/ftg-prober-sync.service` holds the full `ExecStartPre` and `ExecStopPost` lines. `TimeoutStartSec=3600` is 2 health windows of 900 s plus 1800 s of build time. The revert is an `ExecStopPost` because the start timeout kills the `ExecStart` cgroup.

```ini
[Unit]
Description=Run ftg-prober-sync.service on a timer (ADR 0012)

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

The 5-minute interval is a setting of the timer unit, not a Terraform variable. `ftg-prober-sync.service` sets no `EnvironmentFile=` of its own: `sync.sh` sources `/etc/ftg/sync-env` (section 5, step 5) directly, with a plain shell `.`, separate from `ftg-prober.service`'s own `EnvironmentFile=/etc/ftg/env`.

`ftg-compact.service` and `ftg-compact.timer` run `ftg-prober compact` ([spec 0003](0003-prober-core-spec.md) section 9B, [ADR 0025](../decisions/0025-daily-compaction-runs-on-the-prober-vm.md)). `sync.sh` writes both units, and the proxy drop-in of `ftg-prober.service` when it exists, each time their text differs from the file on the VM, then runs `systemctl daemon-reload` and, when the timer is not enabled, `systemctl enable --now ftg-compact.timer`. It does this from its `EXIT` trap, in a subshell, after the outcome of the release tick is settled, and not while a self-updated `sync.sh` is on trial. A failure there logs `sync.compact_units_failed` and never changes the exit code of the tick, `/opt/ftg/current`, or `ftg-prober.service`. They are not in `user_data`: a `user_data` change replaces the instance (`user_data_replace_on_change = true`, section 4.6), and a new `sync.sh` reaches the VM through the hash-named code objects with no instance change ([ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md)). The store URL is the one of `ftg-prober.service`, `s3://<bucket>/`, from `FTG_BUCKET` of `/etc/ftg/sync-env`.

`sync.sh` also runs `systemctl disable --now apt-daily-upgrade.timer apt-daily.timer` when either timer is enabled, from the same `EXIT` trap and under the same rules. Reason: the daily OS upgrade made `needrestart` restart `ftg-prober.service` at a random time near 06:00 UTC, which drains the in-flight probes. The VM now gets OS updates only when it is re-created. `disable --now` stops the timers and removes their enable links; it is idempotent, and a later tick does nothing ([systemctl](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html), [Ubuntu automatic updates](https://documentation.ubuntu.com/server/how-to/software/automatic-updates/)). The command is not in `user_data`, so no instance or VM is replaced.

```ini
[Unit]
Description=follow-the-gpus daily compaction (ADR 0025)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=ftg
EnvironmentFile=/etc/ftg/env
ExecStart=/opt/ftg/current/venv/bin/ftg-prober compact --provider aws --config /opt/ftg/current/config/<file name of targets_file> --store s3://<bucket>/
Nice=10
MemoryMax=384M
TimeoutStartSec=3h
NoNewPrivileges=yes
PrivateTmp=yes
```

```ini
[Unit]
Description=Run ftg-compact.service 4 times a day (ADR 0025)

[Timer]
OnCalendar=*-*-* 00/6:30:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

`OnCalendar=` runs the job at 00:30, 06:30, 12:30, and 18:30 UTC; the 00:30 run is the first after a day closes (spec 0003 section 9B, item 2). `Persistent=true` runs a missed run once when the timer starts again ([systemd.timer](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html)). systemd does not start a 2nd run of a unit that still runs. `MemoryMax=` caps the job's memory so it cannot take the prober's memory on the 2 GiB `t4g.small` ([systemd.resource-control](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html)). 384 MiB is about 2 times about 190 MiB, the peak of `compact_day` on the largest real day and probe type, `aws_spot_price` of 2026-10-19 (11.2 MB raw), with boto3 loaded, measured as macOS RSS. The full job on the VM is not measured yet. `TimeoutStartSec=3h` is an estimate, not a measurement: it must cover the first run, which builds every existing day.

## 7. Outputs

| Output | Value |
|---|---|
| `bucket_name` | The bucket name. |
| `store_url` | `s3://<bucket>/`. The `--store` value of the prober and the store of the dashboard. |
| `instance_id` | The instance ID. |
| `region` | `var.region`. |
| `experiment` | The `experiment` of the targets file in `var.targets_file`. |
| `target_regions` | The sorted, unique Regions that the `aws` section of that file names: `jobs[].regions`, `provisioning_probes[].region`, `quota.regions`. |
| `sweeper_function_name` | The name of the sweeper function (section 4.7). |
| `sweeper_stopped_alarm_name` | The name of the sweeper-stopped alarm, or `null` without `alert_email` (section 4.8). |

## 8. Make targets

All targets run plain OpenTofu as `$(MISE_EXEC) tofu -chdir=terraform/aws`. The stack reads its values from `terraform/aws/terraform.tfvars`, and the AWS CLI login gives the credentials. No variable is exported. `plan/aws`, `apply/aws`, and `deploy/aws` first run the settings check of [spec 0014](0014-config-and-deploy-spec.md) section 6: with no `terraform/aws/terraform.tfvars`, they print 1 line and exit 2. Their `init` adds `-backend-config=backend.hcl` when `terraform/aws/backend.hcl` exists. `AUTO_APPROVE=1` adds `-auto-approve`.

| Target | Steps |
|---|---|
| `plan/aws` | `tofu init -input=false` (with `-backend-config=backend.hcl` only when that file exists), then `tofu plan -input=false -out=tfplan`. |
| `apply/aws` | `tofu apply -input=false tfplan`: the saved plan only. It fails when there is no `tfplan`. |
| `destroy/aws` | `tofu init -input=false`, then `tofu destroy -input=false`. The help text starts with "WARNING!!!". |
| `status/aws` | `uv run terraform/aws/status.py <instance_id> <bucket_name> <region> <experiment> <target_regions>` with the outputs `instance_id`, `bucket_name`, `region`, `experiment`, and `target_regions` (`tofu output`; the last as `-json`). The stack computes `experiment` and `target_regions` from its own `targets_file` variable, so the target never reads another file. `target_regions` is the sorted set of Regions that the `aws` section names (`jobs[].regions`, `provisioning_probes[].region`, `quota.regions`); a null value in the file counts as no Region, so the apply does not fail on it. If the set is empty, the script prints that no Region was checked and exits 1, and it prints no `leaked resources` line. The script prints the instance state from `DescribeInstances`, then, for each of those Regions, the Capacity Reservations with the `experiment` tag in state `active` or `pending` from `DescribeCapacityReservations` ([API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityReservations.html)): id, instance type, count, `CreateDate`, and `EndDate`. A reservation older than 180 s (the sweeper grace period) is marked `LEAKED`. The script prints the total and the line `leaked resources: N`, the same line as `make status/azure`. Then it prints the count of objects and the newest 5 keys by `LastModified` from `ListObjectsV2`. It declares `boto3[crt]` in inline script metadata, so `uv` makes the environment ([uv: running scripts](https://docs.astral.sh/uv/guides/scripts/)). The `crt` extra must be present. Without it, a profile that uses `aws login` fails with `MissingDependencyException`. `ListObjectsV2` returns pages of up to 1,000 objects in key order ([ListObjectsV2](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html)), so the script reads all pages and sorts. The operator credentials need `ec2:DescribeInstances`, `ec2:DescribeCapacityReservations`, and `s3:ListBucket`. The AWS CLI is not a pinned tool, so the target does not use it. |
| `verify/aws` | Reads `bucket_name`, `instance_id`, `region`, and `experiment` with `tofu output -raw` ([spec 0014](0014-config-and-deploy-spec.md) section 4). Prints the newest row object and the newest log object under `experiment=<name>/`, the sha256 of `manifest.json`, the release that runs on the VM, and the state and start time of `ftg-prober`. Exits 1 unless the release equals the manifest hash and the service is active ([spec 0014](0014-config-and-deploy-spec.md) section 6). |
| `lint/aws` | `tofu fmt -check -recursive`, `tofu init -backend=false -input=false`, `tofu validate`. No credentials. |
| `test/aws` | `bash -n terraform/aws/user_data.sh.tftpl`, `tofu init -backend=false -input=false`, `tofu test`. No credentials. |

## 9. Tests

`terraform/aws/tests/stack.tftest.hcl` uses `mock_provider` blocks for `aws`, `archive`, and `random` ([OpenTofu test mocking](https://opentofu.org/docs/cli/commands/test/); timing differs from [Terraform's](https://developer.hashicorp.com/terraform/language/tests/mocking), ADR 0014). `override_data` blocks give the zone list, the VPC ID and the zone of the subnet, the IPv6 CIDR of the VPC, the AMI ID, and the zip hashes, and the IDs of the 2 route table lookups. `override_resource` blocks give the bucket ARN and the random suffix. A file-level `variables` block sets `vpc_id` and `subnet_id`. 42 runs:

1. `defaults` (apply): the bucket name and settings, the 6 base policy statements (`ReadCode`'s 3-key `Resource` list and `DenyWriteCode`, ADR 0012; `ReadRowsForCompaction` and `ListBucketForCompaction`, ADR 0025; no statement allows `s3:DeleteObject`), the SSM attachment, the code keys, the `manifest.json` and `sync.sh` fixed-key objects (section 4.5), the instance AMI, type, subnet (`subnet_id`), the security group VPC (`vpc_id`), IMDSv2, `user_data_replace_on_change`, the user data content (it fetches `sync.sh`, writes `sync-env`, writes the `ftg-prober.service` unit pointed at `/opt/ftg/current`, and starts the sync timer and unit, not the old zip-key/venv-build steps), the created group (no ingress, all IPv4 egress, no IPv6 rule), and the outputs. `outputs_follow_the_targets_file` (apply, `targets_file` = a fixture) pins `experiment` and `target_regions` to that file.
2. `no_ssm_and_custom_prefix` (apply): `enable_ssm = false` attaches nothing; `name_prefix`, a `targets_file` with a different `experiment`, and `region` reach the names and the user data.
3. `rejects_upper_case_prefix` (plan): `name_prefix = "FTG"` fails the validation.
4. `rejects_reserved_s3_prefix` (plan): `name_prefix = "sthree-ftg"` fails the validation.
5. `vpc_id_rejects_empty` and `vpc_id_rejects_a_subnet_id` (plan): an empty `vpc_id` and a subnet ID in `vpc_id` fail the validation.
6. `subnet_id_rejects_empty` and `subnet_id_rejects_a_vpc_id` (plan): an empty `subnet_id` and a VPC ID in `subnet_id` fail the validation.
7. `subnet_in_another_vpc_fails` (plan): a `vpc_id` that is not the VPC of the subnet fails the precondition.
8. `subnet_zone_without_the_type_fails` (plan): a zone list without the zone of the subnet fails the precondition.
9. `s3_endpoint_is_off_by_default` (plan): the default creates no endpoint.
10. `s3_endpoint_on_uses_the_subnet_route_table` (plan): `enable_s3_gateway_endpoint = true` creates 1 gateway endpoint for `com.amazonaws.us-east-1.s3` in `vpc_id`, on the route table of the subnet.
11. `s3_endpoint_falls_back_to_the_main_route_table` (plan): a subnet with no own table gives the main route table, and `region` reaches the service name.
11a. `allocation_probe_iam_is_off_by_default` (apply) and `allocation_probe_iam` (apply): `grant_billable_provisioning` defaults to `false`, keeping exactly the 6 base statements (ADR 0012 adds `DenyWriteCode`; ADR 0025 adds `ReadRowsForCompaction` and `ListBucketForCompaction`), regardless of the targets file; `grant_billable_provisioning = true` adds the 5 `aws_odcr` statements (only the 2 read statements when no `aws.provisioning_probes` entry names a Region), their actions, and their tag conditions (section 4.3), again regardless of the file.

12. `proxy_option_is_off_by_default` (plan): the user data holds no proxy, drop-in, or template directive line.
13. `proxy_url_sets_apt_env_and_dropin` (plan): the proxy URL reaches the apt file, the exports, and the drop-in. `NO_PROXY` holds the 3 fixed hosts and `no_proxy`. The script has no `/etc/environment` and no `.amazonaws.com` bypass.
13a. `proxy_url_sets_ssm_agent_dropin` (plan): the user data holds the SSM agent drop-in with the 3 variables, `169.254.169.254` in `NO_PROXY_LIST`, and the restart command. `proxy_option_is_off_by_default` shows that none of it exists by default.
14. `ipv6_vpc_adds_the_ipv6_egress_rule` (plan): a VPC with an IPv6 CIDR block gives 1 IPv6 rule (`-1`, `::/0`), and the IPv4 rule stays. `supplied_groups_join_the_created_one` (plan): 2 given groups and the created group are on the instance. `no_created_group_uses_only_the_given_groups` (plan): `create_security_group = false` creates no group and no rule, and the instance has only the given group. `no_created_group_and_no_given_group_fails` (plan): `false` with an empty list fails the precondition.
15. `rejects_a_bad_proxy_url`, `rejects_a_proxy_url_with_a_path`, `rejects_a_bad_security_group_id`, `rejects_a_proxy_port_of_zero`, `rejects_a_proxy_port_above_65535`, `rejects_a_proxy_url_with_an_ipv4_host`, and `rejects_a_proxy_url_with_an_ipv6_host` (plan): each fails its validation.
16. `code_objects_set_empty_tags_and_metadata` (plan): `tags` and `metadata` are empty maps on both `aws_s3_object` resources.
17. `custom_targets_file_is_read_on_the_vm` (apply): `targets_file = "examples/targets.example.yaml"` makes the unit's `ExecStart` read `/opt/ftg/current/config/targets.example.yaml`, not a fixed `targets.yaml` (ADR 0012).
18. `allocation_probe_iam_stays_off_with_an_active_consented_entry` (apply): a targets file with an active, consented entry and `grant_billable_provisioning` still `false` adds no allocation statement (ADR 0015).
19. `units_in_user_data` (apply): the user data holds `User=ftg` and no `ReadWritePaths`, the sync unit's `TimeoutStartSec=3600`, and its `ExecStopPost` revert.
20. `user_data_holds_no_code_hash` (apply): the user data holds none of the zip hashes or the code, config, and `sync.sh` object keys, and `manifest.json` holds the prober hash (ADR 0012).
21. `user_data_unchanged_by_new_zip_hashes` (apply): new zip hashes change `manifest.json` and leave the user data without them.
22. `new_image_does_not_change_the_instance` (apply, after `defaults`): a new value of the AMI parameter leaves `aws_instance.prober.ami` at the first value. The mock provider cannot show "no diff" directly; the assert shows the AMI stays, and the test fails without `ignore_changes` (the update, or a replacement, gives `ami-1new`).

A variable with no default stops `tofu test` before the plan when no value is set, thus the tests check the validation of empty and wrong values.

A mock provider fills each computed attribute with a generated value. Thus the tests assert only on attributes that the config sets.

`terraform/aws/tests/code_zip_location.tftest.hcl` does not mock the `archive` provider, so its `zip_builds_outside_dist` run builds the 3 zips for real from `../../prober`, `../../config`, and `../../sweeper/aws` and asserts that `output_path` holds `.terraform/`, not `dist/` (section 4.5). `scripts/check-stale-build-artifact.sh`, run by `make test`, plants a stale `dist/prober.zip`, `dist/config.zip`, and `dist/sweeper.zip`, runs that test, and checks that the real build output under `.terraform/ftg-build/` is not the stale file.

`terraform/aws/tests/sweeper.tftest.hcl` (8 runs, with `tests/fixtures/sweeper_regions.yaml`) pins the sweeper of section 4.7: the function settings and environment, no `vpc_config`, the schedule and its target and retry policy, the scheduler role's 1 statement, the 3 statements of the sweeper role with their exact actions, resources, and conditions (the `experiment` resource tag, the Regions), no `logs:*` action in either sweeper policy, the stack Region for a targets file with no Region under `aws`, and the sweeper with `grant_billable_provisioning` `false` and `true`. It also pins the alarm of section 4.8: no topic, no subscription, no alarm, and a `null` output without `alert_email`; with it, the email subscription, the alarm settings, the 3 metric queries, the notification of the topic on `ALARM` and `OK`, and the validation error for a bad address. Both test files override the ARNs of the 2 sweeper roles, of the function, and of the topic, because the provider checks them at plan time (CONTRIBUTING.md, OpenTofu tests).

`allocation_probe_iam_is_off_by_default` and `allocation_probe_iam`, in `stack.tftest.hcl` (item 11a, above), are the only tests of `grant_billable_provisioning`: there is no separate `allocation_guard.tftest.hcl` file, because there is no targets-file-reading guard left to test (ADR 0015). The fixtures of `prober/tests/fixtures/` that these 2 runs do not touch (`provisioning_guard.yaml`, `provisioning_guard_aws.yaml`, and their companions) still exist, but only `prober/tests/test_config.py` loads them now; they test the prober's own, unchanged per-job consent guard.
`terraform/tests/sync.test.sh` (`make test/sync`) tests `sync.sh` itself, shared with the Azure stack: a fakebin directory stands in for `aws`, `fetch-blob.sh`, `systemctl`, `journalctl`, and the release venv (`python3`, `pip`, `ftg-prober`), so each tick is deterministic and makes no real network, cloud, or systemd call. It covers the cases of [spec 0003](0003-prober-core-spec.md) section 6A: a good first boot, a first boot that fails the gate or the health check, a normal update (good, a sha256 mismatch, a transient and a tripped download failure), an unchanged manifest, `manifest.failed` blocking a retry until a new manifest arrives, a rollback that succeeds and 1 that also fails terminally, a revert that reuses an existing release, and the Azure `BlobNotFound` case.

## 10. Operations

- **Day 1 quota request (spec/0002 D20).** Request an increase of the Spot quotas `All P Spot Instance Requests` and `All G and VT Spot Instance Requests` in each target Region. Their default is 0 vCPUs ([Spot Instance quotas](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-limits.html)). `make quota/aws` files them, together with the On-Demand quotas of the same groups, from `aws.quota` in `config/targets.yaml` ([spec 0012](0012-aws-quota-requests-spec.md)).
- **Logs.** With `enable_ssm = true`: `aws ssm start-session --target <instance_id>` ([start-session](https://docs.aws.amazon.com/cli/latest/reference/ssm/start-session.html)), then `journalctl -u ftg-prober` or `journalctl -u ftg-prober-sync`. The operator machine needs the Session Manager plugin for the AWS CLI. With `enable_ssm = false` there is no shell access by design.
- **Destroy.** Copy the data out with `aws s3 sync`, empty the bucket with `aws s3 rm --recursive`, then `make destroy/aws`.
- **Code update.** `make plan/aws` and `make apply/aws` again upload the 2 new zips and rewrites `manifest.json` in place; the apply no longer touches the VM, so there is no replacement and no downtime at apply time ([ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md); the 1 exception is the apply that first moves an existing stack to this design, which still replaces the VM once). The VM's own `ftg-prober-sync.timer` notices the new manifest within its 5-minute interval and does the pull and the restart on its own; the only downtime is that restart, a few seconds. To update sooner than the next tick, with `enable_ssm = true`: `aws ssm start-session --target <instance_id>`, then `sudo systemctl start ftg-prober-sync.service` once. If the sync unit's health check was wrong about a build that is actually good, clear it the same way: `sudo rm /etc/ftg/manifest.failed /etc/ftg/download-failing-since` (the 2nd file may not already exist), then `sudo systemctl start ftg-prober-sync.service` again ([spec 0003](0003-prober-core-spec.md) section 6A).

## 11. Decisions

| # | Decision | Reason |
|---|---|---|
| A1 | 2 code zips, not 1. | `archive_file` takes 1 source folder (4.5). The user data downloads both. An earlier build plan said 1 zip; this spec replaces that line for AWS. |
| A2 | The created security group allows all outbound traffic. | The VM needs 443 and 80 (the Ubuntu mirrors use plain HTTP, 4.4), and DNS to a custom server, and a proxy port. A rule list for each case is fragile. The module pattern uses open egress, and the group has no ingress rule. It works in any VPC. |
| A3 | `enable_ssm` defaults to `true`. | An operator can read the logs with no inbound port and no SSH key. `false` gives the smallest role. |
| A4 | `force_destroy = false` on the bucket. | A destroy must not delete the experiment data by accident. |
| A5 | `status/aws` runs a boto3 script with `uv`. | The AWS CLI is not in `mise.toml`, and each tool must run through `mise exec`. |
| A6 | The service runs as the system user `ftg`. | The prober needs no root rights. |
| A7 | `RestartPreventExitStatus=2`. | Exit code 2 is a config error; a restart loop would hide it. |
| A8 | `/etc/ftg/env` also holds `AWS_DEFAULT_REGION`. | boto3 clients that take no explicit Region then use the Region of the stack. |
| A9 | The zip excludes also cover `.pytest_cache`, `.ruff_cache`, and `*.egg-info`. | A cache change must not change the zip hash, because a new hash replaces the VM. |
| A10 | The operator gives the subnet. A precondition checks that its zone offers the instance type. | Not every zone offers every type. The plan fails early instead of the launch. |
| A11 | The IAM policy is a `jsonencode` object. | The mock tests can decode and check it; a data source would return a mock value. |
| A12 | The key holds the full hex SHA-256 of the zip. | The hash is the version. Implementer's choice: a shorter prefix of the hash also conforms. |
| A13 | The stack deploys into an existing VPC and subnet, `vpc_id` and `subnet_id`, with no default. It does not read the default VPC. | The network belongs to the network owner. A default VPC does not exist in every account. |
| A14 | `preflight/aws` is a separate Python project in `preflight/`, with boto3. | The checks must not change the prober zip, because a new zip replaces the VM (4.5). The AWS CLI is not a pinned tool (A5). |
| A15 | No Capacity Block access is a warning. A GPU Spot quota of 0 is a warning (12.2). The preflight reads no On-Demand GPU quota. | The deploy works without Capacity Block access; only the `aws_cb_offering` rows change. The Spot quota is the day 1 request of section 10, not a deploy input. |
| A16 | The permission check is a policy simulation, and a simulation that cannot run is a warning. | The caller can lack `iam:SimulatePrincipalPolicy` and still have the deploy rights. |
| A17 | The outbound check reads the route table and is never a `FAIL`. | Outbound access belongs to the network owner (4.4). A transit gateway or firewall route can reach the internet, and the check cannot prove that it does. |
| A18 | `enable_s3_gateway_endpoint` defaults to `false`. The stack finds the route tables from `subnet_id` and takes no route table input. | The network belongs to the network owner (A13). The default leaves the network unchanged. The stack knows the subnet, so the lookup is the smallest input. |
| A19 | `egress_proxy_url` and `no_proxy` are optional. Their defaults give the same user data as before. | A network can force all traffic through a proxy. Other networks need none of it. |
| A21 | The proxy applies to the install steps, to the prober unit, and to the SSM agent unit only, by a script export and systemd drop-ins. The script does not write `/etc/environment`. | A system-wide file would also change other services. The SSM agent gets its own drop-in (step 0b), as AWS describes. |
| A22 | `169.254.169.254`, `localhost`, and `127.0.0.1` are always in `NO_PROXY`. AWS API hosts are not. | The metadata service is link-local and a proxy cannot reach it. The AWS APIs must use the proxy, because the network allows no direct path. |
| A23 | The stack accepts a proxy host name and pins no proxy IP address. | The IP address of a proxy can change. |
| A24 | The stack takes `vpc_security_group_ids` and `create_security_group` (default `true`). The instance gets the created group and the given groups. The stack never uses the default security group. | This is the pattern of [terraform-aws-modules/ec2-instance](https://github.com/terraform-aws-modules/terraform-aws-ec2-instance). The created group works in any VPC, also with custom DHCP DNS. Network owners can add their own groups, or use only theirs. The default group of a VPC allows traffic between its members and is easy to change by mistake ([default security group](https://docs.aws.amazon.com/vpc/latest/userguide/default-security-group.html)). |
| A25 | `manifest.json` is 1 more fixed-key S3 object; `sync.sh` is a 3rd hash-keyed object, like the 2 code zips, not a 2nd fixed key. | A fixed key updates in place (A12's hashed keys cannot), which is what lets the manifest always exist. `sync.sh` stays hash-verified and replaced the same way as the 2 zips; a fixed key for it was rejected because the user data would then need to embed its content (ruled out by the 16 KiB EC2 user data limit) or still name a key somewhere in the user data, which a `sync.sh`-only change would then also replace the VM for ([ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md) section 4). The manifest's own `sync_key` field, read by `sync.sh` on every tick, is what lets it self-update with no VM-side fixed key at all. |
| A26 | `DenyWriteCode` is a new, explicit `Deny` statement, not a narrower `Resource` on `WriteRecords`. | An explicit `Deny` always wins, so it needs no tracking of the prober's own data key layout, which is superseded and not yet implemented (ADR 0010). |

Implementer's choice: the root disk size (8 GB or more), the `RestartSec` value, the retry count and delay in the user data, the `description` texts, and the 5-minute `ftg-prober-sync.timer` interval (ADR 0012 leaves it a setting of the timer unit, not a variable).

## 12. Prerequisites and preflight

The README section "Before you deploy" gives the same list for the operator, with the cost.

### 12.1 Prerequisites

| Item | Requirement | If it is missing |
|---|---|---|
| Credentials | The AWS CLI login: the default profile, or the profile that `AWS_PROFILE` names, has working credentials. For a profile from `aws login`, the OpenTofu AWS provider needs a second profile with `credential_process` (README, Prerequisites). | The deploy fails. |
| Rights | The caller can run the actions of 12.3. | The deploy fails. |
| Network inputs | `vpc_id` and `subnet_id` are set in `terraform/aws/terraform.tfvars` (section 3). | The plan stops: the variables are required and have no default. |
| Network | The VPC and the subnet exist in `region`. The subnet is in `vpc_id`, and its zone offers `instance_type` (4.1). | The plan fails on the data source or on a precondition. |
| Outbound access | Outbound access from the subnet (4.4): a NAT gateway, an internet gateway, or a proxy (`egress_proxy_url`). The network owner sets it up before the deploy. | The deploy succeeds, but the first boot cannot install the prober. |
| VM quota | The On-Demand vCPU quota of the family of `instance_type` is at least the vCPU count of the type. The default `t4g.small` has 2 vCPUs. Its quota is "Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances", code `L-1216C47A`. Running instances also count. | The instance launch fails. |
| Spot quota | The day 1 request of section 10: "All P Spot Instance Requests", code `L-7212CCBC`, and "All G and VT Spot Instance Requests", code `L-3819A6DF`. Their default is 0 vCPUs. The Service Quotas console links of that page end in the 2 codes ([Spot Instance quotas](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-limits.html)). The preflight reads both in `region` and warns on 0. | No deploy gap. The `aws_spot_score` rows can show `outcome = cap`. |
| Capacity Block access | The account can call `DescribeCapacityBlockOfferings`. Without access, the call returns `CapacityBlockDescribeLimitExceeded` (spec 0008, section 3.4). | No deploy gap. Each `aws_cb_offering` row is an `error` row. |

The quota codes are the codes that the Service Quotas API lists for EC2. A quota without an account value has an AWS default value ([GetAWSDefaultServiceQuota](https://docs.aws.amazon.com/servicequotas/2019-06-24/apireference/API_GetAWSDefaultServiceQuota.html)).

### 12.2 `make preflight/aws`

The target runs `tofu -chdir=terraform/aws init -input=false`, which reads the backend (a local state file by default, or the remote backend of `backend_override.tf`), then `uv run --locked python -m ftg_preflight aws` in `preflight/`. The arguments are the stack folder and the experiment file (default `config/targets.yaml`). The inputs come from `terraform/aws/terraform.tfvars` through `tofu console`.

Inputs:

1. The variables `region`, `instance_type`, `vpc_id`, and `subnet_id`, from `tofu console` with `jsonencode(var.<name>)`. The console applies the defaults, `terraform.tfvars`, the `*.auto.tfvars` files, and `TF_VAR_*` ([OpenTofu `console`](https://opentofu.org/docs/cli/commands/console/); [variable assignment](https://opentofu.org/docs/language/values/variables/#assigning-values-to-root-module-variables)). It reads no cloud API for a variable. For a required variable with no value, the console prints `(known after apply)`. The preflight code and test record this output (`preflight/ftg_preflight/terraform.py`, `UNSET_MARK`; `preflight/tests/test_cli.py`, `decode_console_json`). The preflight reads it as not set.
2. `aws.capacity_blocks` from the targets file: the `aws_cb_offering` job's settings (code defaults merged with its `modules:` override, ADR 0010 section 3). Empty when the file lists no `aws_cb_offering` job.
3. `AWS_REGION`, and the active profile of `~/.aws/config` (the default profile, or `AWS_PROFILE`).

Rules:

1. Each AWS call only reads. The checks write no file and change no account setting. The target runs `tofu init`, which writes only `terraform/aws/.terraform/` and reads the backend. It writes no state.
2. Each check gives 1 or more findings. A finding has a level, a check name, a message, and a fix.
3. `FAIL` is a gap that blocks the deploy. `WARN` is a signal gap, or a check that cannot run. `PASS` is no gap.
4. Exit code 0: no `FAIL`. Exit code 1: 1 or more `FAIL`. Exit code 2: OpenTofu or the targets file cannot be read.
5. The output has 1 line per finding: `<LEVEL>  <check>: <message>`, then an indented line `fix: <fix>` for a `WARN` or `FAIL` with a fix, then a count line.
6. A message names no account ID. The credentials check prints the kind and the name of the principal only.
7. The project depends on `boto3[crt]`. botocore needs the `crt` extra to read an `aws login` profile; without it, boto3 stops with `MissingDependencyException` (author test, 2026-10-12).

Checks, in order:

| Check | Calls | Result |
|---|---|---|
| `aws/region` | none | `WARN` when `AWS_REGION` is set and differs from `region`. |
| `aws/profile` | none | `WARN` when the profile, or a `source_profile` in its chain, has `login_session` and none has `credential_process`. |
| `aws/network-inputs` | none | `FAIL` when `vpc_id` or `subnet_id` is not set. Then the network checks do not run. |
| `aws/credentials` | STS `GetCallerIdentity` | `FAIL` on an error. The other checks do not run. |
| `aws/permissions` | IAM `GetRole` for an assumed role, then `SimulatePrincipalPolicy` | `FAIL` when an action of 12.3 is not `allowed`. `WARN` when the simulation cannot run. `PASS` for the root user, with no simulation. |
| `aws/vpc` | EC2 `DescribeVpcs` with `VpcIds = [vpc_id]` | `FAIL` on `InvalidVpcID.NotFound`. |
| `aws/subnet` | EC2 `DescribeSubnets` with `SubnetIds = [subnet_id]`, `DescribeInstanceTypeOfferings` | `FAIL` on `InvalidSubnetID.NotFound`, when the `VpcId` of the subnet is not `vpc_id`, or when the zone of the subnet does not offer `instance_type`. These are the 2 preconditions of 4.1, before the plan. |
| `aws/outbound` | EC2 `DescribeRouteTables` for the subnet association, else for the main route table of the VPC. `DescribeNatGateways` for a NAT gateway target. | `PASS` when the IPv4 default route (`0.0.0.0/0`, not `blackhole`) goes to a public NAT gateway, or to an internet gateway while the subnet has `MapPublicIpOnLaunch`. Else `WARN`: no default route, a private NAT gateway, an internet gateway with no public IP, another target, or a read error. Never `FAIL`, because the check cannot prove a path to the internet. |
| `aws/vm-quota` | EC2 `DescribeInstanceTypes`, Service Quotas `GetServiceQuota` (or `GetAWSDefaultServiceQuota`) | `FAIL` when the quota value is under the vCPU count. `WARN` for a family with no known quota code. The known codes are the standard families only; the check reads no quota of a GPU or accelerator family. |
| `aws/spot-quota` | Service Quotas `GetServiceQuota` (or `GetAWSDefaultServiceQuota`) for `L-7212CCBC` and `L-3819A6DF` in `region` | 1 finding for each quota. `WARN` when the value is 0; the message names the quota. `PASS` when the value is more than 0. Never `FAIL`. A read error goes to `aws/api`. The check calls no quota request API, for example no `RequestServiceQuotaIncrease`. |
| `aws/capacity-blocks` | EC2 `DescribeCapacityBlockOfferings` for the first type, count, and duration of the resolved `aws.capacity_blocks` (item 2, above) | `WARN` on an error. The message names `CapacityBlockDescribeLimitExceeded` as no access. |
| `aws/api` | the reads of `aws/vpc`, `aws/subnet`, `aws/vm-quota`, and `aws/spot-quota` | `FAIL` on a read error that the check does not handle, for example `AccessDenied` on `DescribeVpcs`. The rest of that step stops; the next steps still run. |

The simulation evaluates the identity-based policies and the service control policies of the principal. It does not make the calls. AWS says that "the policy simulator results can differ from your live AWS environment" ([SimulatePrincipalPolicy](https://docs.aws.amazon.com/IAM/latest/APIReference/API_SimulatePrincipalPolicy.html)). `PolicySourceArn` takes "a user, group, or role", and a caller ARN cannot be "the ARN of an assumed role" ([same page](https://docs.aws.amazon.com/IAM/latest/APIReference/API_SimulatePrincipalPolicy.html)). Thus the check maps an assumed-role ARN to its role ARN with `GetRole`.

Facts behind the outbound check:

- A subnet that has no explicit route table association "is implicitly associated with the main route table" ([Subnet route tables](https://docs.aws.amazon.com/vpc/latest/userguide/subnet-route-tables.html)).
- "To enable communication over the internet for IPv4, your instance must have a public IPv4 address", and the route table of the subnet needs a route to the internet gateway ([Internet gateways](https://docs.aws.amazon.com/vpc/latest/userguide/VPC_Internet_Gateway.html)).
- Through a public NAT gateway, "Instances in private subnets can connect to the internet". A private NAT gateway connects only "to other VPCs or your on-premises network" ([NAT gateways](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-nat-gateway.html)).
- A route to a transit gateway or a firewall can also reach the internet. The check cannot follow it, so it gives a `WARN`. The instance sets no `associate_public_ip_address`, so `MapPublicIpOnLaunch` of the subnet decides the public IP (4.4).

### 12.3 Deploy actions

The simulation checks these actions on resource `*`: `iam:CreateRole`, `iam:PutRolePolicy`, `iam:AttachRolePolicy`, `iam:CreateInstanceProfile`, `iam:AddRoleToInstanceProfile`, `iam:PassRole`, `ec2:DescribeSubnets`, `ec2:DescribeInstanceTypeOfferings`, `ec2:CreateSecurityGroup`, `ec2:AuthorizeSecurityGroupEgress`, `ec2:RevokeSecurityGroupEgress`, `ec2:RunInstances`, `ec2:CreateTags`, `ssm:GetParameter`, `s3:CreateBucket`, `s3:PutBucketPublicAccessBlock`, `s3:PutEncryptionConfiguration`, `s3:PutLifecycleConfiguration`, `s3:PutBucketVersioning`, `s3:PutBucketTagging`, `s3:PutObject`. The list holds the main create actions of the stack, not each read action of the provider. Implementer's choice: more actions. The list leaves out the actions of the optional S3 gateway endpoint (`ec2:CreateVpcEndpoint`, `ec2:DescribeRouteTables`). The preflight does not read `enable_s3_gateway_endpoint`.

### 12.4 Tests

`make preflight/test` runs pytest in `preflight/`, with no network and no credentials. Fakes replay the recorded responses in `preflight/tests/fixtures/aws.json`, in the shape of the AWS API responses. The tests cover each `PASS`, `WARN`, and `FAIL` path of the checks table, the role ARN mapping, the default quota fallback, the main route table fallback, a public and a private NAT gateway, the exit codes, and the `tofu console` parsing, also of an unset variable. A test checks that the only quota codes read are `L-1216C47A`, `L-7212CCBC`, and `L-3819A6DF`, and that the check calls no quota request API. `make preflight/lint` runs ruff. `make lint` and `make test` include both targets.

Open: the first run against a real account.

## 13. Sources

- Canonical, find Ubuntu images on AWS: <https://ubuntu.com/aws/docs/aws-how-to/instances/find-ubuntu-images/>
- Ubuntu 24.04 LTS release notes: <https://discourse.ubuntu.com/t/noble-numbat-release-notes/39890>
- AWS, EC2 service reference: <https://servicereference.us-east-1.amazonaws.com/v1/ec2/ec2.json>
- AWS, Spot placement score IAM permission: <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/sps-iam-permission.html>
- AWS, S3 PutObject: <https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html>
- AWS, S3 GetObject: <https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html>
- AWS, bucket naming rules: <https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucketnamingrules.html>
- AWS, default bucket encryption: <https://docs.aws.amazon.com/AmazonS3/latest/userguide/default-bucket-encryption.html>
- AWS, AmazonSSMManagedInstanceCore: <https://docs.aws.amazon.com/aws-managed-policy/latest/reference/AmazonSSMManagedInstanceCore.html>
- AWS, AMIs with SSM Agent preinstalled: <https://docs.aws.amazon.com/systems-manager/latest/userguide/ami-preinstalled-agent.html>
- AWS, default VPCs: <https://docs.aws.amazon.com/vpc/latest/userguide/default-vpc.html>
- AWS, default subnets: <https://docs.aws.amazon.com/vpc/latest/userguide/default-subnet.html>
- AWS, security groups: <https://docs.aws.amazon.com/vpc/latest/userguide/vpc-security-groups.html>
- AWS, default security group: <https://docs.aws.amazon.com/vpc/latest/userguide/default-security-group.html>
- Terraform module for EC2 instances (`variables.tf`, `main.tf`): <https://github.com/terraform-aws-modules/terraform-aws-ec2-instance>
- AWS, gateway endpoints: <https://docs.aws.amazon.com/vpc/latest/privatelink/gateway-endpoints.html>
- AWS, route tables: <https://docs.aws.amazon.com/vpc/latest/userguide/VPC_Route_Tables.html>
- AWS, ListObjectsV2: <https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html>
- AWS, SSM start-session: <https://docs.aws.amazon.com/cli/latest/reference/ssm/start-session.html>
- cloud-init, module reference (apt): <https://docs.cloud-init.io/en/latest/reference/modules.html>
- AWS, DescribeInstanceTypeOfferings: <https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeInstanceTypeOfferings.html>
- AWS, instance metadata options: <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-options.html>
- AWS, run commands at launch (user data): <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/user-data.html>
- AWS, install the AWS CLI: <https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html>
- AWS, Spot Instance quotas: <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-limits.html>
- HashiCorp, archive_file: <https://registry.terraform.io/providers/hashicorp/archive/latest/docs/data-sources/file>
- HashiCorp, aws_instance: <https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/instance>
- OpenTofu, `tofu test`: <https://opentofu.org/docs/cli/commands/test/>
- HashiCorp, Terraform test mocking: <https://developer.hashicorp.com/terraform/language/tests/mocking>
- systemd, systemd.service (Ubuntu 24.04 man page): <https://manpages.ubuntu.com/manpages/noble/man5/systemd.service.5.html>
- HashiCorp, terraform-provider-archive source: <https://github.com/hashicorp/terraform-provider-archive>
- Astral, uv scripts: <https://docs.astral.sh/uv/guides/scripts/>
- AWS, IAM SimulatePrincipalPolicy: <https://docs.aws.amazon.com/IAM/latest/APIReference/API_SimulatePrincipalPolicy.html>
- AWS, Service Quotas GetAWSDefaultServiceQuota: <https://docs.aws.amazon.com/servicequotas/2019-06-24/apireference/API_GetAWSDefaultServiceQuota.html>
- HashiCorp, `create_before_destroy` and a dependent resource: <https://github.com/hashicorp/terraform/blob/main/docs/destroying.md>
- AWS, IAM policy evaluation logic (an explicit Deny overrides an Allow): <https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic.html>
- HashiCorp, `aws_s3_object` resource schema (`bucket` and `key` are the only `ForceNew` fields): <https://github.com/hashicorp/terraform-provider-aws/blob/main/internal/service/s3/object.go>

Sources of single facts in the text:

- [`t4g.small` is an arm64 instance type](https://aws.amazon.com/ec2/instance-types/t4/)
- [`aws_ssm_parameter` `insecure_value` is not sensitive, so it shows in the plan](https://registry.terraform.io/v2/provider-docs/14011436)
- [`aws_ec2_instance_type_offerings` accepts `location_type = "availability-zone"` and filters](https://registry.terraform.io/v2/provider-docs/14011008)
- [DescribeSubnets filters `vpc-id`, `default-for-az`, `availability-zone` exist (used by `aws_subnets`)](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeSubnets.html)
- [`random_id` with `byte_length = 4` gives an 8-character hex](https://registry.terraform.io/v2/provider-docs/13616545)
- [Public access block has 4 settings: BlockPublicAcls, BlockPublicPolicy, IgnorePublicAcls, RestrictPublicBuckets](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PublicAccessBlockConfiguration.html)
- [`archive_file` takes 1 source folder per archive; `output_file_mode` makes the hash independent of local file modes](https://github.com/hashicorp/terraform-provider-archive/blob/main/docs/data-sources/file.md)
- [`aws_s3_object` `source_hash` triggers an update when the file changes](https://registry.terraform.io/v2/provider-docs/14012825)
- [`user_data_replace_on_change = true` makes a user data change replace the instance; Instance without `key_name` has no SSH key](https://registry.terraform.io/v2/provider-docs/14012313)
- [Terraform templates escape a literal `${` as `$${`](https://developer.hashicorp.com/terraform/language/expressions/strings)
- [boto3 uses `AWS_DEFAULT_REGION` as the default Region](https://docs.aws.amazon.com/boto3/latest/guide/configuration.html)
- [Provider constraints are satisfiable: aws `~> 6.0`, archive `~> 2.7`, random `~> 3.7`](https://registry.terraform.io/v1/providers/hashicorp/aws)
