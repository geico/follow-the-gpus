"""Tests of terraform/aws/status.py with recorded describe_capacity_reservations output.

The response shape is the one in the API reference, as boto3 returns it (datetimes parsed):
https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityReservations.html
https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityReservation.html
"""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "terraform" / "aws" / "status.py"
spec = importlib.util.spec_from_file_location("aws_status", SCRIPT)
status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)

NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)


def reservation(rid: str, age_seconds: int, state: str = "active") -> dict:
    created = NOW - timedelta(seconds=age_seconds)
    return {
        "CapacityReservationId": rid,
        "OwnerId": "ACCOUNT_ID",  # placeholder; the real value has 12 digits,
        "CapacityReservationArn": f"arn:aws:ec2:us-east-1:ACCOUNT_ID:capacity-reservation/{rid}",
        "AvailabilityZoneId": "use1-az1",
        "InstanceType": "m5.large",
        "InstancePlatform": "Linux/UNIX",
        "AvailabilityZone": "us-east-1a",
        "Tenancy": "default",
        "TotalInstanceCount": 2,
        "AvailableInstanceCount": 2,
        "EbsOptimized": False,
        "EphemeralStorage": False,
        "State": state,
        "StartDate": created,
        "EndDate": created + timedelta(minutes=5),
        "EndDateType": "limited",
        "InstanceMatchCriteria": "targeted",
        "CreateDate": created,
        "Tags": [{"Key": "experiment", "Value": "ftg-test"}],
    }


class FakeEc2:
    def __init__(self, reservations: list[dict]):
        self.reservations = reservations
        self.calls: list[dict] = []

    def describe_capacity_reservations(self, **kwargs):
        self.calls.append(kwargs)
        return {"CapacityReservations": self.reservations}


def run(capsys, reservations: list[dict]) -> tuple[int, str, FakeEc2]:
    ec2 = FakeEc2(reservations)
    leaked = status.report_reservations({"us-east-1": ec2}, "ftg-test", NOW)
    return leaked, capsys.readouterr().out, ec2


def test_zero_leaks(capsys):
    leaked, out, ec2 = run(capsys, [reservation("cr-0123456789abcdef0", 60)])
    assert leaked == 0
    assert "cr-0123456789abcdef0  m5.large  count 2  active" in out
    assert "LEAKED" not in out
    assert "leaked resources: 0" in out
    assert ec2.calls[0]["Filters"] == [
        {"Name": "tag:experiment", "Values": ["ftg-test"]},
        {"Name": "state", "Values": ["active", "pending"]},
    ]


def test_no_reservations(capsys):
    leaked, out, _ = run(capsys, [])
    assert leaked == 0
    assert "leaked resources: 0" in out


def test_one_leak(capsys):
    leaked, out, _ = run(
        capsys,
        [
            reservation("cr-0123456789abcdef0", 60),
            reservation("cr-0fedcba9876543210", 600, "pending"),
        ],
    )
    assert leaked == 1
    assert "cr-0fedcba9876543210" in out and "LEAKED" in out
    assert out.count("LEAKED") == 1
    assert "leaked resources: 1" in out


def test_grace_boundary(capsys):
    leaked, _, _ = run(capsys, [reservation("cr-0123456789abcdef0", 180)])
    assert leaked == 0


def test_two_pages_follow_next_token(capsys):
    """Page 1 carries NextToken; the second call passes it, as the API returns it."""
    pages = [
        {
            "CapacityReservations": [reservation("cr-0123456789abcdef0", 60)],
            "NextToken": "tok-1",
        },
        {"CapacityReservations": [reservation("cr-0fedcba9876543210", 600)]},
    ]

    class PagedEc2(FakeEc2):
        def describe_capacity_reservations(self, **kwargs):
            self.calls.append(kwargs)
            return pages[len(self.calls) - 1]

    ec2 = PagedEc2([])
    leaked = status.report_reservations({"us-east-1": ec2}, "ftg-test", NOW)
    out = capsys.readouterr().out
    assert len(ec2.calls) == 2
    assert "NextToken" not in ec2.calls[0] and ec2.calls[1]["NextToken"] == "tok-1"
    assert leaked == 1
    assert "cr-0123456789abcdef0" in out and "cr-0fedcba9876543210" in out
    assert "active or pending: 2" in out


def test_no_regions_exits_nonzero_and_says_so(capsys, monkeypatch):
    # An empty target_regions must not print "leaked resources: 0".
    monkeypatch.setattr("sys.argv", ["status.py", "i-0", "bucket", "us-east-1", "ftg-test", "[]"])
    assert status.main() == 1
    captured = capsys.readouterr()
    assert "no Region was checked" in captured.err
    assert "leaked resources" not in captured.out + captured.err
