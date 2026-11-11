"""The ARM deleter against a fake HTTP transport. No network, no credentials."""

from __future__ import annotations

from typing import Any

import pytest
from azure.core.credentials import AccessToken
from azure.core.exceptions import HttpResponseError
from azure.core.pipeline.transport import HttpTransport
from azure.core.rest._http_response_impl import HttpResponseImpl
from azure.mgmt.resource.resources import ResourceManagementClient

from sweeper.azure_clients import API_VERSIONS, ArmDeleter
from sweeper.model import VM, Resource
from tests.fakes import PROBE_RG, rid


class FakeCredential:
    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        return AccessToken("token", 9999999999)


class _Closable:
    def close(self) -> None:
        pass


class FakeTransport(HttpTransport):
    """Answers every request with 1 status code and an empty body."""

    def __init__(self, status: int) -> None:
        self.status = status
        self.urls: list[str] = []

    def send(self, request: Any, **kwargs: Any) -> HttpResponseImpl:
        self.urls.append(request.url)
        response = HttpResponseImpl(
            request=request,
            internal_response=_Closable(),
            status_code=self.status,
            reason="fake",
            content_type="application/json",
            headers={},
            stream_download_generator=None,
        )
        response._content = b'{"error": {"code": "NotFound", "message": "gone"}}'
        return response

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __exit__(self, *args: Any) -> None:
        pass


VM_RESOURCE = Resource(
    id=rid(PROBE_RG, "Microsoft.Compute/virtualMachines/vm1"),
    name="vm1",
    type=VM,
    resource_group=PROBE_RG,
    tags={"experiment": "ftg"},
)


def deleter_with(status: int) -> tuple[ArmDeleter, FakeTransport]:
    transport = FakeTransport(status)
    deleter = ArmDeleter(FakeCredential())
    deleter._clients["test-subscription"] = ResourceManagementClient(
        FakeCredential(), "test-subscription", transport=transport
    )
    return deleter, transport


def test_delete_sends_the_id_and_the_api_version() -> None:
    deleter, transport = deleter_with(200)
    handle = deleter.begin_delete(VM_RESOURCE)
    assert handle is not None
    assert handle.wait(1.0) is True
    (url,) = transport.urls
    assert url.endswith(f"{VM_RESOURCE.id}?api-version={API_VERSIONS[VM]}")


def test_204_means_already_gone() -> None:
    deleter, _ = deleter_with(204)
    assert deleter.begin_delete(VM_RESOURCE) is None


def test_404_means_already_gone() -> None:
    deleter, _ = deleter_with(404)
    assert deleter.begin_delete(VM_RESOURCE) is None


def test_other_errors_raise() -> None:
    deleter, _ = deleter_with(409)
    with pytest.raises(HttpResponseError):
        deleter.begin_delete(VM_RESOURCE)


def test_build_clients_without_subscription_fails_not_unscoped() -> None:
    from sweeper.azure_clients import build_clients

    for env in ({}, {"AZURE_SUBSCRIPTION_ID": ""}, {"AZURE_SUBSCRIPTION_ID": "  "}):
        with pytest.raises(ValueError, match="AZURE_SUBSCRIPTION_ID"):
            build_clients(env)


def test_resource_graph_rows_refuses_no_scope() -> None:
    from sweeper.azure_clients import ResourceGraphRows

    for scope in (None, [], [""]):
        with pytest.raises(ValueError, match="subscription scope"):
            ResourceGraphRows(FakeCredential(), scope)  # type: ignore[arg-type]


def test_query_request_carries_the_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real QueryRequest model, as the real ResourceGraphClient sends it."""
    from azure.mgmt.resourcegraph import ResourceGraphClient
    from azure.mgmt.resourcegraph.models import QueryRequest

    from sweeper.azure_clients import ResourceGraphRows

    sent: list[QueryRequest] = []

    class Page:
        data = [{"id": "x"}]
        skip_token = None

    def resources(self: Any, request: QueryRequest) -> Page:
        sent.append(request)
        return Page()

    monkeypatch.setattr(ResourceGraphClient, "resources", resources)
    rows = ResourceGraphRows(FakeCredential(), ["sub-0"])
    assert rows.query("resources") == [{"id": "x"}]
    (request,) = sent
    assert isinstance(request, QueryRequest)
    assert request.serialize()["subscriptions"] == ["sub-0"]
