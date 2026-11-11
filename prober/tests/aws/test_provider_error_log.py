"""A provider error gives a row with no message and 1 log record with the message as returned.

Logs are logs, metrics are metrics. Spec 0003 section 8, spec 0008.
"""

import logging

from ftg_prober.aws.cb_earliest import CapacityBlockEarliestProbe
from ftg_prober.aws.odcr import OdcrProbe
from ftg_prober.aws.spot_advisor import SpotAdvisorProbe
from ftg_prober.aws.spot_price import SpotPriceProbe
from ftg_prober.aws.spot_score import SpotPlacementScoreProbe
from tests.aws.fakes import FakeClientError, FakeEc2, make_ctx, section
from tests.aws.test_odcr import ONE_ZONE, SETTINGS, FakeClock, entry
from tests.aws.test_spot_advisor import FakeHttp, FakeResponse

# A message with spaces, a quote, and a colon, as AWS returns it.
MESSAGE = "You can't do that: this role isn't allowed"


def provider_records(caplog):
    return [r for r in caplog.records if r.getMessage().startswith("provider.error")]


def assert_logged_once(caplog, rows, probe_type, region, code):
    for row in rows:
        assert "message" not in str(row.details).lower()
        assert MESSAGE not in repr(row)
    (record,) = provider_records(caplog)
    text = record.getMessage()
    assert f"probe_type={probe_type}" in text
    assert f"region={region}" in text
    assert f"code={code}" in text
    assert repr(MESSAGE) in text or MESSAGE in text


def test_spot_price_error_logs_the_message_and_the_row_has_none(caplog):
    ec2 = FakeEc2(
        "us-east-1",
        describe_spot_price_history=[FakeClientError("UnauthorizedOperation", message=MESSAGE)],
    )
    with caplog.at_level(logging.WARNING):
        rows = SpotPriceProbe().run(make_ctx(section(regions=["us-east-1"]), {"us-east-1": ec2}))
    assert {r.outcome for r in rows} == {"error"}
    assert_logged_once(caplog, rows, "aws_spot_price", "us-east-1", "UnauthorizedOperation")
    assert "status=400" in provider_records(caplog)[0].getMessage()


def test_spot_score_error_logs_the_message_and_the_row_has_none(caplog):
    ec2 = FakeEc2(
        "us-east-1",
        get_spot_placement_scores=lambda **_: (_ for _ in ()).throw(
            FakeClientError("UnauthorizedOperation", message=MESSAGE)
        ),
    )
    cfg = section(
        regions=["us-east-1"],
        spot_score_requests=[{"name": "g5", "instance_types": ["g5.xlarge"], "label": "A10G"}],
    )
    with caplog.at_level(logging.WARNING):
        rows = SpotPlacementScoreProbe().run(make_ctx(cfg, {"us-east-1": ec2}))
    assert_logged_once(caplog, rows, "aws_spot_score", "us-east-1", "UnauthorizedOperation")


def test_cb_earliest_error_logs_the_message_and_the_row_has_none(caplog):
    ec2 = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[
            FakeClientError("InvalidParameterValue", message=MESSAGE)
        ],
    )
    with caplog.at_level(logging.WARNING):
        rows = CapacityBlockEarliestProbe().run(
            make_ctx(section(regions=["us-east-1"]), {"us-east-1": ec2})
        )
    assert_logged_once(caplog, rows, "aws_cb_earliest", "us-east-1", "InvalidParameterValue")


def test_spot_advisor_http_error_logs_the_body_and_the_row_has_none(caplog):
    http = FakeHttp(FakeResponse(503, MESSAGE.encode()))
    with caplog.at_level(logging.WARNING):
        rows = SpotAdvisorProbe(http=http).run(make_ctx(section(regions=["us-east-1"])))
    assert {r.error_code for r in rows} == {"HTTP503"}
    assert_logged_once(caplog, rows, "aws_spot_advisor", "us-east-1", "HTTP503")


def test_spot_advisor_failed_fetch_logs_the_message_and_the_row_has_none(caplog):
    http = FakeHttp(OSError(MESSAGE))
    with caplog.at_level(logging.WARNING):
        rows = SpotAdvisorProbe(http=http).run(make_ctx(section(regions=["us-east-1"])))
    assert {r.error_code for r in rows} == {"OSError"}
    assert_logged_once(caplog, rows, "aws_spot_advisor", "us-east-1", "OSError")


def test_odcr_create_error_logs_the_message_and_the_row_has_none(caplog):
    ec2 = FakeEc2(
        "us-east-1",
        **ONE_ZONE,
        create_capacity_reservation=[FakeClientError("RequestLimitExceeded", message=MESSAGE)],
    )
    section_ = {"provisioning_probes": [entry()], **SETTINGS}
    probe = OdcrProbe()
    probe._client_for = lambda _ctx, _target, _mode: (ec2, FakeClock())  # noqa: SLF001
    probe._quota_client_for = lambda _ctx, _target, _mode: None  # noqa: SLF001
    with caplog.at_level(logging.WARNING):
        rows = probe.run(make_ctx(section_, clients={"us-east-1": ec2}))
    assert [r.outcome for r in rows] == ["error"]
    assert_logged_once(caplog, rows, "aws_odcr", "us-east-1", "RequestLimitExceeded")


def test_odcr_precheck_error_logs_the_message_and_the_row_has_none(caplog):
    ec2 = FakeEc2(
        "us-east-1",
        describe_instance_type_offerings=[
            FakeClientError("UnauthorizedOperation", message=MESSAGE)
        ],
    )
    section_ = {"provisioning_probes": [entry()], **SETTINGS}
    probe = OdcrProbe()
    probe._client_for = lambda _ctx, _target, _mode: (ec2, FakeClock())  # noqa: SLF001
    probe._quota_client_for = lambda _ctx, _target, _mode: None  # noqa: SLF001
    with caplog.at_level(logging.WARNING):
        rows = probe.run(make_ctx(section_, clients={"us-east-1": ec2}))
    assert [r.outcome for r in rows] == ["error"]
    assert_logged_once(caplog, rows, "aws_odcr", "us-east-1", "UnauthorizedOperation")


def test_odcr_teardown_logs_a_failed_cancel_and_a_failed_describe(caplog):
    from ftg_prober.aws.odcr import Settings, teardown

    def describe(**kwargs):
        raise FakeClientError("DescribeDenied", message=MESSAGE)

    def cancel(**kwargs):
        raise FakeClientError("CancelDenied", message=MESSAGE)

    ec2 = FakeEc2(
        "us-east-1", describe_capacity_reservations=describe, cancel_capacity_reservation=cancel
    )
    settings = Settings(
        probe_timeout_seconds=20, poll_interval_seconds=5, teardown_timeout_seconds=10
    )
    with caplog.at_level(logging.WARNING):
        result = teardown(ec2, FakeClock(), "cr-x", settings)
    assert result["verified"] is False
    texts = [r.getMessage() for r in provider_records(caplog)]
    for call, code in (
        ("cancel_capacity_reservation", "CancelDenied"),
        ("describe_capacity_reservations", "DescribeDenied"),
    ):
        (line, *_) = [t for t in texts if f"call={call}" in t]
        assert f"code={code}" in line
        assert repr(MESSAGE) in line or MESSAGE in line
