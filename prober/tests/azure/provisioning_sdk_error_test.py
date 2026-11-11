"""A connection reset in a teardown call must not end the teardown early. Spec 0010, 3 rule 2.

`ServiceRequestError` is the real `azure.core` class. It has no `status_code` and no `error`.
The first test sends a real SDK client through a transport that raises it. The others make the
fake adapter fail the named call through the real `_sdk_call`, so the conversion is the code under
test.
"""

from typing import Any

import pytest
from azure.core.exceptions import (
    DeserializationError,
    ServiceRequestError,
    ServiceResponseError,
)
from azure.core.pipeline.transport import HttpTransport
from azure.mgmt.compute import ComputeManagementClient

from ftg_prober.azure.capacity_reservation import CapacityReservationProbe
from ftg_prober.azure.provisioning_adapter import (
    AzureAllocationAdapter,
    AzureCallError,
    _sdk_call,
)
from ftg_prober.azure.vmss_flex import VmssFlexProbe
from tests.azure.provisioning_fakes import (
    FakeAdapter,
    FakeClock,
    PlannedMember,
    b2s,
    entry,
    make_ctx,
    wire,
)

RESET = "Connection aborted: ConnectionResetError(104, 'Connection reset by peer')"


class ResetTransport(HttpTransport):
    """Every request raises the error that a connection reset gives."""

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __exit__(self, *args: Any) -> None:
        pass

    def send(self, request: Any, **kwargs: Any) -> Any:
        raise ServiceRequestError(RESET)


class Credential:
    def get_token(self, *scopes: Any, **kwargs: Any) -> Any:
        from azure.core.credentials import AccessToken

        return AccessToken("x", 4_000_000_000)


def test_real_client_connection_reset_becomes_azure_call_error():
    compute = ComputeManagementClient(
        Credential(), "sub", transport=ResetTransport(), retry_total=0
    )
    with pytest.raises(AzureCallError) as caught:
        AzureAllocationAdapter(compute, lambda: None).list_members("rg", "/ss/1")
    assert caught.value.code == "ServiceRequestError"
    assert RESET in caught.value.message


@pytest.mark.parametrize("error_class", [ServiceRequestError, ServiceResponseError])
def test_sdk_call_converts_the_errors_without_status_or_error(error_class):
    def boom():
        raise error_class("timed out")

    with pytest.raises(AzureCallError) as caught:
        _sdk_call(boom)
    assert (caught.value.code, caught.value.message) == (error_class.__name__, "timed out")


class FlakyAdapter:
    """The fake adapter, but the named call raises a real SDK error through `_sdk_call`.

    `after_scale_in` holds the failure back until the scale set is set to capacity 0. The call
    name `scale_in` means `update_scale_set` with capacity 0. `error` builds the exception.
    """

    def __init__(
        self, inner: FakeAdapter, call: str, times: int, after_scale_in=False, error=None
    ) -> None:
        self.inner = inner
        self.call = call
        self.times = times
        self.error = error or (lambda: ServiceRequestError(RESET))
        self.armed = not after_scale_in

    @property
    def clock(self) -> FakeClock:
        return self.inner.clock

    def for_teardown(self, remaining_seconds):
        return self

    def __getattr__(self, name: str) -> Any:
        target = getattr(self.inner, name)
        if not callable(target):
            return target

        def call(*args: Any, **kwargs: Any) -> Any:
            if name == "update_scale_set" and args[3] == 0:
                self.armed = True
            scale_in = name == "update_scale_set" and args[3] == 0
            hit = scale_in if self.call == "scale_in" else name == self.call
            if hit and self.armed and self.times > 0:
                self.times -= 1

                def fail() -> Any:
                    raise self.error()

                _sdk_call(fail)
            return target(*args, **kwargs)

        return call


def one(adapter: Any, probe: Any, **section: Any) -> Any:
    (row,) = wire(probe, adapter).run(make_ctx([entry()], **section))
    return row


def test_vmss_reset_on_list_members_after_scale_in_still_verifies():
    inner = FakeAdapter(FakeClock(), skus=[b2s(capacity_reservation_supported=False)])
    adapter = FlakyAdapter(inner, "list_members", 2, after_scale_in=True)
    row = one(adapter, VmssFlexProbe())
    teardown = row.details["teardown"]
    assert teardown["errors"] == ["ServiceRequestError"] * 2
    assert teardown["verified"] is True
    assert teardown["remaining"] == 0


def test_vmss_unending_reset_on_list_members_ends_unverified_not_early():
    inner = FakeAdapter(FakeClock(), skus=[b2s(capacity_reservation_supported=False)])
    adapter = FlakyAdapter(inner, "list_members", 10_000, after_scale_in=True)
    row = one(adapter, VmssFlexProbe(), teardown_timeout_seconds=30)
    assert row.details["teardown"]["verified"] is False
    assert row.details["teardown"]["errors"]


def test_reservation_reset_on_get_reservation_still_verifies():
    adapter = FlakyAdapter(FakeAdapter(FakeClock()), "get_reservation", 2, after_scale_in=False)
    adapter.armed = False  # the create path reads the reservation too: fail only after the delete

    original = adapter.inner.delete_reservation

    def delete(*args: Any) -> Any:
        adapter.armed = True
        return original(*args)

    adapter.inner.delete_reservation = delete
    row = one(adapter, CapacityReservationProbe())
    teardown = row.details["teardown"]
    assert teardown["errors"] == ["ServiceRequestError"] * 2
    assert teardown["verified"] is True


def test_reservation_reset_on_delete_reservation_is_tried_again():
    adapter = FlakyAdapter(FakeAdapter(FakeClock()), "delete_reservation", 2)
    row = one(adapter, CapacityReservationProbe())
    teardown = row.details["teardown"]
    assert teardown["errors"] == ["ServiceRequestError"] * 2
    assert teardown["verified"] is True
    assert [c[0] for c in adapter.inner.calls].count("delete_reservation") == 1


def survivors() -> FakeAdapter:
    """A scale set whose 2 VMs survive the scale-in, so the teardown must delete them itself."""
    plan = [PlannedMember("placed", survives_scale_in=True)] * 2
    return FakeAdapter(FakeClock(), skus=[b2s(capacity_reservation_supported=False)], plan=plan)


def test_vmss_reset_on_delete_vm_is_tried_again_and_verifies():
    adapter = FlakyAdapter(survivors(), "delete_vm", 2, after_scale_in=True)
    teardown = one(adapter, VmssFlexProbe()).details["teardown"]
    assert teardown["errors"] == ["ServiceRequestError"] * 2
    assert teardown["verified"] is True
    assert teardown["remaining"] == 0


def test_vmss_reset_on_the_scale_in_is_logged_and_the_vms_are_deleted():
    adapter = FlakyAdapter(survivors(), "scale_in", 1)
    teardown = one(adapter, VmssFlexProbe()).details["teardown"]
    assert teardown["errors"] == ["ServiceRequestError"]
    assert teardown["verified"] is True
    assert teardown["remaining"] == 0


def test_vmss_reset_on_tag_resource_still_verifies():
    adapter = FlakyAdapter(survivors(), "tag_resource", 2, after_scale_in=True)
    teardown = one(adapter, VmssFlexProbe()).details["teardown"]
    assert teardown["verified"] is True
    assert teardown["remaining"] == 0


def test_sdk_call_converts_a_deserialization_error_with_its_message():
    def boom():
        raise DeserializationError("Unable to deserialize to object: type, bad body")

    with pytest.raises(AzureCallError) as caught:
        _sdk_call(boom)
    assert caught.value.code == "DeserializationError"
    assert caught.value.message == "Unable to deserialize to object: type, bad body"


def test_sdk_call_keeps_a_programming_error_loud():
    def boom():
        raise ValueError("a bug")

    with pytest.raises(ValueError):
        _sdk_call(boom)


def test_vmss_deserialization_error_during_teardown_ends_verified():
    adapter = FlakyAdapter(
        survivors(),
        "list_members",
        2,
        after_scale_in=True,
        error=lambda: DeserializationError("bad body"),
    )
    teardown = one(adapter, VmssFlexProbe()).details["teardown"]
    assert teardown["errors"] == ["DeserializationError"] * 2
    assert teardown["verified"] is True


def test_vmss_unending_deserialization_error_ends_unverified_not_early():
    adapter = FlakyAdapter(
        survivors(),
        "list_members",
        10_000,
        after_scale_in=True,
        error=lambda: DeserializationError("bad body"),
    )
    teardown = one(adapter, VmssFlexProbe(), teardown_timeout_seconds=30).details["teardown"]
    assert teardown["verified"] is False
    assert teardown["errors"]


class BodyTransport(HttpTransport):
    """Every request gets a 200 answer with the given body, as a proxy or a bad gateway can give."""

    def __init__(self, body: bytes) -> None:
        self.body = body

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __exit__(self, *args: Any) -> None:
        pass

    def send(self, request: Any, **kwargs: Any) -> Any:
        from azure.core.pipeline.transport import RequestsTransportResponse
        from requests import Response

        raw = Response()
        raw.status_code = 200
        raw._content = self.body
        raw.headers["Content-Type"] = "text/html"
        return RequestsTransportResponse(request, raw)


HTML = b"<html><body>Bad gateway page of a proxy</body></html>"
BODIES = {"html": HTML, "empty": b"", "json_array": b"[]"}


def real_sdk_error(call: str, body: bytes) -> Exception:
    """The exception that the real SDK client raises, through the real adapter, for a 200 body."""
    compute = ComputeManagementClient(
        Credential(), "sub", transport=BodyTransport(body), retry_total=0
    )
    adapter = AzureAllocationAdapter(compute, lambda: None)
    calls = {
        "list_members": lambda: adapter.list_members("rg", "/ss/1"),
        "get_reservation": lambda: adapter.get_reservation("rg", "grp", "res"),
        "get_scale_set": lambda: adapter.get_scale_set("rg", "ss"),
    }
    try:
        calls[call]()
    except Exception as exc:  # noqa: BLE001 - the test wants the exception as the SDK raised it
        return exc
    raise AssertionError(f"{call} did not raise for body {body!r}")


@pytest.mark.parametrize("body", BODIES.values(), ids=BODIES.keys())
def test_vmss_200_body_that_does_not_parse_on_list_members_ends_verified(body, caplog):
    error = real_sdk_error("list_members", body)
    adapter = FlakyAdapter(survivors(), "list_members", 2, after_scale_in=True, error=lambda: error)
    teardown = one(adapter, VmssFlexProbe()).details["teardown"]
    assert teardown["errors"] == [type(error).__name__] * 2
    assert teardown["verified"] is True
    unexpected = [r for r in caplog.records if "teardown.unexpected_error" in r.getMessage()]
    assert len(unexpected) == 2
    assert all(r.levelname == "ERROR" and r.exc_info for r in unexpected)
    assert type(error).__name__ in unexpected[0].getMessage()


def test_vmss_unending_200_body_that_does_not_parse_ends_unverified_not_early():
    error = real_sdk_error("list_members", HTML)
    adapter = FlakyAdapter(
        survivors(), "list_members", 10_000, after_scale_in=True, error=lambda: error
    )
    teardown = one(adapter, VmssFlexProbe(), teardown_timeout_seconds=30).details["teardown"]
    assert teardown["verified"] is False
    assert teardown["errors"]


@pytest.mark.parametrize("body", BODIES.values(), ids=BODIES.keys())
def test_vmss_200_body_that_does_not_parse_on_the_capacity_read_ends_verified(body, caplog):
    error = real_sdk_error("get_scale_set", body)
    adapter = FlakyAdapter(
        survivors(), "get_scale_set", 2, after_scale_in=True, error=lambda: error
    )
    teardown = one(adapter, VmssFlexProbe()).details["teardown"]
    assert teardown["errors"] == [type(error).__name__] * 2
    assert teardown["verified"] is True
    unexpected = [r for r in caplog.records if "teardown.unexpected_error" in r.getMessage()]
    assert len(unexpected) == 2


def test_vmss_unending_200_body_on_the_capacity_read_ends_unverified_not_early():
    error = real_sdk_error("get_scale_set", HTML)
    adapter = FlakyAdapter(
        survivors(), "get_scale_set", 10_000, after_scale_in=True, error=lambda: error
    )
    teardown = one(adapter, VmssFlexProbe(), teardown_timeout_seconds=30).details["teardown"]
    assert teardown["verified"] is False
    assert teardown["errors"]


@pytest.mark.parametrize("call", ["delete_vm", "tag_resource", "scale_in"])
def test_vmss_unexpected_exception_on_the_other_teardown_calls_still_verifies(call):
    error = real_sdk_error("list_members", HTML)
    adapter = FlakyAdapter(survivors(), call, 2, after_scale_in=True, error=lambda: error)
    teardown = one(adapter, VmssFlexProbe()).details["teardown"]
    assert teardown["verified"] is True
    assert teardown["remaining"] == 0


@pytest.mark.parametrize("body", BODIES.values(), ids=BODIES.keys())
def test_reservation_200_body_that_does_not_parse_on_get_reservation_ends_verified(body):
    error = real_sdk_error("get_reservation", body)
    adapter = FlakyAdapter(FakeAdapter(FakeClock()), "get_reservation", 2, after_scale_in=False)
    adapter.armed = False
    adapter.error = lambda: error
    original = adapter.inner.delete_reservation

    def delete(*args: Any) -> Any:
        adapter.armed = True
        return original(*args)

    adapter.inner.delete_reservation = delete
    teardown = one(adapter, CapacityReservationProbe()).details["teardown"]
    assert teardown["errors"] == [type(error).__name__] * 2
    assert teardown["verified"] is True


def test_reservation_unexpected_exception_on_delete_reservation_is_tried_again():
    error = real_sdk_error("get_reservation", HTML)
    adapter = FlakyAdapter(FakeAdapter(FakeClock()), "delete_reservation", 2, error=lambda: error)
    teardown = one(adapter, CapacityReservationProbe()).details["teardown"]
    assert teardown["errors"] == [type(error).__name__] * 2
    assert teardown["verified"] is True


def test_tag_resource_builds_the_tags_client_inside_the_sdk_call():
    def broken_client() -> Any:
        raise ServiceRequestError(RESET)

    adapter = AzureAllocationAdapter(None, lambda: None)
    adapter._tags_client = broken_client  # type: ignore[method-assign]
    with pytest.raises(AzureCallError) as caught:
        adapter.tag_resource("/id", {"a": "b"})
    assert caught.value.code == "ServiceRequestError"
