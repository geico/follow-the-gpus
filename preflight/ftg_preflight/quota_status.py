"""`python -m ftg_preflight.quota status`: the state of the GPU quota requests, by region.

Spec 0011 section 7. Each call only reads. The command shows 1 row for each configured size of
each quota region, and the support ticket of the region.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ftg_preflight.azure import AzError, az_cli, capability, find_sku, restriction, sku_rows
from ftg_preflight.quota import (
    CORES,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    check_extension,
    load_quota,
    quota_limit,
    retrying,
    scope,
    subscription_id,
)

Az = Callable[..., Any]
DASH = "—"
TICKET_PREFIX = "ftg-gpu-quota-"  # a region ticket is named ftg-gpu-quota-<region>-<date>
# The short GPU class of each size of azure.quota. A size that is not here shows its own name.
GPU_CLASSES = {
    "Standard_NC40ads_H100_v5": "H100",
    "Standard_NC24ads_A100_v4": "A100",
    "Standard_NV36ads_A10_v5": "A10",
    "Standard_NC4as_T4_v3": "T4",
    "Standard_NC144ds_xl_RTXPRO6000BSE_v6": "RTX PRO 6000",
}
READ_ERRORS = (AzError, KeyError, TypeError, ValueError, AttributeError, IndexError)


@dataclass
class Row:
    region: str
    gpu: str
    asked_gpus: int | None
    asked_vcpus: int | None
    now: int | None
    state: str
    ticket: str = ""
    ticket_status: str = ""


def norm(name: str) -> str:
    """A key for quota names that differ in case and spaces."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def state_of(raw: str) -> str:
    low = raw.lower()
    if low == "succeeded":
        return "granted"
    return "pending" if low in ("inprogress", "accepted") else low


def to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def region_ticket(tickets: list[dict[str, Any]], region: str) -> tuple[str, str]:
    """(ticket number, status) of the newest ticket named for the region, else ("", "")."""
    prefix = f"{TICKET_PREFIX}{region}-".lower()
    named = [
        t for t in tickets
        if any(str(t.get(k) or "").lower().startswith(prefix) for k in ("name", "title"))
    ]  # fmt: skip
    if not named:
        return "", ""
    newest = max(named, key=lambda t: str(t.get("createdDate") or ""))
    return str(newest.get("supportTicketId") or newest.get("name")), str(newest.get("status") or "")


def region_rows(az: Az, sub: str, region: str, sizes: list[Any], top: int) -> list[Row]:
    skus = sku_rows(az, sub, region)
    reply = az("quota", "request", "list", "--scope", scope(sub, region), "--top", str(top))
    items = reply.get("value") if isinstance(reply, dict) else reply
    # The newest request of each resource name.
    latest: dict[str, dict[str, Any]] = {}
    for item in sorted(items or [], key=lambda i: str(i.get("requestSubmitTime") or "")):
        latest[norm(str(((item.get("value") or [{}])[0].get("name") or {}).get("value") or ""))] = (
            item
        )

    def limit(name: str) -> int | None:
        try:
            return quota_limit(az, sub, region, name)
        except READ_ERRORS:
            return None

    rows = []
    for s in sizes:
        if region not in s.regions:
            continue
        sku = find_sku(skus, s.size)
        family = str((sku or {}).get("family") or "")
        gpus = to_int(capability(sku, "GPUs")) if sku else None
        vcpus = to_int(capability(sku, "vCPUs")) if sku else None
        request = latest.get(norm(family)) if family else None
        if request is not None:
            state = state_of(str(request.get("provisioningState") or ""))
        else:
            state = "restricted" if sku and restriction(sku, region) else DASH
        rows.append(Row(
            region, GPU_CLASSES.get(s.size, s.size),
            s.count * gpus if gpus else None, s.count * vcpus if vcpus else None,
            limit(family) if family else None, state,
        ))  # fmt: skip
    cores = latest.get(CORES)
    if cores is not None:
        asked = to_int(((cores.get("value") or [{}])[0].get("limit") or {}).get("value"))
        rows.append(Row(region, CORES, None, asked, limit(CORES),
                        state_of(str(cores.get("provisioningState") or ""))))  # fmt: skip
    return rows


def _cell(n: int | None) -> str:
    return "?" if n is None else str(n)


def _asked(r: Row) -> str:
    return f"{DASH if r.gpu == CORES else _cell(r.asked_gpus)} / {_cell(r.asked_vcpus)}"


def _now(r: Row) -> str:
    return DASH if r.state == "restricted" else _cell(r.now)


def render_table(rows: list[Row]) -> str:
    table = [["REGION", "GPU", "ASKED (GPUs / vCPU)", "NOW (vCPU)", "STATE", "TICKET"]]
    table += [[r.region, r.gpu, _asked(r), _now(r), r.state, r.ticket or DASH] for r in rows]
    widths = [max(len(row[i]) for row in table) for i in range(6)]
    lines = ["  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)) for row in table]
    return "\n".join(line.rstrip() for line in lines)


def render_markdown(today: str, rows: list[Row]) -> str:
    lines = [f"# Azure GPU quota status, {today}"]
    for region in dict.fromkeys(r.region for r in rows):
        mine = [r for r in rows if r.region == region]
        ticket = (
            f"ticket {mine[0].ticket} ({mine[0].ticket_status})" if mine[0].ticket else "no ticket"
        )
        lines += ["", f"## {region} {DASH} {ticket}", ""]
        lines += ["| GPU | Asked (GPUs / vCPU) | Now (vCPU) | State |", "|---|---|---|---|"]
        lines += [f"| {r.gpu} | {_asked(r)} | {_now(r)} | {r.state} |" for r in mine]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ftg_preflight.quota status",
        description="Show the state of the Azure GPU quota requests of azure.quota, by region. "
        "Read-only.",
    )
    parser.add_argument("--config", required=True, help="the targets file (config/targets.yaml)")
    parser.add_argument("--subscription", help="default: AZURE_SUBSCRIPTION_ID, "
                        "ARM_SUBSCRIPTION_ID, or az account show")  # fmt: skip
    parser.add_argument("--top", type=int, default=100, help="requests to read per region")
    parser.add_argument("--format", choices=("table", "json", "markdown"), default="table")
    parser.add_argument(
        "--install-extension",
        action="store_true",
        help="install the quota and support extensions when they are missing",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    az: Az = az_cli,
    now: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    args = build_parser().parse_args(argv)
    az = retrying(az, sleep)
    try:
        regions, sizes = load_quota(Path(args.config))
    except (OSError, yaml.YAMLError, ValueError) as exc:
        print(f"quota status: {exc}", file=sys.stderr)
        return EXIT_USAGE
    for name in ("quota", "support"):
        message = check_extension(az, args.install_extension, name)
        if message:
            print(f"quota status: {message}", file=sys.stderr)
            return EXIT_USAGE
    try:
        sub = subscription_id(az, args.subscription)
    except (AzError, KeyError, TypeError, AttributeError) as exc:
        print(f"quota status: a read call failed ({exc})", file=sys.stderr)
        return EXIT_USAGE

    errors: list[str] = []
    try:
        tickets = az("support", "in-subscription", "tickets", "list", "--subscription", sub)
        tickets = [t for t in tickets or [] if isinstance(t, dict)]
    except READ_ERRORS as exc:
        errors.append(f"cannot read the support tickets ({exc})")
        tickets = []
    rows: list[Row] = []
    for region in regions:  # one at a time: parallel az processes race on the MSAL token cache
        try:
            found = region_rows(az, sub, region, sizes, args.top)
        except READ_ERRORS as exc:
            errors.append(f"{region}: a read call failed ({' '.join(str(exc).split())})")
            continue
        number, status = region_ticket(tickets, region)
        for r in found:
            r.ticket, r.ticket_status = number, status
        rows += found

    if args.format == "json":
        print(json.dumps({"subscription": sub, "rows": [asdict(r) for r in rows]}, indent=2))
    elif args.format == "markdown":
        print(render_markdown((now or datetime.now(UTC)).date().isoformat(), rows))
    else:
        print(render_table(rows))
    for e in errors:
        print(f"quota status: {e}", file=sys.stderr)
    return EXIT_FAILED if errors else EXIT_OK
