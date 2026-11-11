"""Data types and the type constants of the sweeper."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# ARM resource types, in lower case, as Resource Graph returns them.
VM = "microsoft.compute/virtualmachines"
DISK = "microsoft.compute/disks"
NIC = "microsoft.network/networkinterfaces"
RESERVATION = "microsoft.compute/capacityreservationgroups/capacityreservations"
VMSS = "microsoft.compute/virtualmachinescalesets"

# The delete order of a run. A stage waits for its deletes before the next stage starts.
DELETE_STAGES: tuple[tuple[str, ...], ...] = ((VM,), (NIC, DISK), (RESERVATION,))
DELETABLE_TYPES: frozenset[str] = frozenset(t for stage in DELETE_STAGES for t in stage)

# The types that the query returns. VMSS rows serve the fallback only.
QUERY_TYPES: tuple[str, ...] = (VM, DISK, NIC, RESERVATION, VMSS)

EXPERIMENT_TAG = "experiment"


@dataclass(frozen=True)
class Resource:
    id: str
    name: str
    type: str
    resource_group: str
    tags: dict[str, str] = field(default_factory=dict)
    time_created: datetime | None = None
    vmss_id: str | None = None
    attached_vm_id: str | None = None
    capacity: int | None = None
