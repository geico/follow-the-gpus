"""Fakes for the Azure provisioning probe tests. No network, no credentials, no real sleep."""

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from ftg_prober.azure.provisioning_adapter import (
    AzureCallError,
    Member,
    ReservationGroupInfo,
    ReservationInfo,
    ScaleSetInfo,
    SkuInfo,
    SkuRestriction,
    UsageInfo,
)
from ftg_prober.probes import Clients, ProbeContext
from tests.conftest import MemoryStore

# Built in code, not as a GUID-shaped literal.
RUN_ID = str(uuid.UUID(int=(0x0B6F1F9E << 96) | 1, version=4))


# Names that no formula builds: a probe must select by tags, never by a computed name.
SCALE_SET_NAME = "scale-set-9f3a1c2e"
RESERVATION_GROUP_NAME = "reservation-group-7d41b0c5"
GROUP_REGIONS = (
    "eastus",
    "eastus2",
    "westus",
    "northeurope",
    "southcentralus",
    "westus2",
    "westus3",
    "centralus",
    "northcentralus",
    "westcentralus",
)
EXPERIMENT = "ftg"


def scale_set_name(region: str = "eastus", sku: str = "Standard_B2s") -> str:
    """The name of the default scale set of a size in a Region."""
    middle = "" if sku == "Standard_B2s" else f"-{sku.lower()}"
    return f"{SCALE_SET_NAME}{middle}-{region}"


def group_name(region: str = "eastus") -> str:
    """The name of the default reservation group of a Region."""
    return f"{RESERVATION_GROUP_NAME}-{region}"


def vmss_tags(sku: str = "Standard_B2s", region: str = "eastus", **extra: str) -> dict[str, str]:
    """The tags that Terraform puts on a scale set (ADR 0022)."""
    return {
        "experiment": EXPERIMENT,
        "sku": sku,
        "region": region,
        "target": "x",
        "run_id": "none",
        **extra,
    }


# The text that Azure returns for a failed reservation or scale-out for lack of capacity.
ALLOCATION_FAILED_MESSAGE = (
    "Allocation failed. We do not have sufficient capacity for the requested VM size in this "
    "region. Read more about improving likelihood of allocation success at "
    "http://aka.ms/allocation-guidance"
)


class FakeClock:
    """A monotonic clock that moves only on sleep."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class FakeOp:
    def __init__(
        self, clock: FakeClock, done_at: float | None, error: AzureCallError | None = None
    ):
        self.clock = clock
        self.done_at = done_at
        self._error = error

    def done(self) -> bool:
        return self.done_at is not None and self.clock.t >= self.done_at

    def error(self) -> AzureCallError | None:
        return self._error if self.done() else None


@dataclass
class PlannedMember:
    """1 member of a scale-out: `creating` until `ready_after` seconds, then `final`."""

    final: str  # `placed`, `failed`, or `hang`
    code: str | None = None
    message: str | None = None  # the message of the failed status in the instance view
    ready_after: float = 2.0
    code_after: float = 0.0  # a failed member shows its instance-view code this long later
    survives_scale_in: bool = False
    undeletable: bool = False


def b2s(**overrides: Any) -> SkuInfo:
    fields = dict(
        name="Standard_B2s",
        family="standardBSFamily",
        vcpus=2,
        capacity_reservation_supported=True,
        zones=("1", "2", "3"),
        restrictions=(),
    )
    fields.update(overrides)
    return SkuInfo(**fields)


def usages(family_free: int = 100, total_free: int = 100) -> list[UsageInfo]:
    return [
        UsageInfo("cores", "Total Regional vCPUs", 0, total_free),
        UsageInfo("standardBSFamily", "Standard BS Family vCPUs", 0, family_free),
    ]


class BoundedProxy:
    """What `FakeAdapter.for_teardown` returns: it passes each call on and records its name."""

    def __init__(self, inner: "FakeAdapter") -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._inner, name)
        if not callable(target):
            return target

        def call(*args: Any, **kwargs: Any) -> Any:
            self._inner.bounded_calls.append(name)
            return target(*args, **kwargs)

        return call


@dataclass
class FakeAdapter:
    """A scriptable `AllocationAdapter`. Records each call in order."""

    clock: FakeClock
    skus: list[SkuInfo] = field(default_factory=lambda: [b2s()])
    usage: list[UsageInfo] = field(default_factory=usages)
    skus_error: AzureCallError | None = None
    # Capacity reservation behavior.
    create_error: AzureCallError | None = None  # raised at once
    create_after: float | None = 1.0  # None: never done
    create_op_error: AzureCallError | None = None
    reserved_capacity: int | None = None  # None: the requested capacity
    reservation_limit: int | None = None  # more than this fails like Azure: `AllocationFailed`
    delete_failures: int = 0
    # Scale set behavior.
    scale_set_capacity: int = 0
    get_capacity_missing: bool = False  # `get_scale_set` returns no capacity (None)
    scale_error: AzureCallError | None = None
    scale_op_after: float | None = 2.0
    scale_op_error: AzureCallError | None = None
    scale_in_op_error: AzureCallError | None = None  # the error of every scale-in operation
    plan: list[PlannedMember] = field(default_factory=list)
    leftovers: list[Member] = field(default_factory=list)
    list_error: AzureCallError | None = None
    tag_errors: int = 0
    # Selection by tags. None: the one resource of the default entry.
    scale_sets: list[ScaleSetInfo] | None = None
    reservation_groups: list[ReservationGroupInfo] | None = None
    list_scale_sets_error: AzureCallError | None = None
    list_groups_error: AzureCallError | None = None

    calls: list[tuple[Any, ...]] = field(default_factory=list)
    teardown_remaining: list[Any] = field(default_factory=list)  # the callables of `for_teardown`
    bounded_calls: list[str] = field(default_factory=list)  # the names called through the proxy
    tags: dict[str, dict[str, str]] = field(default_factory=dict)
    reservation: ReservationInfo | None = None
    members: dict[str, tuple[PlannedMember, float]] = field(default_factory=dict)
    # A scale-in sent before this time does not take effect (a test double, not an observation
    # of Azure): it makes the teardown send the scale-in again. None: it takes effect at once.
    scale_in_lost_until: float | None = None
    scale_in_ignored: bool = False  # a scale-in never takes effect: the capacity stays

    def list_skus(self, region):
        self.calls.append(("list_skus", region))
        if self.skus_error:
            raise self.skus_error
        return list(self.skus)

    def list_usage(self, region):
        self.calls.append(("list_usage", region))
        return list(self.usage)

    def create_reservation(self, rg, group, name, region, sku, zone, capacity, tags):
        self.calls.append(("create_reservation", rg, group, name, region, sku, zone, capacity))
        self.tags[name] = dict(tags)
        if self.create_error:
            raise self.create_error
        op_error = self.create_op_error
        if self.reservation_limit is not None and capacity > self.reservation_limit:
            op_error = AzureCallError("AllocationFailed", ALLOCATION_FAILED_MESSAGE)
        capacity = self.reserved_capacity if self.reserved_capacity is not None else capacity
        state = "Failed" if op_error else "Succeeded"
        self.reservation = ReservationInfo(state, capacity)
        done_at = None if self.create_after is None else self.clock.t + self.create_after
        return FakeOp(self.clock, done_at, op_error)

    def get_reservation(self, rg, group, name):
        self.calls.append(("get_reservation", name))
        return self.reservation

    def delete_reservation(self, rg, group, name):
        self.calls.append(("delete_reservation", name))
        if self.delete_failures > 0:
            self.delete_failures -= 1
            raise AzureCallError("Conflict", "busy", status=409)
        self.reservation = None
        return FakeOp(self.clock, self.clock.t)

    def _scale_set(self, name, tags=None):
        return ScaleSetInfo(
            id=f"/fake/virtualMachineScaleSets/{name}",
            name=name,
            capacity=self.scale_set_capacity,
            sku_name="Standard_B2s",
            tags=vmss_tags() if tags is None else tags,
        )

    def list_scale_sets(self, rg):
        self.calls.append(("list_scale_sets", rg))
        if self.list_scale_sets_error:
            raise self.list_scale_sets_error
        if self.scale_sets is not None:
            return list(self.scale_sets)
        return [
            self._scale_set(scale_set_name(region, sku.name), vmss_tags(sku.name, region))
            for sku in self.skus
            for region in GROUP_REGIONS
        ]

    def list_reservation_groups(self, rg):
        self.calls.append(("list_reservation_groups", rg))
        if self.list_groups_error:
            raise self.list_groups_error
        if self.reservation_groups is not None:
            return list(self.reservation_groups)
        return [
            ReservationGroupInfo(group_name(region), {"experiment": EXPERIMENT, "region": region})
            for region in GROUP_REGIONS
        ]

    def for_teardown(self, remaining_seconds):
        """Record the callable and return a proxy: a call through the proxy is a bounded call."""
        self.teardown_remaining.append(remaining_seconds)
        return BoundedProxy(self)

    def get_scale_set(self, rg, name):
        self.calls.append(("get_scale_set", rg, name))
        found = self._scale_set(name)
        return replace(found, capacity=None) if self.get_capacity_missing else found

    def update_scale_set(self, rg, name, sku_name, capacity, tags):
        self.calls.append(("update_scale_set", name, capacity, tags))
        if capacity == 0:
            if self.scale_in_ignored or (
                self.scale_in_lost_until is not None and self.clock.t < self.scale_in_lost_until
            ):
                return FakeOp(self.clock, self.scale_in_lost_until)  # lost: the capacity stays
            error = self.scale_in_op_error
            self.scale_set_capacity = 0
            self.leftovers = []
            for vm, (planned, _) in list(self.members.items()):
                if not planned.survives_scale_in and self._state(vm) != "failed":
                    del self.members[vm]
            return FakeOp(self.clock, self.clock.t, error)
        if self.scale_error:
            raise self.scale_error
        self.scale_set_capacity = capacity
        for index, planned in enumerate(self.plan[:capacity]):
            self.members[f"{name}_{index}"] = (planned, self.clock.t)
        done_at = None if self.scale_op_after is None else self.clock.t + self.scale_op_after
        return FakeOp(self.clock, done_at, self.scale_op_error)

    def _state(self, vm: str) -> str:
        planned, started = self.members[vm]
        if planned.final == "hang" or self.clock.t < started + planned.ready_after:
            return "pending"
        return planned.final

    def _code(self, vm: str) -> str | None:
        planned, started = self.members[vm]
        shown = self.clock.t >= started + planned.ready_after + planned.code_after
        return planned.code if shown else None

    def list_members(self, rg, scale_set_id):
        self.calls.append(("list_members",))
        if self.list_error:
            raise self.list_error
        out = list(self.leftovers)
        for vm in self.members:
            state = self._state(vm)
            out.append(
                Member(
                    id=f"/fake/virtualMachines/{vm}",
                    name=vm,
                    provisioning_state={"pending": "Creating", "placed": "Creating"}.get(
                        state, "Failed"
                    ),
                    power_state={"pending": "creating", "placed": "starting"}.get(state),
                    error_code=self._code(vm) if state == "failed" else None,
                    error_message=self.members[vm][0].message if state == "failed" else None,
                    os_disk_id=f"/fake/disks/{vm}_disk",
                    nic_ids=(f"/fake/networkInterfaces/{vm}_nic",),
                )
            )
        return out

    def delete_vm(self, rg, name):
        self.calls.append(("delete_vm", name))
        self.leftovers = [m for m in self.leftovers if m.name != name]
        if name in self.members and not self.members[name][0].undeletable:
            del self.members[name]
        return FakeOp(self.clock, self.clock.t)

    def tag_resource(self, resource_id, tags):
        self.calls.append(("tag_resource", resource_id))
        if self.tag_errors > 0:
            self.tag_errors -= 1
            raise AzureCallError("Conflict", "busy", status=409)
        self.tags[resource_id] = dict(tags)


def restriction(kind: str = "Location", zones: tuple[str, ...] = ()) -> SkuRestriction:
    """The shape of the Resource SKUs API: `values` and `restrictionInfo.locations` both name the
    region."""
    return SkuRestriction(kind, "NotAvailableForSubscription", ("eastus",), zones, ("eastus",))


def entry(**overrides: Any) -> dict[str, Any]:
    fields = dict(
        sku="Standard_B2s",
        region="eastus",
        zone=None,
        n=2,
        placement_group_mode="multi",
    )
    fields.update(overrides)
    return fields


def make_ctx(
    targets: list[dict[str, Any]],
    store: MemoryStore | None = None,
    run_id: str = RUN_ID,
    simulate: bool = False,
    **section: Any,
) -> ProbeContext:
    config = {
        "regions": ["eastus"],
        "spot_sizes": ["Standard_B2s"],
        "provisioning_probes": targets,
        "poll_interval_seconds": 5,
        "probe_timeout_seconds": 90,
        "teardown_timeout_seconds": 60,
    }
    config.update(section)

    def no_azure(kind: str) -> Any:
        raise AssertionError(f"the test must not build a real Azure client ({kind})")

    return ProbeContext(
        config=config,
        provider="azure",
        run_id=run_id,
        experiment=EXPERIMENT,
        store=store or MemoryStore(),
        clients=Clients(azure_factory=no_azure),
        now_fn=lambda: datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
        simulate=simulate,
    )


def wire(probe: Any, adapter: FakeAdapter) -> Any:
    """Point a probe at the fake adapter and its clock.

    An entry without `probe_type` takes the type of the probe that runs it (the pre-check takes
    `az_vmss_flex`), as the loader would set it from the file (ADR 0022). A test that needs
    another type sets `probe_type` in `entry()`.
    """
    probe.adapter_factory = lambda ctx: adapter
    probe.clock_factory = lambda: adapter.clock
    return type_entries(probe)


def type_entries(probe: Any) -> Any:
    """Make `probe.run` give each entry without `probe_type` the type of the probe."""
    run = probe.run

    def run_with_types(ctx: ProbeContext) -> Any:
        fallback = "az_vmss_flex" if probe.probe_type == "az_precheck" else probe.probe_type
        for target in ctx.config["provisioning_probes"]:
            target.setdefault("probe_type", fallback)
        return run(ctx)

    probe.run = run_with_types
    return probe
