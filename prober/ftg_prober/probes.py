"""The probe plugin contract and the loader. Spec 0003 section 5."""

import importlib
import inspect
import logging
import os
import pkgutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ftg_prober.awsclient import DEFAULT_ATTEMPTS, boto3_client
from ftg_prober.callqueue import Call
from ftg_prober.record import Record, format_ts
from ftg_prober.store import Store, raw_key

log = logging.getLogger(__name__)

PROBE_PACKAGES = {"aws": "ftg_prober.aws", "azure": "ftg_prober.azure"}
PROBE_RG_ENV = "FTG_PROBE_RG"
SUBSCRIPTION_ENVS = ("AZURE_SUBSCRIPTION_ID", "ARM_SUBSCRIPTION_ID")


class Clients:
    """Lazy SDK clients. Each SDK uses its default credential chain. Tests inject factories."""

    def __init__(
        self,
        boto3_factory: Callable[[str, str], Any] | None = None,
        azure_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._custom_boto3 = boto3_factory is not None
        self._boto3_factory = boto3_factory or boto3_client
        self._azure_factory = azure_factory or self._default_factory
        self._cache: dict[tuple[str, ...], Any] = {}
        # The Region lanes (spec 0003, section 6B) ask for clients on several threads: each
        # client is built once, under this lock.
        self._lock = threading.RLock()

    def _default_factory(self, kind: str) -> Any:
        """The default Azure factory: every kind shares the one `credential` client. It runs under
        the lock of `azure`, which is an RLock, so the nested `credential` build does not block."""
        credential = self.azure("credential") if kind != "credential" else None
        return _default_azure(kind, credential)

    def boto3(self, service: str, region: str, max_attempts: int = DEFAULT_ATTEMPTS) -> Any:
        """A boto3 client for `service` in `region`, for example `("ec2", "us-east-1")`.

        `max_attempts` is the botocore retry limit. A probe whose calls the queue paces uses 1:
        the queue owns the throttle handling (spec 0008, 3.3). A test factory takes 2 arguments.
        """
        key = ("boto3", service, region, str(max_attempts))
        with self._lock:
            if key not in self._cache:
                if self._custom_boto3:
                    self._cache[key] = self._boto3_factory(service, region)
                else:
                    self._cache[key] = boto3_client(service, region, max_attempts)
            return self._cache[key]

    def azure(self, kind: str) -> Any:
        """An Azure client: `credential`, `compute`, `resourcegraph`, or `resources`."""
        key = ("azure", kind)
        with self._lock:
            if key not in self._cache:
                self._cache[key] = self._azure_factory(kind)
            return self._cache[key]


def _default_azure(kind: str, credential: Any = None) -> Any:
    if kind == "credential":
        from azure.identity import DefaultAzureCredential

        return DefaultAzureCredential()
    if kind == "compute":
        from azure.mgmt.compute import ComputeManagementClient

        return ComputeManagementClient(credential, azure_subscription_id())
    if kind == "resourcegraph":
        from azure.mgmt.resourcegraph import ResourceGraphClient

        return ResourceGraphClient(credential)
    if kind == "resources":
        from azure.mgmt.resource.resources import ResourceManagementClient

        return ResourceManagementClient(credential, azure_subscription_id())
    raise ValueError(
        f"unknown Azure client kind {kind!r}; use credential, compute, resourcegraph, or resources"
    )


def azure_subscription_id() -> str:
    for name in SUBSCRIPTION_ENVS:
        if os.environ.get(name):
            return os.environ[name]
    raise RuntimeError(
        f"set {SUBSCRIPTION_ENVS[0]} or {SUBSCRIPTION_ENVS[1]} for the Azure clients"
    )


@dataclass
class ProbeContext:
    """What a probe gets for 1 call."""

    config: dict[str, Any]
    provider: str
    run_id: str
    experiment: str
    store: Store
    clients: Clients = field(default_factory=Clients)
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC)
    # Whether this call is simulated: the run-wide `--simulate` flag, or the current job's own
    # `simulate: true` key (ADR 0011 sections 2 and 3). The scheduler resolves this before the
    # call; `simulated()` adds a per-target `simulate` key on top, for a provisioning probe.
    simulate: bool = False
    # The API requests of the call, pages included. The queue takes 1 token for each request.
    api_calls: int = 0
    # Set by the scheduler for a Region lane (spec 0003, section 6B): `stop_fn` turns true when
    # the process stops, and `emit_fn` writes the rows of 1 finished entry at once.
    stop_fn: Callable[[], bool] = lambda: False
    emit_fn: Callable[[list[Record]], None] | None = None

    def now(self) -> datetime:
        """The current time, time zone aware, in UTC."""
        return self.now_fn()

    def timestamp(self) -> str:
        """`now()` as the `ts_utc` string of a record."""
        return format_ts(self.now())

    def simulated(self, target: dict[str, Any] | None = None) -> bool:
        """Whether a call is simulated: the run/job-level flag, or the target's own `simulate`."""
        return self.simulate or bool(target and target.get("simulate"))

    def stopping(self) -> bool:
        """True when the process stops. A probe that loops over entries starts no new entry."""
        return self.stop_fn()

    def emit(self, rows: list[Record]) -> list[Record]:
        """Hand the rows of 1 finished entry to the scheduler, which writes them at once.

        Return the rows that `run` must still return: none when the scheduler wrote them, all of
        them when the context has no writer (a direct call, for example in a test).
        """
        if self.emit_fn is None:
            return rows
        if rows:
            self.emit_fn(rows)
        return []

    def cadence(self, probe_type: str) -> str | None:
        """The configured cadence string of a probe type, or None."""
        from ftg_prober.config import cadence_key

        return (self.config.get("cadence") or {}).get(cadence_key(probe_type))

    def put_raw(self, probe_type: str, data: bytes) -> str:
        """Write a raw feed object under the spec 4.8 key. Return the key."""
        key = raw_key(self.experiment, self.provider, probe_type, self.now())
        self.store.put(key, data)
        return key


class Probe:
    """One probe. A subclass sets the 3 class fields and `run`."""

    probe_type: str
    provider: str
    default_cadence: str

    def run(self, ctx: ProbeContext) -> list[Record]:
        raise NotImplementedError

    # A lane probe reads the `provisioning_probes` entries. The scheduler runs it off its own
    # thread, in 1 lane per Region, 1 lane at a time per Region (spec 0003, section 6B).
    region_lanes: bool = False

    # A probe with a `plan` is queued: the scheduler runs the calls of the plan one by one from a
    # queue (spec 0003, 6A). `throttle_code` is the error code of a call to put back on the queue.
    throttle_code: str | None = None

    def plan(self, config: dict[str, Any]) -> list[Call] | None:
        """The calls of 1 cycle, or None for a probe that runs all at once."""
        return None

    def run_call(self, ctx: ProbeContext, call: Call) -> list[Record]:
        """The rows of 1 call of `plan`. A probe with a plan must define it."""
        raise NotImplementedError

    def follow_up(self, call: Call, rows: list[Record]) -> list[Call]:
        """Calls to add to the queue after `call` wrote `rows`. For example a search step."""
        return []

    @property
    def name(self) -> str:
        return self.probe_type


def is_probe_class(obj: Any) -> bool:
    """A concrete Probe subclass: not Probe itself, and with the 3 class fields set."""
    return (
        inspect.isclass(obj)
        and issubclass(obj, Probe)
        and obj is not Probe
        and all(
            isinstance(getattr(obj, f, None), str)
            for f in ("probe_type", "provider", "default_cadence")
        )
    )


def load_probes(packages: tuple[str, ...] | list[str]) -> list[Probe]:
    """Import each module under the packages and return 1 instance of each Probe subclass."""
    found: dict[str, Probe] = {}
    for package_name in packages:
        package = importlib.import_module(package_name)
        # walk_packages imports each subpackage to find its modules. Without onerror it
        # skips a subpackage whose import fails. A broken probe package must fail loudly.
        for info in pkgutil.walk_packages(
            package.__path__, prefix=package.__name__ + ".", onerror=_raise_import_error
        ):
            module = importlib.import_module(info.name)
            for _, obj in inspect.getmembers(module, is_probe_class):
                if obj.__module__ != module.__name__:
                    continue
                probe = obj()
                if probe.probe_type in found:
                    raise RuntimeError(
                        f"probe_type {probe.probe_type!r} is defined twice: "
                        f"{type(found[probe.probe_type]).__module__} and {module.__name__}"
                    )
                found[probe.probe_type] = probe
                log.info("probe.loaded probe_type=%s module=%s", probe.probe_type, module.__name__)
    return list(found.values())


def _raise_import_error(name: str) -> None:
    raise ImportError(f"cannot import probe package {name!r}")


def packages_for(provider: str) -> tuple[str, ...]:
    return (PROBE_PACKAGES[provider],)
