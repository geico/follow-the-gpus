"""Preflight checks for the AWS stack (spec 0004, section 12). Each call only reads.

The checks take boto3-style clients, so the tests pass fakes with recorded responses.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ftg_preflight.report import Finding, fail, ok, warn

README_PREREQ = "terraform/aws/README.md#prerequisites"
README_NETWORK = "terraform/aws/README.md#network"
README_SPOT = "terraform/aws/README.md#day-1-request-the-spot-quota"

# On-Demand vCPU quota of the families that suit the prober VM. The key is the letters before
# the generation. The check reads no quota of a GPU or accelerator family.
_STANDARD = ("L-1216C47A", "Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances")
ON_DEMAND_QUOTAS: dict[str, tuple[str, str]] = {
    **{letter: _STANDARD for letter in "acdhimrtz"},
    "im": _STANDARD,
    "is": _STANDARD,
}

# The GPU Spot quotas, in vCPUs. Their AWS default is 0. The check only reads them.
SPOT_GPU_QUOTAS = (
    ("L-7212CCBC", "All P Spot Instance Requests"),
    ("L-3819A6DF", "All G and VT Spot Instance Requests"),
)

# The main create actions of `make apply/aws`. The simulation checks each one.
DEPLOY_ACTIONS = (
    "iam:CreateRole",
    "iam:PutRolePolicy",
    "iam:AttachRolePolicy",
    "iam:CreateInstanceProfile",
    "iam:AddRoleToInstanceProfile",
    "iam:PassRole",
    "ec2:DescribeSubnets",
    "ec2:DescribeInstanceTypeOfferings",
    "ec2:CreateSecurityGroup",
    "ec2:AuthorizeSecurityGroupEgress",
    "ec2:RevokeSecurityGroupEgress",
    "ec2:RunInstances",
    "ec2:CreateTags",
    "ssm:GetParameter",
    "s3:CreateBucket",
    "s3:PutBucketPublicAccessBlock",
    "s3:PutEncryptionConfiguration",
    "s3:PutLifecycleConfiguration",
    "s3:PutBucketVersioning",
    "s3:PutBucketTagging",
    "s3:PutObject",
)

# Route targets other than a NAT gateway or an internet gateway, as the EC2 API names them.
ROUTE_TARGETS = (
    ("TransitGatewayId", "a transit gateway"),
    ("NetworkInterfaceId", "a network interface"),
    ("InstanceId", "an instance"),
    ("VpcPeeringConnectionId", "a VPC peering connection"),
    ("GatewayId", "a gateway"),
    ("CoreNetworkArn", "a core network"),
    ("LocalGatewayId", "a local gateway"),
    ("CarrierGatewayId", "a carrier gateway"),
)

ClientFactory = Callable[..., Any]


@dataclass
class AwsInputs:
    region: str
    instance_type: str
    vpc_id: str | None = None
    subnet_id: str | None = None
    capacity_blocks: Mapping[str, Any] = field(default_factory=dict)
    env_region: str | None = None
    profile: Mapping[str, Any] = field(default_factory=dict)


def error_code(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        return exc.response.get("Error", {}).get("Code", "Unknown")
    return type(exc).__name__


def instance_family(instance_type: str) -> str:
    match = re.match(r"([a-z]+?)(\d|$)", instance_type.lower())
    return match.group(1) if match else ""


def check_profile(profile: Mapping[str, Any], profiles: Mapping[str, Mapping[str, Any]]) -> Finding:
    """An `aws login` profile works in boto3 but failed in the Terraform AWS provider."""
    chain, seen, current = [], set(), profile
    while current and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = profiles.get(current.get("source_profile", ""), {})
    uses_login = any("login_session" in p for p in chain)
    has_process = any("credential_process" in p for p in chain)
    if uses_login and not has_process:
        return warn(
            "aws/profile",
            "the profile gets its credentials from `aws login`. In a test deploy, the Terraform "
            "AWS provider failed with such a profile.",
            f"add a credential_process profile and set AWS_PROFILE to it ({README_PREREQ})",
        )
    return ok("aws/profile", "the profile needs no credential_process change")


def check_region(inputs: AwsInputs) -> Finding:
    if inputs.env_region and inputs.env_region != inputs.region:
        return warn(
            "aws/region",
            f"AWS_REGION is {inputs.env_region}, but the stack variable region is {inputs.region}",
            "set AWS_REGION and the Terraform variable region to the same value",
        )
    return ok("aws/region", f"the stack deploys to {inputs.region}")


def caller(sts: Any) -> tuple[Finding, str | None]:
    try:
        arn = sts.get_caller_identity()["Arn"]
    except (ClientError, BotoCoreError) as exc:
        return (
            fail(
                "aws/credentials",
                f"no working credentials ({error_code(exc)})",
                f"set AWS_PROFILE, then sign in, for example `aws login` ({README_PREREQ})",
            ),
            None,
        )
    kind, _, name = arn.split(":", 5)[5].partition("/")
    shown = name.split("/")[0] if kind == "assumed-role" else name.rsplit("/", 1)[-1]
    return ok("aws/credentials", f"signed in as {kind} {shown or kind}"), arn


def policy_source_arn(arn: str, iam: Any) -> str | None:
    """The IAM ARN for SimulatePrincipalPolicy. An assumed role maps to its role ARN."""
    resource = arn.split(":", 5)[5]
    if resource.startswith("user/"):
        return arn
    if resource.startswith("assumed-role/"):
        role_name = resource.split("/")[1]
        return iam.get_role(RoleName=role_name)["Role"]["Arn"]
    return None


def check_permissions(arn: str, iam: Any) -> Finding:
    if arn.split(":", 5)[5] == "root":
        return ok("aws/permissions", "the root user has every right (AWS advises against its use)")
    try:
        source = policy_source_arn(arn, iam)
        if source is None:
            return warn("aws/permissions", "cannot simulate the policy of this kind of principal")
        results = []
        pages = iam.get_paginator("simulate_principal_policy").paginate(
            PolicySourceArn=source, ActionNames=list(DEPLOY_ACTIONS)
        )
        for page in pages:
            results.extend(page["EvaluationResults"])
    except (ClientError, BotoCoreError) as exc:
        return warn(
            "aws/permissions",
            f"cannot check the rights of the caller ({error_code(exc)})",
            "the check needs iam:GetRole and iam:SimulatePrincipalPolicy; "
            "or confirm the rights by hand",
        )
    denied = sorted(r["EvalActionName"] for r in results if r["EvalDecision"] != "allowed")
    if denied:
        return fail(
            "aws/permissions",
            f"the caller cannot run {len(denied)} deploy actions: {', '.join(denied)}",
            "use credentials with rights to create IAM roles, EC2 instances, "
            "security groups, and S3 buckets",
        )
    return ok("aws/permissions", f"the policy simulation allows all {len(DEPLOY_ACTIONS)} actions")


def check_network_inputs(inputs: AwsInputs) -> Finding:
    """vpc_id and subnet_id are required and have no default."""
    unset = [n for n in ("vpc_id", "subnet_id") if not getattr(inputs, n)]
    if unset:
        return fail(
            "aws/network-inputs",
            f"{' and '.join(unset)} not set. The stack deploys into an existing VPC and subnet.",
            "copy terraform/aws/terraform.tfvars.example to terraform/aws/terraform.tfvars "
            f"and put in your values ({README_PREREQ})",
        )
    return ok("aws/network-inputs", f"vpc_id {inputs.vpc_id}, subnet_id {inputs.subnet_id}")


def _not_found(exc: ClientError, suffix: str) -> bool:
    return error_code(exc).endswith(suffix)


def check_network(ec2: Any, inputs: AwsInputs) -> list[Finding]:
    """The VPC and the subnet exist, the subnet is in the VPC, and its zone offers the type."""
    vpc_id, subnet_id, it = inputs.vpc_id, inputs.subnet_id, inputs.instance_type
    try:
        ec2.describe_vpcs(VpcIds=[vpc_id])
    except ClientError as exc:
        if not _not_found(exc, ".NotFound"):
            raise
        return [
            fail(
                "aws/vpc",
                f"VPC {vpc_id} not found in {inputs.region}",
                "fix vpc_id, or set the Terraform variable region to the Region of the VPC",
            )
        ]
    findings = [ok("aws/vpc", f"VPC {vpc_id} exists in {inputs.region}")]
    try:
        subnet = ec2.describe_subnets(SubnetIds=[subnet_id])["Subnets"][0]
    except ClientError as exc:
        if not _not_found(exc, ".NotFound"):
            raise
        findings.append(
            fail("aws/subnet", f"subnet {subnet_id} not found in {inputs.region}", "fix subnet_id")
        )
        return findings
    if subnet["VpcId"] != vpc_id:
        findings.append(
            fail(
                "aws/subnet",
                f"subnet {subnet_id} is in {subnet['VpcId']}, not in vpc_id {vpc_id}",
                "use a subnet of vpc_id, or fix vpc_id",
            )
        )
        return findings
    zone = subnet["AvailabilityZone"]
    offerings = ec2.describe_instance_type_offerings(
        LocationType="availability-zone",
        Filters=[{"Name": "instance-type", "Values": [it]}],
    )["InstanceTypeOfferings"]
    if zone not in {o["Location"] for o in offerings}:
        findings.append(
            fail(
                "aws/subnet",
                f"the zone {zone} of subnet {subnet_id} does not offer {it}",
                "use a subnet in another zone, or change instance_type",
            )
        )
        return findings
    findings.append(ok("aws/subnet", f"subnet {subnet_id} is in {vpc_id}; {zone} offers {it}"))
    findings.append(check_outbound(ec2, vpc_id, subnet))
    return findings


def _route_tables(ec2: Any, vpc_id: str, subnet_id: str) -> list[Mapping[str, Any]]:
    """The route table of the subnet: its explicit association, else the main table of the VPC."""
    tables = ec2.describe_route_tables(
        Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
    )["RouteTables"]
    if tables:
        return tables
    return ec2.describe_route_tables(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "association.main", "Values": ["true"]},
        ]
    )["RouteTables"]


def check_outbound(ec2: Any, vpc_id: str, subnet: Mapping[str, Any]) -> Finding:
    """Outbound internet access is a prerequisite that the network owner sets up.

    The check reads the IPv4 default route of the subnet. It cannot prove that a path reaches
    the internet, so it never fails. A public NAT gateway, or an internet gateway with a public IP
    that the subnet assigns, is a PASS. Anything else is a WARN.
    """
    subnet_id = subnet["SubnetId"]
    fix = f"ask the network owner for outbound internet access from {subnet_id} ({README_NETWORK})"
    try:
        tables = _route_tables(ec2, vpc_id, subnet_id)
    except (ClientError, BotoCoreError) as exc:
        return warn("aws/outbound", f"cannot read the route table ({error_code(exc)})", fix)
    routes = [
        r
        for t in tables
        for r in t.get("Routes") or []
        if r.get("DestinationCidrBlock") == "0.0.0.0/0" and r.get("State") != "blackhole"
    ]
    if not routes:
        return warn(
            "aws/outbound",
            f"the route table of {subnet_id} has no IPv4 default route. The first boot of the VM "
            "cannot reach the package installers.",
            fix,
        )
    route = routes[0]
    if route.get("NatGatewayId"):
        try:
            nat = ec2.describe_nat_gateways(NatGatewayIds=[route["NatGatewayId"]])["NatGateways"]
        except (ClientError, BotoCoreError) as exc:
            return warn("aws/outbound", f"cannot read the NAT gateway ({error_code(exc)})", fix)
        kind = (nat[0].get("ConnectivityType") or "public") if nat else "unknown"
        if kind == "public":
            return ok(
                "aws/outbound", f"the default route of {subnet_id} goes to a public NAT gateway"
            )
        return warn(
            "aws/outbound",
            f"the default route of {subnet_id} goes to a {kind} NAT gateway. A private NAT "
            "gateway does not reach the internet.",
            fix,
        )
    if str(route.get("GatewayId", "")).startswith("igw-"):
        if subnet.get("MapPublicIpOnLaunch"):
            return ok(
                "aws/outbound",
                f"the default route of {subnet_id} goes to an internet gateway, and the subnet "
                "assigns a public IPv4 address",
            )
        return warn(
            "aws/outbound",
            f"the default route of {subnet_id} goes to an internet gateway, but the subnet assigns "
            "no public IPv4 address. The VM gets none, so it has no outbound path.",
            fix,
        )
    target = next((name for key, name in ROUTE_TARGETS if route.get(key)), "other target")
    return warn(
        "aws/outbound",
        f"the default route of {subnet_id} goes to {target}. The check cannot confirm that "
        "it reaches the internet.",
        f"confirm the outbound path with the network owner ({README_NETWORK})",
    )


def quota_value(quotas: Any, code: str) -> float:
    try:
        return quotas.get_service_quota(ServiceCode="ec2", QuotaCode=code)["Quota"]["Value"]
    except ClientError as exc:
        if error_code(exc) != "NoSuchResourceException":
            raise
        return quotas.get_aws_default_service_quota(ServiceCode="ec2", QuotaCode=code)["Quota"][
            "Value"
        ]


def check_vm_quota(ec2: Any, quotas: Any, inputs: AwsInputs) -> Finding:
    it = inputs.instance_type
    try:
        info = ec2.describe_instance_types(InstanceTypes=[it])["InstanceTypes"][0]
    except ClientError as exc:
        return fail("aws/vm-quota", f"cannot read {it} ({error_code(exc)})", "check instance_type")
    vcpus = info["VCpuInfo"]["DefaultVCpus"]
    family = instance_family(it)
    if family not in ON_DEMAND_QUOTAS:
        return warn("aws/vm-quota", f"no known On-Demand quota for the family of {it}")
    code, name = ON_DEMAND_QUOTAS[family]
    limit = quota_value(quotas, code)
    if limit < vcpus:
        return fail(
            "aws/vm-quota",
            f"{it} needs {vcpus} vCPUs, but the quota '{name}' is {limit:g} in {inputs.region}",
            f"request an increase of {code}, or change instance_type",
        )
    return ok(
        "aws/vm-quota",
        f"{it} needs {vcpus} vCPUs; the quota '{name}' is {limit:g}. "
        "Running instances also count against it.",
    )


def check_spot_quotas(quotas: Any, inputs: AwsInputs) -> list[Finding]:
    """A GPU Spot quota of 0 caps the Spot signal. It is a signal gap, never a deploy gap.

    A read error goes up to the `aws/api` rule of `run_checks`.
    """
    findings = []
    for code, name in SPOT_GPU_QUOTAS:
        value = quota_value(quotas, code)
        if value > 0:
            findings.append(
                ok("aws/spot-quota", f"the quota '{name}' is {value:g} vCPUs in {inputs.region}")
            )
            continue
        findings.append(
            warn(
                "aws/spot-quota",
                f"the quota '{name}' ({code}) is {value:g} vCPUs in {inputs.region}. "
                "The aws_spot_score rows can show outcome cap.",
                f"request an increase of {code} on day 1 ({README_SPOT})",
            )
        )
    return findings


def check_capacity_blocks(ec2: Any, inputs: AwsInputs) -> Finding:
    """Capacity Block offerings need account access. No access is a warning."""
    cb = inputs.capacity_blocks
    types = cb.get("instance_types") or []
    if not types:
        return ok("aws/capacity-blocks", "the config has no Capacity Block targets")
    try:
        offers = ec2.describe_capacity_block_offerings(
            InstanceType=types[0],
            InstanceCount=int(cb.get("instance_count", 1)),
            CapacityDurationHours=int((cb.get("duration_hours") or [24])[0]),
        )["CapacityBlockOfferings"]
    except (ClientError, BotoCoreError) as exc:
        code = error_code(exc)
        hint = (
            "the account has no access to Capacity Blocks"
            if code == "CapacityBlockDescribeLimitExceeded"
            else "the call failed"
        )
        return warn(
            "aws/capacity-blocks",
            f"{hint} ({code}). The aws_cb_offering rows will be error rows.",
            "ask AWS Support about Capacity Block access, or keep the error rows as the signal",
        )
    return ok(
        "aws/capacity-blocks", f"the account can read offerings ({len(offers)} for {types[0]})"
    )


def run_checks(clients: ClientFactory, inputs: AwsInputs, profiles: Mapping) -> list[Finding]:
    """Run all checks. `clients(service, region)` returns a boto3-style client."""
    findings = [
        check_region(inputs),
        check_profile(inputs.profile, profiles),
        check_network_inputs(inputs),
    ]
    first, arn = caller(clients("sts", inputs.region))
    findings.append(first)
    if arn is None:
        return findings
    findings.append(check_permissions(arn, clients("iam", inputs.region)))
    ec2 = clients("ec2", inputs.region)
    quotas = clients("service-quotas", inputs.region)
    steps = [
        lambda: [check_vm_quota(ec2, quotas, inputs)],
        lambda: check_spot_quotas(quotas, inputs),
        lambda: [check_capacity_blocks(ec2, inputs)],
    ]
    if inputs.vpc_id and inputs.subnet_id:
        steps.insert(0, lambda: check_network(ec2, inputs))
    for step in steps:
        try:
            findings.extend(step())
        except (ClientError, BotoCoreError) as exc:
            findings.append(
                fail("aws/api", f"a read call failed ({error_code(exc)})", "check the credentials")
            )
    return findings
