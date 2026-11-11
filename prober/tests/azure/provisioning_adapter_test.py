"""The real adapter maps SDK models and errors. Fake SDK objects, and real models. Spec 0010, 3."""

from enum import Enum
from types import SimpleNamespace as NS

import pytest

from ftg_prober.azure.provisioning_adapter import (
    AzureAllocationAdapter,
    AzureCallError,
    PollerOperation,
    member_from_vm,
    to_call_error,
)
from tests.conftest import import_or_skip


class FakeHttpError(Exception):
    """Has the 2 attributes of an azure-core HttpResponseError that the adapter reads."""

    def __init__(self, code, message="", details=(), status_code=400):
        super().__init__(message)
        self.status_code = status_code
        self.error = NS(code=code, message=message, details=[NS(code=c) for c in details])


class FakeRestriction(dict):
    """Like the real model, a Mapping: the `values` field is `r["values"]`, and `r.values` is the
    dict method."""

    def __init__(self, type, reason_code, values, restriction_info):
        super().__init__(values=values)
        self.type = type
        self.reason_code = reason_code
        self.restriction_info = restriction_info


def sku(name, crs="True", restrictions=(), zones=("1", "2"), rtype="virtualMachines"):
    return NS(
        name=name,
        resource_type=rtype,
        family="standardBSFamily",
        capabilities=[
            NS(name="vCPUs", value="2"),
            NS(name="CapacityReservationSupported", value=crs),
        ],
        location_info=[NS(location="eastus", zones=list(zones))],
        restrictions=list(restrictions),
    )


def test_list_skus_reads_capabilities_zones_and_restrictions():
    restriction = FakeRestriction(
        "Zone",
        "NotAvailableForSubscription",
        ["eastus"],
        NS(locations=["eastus"], zones=["3"]),
    )
    calls = []

    def list_skus(**kwargs):
        calls.append(kwargs)
        return [sku("Standard_B2s", restrictions=[restriction]), sku("x", rtype="disks")]

    compute = NS(resource_skus=NS(list=list_skus))
    (found,) = AzureAllocationAdapter(compute, lambda: None).list_skus("eastus")
    assert calls == [{"filter": "location eq 'eastus'"}]
    assert (found.name, found.vcpus, found.capacity_reservation_supported) == (
        "Standard_B2s",
        2,
        True,
    )
    assert found.zones == ("1", "2")
    assert found.restrictions[0].zones == ("3",)
    assert found.restrictions[0].locations == ("eastus",)
    assert found.restrictions[0].values == ("eastus",)
    assert found.restrictions[0].reason_code == "NotAvailableForSubscription"


def test_list_usage():
    usage = NS(
        name=NS(value="cores", localized_value="Total Regional vCPUs"), current_value=4, limit=10
    )
    compute = NS(usage=NS(list=lambda location: [usage]))
    (found,) = AzureAllocationAdapter(compute, lambda: None).list_usage("eastus")
    assert (found.name, found.current, found.limit) == ("cores", 4, 10)


def test_member_from_vm_reads_states_and_ids():
    vm = NS(
        id="/vm/1",
        name="vm1",
        provisioning_state="Failed",
        instance_view=NS(
            statuses=[
                NS(code="ProvisioningState/failed/AllocationFailed", message="raw message"),
                NS(code="PowerState/creating", message=None),
            ]
        ),
        storage_profile=NS(os_disk=NS(managed_disk=NS(id="/disk/1"))),
        network_profile=NS(network_interfaces=[NS(id="/nic/1")]),
    )
    member = member_from_vm(vm)
    assert (member.power_state, member.error_code) == ("creating", "AllocationFailed")
    assert member.error_message == "raw message"
    assert (member.os_disk_id, member.nic_ids) == ("/disk/1", ("/nic/1",))


def test_list_members_filters_by_scale_set_with_the_instance_view():
    calls = []

    def list_vms(rg, **kwargs):
        calls.append((rg, kwargs))
        return []

    compute = NS(virtual_machines=NS(list=list_vms))
    AzureAllocationAdapter(compute, lambda: None).list_members("ftg-probe", "/ss/1")
    assert calls == [
        (
            "ftg-probe",
            {"filter": "'virtualMachineScaleSet/id' eq '/ss/1'", "expand": "instanceView"},
        )
    ]


def test_sdk_error_becomes_azure_call_error_with_detail_codes():
    def boom(*args):
        raise FakeHttpError(
            "ResourceOperationFailure", "x", details=("AllocationFailed",), status_code=409
        )

    compute = NS(capacity_reservations=NS(begin_create_or_update=boom))
    with pytest.raises(AzureCallError) as caught:
        AzureAllocationAdapter(compute, lambda: None).create_reservation(
            "rg", "g", "n", "eastus", "Standard_B2s", None, 1, {}
        )
    assert caught.value.codes == ("ResourceOperationFailure", "AllocationFailed")
    assert caught.value.status == 409


def test_create_reservation_body():
    seen = []

    def create(rg, group, name, body):
        seen.append(body)
        return NS(done=lambda: False)

    compute = NS(capacity_reservations=NS(begin_create_or_update=create))
    AzureAllocationAdapter(compute, lambda: None).create_reservation(
        "rg", "g", "n", "eastus", "Standard_B2s", "1", 2, {"experiment": "ftg"}
    )
    assert seen == [
        {
            "location": "eastus",
            "sku": {"name": "Standard_B2s", "capacity": 2},
            "tags": {"experiment": "ftg"},
            "zones": ["1"],
        }
    ]


def test_get_reservation_404_is_none():
    def get(*args):
        raise FakeHttpError("ResourceNotFound", "gone", status_code=404)

    compute = NS(capacity_reservations=NS(get=get))
    assert AzureAllocationAdapter(compute, lambda: None).get_reservation("rg", "g", "n") is None


def test_poller_operation_never_blocks_and_reads_the_error():
    state = {"done": False}

    def result():
        raise FakeHttpError("AllocationFailed", "no")

    op = PollerOperation(NS(done=lambda: state["done"], result=result))
    assert (op.done(), op.error()) == (False, None)
    state["done"] = True
    assert op.error().code == "AllocationFailed"


def test_non_azure_exception_is_not_wrapped():
    def boom(**kwargs):
        raise KeyError("x")

    compute = NS(resource_skus=NS(list=boom))
    with pytest.raises(KeyError):
        AzureAllocationAdapter(compute, lambda: None).list_skus("eastus")
    assert to_call_error(ValueError("v")).code == "ValueError"


class RestrictionType(str, Enum):
    """Like the SDK enums: a str enum whose `str()` is the member name, not the raw value."""

    ZONE = "Zone"


class ReasonCode(str, Enum):
    NOT_AVAILABLE_FOR_SUBSCRIPTION = "NotAvailableForSubscription"


def test_list_skus_reads_the_raw_value_of_sdk_enums():
    restriction = FakeRestriction(
        RestrictionType.ZONE,
        ReasonCode.NOT_AVAILABLE_FOR_SUBSCRIPTION,
        ["eastus"],
        NS(locations=["eastus"], zones=["3"]),
    )
    assert str(RestrictionType.ZONE) != "Zone"  # the trap that this test guards
    compute = NS(
        resource_skus=NS(list=lambda **kw: [sku("Standard_B2s", restrictions=[restriction])])
    )
    (found,) = AzureAllocationAdapter(compute, lambda: None).list_skus("eastus")
    assert (found.restrictions[0].type, found.restrictions[0].reason_code) == (
        "Zone",
        "NotAvailableForSubscription",
    )


def test_list_skus_reads_restrictions_of_the_real_sdk_model():
    """The real `ResourceSkuRestrictions` is a Mapping, so `.values` is a method. Build
    the models from a REST JSON payload, as the SDK deserializes a response."""
    models = import_or_skip("azure.mgmt.compute.models")
    payload = {
        "resourceType": "virtualMachines",
        "name": "Standard_NC4as_T4_v3",
        "family": "standardNCASv3_T4Family",
        "capabilities": [
            {"name": "vCPUs", "value": "4"},
            {"name": "CapacityReservationSupported", "value": "True"},
        ],
        "locationInfo": [{"location": "centralus", "zones": ["1", "2"]}],
        "restrictions": [
            {
                "type": "Location",
                "values": ["centralus"],
                "restrictionInfo": {"locations": ["centralus"], "zones": ["3"]},
                "reasonCode": "NotAvailableForSubscription",
            },
            {"type": "Zone", "reasonCode": "QuotaId"},
        ],
    }
    compute = NS(resource_skus=NS(list=lambda **kw: [models.ResourceSku(payload)]))
    (found,) = AzureAllocationAdapter(compute, lambda: None).list_skus("centralus")
    first, second = found.restrictions
    assert (first.type, first.reason_code) == ("Location", "NotAvailableForSubscription")
    assert (first.values, first.locations, first.zones) == (("centralus",), ("centralus",), ("3",))
    assert (second.type, second.reason_code, second.values) == ("Zone", "QuotaId", ())
    assert (found.vcpus, found.capacity_reservation_supported, found.zones) == (
        4,
        True,
        ("1", "2"),
    )


def test_a_json_decode_error_from_the_sdk_passes_through_the_adapter():
    """Not an Azure error: `_sdk_call` lets it pass, so `allocate` must catch it."""
    import json

    def list_vms(rg, **kwargs):
        raise json.JSONDecodeError("Expecting value", "<html>", 0)

    compute = NS(virtual_machines=NS(list=list_vms))
    with pytest.raises(json.JSONDecodeError):
        AzureAllocationAdapter(compute, lambda: None).list_members("rg", "/ss/1")


def test_a_scale_set_without_capacity_has_capacity_none():
    from ftg_prober.azure.provisioning_adapter import _scale_set_info

    no_sku = NS(id="/ss/1", name="ss", tags=None, sku=None)
    no_capacity = NS(id="/ss/1", name="ss", tags=None, sku=NS(name="Standard_B2s", capacity=None))
    zero = NS(id="/ss/1", name="ss", tags=None, sku=NS(name="Standard_B2s", capacity=0))
    assert _scale_set_info(no_sku).capacity is None
    assert _scale_set_info(no_capacity).capacity is None
    assert _scale_set_info(zero).capacity == 0


class _StubTransport:
    """A stub azure-core transport: the scale-out PATCH starts an async operation; the first
    poll GET returns 503 when `fail_first_poll`, then `Succeeded`. It records what each request
    carried."""

    def __new__(cls, *args, **kwargs):
        from azure.core.pipeline.transport import HttpTransport

        class Transport(HttpTransport):
            def __init__(self, fail_first_poll):
                self.fail_first_poll = fail_first_poll
                self.polls = 0
                self.get_status = 200  # the status of a GET that is not a poll
                self.sent = []

            def open(self):
                pass

            def close(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def send(self, request, **kw):
                self.sent.append(
                    (
                        request.method,
                        {k: kw[k] for k in ("read_timeout", "connection_timeout") if k in kw},
                    )
                )
                if request.method == "PATCH":
                    return _response(
                        request,
                        200,
                        {"name": "v"},
                        {"Azure-AsyncOperation": "https://management.azure.com/op/1?x=1"},
                    )
                if "/op/1" in request.url:
                    self.polls += 1
                    if self.fail_first_poll and self.polls == 1:
                        return _response(request, 503, {"error": {"code": "Unavailable"}})
                    return _response(request, 200, {"status": "Succeeded"})
                if self.get_status != 200:
                    return _response(request, self.get_status, {"error": {"code": "Unavailable"}})
                return _response(request, 200, {"name": "v"})

        return Transport(*args, **kwargs)


def _response(request, status, body, headers=None):
    import io
    import json

    import requests
    import urllib3
    from azure.core.rest._requests_basic import RestRequestsTransportResponse

    response = requests.Response()
    response.status_code = status
    response.raw = urllib3.response.HTTPResponse(
        body=io.BytesIO(json.dumps(body).encode()), preload_content=False, status=status
    )
    response.headers.update(
        {"Content-Type": "application/json", "Retry-After": "0", **(headers or {})}
    )
    response.reason = "stub"
    return RestRequestsTransportResponse(request=request, internal_response=response)


def _real_client_adapter(transport):
    import time

    import_or_skip("azure.mgmt.compute")
    from azure.core.credentials import AccessToken
    from azure.mgmt.compute import ComputeManagementClient

    class Credential:
        def get_token(self, *scopes, **kwargs):
            return AccessToken("token", int(time.time()) + 3600)

    client = ComputeManagementClient(
        Credential(), "sub", transport=transport, polling_interval=0, retry_backoff_factor=0
    )
    return AzureAllocationAdapter(client, lambda: None)


def _wait(operation):
    import time

    for _ in range(100):
        if operation.done():
            return
        time.sleep(0.05)


def test_the_scale_out_keeps_the_sdk_retries_when_a_poll_returns_503_once():
    """`retry_total=0` is for the teardown only. The scale-out poller still retries."""
    transport = _StubTransport(fail_first_poll=True)
    adapter = _real_client_adapter(transport)
    operation = adapter.update_scale_set("rg", "vmss", "Standard_D2", 4, {"a": "b"})
    _wait(operation)
    assert operation.done()
    assert operation.error() is None
    assert transport.polls == 2


def test_the_teardown_calls_carry_a_timeout_and_the_floor_applies():
    transport = _StubTransport(fail_first_poll=False)
    adapter = _real_client_adapter(transport)
    adapter.for_teardown(lambda: 45).update_scale_set("rg", "vmss", "Standard_D2", 0, None)
    adapter.for_teardown(lambda: 3).get_scale_set("rg", "vmss")
    adapter.for_teardown(lambda: -5).list_members("rg", "/ss/1")
    carried = [c for _, c in transport.sent if c]
    assert {"read_timeout": 45, "connection_timeout": 45} in carried
    assert {"read_timeout": 10, "connection_timeout": 10} in carried
    # The original adapter has no timeout of its own.
    transport.sent.clear()
    adapter.get_scale_set("rg", "vmss")
    assert not [c for _, c in transport.sent if c]


def test_a_teardown_call_is_not_retried_and_the_original_adapter_retries():
    transport = _StubTransport(fail_first_poll=False)
    transport.get_status = 503
    adapter = _real_client_adapter(transport)
    with pytest.raises(AzureCallError):
        adapter.for_teardown(lambda: 60).get_scale_set("rg", "vmss")
    assert len(transport.sent) == 1
    transport.sent.clear()
    with pytest.raises(AzureCallError):
        adapter.get_scale_set("rg", "vmss")
    assert len(transport.sent) > 1


def test_the_scale_in_poller_of_the_teardown_is_not_retried():
    transport = _StubTransport(fail_first_poll=True)
    operation = (
        _real_client_adapter(transport)
        .for_teardown(lambda: 60)
        .update_scale_set("rg", "vmss", "Standard_D2", 0, None)
    )
    _wait(operation)
    assert operation.error() is not None


def _tag_adapter(transport):
    import_or_skip("azure.mgmt.resource")
    import time

    from azure.core.credentials import AccessToken
    from azure.mgmt.resource.resources import ResourceManagementClient

    class Credential:
        def get_token(self, *scopes, **kwargs):
            return AccessToken("token", int(time.time()) + 3600)

    adapter = _real_client_adapter(transport)
    tags = ResourceManagementClient(
        Credential(), "sub", transport=transport, polling_interval=0, retry_backoff_factor=0
    ).tags
    adapter._tags_client = lambda: tags  # type: ignore[method-assign]
    return adapter


def test_the_teardown_tag_call_and_its_poller_carry_a_timeout_and_are_not_retried():
    """A tag call during the teardown is bounded; outside it keeps the SDK defaults."""
    transport = _StubTransport(fail_first_poll=True)
    adapter = _tag_adapter(transport)
    with pytest.raises(AzureCallError):
        adapter.for_teardown(lambda: 45).tag_resource("/subscriptions/s/x", {"a": "b"})
    assert transport.polls == 1
    assert all(c == {"read_timeout": 45, "connection_timeout": 45} for _, c in transport.sent)
    transport = _StubTransport(fail_first_poll=True)
    _tag_adapter(transport).tag_resource("/subscriptions/s/x", {"a": "b"})
    assert transport.polls == 2
    assert not [c for _, c in transport.sent if c]


def test_the_tags_client_is_built_once_by_the_client_factory_across_runs(monkeypatch):
    """Each run builds an adapter (and `for_teardown` copies it). The adapter must not
    build its own `ResourceManagementClient`: the SDK client owns an HTTP session that only its
    `close()` releases (azure-mgmt-resource), and nothing closed the per-run copy. The real class
    is counted, not faked; the client factory of the core builds it once for every run."""
    import_or_skip("azure.mgmt.resource")
    import azure.mgmt.resource.resources as resources

    from ftg_prober.azure.provisioning_adapter import make_real_adapter
    from ftg_prober.probes import Clients

    built = []

    class Counting(resources.ResourceManagementClient):
        def __init__(self, *args, **kwargs):
            built.append(self)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(resources, "ResourceManagementClient", Counting)
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub")
    monkeypatch.setenv("ARM_SUBSCRIPTION_ID", "sub")

    class Ctx:
        clients = Clients()

    tag_clients = []
    for _run in range(2):
        adapter = make_real_adapter(Ctx)
        tag_clients.append(adapter._tags_client())
        tag_clients.append(adapter.for_teardown(lambda: 60)._tags_client())
    assert len(built) == 1
    assert all(c is tag_clients[0] for c in tag_clients)


def test_the_tag_call_does_not_ask_the_client_factory_for_a_client():
    """`Clients.azure` takes a lock that another lane can hold while it builds a
    client, outside the timeout of the tag call. `make_real_adapter` takes the tags client up
    front, and the tag call (also on a `for_teardown` copy) never asks the factory again."""
    from ftg_prober.azure.provisioning_adapter import make_real_adapter

    asked = []

    class FakeClients:
        def azure(self, kind):
            asked.append(kind)
            return NS(tags=f"tags-of-{kind}")

    adapter = make_real_adapter(NS(clients=FakeClients()))
    assert "resources" in asked
    asked.clear()
    assert adapter._tags_client() == "tags-of-resources"
    assert adapter.for_teardown(lambda: 60)._tags_client() == "tags-of-resources"
    assert asked == []


def test_the_teardown_tag_client_is_ready_while_another_lane_holds_the_clients_lock():
    """A lane that builds a client holds `Clients._lock`. The tags client of a
    teardown copy must come back at once anyway (a join timeout keeps the suite from hanging)."""
    import threading

    from ftg_prober.azure.provisioning_adapter import make_real_adapter
    from ftg_prober.probes import Clients

    clients = Clients(azure_factory=lambda kind: NS(tags=f"tags-of-{kind}"))
    adapter = make_real_adapter(NS(clients=clients))
    held, release, got = threading.Event(), threading.Event(), []

    def hold():
        with clients._lock:
            held.set()
            release.wait(10)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert held.wait(5)
    caller = threading.Thread(
        target=lambda: got.append(adapter.for_teardown(lambda: 60)._tags_client()), daemon=True
    )
    caller.start()
    caller.join(2)
    ready = list(got)
    release.set()
    holder.join(5)
    assert ready == ["tags-of-resources"], "the tags client waited for the Clients lock"


def test_the_default_azure_clients_share_one_credential(monkeypatch):
    """The tags client shares the `credential` client, as before the factory built it."""
    import_or_skip("azure.mgmt.resource")
    from ftg_prober.probes import Clients

    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub")
    monkeypatch.setenv("ARM_SUBSCRIPTION_ID", "sub")
    clients = Clients()
    credential = clients.azure("credential")
    assert clients.azure("resources")._config.credential is credential
    assert clients.azure("compute")._config.credential is credential


def test_get_reservation_reads_state_and_capacity_from_the_real_model():
    """`CapacityReservation` of azure-mgmt-compute: `sku.capacity` (int), and
    `properties.provisioningState`, read as `provisioning_state` (REST: Capacity Reservations -
    Get). The fake `ReservationInfo` has the same 2 fields."""
    from azure.mgmt.compute import models

    model = models.CapacityReservation(
        {
            "location": "eastus",
            "sku": {"name": "Standard_B2s", "capacity": 3},
            "properties": {"provisioningState": "Succeeded"},
        }
    )
    compute = NS(capacity_reservations=NS(get=lambda *args: model))
    found = AzureAllocationAdapter(compute, lambda: None).get_reservation("rg", "g", "n")
    assert (found.provisioning_state, found.capacity) == ("Succeeded", 3)


def test_list_members_keeps_the_raw_states_of_the_real_model():
    """`VirtualMachine.properties.provisioningState` and the `PowerState/<state>` status of the
    instance view (REST: Virtual Machines - List with `$expand=instanceView`). The adapter does
    not change the case of either value."""
    from azure.mgmt.compute import models

    vm = models.VirtualMachine(
        {
            "id": "/vm/1",
            "name": "vm1",
            "location": "eastus",
            "properties": {
                "provisioningState": "Succeeded",
                "instanceView": {"statuses": [{"code": "PowerState/Running"}]},
            },
        }
    )
    compute = NS(virtual_machines=NS(list=lambda rg, **kwargs: [vm]))
    (member,) = AzureAllocationAdapter(compute, lambda: None).list_members("rg", "/ss/1")
    assert (member.provisioning_state, member.power_state) == ("Succeeded", "Running")
