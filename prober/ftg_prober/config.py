"""Load and validate an experiment file of modules and jobs. ADR 0010, spec 0003 section 3.

A job names a built-in probe type, or a named module from `modules:`, plus `regions` and
`interval`. Each probe type's code defaults (`MODULE_DEFAULTS`) are merged at the top level
only with the job's module override; a list or a nested mapping is replaced as a whole, never
merged field by field. The provisioning probes (`aws.provisioning_probes`,
`azure.provisioning_probes`) and `*.quota` stay outside the jobs model (ADR 0010, sections 7
and 8): they keep the flat shape of
the earlier schema, because their Terraform wiring is out of scope here (ADR 0010, section 10,
item 4). ADR 0011 gives each provisioning entry its own `simulate` and
`allow_billable_provisioning` keys in place of the earlier per-entry `run_mode` (section 3,
section 7 item 1).

`load_config` returns a `Config` whose `aws`/`azure` fields are a flat, legacy-shaped mapping:
the loader resolves each job against its module and promotes the result onto the flat section,
so that the scheduler, `ProbeContext`, and every probe keep reading `ctx.config["regions"]`,
`ctx.config["spot_score_requests"]`, and so on, unchanged.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ftg_prober.record import AZ_PROVISIONING_CADENCE_TYPES

CADENCE_RE = re.compile(r"^(\d+)(s|m|h|d)$")
# The region and size names of a quota block. Spec 0003 section 3.
AZURE_REGION_RE = re.compile(r"^[a-z0-9]+$")
AZURE_REGION_HINT = "lowercase letters and digits only"
AWS_REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-[0-9]+$")
AWS_REGION_HINT = "a Region code such as us-east-1"
# An EC2 instance type, for example `g5.xlarge` or `p6-b200.48xlarge`.
AWS_SIZE_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)?\.[a-z0-9]+$")
# The hard limit of an entry's `n_max`, the cap of the upward search (ADR 0023).
N_MAX_LIMIT = 16
CADENCE_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
# Spec 0002 section 4.2. A project guard, not an AWS value: AWS states no number for
# the limit on new configurations. The request list has 9 entries. Spec 0008 section 4.4
# freezes the list on MaxConfigLimitExceeded.
MAX_SPOT_SCORE_REQUESTS = 11
# The most DescribeCapacityBlockOfferings calls per Region and cycle. They wait in a queue and
# drain at 1 call per wake. Spec 0008 section 3.3.
MAX_CB_CALLS_PER_REGION = 200
# The worst case calls of 1 `aws_cb_longest` search (spec 0008, section 3B.5).
MAX_CB_LONGEST_CALLS = 19
# ADR 0010 section 2. Safe as a path segment, a cloud tag value, and a Terraform value.
EXPERIMENT_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
EXPERIMENT_MAX_LENGTH = 63
# Spec 0003 section 2: an experiment name is dated and descriptive.
EXPERIMENT_EXAMPLE = "ftg-20261012-signals"
# Optional `azure` keys that the provisioning probes read. Unchanged by ADR 0010 (section 10, item
# 4): the provisioning probes and their timing stay outside the jobs model here.
AZURE_OPTIONAL_INT_KEYS = (
    "probe_timeout_seconds",
    "poll_interval_seconds",
    "teardown_timeout_seconds",
)
# Optional `aws` keys that the `aws_odcr` probe reads. Spec 0002 section 4.9.
AWS_OPTIONAL_INT_KEYS = (
    "probe_timeout_seconds",
    "poll_interval_seconds",
    "teardown_timeout_seconds",
)
# Every key a cloud section may set, outside `modules:` and `jobs:`. An unknown key is refused
# (`_unknown_key_faults`), instead of being silently ignored, so that a key of the earlier, flat
# schema that moved under the jobs model (spec 0003 section 3's "old key, new place" table) is
# never silently dropped. The fault message echoes the key as the file gave it; the list below
# never names a key this schema does not use.
KNOWN_CLOUD_KEYS: dict[str, frozenset[str]] = {
    "aws": frozenset(
        {
            "modules",
            "jobs",
            "cadence",
            "provisioning_probes",
            "quota",
            "probe_timeout_seconds",
            "poll_interval_seconds",
            "teardown_timeout_seconds",
            "queue_wake_seconds",
            "queue_max_calls_per_wake",
            "queue_max_throttle_attempts",
            "queue_jitter",
            "queue_backoff_base_seconds",
            "queue_backoff_cap_seconds",
        }
    ),
    "azure": frozenset(
        {
            "modules",
            "jobs",
            "cadence",
            "provisioning_probes",
            "quota",
            "probe_timeout_seconds",
            "poll_interval_seconds",
            "teardown_timeout_seconds",
        }
    ),
}
# Every key a `*.jobs` entry may set. 1 constant, so that a later change extends this set in 1
# place instead of several. ADR 0011 section 2 adds `simulate`; section 3 reserves
# `allow_billable_provisioning` for a future provisioning job-eligible type (a no-op today:
# section 4).
JOB_ENTRY_KEYS: frozenset[str] = frozenset(
    {"module", "regions", "interval", "simulate", "allow_billable_provisioning"}
)
# Every key a `*.quota.sizes` entry may set (spec 0011 section 3, spec 0003 section 3).
QUOTA_SIZE_ENTRY_KEYS: frozenset[str] = frozenset({"size", "count", "regions"})
# Every key an `aws.provisioning_probes` entry may set (spec 0002 section 4.9). ADR 0011 section 3
# replaces `run_mode` with `simulate` and `allow_billable_provisioning`.
AWS_PROVISIONING_ENTRY_KEYS: frozenset[str] = frozenset(
    {
        "instance_type",
        "region",
        "n",
        "n_max",
        "simulate",
        "allow_billable_provisioning",
        "sim_script",
    }
)
# Every key an `azure.provisioning_probes` entry may set. `sim_script` is the simulated
# fault-injection step (or steps). There is no `run_mode` any more (ADR 0011: no `run_mode`
# anywhere); `terraform/azure/probe.tf`'s "active" rule reads `simulate` from this same file
# instead. The probe type is the key of the mapping, not an entry key (ADR 0022).
AZURE_PROVISIONING_ENTRY_KEYS: frozenset[str] = frozenset(
    {
        "sku",
        "region",
        "zone",
        "n",
        "n_max",
        "placement_group_mode",
        "simulate",
        "allow_billable_provisioning",
        "sim_script",
    }
)
# The keys of `azure.provisioning_probes`: one list of entries per probe type (ADR 0022).
AZURE_PROVISIONING_PROBE_TYPES: tuple[str, ...] = ("az_capacity_reservation", "az_vmss_flex")
# Every key an `aws.jobs[aws_spot_score].spot_score_requests` entry may set (spec 0002 section 4.2).
SPOT_SCORE_REQUEST_KEYS: frozenset[str] = frozenset({"name", "instance_types", "label"})

# Code defaults per job-eligible probe type (ADR 0010 section 3). A job names 1 of these types
# directly, to use its defaults, or a named module from `modules:` that overrides some of them.
# The provisioning probe types (`aws_odcr`, `az_capacity_reservation`, `az_vmss_flex`) and the
# Azure pre-check are not job-eligible in this change: see the module docstring.
#
# `aws_cb_offering`'s defaults are the ADR 0010 section 3 worked example exactly, so that
# example is also a unit test (`test_adr_0010_section_3_merge_example` in test_config.py).
MODULE_DEFAULTS: dict[str, dict[str, Any]] = {
    "aws_spot_score": {"spot_score_requests": []},
    "aws_spot_price": {},
    "aws_spot_advisor": {},
    "aws_cb_offering": {
        "instance_types": ["p5.4xlarge", "p4d.24xlarge"],
        "instance_count": 1,
        "duration_hours": [24],
        "start_window_days": 14,
    },
    "aws_cb_earliest": {},
    "aws_cb_longest": {},
    "az_spot_score": {"spot_sizes": []},
    "az_spot": {"spot_sizes": [], "spot_price_max_age_days": 540},
}
# Settings a module may set beyond `MODULE_DEFAULTS[type]`'s own keys: still a real setting of
# that probe type, just one with no code default (its absence has its own meaning). This, plus
# `MODULE_DEFAULTS[type]`, is the complete, closed list of keys a `*.modules` entry may set
# (critical review finding: any other key, for example `provisioning_probes`, is now a fault
# instead of silently promoted onto the flat section and so past the allocation guard).
MODULE_EXTRA_KEYS: dict[str, tuple[str, ...]] = {
    "aws_cb_offering": ("earliest_duration_hours", "instance_counts"),
}
PROVIDER_OF_JOB_TYPE: dict[str, str] = {
    t: ("aws" if t.startswith("aws_") else "azure") for t in MODULE_DEFAULTS
}
ALLOWED_SETTING_KEYS: dict[str, frozenset[str]] = {
    t: frozenset(MODULE_DEFAULTS[t]) | frozenset(MODULE_EXTRA_KEYS.get(t, ()))
    for t in MODULE_DEFAULTS
}
# A job-eligible probe type that needs another job-eligible type's job to also be listed,
# because it reads that type's settings from the flat, legacy-shaped section (critical review
# finding 2): `aws_spot_price` and `aws_spot_advisor` read the `aws_spot_score` job's
# `spot_score_requests`; `aws_cb_earliest` and `aws_cb_longest` read the `aws_cb_offering` job's
# capacity block settings (`instance_types`, `instance_count`). None of the 3 has settings of
# its own (`MODULE_DEFAULTS` above), by design.
JOB_DEPENDENCIES: dict[str, str] = {
    "aws_spot_price": "aws_spot_score",
    "aws_spot_advisor": "aws_spot_score",
    "aws_cb_earliest": "aws_cb_offering",
    "aws_cb_longest": "aws_cb_offering",
}


class ConfigError(Exception):
    """The config file is not valid. The message lists each fault, 1 per line."""


def parse_cadence(text: Any) -> int:
    """Turn `5m`, `2h`, `24h`, or `30s` into seconds. Raise ValueError on a bad value."""
    match = CADENCE_RE.match(str(text).strip()) if text is not None else None
    if not match:
        raise ValueError(f"cadence {text!r} must look like 30s, 5m, 2h, or 1d")
    seconds = int(match.group(1)) * CADENCE_UNITS[match.group(2)]
    if seconds <= 0:
        raise ValueError(f"cadence {text!r} must be more than 0")
    return seconds


def cadence_key(probe_type: str) -> str:
    """The `cadence` key of a probe type. The pre-check and both provisioning probes use
    `az_provisioning`."""
    return "az_provisioning" if probe_type in AZ_PROVISIONING_CADENCE_TYPES else probe_type


def earliest_hours(blocks: dict[str, Any]) -> list[int]:
    """The durations of `aws_cb_earliest`: `earliest_duration_hours`, else `duration_hours`."""
    return [int(h) for h in blocks.get("earliest_duration_hours", blocks["duration_hours"])]


def counts_for(blocks: dict[str, Any], instance_type: str) -> list[int]:
    """The `InstanceCount` values of 1 type: `instance_counts[type]`, else `[instance_count]`."""
    counts = (blocks.get("instance_counts") or {}).get(instance_type)
    return [int(n) for n in (counts if counts is not None else [blocks["instance_count"]])]


@dataclass(frozen=True)
class Config:
    experiment: str
    aws: dict[str, Any] | None
    azure: dict[str, Any] | None
    # The job-eligible probe types (`MODULE_DEFAULTS`) that have a job, per provider. Critical
    # review finding 2: "a job absent from the file does not run" (ADR 0010 section 1) also
    # applies to a job-eligible type with no job; the scheduler reads this to filter its probes.
    job_types: dict[str, frozenset[str]] = field(default_factory=dict)
    # Each job-eligible probe type's own `simulate: true` key, per provider (ADR 0011 section
    # 2). The scheduler ORs this with the run-wide `--simulate` flag.
    job_simulate: dict[str, dict[str, bool]] = field(default_factory=dict)

    def section(self, provider: str) -> dict[str, Any]:
        found = getattr(self, provider, None)
        if found is None:
            raise ConfigError(f"the config has no `{provider}` section")
        return found

    def has_job(self, provider: str, probe_type: str) -> bool:
        """True when `probe_type` is not job-eligible, or has a job for `provider`.

        A `Config` built directly (not through `load_config`) has no `job_types` entry for
        `provider` at all, not an empty one; this is permissive for that case (every probe
        runs, as before this change) rather than filtering it to nothing. `load_config` always
        sets an entry, even an empty one, for every provider section the file has.
        """
        if probe_type not in MODULE_DEFAULTS:
            return True
        if provider not in self.job_types:
            return True
        return probe_type in self.job_types[provider]

    def simulate_job(self, provider: str, probe_type: str) -> bool:
        """This probe type's own job `simulate: true` key, or False with no such job."""
        return bool(self.job_simulate.get(provider, {}).get(probe_type, False))


def load_config(path: str | Path, *, force_simulate: bool = False) -> Config:
    """Read the YAML file, validate it, and return the config. Raise ConfigError on a fault.

    `force_simulate` treats every job as simulated for the allocation guard (ADR 0011 section
    3, rule 1): the `sim` command passes `True` always; `run` passes its own `--simulate` flag.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    faults = validate(data, force_simulate=force_simulate)
    if faults:
        raise ConfigError("\n".join(f"{path}: {fault}" for fault in faults))
    job_types: dict[str, frozenset[str]] = {}
    job_simulate: dict[str, dict[str, bool]] = {}
    for provider in ("aws", "azure"):
        if isinstance(data.get(provider), dict):
            resolved, _faults = _validate_jobs(data[provider], provider)
            job_types[provider] = frozenset(resolved)
            job_simulate[provider] = {pt: job["simulate"] for pt, job in resolved.items()}
    return Config(
        experiment=data["experiment"] if "experiment" in data else simulated_experiment(),
        aws=_resolved_section(data.get("aws"), "aws"),
        azure=_resolved_section(data.get("azure"), "azure"),
        job_types=job_types,
        job_simulate=job_simulate,
    )


def experiment_fault(name: str) -> str | None:
    """Return the fault of an experiment name, or None when the name is valid (spec 0003
    section 2). The name holds a `-YYYYMMDD-` segment that is a real date, then at least 1
    descriptive word: `ftg-20261012-signals`."""
    if len(name) > EXPERIMENT_MAX_LENGTH or not EXPERIMENT_RE.fullmatch(name):
        return (
            f"experiment must match ^[a-z0-9]+(-[a-z0-9]+)*$ in at most "
            f"{EXPERIMENT_MAX_LENGTH} characters (lowercase words of letters and digits, "
            f"joined by single dashes)"
        )
    words = name.split("-")
    if not any(_is_date(word) for word in words[1:-1]):
        return (
            f"experiment `{name}` must be dated and descriptive: a word, a `-YYYYMMDD-` date, "
            f"and at least 1 word after it, for example `{EXPERIMENT_EXAMPLE}`"
        )
    return None


def _is_date(word: str) -> bool:
    if len(word) != 8 or not word.isdigit():
        return False
    try:
        datetime.strptime(word, "%Y%m%d")
    except ValueError:
        return False
    return True


def simulated_experiment() -> str:
    """The name of a simulated run whose file has no `experiment` key: today's UTC date."""
    return f"ftg-{datetime.now(UTC):%Y%m%d}-simulated"


def validate(data: Any, *, force_simulate: bool = False) -> list[str]:
    """Return each schema fault as 1 message. An empty list means that the config is valid."""
    if not isinstance(data, dict):
        return ["the top level must be a mapping"]
    faults: list[str] = []
    if "experiment" in data:
        _check_str(faults, data["experiment"], "experiment")
        if isinstance(data["experiment"], str):
            fault = experiment_fault(data["experiment"])
            if fault:
                faults.append(fault)
    elif not force_simulate:
        faults.append(
            f"the config needs an `experiment` name, dated and descriptive, for example "
            f"`{EXPERIMENT_EXAMPLE}`"
        )
    if "aws" not in data and "azure" not in data:
        faults.append("the config needs an `aws` section, an `azure` section, or both")
    for key in data:
        if key not in ("experiment", "aws", "azure"):
            faults.append(f"unknown top-level key `{key}`")
    if "aws" in data:
        faults.extend(_validate_aws(data["aws"]))
        faults.extend(_allocation_faults_aws(data["aws"], force_simulate))
    if "azure" in data:
        faults.extend(_validate_azure(data["azure"]))
        faults.extend(_allocation_faults(data["azure"], force_simulate))
    return faults


def _allocation_faults(section: Any, force_simulate: bool) -> list[str]:
    """1 fault per Azure provisioning entry that is not simulated and lacks its own consent.
    ADR 0011 section 3.

    An entry is simulated by its own `simulate: true` key, or by `force_simulate` (the run-wide
    `--simulate` flag, or the `sim` command, which simulates every job). A non-simulated
    provisioning entry needs its own `allow_billable_provisioning: true`.
    """
    if force_simulate or not isinstance(section, dict):
        return []
    faults = []
    for where, entry in _azure_entries(section.get("provisioning_probes")):
        if entry.get("simulate") is True:
            continue
        if entry.get("allow_billable_provisioning") is not True:
            faults.append(
                f"{where} ({entry.get('sku')}, {entry.get('region')}) is"
                " a provisioning probe: it creates VMs."
                " Set `allow_billable_provisioning: true` on purpose, or add `simulate: true`"
            )
    return faults


def _azure_entries(probes: Any) -> list[tuple[str, dict[str, Any]]]:
    """The `(where, entry)` pairs of a well-shaped `azure.provisioning_probes` mapping. A wrong
    shape gives none: `_validate_azure` reports it."""
    if not isinstance(probes, dict):
        return []
    return [
        (f"azure.provisioning_probes.{probe_type}[{i}]", entry)
        for probe_type, entries in probes.items()
        if probe_type in AZURE_PROVISIONING_PROBE_TYPES and isinstance(entries, list)
        for i, entry in enumerate(entries)
        if isinstance(entry, dict)
    ]


def _allocation_faults_aws(section: Any, force_simulate: bool) -> list[str]:
    """1 fault per AWS provisioning entry that is not simulated and lacks its own consent. Same
    rule as Azure.

    An `aws_odcr` entry that is not simulated creates a Capacity Reservation.
    """
    if force_simulate or not isinstance(section, dict):
        return []
    if not isinstance(section.get("provisioning_probes"), list):
        return []
    faults = []
    for i, entry in enumerate(section["provisioning_probes"]):
        if not isinstance(entry, dict) or entry.get("simulate") is True:
            continue
        if entry.get("allow_billable_provisioning") is not True:
            faults.append(
                f"aws.provisioning_probes[{i}] ({entry.get('instance_type')},"
                f" {entry.get('region')})"
                " is a provisioning probe: it creates a Capacity Reservation."
                " Set `allow_billable_provisioning: true` on purpose, or add `simulate: true`"
            )
    return faults


def _validate_aws(section: Any) -> list[str]:
    faults: list[str] = []
    if not isinstance(section, dict):
        return ["`aws` must be a mapping"]
    resolved, job_faults = _validate_jobs(section, "aws")
    faults.extend(job_faults)
    faults.extend(_unknown_key_faults(section, "aws"))
    faults.extend(_cadence_conflict_faults(section, resolved, "aws"))
    if "aws_spot_score" in resolved:
        faults.extend(
            _validate_spot_score_requests(
                resolved["aws_spot_score"]["settings"].get("spot_score_requests"),
                "aws.jobs[aws_spot_score].spot_score_requests",
            )
        )
    if "aws_cb_offering" in resolved:
        faults.extend(
            _validate_capacity_blocks(
                resolved["aws_cb_offering"]["settings"], "aws.jobs[aws_cb_offering]"
            )
        )
    quota_regions = _job_regions(resolved)
    if "quota" in section:
        faults.extend(
            _validate_quota(
                section["quota"], quota_regions, "aws", AWS_REGION_RE, AWS_REGION_HINT, AWS_SIZE_RE
            )
        )
    for key in ("wake_seconds", "max_calls_per_wake", "max_throttle_attempts"):
        if f"queue_{key}" in section:
            _check_int(faults, section[f"queue_{key}"], f"aws.queue_{key}")
    jitter = section.get("queue_jitter", 0)
    if isinstance(jitter, bool) or not isinstance(jitter, int | float) or not 0 <= jitter < 1:
        faults.append("aws.queue_jitter must be a number with 0 <= jitter < 1")
    for key in ("backoff_base_seconds", "backoff_cap_seconds"):
        value = section.get(f"queue_{key}", 0)
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            faults.append(f"aws.queue_{key} must be a number >= 0")
    probes = section.get("provisioning_probes", [])
    if not isinstance(probes, list):
        faults.append("aws.provisioning_probes must be a list")
    else:
        seen_aws: dict[tuple[str, str], str] = {}
        for i, entry in enumerate(probes):
            where = f"aws.provisioning_probes[{i}]"
            if not isinstance(entry, dict):
                faults.append(f"{where} must be a mapping")
                continue
            pair = (str(entry.get("instance_type")).lower(), str(entry.get("region")).lower())
            if pair in seen_aws:
                faults.append(
                    f"{where} repeats the instance type and Region of {seen_aws[pair]}. "
                    "The probe covers every zone of the Region"
                )
            else:
                seen_aws[pair] = where
            faults.extend(_unknown_entry_key_faults(entry, AWS_PROVISIONING_ENTRY_KEYS, where))
            _check_str(faults, entry.get("instance_type"), f"{where}.instance_type")
            instance_type = entry.get("instance_type")
            if (
                isinstance(instance_type, str)
                and instance_type
                and not AWS_SIZE_RE.fullmatch(instance_type)
            ):
                faults.append(f"{where}.instance_type must be an EC2 instance type")
            _check_str(faults, entry.get("region"), f"{where}.region")
            _check_int(faults, entry.get("n"), f"{where}.n")
            _check_n_max(faults, entry, where)
            _check_optional_bool(faults, entry, "simulate", where)
            _check_optional_bool(faults, entry, "allow_billable_provisioning", where)
    for key in AWS_OPTIONAL_INT_KEYS:
        if key in section:
            _check_int(faults, section[key], f"aws.{key}")
    _validate_cadence(faults, section.get("cadence"), "aws.cadence")
    faults.extend(_aws_lane_length_faults(section))
    faults.extend(_poll_interval_faults(section, "aws"))
    return faults


def _validate_azure(section: Any) -> list[str]:
    faults: list[str] = []
    if not isinstance(section, dict):
        return ["`azure` must be a mapping"]
    resolved, job_faults = _validate_jobs(section, "azure")
    faults.extend(job_faults)
    faults.extend(_unknown_key_faults(section, "azure"))
    faults.extend(_cadence_conflict_faults(section, resolved, "azure"))
    if "az_spot_score" in resolved:
        _check_str_list(
            faults,
            resolved["az_spot_score"]["settings"].get("spot_sizes"),
            "azure.jobs[az_spot_score].spot_sizes",
        )
    if "az_spot" in resolved:
        az_spot_settings = resolved["az_spot"]["settings"]
        _check_str_list(
            faults, az_spot_settings.get("spot_sizes"), "azure.jobs[az_spot].spot_sizes"
        )
        if "spot_price_max_age_days" in az_spot_settings:
            _check_int(
                faults,
                az_spot_settings["spot_price_max_age_days"],
                "azure.jobs[az_spot].spot_price_max_age_days",
            )
    faults.extend(_validate_azure_probes(section.get("provisioning_probes", {})))
    for key in AZURE_OPTIONAL_INT_KEYS:
        if key in section:
            _check_int(faults, section[key], f"azure.{key}")
    quota_regions = _job_regions(resolved)
    if "quota" in section:
        faults.extend(
            _validate_quota(
                section["quota"],
                quota_regions,
                "azure",
                AZURE_REGION_RE,
                AZURE_REGION_HINT,
                None,
            )
        )
    _validate_cadence(faults, section.get("cadence"), "azure.cadence")
    faults.extend(_lane_length_faults(section))
    faults.extend(_poll_interval_faults(section, "azure"))
    return faults


# The Azure defaults of the timeouts and the cadence (`azure/prechecks.py` `DEFAULTS` and
# `default_cadence`; a test keeps them equal).
AZURE_DEFAULT_PROBE_TIMEOUT = 90
AZURE_DEFAULT_TEARDOWN_TIMEOUT = 60
AZURE_DEFAULT_PROVISIONING_CADENCE = "2h"
# The AWS defaults (`aws/odcr.py` `DEFAULTS` and `OdcrProbe.default_cadence`; a test keeps them
# equal).
AWS_DEFAULT_PROBE_TIMEOUT = 90
AWS_DEFAULT_TEARDOWN_TIMEOUT = 60
AWS_DEFAULT_PROVISIONING_CADENCE = "2h"
DEFAULT_POLL_INTERVAL = 5  # both clouds (`azure/prechecks.py` and `aws/odcr.py` `DEFAULTS`)


def up_steps(entry: dict[str, Any]) -> int:
    """k of ADR 0023: `ceil(log2(n_max / n))` doublings from `n` to `n_max`, or 0 with no `n_max`.
    Integer arithmetic: `ceil(log2(x))` is `(ceil(x) - 1).bit_length()`."""
    if "n_max" not in entry:
        return 0
    return (-(-int(entry["n_max"]) // int(entry["n"])) - 1).bit_length()


def _lane_length_faults(section: dict[str, Any]) -> list[str]:
    """1 fault per Region whose worst-case Azure lane is not shorter than the provisioning cadence
    (spec 0003, section 6B, rule 8). The check is strict because the `az_precheck` lane shares
    the Region lock and grid slot, and its calls are not in the sum. See `_lane_faults`."""
    return _lane_faults(
        section,
        _azure_entries(section.get("provisioning_probes", {})),
        "azure",
        "az_provisioning",
        AZURE_DEFAULT_PROVISIONING_CADENCE,
        AZURE_DEFAULT_PROBE_TIMEOUT,
        AZURE_DEFAULT_TEARDOWN_TIMEOUT,
    )


def _aws_lane_length_faults(section: dict[str, Any]) -> list[str]:
    """The same refusal for `aws_odcr` (spec 0003, section 6B, rule 8): strict, so a lane that
    equals the cadence is refused. See `_lane_faults`."""
    probes = section.get("provisioning_probes", [])
    entries = [
        (f"aws.provisioning_probes[{i}]", entry)
        for i, entry in enumerate(probes if isinstance(probes, list) else [])
        if isinstance(entry, dict)
    ]
    return _lane_faults(
        section,
        entries,
        "aws",
        "aws_odcr",
        AWS_DEFAULT_PROVISIONING_CADENCE,
        AWS_DEFAULT_PROBE_TIMEOUT,
        AWS_DEFAULT_TEARDOWN_TIMEOUT,
    )


def _lane_faults(
    section: dict[str, Any],
    entries: list[tuple[str, dict[str, Any]]],
    cloud: str,
    cadence_key: str,
    default_cadence: str,
    default_probe_timeout: int,
    default_teardown_timeout: int,
) -> list[str]:
    """1 fault per Region whose worst-case lane is not shorter than the provisioning cadence.
    The lane of a Region runs all its entries in series, each
    with the budget (1 + k) x (probe timeout + teardown timeout) of ADR 0023, section 5; a
    simulated entry counts too, so that flipping `simulate` never breaks the cadence. An entry
    or a key that other checks refuse is skipped here."""
    cadence_map = section.get("cadence")
    if cadence_map is None:
        cadence_map = {}
    if not isinstance(cadence_map, dict):
        return []  # `_validate_cadence` reports it
    cadence_text = cadence_map.get(cadence_key, default_cadence)
    try:
        cadence = parse_cadence(cadence_text)
    except ValueError:
        return []
    timeouts = []
    for key, default in (
        ("probe_timeout_seconds", default_probe_timeout),
        ("teardown_timeout_seconds", default_teardown_timeout),
    ):
        value = section.get(key, default)
        if not _is_int(value) or value < 1:
            return []
        timeouts.append(value)
    per_attempt = sum(timeouts)
    lanes: dict[str, int] = {}
    for _where, entry in entries:
        n, n_max, region = entry.get("n"), entry.get("n_max"), entry.get("region")
        if not isinstance(region, str) or not _is_int(n) or n < 1:
            continue
        if "n_max" in entry and (not _is_int(n_max) or not n < n_max <= N_MAX_LIMIT):
            continue
        lanes[region.lower()] = lanes.get(region.lower(), 0) + (1 + up_steps(entry)) * per_attempt
    return [
        f"{cloud}.provisioning_probes: the worst-case lane of Region {region} is {seconds} s"
        f" (the sum over its entries of (1 + k) x {per_attempt} s), which is not shorter than"
        f" {cloud}.cadence.{cadence_key} of {cadence} s. Cut entries from the Region, lower"
        " `n_max`, or lengthen the cadence (spec 0003, section 6B, rule 8)"
        for region, seconds in sorted(lanes.items())
        if seconds >= cadence
    ]


def _poll_interval_faults(section: dict[str, Any], provider: str) -> list[str]:
    """1 fault when `probe_timeout_seconds` is not longer than `poll_interval_seconds` (spec 0003,
    section 3). The deadline floor `max(longest probe, poll interval)` then never lets a create
    call start, so each real provisioning entry writes only a skipped `call deadline` row. The
    defaults are 90 and 5 for both clouds. A value that other checks refuse is skipped here."""
    timeout = section.get("probe_timeout_seconds", AZURE_DEFAULT_PROBE_TIMEOUT)
    poll = section.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL)
    if not _is_int(timeout) or not _is_int(poll) or timeout < 1 or poll < 1:
        return []
    if timeout > poll:
        return []
    return [
        f"{provider}.probe_timeout_seconds ({timeout}) must be longer than"
        f" {provider}.poll_interval_seconds ({poll}): no create call could start (the entry"
        " deadline runs from before the precheck), and every"
        " provisioning entry would write only a skipped `call deadline` row"
    ]


def _validate_azure_probes(probes: Any) -> list[str]:
    """The faults of `azure.provisioning_probes`: a mapping of probe type to a list of entries
    (ADR 0022). The old list shape and the `route` key get a message that names the new shape."""
    faults: list[str] = []
    if isinstance(probes, list):
        return [
            "azure.provisioning_probes is a list. It is now a mapping from probe type"
            f" ({', '.join(AZURE_PROVISIONING_PROBE_TYPES)}) to a list of entries (ADR 0022)"
        ]
    if not isinstance(probes, dict):
        return ["azure.provisioning_probes must be a mapping from probe type to a list of entries"]
    for probe_type, entries in probes.items():
        if probe_type not in AZURE_PROVISIONING_PROBE_TYPES:
            faults.append(
                f"azure.provisioning_probes.{probe_type} is not a known probe type."
                f" Use {' or '.join(AZURE_PROVISIONING_PROBE_TYPES)} (ADR 0022)"
            )
        elif not isinstance(entries, list):
            faults.append(f"azure.provisioning_probes.{probe_type} must be a list of entries")
        else:
            faults.extend(
                f"azure.provisioning_probes.{probe_type}[{i}] must be a mapping"
                for i, entry in enumerate(entries)
                if not isinstance(entry, dict)
            )
    seen: dict[tuple[str, str], str] = {}
    for where, entry in _azure_entries(probes):
        if "run_mode" in entry:
            faults.append(f"{where}.run_mode is removed (ADR 0011); use `simulate: true` instead")
        if "route" in entry:
            faults.append(
                f"{where}.route is removed (ADR 0022); the probe type that lists the entry"
                " decides what the probe does"
            )
        other_keys = {k: v for k, v in entry.items() if k not in ("run_mode", "route")}
        faults.extend(_unknown_entry_key_faults(other_keys, AZURE_PROVISIONING_ENTRY_KEYS, where))
        _check_str(faults, entry.get("sku"), f"{where}.sku")
        _check_str(faults, entry.get("region"), f"{where}.region")
        zone = entry.get("zone")
        if zone is not None and not isinstance(zone, str | int):
            faults.append(f"{where}.zone must be a string, a number, or null")
        _check_int(faults, entry.get("n"), f"{where}.n")
        _check_n_max(faults, entry, where)
        if entry.get("placement_group_mode") == "single":
            faults.append(
                f"{where}.placement_group_mode `single` is removed (ADR 0021); use `multi`"
            )
        elif entry.get("placement_group_mode") != "multi":
            faults.append(f"{where}.placement_group_mode must be `multi`")
        _check_optional_bool(faults, entry, "simulate", where)
        _check_optional_bool(faults, entry, "allow_billable_provisioning", where)
        if ".az_vmss_flex[" in where and all(
            isinstance(entry.get(k), str) for k in ("sku", "region")
        ):
            key = (entry["sku"].lower(), entry["region"].lower())
            if key in seen:
                faults.append(
                    f"{where} repeats the size and Region of {seen[key]}. Use one az_vmss_flex"
                    " entry per size and Region: the scale set is selected by its tags (ADR 0022)"
                )
            else:
                seen[key] = where
    return faults


def _unknown_key_faults(section: dict[str, Any], provider: str) -> list[str]:
    """Refuse a cloud-section key outside `KNOWN_CLOUD_KEYS`, instead of silently ignoring it
    (review finding: a key of the earlier, flat schema must not go dark with no error). The
    message echoes the key as the file gave it."""
    return [
        f"{provider}.{key} is not a known key. See spec 0003 section 3 for the file shape."
        + (f" {RENAMED_KEYS[key]}" if key in RENAMED_KEYS else "")
        for key in section
        if key not in KNOWN_CLOUD_KEYS[provider]
    ]


# Keys that an earlier version of the schema accepted. The unknown-key fault names the new key
# (ADR 0016).
RENAMED_KEYS = {
    "allow_allocation": "`allow_allocation` is now `allow_billable_provisioning` (ADR 0016).",
    "allocation_probes": "`allocation_probes` is now `provisioning_probes`.",
    "az_allocation": "`az_allocation` is now `az_provisioning`.",
}


def _unknown_entry_key_faults(
    entry: dict[str, Any], allowed: frozenset[str], where: str
) -> list[str]:
    """Refuse a key of a list entry (a job, a quota size, a provisioning probe, a Spot score
    request)
    outside `allowed`, the same way `_unknown_key_faults` refuses one in a cloud section: a
    key this schema does not use must not be silently ignored. The message echoes the key as
    the file gave it."""
    return [
        f"{where}.{key} is not a known key. See spec 0003 section 3 for the file shape."
        + (f" {RENAMED_KEYS[key]}" if key in RENAMED_KEYS else "")
        for key in entry
        if key not in allowed
    ]


def _cadence_conflict_faults(
    section: dict[str, Any], resolved: dict[str, dict[str, Any]], provider: str
) -> list[str]:
    """A job-eligible probe type's cadence is its job's `interval`, never a `*.cadence` entry,
    whether or not that job is actually listed."""
    cadence = section.get("cadence")
    if not isinstance(cadence, dict):
        return []
    faults = []
    for probe_type in MODULE_DEFAULTS:
        if PROVIDER_OF_JOB_TYPE[probe_type] != provider or probe_type not in cadence:
            continue
        if probe_type in resolved:
            faults.append(
                f"{provider}.cadence.{probe_type} is set, but {provider}.jobs[{probe_type}] "
                "already has its own `interval`; remove 1 of the 2"
            )
        else:
            faults.append(
                f"{provider}.cadence.{probe_type} is set, but {probe_type} is job-eligible: "
                "its cadence is its job's `interval`, not a `*.cadence` entry"
            )
    return faults


def _job_regions(resolved: dict[str, dict[str, Any]]) -> list[str]:
    """The union of the `regions` of every resolved job, in first-seen order."""
    regions: list[str] = []
    for job in resolved.values():
        for region in job["regions"]:
            if region not in regions:
                regions.append(region)
    return regions


def _validate_jobs(
    section: dict[str, Any], provider: str
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Validate `modules:` and `jobs:`. Return the resolved jobs (by probe type) and the faults.

    A resolved job is `{"regions": [...], "interval": str | None, "settings": {...}}`, the
    settings being the probe type's code default merged with the module override at the top
    level only (ADR 0010 section 3): a key the override sets replaces the whole default value
    for that key; a key it omits keeps the default.
    """
    faults: list[str] = []
    module_defs: dict[str, tuple[str, dict[str, Any]]] = {}
    modules = section.get("modules")
    if modules is not None:
        if not isinstance(modules, dict):
            faults.append(f"{provider}.modules must be a mapping")
            modules = {}
        for name, body in modules.items():
            where = f"{provider}.modules.{name}"
            if not isinstance(body, dict):
                faults.append(f"{where} must be a mapping")
                continue
            probe_type = body.get("type")
            if not (
                isinstance(probe_type, str)
                and probe_type in MODULE_DEFAULTS
                and PROVIDER_OF_JOB_TYPE[probe_type] == provider
            ):
                faults.append(f"{where}.type must be a known {provider} probe type")
                continue
            overrides = {k: v for k, v in body.items() if k != "type"}
            allowed = ALLOWED_SETTING_KEYS[probe_type]
            for key in overrides:
                if key not in allowed:
                    faults.append(
                        f"{where}.{key} is not a setting of {probe_type}; the settings are "
                        f"{', '.join(sorted(allowed)) or '(none)'}"
                    )
            module_defs[name] = (probe_type, overrides)
    jobs = section.get("jobs", [])
    if not isinstance(jobs, list):
        faults.append(f"{provider}.jobs must be a list")
        jobs = []
    resolved: dict[str, dict[str, Any]] = {}
    for i, job in enumerate(jobs):
        where = f"{provider}.jobs[{i}]"
        if not isinstance(job, dict):
            faults.append(f"{where} must be a mapping")
            continue
        faults.extend(_unknown_entry_key_faults(job, JOB_ENTRY_KEYS, where))
        name = job.get("module")
        if not isinstance(name, str) or not name:
            faults.append(f"{where}.module must be a non-empty string")
            continue
        if name in module_defs:
            probe_type, overrides = module_defs[name]
        elif isinstance(modules, dict) and name in modules:
            # `name` is a `*.modules` entry, but its own `type` was already refused above;
            # skip this job instead of falling through to a built-in type of the same name,
            # so only 1 fault (the module's) is reported, not a 2nd, misleading one here.
            continue
        elif name in MODULE_DEFAULTS and PROVIDER_OF_JOB_TYPE[name] == provider:
            probe_type, overrides = name, {}
        else:
            faults.append(
                f"{where}.module {name!r} is not a known module in `{provider}.modules` "
                f"or a built-in {provider} probe type"
            )
            continue
        _check_str_list(faults, job.get("regions"), f"{where}.regions")
        interval = job.get("interval")
        if interval is not None:
            try:
                parse_cadence(interval)
            except ValueError as exc:
                faults.append(f"{where}.interval: {exc}")
        if probe_type in resolved:
            faults.append(f"{provider}.jobs: probe type {probe_type!r} has more than 1 job")
            continue
        _check_optional_bool(faults, job, "simulate", where)
        _check_optional_bool(faults, job, "allow_billable_provisioning", where)
        allowed = ALLOWED_SETTING_KEYS[probe_type]
        safe_overrides = {k: v for k, v in overrides.items() if k in allowed}
        resolved[probe_type] = {
            "regions": job["regions"] if isinstance(job.get("regions"), list) else [],
            "interval": interval if isinstance(interval, str) else None,
            "settings": {**MODULE_DEFAULTS[probe_type], **safe_overrides},
            # ADR 0011 section 2: a job's own `simulate: true` key. The run-wide `--simulate`
            # flag ORs with this at run time (`Scheduler.job_simulated`); `check-config` and
            # `sim` read only this key (plus `force_simulate` for the allocation guard).
            "simulate": job.get("simulate") is True,
        }
    faults.extend(_dependency_faults(resolved, provider))
    faults.extend(_shared_key_faults(resolved, provider))
    return resolved, faults


def _dependency_faults(resolved: dict[str, dict[str, Any]], provider: str) -> list[str]:
    """A job that reads another job's settings (`JOB_DEPENDENCIES`) needs that job too."""
    faults = []
    for probe_type, needed in JOB_DEPENDENCIES.items():
        if probe_type in resolved and needed not in resolved:
            faults.append(
                f"{provider}.jobs: {probe_type} needs a {needed} job too, for its settings"
            )
    return faults


def _shared_key_faults(resolved: dict[str, dict[str, Any]], provider: str) -> list[str]:
    """This build keeps 1 shared `regions` list and 1 shared `spot_sizes` list per cloud
    (spec 0003 section 3): jobs of 1 provider must agree, instead of silently picking a winner.
    """
    faults: list[str] = []
    region_sets = {tuple(job["regions"]) for job in resolved.values()}
    if len(region_sets) > 1:
        faults.append(
            f"{provider}.jobs: every job of {provider} must use the same `regions` list in "
            "this build (1 shared, flat `regions` list per cloud)"
        )
    if provider == "azure" and "az_spot_score" in resolved and "az_spot" in resolved:
        a = resolved["az_spot_score"]["settings"].get("spot_sizes")
        b = resolved["az_spot"]["settings"].get("spot_sizes")
        if a != b:
            faults.append(
                "azure.jobs: az_spot_score and az_spot must have the same spot_sizes in this "
                "build (1 shared, flat `spot_sizes` list)"
            )
    return faults


def _resolved_section(raw: Any, provider: str) -> dict[str, Any] | None:
    """Build the legacy-shaped section that the scheduler and the probes already read.

    `validate` has already rejected a bad shape, so this trusts `raw`. It resolves `modules:`
    and `jobs:` (ADR 0010 section 1) and promotes the result onto the flat keys of the earlier
    schema (spec 0003 section 3's "old key, new place" table): `regions` becomes every job's
    (identical, by validation) `regions` list; `cadence` gains 1 entry per job with its
    `interval`; the `aws_cb_offering` settings go back under `capacity_blocks`; every other
    setting (`spot_score_requests`, `spot_sizes`, `spot_price_max_age_days`) is a flat key
    already. `provisioning_probes` and `quota` are untouched: ADR 0010 section 10, item 4 keeps
    them out of scope here.
    """
    if not isinstance(raw, dict):
        return None
    resolved, _faults = _validate_jobs(raw, provider)
    out = {k: v for k, v in raw.items() if k not in ("modules", "jobs")}
    cadence = dict(out.get("cadence") or {})
    regions = _job_regions(resolved)
    if regions:
        out["regions"] = regions
    for probe_type, job in resolved.items():
        if job["interval"]:
            cadence[probe_type] = job["interval"]
        if probe_type == "aws_cb_offering":
            out["capacity_blocks"] = dict(job["settings"])
        else:
            out.update(job["settings"])
    if cadence:
        out["cadence"] = cadence
    if provider == "azure" and "provisioning_probes" in out:
        # One list for the lanes and the probes; each entry knows its probe type (ADR 0022).
        out["provisioning_probes"] = [
            {**entry, "probe_type": probe_type}
            for probe_type, entries in out["provisioning_probes"].items()
            for entry in entries
        ]
    return out


def _validate_spot_score_requests(requests: Any, where: str) -> list[str]:
    faults: list[str] = []
    if not isinstance(requests, list) or not requests:
        faults.append(f"{where} must be a non-empty list")
        return faults
    if len(requests) > MAX_SPOT_SCORE_REQUESTS:
        faults.append(
            f"{where} has {len(requests)} entries; the limit is {MAX_SPOT_SCORE_REQUESTS}"
        )
    names = set()
    single_types: set[str] = set()
    for i, entry in enumerate(requests):
        entry_where = f"{where}[{i}]"
        if not isinstance(entry, dict):
            faults.append(f"{entry_where} must be a mapping")
            continue
        faults.extend(_unknown_entry_key_faults(entry, SPOT_SCORE_REQUEST_KEYS, entry_where))
        _check_str(faults, entry.get("name"), f"{entry_where}.name")
        _check_str_list(faults, entry.get("instance_types"), f"{entry_where}.instance_types")
        types = entry.get("instance_types")
        if isinstance(types, list) and len(types) == 1 and isinstance(types[0], str):
            if types[0] in single_types:  # the controls map each type to one single
                faults.append(
                    f"{entry_where}.instance_types: single type {types[0]!r} is used twice"
                )
            single_types.add(types[0])
        _check_str(faults, entry.get("label"), f"{entry_where}.label")
        if entry.get("name") in names:
            faults.append(f"{entry_where}.name {entry.get('name')!r} is used twice")
        names.add(entry.get("name"))
    return faults


def _validate_capacity_blocks(blocks: dict[str, Any], where: str) -> list[str]:
    faults: list[str] = []
    _check_str_list(faults, blocks.get("instance_types"), f"{where}.instance_types")
    _check_int(faults, blocks.get("instance_count"), f"{where}.instance_count")
    hours = blocks.get("duration_hours")
    if not isinstance(hours, list) or not hours or not all(_is_int(h) and h > 0 for h in hours):
        faults.append(f"{where}.duration_hours must be a non-empty list of integers > 0")
    _check_int(faults, blocks.get("start_window_days"), f"{where}.start_window_days")
    earliest = blocks.get("earliest_duration_hours", hours)
    if not isinstance(earliest, list) or not all(_is_int(h) and h > 0 for h in earliest):
        faults.append(f"{where}.earliest_duration_hours must be a list of integers > 0")
    types = blocks.get("instance_types")
    pairs = _check_instance_counts(faults, blocks.get("instance_counts"), types, where)
    if isinstance(types, list) and isinstance(hours, list) and isinstance(earliest, list):
        if pairs is None:
            pairs = len(types)
        calls = pairs * (len(hours) + len(earliest) + MAX_CB_LONGEST_CALLS)
        if calls > MAX_CB_CALLS_PER_REGION:
            faults.append(
                f"{where} makes {calls} worst-case calls per Region "
                f"((instance_types, instance count) pairs x (duration_hours + "
                f"earliest_duration_hours + {MAX_CB_LONGEST_CALLS})); the limit is "
                f"{MAX_CB_CALLS_PER_REGION}"
            )
    return faults


def _check_instance_counts(faults: list[str], counts: Any, types: Any, where: str) -> int | None:
    """Check the optional `instance_counts` and return the number of (type, count) pairs, or None
    when the pairs cannot be counted (no map, or a bad `instance_types` or `instance_counts`)."""
    if counts is None:
        return None
    if not isinstance(counts, dict):
        faults.append(f"{where}.instance_counts must be a mapping of instance type to a list")
        return None
    if not isinstance(types, list) or not all(isinstance(t, str) for t in types):
        return None
    ok = True
    for name, values in counts.items():
        here = f"{where}.instance_counts[{name}]"
        if name not in types:
            faults.append(f"{here}: {name!r} is not in instance_types")
            ok = False
        if (
            not isinstance(values, list)
            or not values
            or not all(_is_int(n) and n > 0 for n in values)
        ):
            faults.append(f"{here} must be a non-empty list of integers of 1 or more")
            ok = False
        elif len(set(values)) != len(values):
            faults.append(f"{here} lists a count twice")
            ok = False
    if not ok:
        return None
    return sum(len(counts.get(t, [None])) for t in types)


def _validate_quota(
    quota: Any,
    cloud_regions: Any,
    cloud: str,
    region_re: re.Pattern[str],
    region_hint: str,
    size_re: re.Pattern[str] | None,
) -> list[str]:
    """The `aws.quota` and `azure.quota` blocks: one function, one field table (spec 0003)."""
    top = f"{cloud}.quota"
    if not isinstance(quota, dict):
        return [f"{top} must be a mapping"]
    faults: list[str] = []
    regions = quota.get("regions")
    _check_str_list(faults, regions, f"{top}.regions")
    known = cloud_regions if isinstance(cloud_regions, list) else []
    quota_regions = regions if isinstance(regions, list) else []
    for region in quota_regions:
        if isinstance(region, str) and region not in known:
            faults.append(f"{top}.regions: {region!r} is not in the regions of any {cloud} job")
    _check_unique(faults, quota_regions, f"{top}.regions")
    _check_canonical(faults, quota_regions, f"{top}.regions", region_re, region_hint)
    sizes = quota.get("sizes")
    if not isinstance(sizes, list) or not sizes:
        faults.append(f"{top}.sizes must be a non-empty list")
        return faults
    seen = set()
    for i, entry in enumerate(sizes):
        where = f"{top}.sizes[{i}]"
        if not isinstance(entry, dict):
            faults.append(f"{where} must be a mapping")
            continue
        faults.extend(_unknown_entry_key_faults(entry, QUOTA_SIZE_ENTRY_KEYS, where))
        size = entry.get("size")
        _check_str(faults, size, f"{where}.size")
        if isinstance(size, str) and size.lower() in seen:
            faults.append(f"{where}.size {size!r} is used twice")
        if isinstance(size, str):
            seen.add(size.lower())
            if size and size_re and not size_re.fullmatch(size):
                faults.append(f"{where}.size {size!r} must be an EC2 instance type")
        _check_int(faults, entry.get("count"), f"{where}.count")
        if "regions" in entry:
            _check_str_list(faults, entry["regions"], f"{where}.regions")
            for region in entry["regions"] if isinstance(entry["regions"], list) else []:
                if isinstance(region, str) and region not in quota_regions:
                    faults.append(f"{where}.regions: {region!r} is not in {top}.regions")
            if isinstance(entry["regions"], list):
                _check_unique(faults, entry["regions"], f"{where}.regions")
                _check_canonical(
                    faults, entry["regions"], f"{where}.regions", region_re, region_hint
                )
    return faults


def _check_unique(faults: list[str], values: list[Any], where: str) -> None:
    """Report each string that is in the list a second time, case-insensitive."""
    seen = set()
    for value in values:
        if isinstance(value, str):
            if value.lower() in seen:
                faults.append(f"{where}: {value!r} is used twice")
            seen.add(value.lower())


def _check_canonical(
    faults: list[str], values: list[Any], where: str, pattern: re.Pattern[str], hint: str
) -> None:
    for value in values:
        if isinstance(value, str) and value and not pattern.fullmatch(value):
            faults.append(f"{where}: {value!r} must be {hint}")


def _validate_cadence(faults: list[str], cadence: Any, where: str) -> None:
    if cadence is None:
        return
    if not isinstance(cadence, dict):
        faults.append(f"{where} must be a mapping of probe type to cadence")
        return
    for key, value in cadence.items():
        if key in RENAMED_KEYS:
            faults.append(f"{where}.{key} is not a known key. {RENAMED_KEYS[key]}")
            continue
        try:
            parse_cadence(value)
        except ValueError as exc:
            faults.append(f"{where}.{key}: {exc}")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_str(faults: list[str], value: Any, where: str) -> None:
    if not isinstance(value, str) or not value:
        faults.append(f"{where} must be a non-empty string")


def _check_int(faults: list[str], value: Any, where: str) -> None:
    if not _is_int(value) or value < 1:
        faults.append(f"{where} must be an integer of 1 or more")


def _check_n_max(faults: list[str], entry: dict[str, Any], where: str) -> None:
    """The optional `n_max` of a provisioning entry: an integer above `n`, at most `N_MAX_LIMIT`
    (ADR 0023). A bad `n` has its own fault."""
    if "n_max" not in entry:
        return
    value = entry["n_max"]
    if not _is_int(value) or value > N_MAX_LIMIT:
        faults.append(f"{where}.n_max must be an integer of at most {N_MAX_LIMIT}")
    elif _is_int(entry.get("n")) and value <= entry["n"]:
        faults.append(f"{where}.n_max must be greater than n")


def _check_str_list(faults: list[str], value: Any, where: str) -> None:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        faults.append(f"{where} must be a non-empty list of strings")


def _check_optional_bool(faults: list[str], entry: dict[str, Any], key: str, where: str) -> None:
    """A key that defaults to `false` when absent: `simulate`, `allow_billable_provisioning`."""
    if key in entry and not isinstance(entry[key], bool):
        faults.append(f"{where}.{key} must be true or false")
