"""Synthetic rows for every probe type, for a simulated job or a simulated run. Spec 0003 section 7.

Each generator makes the rows of 1 probe call. Values use each cloud's native scale.
The randomness comes from 1 seeded `random.Random`, so the same seed gives the same data.
"""

import hashlib
import json
import logging
import math
import random
import uuid
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ftg_prober.aws.cb_earliest import LEAD, next_boundary
from ftg_prober.aws.cb_longest import GALLOP_DAYS as CB_LONGEST_GALLOP_DAYS
from ftg_prober.aws.spot_price import crosses_hour
from ftg_prober.aws.spot_score import add_controls
from ftg_prober.azure.spot_resource_graph import price_max_age_days
from ftg_prober.config import Config, cadence_key, counts_for, earliest_hours, parse_cadence
from ftg_prober.record import PROBE_TYPES, Record, format_ts, to_jsonl
from ftg_prober.store import FileStore, Store, build_key, raw_key, target_for

log = logging.getLogger(__name__)

DEFAULT_CADENCE = {
    "aws_cb_offering": "2h",
    "aws_cb_earliest": "2h",
    "aws_cb_longest": "2h",
    "aws_spot_score": "5m",
    "aws_spot_price": "5m",
    "aws_spot_advisor": "24h",
    "aws_odcr": "2h",
    "az_capacity_reservation": "2h",
    "az_vmss_flex": "2h",
    "az_spot_score": "15m",
    "az_spot": "5m",
    "az_precheck": "2h",
}
# The share of calls that fail, by probe type. The live rows of 2026-10-14 to 2026-10-19 gave about
# 0.004% of the Spot score requests and 0.08% of the Azure Spot calls (the Spot price rows had no
# error call, so that share is a guess of the same size).
AWS_SPOT_SCORE_ERROR_SHARE = 0.00004
AWS_SPOT_PRICE_ERROR_SHARE = 0.00004
# The share of `none` az_spot rows that still have an old price, and of other az_spot_score rows
# with `is_quota_available` = false (live rows; every `none` row has false).
AZ_SPOT_NONE_WITH_PRICE_SHARE = 0.2
AZ_QUOTA_FALSE_SHARE = 0.85
AZ_SPOT_ERROR_SHARE = 0.0008
AWS_ERROR_CODE = "ProxyConnectionError"
AZ_SPOT_ERROR_CODE = "ServiceResponseError"
PROVIDER_OF = {t: ("aws" if t.startswith("aws_") else "azure") for t in PROBE_TYPES}

# AWS Spot placement score, 1 to 10. A base per instance family; GPU families score low.
AWS_BASE_SCORE = {
    "p6": 2,
    "p5en": 2,
    "p5e": 2,
    "p5": 3,
    "p4d": 4,
    "g7e": 6,
    "g6e": 6,
    "g6": 8,
    "g5": 8,
    "g4dn": 9,
}
AWS_CPU_MAX_SCORE = 3
# AWS Spot price per hour, in USD, for the Linux/UNIX product.
AWS_BASE_PRICE = {
    "p6-b200.48xlarge": 36.0,
    "p5en.48xlarge": 24.0,
    "p5.48xlarge": 20.0,
    "p5.4xlarge": 2.5,
    "g7e.2xlarge": 1.5,
    "p4d.24xlarge": 10.0,
    "g6e.xlarge": 0.75,
    "g6.xlarge": 0.35,
    "g5.xlarge": 0.40,
    "g4dn.xlarge": 0.20,
}
AWS_PRODUCT_DESCRIPTION = "Linux/UNIX"
AWS_ZONE_LETTERS = "abc"
AWS_INTERRUPTION_RANGES = ("<5%", "5-10%", "10-15%", "15-20%", ">20%")
# The bounds in percent of each range (None is an open bound) and the `max` the public feed
# gives for it, as `spot_advisor.py` writes them.
AWS_INTERRUPTION_BOUNDS = ((0, 5), (5, 10), (10, 15), (15, 20), (20, None))
AWS_FEED_MAX_PCT = (5, 11, 16, 22, 100)
AWS_CB_FEE_PER_HOUR = {
    "p6-b200.48xlarge": 50.0,
    "p5en.48xlarge": 36.0,
    "p5.48xlarge": 32.0,
    "p5.4xlarge": 4.0,
    "p4d.24xlarge": 15.0,
}

AZURE_SCORES = ("High", "Medium", "Low")
# Weights for High, Medium, Low by VM family. H100 and H200 sizes are mostly Low.
AZURE_SCORE_WEIGHTS = {
    "ND": (1, 2, 7),
    "NC_H100": (2, 4, 4),
    "A100": (3, 4, 3),
    "A10": (6, 3, 1),
    "T4": (7, 2, 1),
    "other": (4, 4, 2),
}
AZURE_EVICTION_BUCKETS = ("0-5", "5-10", "10-15", "15-20", "20+")
AZURE_EVICTION_BOUNDS = ((0, 5), (5, 10), (10, 15), (15, 20), (20, None))
# Azure Regions without Availability Zones: a zonal score row there has no zone (null). The other
# Regions of the targets file have the zones 1, 2, and 3.
# https://learn.microsoft.com/en-us/azure/reliability/regions-list
AZURE_REGIONS_WITHOUT_ZONES = frozenset({"northcentralus", "westus", "westcentralus"})
AZURE_ZONES = ("1", "2", "3")
# A Region with no Spot data in Resource Graph: every row is `none`, like the live rows of a
# Region that is not a Spot Region for these sizes.
AZURE_SPOT_NO_DATA_REGIONS = frozenset({"westcentralus"})
AZURE_BASE_PRICE = {"ND": 25.0, "NC_H100": 2.8, "A100": 1.5, "A10": 0.9, "T4": 0.2, "other": 1.0}
ALLOCATION_OUTCOMES = ("success", "partial", "no_capacity", "timeout", "error")
ALLOCATION_OUTCOME_WEIGHTS = (60, 15, 15, 5, 5)
# The sim pre-check SKU of spec 0010 section 9: family `simFamily`, 2 vCPUs, 1000 free vCPUs.
PRECHECK_FAMILY = "simFamily"
PRECHECK_VCPUS = 2
PRECHECK_FREE_VCPUS = 1000
PRECHECK_RESTRICTED_SHARE = 0.05
PRECHECK_RESTRICTED_CODE = "NotAvailableForSubscription"


class StoreNotEmptyError(ValueError):
    """`sim` refuses a `file://` store folder that already holds a file or a folder."""


def check_empty(store: Store) -> None:
    """Refuse a `file://` store whose folder is not empty. Spec 0003 section 7.2, rule 6.

    The same seed and end time give the same keys, and a second history mixes with the first.
    """
    if not isinstance(store, FileStore) or not store.root.is_dir():
        return
    if any(store.root.iterdir()):
        raise StoreNotEmptyError(
            f"the sim store {store.root} is not empty. "
            "Delete the folder, or give an empty or new folder in --store."
        )


@dataclass
class SimCall:
    """The output of 1 probe call: the rows, and for a feed probe the raw bytes too.

    `write_call` builds the raw object's key itself, the same way it builds the rows' key: the
    key no longer depends on anything the generator decides (ADR 0011 section 6, item 5).
    """

    rows: list[Record]
    raw: bytes | None = None


def _stable(*parts: str) -> float:
    """A number in [0, 1) that depends only on the text, for per-target offsets."""
    return (zlib.crc32("|".join(parts).encode()) % 1000) / 1000.0


def _daily_wave(at: datetime) -> float:
    """-1 to 1. The peak is at 04:00 UTC, when demand is low. The low point is at 16:00 UTC."""
    hour = at.hour + at.minute / 60
    return math.cos(2 * math.pi * (hour - 4) / 24)


def _run_id(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def _row(
    *,
    at: datetime,
    run_id: str,
    simulated: bool,
    provider: str,
    probe_type: str,
    region: str,
    sku: str,
    outcome: str,
    details: dict[str, Any],
    latency_ms: int,
    zone: str | None = None,
    placement_group_mode: str = "n/a",
    n_requested: int = 1,
    n_placed: int | None = None,
    error_code: str | None = None,
) -> Record:
    return Record(
        ts_utc=format_ts(at + timedelta(milliseconds=latency_ms)),
        run_id=run_id,
        simulated=simulated,
        provider=provider,
        region=region,
        zone=zone,
        probe_type=probe_type,
        sku=sku,
        placement_group_mode=placement_group_mode,
        n_requested=n_requested,
        n_placed=n_placed,
        outcome=outcome,
        error_code=error_code,
        latency_ms=latency_ms,
        details=details,
    )


def _aws_family(instance_type: str) -> str:
    return instance_type.split(".")[0].split("-")[0]


def _cadence_s(section: dict[str, Any], probe_type: str) -> int:
    """The cadence of a probe type in seconds: the section's, else the default."""
    cadence = (section.get("cadence") or {}).get(
        cadence_key(probe_type), DEFAULT_CADENCE[probe_type]
    )
    return parse_cadence(cadence)


def _is_gpu_type(instance_type: str) -> bool:
    return _aws_family(instance_type)[0] in ("p", "g")


def _request_gpu_types(section: dict[str, Any]) -> list[str]:
    """The single GPU instance types of the Spot score requests, in request order."""
    types = []
    for entry in section["spot_score_requests"]:
        if len(entry["instance_types"]) == 1 and _is_gpu_type(entry["instance_types"][0]):
            types.append(entry["instance_types"][0])
    return types


def aws_spot_score(
    section: dict[str, Any], at: datetime, rng: random.Random, simulated: bool
) -> list[SimCall]:
    """1 call. 1 row per Spot score request and Region. Scores 1 to 10; a CPU entry of fewer than 3
    types is 3 or lower, a CPU mix of 3 or more types is 4 or higher, and a GPU mix scores as high
    as its best single type. A request that fails gives an `error` row per Region. A control entry
    has `details.control`, written by the probe's own `add_controls`."""
    run_id = _run_id(rng)
    latency = rng.randint(200, 900)
    failed = {
        entry["name"]: rng.random() < AWS_SPOT_SCORE_ERROR_SHARE
        for entry in section["spot_score_requests"]
    }
    error_latency = rng.randint(60_000, 90_000)
    rows = []
    for region in section["regions"]:
        for entry in section["spot_score_requests"]:
            types = entry["instance_types"]
            details = {
                "instance_types": types,
                "request": entry["name"],
                "label": entry["label"],
            }
            if failed[entry["name"]]:
                rows.append(
                    _row(
                        at=at,
                        run_id=run_id,
                        simulated=simulated,
                        provider="aws",
                        probe_type="aws_spot_score",
                        region=region,
                        sku=",".join(types),
                        outcome="error",
                        error_code=AWS_ERROR_CODE,
                        details=details,
                        latency_ms=error_latency,
                    )
                )
                continue
            bases = [
                AWS_BASE_SCORE.get(_aws_family(t), AWS_BASE_SCORE.get(_aws_family(t)[:2], 0))
                for t in types
            ]
            gpu = any(_is_gpu_type(t) for t in types)
            cpu_mix = not gpu and len(types) >= 3
            base = max(bases) if gpu else (9 if cpu_mix else 2)
            score = (
                base
                + 2 * _stable(entry["name"], region)
                - 1
                + 1.5 * _daily_wave(at)
                + rng.choice((-1, 0, 0, 0, 1))
            )
            score = round(min(10, max(1, score)))
            if cpu_mix:
                score = max(score, AWS_CPU_MAX_SCORE + 1)
            elif not gpu:
                score = min(score, AWS_CPU_MAX_SCORE)
            rows.append(
                _row(
                    at=at,
                    run_id=run_id,
                    simulated=simulated,
                    provider="aws",
                    probe_type="aws_spot_score",
                    region=region,
                    sku=",".join(types),
                    outcome="success",
                    details={**details, "score": score},
                    latency_ms=latency,
                )
            )
    _gpu_mix_scores_at_least_the_singles(section["spot_score_requests"], rows)
    add_controls(section["spot_score_requests"], rows)
    return [SimCall(rows)]


def _gpu_mix_scores_at_least_the_singles(
    requests: list[dict[str, Any]], rows: list[Record]
) -> None:
    """A GPU mix scores as high as each single type of the mix in the same Region, like the
    live rows (their `single_not_above_mix` control is always `ok`)."""
    singles = {e["instance_types"][0]: e["name"] for e in requests if len(e["instance_types"]) == 1}
    scores = {
        (r.details["request"], r.region): r.details["score"] for r in rows if "score" in r.details
    }
    mixes = {
        e["name"]: [singles[t] for t in e["instance_types"] if t in singles]
        for e in requests
        if len(e["instance_types"]) > 1 and any(_is_gpu_type(t) for t in e["instance_types"])
    }
    for row in rows:
        if row.details["request"] in mixes and "score" in row.details:
            known = [
                scores[(n, row.region)]
                for n in mixes[row.details["request"]]
                if (n, row.region) in scores
            ]
            row.details["score"] = max([row.details["score"], *known])


def aws_spot_price(
    section: dict[str, Any], at: datetime, rng: random.Random, simulated: bool
) -> list[SimCall]:
    """1 call per Region. 1 row per GPU type and zone: `success` with a new price, or `none`."""
    calls = []
    hours = at.hour + at.minute / 60
    hourly = crosses_hour(at - timedelta(seconds=_cadence_s(section, "aws_spot_price")), at)
    for region in section["regions"]:
        run_id = _run_id(rng)
        latency = rng.randint(150, 700)
        rows = []
        if rng.random() < AWS_SPOT_PRICE_ERROR_SHARE:
            # A failed call gives 1 `error` row per type, with no zone and no details.
            error_latency = rng.randint(60_000, 90_000)
            for instance_type in _request_gpu_types(section):
                rows.append(
                    _row(
                        at=at,
                        run_id=run_id,
                        simulated=simulated,
                        provider="aws",
                        probe_type="aws_spot_price",
                        region=region,
                        sku=instance_type,
                        outcome="error",
                        error_code=AWS_ERROR_CODE,
                        details={},
                        latency_ms=error_latency,
                    )
                )
            calls.append(SimCall(rows))
            continue
        for instance_type in _request_gpu_types(section):
            base = AWS_BASE_PRICE.get(instance_type, 1.0)
            for letter in AWS_ZONE_LETTERS:
                zone = f"{region}{letter}"
                phase = 2 * math.pi * _stable(instance_type, zone)
                price = base * (
                    1 + 0.1 * math.sin(2 * math.pi * hours / 24 + phase) + 0.1 * _stable(zone)
                )
                changed = rng.random() < 0.2
                rows.append(
                    _row(
                        at=at,
                        run_id=run_id,
                        simulated=simulated,
                        provider="aws",
                        probe_type="aws_spot_price",
                        region=region,
                        zone=zone,
                        sku=instance_type,
                        outcome="success" if changed else "none",
                        details=(
                            {
                                "price": round(price, 4),
                                "price_ts": format_ts(at),
                                "currency": "USD",
                                "product_description": AWS_PRODUCT_DESCRIPTION,
                            }
                            if changed
                            else {
                                # A sim zone always has an earlier record, like a real `none`
                                # row that has a zone (spec 0008, section 4.3).
                                "effective_price": round(price, 4),
                                "effective_price_ts": format_ts(at - timedelta(hours=3)),
                            }
                        ),
                        latency_ms=latency,
                    )
                )
                if not changed and hourly:
                    # The hourly poll adds the effective record as a `success` row (spec 0008,
                    # section 5.3).
                    rows.append(
                        _row(
                            at=at,
                            run_id=run_id,
                            simulated=simulated,
                            provider="aws",
                            probe_type="aws_spot_price",
                            region=region,
                            zone=zone,
                            sku=instance_type,
                            outcome="success",
                            details={
                                "price": round(price, 4),
                                "price_ts": format_ts(at - timedelta(hours=3)),
                                "currency": "USD",
                                "product_description": AWS_PRODUCT_DESCRIPTION,
                                "effective": True,
                            },
                            latency_ms=latency,
                        )
                    )
        calls.append(SimCall(rows))
    return calls


def aws_spot_advisor(
    section: dict[str, Any], at: datetime, rng: random.Random, simulated: bool
) -> list[SimCall]:
    """1 call. The raw feed as 1 JSON object, then 1 row per GPU type and Region."""
    run_id = _run_id(rng)
    latency = rng.randint(300, 1500)
    feed: dict[str, Any] = {"spot_advisor": {}}
    rows = []
    for region in section["regions"]:
        feed["spot_advisor"][region] = {"Linux": {}}
        for instance_type in _request_gpu_types(section):
            base = AWS_BASE_SCORE.get(_aws_family(instance_type), 5)
            index = min(4, max(0, 4 - (base + rng.randint(-2, 1)) // 2))  # H100 reaches ">20%"
            savings = rng.randint(55, 80)
            feed["spot_advisor"][region]["Linux"][instance_type] = {"r": index, "s": savings}
            rows.append(
                _row(
                    at=at,
                    run_id=run_id,
                    simulated=simulated,
                    provider="aws",
                    probe_type="aws_spot_advisor",
                    region=region,
                    sku=instance_type,
                    outcome="success",
                    details={
                        "interruption_range": AWS_INTERRUPTION_RANGES[index],
                        "interruption_range_min_pct": AWS_INTERRUPTION_BOUNDS[index][0],
                        "interruption_range_max_pct": AWS_INTERRUPTION_BOUNDS[index][1],
                        "feed_max_pct": AWS_FEED_MAX_PCT[index],
                        "savings_pct": savings,
                        "range_index": index,
                    },
                    latency_ms=latency,
                )
            )
    return [SimCall(rows, json.dumps(feed).encode())]


def _sim_id(*parts: str | int) -> str:
    """A stable id from all the parts: 16 hex digits of a SHA-256 of the joined text. A CRC-32
    (8 hex digits) gave 33 collisions between offerings in a 60-day history."""
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _cb_offering_extra(
    instance_type: str, region: str, zone: str, start: datetime, duration: int, count: int
) -> dict[str, Any]:
    """The keys of a real offering row that the sim rows did not have: `offering_id` and the
    fields of spec 0008, section 3.2. The fixed values are those of a standard block. The id
    depends on the type, Region, zone, start, duration, and count: 2 offerings of 1 call differ."""
    return {
        "offering_id": "cb-sim-"
        + _sim_id(instance_type, region, zone, format_ts(start), duration, count),
        "tenancy": "default",
        "zone_type": None,
        "ultraserver_type": None,
        "ultraserver_count": None,
    }


def aws_cb_offering(
    section: dict[str, Any], at: datetime, rng: random.Random, simulated: bool
) -> list[SimCall]:
    """1 call per instance type, count, duration, and Region (like `request_plan`). 1 row per
    offering, or 1 `none` row."""
    blocks = section["capacity_blocks"]
    calls = []
    for region in section["regions"]:
        for instance_type in blocks["instance_types"]:
            for count in counts_for(blocks, instance_type):
                for duration in blocks["duration_hours"]:
                    calls.append(
                        _cb_offering_call(
                            section, at, rng, simulated, region, instance_type, count, int(duration)
                        )
                    )
    return calls


def _cb_offering_call(
    section: dict[str, Any],
    at: datetime,
    rng: random.Random,
    simulated: bool,
    region: str,
    instance_type: str,
    count: int,
    duration: int,
) -> SimCall:
    blocks = section["capacity_blocks"]
    run_id = _run_id(rng)
    latency = rng.randint(200, 1200)
    rows = []
    seen: set[tuple[datetime, str]] = set()
    for _ in range(rng.choice((0, 0, 1, 1, 2, 3))):
        start = at + timedelta(days=rng.randint(1, blocks["start_window_days"]))
        start = start.replace(minute=0, second=0, microsecond=0, hour=rng.choice((11, 12, 13)))
        zone = f"{region}{rng.choice(AWS_ZONE_LETTERS)}"
        if (start, zone) in seen:  # a real call lists each (zone, start) once
            continue
        seen.add((start, zone))
        fee = AWS_CB_FEE_PER_HOUR.get(instance_type, 20.0) * duration * count
        rows.append(
            _row(
                at=at,
                run_id=run_id,
                simulated=simulated,
                provider="aws",
                probe_type="aws_cb_offering",
                region=region,
                zone=zone,
                sku=instance_type,
                outcome="success",
                n_requested=count,
                details={
                    "start": format_ts(start),
                    "end": format_ts(start + timedelta(hours=duration)),
                    "duration_hours": duration,
                    "duration_minutes": 0,
                    "instance_count": count,
                    "upfront_fee": round(fee, 2),
                    "currency": "USD",
                    **_cb_offering_extra(instance_type, region, zone, start, duration, count),
                },
                latency_ms=latency,
            )
        )
    if not rows:
        rows.append(
            _row(
                at=at,
                run_id=run_id,
                simulated=simulated,
                provider="aws",
                probe_type="aws_cb_offering",
                region=region,
                sku=instance_type,
                outcome="none",
                n_requested=count,
                details={
                    "duration_hours": duration,
                    "start_window_days": blocks["start_window_days"],
                },
                latency_ms=latency,
            )
        )
    return SimCall(rows)


def aws_cb_earliest(
    section: dict[str, Any], at: datetime, rng: random.Random, simulated: bool
) -> list[SimCall]:
    """1 call per instance type, count, earliest duration, and Region. 0 or 1 offering, else
    1 `none` row.

    The start is the first start AWS allows (`at` + 30 min) or the next 11:30 UTC.
    """
    blocks = section["capacity_blocks"]
    calls = []
    for region in section["regions"]:
        for instance_type in blocks["instance_types"]:
            for count in counts_for(blocks, instance_type):
                for duration in earliest_hours(blocks):
                    run_id = _run_id(rng)
                    latency = rng.randint(200, 1200)
                    first = at + LEAD
                    row_args = {
                        "at": at,
                        "run_id": run_id,
                        "simulated": simulated,
                        "provider": "aws",
                        "probe_type": "aws_cb_earliest",
                        "region": region,
                        "sku": instance_type,
                        "n_requested": count,
                        "latency_ms": latency,
                    }
                    if rng.random() < 0.5:
                        start = rng.choice((first, next_boundary(first)))
                        zone = f"{region}{rng.choice(AWS_ZONE_LETTERS)}"
                        fee = AWS_CB_FEE_PER_HOUR.get(instance_type, 20.0) * duration * count
                        row = _row(
                            **row_args,
                            zone=zone,
                            outcome="success",
                            n_placed=None,
                            details={
                                "start": format_ts(start),
                                "end": format_ts(start + timedelta(hours=duration)),
                                "duration_hours": duration,
                                "duration_minutes": 0,
                                "instance_count": count,
                                "upfront_fee": round(fee, 2),
                                "currency": "USD",
                                **_cb_offering_extra(
                                    instance_type, region, zone, start, duration, count
                                ),
                            },
                        )
                    else:
                        row = _row(**row_args, outcome="none", details={"duration_hours": duration})
                    calls.append(SimCall([row]))
    return calls


# The gallop sequence of `cb_longest.py` (spec 0008, section 3B.2): the longest a sim search
# confirms is always 1 of these, like a real search.
AWS_CB_LONGEST_DAYS = CB_LONGEST_GALLOP_DAYS
# Non-throttle error codes only: a throttled attempt is never a step (spec 0008, section 3B.3),
# so it never appears here either.
AWS_CB_LONGEST_ERROR_CODES = ("InvalidParameterValue", "CapacityBlockDescribeLimitExceeded")
# The weights of the 3 outcomes of a search: mostly a found block, some none, a few errors.
AWS_CB_LONGEST_OUTCOME_WEIGHTS = (80, 15, 5)


def _cb_longest_step(
    purpose: str,
    duration_hours: int,
    at: datetime,
    result: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    zone: str | None = None,
    offering_id: str | None = None,
    upfront_fee: float | None = None,
    currency: str | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    """1 `details.steps` entry, in the shape of `cb_longest.Step` (spec 0008, 3B.4)."""
    return {
        "purpose": purpose,
        "duration_hours": duration_hours,
        "start": format_ts(at),
        "end": format_ts(at + timedelta(hours=duration_hours)),
        "result": result,
        "start_date": start_date,
        "end_date": end_date,
        "zone": zone,
        "offering_id": offering_id,
        "upfront_fee": upfront_fee,
        "currency": currency,
        "duration_minutes": 0 if result == "offer" else None,
        "error_code": error_code,
        "ts": format_ts(at),
        "b0": format_ts(next_boundary(at + LEAD)),
    }


def _cb_longest_none_details(at: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """No offer in 1 to 56 days: exactly 7 steps, `earliest.0` plus 6 bisect steps."""
    steps = [_cb_longest_step("earliest.0", 24, at, "none")]
    steps += [_cb_longest_step("earliest_bisect", 24, at, "none") for _ in range(6)]
    none_details = {
        "earliest_start": None,
        "earliest_end": None,
        "earliest_duration_hours": None,
        "earliest_duration_minutes": None,
        "earliest_offering_id": None,
        "earliest_upfront_fee": None,
        "earliest_currency": None,
    }
    return steps, none_details


def aws_cb_longest(
    section: dict[str, Any], at: datetime, rng: random.Random, simulated: bool
) -> list[SimCall]:
    """1 call per instance type, count, and Region: the 1 final row of a search (spec 0008,
    3B.4)."""
    blocks = section["capacity_blocks"]
    calls = []
    for region, instance_type, count in [
        (r, t, n)
        for r in section["regions"]
        for t in blocks["instance_types"]
        for n in counts_for(blocks, t)
    ]:
        run_id = _run_id(rng)
        latency = rng.randint(300, 2000)
        outcome = rng.choices(("success", "none", "error"), weights=AWS_CB_LONGEST_OUTCOME_WEIGHTS)[
            0
        ]
        fee_per_hour = AWS_CB_FEE_PER_HOUR.get(instance_type, 20.0)
        first = at + LEAD
        start = rng.choice((first, next_boundary(first)))
        earliest_offering_id = f"cb-sim-{_sim_id(instance_type, region, count, 'e')}"
        earliest_step = _cb_longest_step(
            "earliest.0",
            24,
            at,
            "offer",
            start_date=format_ts(start),
            end_date=format_ts(start + timedelta(hours=24)),
            zone=f"{region}{rng.choice(AWS_ZONE_LETTERS)}",
            offering_id=earliest_offering_id,
            upfront_fee=round(fee_per_hour * 24 * count, 2),
            currency="USD",
        )
        earliest_details = {
            "earliest_start": earliest_step["start_date"],
            "earliest_end": earliest_step["end_date"],
            "earliest_duration_hours": 24,
            "earliest_duration_minutes": 0,
            "earliest_offering_id": earliest_offering_id,
            "earliest_upfront_fee": earliest_step["upfront_fee"],
            "earliest_currency": "USD",
        }

        if outcome == "none":
            steps, extra = _cb_longest_none_details(at)
            row_kwargs: dict[str, Any] = {
                "outcome": "none",
                "error_code": None,
                "zone": None,
                "n_placed": None,
            }
            extra.update(
                {
                    "longest_start": None,
                    "longest_duration_hours": None,
                    "longest_duration_minutes": None,
                    "longest_end": None,
                    "longest_offering_id": None,
                    "longest_upfront_fee": None,
                }
            )
        elif outcome == "error":
            error_code = rng.choice(AWS_CB_LONGEST_ERROR_CODES)
            steps = [
                earliest_step,
                _cb_longest_step("gallop", 2 * 24, at, "error", error_code=error_code),
            ]
            row_kwargs = {
                "outcome": "error",
                "error_code": error_code,
                "zone": earliest_step["zone"],
                "n_placed": None,
            }
            extra = {
                **earliest_details,
                "longest_start": earliest_step["start_date"],
                "longest_duration_hours": 24,
                "longest_duration_minutes": 0,
                "longest_end": earliest_step["end_date"],
                "longest_offering_id": earliest_offering_id,
                "longest_upfront_fee": earliest_step["upfront_fee"],
            }
        else:
            longest_days = rng.choice(AWS_CB_LONGEST_DAYS)
            longest_offering_id = f"cb-sim-{_sim_id(instance_type, region, count, 'l')}"
            gallop_steps = [
                _cb_longest_step(
                    "gallop",
                    days * 24,
                    at,
                    "offer",
                    start_date=earliest_step["start_date"],
                    end_date=format_ts(start + timedelta(days=days)),
                    zone=earliest_step["zone"],
                    offering_id=(longest_offering_id if days == longest_days else f"cb-sim-{days}"),
                    upfront_fee=round(fee_per_hour * days * 24 * count, 2),
                    currency="USD",
                )
                for days in AWS_CB_LONGEST_DAYS
                if days <= longest_days
            ]
            stopped = longest_days != AWS_CB_LONGEST_DAYS[-1]
            next_gallop_days = (
                AWS_CB_LONGEST_DAYS[AWS_CB_LONGEST_DAYS.index(longest_days) + 1]
                if stopped
                else None
            )
            stop_step = (
                [_cb_longest_step("gallop", next_gallop_days * 24, at, "none")] if stopped else []
            )
            steps = [earliest_step, *gallop_steps, *stop_step]
            longest_fee = round(fee_per_hour * longest_days * 24 * count, 2)
            row_kwargs = {
                "outcome": "success",
                "error_code": None,
                "zone": earliest_step["zone"],
                "n_placed": None,
            }
            extra = {
                **earliest_details,
                "longest_start": earliest_step["start_date"],
                "longest_duration_hours": longest_days * 24,
                "longest_duration_minutes": 0,
                "longest_end": format_ts(start + timedelta(days=longest_days)),
                "longest_offering_id": longest_offering_id,
                "longest_upfront_fee": longest_fee,
            }

        details = {
            **extra,
            "b0": format_ts(next_boundary(at + LEAD)),
            "steps": steps,
            "calls": len(steps),
            "planned_at": format_ts(at),
        }
        calls.append(
            SimCall(
                [
                    _row(
                        at=at,
                        run_id=run_id,
                        simulated=simulated,
                        provider="aws",
                        probe_type="aws_cb_longest",
                        region=region,
                        sku=instance_type,
                        n_requested=count,
                        details=details,
                        latency_ms=latency,
                        **row_kwargs,
                    )
                ]
            )
        )
    return calls


AWS_ODCR_OUTCOMES = ("success", "no_capacity", "quota", "timeout", "error")
AWS_ODCR_OUTCOME_WEIGHTS = (55, 20, 10, 10, 5)


def aws_odcr(
    section: dict[str, Any], at: datetime, rng: random.Random, simulated: bool
) -> list[SimCall]:
    """1 call per `provisioning_probes` entry, 1 row per zone: all or nothing (ADR 0009)."""
    calls = []
    for entry in section.get("provisioning_probes", []):
        run_id = _run_id(rng)
        n = int(entry["n"])
        rows = []
        for letter in AWS_ZONE_LETTERS:  # 1 row per zone of the Region
            outcome = rng.choices(AWS_ODCR_OUTCOMES, weights=AWS_ODCR_OUTCOME_WEIGHTS)[0]
            n_placed: int | None = {"success": n, "no_capacity": 0}.get(outcome)
            error_code = {
                "no_capacity": "InsufficientInstanceCapacity",
                "quota": "InstanceLimitExceeded",
            }.get(outcome)
            latency = (
                rng.randint(90_000, 180_000) if outcome == "timeout" else rng.randint(8_000, 60_000)
            )
            rows.append(
                _row(
                    at=at,
                    run_id=run_id,
                    simulated=simulated,
                    provider="aws",
                    probe_type="aws_odcr",
                    region=entry["region"],
                    zone=f"{entry['region']}{letter}",
                    sku=entry["instance_type"],
                    n_requested=n,
                    n_placed=n_placed,
                    outcome=outcome,
                    error_code=error_code,
                    details={
                        "search_step": 1,
                        "zone_id": f"sim-{entry['region']}-az{AWS_ZONE_LETTERS.index(letter) + 1}",
                    },
                    latency_ms=latency,
                )
            )
        calls.append(SimCall(rows))
    return calls


def _azure_family(size: str) -> str:
    upper = size.upper()
    if upper.startswith("STANDARD_ND"):
        return "ND"
    if "H100" in upper:
        return "NC_H100"
    if "A100" in upper:
        return "A100"
    if "A10_" in upper:
        return "A10"
    if "T4" in upper:
        return "T4"
    return "other"


def _chunks(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _az_score(size: str, region: str, at: datetime, rng: random.Random) -> tuple[str, str]:
    """The score value and the `outcome` of 1 (size, Region, zone) row."""
    family = _azure_family(size)
    if family == "ND" and _stable(size, region) < 0.25:
        return "RestrictedSkuNotAvailable", "restricted"
    if rng.random() < 0.02:
        return "DataNotFoundOrStale", "none"
    high, medium, low = AZURE_SCORE_WEIGHTS[family]
    shift = _daily_wave(at)
    weights = (max(0.2, high + 2 * shift), medium, max(0.2, low - 2 * shift))
    return rng.choices(AZURE_SCORES, weights=weights)[0], "success"


def az_spot_score(
    section: dict[str, Any], at: datetime, rng: random.Random, simulated: bool
) -> list[SimCall]:
    """The calls of the probe: 1 regional call per group of 5 sizes and 8 regions, then 1 zonal
    call per Region and group of 5 sizes. A regional call has 1 row per size and Region. A zonal
    call has 1 row per size, Region, and zone (1, 2, 3), or 1 row with no zone in a Region
    without zones. Score values: High, Medium, Low, with some restricted and some `none`."""
    calls = []
    groups = [
        (regions, sizes, False)
        for sizes in _chunks(section["spot_sizes"], 5)
        for regions in _chunks(section["regions"], 8)
    ] + [
        ([region], sizes, True)
        for region in section["regions"]
        for sizes in _chunks(section["spot_sizes"], 5)
    ]
    for regions, sizes, zonal in groups:
        run_id = _run_id(rng)
        latency = rng.randint(300, 1500)
        rows = []
        for region in regions:
            if not zonal or region in AZURE_REGIONS_WITHOUT_ZONES:
                zones: tuple[str | None, ...] = (None,)
            else:
                zones = AZURE_ZONES
            for size in sizes:
                for zone in zones:
                    value, outcome = _az_score(size, region, at, rng)
                    rows.append(
                        _row(
                            at=at,
                            run_id=run_id,
                            simulated=simulated,
                            provider="azure",
                            probe_type="az_spot_score",
                            region=region,
                            zone=zone,
                            sku=size,
                            outcome=outcome,
                            error_code=value if outcome == "restricted" else None,
                            details={
                                "score": value,
                                "desired_count": 1,
                                "is_quota_available": (
                                    False
                                    if outcome == "none"
                                    else rng.random() >= AZ_QUOTA_FALSE_SHARE
                                ),
                                "scope": "zonal" if zonal else "regional",
                            },
                            latency_ms=latency,
                        )
                    )
        calls.append(SimCall(rows))
    return calls


def az_spot(
    section: dict[str, Any], at: datetime, rng: random.Random, simulated: bool
) -> list[SimCall]:
    """1 call. 1 row per size and region with the eviction rate bucket and the Spot price. A Region
    with no Spot data gives `none` rows (all values null). A failed call gives `error` rows that
    have no eviction rate and name the failed query in `eviction_error_code`."""
    run_id = _run_id(rng)
    latency = rng.randint(400, 2000)
    failed = rng.random() < AZ_SPOT_ERROR_SHARE
    # A price older than `spot_price_max_age_days` is no signal: the row is `none`. The live rows
    # are 650 to 659 days old at the default of 540.
    old_price_age_days = price_max_age_days(section) + 110
    hours = at.hour + at.minute / 60
    rows = []
    for region in section["regions"]:
        for size in section["spot_sizes"]:
            family = _azure_family(size)
            high, _, low = AZURE_SCORE_WEIGHTS[family]
            bucket_index = min(4, max(0, round((low - high) / 2 + 2 + rng.choice((-1, 0, 0, 1)))))
            # About 22% of real rows have no eviction rate: the bucket is unknown (null).
            unknown = failed or rng.random() < 0.22
            bucket = None if unknown else AZURE_EVICTION_BUCKETS[bucket_index]
            bounds = (None, None) if unknown else AZURE_EVICTION_BOUNDS[bucket_index]
            phase = 2 * math.pi * _stable(size, region)
            price = AZURE_BASE_PRICE[family] * (
                1 + 0.1 * math.sin(2 * math.pi * hours / 24 + phase) + 0.1 * _stable(region)
            )
            no_data = region in AZURE_SPOT_NO_DATA_REGIONS
            old_price = no_data and rng.random() < AZ_SPOT_NONE_WITH_PRICE_SHARE
            outcome = "error" if failed else "none" if no_data else "success"
            rows.append(
                _row(
                    at=at,
                    run_id=run_id,
                    simulated=simulated,
                    provider="azure",
                    probe_type="az_spot",
                    region=region,
                    sku=size,
                    outcome=outcome,
                    error_code=AZ_SPOT_ERROR_CODE if failed else None,
                    details={
                        "eviction_rate": None if no_data else bucket,
                        "eviction_rate_min_pct": None if no_data else bounds[0],
                        "eviction_rate_max_pct": None if no_data else bounds[1],
                        "price": None if no_data and not old_price else round(price, 4),
                        "price_effective_date": (
                            None
                            if no_data and not old_price
                            else (
                                at - timedelta(days=old_price_age_days if no_data else 0)
                            ).strftime("%Y-%m-%dT00:00:00Z")
                        ),
                        "currency": "USD",
                        "os_type": "linux",
                        "eviction_error_code": AZ_SPOT_ERROR_CODE if failed else None,
                        "price_error_code": None,
                    },
                    latency_ms=latency,
                )
            )
    return [SimCall(rows)]


def _az_provisioning(
    probe_type: str, section: dict[str, Any], at: datetime, rng: random.Random, simulated: bool
) -> list[SimCall]:
    """1 call per `provisioning_probes` entry of this probe type. 1 row with the outcome."""
    calls = []
    for entry in section.get("provisioning_probes", []):
        if entry["probe_type"] != probe_type:
            continue
        run_id = _run_id(rng)
        n = int(entry["n"])
        outcome = rng.choices(ALLOCATION_OUTCOMES, weights=ALLOCATION_OUTCOME_WEIGHTS)[0]
        n_placed: int | None = {
            "success": n,
            "partial": rng.randint(1, max(1, n - 1)),
            "no_capacity": 0,
        }.get(outcome)
        error_code = {"no_capacity": "AllocationFailed", "error": "InternalExecutionError"}.get(
            outcome
        )
        latency = (
            rng.randint(90_000, 180_000) if outcome == "timeout" else rng.randint(8_000, 60_000)
        )
        zone = entry.get("zone")
        calls.append(
            SimCall(
                [
                    _row(
                        at=at,
                        run_id=run_id,
                        simulated=simulated,
                        provider="azure",
                        probe_type=probe_type,
                        region=entry["region"],
                        zone=str(zone) if zone is not None else None,
                        sku=entry["sku"],
                        placement_group_mode=(
                            entry["placement_group_mode"] if probe_type == "az_vmss_flex" else "n/a"
                        ),
                        n_requested=n,
                        n_placed=n_placed,
                        outcome=outcome,
                        error_code=error_code,
                        details={"n_failed": (n - n_placed) if n_placed is not None else None},
                        latency_ms=latency,
                    )
                ]
            )
        )
    return calls


def az_capacity_reservation(section, at, rng, simulated) -> list[SimCall]:
    return _az_provisioning("az_capacity_reservation", section, at, rng, simulated)


def az_vmss_flex(section, at, rng, simulated) -> list[SimCall]:
    return _az_provisioning("az_vmss_flex", section, at, rng, simulated)


def az_precheck(
    section: dict[str, Any], at: datetime, rng: random.Random, simulated: bool
) -> list[SimCall]:
    """1 call. 1 row per `provisioning_probes` entry, in config order. Spec 0010 section 4.1.

    Each row names the probe type of its entry in `details.entry_probe_type` (ADR 0022).
    """
    entries = section.get("provisioning_probes") or []
    if not entries:
        return []
    run_id = _run_id(rng)
    rows = []
    for entry in entries:
        zone = entry.get("zone")
        details: dict[str, Any] = {
            "entry_probe_type": entry["probe_type"],
            "n_ceiling": None,
            "capacity_reservation_supported": entry["probe_type"] == "az_capacity_reservation",
            "family": PRECHECK_FAMILY,
            "vcpus": PRECHECK_VCPUS,
            "sku_zones": [str(zone)] if zone is not None else ["1", "2", "3"],
            "restrictions": [],
        }
        if rng.random() < PRECHECK_RESTRICTED_SHARE:
            outcome, error_code = "restricted", PRECHECK_RESTRICTED_CODE
            details["reason"] = "Location restriction"
            details["restrictions"] = [
                {
                    "type": "Location",
                    "reason_code": PRECHECK_RESTRICTED_CODE,
                    "values": [entry["region"]],
                    "locations": [entry["region"]],
                    "zones": [],
                }
            ]
        else:
            outcome, error_code = "success", None
            family_used = PRECHECK_VCPUS * rng.randint(0, 50)
            family_free = PRECHECK_FREE_VCPUS - family_used
            details.update(
                n_ceiling=family_free // PRECHECK_VCPUS,
                family_current_vcpus=family_used,
                family_limit_vcpus=PRECHECK_FREE_VCPUS,
                total_current_vcpus=0,
                total_limit_vcpus=PRECHECK_FREE_VCPUS,
                family_available_vcpus=family_free,
                total_available_vcpus=PRECHECK_FREE_VCPUS,
            )
        rows.append(
            _row(
                at=at,
                run_id=run_id,
                simulated=simulated,
                provider="azure",
                probe_type="az_precheck",
                region=entry["region"],
                zone=str(zone) if zone is not None else None,
                sku=entry["sku"],
                placement_group_mode=entry["placement_group_mode"],
                n_requested=int(entry["n"]),
                outcome=outcome,
                error_code=error_code,
                details=details,
                latency_ms=rng.randint(300, 2000),
            )
        )
    return [SimCall(rows)]


Generator = Callable[[dict[str, Any], datetime, random.Random, bool], list[SimCall]]
GENERATORS: dict[str, Generator] = {
    "aws_cb_offering": aws_cb_offering,
    "aws_cb_earliest": aws_cb_earliest,
    "aws_cb_longest": aws_cb_longest,
    "aws_spot_score": aws_spot_score,
    "aws_spot_price": aws_spot_price,
    "aws_spot_advisor": aws_spot_advisor,
    "aws_odcr": aws_odcr,
    "az_capacity_reservation": az_capacity_reservation,
    "az_vmss_flex": az_vmss_flex,
    "az_spot_score": az_spot_score,
    "az_spot": az_spot,
    "az_precheck": az_precheck,
}


def sim_calls(
    probe_type: str,
    section: dict[str, Any],
    at: datetime,
    rng: random.Random,
    simulated: bool = True,
) -> list[SimCall]:
    """The synthetic calls of 1 probe type at 1 moment."""
    return GENERATORS[probe_type](section, at, rng, simulated)


def write_call(store: Store, experiment: str, provider: str, probe_type: str, call: SimCall) -> int:
    """Write 1 call: the raw object first, then the rows as 1 JSONL object. Return the row count."""
    if not call.rows:
        return 0
    end = datetime.strptime(call.rows[-1].ts_utc, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    uid = uuid.UUID(call.rows[0].run_id)
    if call.raw:
        store.put(raw_key(experiment, provider, probe_type, end, uid), call.raw)
    store.put(
        build_key(experiment, provider, probe_type, end, target_for(call.rows), uid),
        to_jsonl(call.rows),
    )
    return len(call.rows)


def floor_to_5_minutes(moment: datetime) -> datetime:
    return moment.replace(minute=moment.minute - moment.minute % 5, second=0, microsecond=0)


def write_history(
    config: Config, store: Store, hours: int, seed: int = 42, end: datetime | None = None
) -> dict[str, dict[str, int]]:
    """Write `hours` of history for every probe type of each configured provider.

    Returns `{probe_type: {"calls": n, "rows": n}}`. Raises `StoreNotEmptyError` for a
    `file://` store whose folder is not empty.
    """
    check_empty(store)
    rng = random.Random(seed)
    end = floor_to_5_minutes(end or datetime.now(UTC))
    start = end - timedelta(hours=hours)
    counts: dict[str, dict[str, int]] = {}
    for probe_type in PROBE_TYPES:
        provider = PROVIDER_OF[probe_type]
        section = getattr(config, provider)
        if section is None:
            continue
        # A job-eligible probe type with no job never runs (ADR 0010 section 1), in `sim` too:
        # its settings would otherwise be missing or stale from an unrelated job.
        if not config.has_job(provider, probe_type):
            continue
        step = timedelta(seconds=_cadence_s(section, probe_type))
        counts[probe_type] = {"calls": 0, "rows": 0}
        at = start
        while at < end:
            for call in sim_calls(probe_type, section, at, rng):
                counts[probe_type]["rows"] += write_call(
                    store, config.experiment, provider, probe_type, call
                )
                counts[probe_type]["calls"] += 1
            at += step
        log.info(
            "sim.wrote probe_type=%s calls=%d rows=%d",
            probe_type,
            counts[probe_type]["calls"],
            counts[probe_type]["rows"],
        )
    return counts
