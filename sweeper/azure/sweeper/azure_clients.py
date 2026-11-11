"""The real Azure clients behind the Rows, Deleter, and Scaler protocols.

Only this module and function_app.py import the Azure SDKs. The tests do not import it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from azure.core.credentials import TokenCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.core.polling import LROPoller
from azure.identity import DefaultAzureCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.compute.models import Sku, VirtualMachineScaleSetUpdate
from azure.mgmt.resource.resources import ResourceManagementClient
from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions

from sweeper.model import DISK, NIC, RESERVATION, VM, Resource
from sweeper.query import resource_group_of, subscription_of

# GA api-versions of the DELETE call for each type. Any GA version that has DELETE works.
API_VERSIONS: dict[str, str] = {
    VM: "2024-07-01",
    DISK: "2024-03-02",
    NIC: "2024-05-01",
    RESERVATION: "2024-07-01",
}


class PollerHandle:
    def __init__(self, poller: LROPoller[Any]) -> None:
        self._poller = poller

    def wait(self, timeout: float) -> bool:
        self._poller.wait(timeout)
        return self._poller.done()


class ResourceGraphRows:
    """Runs the query with Resource Graph and follows the skip token through all pages."""

    def __init__(self, credential: TokenCredential, subscriptions: list[str]) -> None:
        if not subscriptions or not all(s.strip() for s in subscriptions):
            raise ValueError("Resource Graph needs a subscription scope; none was given")
        self._client = ResourceGraphClient(credential)
        self._subscriptions = subscriptions

    def query(self, kql: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        skip_token: str | None = None
        while True:
            options = QueryRequestOptions(result_format="objectArray", skip_token=skip_token)
            request = QueryRequest(query=kql, subscriptions=self._subscriptions, options=options)
            response = self._client.resources(request)
            rows.extend(response.data or [])
            skip_token = response.skip_token
            if not skip_token:
                return rows


class ArmDeleter:
    """Deletes a resource by its id with the ARM Resources API."""

    def __init__(self, credential: TokenCredential) -> None:
        self._credential = credential
        self._clients: dict[str, ResourceManagementClient] = {}

    def _client(self, subscription_id: str) -> ResourceManagementClient:
        if subscription_id not in self._clients:
            self._clients[subscription_id] = ResourceManagementClient(
                self._credential, subscription_id
            )
        return self._clients[subscription_id]

    def begin_delete(self, resource: Resource) -> PollerHandle | None:
        """Start the delete. Return None when the resource is already gone.

        Resource Manager answers a DELETE of a resource that does not exist with
        204 No Content. A 404 comes only when a parent is gone. Both mean "already gone".
        """
        subscription_id = subscription_of(resource.id)
        if subscription_id is None:
            raise ValueError(f"id has no subscription: {resource.id}")
        statuses: list[int] = []
        try:
            poller = self._client(subscription_id).resources.begin_delete_by_id(
                resource.id,
                api_version=API_VERSIONS[resource.type],
                raw_response_hook=lambda r: statuses.append(r.http_response.status_code),
            )
        except ResourceNotFoundError:
            return None
        if statuses and statuses[0] == 204:
            return None
        return PollerHandle(poller)


class VmssScaler:
    """Sets sku.capacity of a scale set to 0 with a PATCH."""

    def __init__(self, credential: TokenCredential) -> None:
        self._credential = credential

    def begin_scale_to_zero(self, vmss: Resource) -> PollerHandle:
        subscription_id = subscription_of(vmss.id)
        resource_group = resource_group_of(vmss.id)
        if subscription_id is None or resource_group is None:
            raise ValueError(f"id is not a scale set id: {vmss.id}")
        client = ComputeManagementClient(self._credential, subscription_id)
        poller = client.virtual_machine_scale_sets.begin_update(
            resource_group, vmss.name, VirtualMachineScaleSetUpdate(sku=Sku(capacity=0))
        )
        return PollerHandle(poller)


def build_clients(env: Mapping[str, str]) -> tuple[ResourceGraphRows, ArmDeleter, VmssScaler]:
    """Build the 3 clients with the default credential chain (managed identity on Azure).

    Raises ValueError when `AZURE_SUBSCRIPTION_ID` is missing: the query is always scoped.
    """
    credential = DefaultAzureCredential()
    subscription = env.get("AZURE_SUBSCRIPTION_ID", "").strip()
    if not subscription:
        raise ValueError("AZURE_SUBSCRIPTION_ID is not set; the sweeper never queries unscoped")
    rows = ResourceGraphRows(credential, [subscription])
    return rows, ArmDeleter(credential), VmssScaler(credential)
