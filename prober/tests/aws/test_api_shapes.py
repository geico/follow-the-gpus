"""Check the request parameters against the botocore EC2 model.

botocore is in the `aws` extra, not in the dev group. Thus `make prober/test` skips this
file. Run it with `uv run --extra aws pytest tests/aws`.
"""

import pytest

from ftg_prober.aws.cb_earliest import CapacityBlockEarliestProbe
from ftg_prober.aws.cb_longest import CapacityBlockLongestProbe
from ftg_prober.aws.cb_offerings import CapacityBlockOfferingsProbe
from ftg_prober.aws.spot_price import SpotPriceProbe
from ftg_prober.aws.spot_score import SpotPlacementScoreProbe
from tests.aws.fakes import FakeEc2, make_ctx, section
from tests.conftest import import_or_skip

botocore_session = import_or_skip("botocore.session")
botocore_validate = import_or_skip("botocore.validate")

OPERATIONS = {
    "describe_capacity_block_offerings": "DescribeCapacityBlockOfferings",
    "get_spot_placement_scores": "GetSpotPlacementScores",
    "describe_spot_price_history": "DescribeSpotPriceHistory",
}


@pytest.mark.parametrize(
    "probe",
    [
        CapacityBlockOfferingsProbe(),
        CapacityBlockEarliestProbe(),
        CapacityBlockLongestProbe(),
        SpotPlacementScoreProbe(),
        SpotPriceProbe(),
    ],
)
def test_each_request_is_valid_for_the_ec2_model(probe):
    model = botocore_session.get_session().get_service_model("ec2")
    clients = {region: FakeEc2(region) for region in section()["regions"]}
    probe.run(make_ctx(section(), clients))
    calls = [call for fake in clients.values() for call in fake.calls]
    assert calls
    for name, kwargs in calls:
        shape = model.operation_model(OPERATIONS[name]).input_shape
        botocore_validate.validate_parameters(kwargs, shape)


def test_a_renamed_odcr_fake_field_fails_the_shape_check():
    """The autouse fixture in `conftest.py` checks each ODCR fake in `test_odcr.py`. Prove it
    fails on a wrong field name, in a response and in a request."""
    from tests.aws.conftest import check_call

    model = botocore_session.get_session().get_service_model("ec2")
    validate = botocore_validate.validate_parameters
    good = {"CapacityReservation": {"CapacityReservationId": "cr-1", "TotalInstanceCount": 1}}
    check_call(model, validate, "create_capacity_reservation", _create_kwargs(), good)
    bad = {"CapacityReservation": {"CapacityReservationID": "cr-1", "TotalInstanceCount": 1}}
    with pytest.raises(AssertionError, match="CapacityReservationID"):
        check_call(model, validate, "create_capacity_reservation", _create_kwargs(), bad)
    with pytest.raises(botocore_validate.ParamValidationError):
        check_call(model, validate, "cancel_capacity_reservation", {"ReservationId": "cr-1"}, {})


def _create_kwargs():
    return {
        "InstanceType": "m5.large",
        "InstancePlatform": "Linux/UNIX",
        "AvailabilityZone": "us-east-1a",
        "InstanceCount": 1,
    }


def test_create_capacity_reservation_takes_every_count_of_the_upward_search():
    """ADR 0023: the steps ask for 2, 4, 8, and 16 instances. The botocore model accepts each
    count, and it states no maximum below the largest step."""
    model = botocore_session.get_session().get_service_model("ec2")
    shape = model.operation_model("CreateCapacityReservation").input_shape
    assert shape.members["InstanceCount"].type_name == "integer"
    assert shape.members["InstanceCount"].metadata.get("max", 16) >= 16
    for count in (2, 4, 8, 16):
        botocore_validate.validate_parameters({**_create_kwargs(), "InstanceCount": count}, shape)
