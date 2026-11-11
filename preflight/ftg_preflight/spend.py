"""`python -m ftg_preflight.spend`: a daily spend estimate from the stored provisioning rows.

For each real, successful provisioning attempt (`aws_odcr`, `az_capacity_reservation`,
`az_vmss_flex`, upward-search steps included) the cost is

    n_placed x max(billing minimum, hold) x list price per hour,  hold = latency + teardown

where `hold` is `latency_ms / 1000` plus `details.teardown.seconds` of the row. The billing
minimum is 60 s for AWS ([AWS: capacity reservation pricing and billing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html))
and, as the conservative reading, 60 s for Azure (full minutes; the docs give no granularity,
research/learnings/azure-capacity-reservation-billing-granularity-not-documented.md).

List prices: the AWS Price List API (`pricing:GetProducts`, On-Demand, Linux, shared tenancy) and
the Azure Retail Prices API (no sign-in), each looked up once per run for each size and Region. A
failed lookup makes the row "price unknown"; the command never guesses a price.

The command only reads: the targets files (for the experiment name), the store, and the 2 public
price APIs. It allocates nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from ftg_preflight.probes import (
    DEFAULT_DAYS,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    ReadError,
    Run,
    load_probes,
    store_parts,
)

MIN_SECONDS = {"aws": 60.0, "azure": 60.0}
TYPES_OF = {"aws": ("aws_odcr",), "azure": ("az_capacity_reservation", "az_vmss_flex")}
AZURE_PRICES_URL = "https://prices.azure.com/api/retail/prices"
AWS_PRICING_REGION = "us-east-1"  # the Price List API endpoint
UNKNOWN = "price unknown"
METHOD = (
    "Method: n_placed x max(60 s, latency + teardown) x list price per hour, for each real "
    "successful provisioning attempt in the window, and for the VMs that a failed scale-set "
    "attempt placed (60 s: the AWS minimum; for Azure the "
    "conservative full-minute reading, since the docs give no granularity). Prices: AWS Price "
    "List API (On-Demand, Linux, shared tenancy) and Azure Retail Prices API (Consumption, "
    "Linux, not Spot or Low Priority), one lookup per size and Region. Disks, NICs, and public IPs "
    "of scale-set VMs are not counted. A failed aws_odcr attempt "
    "is not priced (a reservation is all or nothing). An estimate, not a bill."
)

PriceKey = tuple[str, str, str]  # provider, region, size
Pricer = Callable[[str, str], float]  # (region, size) -> USD per hour; raises LookupError


# ---- the price APIs -----------------------------------------------------------------------


def aws_hourly_price(client: Any, region: str, size: str) -> float:
    """The On-Demand Linux list price per instance hour, from `pricing:GetProducts`."""
    filters = {
        "instanceType": size, "regionCode": region, "operatingSystem": "Linux",
        "tenancy": "Shared", "preInstalledSw": "NA", "capacitystatus": "Used",
    }  # fmt: skip
    prices: set[float] = set()
    token: str | None = None
    while True:  # follow NextToken: a page holds at most MaxResults products
        page = {"NextToken": token} if token else {}
        try:
            resp = client.get_products(
                ServiceCode="AmazonEC2", MaxResults=10,
                Filters=[{"Type": "TERM_MATCH", "Field": k, "Value": v}
                         for k, v in filters.items()],
                **page,
            )  # fmt: skip
        except Exception as exc:  # noqa: BLE001 - any API failure is "price unknown"
            raise LookupError(f"AWS Price List call failed ({type(exc).__name__}: {exc})") from exc
        for item in resp.get("PriceList", []):
            product = json.loads(item) if isinstance(item, str) else item
            for term in (product.get("terms") or {}).get("OnDemand", {}).values():
                for dim in term.get("priceDimensions", {}).values():
                    if dim.get("unit") == "Hrs":
                        prices.add(float(dim["pricePerUnit"]["USD"]))
        token = resp.get("NextToken")
        if not token:
            break
    return _one(prices, "AWS", size, region)


def azure_hourly_price(get: Callable[[str], Mapping[str, Any]], region: str, size: str) -> float:
    """The Linux Consumption list price per hour, from the Azure Retail Prices API."""
    if "'" in region + size:
        raise LookupError("a quote in the size or the Region")
    flt = (
        f"armRegionName eq '{region}' and armSkuName eq '{size}' and "
        "serviceName eq 'Virtual Machines' and priceType eq 'Consumption'"
    )
    try:
        resp = get(AZURE_PRICES_URL + "?" + urllib.parse.urlencode({"$filter": flt}))
    except Exception as exc:  # noqa: BLE001 - any API failure is "price unknown"
        raise LookupError(f"Azure Retail Prices call failed ({type(exc).__name__}: {exc})") from exc
    if resp.get("NextPageLink"):
        raise LookupError("the Azure answer has more than 1 page")
    prices = {
        float(i["retailPrice"])
        for i in resp.get("Items", [])
        if i.get("unitOfMeasure") == "1 Hour"
        and not str(i.get("productName", "")).endswith("Windows")
        and not str(i.get("skuName", "")).endswith(("Spot", "Low Priority"))
    }
    return _one(prices, "Azure", size, region)


def _one(prices: set[float], cloud: str, size: str, region: str) -> float:
    if len(prices) != 1:
        raise LookupError(f"{cloud}: {len(prices)} list prices for {size} in {region}, not 1")
    return next(iter(prices))


def http_get_json(url: str) -> Mapping[str, Any]:
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - fixed https URL
        return json.load(resp)


# ---- the store ----------------------------------------------------------------------------


def spend_sql(store: str, experiment: str, types: Sequence[str], days: Sequence[date],
              out: str) -> str:  # fmt: skip
    """The DuckDB script that writes the real, successful provisioning rows to the JSON file."""
    if re.search(r"""['"\\]""", store + experiment + out):
        raise ValueError("the store, the experiment, and the path must not hold a quote")
    secret, store = store_parts(store)
    unions = " UNION ALL ".join(
        f"SELECT file FROM glob('{store}/experiment={experiment}/provider=*/"
        f"probe_type={t}/date={d.isoformat()}/*.jsonl')"
        for t in types
        for d in days
    )
    return f"""SET TimeZone = 'UTC';
{secret}
SET VARIABLE ftg_globs = (SELECT coalesce(list(file ORDER BY file), []::VARCHAR[]) FROM ({unions}));
SELECT 'ftg_matched=' || len(getvariable('ftg_globs'));
COPY (
  WITH r AS (
    SELECT strftime(ts_utc, '%Y-%m-%d') AS day, probe_type, region, sku, outcome, latency_ms,
      try_cast(details->'teardown'->>'seconds' AS DOUBLE) AS teardown_s,
      -- The teardown before the probe (`pre_teardown`) can also be unverified.
      coalesce((details->'teardown'->>'verified') = 'false'
        OR (details->'pre_teardown'->>'verified') = 'false', false) AS unverified,
      -- A row that skipped its probe (call deadline) copies pre_teardown into teardown: it
      -- placed nothing and is not a failed attempt with an unknown count.
      coalesce(try_cast(details->>'skipped' AS BOOLEAN), false) AS skipped,
      coalesce(try_cast(details->>'cleanup' AS BOOLEAN), false) AS cleanup,
      -- A success counts n_placed. A failed scale set row can still have placed VMs: the
      -- count is in details (`n_placed`, or `n_placed_at_timeout` for a timeout).
      CASE WHEN outcome = 'success' THEN n_placed
        WHEN probe_type = 'az_vmss_flex' THEN coalesce(
          try_cast(details->>'n_placed' AS INTEGER),
          try_cast(details->>'n_placed_at_timeout' AS INTEGER))
      END AS n
    FROM read_json(list_concat(getvariable('ftg_globs'), ['empty.jsonl']),
      format = 'newline_delimited', union_by_name = true, columns = {{
        ts_utc: 'TIMESTAMPTZ', run_mode: 'VARCHAR', simulated: 'BOOLEAN', probe_type: 'VARCHAR',
        region: 'VARCHAR', sku: 'VARCHAR', n_placed: 'INTEGER', outcome: 'VARCHAR',
        latency_ms: 'BIGINT', details: 'JSON'}})
    WHERE NOT coalesce(simulated, run_mode = 'local', false)
  )
  SELECT * FROM r
  WHERE cleanup OR unverified OR n > 0 OR (probe_type = 'aws_odcr' AND outcome = 'timeout')
    OR (probe_type = 'az_vmss_flex' AND n IS NULL AND outcome <> 'success' AND NOT skipped)
  ORDER BY day
) TO '{out}' (FORMAT json, ARRAY true);
"""


def read_attempts(store: str, experiment: str, types: Sequence[str], days: int,
                  run: Run = subprocess.run,
                  today: Callable[[], date] = lambda: datetime.now(UTC).date(),
                  ) -> tuple[list[dict[str, Any]], int]:  # fmt: skip
    """The rows that cost money or may have (see `spend_sql`), and the number of files read."""
    day_list = [today() - timedelta(days=i) for i in range(days)]
    with tempfile.TemporaryDirectory(prefix="ftg-spend.") as tmp:
        out = Path(tmp) / "rows.json"
        (Path(tmp) / "empty.jsonl").write_text("", encoding="utf-8")
        sql = spend_sql(store, experiment, types, day_list, str(out))
        try:
            proc = run(
                ["duckdb", "-init", "/dev/null", "-bail", "-noheader", "-list", ":memory:"],
                input=sql, capture_output=True, text=True, cwd=tmp, check=False,
            )  # fmt: skip
        except FileNotFoundError as exc:
            raise ReadError("duckdb is not installed (run through mise exec --)") from exc
        if proc.returncode != 0:
            raise ReadError(f"duckdb failed: {proc.stderr.strip()[-300:]}")
        try:
            matched = int(re.search(r"ftg_matched=(\d+)", proc.stdout).group(1))
            return json.loads(out.read_text(encoding="utf-8")), matched
        except (OSError, json.JSONDecodeError, AttributeError, ValueError) as exc:
            raise ReadError(f"duckdb wrote no result file ({exc})") from exc


# ---- the estimate -------------------------------------------------------------------------


def estimate(
    attempts: Mapping[str, Sequence[Mapping[str, Any]]], pricers: Mapping[str, Pricer]
) -> tuple[list[dict[str, Any]], dict[PriceKey, str], dict[str, float]]:
    """One cost line for each priced attempt. `attempts` maps a provider to its rows. A line has
    `cost` None when the price lookup failed; `unknown` maps the keys of those lookups to the
    reason. The third value counts what may have cost money and is not fully priced: `cleanup`
    rows, `aws_timeout` rows, `no_count` rows (a failed scale-set row with no count of placed
    VMs), and `unverified` rows (priced rows whose teardown was not verified; `unverified_s` is
    their instance-seconds, priced at the recorded hold only; `unverified_unpriced` counts the
    unverified rows that have no count and so no price)."""
    cache: dict[PriceKey, float | str] = {}
    lines: list[dict[str, Any]] = []
    left = {
        "cleanup": 0,
        "aws_timeout": 0,
        "no_count": 0,
        "unverified": 0,
        "unverified_s": 0.0,
        "unverified_unpriced": 0,
    }
    for provider, rows in attempts.items():
        for r in rows:
            if r.get("cleanup"):
                left["cleanup"] += 1
                continue
            if not r.get("n"):  # an unpriced row: no count of instances
                if r.get("probe_type") == "aws_odcr" and r.get("outcome") == "timeout":
                    left["aws_timeout"] += 1
                elif r.get("unverified"):
                    left["unverified_unpriced"] += 1
                elif (
                    r.get("probe_type") == "az_vmss_flex"
                    and r.get("n") is None
                    and not r.get("skipped")  # the probe never ran: nothing was placed
                    # a scale-out could have happened: a teardown ran, or the call took time
                    and (r.get("teardown_s") is not None or (r.get("latency_ms") or 0) > 0)
                ):
                    left["no_count"] += 1
                continue
            key = (provider, r["region"], r["sku"])
            if key not in cache:
                try:
                    cache[key] = pricers[provider](r["region"], r["sku"])
                except LookupError as exc:
                    cache[key] = str(exc)
            price = cache[key]
            hold = r["latency_ms"] / 1000 + (r.get("teardown_s") or 0.0)
            billed = r["n"] * max(MIN_SECONDS[provider], hold)
            if r.get("unverified"):
                left["unverified"] += 1
                left["unverified_s"] += billed
            lines.append({
                "provider": provider, "day": r["day"], "probe_type": r["probe_type"],
                "region": r["region"], "sku": r["sku"], "billed_s": billed,
                "outcome": r.get("outcome"),
                "cost": None if isinstance(price, str) else billed / 3600 * price,
            })  # fmt: skip
    return lines, {k: v for k, v in cache.items() if isinstance(v, str)}, left  # type: ignore[misc]


def _money(value: float) -> str:
    return f"{value:.4f}"


def _total(lines: Sequence[Mapping[str, Any]]) -> str:
    known = [ln["cost"] for ln in lines if ln["cost"] is not None]
    unknown = len(lines) - len(known)
    text = _money(sum(known))
    return text + (f" (+ {unknown} attempts, {UNKNOWN})" if unknown else "")


def render(
    lines: Sequence[Mapping[str, Any]],
    unknown: Mapping[PriceKey, str],
    days: int,
    left: Mapping[str, float] | None = None,
    reads: Mapping[str, int | None] | None = None,
    unread: Sequence[str] = (),
) -> str:
    left = left or {}
    lower = any(
        left.get(k) for k in ("cleanup", "aws_timeout", "no_count", "unverified",
                              "unverified_unpriced")
    )  # fmt: skip
    out = [METHOD, "", f"Window: the last {days} UTC days. Costs in USD.", ""]
    if reads is not None:
        out += [
            "Stores: "
            + "; ".join(
                f"{c}: {'not read' if n is None else f'{n} files read'}" for c, n in reads.items()
            ),
            "",
        ]
    out += [
        "## Per day and cloud",
        "",
        "| Day | Cloud | Attempts | Instance-seconds billed | Cost |",
        "|---|---|---|---|---|",
    ]
    by_day: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    by_probe: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for ln in lines:
        by_day[(ln["day"], ln["provider"])].append(ln)
        by_probe[(ln["provider"], ln["probe_type"], ln["sku"], ln["region"])].append(ln)
    for (day, provider), group in sorted(by_day.items()):
        out.append(
            f"| {day} | {provider} | {len(group)} | {sum(g['billed_s'] for g in group):.0f} "
            f"| {_total(group)} |"
        )
    out += ["", "## Per probe, whole window", "",
            "| Cloud | Probe type | Size | Region | Attempts | Instance-seconds billed | Cost |",
            "|---|---|---|---|---|---|---|"]  # fmt: skip
    for (provider, ptype, sku, region), group in sorted(by_probe.items()):
        cost = _total(group) if all(g["cost"] is not None for g in group) else UNKNOWN
        out.append(
            f"| {provider} | {ptype} | {sku} | {region} | {len(group)} "
            f"| {sum(g['billed_s'] for g in group):.0f} | {cost} |"
        )
    label = "lower bound " if lower else ""
    if unread:
        label = "incomplete, " + label
    out += [
        "",
        f"## Total, whole window ({label}estimate): {_total(lines)} USD ({len(lines)} attempts)",
    ]
    if unread:
        out += ["", f"Incomplete: the rows of {', '.join(unread)} are missing (see Errors)."]
    if lower:
        out += ["", "## Not counted: the total is a lower bound", ""]
        out += ["These rows may have cost money and are not fully priced:", ""]
        if left.get("aws_timeout"):
            out.append(
                f"- {left['aws_timeout']:.0f} aws_odcr timeout rows: the reservation may have been "
                "active, and the row has no count of instances"
            )
        if left.get("unverified"):
            out.append(
                f"- {left['unverified']:.0f} priced rows with a teardown that was not verified "
                f"({left['unverified_s']:.0f} instance-seconds, priced at the recorded hold only; "
                "the real hold ran until the end date or the sweeper)"
            )
        if left.get("unverified_unpriced"):
            out.append(
                f"- {left['unverified_unpriced']:.0f} rows with a teardown that was not verified "
                "and no count of instances, so no price"
            )
        if left.get("no_count"):
            out.append(
                f"- {left['no_count']:.0f} az_vmss_flex failed rows with no count of placed VMs "
                "(for example an error before the first poll): VMs may have been placed"
            )
        if left.get("cleanup"):
            out.append(
                f"- {left['cleanup']:.0f} cleanup rows: capacity that leaked and lived for its "
                "age, which is not priced"
            )
        out.append("")
        out.append("Partial scale-set placements of failed attempts are priced.")
    if unknown:
        out += ["", "## Price lookups that failed", ""]
        out += [f"- {p} {r} {s}: {why}" for (p, r, s), why in sorted(unknown.items())]
    return "\n".join(out)


# ---- the command --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ftg_preflight.spend",
        description="Estimate the daily spend of the real provisioning probes from the store "
        "rows and the public list prices. Read-only.",
    )
    for provider in ("aws", "azure"):
        parser.add_argument(f"--{provider}-config", help=f"the targets file for {provider}")
        parser.add_argument(
            f"--{provider}-store",
            help=f"the {provider} store: s3://bucket, az://account/container, or file:///path",
        )
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help="read the store rows of the last N UTC days (default 3)")  # fmt: skip
    parser.add_argument("--out", help="also write the output to this file")
    return parser


def main(
    argv: Sequence[str] | None = None, *, run: Run = subprocess.run,
    pricers: Mapping[str, Pricer] | None = None,
    today: Callable[[], date] = lambda: datetime.now(UTC).date(),
) -> int:  # fmt: skip
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.days < 1:
        print("spend: --days must be 1 or more", file=sys.stderr)
        return EXIT_USAGE
    if pricers is None:
        import boto3  # noqa: PLC0415 - only the real run needs it

        client = boto3.Session(region_name=AWS_PRICING_REGION).client("pricing")
        pricers = {
            "aws": lambda region, size: aws_hourly_price(client, region, size),
            "azure": lambda region, size: azure_hourly_price(http_get_json, region, size),
        }
    attempts: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    unread: list[str] = []
    notes: list[str] = []
    reads: dict[str, int | None] = {"aws": None, "azure": None}
    for provider in ("aws", "azure"):
        config, store = getattr(args, f"{provider}_config"), getattr(args, f"{provider}_store")
        if not config:
            if store:
                errors.append(
                    f"{provider}: a store is given but no targets file (--{provider}-config)"
                )
                unread.append(provider)
            continue
        try:
            experiment, probes = load_probes(Path(config), provider)
        except (OSError, yaml.YAMLError, ValueError) as exc:
            print(f"spend: {exc}", file=sys.stderr)
            return EXIT_USAGE
        has_real = any(p.provisioning and not p.simulated for p in probes)
        if not store:
            if has_real:
                errors.append(
                    f"{provider}: a targets file is given but no store (--{provider}-store)"
                )
                unread.append(provider)
            continue  # no real probe and no store: no row to expect, so nothing is missing
        if not experiment:
            errors.append(f"{provider}: the targets file has no experiment name")
            unread.append(provider)
            continue
        try:
            attempts[provider], matched = read_attempts(
                store, experiment, TYPES_OF[provider], args.days, run, today
            )
            reads[provider] = matched
            if not matched and has_real:
                errors.append(f"{provider}: the store matched no file in the window")
                unread.append(provider)
            elif not matched:
                notes.append(
                    f"{provider}: the store matched no file for experiment `{experiment}` in "
                    "the window; real spend under another experiment name is not counted"
                )
            elif not has_real and attempts[provider]:
                notes.append(
                    f"{provider}: targets file lists no real provisioning probe; counted "
                    f"{len(attempts[provider])} real rows from the store"
                )
        except (ReadError, ValueError) as exc:
            errors.append(f"{provider}: cannot read the store ({exc})")
            unread.append(provider)
    if not attempts and not errors:
        print(
            "spend: give a targets file and a store (--aws-store, --azure-store)", file=sys.stderr
        )
        return EXIT_USAGE
    lines, unknown, left = estimate(attempts, pricers)
    text = (
        render(lines, unknown, args.days, left, reads, unread)
        + ("\n\n## Notes\n" + "\n".join(f"- {n}" for n in notes) if notes else "")
        + ("\n\n## Errors\n" + "\n".join(f"- {e}" for e in errors) if errors else "")
    )
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    for e in errors:
        print(f"spend: {e}", file=sys.stderr)
    if unknown:
        print(f"spend: {len(unknown)} price lookups failed; the total is incomplete",
              file=sys.stderr)  # fmt: skip
    return EXIT_FAILED if errors or unknown else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
