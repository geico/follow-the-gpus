"""The Lambda entry point, end to end with stubbed EC2 and S3 clients (spec 0013 sections 3, 9)."""

import json
import logging
from pathlib import Path

import pytest

from sweeper.run import SweepError
from tests.fakes import (
    EXPERIMENT,
    describe_params,
    put_object_ok,
    reservation,
    rid,
    stubbed_ec2,
    stubbed_s3,
)

ROOT = Path(__file__).resolve().parent.parent
ENV = {
    "FTG_EXPERIMENT": EXPERIMENT,
    "FTG_REGIONS": "us-east-1",
    "FTG_DATA_BUCKET": "ftg-data-0123abcd",
    "AWS_LAMBDA_FUNCTION_NAME": "ftg-sweeper",
}


@pytest.fixture(autouse=True)
def _fresh_handler(monkeypatch):
    """`handler._log_handler` is a cold-start cache, and the root logger is global."""
    import handler

    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    monkeypatch.setattr(handler, "_log_handler", None)
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    yield
    root.handlers[:] = saved_handlers


def test_the_zip_root_has_the_handler_and_the_package() -> None:
    for name in ("handler.py", "sweeper/__init__.py"):
        assert (ROOT / name).is_file(), name


def test_the_sweeper_logger_passes_info_and_propagates() -> None:
    import handler

    assert handler.log.name == "sweeper"
    assert handler.log.level == 20
    assert handler.log.propagate is True


def test_the_log_lines_of_a_run_reach_the_store(monkeypatch) -> None:
    """A real run: the decision line and the health line land in 1 object under logs/."""
    import handler

    ec2, ec2_stub = stubbed_ec2()
    ec2_stub.add_response(
        "describe_capacity_reservations",
        {"CapacityReservations": [reservation(1, age_seconds=400)]},
        describe_params(),
    )
    ec2_stub.add_response(
        "cancel_capacity_reservation", {"Return": True}, {"CapacityReservationId": rid(1)}
    )
    s3, s3_stub, puts = stubbed_s3()
    put_object_ok(s3_stub)
    made = []

    def client(service, region=None):
        made.append((service, region))
        return {"ec2": ec2, "s3": s3}[service]

    monkeypatch.setattr(handler, "_client", client)
    with ec2_stub, s3_stub:
        out = handler.lambda_handler({}, None)
        ec2_stub.assert_no_pending_responses()
        s3_stub.assert_no_pending_responses()
    assert out == {"scanned": 1, "cancelled": 1, "errors": 0}
    assert made == [("s3", None), ("ec2", "us-east-1")]
    (put,) = puts
    assert put["Bucket"] == "ftg-data-0123abcd"
    assert put["Key"].startswith(f"experiment={EXPERIMENT}/logs/provider=aws/date=")
    assert "_ftg-sweeper_" in put["Key"]
    bodies = [json.loads(line)["Body"] for line in put["Body"].decode().splitlines()]
    # The handler reads the real clock, so the age is not fixed here; test_run.py pins it.
    assert bodies[0].startswith("sweeper decision: action=cancelled age_s=")
    assert f"region=us-east-1 state=active id={rid(1)} reason=older than 180 s" in bodies[0]
    assert bodies[-1] == "sweeper run: scanned 1, cancelled 1, errors 0"


def test_a_failed_region_still_flushes_the_log_and_fails_the_invocation(monkeypatch) -> None:
    import handler

    ec2, ec2_stub = stubbed_ec2()
    ec2_stub.add_client_error(
        "describe_capacity_reservations",
        service_error_code="UnauthorizedOperation",
        http_status_code=403,
    )
    s3, s3_stub, puts = stubbed_s3()
    put_object_ok(s3_stub)
    monkeypatch.setattr(
        handler, "_client", lambda service, region=None: {"ec2": ec2, "s3": s3}[service]
    )
    with ec2_stub, s3_stub, pytest.raises(SweepError):
        handler.lambda_handler({}, None)
    (put,) = puts
    assert b"sweeper run: scanned 0, cancelled 0, errors 1" in put["Body"]


def test_a_failed_cancel_still_flushes_the_log_and_fails_the_invocation(monkeypatch) -> None:
    import handler

    ec2, ec2_stub = stubbed_ec2()
    ec2_stub.add_response(
        "describe_capacity_reservations",
        {"CapacityReservations": [reservation(7, age_seconds=400)]},
        describe_params(),
    )
    ec2_stub.add_client_error(
        "cancel_capacity_reservation",
        service_error_code="UnauthorizedOperation",
        http_status_code=403,
        expected_params={"CapacityReservationId": rid(7)},
    )
    s3, s3_stub, puts = stubbed_s3()
    put_object_ok(s3_stub)
    monkeypatch.setattr(
        handler, "_client", lambda service, region=None: {"ec2": ec2, "s3": s3}[service]
    )
    with ec2_stub, s3_stub, pytest.raises(SweepError):
        handler.lambda_handler({}, None)
    (put,) = puts
    assert b"sweeper run: scanned 1, cancelled 0, errors 1" in put["Body"]


def test_a_failing_store_client_does_not_stop_the_sweep(monkeypatch, capsys) -> None:
    import handler

    ec2, ec2_stub = stubbed_ec2()
    ec2_stub.add_response(
        "describe_capacity_reservations", {"CapacityReservations": []}, describe_params()
    )

    def client(service, region=None):
        if service == "s3":
            raise RuntimeError("no store")
        return ec2

    monkeypatch.setattr(handler, "_client", client)
    with ec2_stub:
        out = handler.lambda_handler({}, None)
    assert out == {"scanned": 0, "cancelled": 0, "errors": 0}
    assert handler._log_handler is False
    assert "store log handler not attached" in capsys.readouterr().out


class _QuietClient:
    """An EC2 and S3 stand-in: no reservations, and any put succeeds."""

    def describe_capacity_reservations(self, **kwargs):
        return {"CapacityReservations": []}

    def put_object(self, **kwargs):
        return {}


def _store_handlers():
    from sweeper.log_store import S3LogHandler

    return [h for h in logging.getLogger().handlers if isinstance(h, S3LogHandler)]


def test_a_warm_container_keeps_one_store_log_handler(monkeypatch) -> None:
    import handler

    monkeypatch.setattr(handler, "_client", lambda service, region=None: _QuietClient())
    handler.lambda_handler({}, None)
    handler.lambda_handler({}, None)
    assert len(_store_handlers()) == 1


def test_the_attached_store_log_handler_has_no_filter(monkeypatch) -> None:
    import handler

    monkeypatch.setattr(handler, "_client", lambda service, region=None: _QuietClient())
    handler.lambda_handler({}, None)
    (attached,) = _store_handlers()
    assert attached.filters == []
