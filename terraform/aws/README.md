# AWS stack

OpenTofu for the AWS part of the rig: 1 small prober VM, its IAM role, and 1 S3 data bucket. The spec is [spec/0004](../../spec/0004-aws-stack-spec.md). The design comes from [spec/0002](../../spec/0002-gpu-capacity-tracker-spec.md), sections 3 and 8.2.

The VM runs `ftg-prober` as a systemd service. The prober reads 3 free EC2 APIs and the public Spot Advisor feed, and writes 1 JSONL file per probe call to the bucket. The stack creates no other billable resource. A public IPv4 address of the VM, if the subnet assigns one, also has an hourly charge (see [Cost](#cost)).

## Prerequisites

- OpenTofu and the other tools from `mise.toml`. With mise, run `make init` at the repo root. mise is optional ([prerequisites](../../docs/prerequisites.md#tools)).
- An existing VPC and subnet in the target Region. The stack creates no VPC and no subnet. The zone of the subnet must offer `instance_type`.
- Outbound internet access from that subnet (see [Network](#network)). The network owner sets it up before the deploy, like the network itself.
- Credentials with rights to create IAM roles, EC2 instances, security groups, and S3 buckets.
- Your AWS CLI login selects the account: the default profile, or the profile that `AWS_PROFILE` names. The stack reads its Region from the variable `region` in `terraform.tfvars` (default `us-east-1`). If you set `AWS_REGION` for the AWS CLI, set it to the same value.

- If you sign in with `aws login`, the profile holds `login_session`. In a test deploy of this stack, the Terraform AWS provider failed to use such a profile, or a profile with such a `source_profile`. The AWS CLI used it. AWS documents a fix for tools that do not support console credentials. A second profile gets its credentials from the CLI through `credential_process` ([Sharing Login credentials as process credentials](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html#cli-configure-sign-in-cached-credentials)). Add this profile to `~/.aws/config`, and set `AWS_PROFILE=ftg-process`:

  ```ini
  [profile ftg-process]
  credential_process = aws configure export-credentials --profile my-profile --format process
  region = us-east-1
  ```

  `aws configure export-credentials` prints the credentials of a profile in the JSON form of `credential_process` ([export-credentials](https://docs.aws.amazon.com/cli/latest/reference/configure/export-credentials.html)). The SDKs run the command and read the JSON ([Process credential provider](https://docs.aws.amazon.com/sdkref/latest/guide/feature-process-credentials.html)). Run `aws login --profile my-profile` again when the session ends.

- Copy [terraform.tfvars.example](terraform.tfvars.example) to `terraform/aws/terraform.tfvars`, and put in your values. Git ignores `terraform.tfvars`. OpenTofu reads it by itself ([variable definitions files](https://opentofu.org/docs/language/values/variables/#variable-definitions-tfvars-files)). `vpc_id` and `subnet_id` are required and have no default:

  ```sh
  cp terraform/aws/terraform.tfvars.example terraform/aws/terraform.tfvars
  ```

- `tags` (optional) adds tags to each resource of the stack, for example the tags that a tag policy of your account requires. The provider sets them as default tags, next to `experiment = <name>`, the targets file's own `experiment` value (ADR 0010 section 2): the stack reads it from the file, not from a variable. A key `experiment` in `tags` is replaced by that value.

- `enable_s3_gateway_endpoint` (optional, default `false`) creates an S3 gateway endpoint in `vpc_id`. It joins the route table of `subnet_id`, or the main route table when the subnet has no own table. It adds an S3 route to every subnet that uses that table. It does not replace the outbound path. The VM still needs outbound access to the EC2, STS, and SSM APIs, the package mirrors, and the AWS CLI download (see [Network](#network)). Set it when you want the S3 traffic of the VM to stay inside AWS.
- `grant_billable_provisioning` (optional, default `false`) adds the `aws_odcr` provisioning probe's IAM actions to the role: create, cancel, and describe On-Demand Capacity Reservations. This is the only thing that decides whether the role gets these rights; it does not read the targets file ([ADR 0015](../../decisions/0015-two-independent-controls-replace-the-terraform-allocation-guard.md)). Set it to `true` only when you also want an `aws.provisioning_probes` entry to run for real; the targets file's own `allow_billable_provisioning` key is a separate control the prober checks, not this variable.

### Sweeper-stopped alarm

When `alert_email` is set, the stack adds 1 CloudWatch alarm, `<name_prefix>-sweeper-stopped`, and an SNS topic with 1 email subscription ([spec 0004](../../spec/0004-aws-stack-spec.md) section 4.8, [ADR 0024](../../decisions/0024-the-aws-sweeper-alarm-reads-the-lambda-metrics.md)). The alarm reads the built-in Lambda metrics `Invocations` and `Errors`, not a log. It fires when no sweeper run succeeds for 10 minutes, and it sends an email at each change to ALARM or OK after you confirm the subscription. Until you confirm the subscription, the topic does not deliver alarm emails. Confirm it right after the apply, and within 48 hours. After that, the alarm emails on ALARM and on OK. With `alert_email` empty, the stack makes none of these.

After the first apply, open the email from SNS and confirm the subscription ([SNS email notifications](https://docs.aws.amazon.com/sns/latest/dg/sns-email-notifications.html)); until you do, no alarm email arrives. If the alarm fires, check the schedule and the function: `aws scheduler get-schedule --name <name_prefix>-sweeper` and the newest `_ftg-sweeper_` log object (see Logs above). Leaked reservations show in `make status/aws`.

The variables are in [variables.tf](variables.tf). `alert_email` is empty by default. With no address, nothing tells you when the sweeper stops. Set `alert_email` before you turn on `grant_billable_provisioning`.

### Proxy (optional)

A network can force all outbound traffic through a proxy. Set these variables in `terraform/aws/terraform.tfvars`. Both are optional. With the defaults, the VM boots as before.

```hcl
egress_proxy_url = "http://proxy.example.internal:80"   # replace with your own proxy
no_proxy         = [".example.internal"]
```

- `egress_proxy_url`: use a host name, never an IP address. The first boot writes `/etc/apt/apt.conf.d/99proxy`, exports the proxy variables for the install steps, adds a systemd drop-in for the prober service, and adds a drop-in for the SSM agent snap (`snap.amazon-ssm-agent.amazon-ssm-agent`) and restarts it, so that Session Manager works with no direct internet access ([configure SSM Agent to use a proxy](https://docs.aws.amazon.com/systems-manager/latest/userguide/configure-proxy-ssm-agent.html)). The calls to the EC2, STS, and S3 APIs go through the proxy. The script does not write `/etc/environment`.
- `no_proxy`: extra hosts that bypass the proxy. With a proxy and `enable_s3_gateway_endpoint` both on, add `s3.<region>.amazonaws.com` and `.s3.<region>.amazonaws.com` here, so that S3 calls use the endpoint. The stack always adds `169.254.169.254`, `localhost`, and `127.0.0.1`.

## Deploy

```sh
make plan/aws    # tofu init, then tofu plan -out=tfplan
make apply/aws   # applies tfplan, and nothing else
```

`make deploy/aws` runs `tofu init` and `tofu apply` in 1 step, and asks for confirmation. `AUTO_APPROVE=1 make deploy/aws` skips the prompt. Each target stops with 1 line when `terraform/aws/terraform.tfvars` is missing. The state file stays in `terraform/aws/` and is ignored by git. For remote state, see the [config page](../../docs/config.md#backend).

The apply takes about 2 minutes. The first boot of the VM takes 3 to 5 more minutes: it installs packages, the AWS CLI, and the prober. Then the first objects appear under `experiment=<name>/` in the bucket.

Check the result:

```sh
make status/aws
```

It prints the VM state, the leaked Capacity Reservations (the line `leaked resources`), and the newest 5 object keys. The target reads the experiment and the Regions from the stack outputs `experiment` and `target_regions`, so it uses the targets file that the stack uses. The script needs the same credentials as the deploy.

## Day 1: request the Spot quota

Spot Placement Score caps the target capacity by the recent Spot use of the account. A new account has a low cap, in line with its Spot request quota ([Spot placement score](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-placement-score.html)). The default Spot quota for GPU families is 0 vCPUs ([Spot Instance quotas](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-limits.html)). Thus the `aws_spot_score` rows can show `outcome = cap` until the quota rises.

Request the quota on day 1, in each Region of `aws.quota` in `config/targets.yaml`. The command asks for the Spot quotas `All P Spot Instance Requests` and `All G and VT Spot Instance Requests`, and for the On-Demand quotas `Running On-Demand P instances` and `Running On-Demand G and VT instances`. The value is the sum of VM count x vCPUs of the instance types in the file ([spec 0012](../../spec/0012-aws-quota-requests-spec.md)).

```sh
make quota/aws                         # dry run: reads the quotas and prints the requests
make quota/aws QUOTA_ARGS=--apply      # sends the requests (a person runs this)
```

The command uses your boto3 credentials (`AWS_PROFILE`). The API is [RequestServiceQuotaIncrease](https://docs.aws.amazon.com/servicequotas/2019-06-24/apireference/API_RequestServiceQuotaIncrease.html). Approval can take days. The probes run in the meantime, and the rows record the cap.

## Cost

The stack costs about 18 US dollars per month in `us-east-1`, with the default variables (prices of 2026-10-11; prices change):

- The VM is most of it. A `t4g.small` On-Demand instance costs about 12 dollars per month ([EC2 On-Demand pricing](https://aws.amazon.com/ec2/pricing/on-demand/)).
- The 8 GB gp3 root disk costs less than 1 dollar per month ([EBS pricing](https://aws.amazon.com/ebs/pricing/)).
- S3 costs about 1 dollar per month: a few gigabytes of small files, and about 100,000 PUT requests ([S3 pricing](https://aws.amazon.com/s3/pricing/)).
- A public IPv4 address costs 0.005 dollars per hour, about 3.65 dollars per month ([Amazon VPC pricing](https://aws.amazon.com/vpc/pricing/)). The VM gets one only when the subnet assigns it (see [Network](#network)).
- The network path is not in this price. A subnet with no internet path needs a NAT gateway (about 33 dollars per month plus data, see [NAT gateway pricing](https://aws.amazon.com/vpc/pricing/)) or other outbound access.

The 3 EC2 APIs and the Spot Advisor feed have no charge (spec/0002, section 5).

## Logs

The VM has no SSH key and no inbound rule. By default, the role has the `AmazonSSMManagedInstanceCore` policy, so you can open a shell with SSM Session Manager. You need the [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) for the AWS CLI.

```sh
aws ssm start-session --target "$(mise exec -- tofu -chdir=terraform/aws output -raw instance_id)"
```

In the session:

```sh
sudo journalctl -u ftg-prober -f          # the prober log
sudo cat /var/log/cloud-init-output.log   # the first boot: installs and the unit
systemctl status ftg-prober
```

If you set `enable_ssm = false`, there is no shell access by design. Then the only signals are the objects in the bucket (`make status/aws`) and the instance state.

The sweeper (spec/0013) has no CloudWatch Logs: its role has no `logs:*` permission. It writes 1 log object per run to the bucket, under `experiment=<name>/logs/provider=aws/`, next to the prober's log objects. The function name is the host part of its object names (`<ts>_ftg-sweeper_<uuid>.jsonl` with the default `name_prefix`), and the last record of each object is the health line `sweeper run: scanned N, cancelled N, errors N`.

```sh
aws s3 ls "s3://$(mise exec -- tofu -chdir=terraform/aws output -raw bucket_name)/experiment=<name>/logs/provider=aws/" --recursive | grep _ftg-sweeper_ | tail -5
```

## Network

The VM makes outbound connections only:

- HTTPS (443) to the EC2, S3, and SSM endpoints, to the AWS CLI and Python package installers, and to the Spot Advisor feed.
- HTTP (80) to the Ubuntu package mirrors, for `apt-get`.

By default the stack creates 1 security group. It has no ingress rule. It has 1 egress rule for all IPv4 traffic, and 1 for all IPv6 traffic when the VPC has an IPv6 CIDR block. Thus the VM works in any VPC, also with a custom DHCP DNS server or a proxy on any port. Set `vpc_security_group_ids` to add existing groups of the network owner to the VM. Set `create_security_group = false` to use only those groups; the list must then hold at least 1 group, or the plan fails. The stack never uses the default security group ([default security group](https://docs.aws.amazon.com/vpc/latest/userguide/default-security-group.html)). The stack creates the group in `vpc_id`. The stack does not change the network, with 1 exception: when `enable_s3_gateway_endpoint` is true, the stack adds an S3 route to the route table of the subnet. Every other subnet on that table gets the route too. Outbound internet access from the subnet in `subnet_id` is a prerequisite. The network owner sets it up before the deploy: for example a route to a NAT gateway, or a route to an internet gateway and a public IP that the subnet assigns. The plan fails when the subnet is not in `vpc_id`, or when its zone does not offer `instance_type`. Security group rules do not filter the traffic to the instance metadata service, the Amazon DNS server, and the Amazon Time Sync Service ([Security groups](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-security-groups.html)).

## Update the code

A change in `prober/` or in the folder of `targets_file` (the default is `config/`) changes the hash of a zip. The next `make plan/aws` and `make apply/aws` upload the new zip and rewrites `manifest.json` in place. It does not replace the VM, because the user data names only the fixed `manifest.json` key ([ADR 0012](../../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md)). The sync timer on the VM pulls the new code and restarts the prober within 5 minutes. The apply deletes the old zips; no lifecycle rule expires the current ones.

## Destroy

The bucket holds the experiment data. OpenTofu does not delete a bucket with objects.

1. Stop the prober, so that no probe starts during the destroy. Run `make status/aws`. The line `leaked resources` must show `0`. If not, wait 4 minutes for the sweeper, then check again.

2. Copy the data to your computer:

   ```sh
   aws s3 sync "$(mise exec -- tofu -chdir=terraform/aws output -raw store_url)" ./data/aws/
   ```

3. Empty the bucket:

   ```sh
   aws s3 rm "$(mise exec -- tofu -chdir=terraform/aws output -raw store_url)" --recursive
   ```

4. Destroy the stack:

   ```sh
   make destroy/aws
   ```

## Checks

```sh
make lint/aws   # tofu fmt -check and tofu validate, no credentials needed
make test/aws   # tofu test with mock providers, no credentials needed
```
