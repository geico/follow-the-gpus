"""`aws_odcr`: the AWS provisioning probe of spec 0002 section 4.9, ADR 0009 section 3.

The probe asks for `n_requested` On-Demand instances with an On-Demand Capacity Reservation
(ODCR), 1 Availability Zone, `InstanceMatchCriteria` = `targeted`, and an `EndDate` backstop. All
or nothing (ADR 0009, section 1): fewer than `n_requested` placed is a failure, torn down at
once. A capacity failure halves the count, in the search of ADR 0009 section 2. The probe never
calls `RunInstances` and launches no instance, so it needs no launch template, AMI, or subnet.

**The `n_ceiling` from quota (spec 0002 section 4.9, pilot P13):** before the first attempt, the
probe reads the On-Demand vCPU quota of the instance family (`GetServiceQuota`) and the vCPUs
of the instance type (`DescribeInstanceTypes`). The search ceiling C is
`min(n, floor(quota vCPUs / instance vCPUs))`. The quota is the limit, not the free vCPUs: the
probe does not read the vCPUs in use. A quota error (`InstanceLimitExceeded`,
`VcpuLimitExceeded`) still stops the search. When the quota cannot be read, C is the entry `n`
and the row's `details.quota_error` holds the raw code.

**Cleanup, flagged for review (spec 0002 section 4.9):** the simplest design, no new service.
At the start of a run, for each Region with a configured entry, the probe lists its own
reservations by the `experiment` tag and cancels any `active` one older than the probe timeout
plus the teardown timeout, and records 1 row per cancelled reservation. A `pending` reservation
cannot be cancelled (`CancelCapacityReservation`); it is left to its `EndDate` backstop.
"""

import logging
import math
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any, Protocol

from ftg_prober.aws.common import error_code, log_aws_error
from ftg_prober.aws.odcr_sim import SimEc2, VirtualClock, script_of
from ftg_prober.config import AWS_DEFAULT_PROVISIONING_CADENCE, parse_cadence, up_steps
from ftg_prober.log import kv
from ftg_prober.probes import Probe, ProbeContext
from ftg_prober.record import Record

log = logging.getLogger(__name__)

PROBE_TYPE = "aws_odcr"

DEFAULTS = {
    "probe_timeout_seconds": 90,
    "poll_interval_seconds": 5,
    "teardown_timeout_seconds": 60,
}
# The EndDate offset must be more than probe_timeout + teardown_timeout (spec 0002 section
# 4.9), so that the reservation cannot expire during a poll. The exact number is open (P13);
# this adds a 60 s margin, plus 2 poll intervals so that a slow poll cadence still clears it.
END_DATE_FIXED_BUFFER_SECONDS = 60
# A hard maximum on how far ahead `EndDate` may be (about 4 times the default offset of 220 s).
# IAM cannot cap a relative `EndDate` (`ec2:EndDate` takes a fixed date, and a policy variable
# cannot be the value of a Date condition:
# https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_variables.html), so the
# probe checks it before `CreateCapacityReservation`. A larger offset (a config or arithmetic
# bug) raises; it is never clamped, because a clamp could put `EndDate` before the poll ends.
END_DATE_MAX_SECONDS = 900
# Service Quotas codes of the On-Demand vCPU quotas ("Running On-Demand ... instances"), service
# code `ec2`. https://docs.aws.amazon.com/ec2/latest/instancetypes/ec2-instance-quotas.html
QUOTA_SERVICE_CODE = "ec2"
QUOTA_CODE_STANDARD = "L-1216C47A"  # Standard (A, C, D, H, I, M, R, T, Z)
QUOTA_CODE_G_VT = "L-DB2E81BA"
QUOTA_CODE_P = "L-417A185B"
# Families that have an On-Demand quota of their own, not the Standard one. They get no cap.
OTHER_QUOTA_FAMILY_PREFIXES = ("dl", "f", "hpc", "inf", "mac", "trn", "u", "x")
STANDARD_FAMILY_LETTERS = "acdhimrtz"
# `CreateCapacityReservation` and `CancelCapacityReservation` error codes. ADR 0009 section 3.1.
NO_CAPACITY_CODES = ("InsufficientInstanceCapacity",)
QUOTA_CODES = ("InstanceLimitExceeded", "VcpuLimitExceeded")
# The cancel retry backoff of `teardown`: doubles from the poll interval, capped so that a long
# teardown timeout still gets several tries near the end.
CANCEL_BACKOFF_CAP_SECONDS = 30
# An ambiguous create error (no `.response`: a read timeout or a connection error, not a normal
# `ClientError`) retries with the SAME `ClientToken`, so AWS's own idempotency returns the
# existing reservation if the first call did succeed server-side, instead of creating a second
# one. https://docs.aws.amazon.com/AWSEC2/latest/APIReference/Run_Instance_Idempotency.html
AMBIGUOUS_CREATE_MAX_ATTEMPTS = 3
AMBIGUOUS_CREATE_BACKOFF_BASE_SECONDS = 1
AMBIGUOUS_CREATE_BACKOFF_CAP_SECONDS = 5


def _classify_create_error(code: str) -> tuple[str, int | None] | None:
    """A definite AWS rejection of the create. `None` means "not one of these"."""
    if code in NO_CAPACITY_CODES:
        return "no_capacity", 0
    if code in QUOTA_CODES:
        return "quota", None
    return None


def end_date_buffer_seconds(settings: "Settings") -> int:
    return END_DATE_FIXED_BUFFER_SECONDS + 2 * settings.poll_interval_seconds


def quota_code(instance_type: str) -> str | None:
    """The On-Demand vCPU quota code of the instance family, or `None` if this probe has none.

    `g` and `vt` share 1 quota, `p` has its own, and `a c d h i m r t z` share the Standard
    quota. Other families (for example `inf`, `trn`, `x`) have their own quotas, not mapped here.
    """
    family = instance_type.split(".")[0].lower()
    if family.startswith(("vt", "g")):
        return QUOTA_CODE_G_VT
    if family.startswith("p"):
        return QUOTA_CODE_P
    if family.startswith(OTHER_QUOTA_FAMILY_PREFIXES):
        return None
    if family[:1] and family[0] in STANDARD_FAMILY_LETTERS:
        return QUOTA_CODE_STANDARD
    return None


def quota_ceiling(
    quota_client: Any, ec2: Any, target: dict[str, Any]
) -> tuple[int | None, dict[str, Any]]:
    """`(C, details)`: floor(quota vCPUs / instance vCPUs), or `None` when it cannot be read.

    `details` gets `quota_code`, `quota_vcpus`, `instance_vcpus`, `n_ceiling`, or the raw
    `quota_error` code of the failed call (ADR 0006), never a filtered one.
    """
    instance_type = target["instance_type"]
    code = quota_code(instance_type)
    if code is None:
        return None, {"quota_skipped": "no quota code for the family"}
    details: dict[str, Any] = {"quota_code": code}
    try:
        resp = quota_client.get_service_quota(ServiceCode=QUOTA_SERVICE_CODE, QuotaCode=code)
        quota_vcpus = float(resp["Quota"]["Value"])
        found = ec2.describe_instance_types(InstanceTypes=[instance_type])
        instance_vcpus = int(found["InstanceTypes"][0]["VCpuInfo"]["DefaultVCpus"])
    except Exception as exc:  # noqa: BLE001 - every provider failure keeps C = n and is recorded
        details["quota_error"] = error_code(exc)
        log_aws_error(log, PROBE_TYPE, target["region"], exc, call="quota_ceiling")
        return None, details
    ceiling = math.floor(quota_vcpus / instance_vcpus)
    details.update(quota_vcpus=quota_vcpus, instance_vcpus=instance_vcpus, n_ceiling=ceiling)
    return ceiling, details


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass(frozen=True)
class Settings:
    probe_timeout_seconds: int
    poll_interval_seconds: int
    teardown_timeout_seconds: int


def odcr_settings(section: dict[str, Any]) -> Settings:
    values = {}
    for key, default in DEFAULTS.items():
        value = section.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"aws.{key} must be an integer of 1 or more, not {value!r}")
        values[key] = value
    return Settings(**values)


def target_tag(target: dict[str, Any]) -> str:
    """`<instance type>-<zone ID>`: unique per zone, the same in every account."""
    return f"{target['instance_type']}-{target['zone_id']}"


def probe_tags(experiment: str, tag: str, run_id: str) -> dict[str, str]:
    return {"experiment": experiment, "target": tag, "run_id": run_id}


def _aws_tags(tags: dict[str, str]) -> list[dict[str, str]]:
    return [{"Key": k, "Value": v} for k, v in tags.items()]


def _ms(seconds: float) -> int:
    return max(0, int(round(seconds * 1000)))


@dataclass
class Allocation:
    """The result of 1 allocation attempt, with its teardown."""

    outcome: str
    n_placed: int | None
    error_code: str | None
    latency_ms: int
    details: dict[str, Any] = field(default_factory=dict)
    extra_rows: list[Record] = field(default_factory=list)


@dataclass
class _Attempt:
    outcome: str
    n_placed: int | None
    error_code: str | None
    latency_ms: int
    details: dict[str, Any]
    reservation_id: str | None
    state: str | None
    extra_rows: list[Record] = field(default_factory=list)


def _describe(ec2: Any, reservation_id: str) -> tuple[str | None, dict[str, Any]]:
    resp = ec2.describe_capacity_reservations(CapacityReservationIds=[reservation_id])
    found = (resp.get("CapacityReservations") or [{}])[0]
    return found.get("State"), found


def zones_offering(
    ec2: Any, clock: Clock, target: dict[str, Any]
) -> tuple[str, list[dict[str, str]], str | None, int]:
    """The zones of the target's Region that offer its instance type.

    `DescribeInstanceTypeOfferings` with `LocationType=availability-zone-id` lists the zone IDs;
    `DescribeAvailabilityZones` gives each zone name in this account. Returns
    `(outcome, zones, error code, latency ms)`. `outcome` is `success`, `restricted` (no zone
    offers the type), or `error`. Each zone is `{"zone": name, "zone_id": ID}`, sorted by ID.
    A zone ID with no name here (a zone of another type, or one not opted in) is left out and
    logged as `odcr.zone_without_name`. Pages of the offerings call are followed.
    """
    started = clock.monotonic()
    try:
        offered: set[str] = set()
        kwargs: dict[str, Any] = {
            "LocationType": "availability-zone-id",
            "Filters": [{"Name": "instance-type", "Values": [target["instance_type"]]}],
        }
        while True:
            resp = ec2.describe_instance_type_offerings(**kwargs)
            offered.update(o["Location"] for o in resp.get("InstanceTypeOfferings") or [])
            if not resp.get("NextToken"):
                break
            kwargs["NextToken"] = resp["NextToken"]
        zone_ids = sorted(offered)
        names: dict[str, str] = {}
        if zone_ids:
            found = ec2.describe_availability_zones(
                ZoneIds=zone_ids,
                Filters=[{"Name": "zone-type", "Values": ["availability-zone"]}],
            )
            names = {z["ZoneId"]: z["ZoneName"] for z in found.get("AvailabilityZones") or []}
    except Exception as exc:  # noqa: BLE001 - every provider failure becomes an error row
        log_aws_error(
            log, PROBE_TYPE, target["region"], exc, call="describe_instance_type_offerings"
        )
        return "error", [], error_code(exc), _ms(clock.monotonic() - started)
    unnamed = [i for i in zone_ids if i not in names]
    if unnamed:
        log.warning(
            "odcr.zone_without_name %s",
            kv(region=target["region"], instance_type=target["instance_type"], zone_ids=unnamed),
        )
    zones = [{"zone": names[i], "zone_id": i} for i in zone_ids if i in names]
    outcome = "success" if zones else ("unnamed" if zone_ids else "restricted")
    return outcome, zones, None, _ms(clock.monotonic() - started)


def _create_and_poll(
    ctx: ProbeContext,
    ec2: Any,
    create_ec2: Any,
    clock: Clock,
    target: dict[str, Any],
    n: int,
    tags: dict[str, str],
    settings: Settings,
    simulated: bool,
    holder: dict[str, Any],
) -> _Attempt:
    """`holder` gets the reservation ID and the last known state as soon as they are known.

    The caller (`allocate`) reads `holder` to find a reservation to cancel even if an
    unexpected exception (or a `KeyboardInterrupt`) escapes this function after the create
    succeeds (L1): the normal return path is not the only way a reservation must get a
    teardown attempt.
    """
    started = clock.monotonic()
    end_date_offset = (
        settings.probe_timeout_seconds
        + settings.teardown_timeout_seconds
        + end_date_buffer_seconds(settings)
    )
    if end_date_offset > END_DATE_MAX_SECONDS:
        raise ValueError(
            f"EndDate offset {end_date_offset} s is over the maximum of {END_DATE_MAX_SECONDS} s"
        )
    end_date = ctx.now() + timedelta(seconds=end_date_offset)
    # `ClientToken` is the same string across every retry below, so a botocore retry (or 1 of
    # ours) with it never creates a second reservation; `create_ec2` has max_attempts=1, so
    # botocore's own retry never hides a second create behind what we count as 1 attempt.
    client_token = str(uuid.uuid4())
    create_kwargs = {
        "InstanceType": target["instance_type"],
        "InstancePlatform": "Linux/UNIX",
        "AvailabilityZoneId": target["zone_id"],
        "InstanceCount": n,
        "InstanceMatchCriteria": "targeted",
        "EndDateType": "limited",
        "EndDate": end_date,
        "ClientToken": client_token,
        "TagSpecifications": [{"ResourceType": "capacity-reservation", "Tags": _aws_tags(tags)}],
    }
    resp = None
    ambiguous_exc: Exception | None = None
    create_attempts = 0
    for retry_i in range(AMBIGUOUS_CREATE_MAX_ATTEMPTS):
        create_attempts += 1
        try:
            resp = create_ec2.create_capacity_reservation(**create_kwargs)
            break
        except Exception as exc:  # noqa: BLE001 - every provider failure becomes an error row
            if getattr(exc, "response", None) is not None:
                # A normal ClientError: AWS definitely did not create the reservation (for
                # example InsufficientInstanceCapacity). No ambiguity, so no retry.
                code = error_code(exc)
                log_aws_error(
                    log, PROBE_TYPE, target["region"], exc, call="create_capacity_reservation"
                )
                latency = _ms(clock.monotonic() - started)
                classified = _classify_create_error(code)
                if classified:
                    outcome, n_placed = classified
                    return _Attempt(outcome, n_placed, code, latency, {}, None, None)
                return _Attempt("error", None, code, latency, {}, None, None)
            ambiguous_exc = exc
            log_aws_error(
                log, PROBE_TYPE, target["region"], exc, call="create_capacity_reservation"
            )
            more_tries_left = retry_i < AMBIGUOUS_CREATE_MAX_ATTEMPTS - 1
            time_left = clock.monotonic() - started < settings.probe_timeout_seconds
            if not (more_tries_left and time_left):
                break
            backoff = min(
                AMBIGUOUS_CREATE_BACKOFF_BASE_SECONDS * (2**retry_i),
                AMBIGUOUS_CREATE_BACKOFF_CAP_SECONDS,
            )
            clock.sleep(backoff)

    if resp is None:
        # Every retry with the same ClientToken still failed ambiguously (a read timeout or a
        # connection error, never a normal ClientError): AWS's own idempotency would have
        # returned the existing reservation for this token if the first call did succeed
        # server-side. With no reservation ID, poll and the normal teardown are not possible,
        # so a targeted, threshold-free cleanup (by this attempt's own tags) is the last
        # resort; it cannot touch another attempt's reservation.
        code = error_code(ambiguous_exc) if ambiguous_exc is not None else "Unknown"
        latency = _ms(clock.monotonic() - started)
        extra_rows, listed = _ambiguous_create_cleanup(
            ctx, ec2, clock, settings, target, tags, simulated
        )
        return _Attempt(
            "error",
            None,
            code,
            latency,
            {
                "ambiguous_create": True,
                "create_attempts": create_attempts,
                "cleanup_listed": listed,
            },
            None,
            None,
            extra_rows,
        )

    reservation = resp["CapacityReservation"]
    reservation_id = reservation["CapacityReservationId"]
    state = reservation.get("State")
    holder["id"] = reservation_id
    holder["state"] = state
    polls = 0
    poll_errors: list[str] = []
    while state == "pending" and clock.monotonic() - started < settings.probe_timeout_seconds:
        clock.sleep(settings.poll_interval_seconds)
        polls += 1
        try:
            state, reservation = _describe(ec2, reservation_id)
        except Exception as exc:  # noqa: BLE001 - a failed poll (for example a throttle) retries
            poll_errors.append(error_code(exc))
            log_aws_error(
                log, PROBE_TYPE, target["region"], exc, call="describe_capacity_reservations"
            )
        holder["state"] = state
    latency = _ms(clock.monotonic() - started)
    details: dict[str, Any] = {"polls": polls, "reservation_id": reservation_id, "state": state}
    if poll_errors:
        details["poll_errors"] = poll_errors
    if state == "pending":
        return _Attempt("timeout", None, None, latency, details, reservation_id, state)
    if state == "failed":
        return _Attempt("error", None, None, latency, details, reservation_id, state)
    if state == "active":
        total = int(reservation.get("TotalInstanceCount") or 0)
        if total >= n:
            return _Attempt("success", n, None, latency, details, reservation_id, state)
        details["n_placed"] = total
        return _Attempt("no_capacity", 0, None, latency, details, reservation_id, state)
    return _Attempt("error", None, None, latency, details, reservation_id, state)


# End states in which a reservation holds no capacity and bills nothing: the API
# reference says `cancelled` and `expired` capacity is no longer available, and a `failed`
# request never provisioned any. Billing starts when the reservation is provisioned
# (https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityReservation.html,
# https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html).
GONE_STATES = frozenset({"cancelled", "expired", "failed"})


def teardown(
    ec2: Any,
    clock: Clock,
    reservation_id: str | None,
    settings: Settings,
    initial_state: str | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    """Cancel a reservation and verify it gone. Retries with a capped, doubling backoff.

    A `pending` reservation cannot be cancelled (AWS lists the cancellable states:
    `assessing`, `scheduled`, `active`, `delayed`): poll it for the teardown timeout first.
    After that, the cancel loop always runs at least once, whatever the last known state is
    (including `None`, unknown, when every describe call has failed): a describe failure must
    never be read as "nothing to cancel". A cancel call on a state that cannot be cancelled
    (for example still `pending`) just fails, and the failure is recorded in `errors`; it is
    not treated specially. `initial_state` seeds the last known state from an earlier call
    (for example the create-and-poll attempt), so a first describe failure here still has a
    state to fall back on. `verified` is False when the loop never sees `cancelled`, which
    stops the search (ADR 0009, section 2, step 8). A reservation already in `failed` or
    `expired` (like `cancelled`) holds nothing and bills nothing: it is verified at once, with no
    cancel call, and `final_state` says which state was seen.
    """
    if reservation_id is None:
        return {"verified": True, "errors": [], "seconds": 0.0, "final_state": None}
    started = clock.monotonic()
    errors: list[str] = []
    last_state = initial_state

    def describe_or_keep() -> None:
        nonlocal last_state
        try:
            found_state, _ = _describe(ec2, reservation_id)
        except Exception as exc:  # noqa: BLE001 - a failed describe keeps the last known state
            errors.append(error_code(exc))
            log_aws_error(log, PROBE_TYPE, region, exc, call="describe_capacity_reservations")
            return
        last_state = found_state

    describe_or_keep()
    while (
        last_state == "pending" and clock.monotonic() - started < settings.teardown_timeout_seconds
    ):
        clock.sleep(settings.poll_interval_seconds)
        describe_or_keep()

    if last_state in GONE_STATES:
        return {
            "verified": True,
            "errors": errors,
            "seconds": round(clock.monotonic() - started, 3),
            "final_state": last_state,
        }

    verified = False
    first = True
    attempt = 0
    while first or clock.monotonic() - started < settings.teardown_timeout_seconds:
        first = False
        try:
            ec2.cancel_capacity_reservation(CapacityReservationId=reservation_id)
        except Exception as exc:  # noqa: BLE001 - a throttled, failed, or non-cancellable-state
            errors.append(error_code(exc))  # cancel just records the error and retries
            log_aws_error(log, PROBE_TYPE, region, exc, call="cancel_capacity_reservation")
        describe_or_keep()
        if last_state in GONE_STATES:
            verified = True
            break
        if clock.monotonic() - started >= settings.teardown_timeout_seconds:
            break
        backoff = min(settings.poll_interval_seconds * (2**attempt), CANCEL_BACKOFF_CAP_SECONDS)
        clock.sleep(backoff)
        attempt += 1
    if not verified:
        log.warning("odcr.teardown_unverified %s", kv(reservation=reservation_id))
    return {
        "verified": verified,
        "errors": errors,
        "seconds": round(clock.monotonic() - started, 3),
        "final_state": last_state,
    }


def allocate(
    ctx: ProbeContext,
    ec2: Any,
    create_ec2: Any,
    clock: Clock,
    target: dict[str, Any],
    n: int,
    tags: dict[str, str],
    settings: Settings,
    simulated: bool,
) -> Allocation:
    holder: dict[str, Any] = {"id": None, "state": None}
    try:
        attempt = _create_and_poll(
            ctx, ec2, create_ec2, clock, target, n, tags, settings, simulated, holder
        )
    except BaseException:  # noqa: BLE001 - L1: a KeyboardInterrupt must still try to cancel
        teardown(
            ec2,
            clock,
            holder["id"],
            settings,
            initial_state=holder["state"],
            region=target["region"],
        )
        raise
    down = teardown(
        ec2,
        clock,
        attempt.reservation_id,
        settings,
        initial_state=attempt.state,
        region=target["region"],
    )
    details = {**attempt.details, "teardown": down}
    return Allocation(
        attempt.outcome,
        attempt.n_placed,
        attempt.error_code,
        attempt.latency_ms,
        details,
        attempt.extra_rows,
    )


def make_row(
    ctx: ProbeContext,
    target: dict[str, Any],
    *,
    simulated: bool,
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
        provider="aws",
        region=target["region"],
        zone=target.get("zone"),
        probe_type="aws_odcr",
        sku=target["instance_type"],
        placement_group_mode="n/a",
        n_requested=n_requested,
        n_placed=n_placed,
        outcome=outcome,
        error_code=error_code,
        latency_ms=latency_ms,
        details={"zone_id": target.get("zone_id"), **details},
    )


def up_ceiling(target: dict[str, Any], ceiling: int | None) -> int | None:
    """U of ADR 0023: `min(n_max, quota ceiling)`, or `None` when the entry has no `n_max`.
    An unreadable quota (`ceiling` is `None`) leaves `n_max`."""
    if "n_max" not in target:
        return None
    n_max = int(target["n_max"])
    return n_max if ceiling is None else min(n_max, ceiling)


def unproven(rows: list[Record]) -> str | None:
    """Why a zone's rows do not prove that no reservation is open, else `None`.

    The Region goes on to its next zone or entry only on positive proof: every row either holds
    no reservation or has a verified teardown, and an ambiguous create was followed by a
    successful list. Anything else, a row of an unknown shape included, gives a reason.
    """
    if not rows:
        return "no row"
    for row in rows:
        details = row.details
        teardown_ = details.get("teardown")
        if details.get("ambiguous_create") and details.get("cleanup_listed") is not True:
            return "ambiguous create and the list of its reservation failed"
        if teardown_ is not None:
            if teardown_.get("verified") is not True:
                return "teardown not verified"
        elif not details.get("skipped"):
            return "a row with no teardown result"
    return None


def search(
    ctx: ProbeContext,
    ec2: Any,
    create_ec2: Any,
    clock: Clock,
    target: dict[str, Any],
    simulated: bool,
    settings: Settings,
    quota_client: Any = None,
) -> list[Record]:
    """1 zone of a target (`target` holds `zone` and `zone_id`), 1 to `1 + ceil(log2(C))` rows: the
    halving search of ADR 0009 section 2. With
    `n_max`, a full success at C goes up instead (ADR 0023): see `up_ceiling`.

    A real zone has 1 deadline, from before its first attempt: (1 + `up_steps`) x (probe + teardown
    timeout), as the Azure entry budget (ADR 0023, section 5). Before each attempt the probe
    window is the time left minus the teardown timeout; no create call starts when the window is
    shorter than the longest probe so far or than the poll interval. The attempt in progress
    always ends with its verified teardown. A simulated entry takes no wall time: no deadline."""
    n_config = int(target["n"])
    deadline: float | None = None
    if not simulated:
        deadline = clock.monotonic() + (1 + up_steps(target)) * (
            settings.probe_timeout_seconds + settings.teardown_timeout_seconds
        )
    longest = 0.0  # the longest probe so far: a later attempt needs a window as long
    quota_details: dict[str, Any] = {}
    ceiling: int | None = None
    if quota_client is not None:
        ceiling, quota_details = quota_ceiling(quota_client, ec2, target)
    if ceiling == 0:
        return [
            make_row(
                ctx,
                target,
                simulated=simulated,
                n_requested=n_config,
                n_placed=None,
                outcome="quota",
                error_code=None,
                latency_ms=0,
                details={
                    "skipped": True,
                    "reason": "quota ceiling is 0",
                    "n_config": n_config,
                    **quota_details,
                },
            )
        ]
    tags = probe_tags(ctx.experiment, target_tag(target), ctx.run_id)
    lo: int = 0
    hi: int | None = None
    n = n_config if ceiling is None else min(n_config, ceiling)  # C of ADR 0009 section 2
    cap = up_ceiling(target, ceiling)  # U of ADR 0023, or `None` with no `n_max`
    up = False  # True from the first step above C
    step = 1
    rows: list[Record] = []
    while True:
        if ctx.stopping():
            # The process stops: no new attempt starts, not even the first (spec 0003, 6B).
            # A simulated entry never writes `search_stopped` (spec 0010, section 5).
            if rows:
                if not simulated:
                    rows[-1].details["search_stopped"] = "stop"
            else:
                rows.append(
                    make_row(
                        ctx,
                        target,
                        simulated=simulated,
                        n_requested=n_config,
                        n_placed=None,
                        outcome="error",
                        error_code=None,
                        latency_ms=0,
                        details={
                            "skipped": True,
                            "reason": "stop",
                            "n_config": n_config,
                            **({} if simulated else {"search_stopped": "stop"}),
                        },
                    )
                )
            break
        attempt_settings = settings
        if deadline is not None:
            window = deadline - clock.monotonic() - settings.teardown_timeout_seconds
            if window < max(longest, settings.poll_interval_seconds):
                if rows:
                    rows[-1].details["search_stopped"] = "deadline"
                else:
                    rows.append(
                        make_row(
                            ctx,
                            target,
                            simulated=simulated,
                            n_requested=n_config,
                            n_placed=None,
                            outcome="error",
                            error_code=None,
                            latency_ms=0,
                            details={
                                "skipped": True,
                                "reason": "call deadline",
                                "n_config": n_config,
                                "search_stopped": "deadline",
                            },
                        )
                    )
                break
            attempt_settings = replace(
                settings, probe_timeout_seconds=min(settings.probe_timeout_seconds, int(window))
            )
        result = allocate(ctx, ec2, create_ec2, clock, target, n, tags, attempt_settings, simulated)
        longest = max(longest, result.latency_ms / 1000)
        rows.extend(result.extra_rows)
        rows.append(
            make_row(
                ctx,
                target,
                simulated=simulated,
                n_requested=n,
                n_placed=result.n_placed,
                outcome=result.outcome,
                error_code=result.error_code,
                latency_ms=result.latency_ms,
                details={
                    **result.details,
                    **quota_details,
                    "search_step": step,
                    "n_config": n_config,
                    **({"search_direction": "up", "n_max": int(target["n_max"])} if up else {}),
                },
            )
        )
        if not result.details["teardown"]["verified"] or (up and result.outcome != "success"):
            break
        if result.outcome == "success":
            lo = n
            if hi is None:
                # A full success at C: go up when `n_max` allows, else stop (ADR 0023). A step
                # that is not a success ends the search: no bisection above C.
                if cap is None or n >= cap:
                    break
                n = min(2 * n, cap)
                up = True
                step += 1
                continue
            next_n = (lo + hi) // 2
            if next_n == lo:
                break
            n = next_n
        elif result.outcome == "no_capacity":
            hi = n
            next_n = (lo + hi) // 2
            if next_n == lo:
                break
            n = next_n
        else:
            break
        step += 1
    return rows


def _ambiguous_create_cleanup(
    ctx: ProbeContext,
    ec2: Any,
    clock: Clock,
    settings: Settings,
    target: dict[str, Any],
    tags: dict[str, str],
    simulated: bool,
) -> tuple[list[Record], bool]:
    """The last resort after every retry of an ambiguous create error (same `ClientToken`)
    still fails. No age threshold, unlike `cleanup`: this runs once, right after the create,
    for 1 attempt whose reservation (if the create did succeed server-side) was just made, so
    waiting for it to age would leave it running until `EndDate`. Matches only this attempt's
    own `experiment`, `run_id`, and `target` tags, so it can never touch another attempt's or
    another cycle's reservation. Tries to cancel every match, `active` or not: a cancel on a
    state that cannot be cancelled just fails and is recorded (`teardown` does the same).
    Returns `(rows, listed)`: `listed` is False when the list call failed, so the caller cannot
    know whether a reservation is open.
    """
    run_id = str(uuid.uuid4())
    found: list[dict[str, Any]] = []
    token: str | None = None
    try:
        while True:
            kwargs: dict[str, Any] = {
                "Filters": [
                    {"Name": "tag:experiment", "Values": [tags["experiment"]]},
                    {"Name": "tag:run_id", "Values": [tags["run_id"]]},
                    {"Name": "tag:target", "Values": [tags["target"]]},
                ]
            }
            if token:
                kwargs["NextToken"] = token
            resp = ec2.describe_capacity_reservations(**kwargs)
            found.extend(resp.get("CapacityReservations") or [])
            token = resp.get("NextToken")
            if not token:
                break
    except Exception:  # noqa: BLE001 - a failed list here never stops the probe
        log.exception("odcr.ambiguous_cleanup_failed %s", kv(region=target["region"]))
        return [], False
    rows = []
    for reservation in found:
        reservation_id = reservation["CapacityReservationId"]
        down = teardown(
            ec2,
            clock,
            reservation_id,
            settings,
            initial_state=reservation.get("State"),
            region=target["region"],
        )
        rows.append(
            Record(
                ts_utc=ctx.timestamp(),
                run_id=run_id,
                simulated=simulated,
                provider="aws",
                region=target["region"],
                zone=reservation.get("AvailabilityZone") or target.get("zone"),
                probe_type="aws_odcr",
                sku=reservation.get("InstanceType") or target["instance_type"],
                placement_group_mode="n/a",
                n_requested=0,
                n_placed=None,
                outcome="success" if down["verified"] else "error",
                error_code=(
                    None if down["verified"] else (down["errors"][-1] if down["errors"] else None)
                ),
                latency_ms=0,
                details={
                    "cleanup": True,
                    "zone_id": reservation.get("AvailabilityZoneId") or target.get("zone_id"),
                    "ambiguous_create_cleanup": True,
                    "reservation_id": reservation_id,
                    "teardown": down,
                },
            )
        )
    return rows, True


def cleanup(
    ctx: ProbeContext, ec2: Any, clock: Clock, settings: Settings, region: str, simulated: bool
) -> list[Record]:
    """Cancel this probe's own `active` reservations older than probe + teardown timeout.

    Spec 0002 section 4.9. A `pending` reservation is left to its `EndDate` backstop: it
    cannot be cancelled. Runs once per Region per probe call, before that Region's entries,
    with the real EC2 client (never simulated, so there is always something to clean).

    These rows get their own `run_id`, not `ctx.run_id`: a cleanup row is not an attempt of
    any target's search, and ADR 0009 section 2 step 11 derives a cycle's result from the
    rows that share a `run_id` and a target. Giving cleanup rows `ctx.run_id` could turn a
    cycle with no successful or `no_capacity` attempt (result: null) into a false 0.
    """
    threshold = settings.probe_timeout_seconds + settings.teardown_timeout_seconds
    run_id = str(uuid.uuid4())
    found: list[dict[str, Any]] = []
    token: str | None = None
    try:
        while True:
            kwargs: dict[str, Any] = {
                "Filters": [
                    {"Name": "tag:experiment", "Values": [ctx.experiment]},
                    {"Name": "state", "Values": ["active"]},
                ]
            }
            if token:
                kwargs["NextToken"] = token
            resp = ec2.describe_capacity_reservations(**kwargs)
            found.extend(resp.get("CapacityReservations") or [])
            token = resp.get("NextToken")
            if not token:
                break
    except Exception:  # noqa: BLE001 - a failed cleanup list never stops the probe
        log.exception("odcr.cleanup_failed %s", kv(region=region))
        return []
    rows = []
    for reservation in found:
        created = reservation.get("CreateDate")
        age = (ctx.now() - created).total_seconds() if created else None
        if age is None or age < threshold:
            continue
        reservation_id = reservation["CapacityReservationId"]
        down = teardown(ec2, clock, reservation_id, settings, region=region)
        rows.append(
            Record(
                ts_utc=ctx.timestamp(),
                run_id=run_id,
                simulated=simulated,
                provider="aws",
                region=region,
                zone=reservation.get("AvailabilityZone"),
                probe_type="aws_odcr",
                sku=reservation.get("InstanceType") or "unknown",
                placement_group_mode="n/a",
                n_requested=0,
                n_placed=None,
                outcome="success" if down["verified"] else "error",
                error_code=(
                    None if down["verified"] else (down["errors"][-1] if down["errors"] else None)
                ),
                latency_ms=_ms(age),
                details={
                    "cleanup": True,
                    "zone_id": reservation.get("AvailabilityZoneId"),
                    "reservation_id": reservation_id,
                    "age_seconds": round(age, 3),
                    "teardown": down,
                },
            )
        )
    return rows


class OdcrProbe(Probe):
    probe_type = "aws_odcr"
    provider = "aws"
    default_cadence = "2h"
    region_lanes = True  # off the signal thread, 1 lane per Region (spec 0003, section 6B)

    def __init__(self) -> None:
        self._sim_counters: dict[tuple[Any, ...], int] = {}

    def run(self, ctx: ProbeContext) -> list[Record]:
        section = ctx.config
        settings = odcr_settings(section)
        rows: list[Record] = []
        cleaned: set[str] = set()
        bound: dict[str, int] = {}  # Region -> worst-case lane seconds, zones counted
        warned: set[str] = set()  # Regions whose bound warning is logged this cycle
        halted: set[str] = set()  # Regions with an unverified teardown this cycle (ADR 0009, 8)
        for target in section.get("provisioning_probes") or []:
            region = target["region"]
            if ctx.stopping():  # the process stops: no new entry (spec 0003, section 6B)
                log.info("odcr.stopped %s", kv(region=region))
                break
            if region in halted:
                log.warning(
                    "odcr.region_halted %s",
                    kv(region=region, instance_type=target["instance_type"]),
                )
                continue
            simulated = ctx.simulated(target)
            # Cleanup always uses the real client: a simulated entry has nothing to clean up,
            # and using whichever entry happened to come first in the Region would silently
            # skip a real stale reservation when that first entry is simulated (spec 0002,
            # section 4.9).
            try:
                ec2, clock = self._client_for(ctx, target, simulated)
            except Exception as exc:  # noqa: BLE001 - a client build failure is an error row
                rows.extend(ctx.emit([self._build_failed_row(ctx, target, simulated, exc)]))
                continue
            if not simulated and region not in cleaned:
                cleaned.add(region)
                try:
                    rows.extend(ctx.emit(cleanup(ctx, ec2, clock, settings, region, simulated)))
                except Exception:  # noqa: BLE001 - a failed cleanup never stops the entries
                    log.exception("odcr.cleanup_failed %s", kv(region=region))
            pre_outcome, zones, pre_code, pre_latency = zones_offering(ec2, clock, target)
            if pre_outcome != "success":
                reason = {
                    "restricted": "type not offered in any zone of the Region",
                    "unnamed": "the zone IDs that offer the type have no zone name in this account",
                }.get(pre_outcome, "precheck failed")
                n_config = int(target["n"])
                rows.extend(
                    ctx.emit(
                        [
                            make_row(
                                ctx,
                                target,
                                simulated=simulated,
                                n_requested=n_config,
                                n_placed=None,
                                outcome="restricted" if pre_outcome == "restricted" else "error",
                                error_code=pre_code,
                                latency_ms=pre_latency,
                                details={"skipped": True, "reason": reason, "n_config": n_config},
                            )
                        ]
                    )
                )
                continue
            self._warn_when_the_zone_bound_exceeds_the_cadence(
                section, settings, bound, warned, target, len(zones)
            )
            for position, zone in enumerate(zones):  # 1 search per zone, each on its own n, n_max
                if position and ctx.stopping():  # the first zone writes the stop row
                    log.info(
                        "odcr.stopped %s",
                        kv(region=region, zones_not_run=len(zones) - position),
                    )
                    break
                zone_target = {**target, **zone}
                try:
                    zone_ec2 = ec2
                    if simulated:
                        zone_ec2, clock = self._client_for(ctx, zone_target, simulated)
                    create_ec2 = self._create_client_for(ctx, zone_target, simulated, zone_ec2)
                    quota_client = self._quota_client_for(ctx, zone_target, simulated)
                except Exception as exc:  # noqa: BLE001 - a client build failure is an error row
                    rows.extend(
                        ctx.emit([self._build_failed_row(ctx, zone_target, simulated, exc)])
                    )
                    continue
                try:
                    entry_rows = search(
                        ctx, zone_ec2, create_ec2, clock, zone_target, simulated, settings,
                        quota_client,
                    )  # fmt: skip
                    rows.extend(ctx.emit(entry_rows))
                    why = unproven(entry_rows)
                except Exception:  # noqa: BLE001 - 1 failed zone never stops the other zones
                    log.exception(
                        "odcr.failed %s",
                        kv(
                            instance_type=target["instance_type"],
                            region=region,
                            zone=zone["zone"],
                            zone_id=zone["zone_id"],
                        ),
                    )
                    why = "an exception escaped the zone"
                if why is not None and not simulated:
                    # A simulated entry opens no real reservation, so it never halts a Region.
                    # ADR 0009 step 8: a reservation may be open. No create follows in this
                    # Region this cycle; the cleanup and the sweeper cancel it.
                    halted.add(region)
                    log.warning(
                        "odcr.region_halted %s",
                        kv(
                            region=region,
                            zone_id=zone["zone_id"],
                            reason=why,
                            zones_not_run=len(zones) - position - 1,
                        ),
                    )
                    break
        return rows

    def _warn_when_the_zone_bound_exceeds_the_cadence(
        self,
        section: dict[str, Any],
        settings: Settings,
        bound: dict[str, int],
        warned: set[str],
        target: dict[str, Any],
        zone_count: int,
    ) -> None:
        """The worst-case lane of a Region, with every zone of every entry so far at its full
        budget, logged as a warning when it is not shorter than the cadence (spec 0003, section
        6B, rule 8). The bound is a timeout bound, far above a real cycle; a cycle
        that does run long is recorded by the scheduler as `lane.overrun`. It never refuses."""
        region = target["region"]
        entry_budget = (1 + up_steps(target)) * (
            settings.probe_timeout_seconds + settings.teardown_timeout_seconds
        )
        bound[region] = bound.get(region, 0) + zone_count * entry_budget
        cadence_text = (section.get("cadence") or {}).get(
            "aws_odcr", AWS_DEFAULT_PROVISIONING_CADENCE
        )
        try:
            cadence = parse_cadence(cadence_text)
        except ValueError:
            return
        if bound[region] >= cadence and region not in warned:
            warned.add(region)
            log.warning(
                "odcr.lane_bound_exceeds_cadence %s",
                kv(region=region, bound_seconds=bound[region], cadence_seconds=cadence),
            )

    def _build_failed_row(
        self, ctx: ProbeContext, target: dict[str, Any], simulated: bool, exc: Exception
    ) -> Record:
        """The `error` row of an entry whose client build raised (spec 0002, section 4.9). The
        message goes to the log as returned; the next entry builds again."""
        log_aws_error(log, self.probe_type, target["region"], exc, event="odcr.client_failed")
        return make_row(
            ctx,
            target,
            simulated=simulated,
            n_requested=int(target["n"]),
            n_placed=None,
            outcome="error",
            error_code=error_code(exc),
            latency_ms=0,
            details={"n_ceiling": None, "reason": "Client build failed"},
        )

    def _client_for(
        self, ctx: ProbeContext, target: dict[str, Any], simulated: bool
    ) -> tuple[Any, Clock]:
        """Simulated: a `SimEc2` with a virtual clock. Else: the real client."""
        if simulated:
            # A target without a zone is the Region call (the offerings call): the first step.
            step = self._next_sim_step(target) if "zone" in target else "success"
            return SimEc2(step, target["region"]), VirtualClock()
        return ctx.clients.boto3("ec2", target["region"]), RealClock()

    def _create_client_for(
        self, ctx: ProbeContext, target: dict[str, Any], simulated: bool, ec2: Any
    ) -> Any:
        """The `Create` client: `max_attempts=1`, so botocore's own retry of a create call
        never hides a second attempt behind what the probe counts as 1 logical attempt."""
        if simulated:
            return ec2
        return ctx.clients.boto3("ec2", target["region"], max_attempts=1)

    def _quota_client_for(self, ctx: ProbeContext, target: dict[str, Any], simulated: bool) -> Any:
        """The Service Quotas client. A simulated entry calls no provider, so it has none."""
        if simulated:
            return None
        return ctx.clients.boto3("service-quotas", target["region"])

    def _next_sim_step(self, target: dict[str, Any]) -> str:
        script = script_of(target)
        key = (target["instance_type"], target["region"], target["zone_id"])
        index = self._sim_counters.get(key, 0)
        self._sim_counters[key] = index + 1
        return script[index % len(script)]
