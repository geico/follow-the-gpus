"""Azure pre-checks (spec 0002 section 4.3) and the parts that the 2 provisioning probes share.

Spec 0010 sections 2, 4, and 5. The pre-check reads the Resource SKUs API and the Usage API
for each `provisioning_probes` entry. It gives the restrictions and the N ceiling. The probe
`az_precheck` returns 1 row per entry. Each provisioning probe runs the same pre-check for its
own entries only: the entries that the file lists under its probe type (ADR 0022). Each probe
selects the Azure resource that it changes by tags, never by a computed name.
"""

import logging
import os
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any

from ftg_prober.azure import sim
from ftg_prober.azure.provisioning_adapter import (
    AllocationAdapter,
    AzureCallError,
    Clock,
    RealClock,
    SkuRestriction,
    UsageInfo,
    log_azure_error,
    log_teardown_error,
    make_real_adapter,
)
from ftg_prober.config import up_steps
from ftg_prober.log import kv
from ftg_prober.probes import PROBE_RG_ENV, Probe, ProbeContext
from ftg_prober.record import Record

log = logging.getLogger(__name__)

DEFAULT_PROBE_RG = "ftg-probe"
DEFAULT_PREFIX = "ftg"
# Usage row of the regional vCPU quota. `az vm list-usage` shows it as "Total Regional vCPUs".
TOTAL_VCPU_USAGE_NAMES = ("cores", "total regional vcpus")

# Probe timeout + teardown timeout must stay 30 s or more under the sweeper threshold
# FTG_MAX_AGE_SECONDS of 180 s (spec 0010, section 10, decision Z17).
DEFAULTS = {
    "probe_timeout_seconds": 90,
    "poll_interval_seconds": 5,
    "teardown_timeout_seconds": 60,
}


@dataclass(frozen=True)
class AllocationSettings:
    probe_timeout_seconds: int
    poll_interval_seconds: int
    teardown_timeout_seconds: int
    # Set by the halving search for a real entry: the entry deadline on the clock of the attempt
    # and the shortest probe window worth an attempt. `allocate` checks them again after its
    # pre-teardown, because only `allocate` knows whether a pre-teardown ran.
    call_deadline: float | None = None
    min_probe_window: float = 0.0


def allocation_settings(section: dict[str, Any]) -> AllocationSettings:
    """Read the 3 timing keys of the `azure` section. Each must be an integer of 1 or more."""
    values = {}
    for key, default in DEFAULTS.items():
        value = section.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"azure.{key} must be an integer of 1 or more, not {value!r}")
        values[key] = value
    return AllocationSettings(**values)


def probe_rg() -> str:
    """The probe resource group: `FTG_PROBE_RG`, else `ftg-probe`."""
    return os.environ.get(PROBE_RG_ENV) or DEFAULT_PROBE_RG


def name_prefix(rg: str) -> str:
    """The name prefix of the stack: the resource group name without `-probe` (spec 0005, 4.2)."""
    return rg[: -len("-probe")] if rg.endswith("-probe") and len(rg) > 6 else DEFAULT_PREFIX


def sku_slug(sku: str) -> str:
    """Drop `Standard_`, replace `_` with `-`, lower case (spec 0005, 4.2)."""
    return re.sub(r"^standard_", "", sku, flags=re.IGNORECASE).replace("_", "-").lower()


def zone_of(target: dict[str, Any]) -> str | None:
    zone = target.get("zone")
    return None if zone is None else str(zone)


def vmss_target_tag(target: dict[str, Any]) -> str:
    """`<sku slug>-<region>-<placement group mode>`: the scale set name without the prefix. The
    mode is `multi` (spec 0010, section 4.1)."""
    return f"{sku_slug(target['sku'])}-{target['region'].lower()}-{target['placement_group_mode']}"


def reservation_target_tag(target: dict[str, Any]) -> str:
    """`<sku slug>-<region>`, plus `-z<zone>` for a zonal entry."""
    zone = zone_of(target)
    base = f"{sku_slug(target['sku'])}-{target['region'].lower()}"
    return f"{base}-z{zone}" if zone else base


def probe_tags(experiment: str, target_tag: str, run_id: str) -> dict[str, str]:
    """The 3 tags of each probe resource. The sweeper finds resources by `experiment`."""
    return {"experiment": experiment, "target": target_tag, "run_id": run_id}


# --- Series rule: 1 probe at a time per (region, SKU) in this process (spec 0002, 4.5). ---

_SERIES_GUARD = threading.Lock()
_SERIES_LOCKS: dict[tuple[str, str], threading.Lock] = {}


@contextmanager
def series_lock(region: str, sku: str) -> Iterator[None]:
    """Hold the lock of (region, SKU). Both provisioning probes use the same locks."""
    key = (region.lower(), sku.lower())
    with _SERIES_GUARD:
        lock = _SERIES_LOCKS.setdefault(key, threading.Lock())
    with lock:
        yield


# --- Pre-check. ---


@dataclass
class Precheck:
    outcome: str  # `success`, `restricted`, or `error`
    n_ceiling: int | None
    error_code: str | None
    latency_ms: int
    details: dict[str, Any] = field(default_factory=dict)


def _usage_row(usages: list[UsageInfo], match: Callable[[UsageInfo], bool]) -> UsageInfo | None:
    return next((usage for usage in usages if match(usage)), None)


def _restriction_raw(restriction: SkuRestriction) -> dict[str, Any]:
    """1 SKU restriction as returned (ADR 0006). `locations` and `zones` are `restrictionInfo`."""
    return {
        "type": restriction.type,
        "reason_code": restriction.reason_code,
        "values": list(restriction.values),
        "locations": list(restriction.locations),
        "zones": list(restriction.zones),
    }


def run_precheck(
    adapter: AllocationAdapter,
    target: dict[str, Any],
    clock: Clock,
    probe_type: str = "az_precheck",
) -> Precheck:
    """Resource SKUs, then quota, for 1 entry. Never raises for an Azure error.

    A failed call logs 1 line with the message as Azure returned it. The row never holds it.
    Any other exception (a 200 body that is not JSON) gives an `error` row too: its code is the
    type name (spec 0010, section 4).
    """
    started = clock.monotonic()
    region = target["region"]
    zone = zone_of(target)

    def done(outcome, ceiling=None, code=None, **details) -> Precheck:
        latency = int((clock.monotonic() - started) * 1000)
        details = {"n_ceiling": ceiling, **details}
        return Precheck(outcome, ceiling, code, latency, details)

    try:
        skus = adapter.list_skus(region)
    except AzureCallError as exc:
        log_azure_error(log, probe_type, region, exc, call="list_skus")
        return done("error", code=exc.code, reason="Resource SKUs call failed")
    except Exception as exc:  # noqa: BLE001 - a malformed 200 body is an error row, not a crash
        code = log_teardown_error(
            log, probe_type, region, exc, event="allocation.unexpected_error", call="list_skus"
        )
        return done("error", code=code, reason="Resource SKUs call failed")
    sku = next((s for s in skus if s.name.lower() == target["sku"].lower()), None)
    if sku is None:
        return done("restricted", reason="size not offered in the region")

    facts = {
        "capacity_reservation_supported": sku.capacity_reservation_supported,
        "family": sku.family,
        "vcpus": sku.vcpus,
        "sku_zones": list(sku.zones),
        "restrictions": [_restriction_raw(r) for r in sku.restrictions],
    }

    for restriction in sku.restrictions:
        kind = restriction.type.lower()
        locations = restriction.locations or restriction.values
        in_region = not locations or region.lower() in (loc.lower() for loc in locations)
        hit = (kind == "location" and in_region) or (
            kind == "zone" and zone is not None and zone in restriction.zones and in_region
        )
        if hit:
            return done(
                "restricted",
                code=restriction.reason_code,
                reason=f"{restriction.type} restriction",
                **facts,
            )
    if zone is not None and sku.zones and zone not in sku.zones:
        return done("restricted", reason="size not offered in the zone", **facts)

    try:
        usages = adapter.list_usage(region)
    except AzureCallError as exc:
        log_azure_error(log, probe_type, region, exc, call="list_usage")
        return done("error", code=exc.code, reason="Usage call failed", **facts)
    except Exception as exc:  # noqa: BLE001 - a malformed 200 body is an error row, not a crash
        code = log_teardown_error(
            log, probe_type, region, exc, event="allocation.unexpected_error", call="list_usage"
        )
        return done("error", code=code, reason="Usage call failed", **facts)
    family = (sku.family or "").lower()
    family_row = _usage_row(usages, lambda u: bool(family) and u.name.lower() == family)
    total_row = _usage_row(
        usages,
        lambda u: (
            u.name.lower() in TOTAL_VCPU_USAGE_NAMES
            or u.localized_name.lower() in TOTAL_VCPU_USAGE_NAMES
        ),
    )
    family_free = family_row.limit - family_row.current if family_row else None
    total_free = total_row.limit - total_row.current if total_row else None
    known = [v for v in (family_free, total_free) if v is not None]
    ceiling = max(0, min(known)) // sku.vcpus if known and sku.vcpus else None
    return done(
        "success",
        ceiling,
        family_current_vcpus=family_row.current if family_row else None,
        family_limit_vcpus=family_row.limit if family_row else None,
        total_current_vcpus=total_row.current if total_row else None,
        total_limit_vcpus=total_row.limit if total_row else None,
        family_available_vcpus=family_free,
        total_available_vcpus=total_free,
        **facts,
    )


# --- Select a resource by its tags (ADR 0022). ---


def select_by_tags(
    lister: Callable[[], list[Any]],
    match: dict[str, str],
    noun: str,
    probe_type: str,
    region: str,
) -> tuple[Any | None, "Allocation | None"]:
    """List the resources in the probe resource group and keep those whose tags equal `match`
    (compared in lower case). Exactly 1 match gives `(resource, None)`. No match, more than 1
    match, or a failed list gives `(None, Allocation)` with outcome `error` and the reason in
    `details`. This function never writes to Azure and never computes a name.

    `noun` is `scale set` or `reservation group`. `details.match` is the tags that were
    selected on; `details.<noun>s` lists the names of the matches.
    """
    listed = noun.replace(" ", "_") + "s"
    base = {"match": dict(match)}
    try:
        found = lister()
    except Exception as exc:  # noqa: BLE001 - a malformed 200 body is an error row, not a crash
        code = log_teardown_error(
            log, probe_type, region, exc, event="allocation.unexpected_error", call=f"list_{listed}"
        )
        return None, Allocation(
            "error", None, code, 0, {**base, listed: [], "reason": f"{noun}s not listable"}
        )
    wanted = {k: v.lower() for k, v in match.items()}
    chosen = [
        item
        for item in found
        if all((item.tags.get(k) or "").lower() == v for k, v in wanted.items())
    ]
    if len(chosen) == 1:
        return chosen[0], None
    names = sorted(item.name for item in chosen)
    reason = (
        f"no {noun} for this experiment entry"
        if not chosen
        else f"ambiguous: {len(chosen)} {noun}s match this experiment entry"
    )
    return None, Allocation("error", None, None, 0, {**base, listed: names, "reason": reason})


# --- Shared probe machinery. ---


@dataclass
class Allocation:
    """The result of 1 allocation attempt and its teardown."""

    outcome: str
    n_placed: int | None
    error_code: str | None
    latency_ms: int
    details: dict[str, Any] = field(default_factory=dict)


def make_row(
    ctx: ProbeContext,
    probe_type: str,
    target: dict[str, Any],
    *,
    simulated: bool,
    placement_group_mode: str,
    n_requested: int,
    n_placed: int | None,
    outcome: str,
    error_code: str | None,
    latency_ms: int,
    details: dict[str, Any],
) -> Record:
    return Record(
        ts_utc=ctx.timestamp(),
        run_id=ctx.run_id,
        simulated=simulated,
        provider="azure",
        region=target["region"],
        zone=zone_of(target),
        probe_type=probe_type,
        sku=target["sku"],
        placement_group_mode=placement_group_mode,
        n_requested=n_requested,
        n_placed=n_placed,
        outcome=outcome,
        error_code=error_code,
        latency_ms=latency_ms,
        details=details,
    )


def up_ceiling(target: dict[str, Any], ceiling: int | None) -> int | None:
    """U of ADR 0023: `min(n_max, quota ceiling)`, or `None` when the entry has no `n_max`."""
    if "n_max" not in target:
        return None
    n_max = int(target["n_max"])
    return n_max if ceiling is None else min(n_max, ceiling)


@dataclass
class EntryBudget:
    """The deadline of the search of 1 real entry, shared by its precheck and its attempts."""

    seconds: float
    deadline: float | None = None  # set when a real entry starts
    longest: float = 0.0  # the longest probe so far: a later attempt needs a window as long

    def window(self, settings: "AllocationSettings", clock: Clock) -> float:
        """The probe window now: the time left to the deadline minus the teardown timeout."""
        assert self.deadline is not None
        return self.deadline - clock.monotonic() - settings.teardown_timeout_seconds

    def too_short(self, settings: "AllocationSettings", clock: Clock) -> bool:
        """True when a real attempt cannot start: the window is shorter than the longest probe so
        far or than the poll interval (so never at or below 0)."""
        floor = max(self.longest, settings.poll_interval_seconds)
        return self.window(settings, clock) < floor


class AzureAllocationBase(Probe):
    """Shared helper of the 3 Azure provisioning probe classes. Not a probe: no `probe_type`."""

    provider = "azure"
    default_cadence = "2h"
    region_lanes = True  # off the signal thread, 1 lane per Region (spec 0003, section 6B)
    # Tests replace these 2 factories. A `local` target never uses them.
    adapter_factory: Callable[[ProbeContext], AllocationAdapter] = staticmethod(make_real_adapter)
    clock_factory: Callable[[], Clock] = RealClock

    def __init__(self) -> None:
        self._sim_counters: dict[tuple[Any, ...], int] = {}
        self._real_adapter: AllocationAdapter | None = None

    def adapter_for(
        self, ctx: ProbeContext, target: dict[str, Any], simulated: bool
    ) -> tuple[AllocationAdapter, Clock]:
        """Simulated: a SimAdapter with a virtual clock. Else: the real one."""
        if simulated:
            clock = sim.VirtualClock()
            return sim.SimAdapter(target, clock, experiment=ctx.experiment), clock
        if self._real_adapter is None:
            self._real_adapter = self.adapter_factory(ctx)
        return self._real_adapter, self.clock_factory()

    def run_allocation(self, ctx: ProbeContext) -> list[Record]:
        """The loop of a provisioning probe: pre-check each of its own entries (the entries that
        the file lists under its probe type), then probe them.

        The entries run 1 after the other. Each holds the lock of its (region, SKU) from the
        pre-check to the verified teardown. The rows of each entry go to `ctx.emit` at once.
        When the process stops, no new entry starts. Each real entry has its own deadline, so
        an entry late in the list gets the same time as the first one.
        """
        self._real_adapter = None
        settings = allocation_settings(ctx.config)
        rg = probe_rg()
        prefix = name_prefix(rg)
        rows: list[Record] = []
        seen: set[tuple[Any, ...]] = set()
        for target in ctx.config.get("provisioning_probes") or []:
            if target["probe_type"] != self.probe_type:
                continue
            if ctx.stopping():
                log.info(
                    "allocation.stopped %s",
                    kv(probe_type=self.probe_type, region=target["region"]),
                )
                break
            simulated = ctx.simulated(target)
            key = self.dedupe_key(target, simulated)
            if key in seen:
                continue
            try:
                adapter, clock = self.adapter_for(ctx, target, simulated)
            except Exception as exc:  # noqa: BLE001 - a client build failure is an error row
                # The build of a real client failed (spec 0010, section 5, step 8). The entry gets
                # an `error` row, so the store does not go silent; the next entry tries again.
                code = log_teardown_error(
                    log,
                    self.probe_type,
                    target["region"],
                    exc,
                    event="allocation.failed",
                    sku=target["sku"],
                )
                failed = {"n_ceiling": None, "reason": "Adapter build failed"}
                row = make_row(
                    ctx,
                    self.probe_type,
                    target,
                    simulated=simulated,
                    placement_group_mode=self.row_placement_group_mode(target),
                    n_requested=int(target["n"]),
                    n_placed=None,
                    outcome="error",
                    error_code=code,
                    latency_ms=0,
                    details=failed,
                )
                rows.extend(ctx.emit([row]))
                continue
            # 1 deadline per entry, from before its precheck (spec 0010, section 5, step 7). A stop
            # waits for 1 attempt only: the search checks `ctx.stopping()` before each attempt,
            # and `ctx.emit` writes the rows of each finished entry at once.
            # With `n_max`, the budget has 1 more probe and teardown for each step up (ADR 0023).
            budget = EntryBudget(
                (1 + up_steps(target))
                * (settings.probe_timeout_seconds + settings.teardown_timeout_seconds)
            )
            if not simulated:
                budget.deadline = clock.monotonic() + budget.seconds
            with series_lock(target["region"], target["sku"]):
                try:
                    pre = run_precheck(adapter, target, clock, self.probe_type)
                except Exception:  # noqa: BLE001 - 1 failed entry never stops the other entries
                    log.exception(
                        "allocation.failed %s",
                        kv(probe_type=self.probe_type, sku=target["sku"], region=target["region"]),
                    )
                    continue
                if pre.outcome == "error":
                    log.warning(
                        "allocation.skip %s",
                        kv(
                            probe_type=self.probe_type,
                            sku=target["sku"],
                            region=target["region"],
                            reason=pre.details.get("reason"),
                            error_code=pre.error_code,
                        ),
                    )
                    continue
                seen.add(key)
                if simulated:
                    adapter.step = self.next_sim_step(target)  # type: ignore[attr-defined]
                try:
                    new_rows = self.probe_target(
                        ctx,
                        adapter,
                        clock,
                        target,
                        simulated,
                        pre,
                        settings,
                        rg,
                        prefix,
                        budget,
                    )
                    rows.extend(ctx.emit(new_rows))
                except Exception:  # noqa: BLE001 - 1 failed entry never stops the other entries
                    log.exception(
                        "allocation.failed %s",
                        kv(probe_type=self.probe_type, sku=target["sku"], region=target["region"]),
                    )
        return rows

    def dedupe_key(self, target: dict[str, Any], simulated: bool) -> tuple[Any, ...]:
        """Entries with the same key give 1 probe per call. The default key is the entry."""
        return (
            simulated,
            target["sku"],
            target["region"],
            zone_of(target),
            target["n"],
            target.get("n_max"),
            target["placement_group_mode"],
        )

    def row_placement_group_mode(self, target: dict[str, Any]) -> str:
        return target["placement_group_mode"]

    def probe_target(
        self,
        ctx: ProbeContext,
        adapter: AllocationAdapter,
        clock: Clock,
        target: dict[str, Any],
        simulated: bool,
        pre: Precheck,
        settings: AllocationSettings,
        rg: str,
        prefix: str,
        budget: "EntryBudget",
    ) -> list[Record]:
        """The rows of 1 entry: 1 row for `restricted` or `quota` with no allocation call, else
        the halving search of ADR 0009 section 2 (1 to `1 + ceil(log2(C))` rows), or with `n_max`
        the upward search of ADR 0023 after a full success at C."""
        n_config = int(target["n"])
        details: dict[str, Any] = {
            "n_config": n_config,
            "n_ceiling": pre.n_ceiling,
        }
        if simulated:
            details["sim_step"] = adapter.step  # type: ignore[attr-defined]

        def row(outcome, n_requested, n_placed, error_code, latency_ms, extra) -> Record:
            return make_row(
                ctx,
                self.probe_type,
                target,
                simulated=simulated,
                placement_group_mode=self.row_placement_group_mode(target),
                n_requested=n_requested,
                n_placed=n_placed,
                outcome=outcome,
                error_code=error_code,
                latency_ms=latency_ms,
                details={**details, **extra},
            )

        if pre.outcome == "restricted":
            reason = {"skipped": True, "reason": pre.details.get("reason")}
            return [row("restricted", n_config, None, pre.error_code, pre.latency_ms, reason)]
        if pre.n_ceiling == 0:
            reason = {"skipped": True, "reason": "quota ceiling is 0"}
            return [row("quota", n_config, None, None, pre.latency_ms, reason)]
        n = min(n_config, pre.n_ceiling) if pre.n_ceiling is not None else n_config
        lo, hi, step = 0, None, 1
        cap = up_ceiling(target, pre.n_ceiling)  # U of ADR 0023, or `None` with no `n_max`
        up = False  # True from the first step above C
        rows: list[Record] = []
        while True:
            if ctx.stopping():
                # The process stops: no new attempt starts, not even the first one.
                # A simulated entry never writes `search_stopped` (spec 0010, section 5).
                if rows:
                    if not simulated:
                        rows[-1].details["search_stopped"] = "stop"
                else:
                    stop = {"skipped": True, "reason": "stop"}
                    if not simulated:
                        stop["search_stopped"] = "stop"
                    rows.append(row("error", n_config, None, None, 0, stop))
                break
            # A simulated entry takes no wall time: the entry deadline does not bind it.
            real = not simulated and budget.deadline is not None
            window = budget.window(settings, clock) if real else settings.probe_timeout_seconds
            if real and budget.too_short(settings, clock):
                if rows:
                    rows[-1].details["search_stopped"] = "deadline"
                else:
                    skip = {"skipped": True, "reason": "call deadline"}
                    skip["search_stopped"] = "deadline"
                    rows.append(row("error", n_config, None, None, 0, skip))
                break
            attempt_settings = replace(
                settings, probe_timeout_seconds=min(settings.probe_timeout_seconds, int(window))
            )
            if not simulated and budget.deadline is not None:
                attempt_settings = replace(
                    attempt_settings,
                    call_deadline=budget.deadline,
                    min_probe_window=max(budget.longest, settings.poll_interval_seconds),
                )
            result = self.allocate(ctx, adapter, clock, target, n, attempt_settings, rg, prefix)
            if result.details.get("search_stopped") == "deadline":
                # The pre-teardown of `allocate` used the time of this attempt.
                if rows:
                    rows[-1].details["search_stopped"] = "deadline"
                else:
                    rows.append(row(result.outcome, n_config, None, None, 0, result.details))
                break
            budget.longest = max(budget.longest, result.latency_ms / 1000)
            rows.append(
                row(
                    result.outcome,
                    n,
                    result.n_placed,
                    result.error_code,
                    result.latency_ms,
                    {
                        **result.details,
                        "search_step": step,
                        **({"search_direction": "up", "n_max": int(target["n_max"])} if up else {}),
                    },
                )
            )
            # Stop when the teardown is not verified: never stack on resources that remain.
            if not result.details.get("teardown", {}).get("verified"):
                break
            if up and result.outcome != "success":
                break  # above C a failure ends the search: no bisection (ADR 0023)
            if result.outcome == "success":
                lo = n
                if hi is None:
                    if cap is None or n >= cap:
                        break
                    n = min(2 * n, cap)
                    up = True
                    step += 1
                    continue
            elif result.outcome == "no_capacity":
                hi = n
            else:  # quota, restricted_or_no_capacity, timeout, error: a smaller N will not help
                break
            next_n = (lo + hi) // 2
            if next_n == lo:
                break
            n = next_n
            step += 1
        return rows

    def allocate(self, ctx, adapter, clock, target, n, settings, rg, prefix) -> "Allocation":
        raise NotImplementedError

    def next_sim_step(self, target: dict[str, Any]) -> str:
        """The next step of the entry's `sim_script`, cycled per run of this probe."""
        script = sim.script_of(target)
        key = (target["sku"], target["region"], zone_of(target), target["placement_group_mode"])
        index = self._sim_counters.get(key, 0)
        self._sim_counters[key] = index + 1
        return script[index % len(script)]


class PrecheckProbe(AzureAllocationBase):
    """`az_precheck`: 1 row per `provisioning_probes` entry with the N ceiling. The row names the
    probe type of its entry in `details.entry_probe_type`, because a size and a Region can be
    listed under both probe types."""

    probe_type = "az_precheck"

    def run(self, ctx: ProbeContext) -> list[Record]:
        self._real_adapter = None
        rows = []
        for target in ctx.config.get("provisioning_probes") or []:
            if ctx.stopping():
                log.info(
                    "allocation.stopped %s",
                    kv(probe_type=self.probe_type, region=target["region"]),
                )
                break
            simulated = ctx.simulated(target)
            try:
                adapter, clock = self.adapter_for(ctx, target, simulated)
                pre = run_precheck(adapter, target, clock)
            except Exception as exc:  # noqa: BLE001 - 1 failed entry never stops the other entries
                code = log_teardown_error(
                    log,
                    self.probe_type,
                    target["region"],
                    exc,
                    event="allocation.failed",
                    sku=target["sku"],
                )
                failed = {"n_ceiling": None, "reason": "Pre-check failed"}
                pre = Precheck("error", None, code, 0, failed)
            row = make_row(
                ctx,
                self.probe_type,
                target,
                simulated=simulated,
                placement_group_mode=target["placement_group_mode"],
                n_requested=int(target["n"]),
                n_placed=None,
                outcome=pre.outcome,
                error_code=pre.error_code,
                latency_ms=pre.latency_ms,
                details={"entry_probe_type": target["probe_type"], **pre.details},
            )
            rows.extend(ctx.emit([row]))
        return rows
