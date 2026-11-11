"""The record schema of spec 0002 section 7.2. One row per probe attempt or poll."""

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from typing import Any

PROVIDERS = ("aws", "azure")
PLACEMENT_GROUP_MODES = ("single", "multi", "n/a")

# The order of spec 0002 table 7.2. `az_precheck` is the Azure pre-check (spec 0010, Z1).
PROBE_TYPES = (
    "aws_cb_offering",
    "aws_cb_earliest",
    "aws_cb_longest",
    "aws_spot_score",
    "aws_spot_price",
    "aws_spot_advisor",
    "aws_odcr",
    "az_capacity_reservation",
    "az_vmss_flex",
    "az_spot_score",
    "az_spot",
    "az_precheck",
)
# Signal probes read a value. In `local` mode the core writes synthetic rows for them.
SIGNAL_PROBE_TYPES = (
    "aws_spot_score",
    "aws_spot_price",
    "aws_spot_advisor",
    "az_spot_score",
    "az_spot",
)
# In `local` mode the core writes synthetic rows for these types and does not call the probe:
# the signal probes and the read-only Capacity Block offerings. Spec 0003 section 7.1.
CORE_SIM_PROBE_TYPES = (*SIGNAL_PROBE_TYPES, "aws_cb_offering", "aws_cb_earliest", "aws_cb_longest")
# Azure provisioning probes allocate capacity. `aws_odcr` (spec 0002 section 4.9) does its own
# fault injection in `local` mode, like these 2, but keeps its own cadence key (no AWS pre-check
# probe exists, so it does not join AZ_PROVISIONING_CADENCE_TYPES below).
ALLOCATION_PROBE_TYPES = ("az_capacity_reservation", "az_vmss_flex")
# The Azure cadence key `az_provisioning` covers the pre-check and both Azure provisioning probes
# (spec 0010, Z1).
AZ_PROVISIONING_CADENCE_TYPES = ("az_precheck", *ALLOCATION_PROBE_TYPES)

OUTCOMES = (
    "success",
    "partial",
    "no_capacity",
    "restricted",
    "restricted_or_no_capacity",
    "quota",
    "timeout",
    "error",
    "none",
    "cap",
)


def format_ts(moment: datetime) -> str:
    """ISO 8601 in UTC with milliseconds and a `Z` suffix."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def simulated_from_run_mode(run_mode: str) -> bool:
    """ADR 0011 section 5: the reader rule for a row written before this change.

    Such a row has `run_mode` and no `simulated` field. `run_mode: local` means
    `simulated: true`; any other value (`cloud-gpu`, `cloud-cpu`) means `simulated: false`.
    """
    return run_mode == "local"


@dataclass(slots=True)
class Record:
    """One row. The field order is the column order of spec 0002 table 7.2."""

    ts_utc: str
    run_id: str
    simulated: bool
    provider: str
    region: str
    zone: str | None
    probe_type: str
    sku: str
    placement_group_mode: str
    n_requested: int
    n_placed: int | None
    outcome: str
    error_code: str | None
    latency_ms: int
    details: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        """One JSON object, with no new line inside it."""
        return json.dumps(asdict(self), separators=(",", ":"), ensure_ascii=True)

    def validate(self) -> list[str]:
        """Return the list of faults. An empty list means that the row is valid."""
        faults = []
        if not isinstance(self.simulated, bool):
            faults.append("simulated must be true or false")
        if self.provider not in PROVIDERS:
            faults.append(f"provider {self.provider!r} is not one of {PROVIDERS}")
        if self.probe_type not in PROBE_TYPES:
            faults.append(f"probe_type {self.probe_type!r} is not one of {PROBE_TYPES}")
        if self.placement_group_mode not in PLACEMENT_GROUP_MODES:
            faults.append(f"placement_group_mode {self.placement_group_mode!r} is not valid")
        if self.outcome not in OUTCOMES:
            faults.append(f"outcome {self.outcome!r} is not one of {OUTCOMES}")
        if not isinstance(self.details, dict):
            faults.append("details must be an object")
        if not isinstance(self.ts_utc, str) or not self.ts_utc.endswith("Z"):
            faults.append("ts_utc must be an ISO 8601 string with a Z suffix")
        return faults


FIELD_NAMES = tuple(f.name for f in fields(Record))


def to_jsonl(rows: list[Record]) -> bytes:
    """The rows of 1 probe call as JSON Lines, with a new line after each row."""
    return "".join(row.to_json() + "\n" for row in rows).encode("utf-8")
