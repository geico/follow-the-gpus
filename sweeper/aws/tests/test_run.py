"""1 sweeper run against real EC2 clients behind a Stubber (spec 0013 sections 5 to 7)."""

import logging

import pytest

from sweeper.run import SweepError, sweep
from sweeper.settings import Settings
from tests.fakes import EXPERIMENT, NOW, describe_params, reservation, rid, stubbed_ec2

SETTINGS = Settings(
    experiment=EXPERIMENT, regions=("us-east-1",), data_bucket="bucket", max_age_seconds=180
)
LOGGER = logging.getLogger("sweeper")


def run(clients: dict, settings: Settings = SETTINGS):
    return sweep(settings, lambda region: clients[region], now=lambda: NOW, logger=LOGGER)


def decision_lines(caplog) -> list[str]:
    return [m for m in caplog.messages if m.startswith("sweeper decision:")]


def test_old_tagged_reservation_is_cancelled(caplog) -> None:
    """The cancel logic itself: without the CancelCapacityReservation call this test fails."""
    caplog.set_level(logging.INFO, logger="sweeper")
    ec2, stub = stubbed_ec2()
    stub.add_response(
        "describe_capacity_reservations",
        {"CapacityReservations": [reservation(1, age_seconds=181)]},
        describe_params(),
    )
    stub.add_response(
        "cancel_capacity_reservation", {"Return": True}, {"CapacityReservationId": rid(1)}
    )
    with stub:
        result = run({"us-east-1": ec2})
        stub.assert_no_pending_responses()
    assert (result.scanned, result.cancelled, result.errors) == (1, 1, 0)
    assert result.actions == {rid(1): "cancelled"}
    (line,) = decision_lines(caplog)
    assert line == (
        f"sweeper decision: action=cancelled age_s=181 region=us-east-1 state=active "
        f"id={rid(1)} reason=older than 180 s"
    )


@pytest.mark.parametrize("age", [0, 60, 180])
def test_young_reservation_stays(caplog, age: int) -> None:
    """At the threshold is young: the comparison is strict (decision S5)."""
    caplog.set_level(logging.INFO, logger="sweeper")
    ec2, stub = stubbed_ec2()
    stub.add_response(
        "describe_capacity_reservations",
        {"CapacityReservations": [reservation(2, age_seconds=age)]},
        describe_params(),
    )
    with stub:
        result = run({"us-east-1": ec2})
        stub.assert_no_pending_responses()
    assert (result.cancelled, result.errors) == (0, 0)
    assert result.actions == {rid(2): "keep"}
    (line,) = decision_lines(caplog)
    assert "action=keep" in line and "reason=young" in line


@pytest.mark.parametrize("experiment", [None, "ftg-20261001-other"])
def test_untagged_or_other_experiment_is_never_cancelled(experiment) -> None:
    """The query filters on the tag; the rule checks it again (section 6, check 1)."""
    ec2, stub = stubbed_ec2()
    stub.add_response(
        "describe_capacity_reservations",
        {"CapacityReservations": [reservation(3, age_seconds=9999, experiment=experiment)]},
        describe_params(),
    )
    with stub:
        result = run({"us-east-1": ec2})
        stub.assert_no_pending_responses()
    assert result.actions == {rid(3): "skip"}
    assert (result.cancelled, result.errors) == (0, 0)


def test_old_pending_reservation_stays_and_is_logged(caplog) -> None:
    """`pending` cannot be cancelled; the EndDate backstop covers it (section 6, check 4)."""
    caplog.set_level(logging.INFO, logger="sweeper")
    ec2, stub = stubbed_ec2()
    stub.add_response(
        "describe_capacity_reservations",
        {"CapacityReservations": [reservation(4, age_seconds=600, state="pending")]},
        describe_params(),
    )
    with stub:
        result = run({"us-east-1": ec2})
        stub.assert_no_pending_responses()
    assert result.actions == {rid(4): "keep"}
    (line,) = decision_lines(caplog)
    assert "reason=state pending cannot be cancelled" in line


def test_every_page_is_read() -> None:
    ec2, stub = stubbed_ec2()
    stub.add_response(
        "describe_capacity_reservations",
        {"CapacityReservations": [reservation(5, age_seconds=10)], "NextToken": "page-2"},
        describe_params(),
    )
    stub.add_response(
        "describe_capacity_reservations",
        {"CapacityReservations": [reservation(6, age_seconds=400)]},
        describe_params(token="page-2"),
    )
    stub.add_response(
        "cancel_capacity_reservation", {"Return": True}, {"CapacityReservationId": rid(6)}
    )
    with stub:
        result = run({"us-east-1": ec2})
        stub.assert_no_pending_responses()
    assert result.actions == {rid(5): "keep", rid(6): "cancelled"}


def test_a_failed_cancel_logs_the_provider_error_as_returned(caplog) -> None:
    caplog.set_level(logging.INFO, logger="sweeper")
    ec2, stub = stubbed_ec2()
    stub.add_response(
        "describe_capacity_reservations",
        {"CapacityReservations": [reservation(7, age_seconds=400)]},
        describe_params(),
    )
    stub.add_client_error(
        "cancel_capacity_reservation",
        service_error_code="UnauthorizedOperation",
        service_message="You are not authorized to perform this operation.",
        http_status_code=403,
        expected_params={"CapacityReservationId": rid(7)},
    )
    with stub, pytest.raises(SweepError, match=rid(7)):
        run({"us-east-1": ec2})
    stub.assert_no_pending_responses()
    assert "sweeper run: scanned 1, cancelled 0, errors 1" in caplog.messages
    (line,) = decision_lines(caplog)
    assert "action=error" in line
    assert (
        "reason=cancel failed: An error occurred (UnauthorizedOperation) when calling the "
        "CancelCapacityReservation operation: You are not authorized to perform this operation."
    ) in line


def test_a_failed_cancel_does_not_stop_the_other_cancels_and_fails_the_run(caplog) -> None:
    caplog.set_level(logging.INFO, logger="sweeper")
    ec2, stub = stubbed_ec2()
    stub.add_response(
        "describe_capacity_reservations",
        {
            "CapacityReservations": [
                reservation(7, age_seconds=400),
                reservation(8, age_seconds=400),
            ]
        },
        describe_params(),
    )
    stub.add_client_error(
        "cancel_capacity_reservation",
        service_error_code="UnauthorizedOperation",
        http_status_code=403,
        expected_params={"CapacityReservationId": rid(7)},
    )
    stub.add_response(
        "cancel_capacity_reservation", {"Return": True}, {"CapacityReservationId": rid(8)}
    )
    with stub, pytest.raises(SweepError):
        run({"us-east-1": ec2})
    stub.assert_no_pending_responses()
    assert "sweeper run: scanned 2, cancelled 1, errors 1" in caplog.messages


def test_a_run_with_nothing_to_cancel_succeeds() -> None:
    ec2, stub = stubbed_ec2()
    stub.add_response(
        "describe_capacity_reservations",
        {"CapacityReservations": [reservation(5, age_seconds=10)]},
        describe_params(),
    )
    with stub:
        result = run({"us-east-1": ec2})
    assert (result.cancelled, result.errors) == (0, 0)


def test_each_region_is_swept_and_a_failed_region_does_not_stop_the_others(caplog) -> None:
    caplog.set_level(logging.INFO, logger="sweeper")
    east, east_stub = stubbed_ec2("us-east-1")
    west, west_stub = stubbed_ec2("us-west-2")
    east_stub.add_client_error(
        "describe_capacity_reservations",
        service_error_code="RequestLimitExceeded",
        service_message="Request limit exceeded.",
        http_status_code=503,
        expected_params=describe_params(),
    )
    west_stub.add_response(
        "describe_capacity_reservations",
        {"CapacityReservations": [reservation(8, age_seconds=400, region="us-west-2")]},
        describe_params(),
    )
    west_stub.add_response(
        "cancel_capacity_reservation", {"Return": True}, {"CapacityReservationId": rid(8)}
    )
    settings = Settings(
        experiment=EXPERIMENT, regions=("us-east-1", "us-west-2"), data_bucket="bucket"
    )
    with east_stub, west_stub, pytest.raises(SweepError, match="us-east-1"):
        run({"us-east-1": east, "us-west-2": west}, settings)
    east_stub.assert_no_pending_responses()
    west_stub.assert_no_pending_responses()
    assert "sweeper run: scanned 1, cancelled 1, errors 1" in caplog.messages
    assert any(
        m.startswith(
            "sweeper query failed: region=us-east-1 error=An error occurred (RequestLimitExceeded)"
        )
        for m in caplog.messages
    )


def test_the_health_line_ends_every_run(caplog) -> None:
    caplog.set_level(logging.INFO, logger="sweeper")
    ec2, stub = stubbed_ec2()
    stub.add_response(
        "describe_capacity_reservations", {"CapacityReservations": []}, describe_params()
    )
    with stub:
        run({"us-east-1": ec2})
    assert caplog.messages[-1] == "sweeper run: scanned 0, cancelled 0, errors 0"
