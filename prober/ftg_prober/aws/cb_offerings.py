"""Capacity Block offerings. Spec 0002 section 4.1, spec 0008 section 3."""

from datetime import datetime, timedelta
from typing import Any

from ftg_prober.aws.common import paginate, row, to_float
from ftg_prober.callqueue import Call
from ftg_prober.config import counts_for
from ftg_prober.probes import Probe, ProbeContext
from ftg_prober.record import Record, format_ts

MAX_RESULTS = 1000
# The code of a throttled call. The scheduler puts such a call back on the queue (spec 0008, 3.3).
THROTTLE_CODE = "RequestLimitExceeded"


class CapacityBlockOfferingsProbe(Probe):
    """1 `DescribeCapacityBlockOfferings` call per Region, instance type, and duration."""

    probe_type = "aws_cb_offering"
    provider = "aws"
    default_cadence = "2h"
    throttle_code = THROTTLE_CODE

    def run(self, ctx: ProbeContext) -> list[Record]:
        return [row for call in self.plan(ctx.config) for row in self.run_call(ctx, call)]

    def plan(self, config: dict[str, Any]) -> list[Call]:
        return [Call(self.probe_type, r, (t, h, n)) for r, t, h, n in request_plan(config)]

    def run_call(self, ctx: ProbeContext, call: Call) -> list[Record]:
        region = call.region
        instance_type, hours, count = call.args
        blocks = ctx.config["capacity_blocks"]
        ec2 = ctx.clients.boto3("ec2", region, max_attempts=1)
        return self._call(
            ctx,
            ec2,
            region,
            instance_type,
            count,
            hours,
            int(blocks["start_window_days"]),
        )

    def _call(
        self,
        ctx: ProbeContext,
        ec2: Any,
        region: str,
        instance_type: str,
        count: int,
        hours: int,
        window_days: int,
    ) -> list[Record]:
        now = ctx.now()
        return offering_rows(
            ctx,
            self.probe_type,
            ec2,
            region,
            instance_type,
            count,
            hours,
            start=now,
            end=now + timedelta(days=window_days, hours=hours),
            query={"duration_hours": hours, "start_window_days": window_days},
        )


def offering_rows(
    ctx: ProbeContext,
    probe_type: str,
    ec2: Any,
    region: str,
    instance_type: str,
    count: int,
    hours: int,
    *,
    start: datetime,
    end: datetime,
    query: dict[str, Any],
) -> list[Record]:
    """1 `DescribeCapacityBlockOfferings` call and its rows. Spec 0008, 3.2. `query` is the
    `details` of a `none` or `error` row."""
    result = paginate(
        ec2.describe_capacity_block_offerings,
        {
            "InstanceType": instance_type,
            "InstanceCount": count,
            "CapacityDurationHours": hours,
            "StartDateRange": start,
            "EndDateRange": end,
            "MaxResults": MAX_RESULTS,
        },
        "CapacityBlockOfferings",
        probe_type=probe_type,
        region=region,
    )
    ctx.api_calls += max(1, result.pages)
    if result.error_code:
        return [
            row(
                ctx,
                probe_type,
                region=region,
                sku=instance_type,
                n_requested=count,
                outcome="error",
                error_code=result.error_code,
                latency_ms=result.latency_ms,
                details=query,
            )
        ]
    if not result.items:
        return [
            row(
                ctx,
                probe_type,
                region=region,
                sku=instance_type,
                n_requested=count,
                outcome="none",
                latency_ms=result.latency_ms,
                details=query,
            )
        ]
    return [
        row(
            ctx,
            probe_type,
            region=region,
            zone=offering.get("AvailabilityZone"),
            sku=offering.get("InstanceType") or instance_type,
            n_requested=count,
            outcome="success",
            latency_ms=result.latency_ms,
            details={
                "start": _ts(offering.get("StartDate")),
                "end": _ts(offering.get("EndDate")),
                "duration_hours": offering.get("CapacityBlockDurationHours", hours),
                "duration_minutes": offering.get("CapacityBlockDurationMinutes"),
                "instance_count": offering.get("InstanceCount", count),
                "upfront_fee": to_float(offering.get("UpfrontFee")),
                "currency": offering.get("CurrencyCode"),
                "offering_id": offering.get("CapacityBlockOfferingId"),
                "tenancy": offering.get("Tenancy"),
                "zone_type": offering.get("ZoneType"),
                "ultraserver_type": offering.get("UltraserverType"),
                "ultraserver_count": offering.get("UltraserverCount"),
            },
        )
        for offering in result.items
    ]


def request_plan(config: dict[str, Any]) -> list[tuple[str, str, int, int]]:
    """The calls of 1 run, in order: `(region, instance_type, duration_hours, instance_count)`.
    Spec 0008, 3.3."""
    blocks = config["capacity_blocks"]
    return [
        (region, instance_type, int(hours), count)
        for region in config["regions"]
        for instance_type in blocks["instance_types"]
        for count in counts_for(blocks, instance_type)
        for hours in blocks["duration_hours"]
    ]


def _ts(value: Any) -> str | None:
    if isinstance(value, datetime):
        return format_ts(value)
    return None if value is None else str(value)
