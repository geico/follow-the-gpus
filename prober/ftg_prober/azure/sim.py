"""`local` mode of the Azure provisioning probes: scripted outcomes, no Azure call. Spec 0010
sec. 9.

`SimAdapter` has the methods of `AllocationAdapter`. The probes run the same code as in `cloud-gpu`
and `cloud-cpu` mode. The adapter plays 1 step of the entry's `sim_script` per probe run. A
`VirtualClock` replaces the wall clock, so that a `hang` step reaches the timeout at once.
"""

from typing import Any

from ftg_prober.azure.provisioning_adapter import (
    AzureCallError,
    Member,
    ReservationGroupInfo,
    ReservationInfo,
    ScaleSetInfo,
    SkuInfo,
    UsageInfo,
)

SIM_STEPS = ("success", "allocation_failed", "partial", "hang", "quota")
DEFAULT_SIM_SCRIPT = ("success", "partial", "allocation_failed", "hang", "quota")
SIM_FAMILY = "simFamily"
SIM_SCALE_SET = "sim-scale-set"
SIM_VCPUS = 2
SIM_QUOTA_VCPUS = 1000
# A member leaves `creating` this many virtual seconds after the scale-out request.
SIM_ALLOCATION_SECONDS = 2.0
QUOTA_MESSAGE = (
    "Operation results in exceeding quota limits of Core. "
    "Maximum allowed: 4, Current in use: 4, Additional requested: 2."
)


class VirtualClock:
    """A clock that moves only when the probe sleeps. No real wait."""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def script_of(target: dict[str, Any]) -> list[str]:
    """The entry's `sim_script`, else the default script. An empty script is the default."""
    script = target.get("sim_script")
    if not script:
        return list(DEFAULT_SIM_SCRIPT)
    if isinstance(script, str):
        return [script]
    return [str(step) for step in script]


class SimOperation:
    def __init__(self, clock: VirtualClock, done_at: float | None, error: AzureCallError | None):
        self.clock = clock
        self.done_at = done_at
        self._error = error

    def done(self) -> bool:
        return self.done_at is not None and self.clock.monotonic() >= self.done_at

    def error(self) -> AzureCallError | None:
        return self._error if self.done() else None


def _done_now(clock: VirtualClock) -> SimOperation:
    return SimOperation(clock, clock.monotonic(), None)


class SimAdapter:
    """The fault injection adapter. `step` is 1 of `SIM_STEPS`; the probe sets it per run."""

    def __init__(
        self,
        target: dict[str, Any],
        clock: VirtualClock,
        step: str = "success",
        experiment: str = "",
    ):
        self.target = target
        self.experiment = experiment
        self.clock = clock
        self.step = step
        self.calls: list[tuple[str, ...]] = []
        self.tags: dict[str, dict[str, str]] = {}
        self._reservation: ReservationInfo | None = None
        self._capacity = 0
        self._members: dict[str, dict[str, Any]] = {}
        self._scale_started = 0.0
        self._counter = 0

    def _check_step(self) -> None:
        if self.step not in SIM_STEPS:
            raise AzureCallError("SimUnknownStep", f"unknown sim step {self.step!r}")

    # Pre-check.

    def list_skus(self, region: str) -> list[SkuInfo]:
        self.calls.append(("list_skus", region))
        zone = self.target.get("zone")
        return [
            SkuInfo(
                name=self.target["sku"],
                family=SIM_FAMILY,
                vcpus=SIM_VCPUS,
                capacity_reservation_supported=self.target["probe_type"]
                == "az_capacity_reservation",
                zones=(str(zone),) if zone is not None else ("1", "2", "3"),
            )
        ]

    def list_usage(self, region: str) -> list[UsageInfo]:
        self.calls.append(("list_usage", region))
        return [
            UsageInfo("cores", "Total Regional vCPUs", 0, SIM_QUOTA_VCPUS),
            UsageInfo(SIM_FAMILY, "Sim Family vCPUs", 0, SIM_QUOTA_VCPUS),
        ]

    # Capacity reservation. A reservation succeeds or fails as a whole, thus `partial` fails.

    def create_reservation(self, rg, group, name, region, sku, zone, capacity, tags):
        self.calls.append(("create_reservation", name))
        self._check_step()
        if self.step == "quota":
            raise AzureCallError("OperationNotAllowed", QUOTA_MESSAGE, status=409)
        if self.step == "hang":
            self._reservation = ReservationInfo("Creating", capacity)
            return SimOperation(self.clock, None, None)
        done_at = self.clock.monotonic() + SIM_ALLOCATION_SECONDS
        if self.step in ("allocation_failed", "partial"):
            self._reservation = ReservationInfo("Failed", capacity)
            return SimOperation(self.clock, done_at, AzureCallError("AllocationFailed", "sim"))
        self._reservation = ReservationInfo("Succeeded", capacity)
        return SimOperation(self.clock, done_at, None)

    def get_reservation(self, rg, group, name):
        self.calls.append(("get_reservation", name))
        return self._reservation

    def delete_reservation(self, rg, group, name):
        self.calls.append(("delete_reservation", name))
        self._reservation = None
        return _done_now(self.clock)

    # Selection by tags: the simulated resource group holds the one resource that the entry needs.

    def list_reservation_groups(self, rg):
        self.calls.append(("list_reservation_groups", rg))
        tags = {"experiment": self.experiment, "region": self.target["region"].lower()}
        return [ReservationGroupInfo(name="sim-reservation-group", tags=tags)]

    def list_scale_sets(self, rg):
        self.calls.append(("list_scale_sets", rg))
        return [self._scale_set(SIM_SCALE_SET)]

    # Scale set.

    def get_scale_set(self, rg, name):
        self.calls.append(("get_scale_set", name))
        return self._scale_set(name)

    def _scale_set(self, name):
        return ScaleSetInfo(
            id=f"sim/virtualMachineScaleSets/{name}",
            name=name,
            capacity=self._capacity,
            sku_name=self.target["sku"],
            tags={
                "experiment": self.experiment,
                "sku": self.target["sku"],
                "region": self.target["region"].lower(),
            },
        )

    def update_scale_set(self, rg, name, sku_name, capacity, tags):
        self.calls.append(("update_scale_set", name, str(capacity)))
        if capacity == 0:
            self._capacity = 0
            # Scale-in removes the placed members. Failed members stay until deleted.
            for vm, member in list(self._members.items()):
                if self._state(member) != "failed":
                    del self._members[vm]
            return _done_now(self.clock)
        self._check_step()
        if self.step == "quota":
            raise AzureCallError("OperationNotAllowed", QUOTA_MESSAGE, status=409)
        self._capacity = capacity
        self._scale_started = self.clock.monotonic()
        placed = {"success": capacity, "partial": capacity // 2}.get(self.step, 0)
        for index in range(capacity):
            self._counter += 1
            vm = f"{name}_{self._counter:04d}"
            self._members[vm] = {"placed": index < placed}
        if self.step == "hang":
            return SimOperation(self.clock, None, None)
        error = None if self.step == "success" else AzureCallError("AllocationFailed", "sim")
        return SimOperation(self.clock, self._scale_started + SIM_ALLOCATION_SECONDS, error)

    def _state(self, member: dict[str, Any]) -> str:
        if (
            self.step == "hang"
            or self.clock.monotonic() < self._scale_started + SIM_ALLOCATION_SECONDS
        ):
            return "pending"
        return "placed" if member["placed"] else "failed"

    def list_members(self, rg, scale_set_id):
        self.calls.append(("list_members", scale_set_id))
        out = []
        for vm, member in self._members.items():
            state = self._state(member)
            out.append(
                Member(
                    id=f"sim/virtualMachines/{vm}",
                    name=vm,
                    provisioning_state={"pending": "Creating", "placed": "Creating"}.get(
                        state, "Failed"
                    ),
                    power_state={"pending": "creating", "placed": "starting"}.get(state),
                    error_code="AllocationFailed" if state == "failed" else None,
                    os_disk_id=f"sim/disks/{vm}_osdisk",
                    nic_ids=(f"sim/networkInterfaces/{vm}-nic",),
                )
            )
        return out

    def delete_vm(self, rg, name):
        self.calls.append(("delete_vm", name))
        self._members.pop(name, None)
        return _done_now(self.clock)

    def for_teardown(self, remaining_seconds):
        return self  # no SDK call to bound

    def tag_resource(self, resource_id, tags):
        self.calls.append(("tag_resource", resource_id))
        self.tags[resource_id] = dict(tags)
