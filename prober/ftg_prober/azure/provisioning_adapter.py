"""The 1 boundary between the Azure provisioning probes and the Azure SDK. Spec 0010 section 3.

The probes call only the `AllocationAdapter` methods. `AzureAllocationAdapter` maps them to the SDK.
`ftg_prober.azure.sim.SimAdapter` replays scripted outcomes with no Azure call. Tests use fakes.
The SDK is imported inside the calls, so that this module loads without the `azure` extra.
"""

import copy
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ftg_prober.log import kv, log_provider_error


class AzureCallError(Exception):
    """A failed Azure call, with the raw error code and the codes of the error details."""

    def __init__(
        self,
        code: str,
        message: str = "",
        details: tuple[str, ...] = (),
        status: int | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message
        self.details = details
        self.status = status

    @property
    def codes(self) -> tuple[str, ...]:
        """The top code, then the codes of the error details."""
        return (self.code, *self.details)


def log_azure_error(
    logger: logging.Logger, probe_type: str, region: str | None, err: AzureCallError, **fields: Any
) -> None:
    """1 log line for 1 failed Azure call: probe type, Region, code, status, and the message as
    Azure returned it. A row never holds the message."""
    log_provider_error(
        logger,
        probe_type=probe_type,
        region=region,
        code=err.code,
        status=err.status,
        message=err.message,
        **fields,
    )


@dataclass(frozen=True)
class SkuRestriction:
    type: str  # `Location` or `Zone`
    reason_code: str | None  # `QuotaId` or `NotAvailableForSubscription`
    locations: tuple[str, ...] = ()  # `restrictionInfo.locations`
    zones: tuple[str, ...] = ()  # `restrictionInfo.zones`
    values: tuple[str, ...] = ()  # `values`


@dataclass(frozen=True)
class SkuInfo:
    """1 VM size in 1 region, from the Resource SKUs API."""

    name: str
    family: str | None
    vcpus: int | None
    capacity_reservation_supported: bool
    zones: tuple[str, ...] = ()
    restrictions: tuple[SkuRestriction, ...] = ()


@dataclass(frozen=True)
class UsageInfo:
    """1 quota row of the Usage API."""

    name: str
    localized_name: str
    current: int
    limit: int


@dataclass(frozen=True)
class ScaleSetInfo:
    id: str
    name: str
    capacity: int | None  # None: the response has no `sku.capacity`; unknown, never 0
    sku_name: str
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReservationGroupInfo:
    name: str
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReservationInfo:
    provisioning_state: str | None
    capacity: int | None


@dataclass(frozen=True)
class Member:
    """1 VM of a Flexible scale set, with its instance view."""

    id: str
    name: str
    provisioning_state: str | None  # `Creating`, `Succeeded`, `Failed`, `Deleting`, ...
    power_state: str | None  # as Azure returns it: `creating`, `starting`, `running`, ...
    error_code: str | None = None  # from `ProvisioningState/failed/<code>`
    error_message: str | None = None  # the message of that status, as Azure returned it
    os_disk_id: str | None = None
    nic_ids: tuple[str, ...] = ()


class Operation(Protocol):
    """A long-running operation that the probe polls."""

    def done(self) -> bool: ...

    def error(self) -> AzureCallError | None:
        """The error of a done operation, else None."""
        ...


class AllocationAdapter(Protocol):
    def list_skus(self, region: str) -> list[SkuInfo]: ...

    def list_usage(self, region: str) -> list[UsageInfo]: ...

    def create_reservation(
        self,
        rg: str,
        group: str,
        name: str,
        region: str,
        sku: str,
        zone: str | None,
        capacity: int,
        tags: dict[str, str],
    ) -> Operation: ...

    def get_reservation(self, rg: str, group: str, name: str) -> ReservationInfo | None: ...

    def delete_reservation(self, rg: str, group: str, name: str) -> Operation: ...

    def list_reservation_groups(self, rg: str) -> list[ReservationGroupInfo]: ...

    def list_scale_sets(self, rg: str) -> list[ScaleSetInfo]: ...

    def get_scale_set(self, rg: str, name: str) -> ScaleSetInfo: ...

    def update_scale_set(
        self, rg: str, name: str, sku_name: str, capacity: int, tags: dict[str, str] | None
    ) -> Operation: ...

    def list_members(self, rg: str, scale_set_id: str) -> list[Member]: ...

    def delete_vm(self, rg: str, name: str) -> Operation: ...

    def tag_resource(self, resource_id: str, tags: dict[str, str]) -> None: ...

    def for_teardown(self, remaining_seconds: Callable[[], float]) -> "AllocationAdapter": ...


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class RealClock:
    """The wall clock of a `cloud-gpu` or `cloud-cpu` call."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def to_call_error(exc: BaseException) -> AzureCallError:
    """Read the code, the message, and the detail codes of an SDK error."""
    if isinstance(exc, AzureCallError):
        return exc
    err = getattr(exc, "error", None)
    code = getattr(err, "code", None) or type(exc).__name__
    message = getattr(err, "message", None) or str(exc)
    details = tuple(
        d.code for d in (getattr(err, "details", None) or []) if getattr(d, "code", None)
    )
    return AzureCallError(code, message, details, getattr(exc, "status_code", None))


class PollerOperation:
    """Wraps an SDK `LROPoller`. Never blocks: `error()` reads the result only when done."""

    def __init__(self, poller: Any) -> None:
        self._poller = poller
        self._error: AzureCallError | None = None
        self._read = False

    def done(self) -> bool:
        return bool(self._poller.done())

    def error(self) -> AzureCallError | None:
        if not self.done():
            return None
        if not self._read:
            self._read = True
            try:
                self._poller.result()
            except Exception as exc:  # noqa: BLE001 - every SDK failure becomes an error code
                self._error = to_call_error(exc)
        return self._error


def _is_azure_error(exc: Exception) -> bool:
    """True for an `azure.core.exceptions.AzureError` (HTTP error, connection reset, timeout)."""
    try:
        from azure.core.exceptions import AzureError
    except ImportError:  # the `azure` extra is not installed: no SDK error can occur
        return False
    return isinstance(exc, AzureError)


def _is_deserialization_error(exc: Exception) -> bool:
    """True for `azure.core` `DeserializationError`: a `ValueError`, not an `AzureError`."""
    try:
        from azure.core.exceptions import DeserializationError
    except ImportError:
        return False
    return isinstance(exc, DeserializationError)


def _sdk_call(fn: Callable[[], Any]) -> Any:
    """Run 1 SDK call. Turn an Azure SDK error into AzureCallError.

    Every `azure.core` `AzureError` converts, also `ServiceRequestError` and
    `ServiceResponseError` (a connection reset or a timeout). They have no `status_code` and no
    `error`: the code is the type name, and the message is the provider's, as returned. The
    teardown loops catch only AzureCallError, so a raw SDK error would end a teardown early.
    A `DeserializationError` (a `ValueError`: a body that does not match the model) converts too,
    with its message as returned. Any other exception passes through, so a bug stays loud.
    An exception with `status_code` or `error` converts too, so that fake SDK errors work.
    """
    try:
        return fn()
    except AzureCallError:
        raise
    except Exception as exc:
        if (
            _is_azure_error(exc)
            or _is_deserialization_error(exc)
            or hasattr(exc, "status_code")
            or hasattr(exc, "error")
        ):
            raise to_call_error(exc) from exc
        raise


def log_teardown_error(
    logger: logging.Logger,
    probe_type: str,
    region: str | None,
    exc: Exception,
    event: str = "teardown.unexpected_error",
    **fields: Any,
) -> str:
    """Log 1 exception of a teardown call and return its code for the teardown `errors` list.
    A read before the scale-out uses it too, with its own `event`.

    A teardown call can raise any exception (for example a `JSONDecodeError` from an HTML page
    that a proxy returned with status 200). The teardown must still release the capacity, so the
    loops catch `Exception`. An AzureCallError logs as `log_azure_error` does. Any other exception
    logs at ERROR with its type, its message as returned, and the traceback, so a bug stays loud.
    """
    if isinstance(exc, AzureCallError):
        log_azure_error(logger, probe_type, region, exc, **fields)
        return exc.code
    logger.error(
        "%s %s",
        event,
        kv(
            probe_type=probe_type,
            region=region,
            type=type(exc).__name__,
            message=str(exc),
            **fields,
        ),
        exc_info=exc,
    )
    return type(exc).__name__


def _raw(value: Any) -> str:
    """The raw string of an SDK enum member: `.value`. `str()` of a str enum gives its name."""
    return str(getattr(value, "value", value))


def member_from_vm(vm: Any) -> Member:
    """Read the states and the attached resource IDs of 1 VM model with its instance view."""
    power = None
    error_code = None
    error_message = None
    statuses = getattr(getattr(vm, "instance_view", None), "statuses", None) or []
    for status in statuses:
        code = getattr(status, "code", None)
        if not code:
            continue
        parts = code.split("/")
        if parts[0] == "PowerState" and len(parts) > 1:
            power = parts[1]
        elif parts[0] == "ProvisioningState" and len(parts) > 2 and parts[1].lower() == "failed":
            error_code = parts[2]
            error_message = getattr(status, "message", None)
    storage = getattr(vm, "storage_profile", None)
    os_disk = getattr(storage, "os_disk", None)
    managed = getattr(os_disk, "managed_disk", None)
    network = getattr(vm, "network_profile", None)
    nics = tuple(
        n.id for n in (getattr(network, "network_interfaces", None) or []) if getattr(n, "id", None)
    )
    return Member(
        id=vm.id,
        name=vm.name,
        provisioning_state=getattr(vm, "provisioning_state", None),
        power_state=power,
        error_code=error_code,
        error_message=error_message,
        os_disk_id=getattr(managed, "id", None),
        nic_ids=nics,
    )


def _scale_set_info(found: Any) -> ScaleSetInfo:
    """A scale set of the resource group may have no `sku` (another team's, or a partial
    response): it must not fail the list. The probe selects by tags and ignores it."""
    sku = getattr(found, "sku", None)
    capacity = getattr(sku, "capacity", None)
    return ScaleSetInfo(
        id=found.id,
        name=found.name,
        capacity=None if capacity is None else int(capacity),
        sku_name=getattr(sku, "name", None),
        tags=dict(found.tags or {}),
    )


TEARDOWN_CALL_FLOOR_SECONDS = (
    10  # the least timeout of a teardown call, however little budget is left
)


class AzureAllocationAdapter:
    """The real adapter: the compute client and the Resource Management client of the core."""

    def __init__(self, compute: Any, resources_fn: Callable[[], Any]) -> None:
        self.compute = compute
        # The Resource Management client comes from the client factory of the core, which builds
        # it once and owns it (like the compute client): an adapter, or a `for_teardown` copy of
        # it, never builds a client of its own, so no per-run session leaks; the factory's client
        # lives for the process.
        self._resources_fn = resources_fn
        self._remaining: Callable[[], float] | None = None

    def for_teardown(self, remaining_seconds: Callable[[], float]) -> "AzureAllocationAdapter":
        """A copy for the scale set teardown only: its get, update, member list, VM delete, and
        tag call (and the pollers of the update, the delete, and the tag) carry a timeout and no
        retry. The scale-out and the reads before it use the original, with the `azure.core`
        defaults. The copy shares the Resource Management client of the original."""
        bounded = copy.copy(self)
        bounded._remaining = remaining_seconds
        return bounded

    def _bounded(self) -> dict[str, Any]:
        """Per-request options of a teardown call: a connect timeout and a read timeout of
        max(remaining teardown budget, `TEARDOWN_CALL_FLOOR_SECONDS`), and no retry. `azure.core`
        honors the 3 keys for each request; the defaults are 300 s each, and `RetryPolicy` allows
        10 retries in all. The remaining budget is read when the call starts."""
        if self._remaining is None:
            return {}
        timeout = max(self._remaining(), TEARDOWN_CALL_FLOOR_SECONDS)
        return {"connection_timeout": timeout, "read_timeout": timeout, "retry_total": 0}

    def _tags_client(self) -> Any:
        return self._resources_fn().tags

    def list_skus(self, region: str) -> list[SkuInfo]:
        found = _sdk_call(
            lambda: list(self.compute.resource_skus.list(filter=f"location eq '{region}'"))
        )
        out = []
        for sku in found:
            if (getattr(sku, "resource_type", "") or "").lower() != "virtualmachines":
                continue
            caps = {c.name: c.value for c in (sku.capabilities or [])}
            zones: tuple[str, ...] = ()
            for info in sku.location_info or []:
                if (info.location or "").lower() == region.lower():
                    zones = tuple(info.zones or ())
            restrictions = tuple(
                SkuRestriction(
                    type=_raw(r.type),
                    reason_code=_raw(r.reason_code) if r.reason_code else None,
                    locations=tuple(getattr(r.restriction_info, "locations", None) or ()),
                    zones=tuple(getattr(r.restriction_info, "zones", None) or ()),
                    values=tuple(r.get("values") or ()),  # `r.values` is the Mapping method
                )
                for r in (sku.restrictions or [])
            )
            vcpus = caps.get("vCPUs")
            out.append(
                SkuInfo(
                    name=sku.name,
                    family=sku.family,
                    vcpus=int(vcpus) if vcpus and str(vcpus).isdigit() else None,
                    capacity_reservation_supported=str(
                        caps.get("CapacityReservationSupported", "False")
                    ).lower()
                    == "true",
                    zones=zones,
                    restrictions=restrictions,
                )
            )
        return out

    def list_usage(self, region: str) -> list[UsageInfo]:
        found = _sdk_call(lambda: list(self.compute.usage.list(location=region)))
        return [
            UsageInfo(
                name=u.name.value or "",
                localized_name=u.name.localized_value or "",
                current=int(u.current_value or 0),
                limit=int(u.limit or 0),
            )
            for u in found
        ]

    def create_reservation(self, rg, group, name, region, sku, zone, capacity, tags):
        body: dict[str, Any] = {
            "location": region,
            "sku": {"name": sku, "capacity": capacity},
            "tags": tags,
        }
        if zone is not None:
            body["zones"] = [zone]
        return PollerOperation(
            _sdk_call(
                lambda: self.compute.capacity_reservations.begin_create_or_update(
                    rg, group, name, body
                )
            )
        )

    def get_reservation(self, rg, group, name):
        try:
            found = _sdk_call(lambda: self.compute.capacity_reservations.get(rg, group, name))
        except AzureCallError as exc:
            if exc.status == 404:
                return None
            raise
        sku = getattr(found, "sku", None)
        return ReservationInfo(
            provisioning_state=getattr(found, "provisioning_state", None),
            capacity=getattr(sku, "capacity", None),
        )

    def delete_reservation(self, rg, group, name):
        return PollerOperation(
            _sdk_call(lambda: self.compute.capacity_reservations.begin_delete(rg, group, name))
        )

    def list_reservation_groups(self, rg):
        found = _sdk_call(
            lambda: list(self.compute.capacity_reservation_groups.list_by_resource_group(rg))
        )
        return [ReservationGroupInfo(name=g.name, tags=dict(g.tags or {})) for g in found]

    def list_scale_sets(self, rg):
        found = _sdk_call(lambda: list(self.compute.virtual_machine_scale_sets.list(rg)))
        return [_scale_set_info(vmss) for vmss in found]

    def get_scale_set(self, rg, name):
        return _scale_set_info(
            _sdk_call(
                lambda: self.compute.virtual_machine_scale_sets.get(rg, name, **self._bounded())
            )
        )

    def update_scale_set(self, rg, name, sku_name, capacity, tags):
        body: dict[str, Any] = {"sku": {"name": sku_name, "capacity": capacity}}
        if tags is not None:
            body["tags"] = tags
        return PollerOperation(
            _sdk_call(
                lambda: self.compute.virtual_machine_scale_sets.begin_update(
                    rg, name, body, **self._bounded()
                )
            )
        )

    def list_members(self, rg, scale_set_id):
        found = _sdk_call(
            lambda: list(
                self.compute.virtual_machines.list(
                    rg,
                    filter=f"'virtualMachineScaleSet/id' eq '{scale_set_id}'",
                    expand="instanceView",
                    **self._bounded(),
                )
            )
        )
        return [member_from_vm(vm) for vm in found]

    def delete_vm(self, rg, name):
        return PollerOperation(
            _sdk_call(
                lambda: self.compute.virtual_machines.begin_delete(rg, name, **self._bounded())
            )
        )

    def tag_resource(self, resource_id, tags):
        _sdk_call(
            lambda: self._tags_client()
            .begin_update_at_scope(
                resource_id,
                {"operation": "Merge", "properties": {"tags": tags}},
                **self._bounded(),
            )
            .result()
        )


def make_real_adapter(ctx: Any) -> AzureAllocationAdapter:
    """The adapter of a `cloud-gpu` or `cloud-cpu` call, built on the client factory of the core."""
    # Both clients are taken here, before any teardown budget starts: a later `clients.azure`
    # call would take the `Clients` lock, which another lane can hold while it builds a client,
    # outside the timeout of the tag call.
    resources = ctx.clients.azure("resources")
    return AzureAllocationAdapter(
        compute=ctx.clients.azure("compute"),
        resources_fn=lambda: resources,
    )
