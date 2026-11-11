import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ftg_prober.aws.spot_price import SpotPriceProbe, crosses_hour
from tests.aws.fakes import NOW, FakeClientError, FakeEc2, make_ctx, section

START = NOW - timedelta(minutes=5)
GPU_TYPES = ["p5.48xlarge", "g5.xlarge", "g6.xlarge", "g4dn.xlarge"]
# Response body in the shape of the AWS EC2 API reference example for `DescribeSpotPriceHistory`
# (`InstanceType`, `ProductDescription`, `SpotPrice`, `Timestamp`, `AvailabilityZone`), with the
# `SpotPrice` and `Timestamp` values this project's own stored rows gave for `p5.4xlarge` at
# 2026-10-14T04:10:44Z (both zones effective, not new: no earlier test used stored values for
# this path).
# https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeSpotPriceHistory.html
PROVIDER_RESPONSE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "provider"
    / "aws"
    / "describe_spot_price_history_response.json"
)


def _parsed_provider_items():
    body = json.loads(PROVIDER_RESPONSE.read_text())
    items = []
    for item in body["SpotPriceHistory"]:
        item = dict(item)
        item["Timestamp"] = datetime.fromisoformat(item["Timestamp"].replace("Z", "+00:00"))
        items.append(item)
    return items


def record(instance_type, zone, price, at):
    return {
        "AvailabilityZone": zone,
        "AvailabilityZoneId": "use1-az1",
        "InstanceType": instance_type,
        "ProductDescription": "Linux/UNIX",
        "SpotPrice": price,
        "Timestamp": at,
    }


def one_region(**overrides):
    return section(regions=["us-east-1"], **overrides)


def run(pages, config=None, now=NOW):
    ec2 = FakeEc2("us-east-1", describe_spot_price_history=pages)
    rows = SpotPriceProbe().run(make_ctx(config or one_region(), {"us-east-1": ec2}, now=now))
    return rows, ec2


def test_request_has_all_request_gpu_types_linux_and_start_now_minus_cadence():
    _, ec2 = run([{"SpotPriceHistory": []}])

    assert len(ec2.calls) == 1
    name, kwargs = ec2.calls[0]
    assert name == "describe_spot_price_history"
    assert kwargs == {
        "StartTime": START,
        "EndTime": NOW,
        "Filters": [
            {"Name": "instance-type", "Values": GPU_TYPES},
            {"Name": "product-description", "Values": ["Linux/UNIX"]},
        ],
        "MaxResults": 1000,
    }


def test_start_time_follows_the_configured_cadence():
    config = one_region(cadence={"aws_spot_price": "15m"})
    _, ec2 = run([{"SpotPriceHistory": []}], config)
    assert ec2.calls[0][1]["StartTime"] == NOW - timedelta(minutes=15)


def test_one_call_per_region():
    east = FakeEc2("us-east-1", describe_spot_price_history=[{"SpotPriceHistory": []}])
    west = FakeEc2("us-west-2", describe_spot_price_history=[{"SpotPriceHistory": []}])
    SpotPriceProbe().run(make_ctx(section(), {"us-east-1": east, "us-west-2": west}))
    assert len(east.calls) == 1
    assert len(west.calls) == 1


def test_new_records_give_success_rows_and_old_ones_give_none_with_the_effective_price():
    history = [
        record("p5.48xlarge", "us-east-1a", "12.5000", START - timedelta(hours=3)),
        record("p5.48xlarge", "us-east-1a", "12.7500", START + timedelta(minutes=1)),
        record("p5.48xlarge", "us-east-1a", "12.8000", START + timedelta(minutes=3)),
        record("p5.48xlarge", "us-east-1b", "13.0000", START - timedelta(hours=1)),
        record("g5.xlarge", "us-east-1a", "0.4100", START),
    ]
    rows, _ = run([{"SpotPriceHistory": history}])

    by_key = [(r.sku, r.zone, r.outcome) for r in rows]
    assert by_key == [
        ("p5.48xlarge", "us-east-1a", "success"),
        ("p5.48xlarge", "us-east-1a", "success"),
        ("p5.48xlarge", "us-east-1b", "none"),
        ("g5.xlarge", "us-east-1a", "success"),
        ("g6.xlarge", None, "none"),
        ("g4dn.xlarge", None, "none"),
    ]
    assert rows[0].details == {
        "price": 12.75,
        "price_ts": "2026-10-11T12:03:30.000Z",
        "currency": "USD",
        "product_description": "Linux/UNIX",
    }
    assert rows[2].details == {
        "effective_price": 13.0,
        "effective_price_ts": "2026-10-11T11:02:30.000Z",
    }
    # A record exactly at the start time is new.
    assert rows[3].details["price"] == 0.41
    assert rows[4].details == {}
    assert all(r.validate() == [] for r in rows)
    assert all(r.region == "us-east-1" and r.probe_type == "aws_spot_price" for r in rows)


def test_empty_result_gives_1_none_row_per_type():
    rows, _ = run([{"SpotPriceHistory": []}])
    assert [(r.sku, r.zone, r.outcome) for r in rows] == [(t, None, "none") for t in GPU_TYPES]


def test_next_token_is_followed():
    pages = [
        {
            "SpotPriceHistory": [record("p5.48xlarge", "us-east-1a", "12.0", START)],
            "NextToken": "page-2",
        },
        {
            "SpotPriceHistory": [
                record("p5.48xlarge", "us-east-1b", "12.1", START + timedelta(minutes=2))
            ],
            "NextToken": "",
        },
    ]
    rows, ec2 = run(pages)

    assert len(ec2.calls) == 2
    assert ec2.calls[1][1]["NextToken"] == "page-2"
    success = [(r.zone, r.details["price"]) for r in rows if r.outcome == "success"]
    assert success == [("us-east-1a", 12.0), ("us-east-1b", 12.1)]


def test_an_api_error_gives_1_error_row_per_type():
    rows, _ = run([FakeClientError("RequestLimitExceeded")])
    assert [(r.sku, r.outcome, r.error_code) for r in rows] == [
        (t, "error", "RequestLimitExceeded") for t in GPU_TYPES
    ]


def test_first_poll_of_each_hour_also_writes_the_effective_price_as_a_success_row():
    on_the_hour = datetime(2026, 10, 11, 13, 1, 0, tzinfo=UTC)
    start = on_the_hour - timedelta(minutes=5)
    history = [record("p5.48xlarge", "us-east-1b", "13.0000", start - timedelta(hours=1))]
    rows, _ = run([{"SpotPriceHistory": history}], now=on_the_hour)

    p5 = [r for r in rows if r.sku == "p5.48xlarge"]
    assert [r.outcome for r in p5] == ["none", "success"]
    assert p5[1].details == {
        "price": 13.0,
        "price_ts": "2026-10-11T11:56:00.000Z",
        "currency": "USD",
        "product_description": "Linux/UNIX",
        "effective": True,
    }


def test_crosses_hour_is_true_for_1_poll_per_hour():
    polls = [
        datetime(2026, 10, 11, 12, 0, 30, tzinfo=UTC) + timedelta(minutes=5 * i) for i in range(24)
    ]
    hits = [now for now in polls if crosses_hour(now - timedelta(minutes=5), now)]
    assert [h.strftime("%H:%M") for h in hits] == ["12:00", "13:00"]
    exact = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    assert crosses_hour(exact - timedelta(minutes=5), exact) is True
    assert crosses_hour(exact, exact + timedelta(minutes=5)) is False


def test_the_probe_keeps_no_state_between_calls():
    probe = SpotPriceProbe()
    assert vars(probe) == {}


def test_the_provider_response_gives_none_rows_with_the_real_effective_price():
    """Runs the real probe against a reconstructed `DescribeSpotPriceHistory` response
    (PROVIDER_RESPONSE). Both zones' records are older than the poll window, so both give
    `none` rows that carry the real effective price (spec 0008 section 5.2)."""
    now = datetime(2026, 10, 14, 4, 10, 44, 528000, tzinfo=UTC)
    history = _parsed_provider_items()
    config = one_region(
        spot_score_requests=[
            {"name": "p5-1x", "instance_types": ["p5.4xlarge"], "label": "Spot score, 1x H100"}
        ]
    )
    rows, ec2 = run([{"SpotPriceHistory": history}], config, now=now)

    p5 = [r for r in rows if r.sku == "p5.4xlarge"]
    assert {(r.zone, r.outcome, r.error_code) for r in p5} == {
        ("us-east-1a", "none", None),
        ("us-east-1b", "none", None),
    }
    by_zone = {r.zone: r for r in p5}
    assert by_zone["us-east-1a"].details == {
        "effective_price": 3.9118,
        "effective_price_ts": "2026-10-14T04:00:00.000Z",
    }
    assert by_zone["us-east-1b"].details == {
        "effective_price": 4.6722,
        "effective_price_ts": "2026-10-13T20:00:00.000Z",
    }
    assert all(r.validate() == [] for r in rows)
    assert ec2.calls[0][1]["EndTime"] == now


def test_the_provider_response_gives_a_success_row_and_the_hourly_effective_row():
    """The same fixture, polled at 04:02: the poll crosses the 04:00 UTC hour. `us-east-1a`'s
    04:00 record is now inside the poll window, so it is new and
    gives a plain `success` row. `us-east-1b`'s record is still older than the window, so it
    gives a `none` row with `effective_price`, plus the hourly effective `success` row (spec
    0008 section 5.3)."""
    now = datetime(2026, 10, 14, 4, 2, 0, tzinfo=UTC)
    history = _parsed_provider_items()
    config = one_region(
        spot_score_requests=[
            {"name": "p5-1x", "instance_types": ["p5.4xlarge"], "label": "Spot score, 1x H100"}
        ]
    )
    rows, _ = run([{"SpotPriceHistory": history}], config, now=now)

    p5 = [r for r in rows if r.sku == "p5.4xlarge"]
    by_zone_outcome = [(r.zone, r.outcome) for r in p5]
    assert by_zone_outcome == [
        ("us-east-1a", "success"),
        ("us-east-1b", "none"),
        ("us-east-1b", "success"),
    ]
    a_success = p5[0]
    assert a_success.details == {
        "price": 3.9118,
        "price_ts": "2026-10-14T04:00:00.000Z",
        "currency": "USD",
        "product_description": "Linux/UNIX",
    }
    b_none, b_hourly = p5[1], p5[2]
    assert b_none.details == {
        "effective_price": 4.6722,
        "effective_price_ts": "2026-10-13T20:00:00.000Z",
    }
    assert b_hourly.details == {
        "price": 4.6722,
        "price_ts": "2026-10-13T20:00:00.000Z",
        "currency": "USD",
        "product_description": "Linux/UNIX",
        "effective": True,
    }
    assert all(r.validate() == [] for r in rows)
