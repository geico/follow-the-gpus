"""Spot placement scores for a fixed list of Spot score requests. Spec 0002 4.2, spec 0008 4."""

import logging
from typing import Any

from ftg_prober.aws.common import is_gpu_type, paginate, row
from ftg_prober.log import kv
from ftg_prober.probes import Probe, ProbeContext
from ftg_prober.record import Record

log = logging.getLogger(__name__)


def request_name(record: Record) -> str | None:
    """The Spot score request name of a row: `details.request`, or `details.panel` on an old row."""
    details = record.details
    return details["request"] if "request" in details else details.get("panel")


CPU_CONTROL_MAX_SCORE = 3
CPU_MIX_CONTROL_MIN_SCORE = 4
CPU_SHORT_SCORE = 1
CONFIG_LIMIT_CODE = "MaxConfigLimitExceeded"
CAPACITY_CAP_CODE = "TargetCapacityLimitExceeded"


class SpotPlacementScoreProbe(Probe):
    """1 `GetSpotPlacementScores` call per Spot score request, for all configured Regions at once.

    The probe keeps 2 values for the process lifetime: the names of the entries that got a
    score, and the freeze flag. After `MaxConfigLimitExceeded`, only those entries run.
    """

    probe_type = "aws_spot_score"
    provider = "aws"
    default_cadence = "5m"

    def __init__(self) -> None:
        self.frozen = False
        self.live: set[str] = set()

    def run(self, ctx: ProbeContext) -> list[Record]:
        regions = list(ctx.config["regions"])
        requests = list(ctx.config["spot_score_requests"])
        ec2 = ctx.clients.boto3("ec2", regions[0])
        rows: list[Record] = []
        for entry in requests:
            if self.frozen and entry["name"] not in self.live:
                log.warning("spot_score.skipped %s", kv(request=entry["name"], reason="frozen"))
                continue
            rows.extend(self._call(ctx, ec2, entry, regions))
        add_controls(requests, rows)
        return rows

    def _call(
        self, ctx: ProbeContext, ec2: Any, entry: dict[str, Any], regions: list[str]
    ) -> list[Record]:
        types = list(entry["instance_types"])
        result = paginate(
            ec2.get_spot_placement_scores,
            {
                "InstanceTypes": types,
                "TargetCapacity": 1,
                "TargetCapacityUnitType": "units",
                "SingleAvailabilityZone": False,
                "RegionNames": regions,
            },
            "SpotPlacementScores",
            probe_type=self.probe_type,
            region=",".join(regions),
        )
        base = {"instance_types": types, "request": entry["name"], "label": entry.get("label")}

        def make(region: str, outcome: str, details: dict[str, Any]) -> Record:
            return row(
                ctx,
                self.probe_type,
                region=region,
                sku=",".join(types),
                outcome=outcome,
                error_code=result.error_code,
                latency_ms=result.latency_ms,
                details={**base, **details},
            )

        code = result.error_code
        if code and code.startswith(CONFIG_LIMIT_CODE):
            if not self.frozen:
                self.frozen = True
                log.warning(
                    "spot_score.frozen %s", kv(request=entry["name"], live_configs=len(self.live))
                )
            extra = {"frozen": True, "live_configs": len(self.live)}
            return [make(region, "error", extra) for region in regions]
        if code and code.startswith(CAPACITY_CAP_CODE):
            return [make(region, "cap", {}) for region in regions]
        if code:
            return [make(region, "error", {}) for region in regions]

        self.live.add(entry["name"])
        scores = {
            item["Region"]: item["Score"]
            for item in result.items
            if item.get("Region") and not item.get("AvailabilityZoneId") and "Score" in item
        }
        return [
            make(region, "success", {"score": int(scores[region])})
            if region in scores
            else make(region, "none", {})
            for region in regions
        ]


def add_controls(requests: list[dict[str, Any]], rows: list[Record]) -> None:
    """Write `details.control` on the rows of the 3 control kinds of spec 0002 section 4.2.

    - CPU control: an entry with no GPU type and fewer than 3 types. Its score must be 3 or
      lower. AWS scores a request of fewer than 3 types low, so a 2-type CPU entry still uses
      this rule, not the CPU mix rule.
    - CPU mix control: an entry with no GPU type and 3 or more types. Its score must be above 3.
      A score below the minimum gives `ok` None, not False, when a single-type request of a type
      in the mix scores 1 in the same Region and run: the Region is short of that capacity, the
      low score is real Spot availability data, and the control says nothing.
    - GPU mix control: an entry with a GPU type and 2 or more types. No single-type request
      whose type is in the mix may score above the mix in the same Region.

    `ok` is None when a score that the rule needs is missing.
    """
    scores: dict[tuple[str, str], int] = {}
    for record in rows:
        if record.outcome == "success":
            scores[(request_name(record), record.region)] = record.details["score"]
    singles = {
        entry["instance_types"][0]: entry["name"]
        for entry in requests
        if len(entry["instance_types"]) == 1
    }
    by_name = {entry["name"]: entry for entry in requests}
    for record in rows:
        entry = by_name.get(request_name(record))
        if entry is None:
            continue
        types = entry["instance_types"]
        own = scores.get((entry["name"], record.region))
        if not any(is_gpu_type(t) for t in types) and len(types) < 3:
            ok = None if own is None else own <= CPU_CONTROL_MAX_SCORE
            record.details["control"] = {
                "rule": "cpu_score_max",
                "max": CPU_CONTROL_MAX_SCORE,
                "ok": ok,
            }
        elif not any(is_gpu_type(t) for t in types):
            single_scores = {
                t: scores.get((singles[t], record.region)) for t in types if t in singles
            }
            short = CPU_SHORT_SCORE in single_scores.values()
            if own is None:
                ok = None
            elif own >= CPU_MIX_CONTROL_MIN_SCORE:
                ok = True
            else:
                ok = None if short else False
            record.details["control"] = {
                "rule": "cpu_mix_min",
                "min": CPU_MIX_CONTROL_MIN_SCORE,
                "singles": single_scores,
                "ok": ok,
            }
        elif len(types) > 1:
            single_scores = {
                t: scores.get((singles[t], record.region)) for t in types if t in singles
            }
            known = [s for s in single_scores.values() if s is not None]
            ok = None if own is None or not known else all(s <= own for s in known)
            record.details["control"] = {
                "rule": "single_not_above_mix",
                "singles": single_scores,
                "ok": ok,
            }
        else:
            continue
        if record.details["control"]["ok"] is False:
            log.warning(
                "spot_score.control_failed %s",
                kv(
                    request=entry["name"],
                    region=record.region,
                    rule=record.details["control"]["rule"],
                ),
            )
