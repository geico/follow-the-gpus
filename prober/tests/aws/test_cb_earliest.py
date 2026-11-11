from datetime import UTC, datetime, timedelta

import pytest

from ftg_prober.aws.cb_earliest import CapacityBlockEarliestProbe, earliest_plan, search_window
from ftg_prober.config import MAX_CB_CALLS_PER_REGION
from tests.aws.fakes import NOW, FakeClientError, FakeEc2, make_ctx, provider_response, section

OFFERING = {
    "CapacityBlockOfferingId": "cb-0123456789abcdef0",
    "InstanceType": "p5.48xlarge",
    "AvailabilityZone": "us-east-1a",
    "InstanceCount": 1,
    "StartDate": datetime(2026, 10, 11, 12, 37, tzinfo=UTC),
    "EndDate": datetime(2026, 10, 12, 11, 30, tzinfo=UTC),
    "CapacityBlockDurationHours": 22,
    "CapacityBlockDurationMinutes": 53,
    "UpfrontFee": "700.50",
    "CurrencyCode": "USD",
    "Tenancy": "default",
}


def utc(*args):
    return datetime(2026, 10, 13, *args, tzinfo=UTC)


def one_region(**overrides):
    return section(regions=["us-east-1"], **overrides)


def test_an_offering_gives_a_success_row_with_the_raw_fields_of_aws_cb_offering():
    answer = {"CapacityBlockOfferings": [OFFERING]}
    ec2 = FakeEc2("us-east-1", describe_capacity_block_offerings=[answer])
    rows = CapacityBlockEarliestProbe().run(make_ctx(one_region(), {"us-east-1": ec2}))

    assert len(rows) == 1
    row = rows[0]
    assert (row.probe_type, row.provider, row.region, row.zone) == (
        "aws_cb_earliest",
        "aws",
        "us-east-1",
        "us-east-1a",
    )
    assert (row.sku, row.outcome, row.n_requested, row.error_code) == (
        "p5.48xlarge",
        "success",
        1,
        None,
    )
    assert row.details == {
        "start": "2026-10-11T12:37:00.000Z",
        "end": "2026-10-12T11:30:00.000Z",
        "duration_hours": 22,
        "duration_minutes": 53,
        "instance_count": 1,
        "upfront_fee": 700.5,
        "currency": "USD",
        "offering_id": "cb-0123456789abcdef0",
        "tenancy": "default",
        "zone_type": None,
        "ultraserver_type": None,
        "ultraserver_count": None,
    }
    assert row.validate() == []


def test_the_request_has_the_window_of_the_clock():
    ec2 = FakeEc2("us-east-1")
    CapacityBlockEarliestProbe().run(make_ctx(one_region(), {"us-east-1": ec2}))

    name, kwargs = ec2.calls[0]
    assert name == "describe_capacity_block_offerings"
    assert kwargs == {
        "InstanceType": "p5.48xlarge",
        "InstanceCount": 1,
        "CapacityDurationHours": 24,
        "StartDateRange": NOW,
        # NOW is 12:07:30. first = 12:37:30, the next 11:30 is on 2026-10-12.
        "EndDateRange": datetime(2026, 10, 13, 12, 30, tzinfo=UTC),
        "MaxResults": 1000,
    }


def test_no_offering_gives_an_explicit_none_row():
    ec2 = FakeEc2("us-east-1", describe_capacity_block_offerings=[{"CapacityBlockOfferings": []}])
    rows = CapacityBlockEarliestProbe().run(make_ctx(one_region(), {"us-east-1": ec2}))

    assert [(r.outcome, r.zone, r.details) for r in rows] == [
        ("none", None, {"duration_hours": 24})
    ]
    assert rows[0].validate() == []


def test_an_error_gives_an_error_row_with_the_raw_code_and_the_other_calls_go_on():
    ec2_east = FakeEc2(
        "us-east-1", describe_capacity_block_offerings=[FakeClientError("InvalidParameterValue")]
    )
    ec2_west = FakeEc2(
        "us-west-2", describe_capacity_block_offerings=[{"CapacityBlockOfferings": [OFFERING]}]
    )
    rows = CapacityBlockEarliestProbe().run(
        make_ctx(section(), {"us-east-1": ec2_east, "us-west-2": ec2_west})
    )

    assert [(r.region, r.outcome, r.error_code) for r in rows] == [
        ("us-east-1", "error", "InvalidParameterValue"),
        ("us-west-2", "success", None),
    ]
    assert rows[0].details == {"duration_hours": 24}


@pytest.mark.parametrize(
    ("now", "end"),
    [
        # Before 11:00: first = 10:30, the boundary is 11:30 on the same day.
        (utc(10, 0), utc(11, 30) + timedelta(hours=24 + 1)),
        # At 11:00: first = 11:30, which is the boundary.
        (utc(11, 0), utc(11, 30) + timedelta(hours=24 + 1)),
        # Between 11:00 and 11:30: first is after 11:30, so the boundary is the next day.
        (utc(11, 10), utc(11, 30) + timedelta(days=1, hours=24 + 1)),
        # Just after 11:30.
        (utc(11, 31), utc(11, 30) + timedelta(days=1, hours=24 + 1)),
        # Late in the day.
        (utc(23, 59), utc(11, 30) + timedelta(days=1, hours=24 + 1)),
    ],
)
def test_the_window_ends_1h_after_the_block_of_the_next_11_30(now, end):
    assert search_window(now, 24) == (now, end)


def test_the_window_is_at_least_1_day_after_the_start_aws_uses():
    # A 2 h block with `first` = 10:30: boundary + 2 h + 1 h is only 4 h after `first`.
    now = utc(10, 0)
    start, end = search_window(now, 2)
    assert start == now
    assert end == utc(10, 30) + timedelta(days=1)


def test_the_repo_plan_with_the_other_probe_fits_the_limit(repo_config):
    plan = earliest_plan(repo_config.aws)

    assert len(plan) == 42
    assert plan[:4] == [
        ("us-east-1", "p5.4xlarge", 24, 1),
        ("us-east-1", "p5.4xlarge", 168, 1),
        ("us-east-1", "p5.4xlarge", 24, 4),
        ("us-east-1", "p5.4xlarge", 168, 4),
    ]
    for region in repo_config.aws["regions"]:
        calls = sum(1 for r, *_ in plan if r == region)
        assert calls == 14  # 7 (type, count) pairs x 2 durations
        assert calls + 28 + 133 <= MAX_CB_CALLS_PER_REGION


def test_the_run_makes_exactly_the_calls_of_the_plan():
    config = section(
        capacity_blocks={
            "instance_types": ["p5.48xlarge", "p4d.24xlarge"],
            "instance_count": 1,
            "duration_hours": [24, 48],
            "earliest_duration_hours": [48],
            "start_window_days": 14,
        }
    )
    clients = {"us-east-1": FakeEc2("us-east-1"), "us-west-2": FakeEc2("us-west-2")}
    CapacityBlockEarliestProbe().run(make_ctx(config, clients))

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
    assert made == earliest_plan(config)
    assert {hours for _, _, hours, _ in made} == {48}


def test_the_probe_plan_is_the_calls_of_the_plan_as_queue_items():
    config = section(regions=["us-east-1"])
    calls = CapacityBlockEarliestProbe().plan(config)

    assert [(c.probe_type, c.region, c.args) for c in calls] == [
        ("aws_cb_earliest", "us-east-1", ("p5.48xlarge", 24, 1))
    ]


def test_the_provider_response_gives_a_success_row_with_the_real_offering():
    """Runs the real probe against a reconstructed `DescribeCapacityBlockOfferings` response, in
    the shape of the AWS EC2 API reference (`CapacityBlockOffering` object). The fixture holds no
    recorded `p4d.24xlarge` success offering (this project's own stored rows for it are `none`);
    `UpfrontFee` is illustrative, not from a stored row. The offer's `StartDate`/`EndDate` are a
    plausible answer to the request this probe sends at `NOW` (`search_window(NOW, 24)`):
    1 day (24 hours) starting at the next 11:30 UTC boundary, inside `[StartDateRange,
    EndDateRange]`.
    https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html
    https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityBlockOffering.html
    """
    body = provider_response("describe_capacity_block_offerings_earliest_response.json")
    config = section(
        regions=["us-west-2"],
        capacity_blocks={
            "instance_types": ["p4d.24xlarge"],
            "instance_count": 1,
            "duration_hours": [24],
            "start_window_days": 14,
        },
    )
    ec2 = FakeEc2("us-west-2", describe_capacity_block_offerings=[body])
    rows = CapacityBlockEarliestProbe().run(make_ctx(config, {"us-west-2": ec2}))

    assert len(rows) == 1
    kwargs = ec2.calls[0][1]
    offer_start = datetime(2026, 10, 12, 11, 30, tzinfo=UTC)
    offer_end = datetime(2026, 10, 13, 11, 30, tzinfo=UTC)
    assert kwargs["StartDateRange"] <= offer_start
    assert offer_end <= kwargs["EndDateRange"]
    row = rows[0]
    assert (row.probe_type, row.region, row.zone, row.sku, row.outcome, row.error_code) == (
        "aws_cb_earliest",
        "us-west-2",
        "us-west-2a",
        "p4d.24xlarge",
        "success",
        None,
    )
    assert row.details == {
        "start": "2026-10-12T11:30:00.000Z",
        "end": "2026-10-13T11:30:00.000Z",
        "duration_hours": 24,
        "duration_minutes": 0,
        "instance_count": 1,
        "upfront_fee": 755.04,
        "currency": "USD",
        "offering_id": "cb-0123456789abcdef2",
        "tenancy": "default",
        "zone_type": None,
        "ultraserver_type": None,
        "ultraserver_count": None,
    }
    assert row.validate() == []


def test_the_earliest_plan_asks_for_each_count_of_a_type():
    config = section(
        regions=["us-east-1"],
        capacity_blocks={
            "instance_types": ["p5.4xlarge", "p5.48xlarge"],
            "instance_count": 1,
            "instance_counts": {"p5.4xlarge": [1, 8]},
            "duration_hours": [24],
            "start_window_days": 14,
        },
    )
    assert earliest_plan(config) == [
        ("us-east-1", "p5.4xlarge", 24, 1),
        ("us-east-1", "p5.4xlarge", 24, 8),
        ("us-east-1", "p5.48xlarge", 24, 1),
    ]
