"""`python -m ftg_preflight.quota`: request Azure GPU quota from `azure.quota` (spec 0011).

`python -m ftg_preflight.quota status` lists the recent requests (ftg_preflight/quota_status.py).

The default is a dry run: the command reads the quotas and prints the requests. `--apply` sends
them with the `quota` extension of the az CLI. Pass 1 sends the regional `cores` requests.
Pass 2 sends the VM family requests. Pass 3 sends the regional Spot request (`lowPriorityCores`).
"""

from __future__ import annotations

import argparse
import math
import os
import re
import shlex
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ftg_preflight.azure import AzError, az_cli, capability, find_sku, restriction, sku_rows

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
INSTALL = "az extension add --name quota"
CORES = "cores"
# The regional Spot quota (spec 0011 section 5). It is a separate pool from the family quotas.
SPOT = "lowPriorityCores"
# The regional cores target is rounded up to a multiple of this number.
CORES_STEP = 100
# Region and size names of a quota block. Spec 0003 section 3. The prober package has the same
# patterns in ftg_prober/config.py; the 2 packages share no code.
REGION_RE = {
    "azure": (re.compile(r"^[a-z0-9]+$"), "lowercase letters and digits only"),
    "aws": (re.compile(r"^[a-z]{2}(-[a-z]+)+-[0-9]+$"), "a Region code such as us-east-1"),
}
AWS_SIZE_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)?\.[a-z0-9]+$")

Az = Callable[..., Any]


@dataclass(frozen=True)
class SizeAsk:
    size: str
    count: int
    regions: tuple[str, ...]


@dataclass
class Ask:
    """1 quota request: the resource `name` in `region` must reach `target`."""

    region: str
    name: str
    target: int
    note: str
    limit: int | None = None
    error: str = ""

    @property
    def needed(self) -> bool:
        return not self.error and self.limit is not None and self.limit < self.target


@dataclass
class RegionPlan:
    region: str
    skips: list[str] = field(default_factory=list)
    cores: Ask | None = None
    families: list[Ask] = field(default_factory=list)
    spot: Ask | None = None
    error: str = ""


def _job_regions(section: dict[str, Any]) -> list[str]:
    """The union of the `regions` of every job, in first-seen order (ADR 0010 section 1)."""
    regions: list[str] = []
    for job in section.get("jobs") or []:
        for region in (job or {}).get("regions") or []:
            if region not in regions:
                regions.append(region)
    return regions


def load_quota(path: Path, cloud: str = "azure") -> tuple[list[str], list[SizeAsk]]:
    """The regions and the sizes of `<cloud>.quota`. Raise ValueError when the section is bad.

    The checks are the rules of spec 0011 section 3, for both clouds (spec 0003 section 3).
    `ftg-prober check-config` has the same rules in the prober package.
    """
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    azure = data.get(cloud) or {}
    quota = azure.get("quota")
    if not isinstance(quota, dict):
        raise ValueError(f"{path} has no {cloud}.quota section")
    try:
        regions = _str_list(quota["regions"])
        unknown = sorted(set(regions) - set(_job_regions(azure)))
        if unknown:
            raise ValueError(f"{', '.join(unknown)} not in the regions of any {cloud} job")
        if not isinstance(quota["sizes"], list) or not quota["sizes"]:
            raise ValueError("sizes must be a non-empty list")
        sizes = [
            SizeAsk(s["size"], s["count"], tuple(_str_list(s.get("regions", regions))))
            for s in quota["sizes"]
        ]
        names = [s.size for s in sizes]
        if not all(isinstance(n, str) and n for n in names) or _has_duplicate(names):
            raise ValueError("each size must be a unique non-empty string")
        all_regions = regions + [r for s in sizes for r in s.regions]
        region_re, hint = REGION_RE[cloud]
        if not all(region_re.fullmatch(r) for r in all_regions):
            raise ValueError(f"a region name must be {hint}")
        if cloud == "aws" and not all(AWS_SIZE_RE.fullmatch(n) for n in names):
            raise ValueError("each size must be an EC2 instance type")
        if _has_duplicate(regions) or any(_has_duplicate(list(s.regions)) for s in sizes):
            raise ValueError("a region is listed twice")
        if not all(_is_count(s.count) and set(s.regions) <= set(regions) for s in sizes):
            raise ValueError("a count is under 1, or a size region is not a quota region")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"{cloud}.quota in {path} is not valid ({exc}); run make prober/check-config"
        ) from exc
    return regions, sizes


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        raise ValueError(f"{value!r} must be a non-empty list of non-empty strings")
    return value


def _has_duplicate(values: list[str]) -> bool:
    lowered = [v.lower() for v in values]
    return len(set(lowered)) != len(lowered)


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def scope(subscription: str, region: str) -> str:
    return f"/subscriptions/{subscription}/providers/Microsoft.Compute/locations/{region}"


def retrying(az: Az, sleep: Callable[[float], None] = time.sleep) -> Az:
    """Wrap az: retry a call once, after 2 s, when az failed on the shared MSAL token cache.

    Parallel az processes race on that cache, so the commands call az one at a time. The race is
    rare, and a second call works.
    """

    def call(*args: str) -> Any:
        try:
            return az(*args)
        except AzError as exc:
            if "MSAL token cache" not in str(exc):
                raise
            sleep(2)
            return az(*args)

    return call


def quota_limit(az: Az, subscription: str, region: str, name: str) -> int:
    reply = az("quota", "show", "--scope", scope(subscription, region), "--resource-name", name)
    return int(reply["properties"]["limit"]["value"])


def quota_usage(az: Az, subscription: str, region: str, name: str) -> int:
    reply = az(
        "quota", "usage", "show", "--scope", scope(subscription, region), "--resource-name", name
    )
    return int(reply["properties"]["usages"]["value"])


def request_args(subscription: str, ask: Ask) -> list[str]:
    """The arguments of the `az quota update` call for 1 request."""
    # `--resource-type` is optional. The Spot quota of Microsoft.Compute reports no resource
    # type, so its request sends none (spec 0011, section 2).
    resource_type = [] if ask.name == SPOT else ["--resource-type", "dedicated"]
    return [
        "quota",
        "update",
        "--scope",
        scope(subscription, ask.region),
        "--resource-name",
        ask.name,
        *resource_type,
        "--limit-object",
        f"value={ask.target}",
    ]


def cores_target(usage: int, family_vcpus: int) -> int:
    """Current usage plus the family asks, rounded up to the next multiple of CORES_STEP."""
    return math.ceil((usage + family_vcpus) / CORES_STEP) * CORES_STEP


def _read(az: Az, subscription: str, ask: Ask, read: Callable[..., int]) -> None:
    try:
        ask.limit = read(az, subscription, ask.region, ask.name)
    except (AzError, KeyError, TypeError, ValueError) as exc:
        ask.error = f"cannot read the quota ({exc})"


def plan_region(az: Az, subscription: str, region: str, sizes: list[SizeAsk]) -> RegionPlan:
    """Read the SKUs and the quotas of 1 region, and make its requests."""
    plan = RegionPlan(region)
    try:
        skus = sku_rows(az, subscription, region)
    except AzError as exc:
        plan.error = f"cannot read the SKU list ({exc})"
        return plan
    vcpus_by_family: dict[str, int] = {}
    notes: dict[str, list[str]] = {}
    for s in sizes:
        if region not in s.regions:
            continue
        sku = find_sku(skus, s.size)
        reason = restriction(sku, region)
        if reason is not None:
            plan.skips.append(f"{s.size}: {reason}")
            continue
        family = sku.get("family")
        if not family:
            plan.skips.append(f"{s.size}: no family in the SKU list")
            continue
        vcpus = int(capability(sku, "vCPUs") or 0)
        vcpus_by_family[family] = vcpus_by_family.get(family, 0) + s.count * vcpus
        notes.setdefault(family, []).append(f"{s.count} x {s.size} ({vcpus} vCPUs)")
    for family, vcpus in vcpus_by_family.items():
        ask = Ask(region, family, vcpus, ", ".join(notes[family]))
        _read(az, subscription, ask, quota_limit)
        plan.families.append(ask)
    if plan.families:
        total = sum(vcpus_by_family.values())
        spot = Ask(region, SPOT, total, f"{total} vCPUs of all sizes of the region")
        _read(az, subscription, spot, quota_limit)
        plan.spot = spot
        cores = Ask(region, CORES, 0, "")
        try:
            usage = quota_usage(az, subscription, region, CORES)
        except (AzError, KeyError, TypeError, ValueError) as exc:
            cores.error = f"cannot read the usage ({exc})"
        else:
            cores.target = cores_target(usage, total)
            cores.note = f"usage {usage} + family asks {total}, rounded up to {CORES_STEP}"
            _read(az, subscription, cores, quota_limit)
        plan.cores = cores
    return plan


def vnet_regions(az: Az, subscription: str) -> set[str]:
    return {str(loc).lower() for loc in az("network", "vnet", "list", "--subscription",
            subscription, "--query", "[].location")}  # fmt: skip


def check_extension(az: Az, install: bool, name: str = "quota") -> str:
    """An error message when the az CLI extension `name` is missing, else an empty string."""
    command = f"az extension add --name {name}"
    try:
        az("extension", "show", "--name", name)
        return ""
    except AzError as exc:
        if not install:
            return (
                f"the {name} extension of the az CLI is not installed ({exc}).\n"
                f"Install it with: {command}\n"
                "Or run this command again with --install-extension."
            )
    print(f"# installing the {name} extension: {command}", file=sys.stderr)
    try:
        az("extension", "add", "--name", name)
    except AzError as exc:
        return f"cannot install the {name} extension ({exc}). Run: {command}"
    return ""


def subscription_id(az: Az, flag: str | None) -> str:
    sub = flag or os.environ.get("AZURE_SUBSCRIPTION_ID") or os.environ.get("ARM_SUBSCRIPTION_ID")
    return sub or str(az("account", "show")["id"])


def describe(ask: Ask) -> str:
    head = f"# {ask.region} {ask.name!r}"
    if ask.error:
        return f"{head}: {ask.error}; skip"
    if not ask.needed:
        return f"{head}: limit {ask.limit} >= target {ask.target}; skip"
    return f"{head}: limit {ask.limit}, ask {ask.target} ({ask.note})"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ftg_preflight.quota",
        description="Request Azure GPU quota from azure.quota in the targets file. "
        "The default is a dry run. `status --help` shows the read-only status command.",
    )
    parser.add_argument("--config", required=True, help="the targets file (config/targets.yaml)")
    parser.add_argument("--subscription", help="default: AZURE_SUBSCRIPTION_ID, "
                        "ARM_SUBSCRIPTION_ID, or az account show")  # fmt: skip
    parser.add_argument("--apply", action="store_true", help="send the requests")
    parser.add_argument(
        "--require-vnet", action="store_true", help="skip a region with no virtual network"
    )
    parser.add_argument(
        "--install-extension", action="store_true", help=f"run `{INSTALL}` when it is missing"
    )
    return parser


def main(argv: Sequence[str] | None = None, az: Az = az_cli) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["status"]:
        from ftg_preflight import quota_status

        return quota_status.main(argv[1:], az=az)
    az = retrying(az)
    args = build_parser().parse_args(argv)
    try:
        regions, sizes = load_quota(Path(args.config))
    except (OSError, yaml.YAMLError, ValueError) as exc:
        print(f"quota: {exc}", file=sys.stderr)
        return EXIT_USAGE
    message = check_extension(az, args.install_extension)
    if message:
        print(f"quota: {message}", file=sys.stderr)
        return EXIT_USAGE
    try:
        sub = subscription_id(az, args.subscription)
    except (AzError, KeyError, TypeError, AttributeError) as exc:
        print(f"quota: a read call failed ({exc}); check the az login", file=sys.stderr)
        return EXIT_USAGE
    mode = "apply: sends the requests" if args.apply else "dry run: add --apply to send"
    print(f"# Azure quota requests, subscription {sub} ({mode})")
    failed = 0
    try:
        with_vnet = vnet_regions(az, sub)
    except (AzError, TypeError, AttributeError) as exc:
        if args.require_vnet:
            print(f"quota: cannot read the virtual networks ({exc})", file=sys.stderr)
            return EXIT_USAGE
        print(f"# warning: cannot read the virtual networks ({exc}); no VNet check")
        failed += 1
        with_vnet = {r.lower() for r in regions}
    no_vnet = [r for r in regions if r.lower() not in with_vnet]
    for r in no_vnet:
        if args.require_vnet:
            print(f"# {r}: no virtual network in the subscription; skip (--require-vnet)")
        else:
            print(f"# warning: no VNet in {r}; provision one before probes run")
    ready = [r for r in regions if not (args.require_vnet and r in no_vnet)]

    def safe_plan(region: str) -> RegionPlan:
        try:
            return plan_region(az, sub, region, sizes)
        except (AzError, KeyError, TypeError, ValueError, AttributeError, IndexError) as exc:
            return RegionPlan(region, error=f"cannot read the SKU list or a quota ({exc!r})")

    plans = [safe_plan(r) for r in ready]

    for plan in plans:
        if plan.error:
            print(f"# {plan.region}: {plan.error}; skip")
            failed += 1
        for skip in plan.skips:
            print(f"# {plan.region} {skip}; skip")
    sent = 0
    # Regions whose families must not be requested: the reason, by region.
    cores_blocked: dict[str, str] = {}
    # (region, resource) pairs that this run printed or sent. A pair goes out only 1 time.
    done: set[tuple[str, str]] = set()

    def send(ask: Ask) -> None:
        nonlocal failed, sent
        print(describe(ask))
        if ask.error:
            failed += 1
            if ask.name == CORES:
                cores_blocked[ask.region] = "the cores state is unknown"
            return
        if not ask.needed:
            return
        key = (ask.region.lower(), ask.name.lower())
        if key in done:
            print(f"# {ask.region} {ask.name!r}: already requested in this run; skip")
            return
        done.add(key)
        call = request_args(sub, ask)
        print(shlex.join(["az", *call]))
        if not args.apply:
            sent += 1
            return
        try:
            az(*call)
            sent += 1
            print(f"# {ask.region} {ask.name!r}: sent")
        except AzError as exc:
            failed += 1
            print(f"# {ask.region} {ask.name!r}: FAILED ({exc})")
            if ask.name == CORES:
                cores_blocked[ask.region] = "the cores request failed"

    print("## Pass 1: regional vCPU caps (cores)")
    for plan in plans:
        if plan.cores is not None:
            send(plan.cores)
    print("## Pass 2: VM families")
    for plan in plans:
        for ask in plan.families:
            if plan.region in cores_blocked:
                print(f"# {plan.region} {ask.name!r}: {cores_blocked[plan.region]}; skip")
                continue
            send(ask)

    print("## Pass 3: regional Spot vCPUs (lowPriorityCores)")
    for plan in plans:
        if plan.spot is not None:
            send(plan.spot)

    verb = "sent" if args.apply else "to send"
    print(f"## {sent} requests {verb}, {failed} errors.", end="")
    print(f" Regions with no VNet: {', '.join(no_vnet)}" if no_vnet else "")
    return EXIT_FAILED if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
