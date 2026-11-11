"""Check each fake `aws_odcr` request and response against the botocore EC2 model.

A fake must match the real thing. The ODCR probe has made no
real call, so the fakes are the only evidence. botocore is in the `aws` extra; without it the
check is off (the same rule as `test_api_shapes.py`).
"""

from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest

from tests.aws.fakes import FakeEc2
from tests.conftest import require_cloud_extras

ODCR_OPERATIONS = {
    "describe_instance_type_offerings": "DescribeInstanceTypeOfferings",
    "describe_availability_zones": "DescribeAvailabilityZones",
    "create_capacity_reservation": "CreateCapacityReservation",
    "describe_capacity_reservations": "DescribeCapacityReservations",
    "cancel_capacity_reservation": "CancelCapacityReservation",
}

SCALARS = {
    "string": str,
    "integer": int,
    "long": int,
    "boolean": bool,
    "float": (int, float),
    "double": (int, float),
    "timestamp": datetime,
}


def check_response(value: Any, shape: Any, path: str = "response") -> None:
    """Fail on a key or a type that the botocore output shape does not have."""
    kind = shape.type_name
    if kind == "structure":
        assert isinstance(value, dict), f"{path}: expected a dict, got {type(value).__name__}"
        for key, item in value.items():
            assert key in shape.members, f"{path}: the real API has no field {key!r}"
            check_response(item, shape.members[key], f"{path}.{key}")
    elif kind == "list":
        assert isinstance(value, list), f"{path}: expected a list, got {type(value).__name__}"
        for i, item in enumerate(value):
            check_response(item, shape.member, f"{path}[{i}]")
    elif kind == "map":
        assert isinstance(value, dict), f"{path}: expected a dict, got {type(value).__name__}"
        for key, item in value.items():
            check_response(item, shape.value, f"{path}[{key!r}]")
    else:
        expected = SCALARS[kind]
        assert isinstance(value, expected), f"{path}: expected {kind}, got {value!r}"


def check_call(model: Any, validate: Any, operation: str, kwargs: dict[str, Any], answer: Any):
    op = model.operation_model(ODCR_OPERATIONS[operation])
    validate(kwargs, op.input_shape)
    check_response(answer, op.output_shape, f"{operation} response")


@pytest.fixture(autouse=True)
def odcr_fakes_match_botocore(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    try:
        from botocore.session import get_session
        from botocore.validate import validate_parameters
    except ImportError:  # no `aws` extra: skip only the shape check, not the tests
        if require_cloud_extras():  # the flag says the extra is installed: a broken import fails
            raise
        yield
        return
    model = get_session().get_service_model("ec2")
    original = FakeEc2.__getattr__
    # The probe catches every `Exception`, a shape failure included. Record each failure
    # here and assert at teardown, so a swallowed failure still fails the test.
    failures: list[str] = []

    def getattr_checked(self: FakeEc2, name: str) -> Any:
        operation = original(self, name)
        if name not in ODCR_OPERATIONS:
            return operation

        def checked(**kwargs: Any) -> Any:
            # An exception propagates; only a returned response is checked.
            answer = operation(**kwargs)
            try:
                check_call(model, validate_parameters, name, kwargs, answer)
            except Exception as exc:
                failures.append(f"{name}: {exc}")
                raise
            return answer

        return checked

    monkeypatch.setattr(FakeEc2, "__getattr__", getattr_checked)
    yield
    assert not failures, "the fakes do not match the botocore EC2 model:\n" + "\n".join(failures)
