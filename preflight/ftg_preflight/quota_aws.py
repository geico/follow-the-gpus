"""`python -m ftg_preflight.quota_aws`: request AWS GPU quota from `aws.quota` (spec 0012).

The default is a dry run: the command reads the quotas and prints the requests. `--apply` sends
them with Service Quotas `RequestServiceQuotaIncrease`. Each quota group (P, or G and VT) gets
an On-Demand request and a Spot request. The load, the plan lines, and the Ask type are the
Azure command's (ftg_preflight/quota.py).
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import yaml
from botocore.exceptions import BotoCoreError, ClientError

from ftg_preflight.aws import quota_value
from ftg_preflight.quota import EXIT_FAILED, EXIT_OK, EXIT_USAGE, Ask, describe, load_quota

# Quota group -> (On-Demand code and name, Spot code and name). Sources: spec 0012 section 2.
GROUPS: dict[str, tuple[tuple[str, str], tuple[str, str]]] = {
    "P": (
        ("L-417A185B", "Running On-Demand P instances"),
        ("L-7212CCBC", "All P Spot Instance Requests"),
    ),
    "G and VT": (
        ("L-DB2E81BA", "Running On-Demand G and VT instances"),
        ("L-3819A6DF", "All G and VT Spot Instance Requests"),
    ),
}
# The letters and the generation at the start of an instance type: `p5`, `g6e`, `vt1`.
_GROUP_RE = ((re.compile(r"^p\d"), "P"), (re.compile(r"^(g|vt)\d"), "G and VT"))

Clients = Callable[[str, str], Any]


def quota_group(instance_type: str) -> str:
    """The quota group of an instance type. Raise ValueError for a type outside P, G, and VT."""
    for pattern, group in _GROUP_RE:
        if pattern.match(instance_type):
            return group
    raise ValueError(f"{instance_type}: no GPU quota group (this command knows P, G, and VT)")


def request_args(region: str, ask: Ask) -> list[str]:
    """The `aws` CLI form of 1 request, for the dry-run output."""
    return [
        "aws", "service-quotas", "request-service-quota-increase", "--service-code", "ec2",
        "--quota-code", ask.name, "--desired-value", str(ask.target), "--region", region,
    ]  # fmt: skip


def plan_region(
    ec2: Any, quotas: Any, region: str, sizes: list[Any]
) -> tuple[list[Ask], list[str]]:
    """The requests of 1 region, and the types that it cannot plan (a message for each)."""
    vcpus: dict[str, int] = {}
    notes: dict[str, list[str]] = {}
    problems: list[str] = []
    for s in sizes:
        if region not in s.regions:
            continue
        try:
            group = quota_group(s.size)
            info = ec2.describe_instance_types(InstanceTypes=[s.size])["InstanceTypes"][0]
            n = int(info["VCpuInfo"]["DefaultVCpus"])
        except ValueError as exc:
            problems.append(str(exc))
            continue
        except (ClientError, BotoCoreError, KeyError, IndexError, TypeError) as exc:
            problems.append(f"{s.size}: cannot read the vCPUs ({exc})")
            continue
        vcpus[group] = vcpus.get(group, 0) + s.count * n
        notes.setdefault(group, []).append(f"{s.count} x {s.size} ({n} vCPUs)")
    asks = []
    for group, total in vcpus.items():
        for code, name in GROUPS[group]:
            ask = Ask(region, code, total, f"{name}: {', '.join(notes[group])}")
            try:
                ask.limit = int(quota_value(quotas, code))
            except (ClientError, BotoCoreError, KeyError, TypeError, ValueError) as exc:
                ask.error = f"cannot read the quota ({exc})"
            asks.append(ask)
    return asks, problems


def default_clients(service: str, region: str) -> Any:
    import boto3

    return boto3.Session().client(service, region_name=region)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ftg_preflight.quota_aws",
        description="Request AWS GPU quota (On-Demand and Spot) from aws.quota in the targets "
        "file. The default is a dry run.",
    )
    parser.add_argument("--config", required=True, help="the targets file (config/targets.yaml)")
    parser.add_argument("--apply", action="store_true", help="send the requests")
    return parser


def main(argv: Sequence[str] | None = None, clients: Clients = default_clients) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        regions, sizes = load_quota(Path(args.config), "aws")
    except (OSError, yaml.YAMLError, ValueError) as exc:
        print(f"quota_aws: {exc}", file=sys.stderr)
        return EXIT_USAGE
    mode = "apply: sends the requests" if args.apply else "dry run: add --apply to send"
    print(f"# AWS quota requests ({mode})")
    sent = failed = 0
    for region in regions:
        try:
            ec2, quotas = clients("ec2", region), clients("service-quotas", region)
            asks, problems = plan_region(ec2, quotas, region, sizes)
        except (ClientError, BotoCoreError) as exc:
            print(f"# {region}: cannot make the AWS clients ({exc}); skip")
            failed += 1
            continue
        for problem in problems:
            print(f"# {region} {problem}; skip")
            failed += 1
        for ask in asks:
            print(describe(ask))
            if ask.error:
                failed += 1
                continue
            if not ask.needed:
                continue
            print(shlex.join(request_args(region, ask)))
            if args.apply:
                try:
                    quotas.request_service_quota_increase(
                        ServiceCode="ec2", QuotaCode=ask.name, DesiredValue=float(ask.target)
                    )
                    print(f"# {region} {ask.name!r}: sent")
                except (ClientError, BotoCoreError) as exc:
                    failed += 1
                    print(f"# {region} {ask.name!r}: FAILED ({exc})")
                    continue
            sent += 1
    verb = "sent" if args.apply else "to send"
    print(f"## {sent} requests {verb}, {failed} errors.")
    return EXIT_FAILED if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
