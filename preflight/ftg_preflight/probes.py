"""`python -m ftg_preflight.probes`: one Markdown table of the probes of the active experiment.

A probe here is one target of one probe type: a signal probe (a job of the targets file, per
size and Region) or a provisioning probe (an entry of `provisioning_probes`, and for Azure its
pre-check). Each row has the provider, the probe type, the size, the Region, the zone (a dash
when none), N (provisioning only), real or simulated, the cadence, and the last row of the store
(`sim:` before the outcome of a simulated row). A summary per provider follows the table.

The command only reads: the targets files and one `duckdb` run over the store. It calls no cloud
API and allocates nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
DASH = "—"
NO_ROW = "no row"
NOT_READ = "store not read"
HEADER = (
    "Provider", "Probe type", "Size", "Region", "Zone", "N", "Real/Sim", "Cadence",
    "Last row (outcome, time UTC, n_placed)",
)  # fmt: skip
DEFAULT_DAYS = 3
# The first read takes only the newest file of each cell (a file name is
# `<UTC write time>_<cell>_<uuid>.jsonl`, 1 cell for each size and Region of a cycle), and only the
# last FIRST_READ_DAYS days. The newest row of a probe is in the newest file of its cell. For the
# types in WHOLE_WINDOW it skips: AWS stamps a new spot price record about every 6 h per zone, so
# the newest file holds a `none` poll, and a zone can have no success for many hours. Those types
# are read over the whole window at once (1 file for each cell is cheap). A probe with no row in
# the first read makes a second read of the whole window for its probe type.
FIRST_READ_DAYS = 2
WHOLE_WINDOW = ("aws_spot_price",)
SIM = "sim: "  # before the outcome of a simulated row
PROVISIONING_TYPES = ("aws_odcr", "az_capacity_reservation", "az_vmss_flex")
# A size that matches none of these is a control (a CPU size). Azure by a part of the name, AWS
# by the instance family (the text before the dot).
AZURE_GPU_PARTS = ("H100", "A100", "A10", "T4", "RTXPRO6000")
GPU_LETTERS = ("p", "g")
AWS_GPU_FAMILIES = ("p5", "p5en", "p4d", "p6-b200", "g7e", "g6e", "g5", "g4dn", "g6")
# The job-eligible probe types that read the settings of another module (prober config.py).
SETTINGS_OF = {
    "aws_spot_price": "aws_spot_score",
    "aws_spot_advisor": "aws_spot_score",
    "aws_cb_earliest": "aws_cb_offering",
    "aws_cb_longest": "aws_cb_offering",
}

Run = Callable[..., Any]


class ReadError(RuntimeError):
    """The store cannot be read."""


@dataclass(frozen=True)
class Probe:
    provider: str
    probe_type: str
    size: str
    region: str
    zone: str | None
    n: int | None
    simulated: bool
    cadence: str
    mode: str | None = None

    @property
    def provisioning(self) -> bool:
        return self.n is not None

    @property
    def control(self) -> bool:
        if not self.provisioning:
            return False
        if self.provider == "aws":
            return self.size.split(".")[0] not in AWS_GPU_FAMILIES
        return not any(part in self.size for part in AZURE_GPU_PARTS)


@dataclass(frozen=True)
class Last:
    ts: str
    outcome: str
    error_code: str | None
    n_placed: int | None
    # aws_spot_price only: this is then the newest success row, and these 3 fields are its price,
    # the AWS record time of the price, and the time of the last poll (often a `none` row).
    price: str | None = None
    price_ts: str | None = None
    poll_ts: str | None = None
    # A provisioning search, as the old dashboard showed it (the cell rule of
    # `web/sql/cells.sql`, removed at the cut-over; ADR 0023): the row above
    # is the newest row of the newest search that is not an upward row; these are the request of
    # that row, the upward search (`up_max` of `up_n_max`, `up_stopped`), and whether any row of
    # the search, upward or not, has a teardown that is not verified.
    n_requested: int | None = None
    up_max: int | None = None
    up_n_max: int | None = None
    up_stopped: str | None = None
    unverified: bool = False
    # `details.search_stopped` of the rows of the halving phase (not upward rows), for example
    # `deadline` or `stop`: the dashboard marks it, so this table does too.
    stopped: str | None = None

    def text(self, provisioning: bool, simulated: bool = False) -> str:
        if self.poll_ts:
            mark = SIM if simulated else ""
            if self.outcome != "success":  # no success row in the window
                return f"{mark}no success in the window; last poll {self.outcome}, {self.poll_ts}"
            return (
                f"{mark}success, {self.ts}, price={self.price} (record {self.price_ts}); "
                f"last poll {self.poll_ts}"
            )
        outcome = f"{self.outcome} ({self.error_code})" if self.error_code else self.outcome
        n = f", n_placed={self.n_placed}" if provisioning and self.n_placed is not None else ""
        mark = SIM if simulated else ""
        if self.up_max is not None:  # the search went up: base result, then the maximum
            base = f"{outcome} {self.n_placed} of {self.n_requested}"
            stop = f" (stopped: {self.up_stopped})" if self.up_stopped else ""
            top = f" · max {self.up_max} of {self.up_n_max}{stop}"
            n = ""
        else:
            base = f"{outcome} (stopped: {self.stopped})" if self.stopped else outcome
            top = ""
        leak = " · teardown not verified" if self.unverified else ""
        return f"{mark}{base}{top}{leak}, {self.ts}{n}"


@dataclass(frozen=True)
class Row:
    probe: Probe
    last: Last | None
    read: bool = True  # False when no store was read for the probe


# ---- the targets file ---------------------------------------------------------------------


def _strs(value: Any) -> list[str]:
    return [str(x) for x in value] if isinstance(value, list) else []


def job_sizes(probe_type: str, modules: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """The sizes of a job. A job with no size list gives 1 row with no size."""
    base = SETTINGS_OF.get(probe_type, probe_type)
    cfg = next((m for m in modules.values() if m.get("type") == base), {})
    if base == "aws_spot_score":
        requests = [r for r in cfg.get("spot_score_requests") or [] if isinstance(r, dict)]
        if probe_type == "aws_spot_score":  # the store sku is the types of 1 request, joined
            return [",".join(_strs(r.get("instance_types"))) for r in requests]
        # The prober probes only the GPU types (family p or g) for these 2 (request_gpu_types).
        types = (t for r in requests for t in _strs(r.get("instance_types")))
        return list(dict.fromkeys(t for t in types if t.split(".")[0][:1] in GPU_LETTERS))
    if base == "aws_cb_offering":
        return _strs(cfg.get("instance_types"))
    return _strs(cfg.get("spot_sizes"))


def _simulate(entry: dict[str, Any], where: str) -> bool:
    """`simulate` of a targets entry. Like the prober's config check, accept only true or false.
    `where` names the entry in the error."""
    value = entry.get("simulate", False)
    if not isinstance(value, bool):
        raise ValueError(f"{where}: simulate must be true or false, got {value!r}")
    return value


def load_probes(path: Path, provider: str) -> tuple[str, list[Probe]]:
    """The experiment name and the probes of 1 provider in a targets file."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: the top level is not a mapping")
    section = data.get(provider) or {}
    modules = {
        k: v if isinstance(v, dict) else {} for k, v in (section.get("modules") or {}).items()
    }
    cadence = section.get("cadence") or {}
    probes: list[Probe] = []
    try:
        for job in section.get("jobs") or []:
            name = job["module"]
            probe_type = (modules.get(name) or {}).get("type", name)
            sim = _simulate(job, f"job {name}")
            for region in _strs(job.get("regions")):
                for size in job_sizes(probe_type, modules) or [DASH]:
                    probes.append(
                        Probe(provider, probe_type, size, region, None, None, sim,
                              str(job.get("interval", DASH)))
                    )  # fmt: skip
        raw = section.get("provisioning_probes") or ([] if provider == "aws" else {})
        # The Azure file groups the entries by probe type; the AWS file is a list (ADR 0022).
        pairs = (
            [("aws_odcr", e) for e in raw]
            if provider == "aws"
            else [(t, e) for t, es in raw.items() for e in es]
        )
        entries = [e for _, e in pairs]
        key = "instance_type" if provider == "aws" else "sku"
        for ptype, entry in pairs:
            sim = _simulate(
                entry,
                f"provisioning_probes {ptype} {entry.get(key)} in {entry.get('region')}",
            )
            cad = str(cadence.get("aws_odcr" if provider == "aws" else "az_provisioning", DASH))
            probes.append(
                Probe(provider, ptype, str(entry[key]), str(entry["region"]),
                      None if entry.get("zone") is None else str(entry["zone"]),
                      int(entry["n"]), sim, cad, entry.get("placement_group_mode"))
            )  # fmt: skip
        if provider == "azure":  # the pre-check runs once for each distinct size and Region
            groups: dict[tuple[str, str], list[bool]] = {}
            for e in entries:
                groups.setdefault((str(e["sku"]), str(e["region"])), []).append(
                    _simulate(e, f"provisioning_probes {e.get('sku')} in {e.get('region')}")
                )
            cad = str(cadence.get("az_provisioning", DASH))
            probes += [
                Probe(provider, "az_precheck", sku, region, None, None, all(sims), cad)
                for (sku, region), sims in groups.items()
            ]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{path}: {provider}: bad entry ({exc!r})") from exc
    return str(data.get("experiment") or ""), probes


# ---- the store ----------------------------------------------------------------------------


def probe_types_of(probes: Sequence[Probe]) -> list[str]:
    types: list[str] = []
    for p in probes:
        types.append(p.probe_type)
    return list(dict.fromkeys(types))


def store_parts(store: str) -> tuple[str, str]:
    """The DuckDB secret statement (empty for a local store) and the glob prefix of a store."""
    store = store.rstrip("/")
    secret = ""
    if store.startswith("s3://"):
        secret = "CREATE OR REPLACE SECRET s3 (TYPE s3, PROVIDER credential_chain);"
    elif store.startswith("az://"):
        secret = (
            "CREATE OR REPLACE SECRET az (TYPE azure, PROVIDER credential_chain, CHAIN 'cli;env');"
        )
        account, _, path = store.removeprefix("az://").partition("/")
        store = f"az://{account}.blob.core.windows.net/{path}"
    elif store.startswith("file:///"):
        store = store.removeprefix("file://")
    else:
        raise ValueError("the store must start with s3://, az://, or file:///")
    return secret, store


def last_sql(
    store: str, experiment: str, probe_types: Sequence[str], days: Sequence[date], out: str,
    recent: Mapping[str, int] | None = None,
) -> str:  # fmt: skip
    """The DuckDB script that writes the last row of each target to the JSON file `out`.

    `recent` maps a probe type to 0 (read the newest file of each cell only) or to a number of
    seconds (read the files at most that much older than the newest file of the type). A type that
    is not in `recent` is read whole.
    """
    if re.search(r"""['"\\]""", store + experiment + out):
        raise ValueError("the store, the experiment, and the path must not hold a quote")
    secret, store = store_parts(store)
    unions = " UNION ALL ".join(
        f"SELECT '{pt}' AS pt, file FROM glob('{store}/experiment={experiment}/provider=*/"
        f"probe_type={pt}/date={d.isoformat()}/*.jsonl')"
        for pt in probe_types
        for d in days
    )
    secs = "".join(f" WHEN '{pt}' THEN {int(w)}" for pt, w in (recent or {}).items())
    width = f"CASE pt{secs} ELSE NULL END" if secs else "NULL"
    return f"""SET TimeZone = 'UTC';
{secret}
SET VARIABLE ftg_globs = (
  SELECT coalesce(list(file ORDER BY file), []::VARCHAR[]) FROM (
    SELECT file, w, ft, newest, row_number() OVER (
      PARTITION BY pt, cell ORDER BY ft DESC NULLS LAST, file DESC) AS rn
    FROM (
      SELECT file, pt, {width} AS w,
        regexp_extract(regexp_extract(file, '[^/]*$'), '^[^_]*_(.*)_[^_]*$', 1) AS cell,
        try_strptime(substr(regexp_extract(file, '[^/]*$'), 1, 16), '%Y%m%dT%H%M%SZ') AS ft,
        max(try_strptime(substr(regexp_extract(file, '[^/]*$'), 1, 16), '%Y%m%dT%H%M%SZ'))
          OVER (PARTITION BY pt) AS newest
      FROM ({unions})))
  WHERE w IS NULL OR ft IS NULL OR (w = 0 AND rn = 1) OR (w > 0 AND ft >= newest - to_seconds(w)));
SELECT 'ftg_matched=' || len(getvariable('ftg_globs'));
COPY (
  WITH rows AS (
    SELECT ts_utc, provider, probe_type, region, zone, sku, placement_group_mode AS mode, n_placed,
      outcome, error_code, coalesce(simulated, run_mode = 'local', false) AS sim,
      details->>'n_config' AS n_config, details->>'price' AS price,
      details->>'price_ts' AS price_ts,
      coalesce(try_cast(details->>'search_step' AS INTEGER), 0) AS step,
      coalesce(try_cast(details->>'cleanup' AS BOOLEAN), false) AS cleanup,
      run_id, n_requested,
      coalesce(details->>'search_direction' = 'up', false) AS up,
      try_cast(details->>'n_max' AS INTEGER) AS n_max,
      details->>'search_stopped' AS stopped,
      coalesce(details->'teardown'->>'verified' = 'false', false) AS unverified
    FROM read_json(list_concat(getvariable('ftg_globs'), ['empty.jsonl']),
      format = 'newline_delimited', union_by_name = true, columns = {{
        ts_utc: 'TIMESTAMPTZ', run_mode: 'VARCHAR', simulated: 'BOOLEAN', provider: 'VARCHAR',
        region: 'VARCHAR', zone: 'VARCHAR', probe_type: 'VARCHAR', sku: 'VARCHAR',
        placement_group_mode: 'VARCHAR', n_placed: 'INTEGER', outcome: 'VARCHAR',
        error_code: 'VARCHAR', details: 'JSON', run_id: 'VARCHAR', n_requested: 'INTEGER'}}))
  -- The same rule as the old dashboard (`web/sql/cells.sql`, removed at the cut-over; the board's
  -- own rule is the sweep of spec 0006 section 5.3, not this one): up_stopped is the
  -- up row's search_stopped, else the last up outcome when it is not success or no_capacity.
  -- A search is the rows of 1 target that share a run_id (a run_id is unique per cycle, spec
  -- 0002 section 7). The newest search is the run_id of the newest row. Its displayed row is the
  -- newest row that is not an upward row; the upward rows (ADR 0023) give only the up_* columns.
  , keyed AS (
    SELECT *, first_value(run_id) OVER (
      PARTITION BY provider, probe_type, region, zone, sku, mode, n_config, sim
      ORDER BY ts_utc DESC, step DESC) AS newest_run
    FROM rows WHERE NOT cleanup
  ), searched AS (
    SELECT *,
      count(*) FILTER (WHERE up) OVER s AS up_rows,
      max(n_requested) FILTER (WHERE outcome = 'success') OVER s AS up_max,
      max(n_max) FILTER (WHERE up) OVER s AS up_n_max,
      coalesce(max(stopped) FILTER (WHERE up) OVER s,
        if(arg_max(outcome, (ts_utc, step)) FILTER (WHERE up) OVER s
            NOT IN ('success', 'no_capacity'),
          arg_max(outcome, (ts_utc, step)) FILTER (WHERE up) OVER s, NULL)) AS up_stopped,
      bool_or(unverified) OVER s AS search_unverified,
      max(stopped) FILTER (WHERE NOT up) OVER s AS halving_stopped
    FROM keyed
    WINDOW s AS (PARTITION BY provider, probe_type, region, zone, sku, mode, n_config, sim, run_id)
  )
  SELECT provider, probe_type, region, zone, sku, mode, n_config, sim,
    strftime(ts_utc, '%Y-%m-%dT%H:%M:%SZ') AS ts, epoch_ms(ts_utc) AS ms, step,
    outcome, error_code, n_placed, price, price_ts, n_requested,
    if(up_rows > 0, up_max, NULL) AS up_max, up_n_max, up_stopped, search_unverified,
    halving_stopped
  FROM searched
  QUALIFY (run_id IS NOT DISTINCT FROM newest_run AND row_number() OVER (
    PARTITION BY provider, probe_type, region, zone, sku, mode, n_config, sim
    ORDER BY (run_id IS NOT DISTINCT FROM newest_run) DESC, up, ts_utc DESC, step DESC) = 1)
  OR (probe_type = 'aws_spot_price' AND outcome = 'success' AND row_number() OVER (
    PARTITION BY provider, probe_type, region, zone, sku, mode, n_config, sim, outcome = 'success'
    ORDER BY ts_utc DESC, step DESC) = 1)
) TO '{out}' (FORMAT json, ARRAY true);
"""


def read_last(
    store: str, experiment: str, probe_types: Sequence[str], days: int, run: Run = subprocess.run,
    today: Callable[[], date] = lambda: datetime.now(UTC).date(),
    recent: Mapping[str, int] | None = None,
) -> tuple[list[dict[str, Any]], int]:  # fmt: skip
    """The last row of each target in the store, from one `duckdb` run over the last `days` days
    (only the recent files of the types in `recent`, when set), and the number of files read."""
    day_list = [today() - timedelta(days=i) for i in range(days)]
    with tempfile.TemporaryDirectory(prefix="ftg-probes.") as tmp:
        out = Path(tmp) / "last.json"
        (Path(tmp) / "empty.jsonl").write_text("", encoding="utf-8")
        sql = last_sql(store, experiment, probe_types, day_list, str(out), recent)
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
            rows = json.loads(out.read_text(encoding="utf-8"))
            matched = int(re.search(r"ftg_matched=(\d+)", proc.stdout).group(1))
            return rows, matched
        except (OSError, json.JSONDecodeError, AttributeError, ValueError) as exc:
            raise ReadError(f"duckdb wrote no result file ({exc})") from exc


def first_read(probes: Sequence[Probe]) -> dict[str, int]:
    """The types of the first read, each with 0: the newest file of each cell."""
    return {t: 0 for t in probe_types_of(probes) if t not in WHOLE_WINDOW}


def _needs_wider_read(last: Last | None) -> bool:
    return last is None or (last.poll_ts is not None and last.outcome != "success")


def pick_last(probe: Probe, rows: Sequence[Mapping[str, Any]]) -> Last | None:
    """The newest row for the probe: same type, size, Region, and real or simulated.

    A provisioning row must also match the zone and N. A row with the entry's placement mode, or
    with none (a reservation row has `n/a`), matches; a row with another mode does not. The
    pre-check and the signal probes have no zone, so their rows match on the size and the Region
    only.
    """
    best: Mapping[str, Any] | None = None
    ok_by_zone: dict[str | None, Mapping[str, Any]] = {}
    for r in rows:
        if r["probe_type"] != probe.probe_type or bool(r["sim"]) != probe.simulated:
            continue
        if (r["region"], r["sku"]) != (probe.region, probe.size):
            continue
        if probe.provisioning:
            # An AWS entry has no zone: the probe covers every zone of the Region.
            if probe.provider != "aws" and r["zone"] != probe.zone:
                continue
            if r.get("n_config") not in (None, str(probe.n)):
                continue
            if r.get("mode") not in (None, "n/a", probe.mode):
                continue
        if best is None or _age(r) > _age(best):
            best = r
        z = r.get("zone")
        if r["outcome"] == "success" and (z not in ok_by_zone or _age(r) > _age(ok_by_zone[z])):
            ok_by_zone[z] = r
    if best is None:
        return None
    if probe.probe_type == "aws_spot_price":
        # A poll with no new price record is a `none` row: show the newest price of each zone
        # (always with the zone) and the time of the last poll.
        if not ok_by_zone:
            return Last(best["ts"], best["outcome"], best.get("error_code"), None, None, None,
                        best["ts"])  # fmt: skip
        newest = max(ok_by_zone.values(), key=_age)
        price = ", ".join(
            f"{(z or '').removeprefix(probe.region)} {r.get('price')}".strip()
            for z, r in sorted(ok_by_zone.items(), key=lambda kv: str(kv[0]))
        )
        return Last(newest["ts"], "success", None, None, price, newest.get("price_ts"), best["ts"])
    return Last(
        best["ts"], best["outcome"], best.get("error_code"), best.get("n_placed"),
        n_requested=best.get("n_requested"), up_max=best.get("up_max"),
        up_n_max=best.get("up_n_max"), up_stopped=best.get("up_stopped"),
        unverified=bool(best.get("search_unverified")), stopped=best.get("halving_stopped"),
    )  # fmt: skip


def _age(r: Mapping[str, Any]) -> tuple[str, int, int]:
    """Sort key of a row: the second, then the millisecond, then the search step (rows of 1 search
    can share a second; the later step is the newer row)."""
    return r["ts"], r.get("ms") or 0, r.get("step") or 0


# ---- the table ----------------------------------------------------------------------------


def _text(value: Any) -> str:
    return DASH if value is None else str(value).replace("|", "\\|")


def render_table(rows: Sequence[Row]) -> str:
    lines = ["| " + " | ".join(HEADER) + " |", "|" + "|".join("---" for _ in HEADER) + "|"]
    for r in rows:
        p = r.probe
        values = (
            p.provider, p.probe_type, p.size + (" (control)" if p.control else ""), p.region,
            p.zone, p.n, "sim" if p.simulated else "real", p.cadence,
            r.last.text(p.provisioning, p.simulated) if r.last else (DASH if r.read else NOT_READ),
        )  # fmt: skip
        lines.append("| " + " | ".join(_text(v) for v in values) + " |")
    return "\n".join(lines)


def summarize(rows: Sequence[Row]) -> list[str]:
    """Per provider: the probes of each type, real and simulated, and the last-row outcomes."""
    out = []
    for provider in dict.fromkeys(r.probe.provider for r in rows):
        mine = [r for r in rows if r.probe.provider == provider]
        out += [f"### {provider}", f"- total: {len(mine)} probes{_split(mine)}"]
        for ptype in dict.fromkeys(r.probe.probe_type for r in mine):
            of_type = [r for r in mine if r.probe.probe_type == ptype]
            out.append(
                f"- {ptype}: {len(of_type)} probes{_split(of_type)}; last row: {_outcomes(of_type)}"
            )
    return out


def _split(rows: Sequence[Row]) -> str:
    sim = sum(r.probe.simulated for r in rows)
    return f" ({len(rows) - sim} real, {sim} simulated)"


def _outcome_order(item: tuple[str, int]) -> tuple[bool, bool, str]:
    key = item[0]
    return key in (NO_ROW, NOT_READ), key.startswith(SIM), key


def _outcomes(rows: Sequence[Row]) -> str:
    counts = Counter(
        (SIM if r.probe.simulated else "") + r.last.outcome
        if r.last
        else (NO_ROW if r.read else NOT_READ)
        for r in rows
    )
    return ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=_outcome_order))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ftg_preflight.probes",
        description="Print a Markdown table of the probes of the active experiment, with the "
        "last row of each in the store. Read-only.",
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


def main(argv: Sequence[str] | None = None, *, run: Run = subprocess.run) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.days < 1:
        print("probes: --days must be 1 or more", file=sys.stderr)
        return EXIT_USAGE
    errors: list[str] = []
    notes: list[str] = []
    rows: list[Row] = []
    for provider in ("aws", "azure"):
        config, store = getattr(args, f"{provider}_config"), getattr(args, f"{provider}_store")
        if not config:
            continue
        try:
            experiment, probes = load_probes(Path(config), provider)
        except (OSError, yaml.YAMLError, ValueError) as exc:
            print(f"probes: {exc}", file=sys.stderr)
            return EXIT_USAGE
        last_rows: list[dict[str, Any]] = []
        read = False
        if store and experiment:
            try:
                plan = first_read(probes)
                last_rows, matched = [], 0
                if plan:
                    last_rows, matched = read_last(
                        store, experiment, list(plan), min(args.days, FIRST_READ_DAYS), run,
                        recent=plan,
                    )  # fmt: skip
                missing = probe_types_of(
                    [
                        p
                        for p in probes
                        if p.probe_type in WHOLE_WINDOW
                        or _needs_wider_read(pick_last(p, last_rows))
                    ]
                )
                if missing:  # a probe with no row in the first read: read its whole window
                    more, matched_more = read_last(store, experiment, missing, args.days, run)
                    last_rows, matched = last_rows + more, matched + matched_more
                read = True
                notes.append(
                    f"{provider}: last rows from the last {args.days} UTC days of the store "
                    f"(experiment {experiment})"
                    + ("" if matched else "; the store matched no file in the window")
                )
            except (ReadError, ValueError) as exc:
                errors.append(f"{provider}: cannot read the store ({exc})")
        else:
            notes.append(f"{provider}: store not read (no store or no experiment name)")
        rows += [Row(p, pick_last(p, last_rows), read) for p in probes]
    text = "\n".join(
        [*(f"- {n}" for n in notes), "", render_table(rows), "", "## Summary", *summarize(rows)]
        + (["", "## Errors", *[f"- {e}" for e in errors]] if errors else [])
    )
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    for e in errors:
        print(f"probes: {e}", file=sys.stderr)
    return EXIT_FAILED if errors else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
