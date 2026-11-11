"""Tests of `python -m ftg_preflight.quota_aws` with fake clients. No network, no credentials."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from ftg_preflight import quota_aws
from tests.fakes import FakeClient

CONFIG = """
aws:
  jobs:
    - {module: aws_spot_score, regions: [us-east-1, us-west-2, us-east-2]}
  quota:
    regions: [us-east-1, us-west-2]
    sizes:
      - {size: p5.4xlarge, count: 8}
      - {size: g5.xlarge, count: 16}
      - {size: g4dn.xlarge, count: 32}
      - {size: g6e.xlarge, count: 16, regions: [us-west-2]}
"""
VCPUS = {"p5.4xlarge": 16, "g5.xlarge": 4, "g4dn.xlarge": 4, "g6e.xlarge": 4}
P_OD, P_SPOT = "L-417A185B", "L-7212CCBC"
G_OD, G_SPOT = "L-DB2E81BA", "L-3819A6DF"


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


class World:
    """Fake EC2 and Service Quotas clients for every Region, and a record of the requests."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], float] = {}  # (region, code) -> account value
        self.defaults: dict[str, float] = {}  # code -> AWS default (the fallback)
        self.fail_request: set[str] = set()
        self.requests: list[tuple[str, str, float]] = []

    def __call__(self, service: str, region: str) -> FakeClient:
        if service == "ec2":
            return FakeClient({"describe_instance_types": self.describe})
        return FakeClient(
            {
                "get_service_quota": lambda **kw: self.get(region, **kw),
                "get_aws_default_service_quota": lambda **kw: self.default(**kw),
                "request_service_quota_increase": lambda **kw: self.request(region, **kw),
            }
        )

    @staticmethod
    def describe(InstanceTypes: list[str]) -> dict[str, Any]:  # noqa: N803 - boto3 names
        (name,) = InstanceTypes
        if name not in VCPUS:
            raise client_error("InvalidInstanceType")
        return {
            "InstanceTypes": [{"InstanceType": name, "VCpuInfo": {"DefaultVCpus": VCPUS[name]}}]
        }

    def get(self, region: str, ServiceCode: str, QuotaCode: str) -> dict[str, Any]:  # noqa: N803
        if (region, QuotaCode) not in self.values:
            raise client_error("NoSuchResourceException")
        return {"Quota": {"Value": self.values[(region, QuotaCode)]}}

    def default(self, ServiceCode: str, QuotaCode: str) -> dict[str, Any]:  # noqa: N803
        return {"Quota": {"Value": self.defaults.get(QuotaCode, 0.0)}}

    def request(self, region: str, ServiceCode: str, QuotaCode: str, DesiredValue: float) -> Any:  # noqa: N803
        if QuotaCode in self.fail_request:
            raise client_error("ResourceAlreadyExistsException")
        self.requests.append((region, QuotaCode, DesiredValue))
        return {"RequestedQuota": {"Status": "PENDING"}}


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "targets.yaml"
    path.write_text(CONFIG)
    return path


def run(config: Path, world: World, *extra: str) -> int:
    return quota_aws.main(["--config", str(config), *extra], clients=world)


@pytest.mark.parametrize(
    ("instance_type", "group"),
    [("p5.4xlarge", "P"), ("p6-b200.48xlarge", "P"), ("g5.xlarge", "G and VT"),
     ("g7e.2xlarge", "G and VT"), ("vt1.3xlarge", "G and VT")],
)  # fmt: skip
def test_quota_group(instance_type, group):
    assert quota_aws.quota_group(instance_type) == group


@pytest.mark.parametrize(
    "instance_type", ["m5.large", "trn1.2xlarge", "inf2.xlarge", "gr6.4xlarge"]
)
def test_quota_group_rejects_other_families(instance_type):
    with pytest.raises(ValueError, match="no GPU quota group"):
        quota_aws.quota_group(instance_type)


def test_dry_run_prints_on_demand_and_spot_and_sends_nothing(config, capsys):
    world = World()
    assert run(config, world) == 0
    out = capsys.readouterr().out
    assert world.requests == []
    assert "dry run" in out
    # us-east-1: P = 8 x 16 = 128. G and VT = 16 x 4 + 32 x 4 = 192.
    # us-west-2 adds 16 x 4 = 64 to G and VT: 256.
    for code, value, region in [
        (P_OD, 128, "us-east-1"), (P_SPOT, 128, "us-east-1"),
        (G_OD, 192, "us-east-1"), (G_SPOT, 192, "us-east-1"),
        (G_OD, 256, "us-west-2"), (G_SPOT, 256, "us-west-2"),
    ]:  # fmt: skip
        assert (
            f"aws service-quotas request-service-quota-increase --service-code ec2 "
            f"--quota-code {code} --desired-value {value} --region {region}" in out
        )
    assert "us-east-2" not in out  # not a quota region
    assert "## 8 requests to send, 0 errors." in out


def test_apply_sends_the_requests(config):
    world = World()
    assert run(config, world, "--apply") == 0
    assert sorted(world.requests) == sorted(
        [
            ("us-east-1", P_OD, 128.0), ("us-east-1", P_SPOT, 128.0),
            ("us-east-1", G_OD, 192.0), ("us-east-1", G_SPOT, 192.0),
            ("us-west-2", P_OD, 128.0), ("us-west-2", P_SPOT, 128.0),
            ("us-west-2", G_OD, 256.0), ("us-west-2", G_SPOT, 256.0),
        ]
    )  # fmt: skip


def test_a_quota_that_is_high_enough_is_skipped(config, capsys):
    world = World()
    world.values[("us-east-1", P_SPOT)] = 128  # the account value
    world.defaults[G_OD] = 1000  # no account value: the AWS default counts
    assert run(config, world, "--apply") == 0
    sent = {(r, c) for r, c, _ in world.requests}
    assert ("us-east-1", P_SPOT) not in sent
    assert ("us-east-1", G_OD) not in sent and ("us-west-2", G_OD) not in sent
    assert ("us-east-1", P_OD) in sent
    out = capsys.readouterr().out
    assert f"# us-east-1 '{P_SPOT}': limit 128 >= target 128; skip" in out


def test_a_failed_request_counts_an_error_and_the_run_continues(config, capsys):
    world = World()
    world.fail_request = {P_SPOT}
    assert run(config, world, "--apply") == 1
    assert {c for _, c, _ in world.requests} == {P_OD, G_OD, G_SPOT}
    out = capsys.readouterr().out
    assert f"# us-east-1 '{P_SPOT}': FAILED" in out
    assert "## 6 requests sent, 2 errors." in out


def test_an_unknown_family_is_an_error_with_a_message(tmp_path, capsys):
    path = tmp_path / "targets.yaml"
    path.write_text(
        "aws:\n  jobs: [{module: aws_spot_score, regions: [us-east-1]}]\n  quota:\n"
        "    regions: [us-east-1]\n"
        "    sizes: [{size: m5.large, count: 1}, {size: g5.xlarge, count: 2}]\n"
    )
    world = World()
    world.defaults.update({G_OD: 0})
    VCPUS["m5.large"] = 2
    try:
        assert run(path, world) == 1
    finally:
        del VCPUS["m5.large"]
    out = capsys.readouterr().out
    assert "# us-east-1 m5.large: no GPU quota group (this command knows P, G, and VT); skip" in out
    assert "--quota-code L-DB2E81BA --desired-value 8" in out  # the g5 ask still goes out


def test_an_unreadable_instance_type_is_an_error(tmp_path, capsys):
    path = tmp_path / "targets.yaml"
    path.write_text(
        "aws:\n  jobs: [{module: aws_spot_score, regions: [us-east-1]}]\n  quota:\n"
        "    regions: [us-east-1]\n"
        "    sizes: [{size: g99.xlarge, count: 1}]\n"
    )
    assert run(path, World()) == 1
    assert "g99.xlarge: cannot read the vCPUs" in capsys.readouterr().out


def test_an_unreadable_quota_is_an_error_and_is_not_requested(config, capsys):
    world = World()

    def broken(**_: Any) -> Any:
        raise client_error("AccessDeniedException")

    real = world.__call__

    def clients(service: str, region: str) -> FakeClient:
        client = real(service, region)
        if service == "service-quotas":
            client.replies["get_service_quota"] = broken
        return client

    assert quota_aws.main(["--config", str(config), "--apply"], clients=clients) == 1
    assert world.requests == []
    assert "cannot read the quota" in capsys.readouterr().out


@pytest.mark.parametrize(
    "bad",
    [
        "regions: [eastus]\n    sizes: [{size: g5.xlarge, count: 1}]",
        "regions: [us-east-1]\n    sizes: [{size: G5, count: 1}]",
        "regions: [us-east-1]\n    sizes: [{size: g5.xlarge, count: 0}]",
        "regions: [us-east-1, US-EAST-1]\n    sizes: [{size: g5.xlarge, count: 1}]",
        "regions: [us-east-1]\n    sizes: [{size: g5.xlarge, count: 1, regions: [us-west-2]}]",
        "regions: [us-east-1]\n    sizes: [{size: g5.large, count: 1}, {size: G5.large, count: 2}]",
    ],
)
def test_a_bad_quota_section_exits_2(tmp_path, capsys, bad):
    path = tmp_path / "targets.yaml"
    path.write_text(f"aws:\n  regions: [us-east-1, us-west-2, eastus]\n  quota:\n    {bad}\n")
    world = World()
    assert run(path, world) == 2
    assert "is not valid" in capsys.readouterr().err


def test_a_missing_quota_section_exits_2(tmp_path, capsys):
    path = tmp_path / "targets.yaml"
    path.write_text("aws:\n  regions: [us-east-1]\n")
    assert run(path, World()) == 2
    assert "no aws.quota section" in capsys.readouterr().err


def test_the_repo_targets_file_loads_for_aws():
    from ftg_preflight.quota import load_quota

    repo = Path(__file__).resolve().parents[2] / "config" / "targets.yaml"
    regions, sizes = load_quota(repo, "aws")
    assert regions == ["us-east-1", "us-east-2", "us-west-2"]
    assert [quota_aws.quota_group(s.size) for s in sizes] == ["P"] + ["G and VT"] * 5
