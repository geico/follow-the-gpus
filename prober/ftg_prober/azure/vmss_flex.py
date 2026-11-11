"""`az_vmss_flex`: the VMSS Flexible probe of spec 0002 section 4.5. Spec 0010 section 7.

Sequence per entry: select the scale set of the entry by its tags (`experiment`, `sku`, `region`;
ADR 0022), set its capacity to N, tag the new members,
poll them, classify as soon as the allocation outcome shows, then set the capacity to 0,
delete the remaining VMs, and verify that 0 VMs remain.
"""

import logging
from dataclasses import replace
from typing import Any

from ftg_prober.azure.prechecks import (
    Allocation,
    AllocationSettings,
    AzureAllocationBase,
    probe_tags,
    select_by_tags,
    vmss_target_tag,
)
from ftg_prober.azure.provisioning_adapter import (
    AllocationAdapter,
    AzureCallError,
    Clock,
    Member,
    Operation,
    log_azure_error,
    log_teardown_error,
)
from ftg_prober.log import kv, log_provider_error
from ftg_prober.probes import ProbeContext
from ftg_prober.record import Record

log = logging.getLogger(__name__)

PROBE_TYPE = "az_vmss_flex"

# The outcome classification table of spec 0002 section 4.5. Spec 0010 section 6.
NO_CAPACITY_CODES = frozenset(
    {
        "AllocationFailed",
        "ZonalAllocationFailed",
        "OverconstrainedAllocationRequest",
        "OverconstrainedZonalAllocationRequest",
    }
)
RESTRICTED_OR_NO_CAPACITY_CODES = frozenset({"SkuNotAvailable"})
QUOTA_CODES = frozenset({"ResourceQuotaExceeded"})
# `OperationNotAllowed` is a quota error only when its message names the quota.
QUOTA_IF_MESSAGE_CODES = frozenset({"OperationNotAllowed"})
KNOWN_CODES = (
    NO_CAPACITY_CODES | RESTRICTED_OR_NO_CAPACITY_CODES | QUOTA_CODES | QUOTA_IF_MESSAGE_CODES
)

# A member in one of these power states got a host: the allocation succeeded.
PLACED_POWER_STATES = frozenset(
    {"starting", "running", "stopping", "stopped", "deallocating", "deallocated"}
)
# After a scale-in to 0, the probe deletes each remaining member directly after this time.
SCALE_IN_GRACE_SECONDS = 30


def classify_code(code: str | None, message: str = "") -> str:
    """The `outcome` of 1 error code when no VM was placed."""
    if code in NO_CAPACITY_CODES:
        return "no_capacity"
    if code in RESTRICTED_OR_NO_CAPACITY_CODES:
        return "restricted_or_no_capacity"
    if code in QUOTA_CODES:
        return "quota"
    if code in QUOTA_IF_MESSAGE_CODES and "quota" in (message or "").lower():
        return "quota"
    return "error"


def primary_code(codes: list[str]) -> str | None:
    """The raw code for `error_code`: the first code that the table knows, else the first code."""
    for code in codes:
        if code in KNOWN_CODES:
            return code
    return codes[0] if codes else None


def classify_allocation(
    n_requested: int,
    n_placed: int,
    codes: list[str],
    message: str = "",
    timed_out: bool = False,
) -> tuple[str, str | None]:
    """Return `(outcome, error_code)` for 1 allocation attempt (spec 0002 section 4.5 table)."""
    code = primary_code(codes)
    if timed_out:
        return "timeout", code
    if n_placed >= n_requested:
        return "success", None
    # All or nothing (ADR 0009): fewer than N placed is a failure, never `partial`.
    return classify_code(code, message), code


def member_state_counts(members: list[Member]) -> list[dict[str, Any]]:
    """The raw `(provisioning_state, power_state)` pairs of the members, with a count for each
    pair, in the order of first appearance. A state that Azure did not return is null. At most 1
    entry for each member (at most N, and N <= `n_max` <= 16 in the reference file)."""
    counts: dict[tuple[str | None, str | None], int] = {}
    for m in members:
        key = (m.provisioning_state, m.power_state)
        counts[key] = counts.get(key, 0) + 1
    return [
        {"provisioning_state": prov, "power_state": power, "count": n}
        for (prov, power), n in counts.items()
    ]


def member_state(member: Member) -> str:
    """`placed`, `failed`, or `pending` for 1 member."""
    if (member.power_state or "").lower() in PLACED_POWER_STATES:
        return "placed"
    if (member.provisioning_state or "").lower() == "failed" or member.error_code:
        return "failed"
    if (member.provisioning_state or "").lower() == "succeeded":
        return "placed"
    return "pending"


class MemberTagger:
    """Tags each member VM, OS disk, and NIC 1 time. A failed tag call is tried again later."""

    def __init__(
        self, adapter: AllocationAdapter, tags: dict[str, str], region: str | None = None
    ) -> None:
        self.adapter = adapter
        self.tags = tags
        self.region = region
        self.tagged: set[str] = set()
        self.errors = 0

    def tag(self, members: list[Member], adapter: AllocationAdapter | None = None) -> None:
        """Tag with `adapter` (the bounded copy during a teardown) or with the one of the tagger."""
        adapter = adapter or self.adapter
        for member in members:
            for resource_id in (member.id, member.os_disk_id, *member.nic_ids):
                if not resource_id or resource_id.lower() in self.tagged:
                    continue
                try:
                    adapter.tag_resource(resource_id, self.tags)
                except AzureCallError as exc:
                    self.errors += 1
                    log_azure_error(
                        log,
                        PROBE_TYPE,
                        self.region,
                        exc,
                        event="allocation.tag_failed",
                        id=resource_id,
                    )
                    continue
                self.tagged.add(resource_id.lower())


class VmssFlexProbe(AzureAllocationBase):
    probe_type = "az_vmss_flex"

    def run(self, ctx: ProbeContext) -> list[Record]:
        return self.run_allocation(ctx)

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
        match = {
            "experiment": ctx.experiment,
            "sku": target["sku"],
            "region": region.lower(),
        }
        scale_set, failure = select_by_tags(
            lambda: adapter.list_scale_sets(rg), match, "scale set", self.probe_type, region
        )
        if failure is not None:
            return failure
        name = scale_set.name
        details: dict[str, Any] = {"match": match, "scale_set": name}
        tagger = MemberTagger(
            adapter, probe_tags(ctx.experiment, vmss_target_tag(target), ctx.run_id), region
        )

        try:
            leftover = adapter.list_members(rg, scale_set.id)
        except Exception as exc:  # noqa: BLE001 - a malformed 200 body is an error row, not a crash
            code = log_teardown_error(
                log,
                self.probe_type,
                region,
                exc,
                event="allocation.unexpected_error",
                call="list_members",
            )
            details["reason"] = "scale set members not readable"
            return Allocation("error", None, code, 0, details)
        if scale_set.capacity != 0 or leftover:
            clean = teardown(adapter, clock, rg, scale_set, settings, tagger, region)
            details["pre_teardown"] = clean
            if not clean["verified"]:
                details["reason"] = "scale set not empty before the probe"
                return Allocation("error", None, None, 0, details)

        if settings.call_deadline is not None and "pre_teardown" in details:
            # The pre-teardown used time of the call: the attempt needs its probe and its teardown
            # to fit before the deadline (spec 0010, section 5, step 7).
            window = settings.call_deadline - clock.monotonic() - settings.teardown_timeout_seconds
            if window < max(settings.min_probe_window, settings.poll_interval_seconds):
                details.update(
                    skipped=True,
                    reason="call deadline",
                    search_stopped="deadline",
                    teardown=details["pre_teardown"],
                )
                return Allocation("error", None, None, 0, details)
            settings = replace(
                settings, probe_timeout_seconds=min(settings.probe_timeout_seconds, int(window))
            )

        started = clock.monotonic()
        try:
            result = self._scale_out(adapter, clock, rg, scale_set, n, settings, tagger, ctx)
        except Exception as exc:  # noqa: BLE001 - an unknown failure still gets a teardown
            err = exc if isinstance(exc, AzureCallError) else None
            latency = int((clock.monotonic() - started) * 1000)
            if err is not None:
                log_azure_error(log, self.probe_type, region, err, call="scale_out")
                code = primary_code(list(err.codes))
                outcome = classify_code(code, err.message)
                n_placed = 0 if outcome in ("no_capacity", "restricted_or_no_capacity") else None
                result = Allocation(
                    outcome, n_placed, code, latency, {"error_codes": list(err.codes)}
                )
            else:
                log.exception(
                    "allocation.failed %s",
                    kv(probe_type=self.probe_type, region=region, scale_set=name),
                )
                result = Allocation("error", None, None, latency)
        finally:
            down = teardown(adapter, clock, rg, scale_set, settings, tagger, region)
        details.update(result.details)
        details["teardown"] = down
        details["tagged"] = len(tagger.tagged)
        details["tag_errors"] = tagger.errors
        return Allocation(
            result.outcome, result.n_placed, result.error_code, result.latency_ms, details
        )

    def _scale_out(
        self,
        adapter: AllocationAdapter,
        clock: Clock,
        rg: str,
        scale_set: Any,
        n: int,
        settings: AllocationSettings,
        tagger: MemberTagger,
        ctx: ProbeContext,
    ) -> Allocation:
        started = clock.monotonic()
        tags = {**scale_set.tags, "run_id": ctx.run_id}
        op: Operation = adapter.update_scale_set(rg, scale_set.name, scale_set.sku_name, n, tags)
        polls = 0
        timed_out = False
        while True:
            members = adapter.list_members(rg, scale_set.id)
            tagger.tag(members)  # tag first, then classify (spec 0005, A21)
            polls += 1
            states = [member_state(m) for m in members]
            pending = states.count("pending")
            op_done = op.done()
            if pending == 0 and (len(members) >= n or op_done):
                break
            # All or nothing: 1 failed member means N cannot place. Wait for its code from the
            # instance view first, else the row is `error` (spec 0010, section 6, "no code").
            failed_codes = [m.error_code for m in members if member_state(m) == "failed"]
            if failed_codes and (op_done or all(failed_codes)):
                break
            if clock.monotonic() - started >= settings.probe_timeout_seconds:
                timed_out = True
                break
            clock.sleep(settings.poll_interval_seconds)
        latency = int((clock.monotonic() - started) * 1000)
        for member in members:
            if member_state(member) == "failed" and member.error_message:
                log_provider_error(
                    log,
                    probe_type=self.probe_type,
                    region=tagger.region,
                    code=member.error_code,
                    message=member.error_message,
                    call="member_instance_view",
                    vm=member.name,
                )
        placed = states.count("placed")
        failed = states.count("failed")
        codes = [m.error_code for m in members if member_state(m) == "failed" and m.error_code]
        op_error = op.error() if op.done() else None
        message = ""
        if op_error is not None:
            codes.extend(c for c in op_error.codes if c not in codes)
            message = op_error.message
            log_azure_error(log, self.probe_type, tagger.region, op_error, call="update_scale_set")
        outcome, code = classify_allocation(n, placed, codes, message, timed_out)
        details = {
            "n_failed": failed,
            "n_pending": pending,
            "polls": polls,
            "member_codes": sorted(set(codes)),
            "member_states": member_state_counts(members),
        }
        if op_error is not None:
            details["error_codes"] = list(op_error.codes)  # as returned, in order
        if outcome == "timeout":
            details["n_placed_at_timeout"] = placed
            return Allocation(outcome, None, code, latency, details)
        if outcome in ("quota", "error"):
            details["n_placed"] = placed
            return Allocation(outcome, None, code, latency, details)
        if outcome == "success":
            return Allocation(outcome, placed, code, latency, details)
        # A failed attempt places 0 (spec 0010 section 7.5); the raw count stays in `details`.
        details["n_placed"] = placed
        n_placed = 0 if outcome in ("no_capacity", "restricted_or_no_capacity") else None
        return Allocation(outcome, n_placed, code, latency, details)


def teardown(
    adapter: AllocationAdapter,
    clock: Clock,
    rg: str,
    scale_set: Any,
    settings: AllocationSettings,
    tagger: MemberTagger,
    region: str | None = None,
) -> dict[str, Any]:
    """Set the capacity to 0, delete the remaining VMs (Failed ones too), re-send the scale-in
    while the scale set capacity is above 0, verify 0 VMs and a scale set capacity of 0."""
    started = clock.monotonic()
    # Only the teardown calls are bounded: each gets max(remaining budget, 10 s) and no retry.
    adapter = adapter.for_teardown(
        lambda: started + settings.teardown_timeout_seconds - clock.monotonic()
    )
    errors: list[str] = []
    scale_in: Operation | None = None
    try:
        scale_in = adapter.update_scale_set(rg, scale_set.name, scale_set.sku_name, 0, None)
    except Exception as exc:  # noqa: BLE001 - a teardown call that fails must not end the teardown
        errors.append(log_teardown_error(log, PROBE_TYPE, region, exc, call="update_scale_set"))
    delete_requested: set[str] = set()
    read_ops: list[Operation] = []  # compared by identity, so a reused id cannot hide one

    def read_scale_in_error() -> None:
        # A done scale-in that failed: log its error once, as Azure returned it.
        if scale_in is None or any(op is scale_in for op in read_ops) or not scale_in.done():
            return
        read_ops.append(scale_in)
        op_error = scale_in.error()
        if op_error is not None:
            errors.append(op_error.code)
            log_azure_error(log, PROBE_TYPE, region, op_error, call="update_scale_set")

    remaining = -1
    while True:
        elapsed = clock.monotonic() - started
        read_scale_in_error()
        try:
            members = adapter.list_members(rg, scale_set.id)
        except Exception as exc:  # noqa: BLE001 - a teardown call that fails must not end it
            errors.append(log_teardown_error(log, PROBE_TYPE, region, exc, call="list_members"))
            members, remaining = None, -1
        if members is not None:
            remaining = len(members)
            if not members:
                try:
                    capacity = adapter.get_scale_set(rg, scale_set.name).capacity
                except Exception as exc:  # noqa: BLE001 - a teardown call that fails must not end it
                    errors.append(
                        log_teardown_error(log, PROBE_TYPE, region, exc, call="get_scale_set")
                    )
                    capacity = None
                if capacity == 0:
                    break
                remaining = -1  # no member, but the scale set still asks for some: not verified
                if scale_in is None or scale_in.done():
                    read_scale_in_error()
                    try:
                        scale_in = adapter.update_scale_set(
                            rg, scale_set.name, scale_set.sku_name, 0, None
                        )
                    except Exception as exc:  # noqa: BLE001 - a teardown call that fails must not end it
                        errors.append(
                            log_teardown_error(
                                log, PROBE_TYPE, region, exc, call="update_scale_set"
                            )
                        )
        if members and (scale_in is None or scale_in.done() or elapsed >= SCALE_IN_GRACE_SECONDS):
            for member in members:
                if member.name in delete_requested:
                    continue
                if (member.provisioning_state or "").lower() == "deleting":
                    continue
                try:
                    adapter.delete_vm(rg, member.name)
                    delete_requested.add(member.name)
                except Exception as exc:  # noqa: BLE001 - a failed delete retries on the next poll
                    errors.append(
                        log_teardown_error(log, PROBE_TYPE, region, exc, call="delete_vm")
                    )
        if members:
            # After the deletes were sent, so a hung tag call cannot delay them. The tag lets
            # the sweeper find a survivor.
            try:
                tagger.tag(members, adapter)  # the bounded copy: a hung tag call must end
            except Exception as exc:  # noqa: BLE001 - a tag that fails must not end the teardown
                errors.append(log_teardown_error(log, PROBE_TYPE, region, exc, call="tag"))
        if elapsed >= settings.teardown_timeout_seconds:
            break
        clock.sleep(settings.poll_interval_seconds)
    read_scale_in_error()
    verified = remaining == 0
    result = {
        "verified": verified,
        "remaining": remaining,
        "deleted_directly": len(delete_requested),
        "errors": errors,
        "seconds": round(clock.monotonic() - started, 3),
    }
    if not verified:
        log.warning(
            "allocation.teardown_unverified %s", kv(scale_set=scale_set.name, remaining=remaining)
        )
    return result
