"""Tests of `python -m ftg_preflight.spend`. No network, no cloud credentials.

The store read runs the real `duckdb` CLI on a local `file:///` store. The price fakes return the
recorded responses of the real APIs (tests/fixtures/aws_pricing_get_products_*.json from
`aws pricing get-products`, and azure_retail_prices_*.json from the Azure Retail Prices API),
so the parsers read the real response shape.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ftg_preflight import spend

FIXTURES = Path(__file__).parent / "fixtures"
AWS_PRICE = json.loads((FIXTURES / "aws_pricing_get_products_m5_large_us_east_1.json").read_text())
AZURE_PRICE = json.loads((FIXTURES / "azure_retail_prices_d2as_v4_eastus.json").read_text())
TODAY = date(2026, 10, 16)
CONFIG = """
experiment: ftg-test
aws:
  cadence: {aws_odcr: 2h}
  provisioning_probes:
    - {instance_type: m5.large, region: us-east-1, zone: us-east-1a, n: 2}
azure:
  cadence: {az_provisioning: 2h}
  provisioning_probes:
    az_capacity_reservation:
      - {sku: Standard_D2as_v4, region: eastus, zone: null, n: 2}
"""


def row(ts: str, provider: str, ptype: str, sku: str, region: str, n_placed: int | None,
        latency_ms: int, teardown_s: float | None, outcome: str = "success",
        **kw: Any):  # fmt: skip
    details: dict[str, Any] = {} if teardown_s is None else {"teardown": {"seconds": teardown_s}}
    return {"ts_utc": ts, "run_id": "r", "simulated": False, "provider": provider,
            "region": region, "zone": None, "probe_type": ptype, "sku": sku,
            "n_requested": n_placed, "n_placed": n_placed, "outcome": outcome,
            "error_code": None, "latency_ms": latency_ms, "details": details, **kw}  # fmt: skip


ROWS = [
    # 2 x 60 s (hold 8 s is under the minimum) x 0.096 / 3600 = 0.0032
    row("2026-10-16T01:00:00Z", "aws", "aws_odcr", "m5.large", "us-east-1", 2, 5000, 3.0),
    # an upward step: 4 x 80 s x 0.096 / 3600 = 0.0085333
    row("2026-10-16T02:00:00Z", "aws", "aws_odcr", "m5.large", "us-east-1", 4, 70000, 10.0),
    # the day before: 2 x 60 s: 0.0032
    row("2026-10-15T01:00:00Z", "aws", "aws_odcr", "m5.large", "us-east-1", 2, 1000, 1.0),
    # not billed: a failed attempt, a simulated row, a cleanup row, a row before the window
    row(
        "2026-10-16T03:00:00Z",
        "aws",
        "aws_odcr",
        "m5.large",
        "us-east-1",
        0,
        900,
        0.0,
        "no_capacity",
    ),  # fmt: skip
    row(
        "2026-10-16T03:30:00Z",
        "aws",
        "aws_odcr",
        "m5.large",
        "us-east-1",
        2,
        5000,
        3.0,
        simulated=True,
    ),  # fmt: skip
    {
        **row("2026-10-16T04:00:00Z", "aws", "aws_odcr", "m5.large", "us-east-1", 2, 5000, 3.0),
        "details": {"cleanup": True, "teardown": {"seconds": 3.0}},
    },  # fmt: skip
    row("2026-10-10T01:00:00Z", "aws", "aws_odcr", "m5.large", "us-east-1", 2, 5000, 3.0),
    # Azure: 2 x 75 s x 0.096 / 3600 = 0.004
    row(
        "2026-10-16T05:00:00Z",
        "azure",
        "az_capacity_reservation",
        "Standard_D2as_v4",
        "eastus",
        2,
        30000,
        45.0,
        placement_group_mode="n/a",
    ),  # fmt: skip
    # Azure, a size the price API does not list: price unknown
    row(
        "2026-10-16T06:00:00Z",
        "azure",
        "az_vmss_flex",
        "Standard_NC4as_T4_v3",
        "eastus",
        2,
        90000,
        20.0,
        placement_group_mode="single",
    ),  # fmt: skip
    # an AWS success whose teardown was not verified: priced at the recorded hold, 2 x 60 s
    {
        **row("2026-10-16T07:00:00Z", "aws", "aws_odcr", "m5.large", "us-east-1", 2, 5000, 3.0),
        "details": {"teardown": {"seconds": 3.0, "verified": False}},
    },
    # an AWS timeout: may have cost money, has no count of instances
    row(
        "2026-10-16T08:00:00Z",
        "aws",
        "aws_odcr",
        "m5.large",
        "us-east-1",
        None,
        60000,
        5.0,
        "timeout",
    ),  # fmt: skip
    # failed scale set rows with placed VMs: 1 VM at the 60 s floor, 2 VMs at (100 + 20) s
    {
        **row(
            "2026-10-16T09:00:00Z",
            "azure",
            "az_vmss_flex",
            "Standard_D2as_v4",
            "eastus",
            None,
            10000,
            0.0,
            "quota",
        ),
        "details": {"n_placed": 1, "teardown": {"seconds": 20.0}},
    },
    {
        **row(
            "2026-10-16T10:00:00Z",
            "azure",
            "az_vmss_flex",
            "Standard_D2as_v4",
            "eastus",
            None,
            100000,
            0.0,
            "timeout",
        ),
        "details": {"n_placed_at_timeout": 2, "teardown": {"seconds": 20.0}},
    },
    # a scale set no_capacity row that placed 0: nothing to price
    {
        **row(
            "2026-10-16T11:00:00Z",
            "azure",
            "az_vmss_flex",
            "Standard_D2as_v4",
            "eastus",
            0,
            1000,
            0.0,
            "no_capacity",
        ),
        "details": {"n_placed": 0, "teardown": {"seconds": 5.0}},
    },
    # an exception-path error row: no count of placed VMs anywhere
    row(
        "2026-10-16T12:00:00Z",
        "azure",
        "az_vmss_flex",
        "Standard_D2as_v4",
        "eastus",
        None,
        2000,
        4.0,
        "error",
    ),
    # an early error row (scale set not found): no teardown, no latency, nothing placed
    row(
        "2026-10-16T12:30:00Z",
        "azure",
        "az_vmss_flex",
        "Standard_D2as_v4",
        "eastus",
        None,
        0,
        None,
        "error",
    ),
    # a not-verified teardown with no count: not priced, and not a "priced row"
    {
        **row(
            "2026-10-16T13:00:00Z",
            "aws",
            "aws_odcr",
            "m5.large",
            "us-east-1",
            None,
            2000,
            4.0,
            "no_capacity",
        ),
        "details": {"teardown": {"seconds": 4.0, "verified": False}},
    },
]


class FakePricing:
    """boto3 `pricing` client that replays the recorded response for m5.large only."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_products(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        wanted = {f["Field"]: f["Value"] for f in kw["Filters"]}
        return AWS_PRICE if wanted["instanceType"] == "m5.large" else {"PriceList": []}


def azure_get(url: str) -> dict[str, Any]:
    flt = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["$filter"][0]
    return AZURE_PRICE if "Standard_D2as_v4" in flt else {**AZURE_PRICE, "Items": [], "Count": 0}


def write_store(root: Path) -> str:
    for i, r in enumerate(ROWS):
        d = root / f"experiment=ftg-test/provider={r['provider']}/probe_type={r['probe_type']}"
        d = d / f"date={r['ts_utc'][:10]}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{i}.jsonl").write_text(json.dumps(r) + "\n", encoding="utf-8")
    return f"file://{root}"


def test_real_response_shapes_give_the_list_price():
    fake = FakePricing()
    assert spend.aws_hourly_price(fake, "us-east-1", "m5.large") == pytest.approx(0.096)
    assert {f["Field"] for f in fake.calls[0]["Filters"]} >= {"instanceType", "regionCode"}
    assert spend.azure_hourly_price(azure_get, "eastus", "Standard_D2as_v4") == pytest.approx(0.096)


def test_no_price_raises_lookup_error():
    with pytest.raises(LookupError):
        spend.aws_hourly_price(FakePricing(), "us-east-1", "p5.48xlarge")
    with pytest.raises(LookupError):
        spend.azure_hourly_price(azure_get, "eastus", "Standard_NC4as_T4_v3")

    class Broken:
        def get_products(self, **kw: Any):
            raise RuntimeError("boom")

    with pytest.raises(LookupError, match="failed"):
        spend.aws_hourly_price(Broken(), "us-east-1", "m5.large")


def test_estimate_from_the_real_duckdb(tmp_path: Path, capsys):
    store = write_store(tmp_path / "store")
    cfg = tmp_path / "targets.yaml"
    cfg.write_text(CONFIG, encoding="utf-8")
    fake = FakePricing()
    pricers = {
        "aws": lambda region, size: spend.aws_hourly_price(fake, region, size),
        "azure": lambda region, size: spend.azure_hourly_price(azure_get, region, size),
    }
    code = spend.main(
        ["--aws-config", str(cfg), "--azure-config", str(cfg), "--aws-store", store,
         "--azure-store", store, "--days", "3"],
        pricers=pricers, today=lambda: TODAY,
    )  # fmt: skip
    out = capsys.readouterr().out
    assert code == 1  # a price lookup failed
    assert out.startswith("Method: n_placed x max(60 s, latency + teardown)")
    assert "| 2026-10-15 | aws | 1 | 120 | 0.0032 |" in out
    assert "| 2026-10-16 | aws | 3 | 560 | 0.0149 |" in out  # 0.0032 + 0.0085333 + 0.0032
    # azure: 150 s + 60 s + 240 s priced, and 220 s of the unknown size
    assert "| 2026-10-16 | azure | 4 | 670 | 0.0120 (+ 1 attempts, price unknown) |" in out
    assert "| aws | aws_odcr | m5.large | us-east-1 | 4 | 680 | 0.0181 |" in out
    assert (
        "| azure | az_capacity_reservation | Standard_D2as_v4 | eastus | 1 | 150 | 0.0040 |" in out
    )
    assert "| azure | az_vmss_flex | Standard_D2as_v4 | eastus | 2 | 300 | 0.0080 |" in out
    assert (
        "| azure | az_vmss_flex | Standard_NC4as_T4_v3 | eastus | 1 | 220 | price unknown |" in out
    )
    assert (
        "## Total, whole window (lower bound estimate): 0.0301 (+ 1 attempts, price unknown) USD "
        "(8 attempts)" in out
    )
    assert "Standard_NC4as_T4_v3: Azure: 0 list prices" in out
    assert "Stores: aws: 9 files read; azure: 7 files read" in out
    assert "## Not counted: the total is a lower bound" in out
    assert "- 1 aws_odcr timeout rows" in out
    assert "- 1 priced rows with a teardown that was not verified (120 instance-seconds" in out
    assert "- 1 rows with a teardown that was not verified and no count of instances" in out
    assert "- 1 az_vmss_flex failed rows with no count of placed VMs" in out
    assert "Incomplete:" not in out
    assert "- 1 cleanup rows" in out
    assert "Disks, NICs, and public IPs of scale-set VMs are not counted" in out.replace("\n", " ")
    assert "budget" not in out.lower()
    # one lookup for each size and Region
    assert len(fake.calls) == 1


def test_store_read_failure(tmp_path: Path, capsys):
    cfg = tmp_path / "targets.yaml"
    cfg.write_text(CONFIG, encoding="utf-8")

    def failing(*a: Any, **k: Any):
        import subprocess

        return subprocess.CompletedProcess(a[0], 1, "", "Catalog Error: nope")

    code = spend.main(["--aws-config", str(cfg), "--aws-store", f"file://{tmp_path}"],
                      run=failing, pricers={})  # fmt: skip
    assert code == 1
    out = capsys.readouterr().out
    assert "cannot read the store (duckdb failed: Catalog Error" in out
    assert "## Total, whole window (incomplete, estimate)" in out
    assert "Incomplete: the rows of aws are missing" in out


def test_config_without_store_is_incomplete(tmp_path: Path, capsys):
    store = write_store(tmp_path / "store")
    cfg = tmp_path / "targets.yaml"
    cfg.write_text(CONFIG, encoding="utf-8")
    fake = FakePricing()
    pricers = {"aws": lambda region, size: spend.aws_hourly_price(fake, region, size)}
    code = spend.main(
        ["--aws-config", str(cfg), "--azure-config", str(cfg), "--aws-store", store,
         "--days", "3"],
        pricers=pricers, today=lambda: TODAY,
    )  # fmt: skip
    out = capsys.readouterr().out
    assert code == 1
    assert "## Total, whole window (incomplete, " in out
    assert "Incomplete: the rows of azure are missing" in out
    assert "azure: a targets file is given but no store (--azure-store)" in out


def test_no_file_matched_warns_and_fails(tmp_path: Path, capsys):
    cfg = tmp_path / "targets.yaml"
    cfg.write_text(CONFIG, encoding="utf-8")
    empty = tmp_path / "empty"
    empty.mkdir()
    code = spend.main(
        ["--aws-config", str(cfg), "--aws-store", f"file://{empty}"],
        pricers={}, today=lambda: TODAY,
    )  # fmt: skip
    out = capsys.readouterr()
    assert code == 1
    assert "Stores: aws: 0 files read; azure: not read" in out.out
    assert "aws: the store matched no file in the window" in out.err
    assert "lower bound" not in out.out


def test_price_error_keeps_the_provider_message():
    class Denied:
        def get_products(self, **kw: Any):
            raise RuntimeError("AccessDeniedException: not authorized for pricing:GetProducts")

    with pytest.raises(LookupError, match="AccessDeniedException: not authorized"):
        spend.aws_hourly_price(Denied(), "us-east-1", "m5.large")


def test_aws_price_follows_next_token():
    class Paged:
        def __init__(self) -> None:
            self.tokens: list[Any] = []

        def get_products(self, **kw: Any) -> dict[str, Any]:
            self.tokens.append(kw.get("NextToken"))
            if kw.get("NextToken") is None:
                return {"PriceList": [], "NextToken": "page2"}
            return AWS_PRICE

    fake = Paged()
    assert spend.aws_hourly_price(fake, "us-east-1", "m5.large") == pytest.approx(0.096)
    assert fake.tokens == [None, "page2"]


def _store_of(root: Path, rows: list[dict[str, Any]]) -> str:
    for i, r in enumerate(rows):
        d = root / f"experiment=ftg-test/provider={r['provider']}/probe_type={r['probe_type']}"
        d = d / f"date={r['ts_utc'][:10]}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{i}.jsonl").write_text(json.dumps(r) + "\n", encoding="utf-8")
    return f"file://{root}"


def _azure_main(tmp_path: Path, rows: list[dict[str, Any]], capsys):
    cfg = tmp_path / "targets.yaml"
    cfg.write_text(CONFIG, encoding="utf-8")
    pricers = {"azure": lambda region, size: spend.azure_hourly_price(azure_get, region, size)}
    code = spend.main(["--azure-config", str(cfg), "--azure-store", _store_of(tmp_path / "s", rows),
                       "--days", "3"], pricers=pricers, today=lambda: TODAY)  # fmt: skip
    return code, capsys.readouterr().out


def test_unverified_pre_teardown_is_not_counted_as_verified(tmp_path: Path, capsys):
    r = row("2026-10-16T05:00:00Z", "azure", "az_vmss_flex", "Standard_D2as_v4", "eastus", 2,
            30000, 45.0)  # fmt: skip
    r["details"]["pre_teardown"] = {"seconds": 5.0, "verified": False}
    code, out = _azure_main(tmp_path, [r], capsys)
    assert code == 0
    assert "- 1 priced rows with a teardown that was not verified" in out
    assert "lower bound" in out


def test_call_deadline_skip_row_is_not_a_missing_count(tmp_path: Path, capsys):
    r = row("2026-10-16T05:00:00Z", "azure", "az_vmss_flex", "Standard_D2as_v4", "eastus", 0, 0,
            4.0, "error")  # fmt: skip
    r["n_placed"] = None
    r["details"] = {"skipped": True, "reason": "call deadline",
                    "pre_teardown": {"seconds": 4.0, "verified": True},
                    "teardown": {"seconds": 4.0, "verified": True}}  # fmt: skip
    code, out = _azure_main(tmp_path, [r], capsys)
    assert code == 0
    assert "no count of placed VMs" not in out
    assert "Not counted" not in out


def test_other_cloud_without_a_real_probe_is_not_unread(tmp_path: Path, capsys):
    store = write_store(tmp_path / "store")
    aws_cfg = tmp_path / "aws.yaml"
    aws_cfg.write_text(CONFIG, encoding="utf-8")
    sim_cfg = tmp_path / "azure.yaml"
    sim_cfg.write_text(
        "experiment: ftg-test\nazure:\n  provisioning_probes:\n    az_vmss_flex:\n"
        "      - {sku: Standard_D2as_v4, region: eastus, zone: null, n: 2, simulate: true}\n",
        encoding="utf-8",
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    fake = FakePricing()
    code = spend.main(
        ["--aws-config", str(aws_cfg), "--azure-config", str(sim_cfg), "--aws-store", store,
         "--azure-store", f"file://{empty}", "--days", "3"],
        pricers={"aws": lambda region, size: spend.aws_hourly_price(fake, region, size)},
        today=lambda: TODAY,
    )  # fmt: skip
    out = capsys.readouterr()
    assert code == 0
    assert "Incomplete" not in out.out
    assert "incomplete" not in out.out
    assert out.err == ""


def test_store_without_config_says_so(tmp_path: Path, capsys):
    store = write_store(tmp_path / "store")
    cfg = tmp_path / "targets.yaml"
    cfg.write_text(CONFIG, encoding="utf-8")
    fake = FakePricing()
    code = spend.main(
        ["--aws-config", str(cfg), "--aws-store", store, "--azure-store", store, "--days", "3"],
        pricers={"aws": lambda region, size: spend.aws_hourly_price(fake, region, size)},
        today=lambda: TODAY,
    )  # fmt: skip
    out = capsys.readouterr()
    assert code == 1
    assert "azure: a store is given but no targets file (--azure-config)" in out.err
    assert "Incomplete: the rows of azure are missing" in out.out


SIM_AZURE = (
    "experiment: ftg-test\nazure:\n  provisioning_probes:\n    az_vmss_flex:\n"
    "      - {sku: Standard_D2as_v4, region: eastus, zone: null, n: 2, simulate: true}\n"
)
REAL_VMSS = row("2026-10-16T05:00:00Z", "azure", "az_vmss_flex", "Standard_D2as_v4", "eastus", 2,
                30000, 45.0)  # fmt: skip


@pytest.mark.parametrize("text", [SIM_AZURE, "experiment: ftg-test\n"])
def test_real_rows_are_counted_when_the_targets_file_lists_no_real_probe(
    tmp_path: Path, capsys, text: str
):
    cfg = tmp_path / "azure.yaml"
    cfg.write_text(text, encoding="utf-8")
    store = _store_of(tmp_path / "s", [REAL_VMSS])
    pricers = {"azure": lambda region, size: spend.azure_hourly_price(azure_get, region, size)}
    code = spend.main(["--azure-config", str(cfg), "--azure-store", store, "--days", "3"],
                      pricers=pricers, today=lambda: TODAY)  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0
    assert "(1 attempts)" in out
    assert "azure: targets file lists no real provisioning probe; counted 1 real rows" in out
    assert "azure: not read" not in out
    assert "incomplete" not in out


def test_no_real_probe_and_no_store_is_a_silent_skip(tmp_path: Path, capsys):
    store = write_store(tmp_path / "store")
    aws_cfg = tmp_path / "aws.yaml"
    aws_cfg.write_text(CONFIG, encoding="utf-8")
    sim_cfg = tmp_path / "azure.yaml"
    sim_cfg.write_text(SIM_AZURE, encoding="utf-8")
    fake = FakePricing()
    code = spend.main(
        ["--aws-config", str(aws_cfg), "--azure-config", str(sim_cfg), "--aws-store", store,
         "--days", "3"],
        pricers={"aws": lambda region, size: spend.aws_hourly_price(fake, region, size)},
        today=lambda: TODAY,
    )  # fmt: skip
    out = capsys.readouterr()
    assert code == 0
    assert "incomplete" not in out.out.lower()
    assert out.err == ""


def test_all_simulated_configs_with_stores_read_the_stores(tmp_path: Path, capsys):
    cfg = tmp_path / "sim.yaml"
    cfg.write_text(SIM_AZURE + "aws:\n  provisioning_probes:\n"
                   "    - {instance_type: m5.large, region: us-east-1, zone: null, n: 2,"
                   " simulate: true}\n", encoding="utf-8")  # fmt: skip
    empty = tmp_path / "empty"
    empty.mkdir()
    code = spend.main(
        ["--aws-config", str(cfg), "--azure-config", str(cfg), "--aws-store", f"file://{empty}",
         "--azure-store", f"file://{empty}", "--days", "3"],
        pricers={}, today=lambda: TODAY,
    )  # fmt: skip
    out = capsys.readouterr()
    assert code == 0
    assert "give a targets file and a store" not in out.err
    assert "Stores: aws: 0 files read; azure: 0 files read" in out.out


def test_all_simulated_config_with_no_matching_file_names_the_experiment(tmp_path: Path, capsys):
    cfg = tmp_path / "azure.yaml"
    cfg.write_text(SIM_AZURE, encoding="utf-8")
    empty = tmp_path / "empty"
    empty.mkdir()
    code = spend.main(
        ["--azure-config", str(cfg), "--azure-store", f"file://{empty}", "--days", "3"],
        pricers={}, today=lambda: TODAY,
    )  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0
    assert "azure: the store matched no file for experiment `ftg-test` in the window" in out
