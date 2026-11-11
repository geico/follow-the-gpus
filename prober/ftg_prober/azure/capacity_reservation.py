"""`az_capacity_reservation`: the probe of spec 0002 section 4.4. Spec 0010 section 8.

Sequence per entry: select the reservation group of the Region by its tags (`experiment`,
`region`; ADR 0022), create a capacity reservation for N VMs of the size in it, record the
result, delete the reservation, and verify the delete.
"""

import logging
from typing import Any

from ftg_prober.azure.prechecks import (
    Allocation,
    AllocationSettings,
    AzureAllocationBase,
    probe_tags,
    reservation_target_tag,
    select_by_tags,
    sku_slug,
    zone_of,
)
from ftg_prober.azure.provisioning_adapter import (
    AllocationAdapter,
    AzureCallError,
    Clock,
    Operation,
    log_azure_error,
    log_teardown_error,
)
from ftg_prober.azure.vmss_flex import classify_code, primary_code
from ftg_prober.log import kv
from ftg_prober.probes import ProbeContext
from ftg_prober.record import Record

log = logging.getLogger(__name__)

PROBE_TYPE = "az_capacity_reservation"


def reservation_name(prefix: str, target: dict[str, Any], run_id: str) -> str:
    """`<prefix>-cr-<sku slug>-<region>[-z<zone>]-<first 8 characters of run_id>`."""
    zone = zone_of(target)
    zone_part = f"-z{zone}" if zone else ""
    return (
        f"{prefix}-cr-{sku_slug(target['sku'])}-{target['region'].lower()}{zone_part}-{run_id[:8]}"
    )


class CapacityReservationProbe(AzureAllocationBase):
    probe_type = "az_capacity_reservation"

    def run(self, ctx: ProbeContext) -> list[Record]:
        return self.run_allocation(ctx)

    def dedupe_key(self, target: dict[str, Any], mode: str) -> tuple[Any, ...]:
        """A reservation has no placement group mode. Entries that differ only there run 1 time."""
        return (
            mode,
            target["sku"].lower(),
            target["region"].lower(),
            zone_of(target),
            target["n"],
            target.get("n_max"),
        )

    def row_placement_group_mode(self, target: dict[str, Any]) -> str:
        return "n/a"

    def allocate(
        self,
        ctx: ProbeContext,
        adapter: AllocationAdapter,
        clock: Clock,
        target: dict[str, Any],
        n: int,
        settings: AllocationSettings,
        rg: str,
        prefix: str,
    ) -> Allocation:
        region = target["region"]
        match = {"experiment": ctx.experiment, "region": region.lower()}
        found, failure = select_by_tags(
            lambda: adapter.list_reservation_groups(rg),
            match,
            "reservation group",
            self.probe_type,
            region,
        )
        if failure is not None:
            return failure
        group = found.name
        name = reservation_name(prefix, target, ctx.run_id)
        tags = probe_tags(ctx.experiment, reservation_target_tag(target), ctx.run_id)
        details: dict[str, Any] = {"match": match, "reservation_group": group, "reservation": name}
        started = clock.monotonic()
        try:
            result = self._create(adapter, clock, rg, group, name, target, n, tags, settings)
        except Exception:  # noqa: BLE001 - an unknown failure still gets a teardown
            latency = int((clock.monotonic() - started) * 1000)
            log.exception(
                "allocation.failed %s",
                kv(probe_type=self.probe_type, region=target["region"], reservation=name),
            )
            result = Allocation("error", None, None, latency)
        finally:
            down = delete_and_verify(
                adapter, clock, rg, group, name, settings, region=target["region"]
            )
        details.update(result.details)
        details["teardown"] = down
        return Allocation(
            result.outcome, result.n_placed, result.error_code, result.latency_ms, details
        )

    def _create(self, adapter, clock, rg, group, name, target, n, tags, settings) -> Allocation:
        started = clock.monotonic()

        def failed(err: AzureCallError) -> Allocation:
            log_azure_error(log, self.probe_type, target["region"], err, call="create_reservation")
            latency = int((clock.monotonic() - started) * 1000)
            outcome = classify_code(primary_code(list(err.codes)), err.message)
            code = primary_code(list(err.codes))
            # A reservation succeeds or fails as a whole: a capacity failure places 0 VMs.
            n_placed = 0 if outcome in ("no_capacity", "restricted_or_no_capacity") else None
            # All codes of the error, as returned: the top code, then the detail codes (ADR 0006).
            return Allocation(outcome, n_placed, code, latency, {"error_codes": list(err.codes)})

        try:
            op: Operation = adapter.create_reservation(
                rg, group, name, target["region"], target["sku"], zone_of(target), n, tags
            )
        except AzureCallError as err:
            return failed(err)
        polls = 0
        while not op.done():
            if clock.monotonic() - started >= settings.probe_timeout_seconds:
                latency = int((clock.monotonic() - started) * 1000)
                return Allocation("timeout", None, None, latency, {"polls": polls})
            clock.sleep(settings.poll_interval_seconds)
            polls += 1
        err = op.error()
        if err is not None:
            return failed(err)
        latency = int((clock.monotonic() - started) * 1000)
        # A reservation succeeds or fails as a whole: success means all N are reserved.
        info = adapter.get_reservation(rg, group, name)
        state = info.provisioning_state if info else None
        reserved = info.capacity if info else None  # the number Azure returns, as returned
        return Allocation(
            "success",
            n,
            None,
            latency,
            {"polls": polls, "provisioning_state": state, "capacity": reserved},
        )


def delete_and_verify(
    adapter: AllocationAdapter,
    clock: Clock,
    rg: str,
    group: str,
    name: str,
    settings: AllocationSettings,
    region: str | None = None,
) -> dict[str, Any]:
    """Delete the reservation, then read it until Azure answers 404. Always runs."""
    started = clock.monotonic()
    errors: list[str] = []
    op: Operation | None = None
    verified = False
    while True:
        if op is None:
            try:
                op = adapter.delete_reservation(rg, group, name)
            except Exception as exc:  # noqa: BLE001 - a teardown call that fails must not end it
                errors.append(
                    log_teardown_error(log, PROBE_TYPE, region, exc, call="delete_reservation")
                )
        if op is not None and op.done():
            err = op.error()
            if err is not None:
                errors.append(err.code)
                log_azure_error(log, PROBE_TYPE, region, err, call="delete_reservation")
                op = None  # try the delete again
        try:
            verified = adapter.get_reservation(rg, group, name) is None
        except Exception as exc:  # noqa: BLE001 - a failed read retries on the next poll
            errors.append(log_teardown_error(log, PROBE_TYPE, region, exc, call="get_reservation"))
        if verified or clock.monotonic() - started >= settings.teardown_timeout_seconds:
            break
        clock.sleep(settings.poll_interval_seconds)
    if not verified:
        log.warning("allocation.teardown_unverified %s", kv(reservation=name, group=group))
    return {
        "verified": verified,
        "errors": errors,
        "seconds": round(clock.monotonic() - started, 3),
    }
