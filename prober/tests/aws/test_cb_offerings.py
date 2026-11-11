from datetime import UTC, datetime, timedelta

from ftg_prober.aws.cb_offerings import CapacityBlockOfferingsProbe, request_plan
from ftg_prober.config import MAX_CB_CALLS_PER_REGION
from tests.aws.fakes import NOW, FakeClientError, FakeEc2, make_ctx, provider_response, section

OFFERING = {
    "CapacityBlockOfferingId": "cb-0123456789abcdef0",
    "InstanceType": "p5.48xlarge",
    "AvailabilityZone": "us-east-1a",
    "InstanceCount": 1,
    "StartDate": datetime(2026, 10, 14, 11, 30, tzinfo=UTC),
    "EndDate": datetime(2026, 10, 15, 11, 30, tzinfo=UTC),
    "CapacityBlockDurationHours": 24,
    "UpfrontFee": "755.04",
    "CurrencyCode": "USD",
    "Tenancy": "default",
}


def one_region(**overrides):
    return section(regions=["us-east-1"], **overrides)


def test_one_row_per_offering_with_the_details_of_spec_4_1():
    second = {**OFFERING, "AvailabilityZone": "us-east-1b", "CapacityBlockOfferingId": "cb-2"}
    ec2 = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[{"CapacityBlockOfferings": [OFFERING, second]}],
    )
    rows = CapacityBlockOfferingsProbe().run(make_ctx(one_region(), {"us-east-1": ec2}))

    assert [r.zone for r in rows] == ["us-east-1a", "us-east-1b"]
    first = rows[0]
    assert first.probe_type == "aws_cb_offering"
    assert first.provider == "aws"
    assert first.region == "us-east-1"
    assert first.sku == "p5.48xlarge"
    assert first.outcome == "success"
    assert first.n_requested == 1
    assert first.n_placed is None
    assert first.error_code is None
    assert first.details == {
        "start": "2026-10-14T11:30:00.000Z",
        "end": "2026-10-15T11:30:00.000Z",
        "duration_hours": 24,
        "duration_minutes": None,
        "instance_count": 1,
        "upfront_fee": 755.04,
        "currency": "USD",
        "offering_id": "cb-0123456789abcdef0",
        "tenancy": "default",
        "zone_type": None,
        "ultraserver_type": None,
        "ultraserver_count": None,
    }
    assert all(r.validate() == [] for r in rows)


def test_the_minutes_of_a_partial_block_are_kept_raw():
    partial = {
        **OFFERING,
        "CapacityBlockDurationHours": 2,
        "CapacityBlockDurationMinutes": 35,
    }
    ec2 = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[{"CapacityBlockOfferings": [partial]}],
    )
    rows = CapacityBlockOfferingsProbe().run(make_ctx(one_region(), {"us-east-1": ec2}))

    assert rows[0].details["duration_hours"] == 2
    assert rows[0].details["duration_minutes"] == 35


def test_the_other_offering_fields_are_stored_raw():
    """Keys of `CapacityBlockOffering` in botocore 1.43.114 and
    https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityBlockOffering.html:
    `Tenancy`, `ZoneType`, `UltraserverType` (strings) and `UltraserverCount` (integer)."""
    full = {
        **OFFERING,
        "Tenancy": "dedicated",
        "ZoneType": "availability-zone",
        "UltraserverType": "u-p6e-gb200x72",
        "UltraserverCount": 2,
    }
    bare = {k: v for k, v in OFFERING.items() if k != "Tenancy"}
    ec2 = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[{"CapacityBlockOfferings": [full, bare]}],
    )
    rows = CapacityBlockOfferingsProbe().run(make_ctx(one_region(), {"us-east-1": ec2}))

    keys = ("tenancy", "zone_type", "ultraserver_type", "ultraserver_count")
    assert [r.details[k] for r in rows[:1] for k in keys] == [
        "dedicated",
        "availability-zone",
        "u-p6e-gb200x72",
        2,
    ]
    assert [rows[1].details[k] for k in keys] == [None, None, None, None]
    assert all(r.validate() == [] for r in rows)


def test_request_parameters_come_from_the_config():
    ec2 = FakeEc2("us-east-1")
    config = one_region(
        capacity_blocks={
            "instance_types": ["p5.48xlarge", "p4d.24xlarge"],
            "instance_count": 2,
            "duration_hours": [24, 48],
            "start_window_days": 7,
        }
    )
    CapacityBlockOfferingsProbe().run(make_ctx(config, {"us-east-1": ec2}))

    assert len(ec2.calls) == 4
    name, kwargs = ec2.calls[1]
    assert name == "describe_capacity_block_offerings"
    assert kwargs == {
        "InstanceType": "p5.48xlarge",
        "InstanceCount": 2,
        "CapacityDurationHours": 48,
        "StartDateRange": NOW,
        "EndDateRange": NOW + timedelta(days=7, hours=48),
        "MaxResults": 1000,
    }
    assert [c[1]["InstanceType"] for c in ec2.calls] == [
        "p5.48xlarge",
        "p5.48xlarge",
        "p4d.24xlarge",
        "p4d.24xlarge",
    ]


def test_no_offering_gives_an_explicit_none_row():
    ec2 = FakeEc2("us-east-1", describe_capacity_block_offerings=[{"CapacityBlockOfferings": []}])
    rows = CapacityBlockOfferingsProbe().run(make_ctx(one_region(), {"us-east-1": ec2}))

    assert len(rows) == 1
    assert rows[0].outcome == "none"
    assert rows[0].zone is None
    assert rows[0].details == {"duration_hours": 24, "start_window_days": 14}
    assert rows[0].validate() == []


def test_next_token_is_followed():
    second = {**OFFERING, "AvailabilityZone": "us-east-1c"}
    ec2 = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[
            {"CapacityBlockOfferings": [OFFERING], "NextToken": "page-2"},
            {"CapacityBlockOfferings": [second]},
        ],
    )
    rows = CapacityBlockOfferingsProbe().run(make_ctx(one_region(), {"us-east-1": ec2}))

    assert [r.zone for r in rows] == ["us-east-1a", "us-east-1c"]
    assert "NextToken" not in ec2.calls[0][1]
    assert ec2.calls[1][1]["NextToken"] == "page-2"


def test_an_api_error_gives_an_error_row_with_the_raw_code_and_the_other_calls_go_on():
    ec2_east = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[FakeClientError("InvalidParameterValue")],
    )
    ec2_west = FakeEc2(
        "us-west-2",
        describe_capacity_block_offerings=[{"CapacityBlockOfferings": [OFFERING]}],
    )
    rows = CapacityBlockOfferingsProbe().run(
        make_ctx(section(), {"us-east-1": ec2_east, "us-west-2": ec2_west})
    )

    assert [(r.region, r.outcome, r.error_code) for r in rows] == [
        ("us-east-1", "error", "InvalidParameterValue"),
        ("us-west-2", "success", None),
    ]


def test_an_unsupported_code_and_a_transport_failure_are_kept_raw():
    class EndpointConnectionError(Exception):
        pass

    ec2 = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[
            FakeClientError("UnsupportedOperation"),
            EndpointConnectionError("no route"),
        ],
    )
    config = one_region(
        capacity_blocks={
            "instance_types": ["p6-b300.48xlarge"],
            "instance_count": 1,
            "duration_hours": [24, 72],
            "start_window_days": 14,
        }
    )
    rows = CapacityBlockOfferingsProbe().run(make_ctx(config, {"us-east-1": ec2}))

    assert [r.error_code for r in rows] == ["UnsupportedOperation", "EndpointConnectionError"]
    assert all(r.outcome == "error" for r in rows)


def test_the_repo_plan_makes_28_calls_per_region_under_the_limit(repo_config):
    plan = request_plan(repo_config.aws)

    assert len(plan) == 84
    assert plan[:5] == [
        ("us-east-1", "p5.4xlarge", 24, 1),
        ("us-east-1", "p5.4xlarge", 72, 1),
        ("us-east-1", "p5.4xlarge", 120, 1),
        ("us-east-1", "p5.4xlarge", 168, 1),
        ("us-east-1", "p5.4xlarge", 24, 4),
    ]
    for region in repo_config.aws["regions"]:
        calls = sum(1 for r, *_ in plan if r == region)
        assert calls == 28  # 7 (type, count) pairs x 4 durations
        assert calls <= MAX_CB_CALLS_PER_REGION


def test_the_run_makes_exactly_the_calls_of_the_plan():
    config = section(
        capacity_blocks={
            "instance_types": ["p5.48xlarge", "p4d.24xlarge"],
            "instance_count": 1,
            "duration_hours": [24, 48],
            "start_window_days": 14,
        }
    )
    clients = {"us-east-1": FakeEc2("us-east-1"), "us-west-2": FakeEc2("us-west-2")}
    CapacityBlockOfferingsProbe().run(make_ctx(config, clients))

    made = [
        (
            region,
            kwargs["InstanceType"],
            kwargs["CapacityDurationHours"],
            kwargs["InstanceCount"],
        )
        for region, ec2 in clients.items()
        for _, kwargs in ec2.calls
    ]
    assert made == request_plan(config)


def test_a_throttled_call_gives_1_error_row_and_the_other_calls_of_the_run_still_run():
    config = section(
        capacity_blocks={
            "instance_types": ["p5.48xlarge", "p4d.24xlarge"],
            "instance_count": 1,
            "duration_hours": [24],
            "start_window_days": 14,
        }
    )
    ec2_east = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[
            FakeClientError("RequestLimitExceeded"),
            {"CapacityBlockOfferings": []},
        ],
    )
    ec2_west = FakeEc2(
        "us-west-2",
        describe_capacity_block_offerings=[
            {"CapacityBlockOfferings": [OFFERING]},
            {"CapacityBlockOfferings": []},
        ],
    )
    rows = CapacityBlockOfferingsProbe().run(
        make_ctx(config, {"us-east-1": ec2_east, "us-west-2": ec2_west})
    )

    assert len(ec2_east.calls) + len(ec2_west.calls) == 4
    assert [(r.region, r.sku, r.outcome, r.error_code) for r in rows] == [
        ("us-east-1", "p5.48xlarge", "error", "RequestLimitExceeded"),
        ("us-east-1", "p4d.24xlarge", "none", None),
        ("us-west-2", "p5.48xlarge", "success", None),
        ("us-west-2", "p4d.24xlarge", "none", None),
    ]
    assert rows[0].zone is None
    assert rows[0].details == {"duration_hours": 24, "start_window_days": 14}
    assert all(r.validate() == [] for r in rows)


def test_the_account_limit_code_gives_1_error_row_per_call_and_each_call_runs():
    code = "CapacityBlockDescribeLimitExceeded"
    config = section(
        capacity_blocks={
            "instance_types": ["p5.48xlarge", "p4d.24xlarge"],
            "instance_count": 1,
            "duration_hours": [24],
            "start_window_days": 14,
        }
    )
    clients = {
        region: FakeEc2(region, describe_capacity_block_offerings=lambda **_: FakeClientError(code))
        for region in ("us-east-1", "us-west-2")
    }
    rows = CapacityBlockOfferingsProbe().run(make_ctx(config, clients))

    assert sum(len(ec2.calls) for ec2 in clients.values()) == 4
    assert len(rows) == 4
    assert all(r.outcome == "error" and r.error_code == code for r in rows)


def test_the_probe_counts_the_requests_of_a_call_with_pages():
    second = {**OFFERING, "AvailabilityZone": "us-east-1c"}
    ec2 = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[
            {"CapacityBlockOfferings": [OFFERING], "NextToken": "page-2"},
            {"CapacityBlockOfferings": [second]},
        ],
    )
    ctx = make_ctx(one_region(), {"us-east-1": ec2})
    CapacityBlockOfferingsProbe().run(ctx)

    assert ctx.api_calls == 2


def test_the_provider_response_gives_a_row_per_real_offering():
    """Runs the real probe against a reconstructed `DescribeCapacityBlockOfferings` response, in
    the shape of the AWS EC2 API reference (`CapacityBlockOffering` object). The offerings are
    grounded in this project's own stored row for `p6-b200.48xlarge` on 2026-10-14
    (`start`/`end`/`upfront_fee`), with 1 extra, illustrative offering in a 2nd zone.
    https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html
    https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityBlockOffering.html
    """
    body = provider_response("describe_capacity_block_offerings_response.json")
    config = section(
        regions=["us-east-1"],
        capacity_blocks={
            "instance_types": ["p6-b200.48xlarge"],
            "instance_count": 1,
            "duration_hours": [24],
            "start_window_days": 14,
        },
    )
    ec2 = FakeEc2("us-east-1", describe_capacity_block_offerings=[body])
    rows = CapacityBlockOfferingsProbe().run(make_ctx(config, {"us-east-1": ec2}))

    assert [r.zone for r in rows] == ["us-east-1d", "us-east-1f"]
    first = rows[0]
    assert first.probe_type == "aws_cb_offering"
    assert (first.outcome, first.error_code) == ("success", None)
    assert first.sku == "p6-b200.48xlarge"
    assert first.details == {
        "start": "2026-10-17T11:30:00.000Z",
        "end": "2026-10-18T11:30:00.000Z",
        "duration_hours": 24,
        "duration_minutes": 0,
        "instance_count": 1,
        "upfront_fee": 2727.98,
        "currency": "USD",
        "offering_id": "cb-0123456789abcdef0",
        "tenancy": "default",
        "zone_type": None,
        "ultraserver_type": None,
        "ultraserver_count": None,
    }
    assert all(r.validate() == [] for r in rows)


def test_a_type_in_instance_counts_is_asked_for_each_count_and_the_rest_use_instance_count():
    config = section(
        capacity_blocks={
            "instance_types": ["p5.4xlarge", "p5.48xlarge"],
            "instance_count": 2,
            "instance_counts": {"p5.4xlarge": [1, 4]},
            "duration_hours": [24],
            "start_window_days": 14,
        }
    )
    clients = {"us-east-1": FakeEc2("us-east-1"), "us-west-2": FakeEc2("us-west-2")}
    rows = CapacityBlockOfferingsProbe().run(make_ctx(config, clients))

    made = [
        (kwargs["InstanceType"], kwargs["InstanceCount"])
        for _, kwargs in clients["us-east-1"].calls
    ]
    assert made == [("p5.4xlarge", 1), ("p5.4xlarge", 4), ("p5.48xlarge", 2)]
    assert sorted({(r.sku, r.n_requested) for r in rows}) == [
        ("p5.48xlarge", 2),
        ("p5.4xlarge", 1),
        ("p5.4xlarge", 4),
    ]
