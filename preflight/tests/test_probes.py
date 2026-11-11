"""Tests of `python -m ftg_preflight.probes`. No network, no cloud credentials.

The store read runs the real `duckdb` CLI on a local `file:///` store, so the SQL, the stdout,
and the exit code are the real ones. The failure fake replays the recorded output of the real CLI
(`SELECT * FROM nope;` in a `-bail` run: exit 1, the message on stderr, nothing on stdout).
"""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ftg_preflight import probes
from ftg_preflight.probes import Last, Probe, Row

TODAY = date(2026, 10, 16)
CONFIG = """
experiment: ftg-test
aws:
  modules:
    aws_spot_score:
      type: aws_spot_score
      spot_score_requests:
        - {name: g5, instance_types: [g5.xlarge]}
        - {name: trio, instance_types: [g5.xlarge, g5.2xlarge]}
        - {name: cpu, instance_types: [m5.xlarge]}
    aws_cb_offering:
      type: aws_cb_offering
      instance_types: [p5.4xlarge]
  jobs:
    - {module: aws_spot_score, regions: [us-east-1], interval: 5m}
    - {module: aws_spot_price, regions: [us-east-1], interval: 5m}
    - {module: aws_cb_offering, regions: [us-east-1], interval: 2h, simulate: true}
  cadence: {aws_odcr: 2h}
  provisioning_probes:
    - {instance_type: m5.large, region: us-east-1, zone: us-east-1a, n: 2, simulate: true}
    - {instance_type: p5.4xlarge, region: us-east-1, zone: us-east-1a, n: 2, allow_billable_provisioning: true}
azure:
  modules:
    az_spot:
      type: az_spot
      spot_sizes: [Standard_NC4as_T4_v3]
  jobs:
    - {module: az_spot, regions: [eastus, westus], interval: 5m}
  cadence: {az_provisioning: 2h}
  provisioning_probes:
    az_capacity_reservation:
      - {sku: Standard_D2as_v4, region: eastus, zone: null, n: 2, placement_group_mode: multi, simulate: true}
      - {sku: Standard_NC4as_T4_v3, region: eastus, zone: null, n: 2, placement_group_mode: multi, allow_billable_provisioning: true}
    az_vmss_flex:
      - {sku: Standard_NC4as_T4_v3, region: eastus, zone: null, n: 2, placement_group_mode: single, simulate: true}
"""
# Rows in the shape of the store (spec 0003): 1 JSON object per line.
BASE = {"n_requested": 2, "n_placed": None, "error_code": None, "latency_ms": 5, "details": {}}


def row(ts: str, provider: str, probe_type: str, region: str, sku: str, outcome: str, **kw: Any):
    return {**BASE, "ts_utc": ts, "run_id": "r1", "simulated": False, "provider": provider,
            "region": region, "zone": None, "probe_type": probe_type, "sku": sku,
            "placement_group_mode": None, "outcome": outcome, **kw}  # fmt: skip


ROWS = [
    row("2026-10-16T01:00:00Z", "aws", "aws_spot_score", "us-east-1", "g5.xlarge", "success"),
    row("2026-10-16T02:00:00Z", "aws", "aws_spot_score", "us-east-1", "g5.xlarge", "none"),
    # the cleanup row of a search is no probe row
    row(
        "2026-10-16T03:00:00Z",
        "aws",
        "aws_odcr",
        "us-east-1",
        "p5.4xlarge",
        "success",
        zone="us-east-1a",
        n_placed=0,
        details={"cleanup": True},
    ),  # fmt: skip
    row(
        "2026-10-16T02:30:00Z",
        "aws",
        "aws_odcr",
        "us-east-1",
        "p5.4xlarge",
        "no_capacity",
        zone="us-east-1a",
        n_placed=0,
        error_code="InsufficientInstanceCapacity",
        details={"n_config": 2, "search_step": 1},
    ),  # fmt: skip
    # a reservation row has the mode `n/a`; a simulated row is a simulated probe
    row(
        "2026-10-16T04:00:00Z",
        "azure",
        "az_capacity_reservation",
        "eastus",
        "Standard_NC4as_T4_v3",
        "success",
        n_placed=2,
        placement_group_mode="n/a",
    ),  # fmt: skip
    row(
        "2026-10-16T05:00:00Z",
        "azure",
        "az_vmss_flex",
        "eastus",
        "Standard_NC4as_T4_v3",
        "success",
        n_placed=2,
        placement_group_mode="single",
        simulated=True,
    ),  # fmt: skip
    row(
        "2026-10-16T06:00:00Z", "azure", "az_precheck", "eastus", "Standard_NC4as_T4_v3", "success"
    ),
    # an old row has `run_mode` and no `simulated`: `local` means simulated
    {
        k: v
        for k, v in row(
            "2026-10-16T07:00:00Z",
            "azure",
            "az_spot",
            "eastus",
            "Standard_NC4as_T4_v3",
            "success",
            run_mode="local",
        ).items()
        if k != "simulated"
    },  # fmt: skip
]


def write_store(root: Path) -> str:
    for i, r in enumerate(ROWS):
        day = r["ts_utc"][:10]
        d = root / f"experiment=ftg-test/provider={r['provider']}/probe_type={r['probe_type']}"
        d = d / f"date={day}"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{i}.jsonl"
        f.write_text(json.dumps(r) + "\n", encoding="utf-8")
    return f"file://{root}"


@pytest.fixture
def targets(tmp_path: Path) -> Path:
    path = tmp_path / "targets.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    return path


def test_load_probes_aws(targets: Path):
    experiment, found = probes.load_probes(targets, "aws")
    assert experiment == "ftg-test"
    got = [(p.probe_type, p.size, p.n, p.simulated, p.cadence, p.control) for p in found]
    assert got == [
        ("aws_spot_score", "g5.xlarge", None, False, "5m", False),
        ("aws_spot_score", "g5.xlarge,g5.2xlarge", None, False, "5m", False),
        ("aws_spot_score", "m5.xlarge", None, False, "5m", False),  # the score keeps a CPU type
        ("aws_spot_price", "g5.xlarge", None, False, "5m", False),
        ("aws_spot_price", "g5.2xlarge", None, False, "5m", False),
        ("aws_cb_offering", "p5.4xlarge", None, True, "2h", False),
        ("aws_odcr", "m5.large", 2, True, "2h", True),
        ("aws_odcr", "p5.4xlarge", 2, False, "2h", False),
    ]


def test_load_probes_azure_has_a_precheck_per_size_and_region(targets: Path):
    _, found = probes.load_probes(targets, "azure")
    got = [(p.probe_type, p.size, p.region, p.mode, p.simulated) for p in found]
    assert got[0] == ("az_spot", "Standard_NC4as_T4_v3", "eastus", None, False)
    assert got[1][2] == "westus"
    prov = [g for g in got if g[0] in ("az_capacity_reservation", "az_vmss_flex")]
    assert [(g[0], g[3]) for g in prov] == [
        ("az_capacity_reservation", "multi"),
        ("az_capacity_reservation", "multi"),
        ("az_vmss_flex", "single"),
    ]
    # the pre-check is simulated only when every entry of its size and Region is simulated
    pre = {(g[1]): g[4] for g in got if g[0] == "az_precheck"}
    assert pre == {"Standard_D2as_v4": True, "Standard_NC4as_T4_v3": False}


def test_load_probes_bad_entry(tmp_path: Path):
    path = tmp_path / "t.yaml"
    path.write_text("aws:\n  provisioning_probes:\n    - {region: us-east-1}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bad entry"):
        probes.load_probes(path, "aws")


@pytest.mark.parametrize(
    "text",
    [
        "aws:\n  provisioning_probes:\n"
        "    - {instance_type: m5.large, region: us-east-1, n: 2, simulate: 'false'}\n",
        "aws:\n  jobs:\n    - {module: spot, regions: [us-east-1], interval: 1h, simulate: 'false'}\n",
        "azure:\n  provisioning_probes:\n    az_vmss_flex:\n"
        "      - {sku: Standard_D2as_v4, region: eastus, n: 2, simulate: 'false'}\n",
    ],
)
def test_load_probes_rejects_a_non_boolean_simulate(tmp_path: Path, text: str):
    path = tmp_path / "t.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(
        ValueError, match=r"(job spot|provisioning_probes .*): simulate must be true or false"
    ):
        probes.load_probes(path, "aws" if text.startswith("aws") else "azure")


def run_main(targets: Path, store: str | None, capsys, *extra: str, run=subprocess.run):
    args = ["--aws-config", str(targets), "--azure-config", str(targets), *extra]
    if store:
        args += ["--aws-store", store, "--azure-store", store]
    code = probes.main(args, run=run)
    return code, capsys.readouterr()


def test_table_and_summary_from_the_real_duckdb(targets: Path, tmp_path: Path, capsys, monkeypatch):
    store = write_store(tmp_path / "store")
    real = probes.read_last
    monkeypatch.setattr(probes, "read_last", lambda *a, **k: real(*a, **k, today=lambda: TODAY))
    code, out = run_main(targets, store, capsys)
    assert code == 0, out.err
    lines = out.out.splitlines()
    assert any(
        ln.startswith("| Provider | Probe type | Size | Region | Zone | N |") for ln in lines
    )
    text = out.out
    assert (
        "| aws | aws_spot_score | g5.xlarge | us-east-1 | — | — | real | 5m | none, 2026-10-16T02:00:00Z |"
        in text
    )
    assert (
        "| aws | aws_spot_score | g5.xlarge,g5.2xlarge | us-east-1 | — | — | real | 5m | — |"
        in text
    )
    assert (
        "| aws | aws_odcr | m5.large (control) | us-east-1 | us-east-1a | 2 | sim | 2h | — |"
        in text
    )
    assert ("| aws | aws_odcr | p5.4xlarge | us-east-1 | us-east-1a | 2 | real | 2h | "
            "no_capacity (InsufficientInstanceCapacity), 2026-10-16T02:30:00Z, n_placed=0 |") in text  # fmt: skip
    # the reservation row (`n/a`) is the last row of the real `multi` entry; the simulated
    # scale set row belongs to the simulated `single` entry
    assert ("| azure | az_capacity_reservation | Standard_NC4as_T4_v3 | eastus | —"
            " | 2 | real | 2h | success, 2026-10-16T04:00:00Z, n_placed=2 |") in text  # fmt: skip
    assert ("| azure | az_vmss_flex | Standard_NC4as_T4_v3 | eastus | —"
            " | 2 | sim | 2h | sim: success, 2026-10-16T05:00:00Z, n_placed=2 |") in text  # fmt: skip
    assert "| azure | az_precheck | Standard_NC4as_T4_v3 | eastus | — | — | real | 2h | success, 2026-10-16T06:00:00Z |" in text  # fmt: skip
    # the old row with run_mode `local` is a simulated row, so the real az_spot probe has none
    assert "| azure | az_spot | Standard_NC4as_T4_v3 | eastus | — | — | real | 5m | — |" in text
    summary = text[text.index("## Summary") :]
    assert "### aws\n- total: 8 probes (6 real, 2 simulated)" in summary
    assert "- aws_spot_score: 3 probes (3 real, 0 simulated); last row: none 1, no row 2" in summary
    assert (
        "- aws_odcr: 2 probes (1 real, 1 simulated); last row: no_capacity 1, no row 1" in summary
    )
    assert "### azure" in summary
    assert "- az_spot: 2 probes (2 real, 0 simulated); last row: no row 2" in summary
    assert "cell" not in text.lower()


def test_integer_zone_is_a_string(tmp_path: Path):
    path = tmp_path / "t.yaml"
    path.write_text(
        "azure:\n  provisioning_probes:\n    az_vmss_flex:\n"
        "      - {sku: Standard_NC4as_T4_v3, region: eastus, zone: 1, n: 2, simulate: true}\n",
        encoding="utf-8",
    )
    _, found = probes.load_probes(path, "azure")
    assert found[0].zone == "1"
    row = {"probe_type": "az_vmss_flex", "region": "eastus", "sku": "Standard_NC4as_T4_v3",
           "zone": "1", "sim": True, "mode": None, "n_config": None, "ts": "t",
           "outcome": "success", "error_code": None, "n_placed": 2}  # fmt: skip
    assert probes.pick_last(found[0], [row]) is not None


def test_out_file_and_no_store(targets: Path, tmp_path: Path, capsys):
    out_file = tmp_path / "out.md"
    code, out = run_main(targets, None, capsys, "--out", str(out_file))
    assert code == 0
    assert out_file.read_text(encoding="utf-8") == out.out
    assert "store not read" in out.out
    assert "- aws: store not read (no store or no experiment name)" in out.out
    assert "no row" not in out.out


def test_days_window_excludes_old_rows(tmp_path: Path):
    store = write_store(tmp_path / "store")
    rows, matched = probes.read_last(
        store, "ftg-test", ["aws_spot_score"], 1, today=lambda: date(2026, 10, 17)
    )
    # the rows are 1 day older than the window
    assert ([r["outcome"] for r in rows], matched) == ([], 0)
    rows, matched = probes.read_last(
        store, "ftg-test", ["aws_spot_score"], 2, today=lambda: date(2026, 10, 17)
    )
    assert ([r["outcome"] for r in rows], matched) == (["none"], 2)  # 2 files


def test_zero_matched_files_is_stated(targets: Path, tmp_path: Path, capsys):
    (tmp_path / "empty").mkdir()
    code, out = run_main(targets, f"file://{tmp_path / 'empty'}", capsys)
    assert code == 0
    assert "the store matched no file in the window" in out.out


# The recorded output of the real CLI: `echo 'SELECT * FROM nope;' | duckdb -bail ...`.
REAL_FAILURE = subprocess.CompletedProcess(
    ["duckdb"], 1, "",
    'Catalog Error: Table with name nope does not exist!\nDid you mean "pg_enum"?\n\n'
    "LINE 1: SELECT * FROM nope;\n                      ^\n",
)  # fmt: skip


def test_store_failure_keeps_the_table_and_exits_1(targets: Path, capsys):
    code, out = run_main(targets, "s3://bucket", capsys, run=lambda *a, **k: REAL_FAILURE)
    assert code == 1
    assert "| aws | aws_spot_score |" in out.out
    assert (
        "## Errors" in out.out and "cannot read the store (duckdb failed: Catalog Error" in out.out
    )
    assert "probes: aws: cannot read the store" in out.err


def test_missing_duckdb(targets: Path, capsys):
    def gone(*a: Any, **k: Any):
        raise FileNotFoundError("duckdb")

    code, out = run_main(targets, "s3://bucket", capsys, run=gone)
    assert code == 1
    assert "duckdb is not installed" in out.out


def test_bad_targets_file_exits_2(tmp_path: Path, capsys):
    code = probes.main(["--aws-config", str(tmp_path / "missing.yaml")])
    assert code == 2
    assert "probes:" in capsys.readouterr().err


def test_bad_store_url_and_quote():
    with pytest.raises(ValueError, match="s3://, az://, or file:///"):
        probes.last_sql("http://x", "e", ["aws_odcr"], [TODAY], "/tmp/o")
    with pytest.raises(ValueError, match="quote"):
        probes.last_sql("s3://b", "e'x", ["aws_odcr"], [TODAY], "/tmp/o")


def test_azure_store_url_and_secret():
    sql = probes.last_sql("az://acct/data", "e", ["az_precheck"], [TODAY], "/tmp/o")
    assert (
        "az://acct.blob.core.windows.net/data/experiment=e/provider=*/probe_type=az_precheck/date=2026-10-16/"
        in sql
    )
    assert "TYPE azure" in sql


def test_pick_last_prefers_newest_and_checks_n():
    p = Probe("aws", "aws_odcr", "p5.4xlarge", "us-east-1", "us-east-1a", 2, False, "2h")
    base = {"probe_type": "aws_odcr", "region": "us-east-1", "sku": "p5.4xlarge",
            "zone": "us-east-1a", "sim": False, "mode": None, "outcome": "success",
            "error_code": None, "n_placed": 2}  # fmt: skip
    rows = [
        {**base, "ts": "2026-10-16T01:00:00Z", "n_config": "2"},
        {**base, "ts": "2026-10-16T09:00:00Z", "n_config": "4", "outcome": "no_capacity"},
        {**base, "ts": "2026-10-16T03:00:00Z", "n_config": "2", "outcome": "partial"},
    ]
    assert probes.pick_last(p, rows) == Last("2026-10-16T03:00:00Z", "partial", None, 2)
    assert probes.pick_last(p, []) is None


def test_pick_last_of_an_aws_entry_with_no_zone_matches_a_row_of_any_zone():
    """An `aws_odcr` entry names no zone; the probe covers every zone of the Region."""
    p = Probe("aws", "aws_odcr", "p5.4xlarge", "us-east-1", None, 2, False, "2h")
    row = {"probe_type": "aws_odcr", "region": "us-east-1", "sku": "p5.4xlarge",
           "zone": "us-east-1b", "sim": False, "mode": None, "outcome": "success",
           "error_code": None, "n_placed": 2, "ts": "2026-10-16T01:00:00Z", "n_config": "2"}  # fmt: skip
    assert probes.pick_last(p, [row]) == Last("2026-10-16T01:00:00Z", "success", None, 2)


def test_summary_counts():
    a = Probe("aws", "aws_spot_score", "g5.xlarge", "us-east-1", None, None, False, "5m")
    b = Probe("aws", "aws_spot_score", "g6.xlarge", "us-east-1", None, None, True, "5m")
    rows = [Row(a, Last("t", "success", None, None)), Row(b, None)]
    assert probes.summarize(rows) == [
        "### aws",
        "- total: 2 probes (1 real, 1 simulated)",
        "- aws_spot_score: 2 probes (1 real, 1 simulated); last row: success 1, no row 1",
    ]


def test_zone_column_is_the_zone_or_a_dash_never_the_mode(
    targets: Path, tmp_path: Path, capsys, monkeypatch
):
    path = tmp_path / "zoned.yaml"
    path.write_text(
        "experiment: ftg-test\nazure:\n  cadence: {az_provisioning: 2h}\n  provisioning_probes:\n"
        "    az_vmss_flex:\n"
        "    - {sku: Standard_NC4as_T4_v3, region: eastus, zone: '2', n: 2, "
        "placement_group_mode: multi, simulate: true}\n"
        "    - {sku: Standard_NC4as_T4_v3, region: westus, zone: null, n: 2, "
        "placement_group_mode: multi, simulate: true}\n",
        encoding="utf-8",
    )
    code, out = run_main(path, None, capsys)
    assert code == 0
    assert "| eastus | 2 | 2 | sim |" in out.out
    assert "| westus | — | 2 | sim |" in out.out
    assert "multi" not in out.out


def test_simulated_rows_are_marked_and_counted_apart(
    targets: Path, tmp_path: Path, capsys, monkeypatch
):
    a = Probe("aws", "aws_spot_score", "g5.xlarge", "us-east-1", None, None, False, "5m")
    b = Probe("aws", "aws_spot_score", "g6.xlarge", "us-east-1", None, None, True, "5m")
    c = Probe("aws", "aws_spot_score", "g6e.xlarge", "us-east-1", None, None, True, "5m")
    last = Last("2026-10-16T01:00:00Z", "success", None, 2)
    rows = [Row(a, last), Row(b, last), Row(c, None)]
    table = probes.render_table(rows)
    assert "| real | 5m | success, 2026-10-16T01:00:00Z |" in table
    assert "| sim | 5m | sim: success, 2026-10-16T01:00:00Z |" in table
    assert probes.summarize(rows)[-1].endswith("last row: success 1, sim: success 1, no row 1")
    # a real run: the simulated row of the store carries the mark too
    real = probes.read_last
    monkeypatch.setattr(probes, "read_last", lambda *a, **k: real(*a, **k, today=lambda: TODAY))
    store = write_store(tmp_path / "store")
    _, out = run_main(targets, store, capsys)
    assert "sim: success, 2026-10-16T05:00:00Z, n_placed=2 |" in out.out


def _spot_store(root: Path, rows: list[tuple[str, str]]) -> str:
    """Files named like the prober's: the write time first. `rows` is (time, size)."""
    d = root / "experiment=ftg-test/provider=aws/probe_type=aws_spot_score/date=2026-10-16"
    d.mkdir(parents=True)
    for i, (t, size) in enumerate(rows):
        rec = row(f"2026-10-16T{t}Z", "aws", "aws_spot_score", "us-east-1", size, f"o{i}")
        (d / f"20261016T{t.replace(':', '')}Z_x_{i}.jsonl").write_text(
            json.dumps(rec) + "\n", encoding="utf-8"
        )
    return f"file://{root}"


def test_first_read_takes_the_newest_file_of_each_cell(tmp_path: Path):
    # file names are `<time>_<cell>_<uuid>`: 7 writes of the cell `x`, 2 of the cell `y`
    d = tmp_path / "s/experiment=ftg-test/provider=aws/probe_type=aws_spot_score/date=2026-10-16"
    d.mkdir(parents=True)
    for i, (t, cell) in enumerate([(f"0{h}:00:00", "x") for h in range(1, 8)]):
        rec = row(f"2026-10-16T{t}Z", "aws", "aws_spot_score", "us-east-1", "g5.xlarge", f"o{i}")
        (d / f"20261016T{t.replace(':', '')}Z_{cell}_u{i}.jsonl").write_text(json.dumps(rec) + "\n")
    for t, cell in (("02:30:00", "y"), ("03:30:00", "y")):
        rec = row(f"2026-10-16T{t}Z", "aws", "aws_spot_score", "us-east-1", "g6.xlarge", "oy")
        (d / f"20261016T{t.replace(':', '')}Z_{cell}_u.jsonl").write_text(json.dumps(rec) + "\n")
    store = f"file://{tmp_path / 's'}"
    rows, matched = probes.read_last(
        store, "ftg-test", ["aws_spot_score"], 1, today=lambda: TODAY, recent={"aws_spot_score": 0}
    )
    assert matched == 2 and sorted(r["outcome"] for r in rows) == ["o6", "oy"]
    # with a window of 2 h, every file of the type that is at most 2 h older than the newest
    rows, matched = probes.read_last(
        store, "ftg-test", ["aws_spot_score"], 1, today=lambda: TODAY,
        recent={"aws_spot_score": 2 * 3600},
    )  # fmt: skip
    assert matched == 3  # the files of 05:00, 06:00, and 07:00
    rows, matched = probes.read_last(store, "ftg-test", ["aws_spot_score"], 1, today=lambda: TODAY)
    assert matched == 9


def test_first_read_plan_per_probe_type():
    def pr(t: str) -> Probe:
        return Probe("aws", t, "g5.xlarge", "us-east-1", None, None, False, "5m")

    got = probes.first_read([pr("aws_spot_score"), pr("aws_spot_price")])
    # the spot price is read over the whole window: a zone can have no success for many hours
    assert got == {"aws_spot_score": 0}


def test_a_probe_missing_from_the_recent_files_is_read_over_the_window(
    tmp_path: Path, capsys, monkeypatch
):
    # g6.xlarge wrote only in the oldest file: the recent files do not hold it
    rows = [("01:00:00", "g6.xlarge")] + [(f"0{h}:00:00", "g5.xlarge") for h in range(2, 8)]
    store = _spot_store(tmp_path / "s", rows)
    targets = tmp_path / "t.yaml"
    targets.write_text(
        "experiment: ftg-test\naws:\n  modules:\n    s: {type: aws_spot_score, spot_score_requests:"
        " [{instance_types: [g5.xlarge]}, {instance_types: [g6.xlarge]}]}\n"
        "  jobs:\n    - {module: s, regions: [us-east-1], interval: 5m}\n",
        encoding="utf-8",
    )
    calls: list[str] = []

    def spy(*a: Any, **k: Any):
        calls.append(k["input"])
        return subprocess.run(*a, **k)

    real = probes.read_last
    monkeypatch.setattr(probes, "read_last", lambda *a, **k: real(*a, **k, today=lambda: TODAY))
    code = probes.main(["--aws-config", str(targets), "--aws-store", store], run=spy)
    out = capsys.readouterr().out
    assert code == 0
    assert len(calls) == 2 and "WHEN 'aws_spot_score' THEN 0" in calls[0]
    assert "THEN" not in calls[1]
    assert "| g5.xlarge | us-east-1 | — | — | real | 5m | o6, 2026-10-16T07:00:00Z |" in out
    assert "| g6.xlarge | us-east-1 | — | — | real | 5m | o0, 2026-10-16T01:00:00Z |" in out


def test_no_second_read_when_the_recent_files_hold_every_probe(tmp_path: Path, capsys, monkeypatch):
    real = probes.read_last
    monkeypatch.setattr(probes, "read_last", lambda *a, **k: real(*a, **k, today=lambda: TODAY))
    store = _spot_store(tmp_path / "s", [(f"0{h}:00:00", "g5.xlarge") for h in range(1, 8)])
    targets = tmp_path / "t.yaml"
    targets.write_text(
        "experiment: ftg-test\naws:\n  modules:\n    s: {type: aws_spot_score, spot_score_requests:"
        " [{instance_types: [g5.xlarge]}]}\n"
        "  jobs:\n    - {module: s, regions: [us-east-1], interval: 5m}\n",
        encoding="utf-8",
    )
    calls: list[str] = []

    def spy(*a: Any, **k: Any):
        calls.append(k["input"])
        return subprocess.run(*a, **k)

    code = probes.main(
        ["--aws-config", str(targets), "--aws-store", store, "--days", "30"], run=spy
    )
    assert code == 0 and len(calls) == 1
    assert "o6, 2026-10-16T07:00:00Z" in capsys.readouterr().out


def test_spot_price_shows_the_newest_price_and_the_last_poll(tmp_path: Path, capsys, monkeypatch):
    # 1 success row, then 6 polls with no new record (`none`): the latest polls hold no price
    d = tmp_path / "s/experiment=ftg-test/provider=aws/probe_type=aws_spot_price/date=2026-10-16"
    d.mkdir(parents=True)
    recs = [("01:00:00", "success", {"price": 1.25, "price_ts": "2026-10-16T00:40:00Z"})]
    recs += [(f"0{h}:00:00", "none", {}) for h in range(2, 8)]
    for i, (t, outcome, details) in enumerate(recs):
        rec = row(f"2026-10-16T{t}Z", "aws", "aws_spot_price", "us-east-1", "g5.xlarge", outcome,
                  zone="us-east-1a", details=details)  # fmt: skip
        (d / f"20261016T{t.replace(':', '')}Z_x_{i}.jsonl").write_text(json.dumps(rec) + "\n")
    targets = tmp_path / "t.yaml"
    targets.write_text(
        "experiment: ftg-test\naws:\n  modules:\n    s: {type: aws_spot_score, spot_score_requests:"
        " [{instance_types: [g5.xlarge]}]}\n"
        "  jobs:\n    - {module: s, regions: [us-east-1], interval: 5m}\n"
        "    - {module: aws_spot_price, regions: [us-east-1], interval: 5m}\n",
        encoding="utf-8",
    )
    real = probes.read_last
    monkeypatch.setattr(probes, "read_last", lambda *a, **k: real(*a, **k, today=lambda: TODAY))
    code = probes.main(["--aws-config", str(targets), "--aws-store", f"file://{tmp_path / 's'}"])
    out = capsys.readouterr().out
    assert code == 0
    assert ("| aws_spot_price | g5.xlarge | us-east-1 | — | — | real | 5m | success, "
            "2026-10-16T01:00:00Z, price=a 1.25 (record 2026-10-16T00:40:00Z); "
            "last poll 2026-10-16T07:00:00Z |") in out  # fmt: skip


def test_spot_price_with_no_success_in_the_window_says_so():
    p = Probe("aws", "aws_spot_price", "g5.xlarge", "us-east-1", None, None, False, "5m")
    r = {"probe_type": "aws_spot_price", "region": "us-east-1", "sku": "g5.xlarge", "zone": "a",
         "sim": False, "mode": None, "n_config": None, "ts": "2026-10-16T07:00:00Z",
         "outcome": "none", "error_code": None, "n_placed": None}  # fmt: skip
    last = probes.pick_last(p, [r])
    assert last.text(False) == "no success in the window; last poll none, 2026-10-16T07:00:00Z"


def _store_of(root: Path, probe_type: str, recs: list[dict[str, Any]]) -> str:
    d = root / f"experiment=ftg-test/provider=aws/probe_type={probe_type}/date=2026-10-16"
    d.mkdir(parents=True)
    for i, rec in enumerate(recs):
        (d / f"20261016T0{i}0000Z_cell_u{i}.jsonl").write_text(json.dumps(rec) + "\n")
    return f"file://{root}"


def test_equal_second_rows_of_a_search_show_the_last_step(tmp_path: Path, capsys, monkeypatch):
    # 1 search: the 3 rows share ts_utc (to the second); the last step is the newest row
    base = dict(zone="us-east-1a", n_placed=0)
    recs = [
        row("2026-10-16T01:00:00.100Z", "aws", "aws_odcr", "us-east-1", "p5.4xlarge", "success",
            details={"n_config": 2, "search_step": 1}, **base),
        row("2026-10-16T01:00:00.200Z", "aws", "aws_odcr", "us-east-1", "p5.4xlarge",
            "no_capacity", details={"n_config": 2, "search_step": 2}, **base),
        row("2026-10-16T01:00:00.200Z", "aws", "aws_odcr", "us-east-1", "p5.4xlarge",
            "restricted", details={"n_config": 2, "search_step": 3}, **base),
    ]  # fmt: skip
    store = _store_of(tmp_path / "s", "aws_odcr", recs)
    targets = tmp_path / "t.yaml"
    targets.write_text(
        "experiment: ftg-test\naws:\n  cadence: {aws_odcr: 2h}\n  provisioning_probes:\n"
        "    - {instance_type: p5.4xlarge, region: us-east-1, zone: us-east-1a, n: 2, "
        "allow_billable_provisioning: true}\n",
        encoding="utf-8",
    )
    real = probes.read_last
    monkeypatch.setattr(probes, "read_last", lambda *a, **k: real(*a, **k, today=lambda: TODAY))
    assert probes.main(["--aws-config", str(targets), "--aws-store", store]) == 0
    out = capsys.readouterr().out
    assert "| 2 | real | 2h | restricted, 2026-10-16T01:00:00Z, n_placed=0 |" in out


def test_spot_price_with_several_zones_shows_each_zone(tmp_path: Path, capsys, monkeypatch):
    recs = [
        row(f"2026-10-16T0{i}:00:00Z", "aws", "aws_spot_price", "us-east-1", "g5.xlarge", o,
            zone=z, details=d)
        for i, (z, o, d) in enumerate(
            [("us-east-1a", "success", {"price": 1.5, "price_ts": "2026-10-16T00:00:00Z"}),
             ("us-east-1b", "success", {"price": 1.75, "price_ts": "2026-10-16T01:00:00Z"}),
             ("us-east-1a", "none", {}), ("us-east-1b", "none", {})]
        )
    ]  # fmt: skip
    store = _store_of(tmp_path / "s", "aws_spot_price", recs)
    targets = tmp_path / "t.yaml"
    targets.write_text(
        "experiment: ftg-test\naws:\n  modules:\n    s: {type: aws_spot_score, "
        "spot_score_requests: [{instance_types: [g5.xlarge]}]}\n"
        "  jobs:\n    - {module: aws_spot_price, regions: [us-east-1], interval: 5m}\n"
        "    - {module: s, regions: [us-east-1], interval: 5m}\n",
        encoding="utf-8",
    )
    real = probes.read_last
    monkeypatch.setattr(probes, "read_last", lambda *a, **k: real(*a, **k, today=lambda: TODAY))
    assert probes.main(["--aws-config", str(targets), "--aws-store", store]) == 0
    out = capsys.readouterr().out
    assert (
        "price=a 1.5, b 1.75 (record 2026-10-16T01:00:00Z); last poll 2026-10-16T03:00:00Z" in out
    )


def _odcr_row(ts: str, ms: int | None, step: int | None, outcome: str) -> dict[str, Any]:
    r = {"probe_type": "aws_odcr", "region": "us-east-1", "sku": "p5.4xlarge",
         "zone": "us-east-1a", "sim": False, "mode": None, "n_config": "2", "ts": ts,
         "outcome": outcome, "error_code": None, "n_placed": 0}  # fmt: skip
    if ms is not None:
        r["ms"] = ms
    if step is not None:
        r["step"] = step
    return r


@pytest.mark.parametrize(
    "rows",
    [
        # same second, ms differ (earlier success first in the list)
        [_odcr_row("T", 1000, 1, "success"), _odcr_row("T", 1500, 1, "no_capacity")],
        # same second and ms, steps differ
        [_odcr_row("T", 1000, 1, "success"), _odcr_row("T", 1000, 3, "no_capacity")],
        # ms differ, step absent
        [_odcr_row("T", 1000, None, "success"), _odcr_row("T", 1500, None, "no_capacity")],
    ],
    ids=["ms", "step", "ms-no-step"],
)
def test_pick_last_breaks_a_same_second_tie_on_ms_then_step(rows):
    p = Probe("aws", "aws_odcr", "p5.4xlarge", "us-east-1", "us-east-1a", 2, False, "2h")
    assert probes.pick_last(p, rows).outcome == "no_capacity"
    assert probes.pick_last(p, rows[::-1]).outcome == "no_capacity"


def test_spot_price_zone_with_an_old_success_still_shows(tmp_path: Path, capsys, monkeypatch):
    # zone a: success at 00:05, then `none` hourly; zone b: success at 10:05; newest poll 11:05
    d = tmp_path / "s/experiment=ftg-test/provider=aws/probe_type=aws_spot_price/date=2026-10-16"
    d.mkdir(parents=True)
    recs = [("00:05", "a", "success", 1.5), ("10:05", "b", "success", 1.9)]
    recs += [(f"{h:02d}:05", "a", "none", None) for h in range(1, 12)]
    recs += [(f"{h:02d}:06", "b", "none", None) for h in range(11, 12)]
    for i, (t, z, outcome, price) in enumerate(recs):
        details = {"price": price, "price_ts": f"2026-10-16T{t}:00Z"} if price else {}
        rec = row(f"2026-10-16T{t}:00Z", "aws", "aws_spot_price", "us-east-1", "g5.xlarge", outcome,
                  zone=f"us-east-1{z}", details=details)  # fmt: skip
        (d / f"20261016T{t.replace(':', '')}00Z_{z}_u{i}.jsonl").write_text(json.dumps(rec) + "\n")
    targets = tmp_path / "t.yaml"
    targets.write_text(
        "experiment: ftg-test\naws:\n  jobs:\n"
        "    - {module: aws_spot_price, regions: [us-east-1], interval: 5m}\n"
        "  modules:\n    aws_spot_score: {type: aws_spot_score, spot_score_requests: "
        "[{instance_types: [g5.xlarge]}]}\n",
        encoding="utf-8",
    )
    real = probes.read_last
    monkeypatch.setattr(probes, "read_last", lambda *a, **k: real(*a, **k, today=lambda: TODAY))
    assert (
        probes.main(["--aws-config", str(targets), "--aws-store", f"file://{tmp_path / 's'}"]) == 0
    )
    out = capsys.readouterr().out
    assert "price=a 1.5, b 1.9 (record 2026-10-16T10:05:00Z); last poll 2026-10-16T11:06:00Z" in out


# ---- the upward search (ADR 0023) ---------------------------------------------


def _search_line(
    tmp_path: Path,
    steps: list[tuple],
    monkeypatch,
    capsys,
    run_id: str = "r1",
    older: list[dict] = (),
) -> str:
    """The table line of 1 aws_odcr entry (n = 2) whose newest cycle is `steps`: tuples of
    (n_requested, outcome, details). All rows are in 1 file, as the prober writes a cycle."""
    d = tmp_path / "s/experiment=ftg-test/provider=aws/probe_type=aws_odcr/date=2026-10-16"
    d.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(o) for o in older]
    for i, (n_req, outcome, details) in enumerate(steps):
        rec = row(f"2026-10-16T01:00:0{i}Z", "aws", "aws_odcr", "us-east-1", "p5.4xlarge", outcome,
                  zone="us-east-1a", n_requested=n_req, n_placed=n_req if outcome == "success" else 0,
                  run_id=run_id, details={"n_config": 2, "search_step": i + 1, **details})  # fmt: skip
        lines.append(json.dumps(rec))
    (d / "20261016T010000Z_cell_u.jsonl").write_text("\n".join(lines) + "\n")
    targets = tmp_path / "t.yaml"
    targets.write_text(
        "experiment: ftg-test\naws:\n  cadence: {aws_odcr: 2h}\n  provisioning_probes:\n"
        "    - {instance_type: p5.4xlarge, region: us-east-1, zone: us-east-1a, n: 2, n_max: 16, "
        "allow_billable_provisioning: true}\n",
        encoding="utf-8",
    )
    real = probes.read_last
    monkeypatch.setattr(probes, "read_last", lambda *a, **k: real(*a, **k, today=lambda: TODAY))
    assert (
        probes.main(["--aws-config", str(targets), "--aws-store", f"file://{tmp_path / 's'}"]) == 0
    )
    return next(x for x in capsys.readouterr().out.splitlines() if x.startswith("| aws | aws_odcr"))


UP = {"search_direction": "up", "n_max": 16}


def test_upward_search_shows_the_base_result_and_the_max_without_a_stop_on_no_capacity(
    tmp_path: Path, monkeypatch, capsys
):
    line = _search_line(tmp_path, [
        (2, "success", {}), (4, "success", UP), (8, "success", UP), (16, "no_capacity", UP),
    ], monkeypatch, capsys)  # fmt: skip
    assert line.endswith("| success 2 of 2 · max 8 of 16, 2026-10-16T01:00:00Z |")


def test_upward_search_that_reaches_n_max_has_no_stop(tmp_path: Path, monkeypatch, capsys):
    line = _search_line(tmp_path, [
        (2, "success", {}), (4, "success", UP), (8, "success", UP), (16, "success", UP),
    ], monkeypatch, capsys)  # fmt: skip
    assert "| success 2 of 2 · max 16 of 16, 2026-10-16T01:00:00Z |" in line


def test_search_with_no_upward_row_is_unchanged(tmp_path: Path, monkeypatch, capsys):
    line = _search_line(tmp_path, [(2, "success", {})], monkeypatch, capsys)
    assert line.endswith("| success, 2026-10-16T01:00:00Z, n_placed=2 |")


def test_failure_at_c_with_halving_is_unchanged(tmp_path: Path, monkeypatch, capsys):
    line = _search_line(tmp_path, [
        (2, "no_capacity", {}), (1, "success", {}),
    ], monkeypatch, capsys)  # fmt: skip
    assert line.endswith("| success, 2026-10-16T01:00:01Z, n_placed=1 |")


def test_unverified_teardown_on_an_upward_row_is_marked(tmp_path: Path, monkeypatch, capsys):
    bad = {**UP, "teardown": {"verified": False}, "search_stopped": "teardown_unverified"}
    line = _search_line(tmp_path, [
        (2, "success", {}), (4, "success", UP), (8, "success", bad),
    ], monkeypatch, capsys)  # fmt: skip
    assert (
        "| success 2 of 2 · max 8 of 16 (stopped: teardown_unverified) · teardown not verified, "
        in line
    )


def test_upward_rows_of_an_older_cycle_do_not_show(tmp_path: Path, monkeypatch, capsys):
    old = row("2026-10-16T00:00:00Z", "aws", "aws_odcr", "us-east-1", "p5.4xlarge", "success",
              zone="us-east-1a", n_requested=8, n_placed=8, run_id="old",
              details={"n_config": 2, "search_step": 1, **UP})  # fmt: skip
    line = _search_line(
        tmp_path, [(2, "success", {})], monkeypatch, capsys, run_id="new", older=[old]
    )
    assert line.endswith("| success, 2026-10-16T01:00:00Z, n_placed=2 |")


def test_upward_search_that_ends_in_a_timeout_shows_the_stop(tmp_path: Path, monkeypatch, capsys):
    line = _search_line(tmp_path, [
        (2, "success", {}), (4, "success", UP), (8, "timeout", UP),
    ], monkeypatch, capsys)  # fmt: skip
    assert "| success 2 of 2 · max 4 of 16 (stopped: timeout), 2026-10-16T01:00:00Z |" in line


def test_halving_phase_stop_is_marked(tmp_path: Path, monkeypatch, capsys):
    """A `deadline` or `stop` on a row that is not an upward row shows, as the
    dashboard shows it."""
    line = _search_line(tmp_path, [
        (2, "no_capacity", {}), (1, "success", {"search_stopped": "deadline"}),
    ], monkeypatch, capsys)  # fmt: skip
    assert line.endswith("| success (stopped: deadline), 2026-10-16T01:00:01Z, n_placed=1 |")
