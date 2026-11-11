"""Earliest Capacity Block start. Spec 0008 section 3A."""

from datetime import datetime, timedelta
from typing import Any

from ftg_prober.aws.cb_offerings import THROTTLE_CODE, offering_rows
from ftg_prober.callqueue import Call
from ftg_prober.config import counts_for, earliest_hours
from ftg_prober.probes import Probe, ProbeContext
from ftg_prober.record import Record

# AWS moves a start at or before now + 30 min to now + 30 min (observed, spec 0008 3A.1).
LEAD = timedelta(minutes=30)
# A Capacity Block ends at 11:30 UTC. A start is rounded up to this time of day.
BOUNDARY_HOUR, BOUNDARY_MINUTE = 11, 30
SLACK = timedelta(hours=1)
MIN_RANGE = timedelta(days=1)


class CapacityBlockEarliestProbe(Probe):
    """1 `DescribeCapacityBlockOfferings` call per Region, instance type, and duration."""

    probe_type = "aws_cb_earliest"
    provider = "aws"
    default_cadence = "2h"
    throttle_code = THROTTLE_CODE

    def run(self, ctx: ProbeContext) -> list[Record]:
        return [row for call in self.plan(ctx.config) for row in self.run_call(ctx, call)]

    def plan(self, config: dict[str, Any]) -> list[Call]:
        return [Call(self.probe_type, r, (t, h, n)) for r, t, h, n in earliest_plan(config)]

    def run_call(self, ctx: ProbeContext, call: Call) -> list[Record]:
        region = call.region
        instance_type, hours, count = call.args
        start, end = search_window(ctx.now(), hours)
        return offering_rows(
            ctx,
            self.probe_type,
            ctx.clients.boto3("ec2", region, max_attempts=1),
            region,
            instance_type,
            count,
            hours,
            start=start,
            end=end,
            query={"duration_hours": hours},
        )


def next_boundary(first: datetime) -> datetime:
    """The next 11:30 UTC at or after `first`."""
    boundary = first.replace(hour=BOUNDARY_HOUR, minute=BOUNDARY_MINUTE, second=0, microsecond=0)
    return boundary if boundary >= first else boundary + timedelta(days=1)


def search_window(now: datetime, hours: int) -> tuple[datetime, datetime]:
    """`(StartDateRange, EndDateRange)` that admit the earliest start and no later one.

    `first` is the start AWS uses for `StartDateRange = now`: now + 30 min. The next 11:30 UTC
    at or after `first` is the `boundary`. A block that starts at `first` or at `boundary` fits.
    A block that starts after `boundary` ends too late. The end is at least 1 day after `first`,
    or AWS rejects the call. Spec 0008, 3A.2.
    """
    first = now + LEAD
    end = next_boundary(first) + timedelta(hours=hours) + SLACK
    return now, max(end, first + MIN_RANGE)


def earliest_plan(config: dict[str, Any]) -> list[tuple[str, str, int, int]]:
    """The calls of 1 run, in order: `(region, instance_type, duration_hours, instance_count)`.
    Spec 0008, 3A.3."""
    blocks = config["capacity_blocks"]
    return [
        (region, instance_type, hours, count)
        for region in config["regions"]
        for instance_type in blocks["instance_types"]
        for count in counts_for(blocks, instance_type)
        for hours in earliest_hours(blocks)
    ]
