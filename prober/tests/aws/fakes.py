"""Hand-written fakes for the AWS probe tests. No network, no credentials."""

import copy
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ftg_prober.probes import Clients, ProbeContext
from tests.conftest import MemoryStore

NOW = datetime(2026, 10, 11, 12, 7, 30, tzinfo=UTC)
FIXTURES = Path(__file__).parents[1] / "fixtures" / "provider" / "aws"
_TIMESTAMP_FIELDS = ("StartDate", "EndDate", "Timestamp")


def provider_response(name: str) -> dict[str, Any]:
    """A provider response fixture of `tests/fixtures/provider/aws/<name>`, with its
    `StartDate`/`EndDate`/`Timestamp` strings parsed to `datetime`, as botocore gives them."""
    body = json.loads((FIXTURES / name).read_text())
    for items in body.values():
        if not isinstance(items, list):
            continue
        for item in items:
            for field in _TIMESTAMP_FIELDS:
                if isinstance(item.get(field), str):
                    item[field] = datetime.fromisoformat(item[field].replace("Z", "+00:00"))
    return body


class FakeClientError(Exception):
    """Has the `response` shape of a botocore `ClientError`."""

    def __init__(self, code: str, operation: str = "Operation", message: str = "fake") -> None:
        super().__init__(f"An error occurred ({code}) when calling the {operation} operation")
        self.response = {
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        }


class FakeEc2:
    """A fake EC2 client. Each operation takes a queue of responses or exceptions.

    `calls` records each call as `(operation, kwargs)`.
    """

    OPERATIONS = (
        "describe_capacity_block_offerings",
        "get_spot_placement_scores",
        "describe_spot_price_history",
        "describe_instance_type_offerings",
        "describe_availability_zones",
        "describe_instance_types",
        "create_capacity_reservation",
        "describe_capacity_reservations",
        "cancel_capacity_reservation",
    )

    def __init__(self, region: str, **queues: list[Any] | Callable[..., Any]) -> None:
        self.region = region
        self.queues = queues
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name not in self.OPERATIONS:
            raise AttributeError(name)

        def operation(**kwargs: Any) -> Any:
            self.calls.append((name, copy.deepcopy(kwargs)))
            queue = self.queues.get(name)
            if callable(queue):
                answer = queue(**kwargs)
            elif queue:
                answer = queue.pop(0)
            else:
                answer = {}
            if isinstance(answer, BaseException):
                raise answer
            return answer

        return operation


def make_ctx(
    section: dict[str, Any],
    clients: dict[str, FakeEc2] | None = None,
    now: datetime = NOW,
    store: MemoryStore | None = None,
    simulate: bool = False,
) -> ProbeContext:
    """A context whose boto3 factory returns the fake client of each Region."""
    clients = clients or {}

    def factory(service: str, region: str) -> FakeEc2:
        assert service == "ec2"
        return clients[region]

    return ProbeContext(
        config=section,
        provider="aws",
        run_id=str(uuid.UUID(int=1, version=4)),
        experiment="ftg",
        store=store or MemoryStore(),
        clients=Clients(boto3_factory=factory),
        now_fn=lambda: now,
        simulate=simulate,
    )


def section(**overrides: Any) -> dict[str, Any]:
    """A small `aws` config section. Keys follow spec 0003 section 3."""
    base: dict[str, Any] = {
        "regions": ["us-east-1", "us-west-2"],
        "spot_score_requests": [
            {"name": "p5", "instance_types": ["p5.48xlarge"], "label": "8x H100"},
            {"name": "g5", "instance_types": ["g5.xlarge"], "label": "1x A10G"},
            {"name": "g6", "instance_types": ["g6.xlarge"], "label": "1x L4"},
            {
                "name": "control-trio",
                "instance_types": ["g5.xlarge", "g4dn.xlarge", "g6.xlarge"],
                "label": "Control: trio",
            },
            {"name": "control-cpu", "instance_types": ["m5.4xlarge"], "label": "Control: CPU"},
        ],
        "capacity_blocks": {
            "instance_types": ["p5.48xlarge"],
            "instance_count": 1,
            "duration_hours": [24],
            "start_window_days": 14,
        },
        "cadence": {"aws_spot_score": "5m", "aws_spot_price": "5m"},
    }
    base.update(overrides)
    return base
