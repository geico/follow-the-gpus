"""The Resource Graph query and the parser for its rows."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sweeper.model import EXPERIMENT_TAG, QUERY_TYPES, Resource

# Resource group names and tag values that the query accepts. The limit keeps the KQL safe.
_NAME_RE = re.compile(r"^[A-Za-z0-9._()-]{1,90}$")
_RG_RE = re.compile(r"/resourceGroups/([^/]+)/", re.IGNORECASE)
_SUBSCRIPTION_RE = re.compile(r"^/subscriptions/([^/]+)/", re.IGNORECASE)


class QueryError(ValueError):
    """A query input or a query row is not valid."""


def _check_name(value: str, what: str) -> str:
    if not _NAME_RE.match(value):
        raise QueryError(f"{what} has characters that the query does not accept")
    return value


def build_query(probe_rg: str, experiment: str) -> str:
    """Return the KQL text of the 1 query that a run sends to Resource Graph."""
    rg = _check_name(probe_rg, "FTG_PROBE_RG")
    tag = _check_name(experiment, "FTG_EXPERIMENT")
    types = ", ".join(f"'{t}'" for t in QUERY_TYPES)
    return (
        "Resources\n"
        f"| where resourceGroup =~ '{rg}'\n"
        f"| where tostring(tags['{EXPERIMENT_TAG}']) =~ '{tag}'\n"
        f"| where type in~ ({types})\n"
        "| project id, name, type, resourceGroup, tags,\n"
        "    timeCreated = tostring(properties.timeCreated),\n"
        "    vmssId = tostring(properties.virtualMachineScaleSet.id),\n"
        "    attachedVmId = tostring(properties.virtualMachine.id),\n"
        "    capacity = toint(sku.capacity)\n"
        "| order by id asc"
    )


def parse_time(value: Any) -> datetime | None:
    """Parse an ISO 8601 time from Resource Graph. Return None for an empty value."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def resource_group_of(resource_id: str) -> str | None:
    match = _RG_RE.search(resource_id)
    return match.group(1) if match else None


def subscription_of(resource_id: str) -> str | None:
    match = _SUBSCRIPTION_RE.search(resource_id)
    return match.group(1) if match else None


def parse_row(row: dict[str, Any]) -> Resource:
    """Turn 1 Resource Graph row into a Resource. The resource group comes from the id."""
    resource_id = str(row.get("id") or "")
    if not resource_id:
        raise QueryError("row has no id")
    resource_group = resource_group_of(resource_id)
    if resource_group is None:
        raise QueryError(f"id has no resource group: {resource_id}")
    tags = row.get("tags") or {}
    capacity = row.get("capacity")
    return Resource(
        id=resource_id,
        name=str(row.get("name") or ""),
        type=str(row.get("type") or "").lower(),
        resource_group=resource_group,
        tags={str(k): str(v) for k, v in tags.items()} if isinstance(tags, dict) else {},
        time_created=parse_time(row.get("timeCreated")),
        vmss_id=str(row["vmssId"]) if row.get("vmssId") else None,
        attached_vm_id=str(row["attachedVmId"]) if row.get("attachedVmId") else None,
        capacity=int(capacity) if capacity is not None else None,
    )
