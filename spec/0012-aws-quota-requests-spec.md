---
title: "AWS quota requests spec: GPU On-Demand and Spot vCPU quota from the targets file"
date: 2026-10-12
---

# AWS quota requests spec: GPU On-Demand and Spot vCPU quota from the targets file

**Status:** Phase 1 build · 2026-10-12
**Parent:** [spec 0002](0002-gpu-capacity-tracker-spec.md), decision D20 (request quota on day 1). The config schema is [spec 0003](0003-prober-core-spec.md), section 3. The AWS stack is [spec 0004](0004-aws-stack-spec.md). The Azure twin of this command is [spec 0011](0011-azure-quota-requests-spec.md).
**Code:** `preflight/ftg_preflight/quota_aws.py`, `preflight/tests/test_quota_aws.py`, `tasks/Makefile.quota`, the `aws.quota` section of `config/targets.yaml`.

A person must be able to build the command again from this file and spec 0003.

## 1. Scope and rules

1. The command requests vCPU quota for the AWS GPU instance types of `aws.quota` in `config/targets.yaml`. For each quota group it requests the On-Demand quota and the Spot quota.
2. The default is a dry run. A dry run only reads. It prints each request as an `aws` command line.
3. The flag `--apply` sends the requests. Only a person runs `--apply`.
4. Every AWS call goes through boto3, as in `preflight/ftg_preflight/aws.py`. Credentials come from the normal boto3 chain (`AWS_PROFILE`, and so on).
5. No tracked file holds an account ID. The command prints AWS error messages as AWS returns them, so an error line can hold the caller ARN, which holds the account ID.
6. A type outside the groups of section 2 is an error with a message. The command still plans the other types.
7. The tests use fake clients. They use no network and no credentials ([ADR 0005](../decisions/0005-prober-and-sweeper-in-python.md)).

## 2. Background

EC2 sets vCPU quota for each purchasing option and each instance group, in each Region. The On-Demand quotas are "Running On-Demand P instances" and "Running On-Demand G and VT instances". The Spot quotas are "All P Spot Instance Requests" and "All G and VT Spot Instance Requests". The default of each is 0 vCPUs ([instance type quotas](https://docs.aws.amazon.com/ec2/latest/instancetypes/ec2-instance-quotas.html), [Spot Instance quotas](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-limits.html)).

| Group | Instance types | On-Demand quota | Spot quota |
|---|---|---|---|
| `P` | the family starts with `p` and a digit, for example `p5.4xlarge` | `L-417A185B` | `L-7212CCBC` |
| `G and VT` | the family starts with `g` or `vt` and a digit, for example `g5.xlarge` | `L-DB2E81BA` | `L-3819A6DF` |

The codes are in the console links of the two pages above. Service Quotas takes the code in `QuotaCode` ([RequestServiceQuotaIncrease](https://docs.aws.amazon.com/servicequotas/2019-06-24/apireference/API_RequestServiceQuotaIncrease.html)).

## 3. Config: `aws.quota`

The shape is the shape of `azure.quota`. [Spec 0003](0003-prober-core-spec.md), section 3, states it once for both clouds.

```yaml
aws:
  quota:
    regions: [us-east-1, us-east-2, us-west-2]
    sizes:
      - {size: p5.4xlarge, count: 8}
      - {size: g6e.xlarge, count: 16, regions: [us-east-1]}
```

The rules of the field table are the rules of [spec 0011](0011-azure-quota-requests-spec.md), section 3, with these differences:

| Field | Difference |
|---|---|
| `aws.quota.regions` | A Region code: `^[a-z]{2}(-[a-z]+)+-[0-9]+$`, for example `us-east-1`. Each Region must be in the union of the `regions` lists of the `aws` jobs (`_job_regions` in `quota.py`); `aws` has no top-level `regions` key since ADR 0010 section 1 ([spec 0003](0003-prober-core-spec.md), section 3). |
| `sizes[].size` | An EC2 instance type: `^[a-z0-9]+(-[a-z0-9]+)?\.[a-z0-9]+$`, for example `g5.xlarge` or `p6-b200.48xlarge`. |
| `sizes[].count` | The number of instances of the type in each Region. |

`ftg-prober check-config` must report each fault of the table, for both clouds, with 1 shared function. The command checks every rule itself, because `--config` can name a file that `check-config` did not check.

The YAML holds no quota code and no vCPU count. The command reads the vCPUs from EC2 (section 5).

The repo file must hold the 3 Regions of the `aws` jobs, and the 1-GPU types of the quota-class rows of `aws.spot_score_requests` (rows 1 to 6 of [spec 0002](0002-gpu-capacity-tracker-spec.md), section 4.2): `p5.4xlarge` x 8, `g7e.2xlarge` x 8, `g6e.xlarge` x 16, `g5.xlarge` x 16, `g4dn.xlarge` x 32, and `g6.xlarge` x 16. The single-size test rows of the closed 24 h test (rows 9 to 11) are not quota classes. The counts follow the Azure counts of the same GPU class (H100, RTX PRO 6000, A10, T4). L40S and L4 have no size in `azure.quota`, so they use 16. These counts are request sizes of this project.

## 4. Command line

```text
python -m ftg_preflight.quota_aws --config PATH [--apply]
make quota/aws [QUOTA_ARGS="--apply"]   # the profile: AWS_PROFILE (spec 0014)
```

| Flag | Effect |
|---|---|
| `--config` | Required. The targets file. |
| `--apply` | Send the requests. Without it, the command sends nothing. |

Exit codes:

| Code | Meaning |
|---|---|
| 0 | The dry run or the requests finished with no error. |
| 1 | A read call, a type, or a request failed. The output names each failure. |
| 2 | The config has no valid `aws.quota` section. |

## 5. Steps

The command must do these steps in this order, for each quota Region in config order.

1. Read `aws.quota` from the config. If it is missing or breaks a rule of section 3, exit 2.
2. For each type of the Region, find its group (section 2). A type with no group is an error: `<type>: no GPU quota group (this command knows P, G, and VT)`. Skip it.
3. Read the vCPUs of the type: EC2 `DescribeInstanceTypes`, field `VCpuInfo.DefaultVCpus` ([DescribeInstanceTypes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeInstanceTypes.html)). A failed read is an error. Skip the type.
4. For each group, the ask is the sum of `count` x vCPUs of its types.
5. For each group, read the current value of its On-Demand quota and of its Spot quota with Service Quotas `GetServiceQuota` ([GetServiceQuota](https://docs.aws.amazon.com/servicequotas/2019-06-24/apireference/API_GetServiceQuota.html)). When the account has no value (`NoSuchResourceException`), read `GetAWSDefaultServiceQuota` ([GetAWSDefaultServiceQuota](https://docs.aws.amazon.com/servicequotas/2019-06-24/apireference/API_GetAWSDefaultServiceQuota.html)). A failed read skips that request and counts as an error.
6. Request each quota whose current value is lower than the ask: Service Quotas `RequestServiceQuotaIncrease` with `ServiceCode` `ec2`, the `QuotaCode`, and `DesiredValue` equal to the ask. The On-Demand ask and the Spot ask of a group are the same number.
7. A failed request counts as an error. The command continues.

A quota whose current value is the ask or more gets 1 line with the reason (`limit X >= target Y; skip`).

## 6. Output

Each line starts with `#` or `##`, or is an `aws service-quotas request-service-quota-increase` command line with `--region`. A person can paste that line into a shell. The last line counts the requests and the errors. With `--apply`, each request gets a line `sent` or `FAILED`.

## 7. Decisions

| # | Decision | Reason |
|---|---|---|
| W1 | The code is a second module, `quota_aws.py`. It imports the config load, the `Ask` type, and the plan line from `quota.py`, and the quota read from `aws.py`. | The Azure command is built on the `az` CLI, and this one on boto3. A `--provider` switch would join 2 unlike flows in 1 `main`. The shared parts are small functions. |
| W2 | The command uses boto3, not the `aws` CLI. | `aws.py` already uses boto3. Tests pass fakes without a process. |
| W3 | The On-Demand ask and the Spot ask are equal. | Both quotas count vCPUs of the same types. |
| W4 | The ask is the sum for the Region. It is not added to current usage. | The AWS quota is a cap on running vCPUs; usage of other instances is not read. The person can raise a request by hand. |

## 8. Sources

- [EC2 instance type quotas](https://docs.aws.amazon.com/ec2/latest/instancetypes/ec2-instance-quotas.html)
- [Spot Instance quotas](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-limits.html)
- [Service Quotas API](https://docs.aws.amazon.com/servicequotas/2019-06-24/apireference/Welcome.html)
- [DescribeInstanceTypes](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeInstanceTypes.html)
