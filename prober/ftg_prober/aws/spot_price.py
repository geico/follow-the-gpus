"""Spot price history. Spec 0002 section 4.8, spec 0008 section 5."""

from datetime import datetime, timedelta
from typing import Any

from ftg_prober.aws.common import paginate, request_gpu_types, row, to_float
from ftg_prober.config import parse_cadence
from ftg_prober.probes import Probe, ProbeContext
from ftg_prober.record import Record, format_ts

PRODUCT_DESCRIPTION = "Linux/UNIX"
MAX_RESULTS = 1000
CURRENCY = "USD"


class SpotPriceProbe(Probe):
    """1 `DescribeSpotPriceHistory` call per Region, with all GPU types of the Spot score requests.

    The probe keeps no state between calls (spec 0002, D13). The start time is now minus
    the cadence. A record at or after the start time is new.
    """

    probe_type = "aws_spot_price"
    provider = "aws"
    default_cadence = "5m"

    def run(self, ctx: ProbeContext) -> list[Record]:
        now = ctx.now()
        cadence_s = parse_cadence(ctx.cadence(self.probe_type) or self.default_cadence)
        start = now - timedelta(seconds=cadence_s)
        types = request_gpu_types(ctx.config)
        if not types:
            return []
        hourly = crosses_hour(start, now)
        rows: list[Record] = []
        for region in ctx.config["regions"]:
            rows.extend(self._region(ctx, region, types, start, now, hourly))
        return rows

    def _region(
        self,
        ctx: ProbeContext,
        region: str,
        types: list[str],
        start: datetime,
        now: datetime,
        hourly: bool,
    ) -> list[Record]:
        ec2 = ctx.clients.boto3("ec2", region)
        result = paginate(
            ec2.describe_spot_price_history,
            {
                "StartTime": start,
                "EndTime": now,
                "Filters": [
                    {"Name": "instance-type", "Values": types},
                    {"Name": "product-description", "Values": [PRODUCT_DESCRIPTION]},
                ],
                "MaxResults": MAX_RESULTS,
            },
            "SpotPriceHistory",
            probe_type=self.probe_type,
            region=region,
        )

        def make(sku: str, outcome: str, details: dict[str, Any], zone: str | None = None):
            return row(
                ctx,
                self.probe_type,
                region=region,
                zone=zone,
                sku=sku,
                outcome=outcome,
                error_code=result.error_code,
                latency_ms=result.latency_ms,
                details=details,
            )

        if result.error_code:
            return [make(t, "error", {}) for t in types]

        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in result.items:
            if item.get("ProductDescription", PRODUCT_DESCRIPTION) != PRODUCT_DESCRIPTION:
                continue
            key = (item.get("InstanceType"), item.get("AvailabilityZone"))
            if key[0] in types and key[1]:
                groups.setdefault(key, []).append(item)

        rows: list[Record] = []
        for instance_type in types:
            zones = sorted(z for t, z in groups if t == instance_type)
            if not zones:
                rows.append(make(instance_type, "none", {}))
                continue
            for zone in zones:
                items = sorted(groups[(instance_type, zone)], key=lambda i: i["Timestamp"])
                new = [i for i in items if i["Timestamp"] >= start]
                before = [i for i in items if i["Timestamp"] < start]
                effective = before[-1] if before else None
                for item in new:
                    rows.append(make(instance_type, "success", price_details(item), zone))
                if new:
                    continue
                none_details: dict[str, Any] = {}
                if effective is not None:
                    none_details = {
                        "effective_price": to_float(effective.get("SpotPrice")),
                        "effective_price_ts": format_ts(effective["Timestamp"]),
                    }
                rows.append(make(instance_type, "none", none_details, zone))
                if hourly and effective is not None:
                    details = {**price_details(effective), "effective": True}
                    rows.append(make(instance_type, "success", details, zone))
        return rows


def price_details(item: dict[str, Any]) -> dict[str, Any]:
    """The `details` of a price row: spec 0003 section 4.4 keys `price` and `price_ts`."""
    return {
        "price": to_float(item.get("SpotPrice")),
        "price_ts": format_ts(item["Timestamp"]),
        "currency": CURRENCY,
        "product_description": item.get("ProductDescription", PRODUCT_DESCRIPTION),
    }


def crosses_hour(start: datetime, now: datetime) -> bool:
    """True when a full UTC hour (minute 0, second 0) is after `start` and at or before `now`.

    With polls that follow each other at the cadence, 1 poll per hour gets True.
    """
    top = now.replace(minute=0, second=0, microsecond=0)
    return start < top <= now
