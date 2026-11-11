"""Check the Service Quotas and EC2 calls of `aws_odcr` against the botocore service models.

botocore is in the `aws` extra. Run with `uv run --extra aws pytest tests/aws`.
"""

from typing import Any

from ftg_prober.aws.odcr import QUOTA_CODE_G_VT, QUOTA_CODE_P, QUOTA_CODE_STANDARD, quota_ceiling
from tests.aws.test_odcr import FakeServiceQuotas, entry
from tests.conftest import import_or_skip

botocore_session = import_or_skip("botocore.session")
botocore_validate = import_or_skip("botocore.validate")


class Recorder:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_service_quota(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("GetServiceQuota", kwargs))
        return FakeServiceQuotas(self.response["v"]).get_service_quota(**kwargs)

    def describe_instance_types(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("DescribeInstanceTypes", kwargs))
        return {"InstanceTypes": [{"VCpuInfo": {"DefaultVCpus": 4}}]}


def _validate(service: str, operation: str, kwargs: dict[str, Any], output: bool = False) -> None:
    model = botocore_session.get_session().get_service_model(service)
    op = model.operation_model(operation)
    shape = op.output_shape if output else op.input_shape
    botocore_validate.validate_parameters(kwargs, shape)


def test_requests_are_valid_for_the_service_models():
    fake = Recorder({"v": 16.0})
    ceiling, _ = quota_ceiling(fake, fake, entry())
    assert ceiling == 4
    by_name = dict(fake.calls)
    _validate("service-quotas", "GetServiceQuota", by_name["GetServiceQuota"])
    _validate("ec2", "DescribeInstanceTypes", by_name["DescribeInstanceTypes"])


def test_the_fake_responses_are_valid_for_the_service_models():
    response = FakeServiceQuotas(16.0).get_service_quota(ServiceCode="ec2", QuotaCode="L-1")
    _validate("service-quotas", "GetServiceQuota", response, output=True)
    _validate(
        "ec2",
        "DescribeInstanceTypes",
        {"InstanceTypes": [{"VCpuInfo": {"DefaultVCpus": 4}}]},
        output=True,
    )


def test_quota_codes_are_quota_code_shaped():
    for code in (QUOTA_CODE_STANDARD, QUOTA_CODE_G_VT, QUOTA_CODE_P):
        assert code.startswith("L-") and len(code) == 10
