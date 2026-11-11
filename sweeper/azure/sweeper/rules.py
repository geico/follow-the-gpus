"""The age rule: which resource a run deletes, keeps, or skips."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sweeper.model import DELETABLE_TYPES, EXPERIMENT_TAG, NIC, VM, Resource

KEEP = "keep"
SKIP = "skip"
DELETE = "delete"


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    age_seconds: float | None


def age_seconds(resource: Resource, now: datetime) -> float | None:
    """Return now minus timeCreated in seconds, or None when the type has no timeCreated."""
    if resource.time_created is None:
        return None
    return (now - resource.time_created).total_seconds()


def is_young_vm(resource: Resource, now: datetime, max_age_seconds: int) -> bool:
    """True for a VM at or under the threshold. Such a VM shows a probe in flight."""
    if resource.type != VM:
        return False
    age = age_seconds(resource, now)
    return age is not None and age <= max_age_seconds


def decide(
    resource: Resource,
    now: datetime,
    *,
    max_age_seconds: int,
    probe_rg: str,
    experiment: str,
    probe_in_flight: bool,
) -> Decision:
    """Decide 1 resource. The checks run in this order, and the first hit wins."""
    age = age_seconds(resource, now)
    if resource.resource_group.lower() != probe_rg.lower():
        return Decision(SKIP, "outside probe resource group", age)
    if resource.tags.get(EXPERIMENT_TAG) != experiment:
        return Decision(SKIP, "experiment tag does not match", age)
    if resource.type not in DELETABLE_TYPES:
        return Decision(SKIP, "type is not deletable", age)
    if age is not None:
        if age > max_age_seconds:
            return Decision(DELETE, f"older than {max_age_seconds} s", age)
        return Decision(KEEP, "young", age)
    if resource.type == NIC:
        if resource.attached_vm_id:
            return Decision(KEEP, "attached to a VM, age unknown", None)
        if probe_in_flight:
            return Decision(KEEP, "unattached, but a young VM is present", None)
        return Decision(DELETE, "unattached, age unknown", None)
    return Decision(KEEP, "no timeCreated", None)
