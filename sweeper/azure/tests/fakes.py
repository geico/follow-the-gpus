"""Fakes for the Rows, Deleter, and Scaler protocols. No network, no credentials."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sweeper.model import VM, Resource

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)
# Not a GUID on purpose.
SUB = "/subscriptions/test-subscription"
PROBE_RG = "ftg-probe"


def rid(rg: str, provider_path: str) -> str:
    return f"{SUB}/resourceGroups/{rg}/providers/{provider_path}"


def row(
    provider_path: str,
    *,
    rtype: str,
    age_seconds: float | None,
    rg: str = PROBE_RG,
    experiment: str = "ftg",
    vmss_id: str | None = None,
    attached_vm_id: str | None = None,
    capacity: int | None = None,
) -> dict[str, Any]:
    """Build 1 Resource Graph row, in the shape of the query's project line."""
    created = "" if age_seconds is None else (NOW - timedelta(seconds=age_seconds)).isoformat()
    return {
        "id": rid(rg, provider_path),
        "name": provider_path.rsplit("/", 1)[-1],
        "type": rtype,
        "resourceGroup": rg.lower(),
        "tags": {"experiment": experiment},
        "timeCreated": created.replace("+00:00", "Z"),
        "vmssId": vmss_id or "",
        "attachedVmId": attached_vm_id or "",
        "capacity": capacity,
    }


def vm_row(name: str, age_seconds: float, **kwargs: Any) -> dict[str, Any]:
    return row(
        f"Microsoft.Compute/virtualMachines/{name}", rtype=VM, age_seconds=age_seconds, **kwargs
    )


class FakeRows:
    def __init__(self, rows: list[dict[str, Any]] | None = None, error: Exception | None = None):
        self.rows = rows or []
        self.error = error
        self.queries: list[str] = []

    def query(self, kql: str) -> list[dict[str, Any]]:
        self.queries.append(kql)
        if self.error is not None:
            raise self.error
        return list(self.rows)


class FakeHandle:
    def __init__(self, done: bool = True, error: Exception | None = None) -> None:
        self.done = done
        self.error = error
        self.waits: list[float] = []

    def wait(self, timeout: float) -> bool:
        self.waits.append(timeout)
        if self.error is not None:
            raise self.error
        return self.done


class FakeDeleter:
    """Records each begin_delete call in order. Ids in gone answer 404. Ids in failing raise."""

    def __init__(
        self,
        gone: set[str] | None = None,
        failing: set[str] | None = None,
        slow: set[str] | None = None,
    ) -> None:
        self.gone = {g.lower() for g in (gone or set())}
        self.failing = {f.lower() for f in (failing or set())}
        self.slow = {s.lower() for s in (slow or set())}
        self.calls: list[Resource] = []

    def begin_delete(self, resource: Resource) -> FakeHandle | None:
        self.calls.append(resource)
        key = resource.id.lower()
        if key in self.failing:
            raise RuntimeError("409 conflict")
        if key in self.gone:
            return None
        return FakeHandle(done=key not in self.slow)

    @property
    def deleted_ids(self) -> list[str]:
        return [r.id for r in self.calls]


class FakeScaler:
    def __init__(self) -> None:
        self.calls: list[Resource] = []

    def begin_scale_to_zero(self, vmss: Resource) -> FakeHandle:
        self.calls.append(vmss)
        return FakeHandle(done=True)
