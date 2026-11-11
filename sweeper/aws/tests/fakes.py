"""Real boto3 clients behind a botocore Stubber, so every fake request and response is checked
against the real EC2 and S3 service models (a fake must match the real thing). No network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from botocore.stub import ANY, Stubber

from sweeper.rules import LISTED_STATES

NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
EXPERIMENT = "ftg-20261015-test"
# Not a real account ID.
ACCOUNT = "test-account"


def rid(n: int) -> str:
    return f"cr-{n:017x}"


def reservation(
    n: int,
    *,
    age_seconds: float,
    state: str = "active",
    experiment: str | None = EXPERIMENT,
    region: str = "us-east-1",
) -> dict[str, Any]:
    """1 `CapacityReservations` item in the shape `DescribeCapacityReservations` returns."""
    created = NOW - timedelta(seconds=age_seconds)
    tags = [
        {"Key": "target", "Value": f"m5.large-{region}a"},
        {"Key": "run_id", "Value": f"run-{n}"},
    ]
    if experiment is not None:
        tags.append({"Key": "experiment", "Value": experiment})
    return {
        "CapacityReservationId": rid(n),
        "OwnerId": ACCOUNT,
        "CapacityReservationArn": f"arn:aws:ec2:{region}:{ACCOUNT}:capacity-reservation/{rid(n)}",
        "InstanceType": "m5.large",
        "InstancePlatform": "Linux/UNIX",
        "AvailabilityZone": f"{region}a",
        "Tenancy": "default",
        "TotalInstanceCount": 2,
        "AvailableInstanceCount": 2,
        "EbsOptimized": True,
        "EphemeralStorage": False,
        "State": state,
        "StartDate": created,
        "EndDate": created + timedelta(seconds=220),
        "EndDateType": "limited",
        "InstanceMatchCriteria": "targeted",
        "CreateDate": created,
        "Tags": tags,
    }


def describe_params(experiment: str = EXPERIMENT, token: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "Filters": [
            {"Name": "tag:experiment", "Values": [experiment]},
            {"Name": "state", "Values": list(LISTED_STATES)},
        ]
    }
    if token:
        params["NextToken"] = token
    return params


def stubbed_ec2(region: str = "us-east-1") -> tuple[Any, Stubber]:
    client = boto3.client("ec2", region_name=region)
    return client, Stubber(client)


def stubbed_s3(region: str = "us-east-1") -> tuple[Any, Stubber, list[dict[str, Any]]]:
    """An S3 client whose `PutObject` calls are recorded with their full parameters."""
    client = boto3.client("s3", region_name=region)
    calls: list[dict[str, Any]] = []

    def record(params: dict[str, Any], **_: Any) -> None:
        calls.append(dict(params))

    client.meta.events.register("provide-client-params.s3.PutObject", record)
    return client, Stubber(client), calls


def put_object_ok(stub: Stubber) -> None:
    stub.add_response(
        "put_object",
        {"ETag": '"etag-1"'},
        {"Bucket": ANY, "Key": ANY, "Body": ANY},
    )
