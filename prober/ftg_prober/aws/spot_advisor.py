"""Spot Instance Advisor feed. Spec 0002 section 4.8, spec 0008 section 6."""

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from ftg_prober.aws.common import request_gpu_types, row
from ftg_prober.log import log_provider_error
from ftg_prober.probes import Probe, ProbeContext
from ftg_prober.record import Record

log = logging.getLogger(__name__)

FEED_URL = "https://spot-bid-advisor.s3.amazonaws.com/spot-advisor-data.json"
TIMEOUT_S = 30
OS_KEY = "Linux"
# The 5 known labels and their bounds in percent (spec 0008 section 6.3). None is an open bound.
LABEL_BOUNDS: dict[str, tuple[int, int | None]] = {
    "<5%": (0, 5),
    "5-10%": (5, 10),
    "10-15%": (10, 15),
    "15-20%": (15, 20),
    ">20%": (20, None),
}
FORMAT_ERROR = "FeedFormat"


def _body_text(response: Any) -> str:
    """The body of a failed response as returned."""
    content = getattr(response, "content", b"")
    return content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)


class SpotAdvisorProbe(Probe):
    """1 HTTPS GET of the feed per call. The default cadence is 1 day.

    `http` is any object with `get(url, timeout=...)` that returns a response with
    `status_code` and `content`. The default is the `requests` module. Tests pass a fake.
    """

    probe_type = "aws_spot_advisor"
    provider = "aws"
    default_cadence = "24h"

    def __init__(self, http: Any = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.http = http
        self.clock = clock

    def _client(self) -> Any:
        if self.http is None:
            import requests

            self.http = requests
        return self.http

    def run(self, ctx: ProbeContext) -> list[Record]:
        types = request_gpu_types(ctx.config)
        regions = list(ctx.config["regions"])
        started = self.clock()
        try:
            response = self._client().get(FEED_URL, timeout=TIMEOUT_S)
            latency_ms = _ms(self.clock() - started)
        except Exception as exc:  # noqa: BLE001 - a failed fetch becomes error rows
            log_provider_error(
                log,
                probe_type=self.probe_type,
                region=",".join(regions),
                code=type(exc).__name__,
                message=str(exc),
            )
            return self._errors(
                ctx, types, regions, type(exc).__name__, _ms(self.clock() - started)
            )
        if response.status_code != 200:
            log_provider_error(
                log,
                probe_type=self.probe_type,
                region=",".join(regions),
                code=f"HTTP{response.status_code}",
                status=response.status_code,
                message=_body_text(response),
            )
            return self._errors(ctx, types, regions, f"HTTP{response.status_code}", latency_ms)
        body = response.content
        try:
            feed = json.loads(body)
            advisor = feed["spot_advisor"]
            ranges = {item["index"]: (item["label"], item.get("max")) for item in feed["ranges"]}
            if not isinstance(advisor, dict):
                raise TypeError("spot_advisor is not an object")
        except (ValueError, KeyError, TypeError) as exc:
            log_provider_error(
                log,
                probe_type=self.probe_type,
                region=",".join(regions),
                code=FORMAT_ERROR,
                status=response.status_code,
                message=repr(exc),
            )
            return self._errors(ctx, types, regions, FORMAT_ERROR, latency_ms)

        ctx.put_raw(self.probe_type, body)

        rows: list[Record] = []
        for region in regions:
            by_os = advisor.get(region) or {}
            linux = (by_os.get(OS_KEY) if isinstance(by_os, dict) else None) or {}
            if not isinstance(by_os, dict) or not isinstance(linux, dict):
                rows.extend(
                    self._row(ctx, region, t, "error", latency_ms, {}, FORMAT_ERROR) for t in types
                )
                continue
            for instance_type in types:
                entry = linux.get(instance_type)
                if entry is None:
                    rows.append(self._row(ctx, region, instance_type, "none", latency_ms, {}))
                    continue
                details = _details(entry, ranges)
                if details is None:
                    rows.append(
                        self._row(ctx, region, instance_type, "error", latency_ms, {}, FORMAT_ERROR)
                    )
                else:
                    rows.append(
                        self._row(ctx, region, instance_type, "success", latency_ms, details)
                    )
        return rows

    def _row(
        self,
        ctx: ProbeContext,
        region: str,
        instance_type: str,
        outcome: str,
        latency_ms: int,
        details: dict[str, Any],
        code: str | None = None,
    ) -> Record:
        return row(
            ctx,
            self.probe_type,
            region=region,
            sku=instance_type,
            outcome=outcome,
            error_code=code,
            latency_ms=latency_ms,
            details=details,
        )

    def _errors(
        self, ctx: ProbeContext, types: list[str], regions: list[str], code: str, latency_ms: int
    ) -> list[Record]:
        return [
            self._row(ctx, region, instance_type, "error", latency_ms, {}, code)
            for region in regions
            for instance_type in types
        ]


def _details(entry: Any, ranges: dict[Any, tuple[Any, Any]]) -> dict[str, Any] | None:
    """The `details` of 1 feed entry, from its `r` and `s`. None on a bad entry.

    The label is kept as returned. A label that is not in `LABEL_BOUNDS` gets null bounds.
    `feed_max_pct` is the `max` of the range as the feed gives it.
    """
    if not isinstance(entry, dict):
        return None
    index = entry.get("r")
    savings = entry.get("s")
    if isinstance(index, bool) or not isinstance(index, int) or index not in ranges:
        return None
    label, feed_max = ranges[index]
    if not isinstance(label, str) or _number(savings) is None:
        return None
    low, high = LABEL_BOUNDS.get(label, (None, None))
    return {
        "interruption_range": label,
        "interruption_range_min_pct": low,
        "interruption_range_max_pct": high,
        "feed_max_pct": _number(feed_max),
        "savings_pct": savings,
        "range_index": index,
    }


def _number(value: Any) -> int | float | None:
    """The value if it is a JSON number, else None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def _ms(seconds: float) -> int:
    return max(0, int(round(seconds * 1000)))
