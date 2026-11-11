"""The first free day and the longest Capacity Block per type and Region. Spec 0008 section 3B.

A search runs as a sequence of 1-call steps on the call queue (spec 0003, section 6A). The state
of a search travels in `Call.args`: the probe instance holds no per-search state. Each step's
`run_call` embeds the next `Call` in `details["_next"]` of the row it returns; `follow_up` only
reads that key. The scheduler writes no row for a call whose `follow_up` adds another call, so an
intermediate step writes nothing; the last step of a search has no `_next`, and its row is the 1
row of the search (spec 0003, section 6A, rule 5).
"""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ftg_prober.aws.cb_earliest import LEAD, next_boundary, search_window
from ftg_prober.aws.cb_offerings import THROTTLE_CODE
from ftg_prober.aws.common import paginate, row, to_float
from ftg_prober.callqueue import Call
from ftg_prober.config import counts_for
from ftg_prober.probes import Probe, ProbeContext
from ftg_prober.record import Record, format_ts

# The smallest j (days) with a 1-day offer is bisected in this range (spec 0008, 3B.2).
BISECT_MAX_DAYS = 56
# The sentinel upper bound of the earliest-start bisection: no j in 1..56 has an offer.
BISECT_NONE = BISECT_MAX_DAYS + 1
# The gallop sequence of section 3B.2, in days.
GALLOP_DAYS = (2, 4, 8, 14, 28, 56, 112, 182)
# Every duration the API allows: 1-day steps to 14 days, then 7-day steps to 182 (spec 0008, 3.1).
ALLOWED_DAYS = tuple(range(1, 15)) + tuple(range(21, 183, 7))
# A search never makes more than this many calls (spec 0008, 3B.2).
MAX_STEPS = 19
SLACK = timedelta(hours=1)


@dataclass(frozen=True)
class Step:
    """1 call of a search. Spec 0008, 3B.4."""

    purpose: str
    duration_hours: int
    start: str
    end: str
    result: str  # "offer", "none", or "error"
    start_date: str | None
    end_date: str | None
    zone: str | None
    offering_id: str | None
    upfront_fee: float | None
    currency: str | None
    error_code: str | None
    ts: str
    b0: str
    duration_minutes: int | None = None


@dataclass(frozen=True)
class Offer:
    """A raw offer snapshot carried forward in `Call.args`."""

    start_date: str
    end_date: str | None
    zone: str | None
    offering_id: str | None
    upfront_fee: float | None
    currency: str | None
    duration_hours: int
    duration_minutes: int | None = None


class CapacityBlockLongestProbe(Probe):
    """1 `DescribeCapacityBlockOfferings` call per step of a search for each Region and type."""

    probe_type = "aws_cb_longest"
    provider = "aws"
    default_cadence = "2h"
    throttle_code = THROTTLE_CODE

    def run(self, ctx: ProbeContext) -> list[Record]:
        """`--once`: run every search of `plan` to its end, with no queue (spec 0008, 3B.3)."""
        rows = []
        for call in self.plan(ctx.config):
            rows.extend(self._run_to_end(ctx, call))
        return rows

    def _run_to_end(self, ctx: ProbeContext, call: Call) -> list[Record]:
        while True:
            result = self.run_call(ctx, call)
            if not result:
                return []
            next_call = result[0].details.get("_next")
            if next_call is None:
                return result
            call = next_call

    def plan(self, config: dict[str, Any]) -> list[Call]:
        blocks = config["capacity_blocks"]
        return [
            Call(
                self.probe_type,
                region,
                _initial_args(instance_type, count),
                key=(instance_type, count),
            )
            for region in config["regions"]
            for instance_type in blocks["instance_types"]
            for count in counts_for(blocks, instance_type)
        ]

    def follow_up(self, call: Call, rows: list[Record]) -> list[Call]:
        if not rows:
            return []
        next_call = rows[0].details.get("_next")
        return [next_call] if next_call is not None else []

    def run_call(self, ctx: ProbeContext, call: Call) -> list[Record]:
        instance_type, count, phase, phase_state, earliest, longest, steps, planned_at, b0 = (
            call.args
        )
        now = ctx.now()
        planned_at = planned_at or format_ts(now)
        # B0 is fixed once, at the first step of the search (`earliest0`), and carried forward
        # unchanged: recomputing it on every call would move the earliest-bisect window by a day
        # whenever `now` crosses 11:00 UTC mid-search, which breaks the bisection (spec 0008,
        # section 3B.2).
        b0 = b0 or format_ts(next_boundary(now + LEAD))
        region = call.region
        ec2 = ctx.clients.boto3("ec2", region, max_attempts=1)

        purpose, hours, start, end = _request(now, phase, phase_state, earliest, b0)
        result = paginate(
            ec2.describe_capacity_block_offerings,
            {
                "InstanceType": instance_type,
                "InstanceCount": count,
                "CapacityDurationHours": hours,
                "StartDateRange": start,
                "EndDateRange": end,
                "MaxResults": 1000,
            },
            "CapacityBlockOfferings",
            probe_type=self.probe_type,
            region=region,
        )
        ctx.api_calls += max(1, result.pages)

        if result.error_code == THROTTLE_CODE:
            # A throttled attempt is not a step: `steps` keeps the search's state unchanged. The
            # queue puts a non-final throttled attempt back and never writes this row (spec 0003,
            # section 6A, rule 4); the last allowed attempt is written as is, so the search ends
            # with a full error row, not an empty one (spec 0008, section 3B.3).
            return [
                row(
                    ctx,
                    self.probe_type,
                    region=region,
                    zone=earliest.zone if earliest else None,
                    sku=instance_type,
                    n_requested=count,
                    outcome="error",
                    error_code=THROTTLE_CODE,
                    latency_ms=result.latency_ms,
                    details=_final_details(steps, earliest, longest, planned_at, b0),
                )
            ]

        offer = _pick_offer(result.items, phase, earliest)
        step = Step(
            purpose=purpose,
            # The raw `CapacityBlockDurationHours` of the offer, like `offering_rows`; else the
            # requested duration, for a `none` or `error` step.
            duration_hours=(offer.get("CapacityBlockDurationHours", hours) if offer else hours),
            start=format_ts(start),
            end=format_ts(end),
            result="error" if result.error_code else ("offer" if offer else "none"),
            start_date=_ts(offer.get("StartDate")) if offer else None,
            end_date=_ts(offer.get("EndDate")) if offer else None,
            zone=offer.get("AvailabilityZone") if offer else None,
            offering_id=offer.get("CapacityBlockOfferingId") if offer else None,
            upfront_fee=to_float(offer.get("UpfrontFee")) if offer else None,
            currency=offer.get("CurrencyCode") if offer else None,
            duration_minutes=offer.get("CapacityBlockDurationMinutes") if offer else None,
            error_code=result.error_code,
            ts=format_ts(now),
            b0=b0,
        )
        steps = (*steps, step)

        if result.error_code:
            final_args = (ctx, region, instance_type, count, result, steps, earliest, longest)
            return [self._final(*final_args, "error", planned_at, b0)]

        # Advance first, so the offer of this very step (the one that ends the search, or the
        # one taken at the 19-call cap) is never lost (spec 0008, section 3B.2).
        next_phase, next_state, earliest, longest = _advance(
            phase, phase_state, step, earliest, longest
        )
        final_args = (ctx, region, instance_type, count, result, steps, earliest, longest)
        if next_phase is None or len(steps) >= MAX_STEPS:
            outcome = "success" if earliest else "none"
            return [self._final(*final_args, outcome, planned_at, b0)]
        next_args = (
            instance_type,
            count,
            next_phase,
            next_state,
            earliest,
            longest,
            steps,
            planned_at,
            b0,
        )

        return [
            row(
                ctx,
                self.probe_type,
                region=region,
                sku=instance_type,
                n_requested=count,
                outcome="none",
                latency_ms=result.latency_ms,
                details={
                    "_next": Call(self.probe_type, region, next_args, key=(instance_type, count))
                },
            )
        ]

    def _final(
        self,
        ctx: ProbeContext,
        region: str,
        instance_type: str,
        count: int,
        result: Any,
        steps: tuple[Step, ...],
        earliest: Offer | None,
        longest: Offer | None,
        outcome: str,
        planned_at: str,
        b0: str,
    ) -> Record:
        return row(
            ctx,
            self.probe_type,
            region=region,
            zone=earliest.zone if earliest else None,
            sku=instance_type,
            n_requested=count,
            outcome=outcome,
            error_code=steps[-1].error_code if outcome == "error" else None,
            latency_ms=result.latency_ms,
            details=_final_details(steps, earliest, longest, planned_at, b0),
        )


def _initial_args(instance_type: str, count: int) -> tuple[Any, ...]:
    return (instance_type, count, "earliest0", (), None, None, (), None, None)


def _request(
    now: datetime, phase: str, phase_state: tuple[Any, ...], earliest: Offer | None, b0: str
) -> tuple[str, int, datetime, datetime]:
    """`(purpose, duration_hours, StartDateRange, EndDateRange)` of the step. Spec 0008, 3B.2-3.

    `b0` is the search's fixed B0 (section 3B.2): the next 11:30 UTC boundary at or after the
    search's first call, not recomputed from `now` on every step.
    """
    if phase == "earliest0":
        start, end = search_window(now, 24)
        return "earliest.0", 24, start, end
    if phase == "earliest_bisect":
        lo, hi, _hi_offer = phase_state
        j = (lo + hi) // 2
        return "earliest_bisect", 24, now, _parse(b0) + timedelta(days=j + 1, hours=1)
    if phase == "gallop":
        (idx,) = phase_state
        days = GALLOP_DAYS[idx]
        return "gallop", days * 24, now, _parse(earliest.start_date) + timedelta(days=days) + SLACK
    if phase == "bisect":
        lo_idx, hi_idx = phase_state
        mid = (lo_idx + hi_idx) // 2
        days = ALLOWED_DAYS[mid]
        return "bisect", days * 24, now, _parse(earliest.start_date) + timedelta(days=days) + SLACK
    raise ValueError(f"unknown phase {phase!r}")


def _advance(
    phase: str,
    phase_state: tuple[Any, ...],
    step: Step,
    earliest: Offer | None,
    longest: Offer | None,
) -> tuple[str | None, tuple[Any, ...] | None, Offer | None, Offer | None]:
    """`(next_phase, next_state, earliest, longest)`. `next_phase` is None when the search is
    finished; `earliest` and `longest` always reflect this step, even when it is the last one."""
    if phase == "earliest0":
        if step.result == "offer":
            offer = _offer_of(step)
            return "gallop", (0,), offer, offer
        return "earliest_bisect", (0, BISECT_NONE, None), None, None
    if phase == "earliest_bisect":
        lo, hi, hi_offer = phase_state
        j = (lo + hi) // 2
        if step.result == "offer":
            hi, hi_offer = j, _offer_of(step)
        else:
            lo = j
        if hi - lo <= 1:
            if hi == BISECT_NONE:
                return None, None, None, None
            return "gallop", (0,), hi_offer, hi_offer
        return "earliest_bisect", (lo, hi, hi_offer), None, None
    if phase == "gallop":
        (idx,) = phase_state
        days = GALLOP_DAYS[idx]
        if step.result == "offer" and _at_s(_parse(step.start_date), _parse(earliest.start_date)):
            longest = _offer_of(step)
            if idx + 1 >= len(GALLOP_DAYS):
                return None, None, earliest, longest
            return "gallop", (idx + 1,), earliest, longest
        lo_days = 1 if idx == 0 else GALLOP_DAYS[idx - 1]
        lo_idx, hi_idx = ALLOWED_DAYS.index(lo_days), ALLOWED_DAYS.index(days)
        if hi_idx - lo_idx <= 1:
            return None, None, earliest, longest
        return "bisect", (lo_idx, hi_idx), earliest, longest
    if phase == "bisect":
        lo_idx, hi_idx = phase_state
        mid = (lo_idx + hi_idx) // 2
        if step.result == "offer" and _at_s(_parse(step.start_date), _parse(earliest.start_date)):
            longest = _offer_of(step)
            lo_idx = mid
        else:
            hi_idx = mid
        if hi_idx - lo_idx <= 1:
            return None, None, earliest, longest
        return "bisect", (lo_idx, hi_idx), earliest, longest
    raise ValueError(f"unknown phase {phase!r}")


def _offer_of(step: Step) -> Offer:
    return Offer(
        start_date=step.start_date,
        end_date=step.end_date,
        zone=step.zone,
        offering_id=step.offering_id,
        upfront_fee=step.upfront_fee,
        currency=step.currency,
        duration_hours=step.duration_hours,
        duration_minutes=step.duration_minutes,
    )


def _at_s(candidate: datetime, s: datetime) -> bool:
    """Whether `candidate` is the same possible block start as `s` (spec 0008, section 3B.2).

    An AWS-chosen "first" start (now + 30 minutes, not an 11:30 UTC boundary) drifts later from
    1 call to the next, because each call's `now` is later than the last: comparing `StartDate`
    for exact equality would see this drift as a different start and stop the gallop or bisect
    too early. 2 starts are the same target when they fall on the same side of the same 11:30
    UTC boundary: the boundary at or after each is on the same calendar day.
    """
    return next_boundary(candidate).date() == next_boundary(s).date()


def _pick_offer(items: list[dict[str, Any]], phase: str, earliest: Offer | None) -> dict | None:
    """The offering of `items` that answers this step's question, or None for a `none` step
    (spec 0008, section 3B.2).

    `items` is not sorted and is not 1 item: the API returns up to 6, by price, not by date. An
    item with no `StartDate` answers neither question and is dropped; with none left, the step
    reads as `none`, like an empty `items`. The earliest phases (`earliest0`, `earliest_bisect`)
    ask "what is the earliest start in this window", so the item with the smallest `StartDate`
    wins. The gallop and bisect phases ask "is there still an offer at `S`", so an item that is
    `_at_s` wins; with no item at `S`, the step is a stop, and reads as `none`, not `offer`, even
    though the call did return offerings.
    """
    items = [item for item in items if item.get("StartDate") is not None]
    if not items:
        return None
    if phase in ("earliest0", "earliest_bisect"):
        return min(items, key=lambda item: item["StartDate"])
    for item in items:
        if earliest and _at_s(item["StartDate"], _parse(earliest.start_date)):
            return item
    return None


def _final_details(
    steps: tuple[Step, ...],
    earliest: Offer | None,
    longest: Offer | None,
    planned_at: str,
    b0: str,
) -> dict[str, Any]:
    return {
        "earliest_start": earliest.start_date if earliest else None,
        "earliest_end": earliest.end_date if earliest else None,
        "earliest_duration_hours": earliest.duration_hours if earliest else None,
        "earliest_duration_minutes": earliest.duration_minutes if earliest else None,
        "earliest_offering_id": earliest.offering_id if earliest else None,
        "earliest_upfront_fee": earliest.upfront_fee if earliest else None,
        "earliest_currency": earliest.currency if earliest else None,
        "b0": b0,
        "longest_start": longest.start_date if longest else None,
        "longest_duration_hours": longest.duration_hours if longest else None,
        "longest_duration_minutes": longest.duration_minutes if longest else None,
        "longest_end": longest.end_date if longest else None,
        "longest_offering_id": longest.offering_id if longest else None,
        "longest_upfront_fee": longest.upfront_fee if longest else None,
        "steps": [asdict(s) for s in steps],
        "calls": len(steps),
        "planned_at": planned_at,
    }


def _ts(value: Any) -> str | None:
    if isinstance(value, datetime):
        return format_ts(value)
    return None if value is None else str(value)


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
