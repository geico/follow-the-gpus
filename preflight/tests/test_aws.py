from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ftg_preflight import aws
from ftg_preflight.report import Level, exit_code
from tests.fakes import FakeClient, aws_reply

CB = {"instance_types": ["p5.48xlarge"], "instance_count": 1, "duration_hours": [24]}
VPC = "vpc-0123456789abcdef0"
SUBNET = "subnet-0123456789abcdef0"


def inputs(**kw: Any) -> aws.AwsInputs:
    base = {
        "region": "us-east-1",
        "instance_type": "t4g.small",
        "vpc_id": VPC,
        "subnet_id": SUBNET,
        "capacity_blocks": CB,
        "env_region": "us-east-1",
    }
    base.update(kw)
    return aws.AwsInputs(**base)


def all_allowed(**kwargs: Any) -> dict[str, Any]:
    names = kwargs["ActionNames"]
    return {
        "IsTruncated": False,
        "EvaluationResults": [
            {"EvalActionName": n, "EvalResourceName": "*", "EvalDecision": "allowed"} for n in names
        ],
    }


SPOT_P, SPOT_G = "L-7212CCBC", "L-3819A6DF"
QUOTA_REPLIES = {
    "L-1216C47A": "quota_standard_16",
    SPOT_P: "quota_spot_p_8",
    SPOT_G: "quota_spot_g_8",
}


def quota_by_code(**replies: str):
    """Service Quotas replies by quota code. A keyword overrides the reply of a code."""
    keys = {**QUOTA_REPLIES, **replies}

    def reply(**kwargs: Any) -> Any:
        assert kwargs["ServiceCode"] == "ec2"
        return aws_reply(keys[kwargs["QuotaCode"]])

    return reply


def route_tables(subnet_table: str, main_table: str = "route_tables_none"):
    """The table of the subnet association, else the main table of the VPC."""

    def reply(**kwargs: Any) -> Any:
        names = {f["Name"] for f in kwargs["Filters"]}
        return aws_reply(subnet_table if "association.subnet-id" in names else main_table)

    return reply


def clients(**overrides: dict[str, Any]):
    replies = {
        "sts": {"get_caller_identity": aws_reply("get_caller_identity")},
        "iam": {"get_role": aws_reply("get_role"), "simulate_principal_policy": all_allowed},
        "ec2": {
            "describe_vpcs": aws_reply("describe_vpcs"),
            "describe_subnets": aws_reply("describe_subnets"),
            "describe_route_tables": route_tables("route_tables_nat"),
            "describe_nat_gateways": aws_reply("nat_gateway_public"),
            "describe_instance_type_offerings": aws_reply("describe_instance_type_offerings"),
            "describe_instance_types": aws_reply("describe_instance_types"),
            "describe_capacity_block_offerings": aws_reply("capacity_block_offerings"),
        },
        "service-quotas": {"get_service_quota": quota_by_code()},
    }
    for service, methods in overrides.items():
        replies[service.replace("_", "-")].update(methods)
    made = {s: FakeClient(r) for s, r in replies.items()}
    return lambda service, region: made[service], made


def levels(findings, check: str) -> list[Level]:
    return [f.level for f in findings if f.check == check]


def test_all_checks_pass():
    factory, made = clients()
    findings = aws.run_checks(factory, inputs(), {})
    assert [f for f in findings if f.level is not Level.PASS] == []
    assert exit_code(findings) == 0
    sim = made["iam"].calls[-1][1]
    assert sim["PolicySourceArn"].endswith(":role/aws-reserved/sso.amazonaws.com/ftg-deployer")
    assert set(sim["ActionNames"]) == set(aws.DEPLOY_ACTIONS)


def test_no_credentials_stops_early():
    factory, made = clients(sts={"get_caller_identity": aws_reply("get_caller_identity_expired")})
    findings = aws.run_checks(factory, inputs(), {})
    assert levels(findings, "aws/credentials") == [Level.FAIL]
    assert "ExpiredToken" in findings[-1].message
    assert made["ec2"].calls == []
    assert exit_code(findings) == 1


def test_denied_actions_fail():
    factory, _ = clients(iam={"simulate_principal_policy": aws_reply("simulate_denies")})
    findings = aws.run_checks(factory, inputs(), {})
    perm = [f for f in findings if f.check == "aws/permissions"][0]
    assert perm.level is Level.FAIL
    assert "iam:CreateRole, iam:PassRole" in perm.message


def test_simulation_not_allowed_is_a_warning():
    factory, _ = clients(iam={"simulate_principal_policy": aws_reply("simulate_access_denied")})
    findings = aws.run_checks(factory, inputs(), {})
    assert levels(findings, "aws/permissions") == [Level.WARN]
    assert exit_code(findings) == 0


def test_iam_user_arn_is_the_policy_source():
    factory, made = clients(sts={"get_caller_identity": aws_reply("get_caller_identity_user")})
    aws.run_checks(factory, inputs(), {})
    method, kwargs = made["iam"].calls[-1]
    assert method == "simulate_principal_policy"
    assert kwargs["PolicySourceArn"] == "arn:aws:iam::test-account:user/ops/deployer"


def test_root_skips_simulation():
    factory, made = clients(sts={"get_caller_identity": aws_reply("get_caller_identity_root")})
    findings = aws.run_checks(factory, inputs(), {})
    assert levels(findings, "aws/permissions") == [Level.PASS]
    assert made["iam"].calls == []


def test_caller_message_has_no_account_id():
    factory, _ = clients()
    findings = aws.run_checks(factory, inputs(), {})
    cred = [f for f in findings if f.check == "aws/credentials"][0]
    assert cred.message == "signed in as assumed-role ftg-deployer"
    assert "test-account" not in cred.message


def test_network_calls_name_the_given_ids():
    factory, made = clients()
    aws.run_checks(factory, inputs(), {})
    calls = dict(made["ec2"].calls)
    assert calls["describe_vpcs"] == {"VpcIds": [VPC]}
    assert calls["describe_subnets"] == {"SubnetIds": [SUBNET]}
    assert calls["describe_nat_gateways"] == {"NatGatewayIds": ["nat-0example"]}


@pytest.mark.parametrize("unset", [{"vpc_id": None}, {"subnet_id": None}])
def test_unset_network_variable_fails_and_skips_the_network_reads(unset):
    factory, made = clients()
    findings = aws.run_checks(factory, inputs(**unset), {})
    net = [f for f in findings if f.check == "aws/network-inputs"][0]
    assert net.level is Level.FAIL
    assert "copy terraform/aws/terraform.tfvars.example to terraform/aws/terraform.tfvars" in (
        net.fix
    )
    methods = {m for m, _ in made["ec2"].calls}
    assert not methods & {"describe_vpcs", "describe_subnets", "describe_route_tables"}
    assert exit_code(findings) == 1


def test_missing_vpc_fails():
    factory, _ = clients(ec2={"describe_vpcs": aws_reply("vpc_not_found")})
    findings = aws.run_checks(factory, inputs(), {})
    assert levels(findings, "aws/vpc") == [Level.FAIL]
    assert levels(findings, "aws/subnet") == []
    assert levels(findings, "aws/api") == []


def test_missing_subnet_fails():
    factory, _ = clients(ec2={"describe_subnets": aws_reply("subnet_not_found")})
    findings = aws.run_checks(factory, inputs(), {})
    assert levels(findings, "aws/subnet") == [Level.FAIL]
    assert levels(findings, "aws/outbound") == []


def test_subnet_in_another_vpc_fails():
    factory, _ = clients(ec2={"describe_subnets": aws_reply("describe_subnets_other_vpc")})
    findings = aws.run_checks(factory, inputs(), {})
    sub = [f for f in findings if f.check == "aws/subnet"][0]
    assert sub.level is Level.FAIL
    assert "not in vpc_id" in sub.message


def test_subnet_zone_without_the_type_fails():
    factory, _ = clients(
        ec2={
            "describe_instance_type_offerings": aws_reply("describe_instance_type_offerings_only_c")
        }
    )
    findings = aws.run_checks(factory, inputs(), {})
    sub = [f for f in findings if f.check == "aws/subnet"][0]
    assert sub.level is Level.FAIL
    assert "us-east-1a" in sub.message


def test_outbound_via_main_table_internet_gateway_and_public_ip_passes():
    factory, made = clients(
        ec2={
            "describe_subnets": aws_reply("describe_subnets_public"),
            "describe_route_tables": route_tables("route_tables_none", "route_tables_main_igw"),
        }
    )
    findings = aws.run_checks(factory, inputs(), {})
    assert levels(findings, "aws/outbound") == [Level.PASS]
    main = [k for m, k in made["ec2"].calls if m == "describe_route_tables"][1]
    assert {"Name": "association.main", "Values": ["true"]} in main["Filters"]


@pytest.mark.parametrize(
    ("subnet", "table", "text"),
    [
        ("describe_subnets", "route_tables_main_igw", "assigns no public IPv4 address"),
        ("describe_subnets", "route_tables_local_only", "no IPv4 default route"),
        ("describe_subnets", "route_tables_tgw", "a transit gateway"),
        ("describe_subnets", "route_tables_nat", "private NAT gateway"),
    ],
)
def test_outbound_gaps_are_warnings(subnet, table, text):
    factory, _ = clients(
        ec2={
            "describe_subnets": aws_reply(subnet),
            "describe_route_tables": route_tables(table),
            "describe_nat_gateways": aws_reply("nat_gateway_private"),
        }
    )
    findings = aws.run_checks(factory, inputs(), {})
    out = [f for f in findings if f.check == "aws/outbound"]
    assert [f.level for f in out] == [Level.WARN]
    assert text in out[0].message
    assert "network owner" in out[0].fix
    assert exit_code(findings) == 0


def test_route_table_read_error_is_a_warning():
    factory, _ = clients(ec2={"describe_route_tables": aws_reply("simulate_access_denied")})
    findings = aws.run_checks(factory, inputs(), {})
    assert levels(findings, "aws/outbound") == [Level.WARN]
    assert levels(findings, "aws/api") == []


def test_on_demand_quota_under_the_vcpus_fails():
    quota = quota_by_code(**{"L-1216C47A": "quota_standard_0"})
    factory, _ = clients(service_quotas={"get_service_quota": quota})
    findings = aws.run_checks(factory, inputs(), {})
    vm = [f for f in findings if f.check == "aws/vm-quota"][0]
    assert vm.level is Level.FAIL
    assert "needs 2 vCPUs" in vm.message and "L-1216C47A" in vm.fix


def test_quota_falls_back_to_the_default_value():
    def quota(**kwargs):
        return aws_reply("quota_no_such_resource")

    factory, made = clients(
        service_quotas={
            "get_service_quota": quota,
            "get_aws_default_service_quota": aws_reply("quota_standard_16"),
        }
    )
    findings = aws.run_checks(factory, inputs(), {})
    assert levels(findings, "aws/vm-quota") == [Level.PASS]
    assert ("get_aws_default_service_quota", {"ServiceCode": "ec2", "QuotaCode": "L-1216C47A"}) in (
        made["service-quotas"].calls
    )


def test_the_only_quotas_read_are_the_standard_and_the_gpu_spot_quotas():
    factory, made = clients()
    aws.run_checks(factory, inputs(), {})
    codes = {k["QuotaCode"] for _, k in made["service-quotas"].calls}
    assert codes == {"L-1216C47A", SPOT_P, SPOT_G}


def test_gpu_prober_type_reads_no_on_demand_quota():
    factory, made = clients(
        ec2={"describe_instance_types": {"InstanceTypes": [{"VCpuInfo": {"DefaultVCpus": 4}}]}}
    )
    findings = aws.run_checks(factory, inputs(instance_type="g5.xlarge"), {})
    assert levels(findings, "aws/vm-quota") == [Level.WARN]
    codes = {k["QuotaCode"] for _, k in made["service-quotas"].calls}
    assert codes == {SPOT_P, SPOT_G}


def test_positive_spot_quotas_pass():
    factory, _ = clients()
    findings = aws.run_checks(factory, inputs(), {})
    spot = [f for f in findings if f.check == "aws/spot-quota"]
    assert [f.level for f in spot] == [Level.PASS, Level.PASS]
    assert "'All P Spot Instance Requests' is 8 vCPUs in us-east-1" in spot[0].message
    assert "'All G and VT Spot Instance Requests' is 8 vCPUs in us-east-1" in spot[1].message


@pytest.mark.parametrize(
    ("zero", "name"),
    [
        ({SPOT_P: "quota_spot_p_0"}, "All P Spot Instance Requests"),
        ({SPOT_G: "quota_spot_g_0"}, "All G and VT Spot Instance Requests"),
    ],
)
def test_zero_spot_quota_is_a_warning_that_names_the_quota(zero, name):
    factory, _ = clients(service_quotas={"get_service_quota": quota_by_code(**zero)})
    findings = aws.run_checks(factory, inputs(), {})
    spot = [f for f in findings if f.check == "aws/spot-quota"]
    assert sorted(f.level.name for f in spot) == ["PASS", "WARN"]
    low = [f for f in spot if f.level is Level.WARN][0]
    assert f"'{name}'" in low.message and "is 0 vCPUs in us-east-1" in low.message
    assert "day 1" in low.fix
    assert exit_code(findings) == 0


def test_spot_quota_with_no_account_value_uses_the_default_of_0():
    def no_value(**kwargs):
        if kwargs["QuotaCode"] == "L-1216C47A":
            return aws_reply("quota_standard_16")
        return aws_reply("quota_no_such_resource")

    defaults = quota_by_code(**{SPOT_P: "quota_spot_p_0", SPOT_G: "quota_spot_g_0"})
    factory, _ = clients(
        service_quotas={"get_service_quota": no_value, "get_aws_default_service_quota": defaults}
    )
    findings = aws.run_checks(factory, inputs(), {})
    assert levels(findings, "aws/spot-quota") == [Level.WARN, Level.WARN]
    assert exit_code(findings) == 0


def test_spot_quota_read_error_follows_the_api_rule():
    def denied(**kwargs):
        if kwargs["QuotaCode"] == "L-1216C47A":
            return aws_reply("quota_standard_16")
        return aws_reply("quota_access_denied")

    factory, _ = clients(service_quotas={"get_service_quota": denied})
    findings = aws.run_checks(factory, inputs(), {})
    assert levels(findings, "aws/spot-quota") == []
    api = [f for f in findings if f.check == "aws/api"]
    assert [f.level for f in api] == [Level.FAIL]
    assert "AccessDeniedException" in api[0].message
    assert levels(findings, "aws/vm-quota") == [Level.PASS]
    assert levels(findings, "aws/capacity-blocks") == [Level.PASS]


def test_spot_quota_is_read_in_the_deploy_region():
    factory, made = clients()
    regions = []

    def factory_with_regions(service, region):
        regions.append((service, region))
        return factory(service, region)

    aws.run_checks(factory_with_regions, inputs(region="us-west-2", env_region="us-west-2"), {})
    assert ("service-quotas", "us-west-2") in regions
    assert {r for s, r in regions if s == "service-quotas"} == {"us-west-2"}
    spot = [f for f in made["service-quotas"].calls if f[1]["QuotaCode"] in (SPOT_P, SPOT_G)]
    assert len(spot) == 2


def test_spot_quota_check_never_requests_a_quota():
    factory, made = clients(
        service_quotas={
            "get_service_quota": quota_by_code(
                **{SPOT_P: "quota_spot_p_0", SPOT_G: "quota_spot_g_0"}
            )
        }
    )
    findings = aws.run_checks(factory, inputs(), {})
    assert Level.FAIL not in levels(findings, "aws/spot-quota")
    methods = {m for m, _ in made["service-quotas"].calls}
    assert methods <= {"get_service_quota", "get_aws_default_service_quota"}
    source = Path(aws.__file__).read_text(encoding="utf-8")
    assert "request_service_quota_increase" not in source
    assert "RequestServiceQuotaIncrease" not in source


def test_capacity_block_no_access_is_a_warning():
    factory, _ = clients(
        ec2={"describe_capacity_block_offerings": aws_reply("capacity_block_limit")}
    )
    findings = aws.run_checks(factory, inputs(), {})
    cb = [f for f in findings if f.check == "aws/capacity-blocks"][0]
    assert cb.level is Level.WARN
    assert "no access" in cb.message and "CapacityBlockDescribeLimitExceeded" in cb.message
    assert exit_code(findings) == 0


def test_capacity_block_call_uses_the_config():
    factory, made = clients()
    aws.run_checks(factory, inputs(), {})
    calls = [c for c in made["ec2"].calls if c[0] == "describe_capacity_block_offerings"]
    assert calls == [
        (
            "describe_capacity_block_offerings",
            {"InstanceType": "p5.48xlarge", "InstanceCount": 1, "CapacityDurationHours": 24},
        )
    ]


def test_region_mismatch_is_a_warning():
    factory, _ = clients()
    findings = aws.run_checks(factory, inputs(env_region="us-west-2"), {})
    assert levels(findings, "aws/region") == [Level.WARN]


@pytest.mark.parametrize(
    ("profile", "profiles", "level"),
    [
        ({"login_session": "arn:aws:iam::test-account:user/dev"}, {}, Level.WARN),
        (
            {"role_arn": "arn:aws:iam::test-account:role/x", "source_profile": "base"},
            {"base": {"login_session": "arn:aws:iam::test-account:user/dev"}},
            Level.WARN,
        ),
        ({"credential_process": "aws configure export-credentials --profile dev"}, {}, Level.PASS),
        ({"sso_session": "dev"}, {}, Level.PASS),
    ],
)
def test_profile_check(profile, profiles, level):
    assert aws.check_profile(profile, profiles).level is level


@pytest.mark.parametrize(
    ("instance_type", "family"),
    [("t4g.small", "t"), ("p5.48xlarge", "p"), ("inf2.xlarge", "inf"), ("im4gn.large", "im")],
)
def test_instance_family(instance_type, family):
    assert aws.instance_family(instance_type) == family
