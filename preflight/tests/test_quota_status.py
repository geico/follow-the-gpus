"""Tests of `python -m ftg_preflight.quota status` with a fake az. No network, no credentials."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ftg_preflight import quota, quota_status
from ftg_preflight.azure import AzError
from tests.test_quota import CONFIG, FakeAz, location_restriction, sku

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


def request(name: str, value: int, state: str, month_day: str = "10-13") -> dict:
    """1 item of `az quota request list`: the fields are at the top level."""
    return {
        "name": f"id-{name}-{month_day}",
        "provisioningState": state,
        "requestSubmitTime": f"2026-{month_day}T00:10:00Z",
        "value": [{"name": {"value": name}, "limit": {"value": value}}],
    }


def ticket(number: str, region: str, date: str = "10-13", status: str = "Open") -> dict:
    """1 item of `az support in-subscription tickets list`."""
    return {
        "supportTicketId": number,
        "name": f"ftg-gpu-quota-{region}-2026{date.replace('-', '')}",
        "title": "Quota request",
        "status": status,
        "createdDate": f"2026-{date}T08:00:00Z",
    }


class StatusAz(FakeAz):
    """The quota fake plus the request list and the support tickets."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: dict[str, Any] = {
            "eastus": [
                request("CORES", 500, "Succeeded"),
                request("STANDARDNCADSH100V5FAMILY", 320, "Failed"),
                request("STANDARDNCADSH100V5FAMILY", 320, "InProgress", "09-01"),
            ],
            "westus2": [request("STANDARD NCASV3_T4 FAMILY", 128, "Succeeded")],
        }
        self.limits[("eastus", "cores")] = 500
        self.limits[("westus2", "Standard NCASv3_T4 Family")] = 128
        self.tickets: list[dict] = [ticket("1000", "eastus"), ticket("2000", "westus2")]
        self.active = 0
        self.overlap = False

    def __call__(self, *args: str) -> Any:
        self.active += 1
        self.overlap |= self.active > 1
        try:
            if args[:2] == ("extension", "show") and args[3] == "support":
                return {"name": "support"}
            if args[:3] == ("quota", "request", "list"):
                region = args[args.index("--scope") + 1].rsplit("/", 1)[1]
                result = self.requests[region]
                if isinstance(result, Exception):
                    raise result
                return result
            if args[0] == "support":
                return self.tickets
            return super().__call__(*args)
        finally:
            self.active -= 1


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "targets.yaml"
    path.write_text(CONFIG)
    return path


def status(config: Path, az: Any, *extra: str, sleep=lambda s: None) -> int:
    return quota_status.main(
        ["--config", str(config), "--subscription", "sub-0", *extra], az=az, now=NOW, sleep=sleep
    )


def cells(out: str) -> list[list[str]]:
    return [line.split("  ") for line in out.splitlines()]


def test_the_default_table_has_6_columns_and_a_row_for_each_configured_size(config, capsys):
    az = StatusAz()
    assert status(config, az) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["REGION", "GPU", "ASKED", "(GPUs", "/", "vCPU)", "NOW", "(vCPU)",
                                "STATE", "TICKET"]  # fmt: skip
    rows = [line.split() for line in lines[1:]]
    assert rows == [
        # the latest H100 request failed; the older one (09-01) does not count
        ["eastus", "H100", "8", "/", "320", "0", "failed", "1000"],
        ["eastus", "T4", "32", "/", "128", "0", "—", "1000"],
        ["eastus", "cores", "—", "/", "500", "500", "granted", "1000"],
        ["westus2", "H100", "8", "/", "320", "0", "—", "2000"],
        ["westus2", "T4", "32", "/", "128", "128", "granted", "2000"],
        ["westus2", "RTX", "PRO", "6000", "32", "/", "1152", "0", "—", "2000"],
    ]


def test_a_restricted_size_without_a_request_is_restricted(config, capsys):
    az = StatusAz()
    az.skus["westus2"][0] = sku(
        "Standard_NC40ads_H100_v5", "westus2", [location_restriction("westus2")]
    )
    assert status(config, az) == 0
    row = next(r for r in capsys.readouterr().out.splitlines() if "H100" in r and "westus2" in r)
    assert row.split() == ["westus2", "H100", "8", "/", "320", "—", "restricted", "2000"]


def test_a_request_state_wins_over_restricted_and_pending_is_pending(config, capsys):
    az = StatusAz()
    az.skus["eastus"][0] = sku(
        "Standard_NC40ads_H100_v5", "eastus", [location_restriction("eastus")]
    )
    az.requests["eastus"][1] = request("STANDARDNCADSH100V5FAMILY", 320, "Accepted")
    assert status(config, az) == 0
    row = next(r for r in capsys.readouterr().out.splitlines() if "H100" in r and "eastus" in r)
    assert row.split()[-2:] == ["pending", "1000"]


def test_the_region_ticket_is_the_newest_with_the_region_prefix(config, capsys):
    az = StatusAz()
    az.tickets = [
        ticket("1", "eastus", "10-01"),
        ticket("2", "eastus", "10-12"),
        ticket("3", "eastus2", "10-13"),  # another region: eastus2 is not eastus
        {**ticket("4", "eastus"), "name": "other", "title": "FTG-GPU-QUOTA-westus2-x"},
    ]
    assert status(config, az) == 0
    out = capsys.readouterr().out.splitlines()
    assert {r.split()[-1] for r in out if r.startswith("eastus")} == {"2"}
    assert {r.split()[-1] for r in out if r.startswith("westus2")} == {"4"}


def test_no_ticket_shows_a_dash(config, capsys):
    az = StatusAz()
    az.tickets = []
    assert status(config, az) == 0
    assert all(r.endswith("—") for r in capsys.readouterr().out.splitlines()[1:])


def test_a_failed_region_keeps_the_other_regions_and_exits_1(config, capsys):
    az = StatusAz()
    az.requests["eastus"] = AzError("boom\nsecond line", "")
    assert status(config, az) == 1
    captured = capsys.readouterr()
    assert "westus2" in captured.out and "eastus" not in captured.out
    assert captured.err.strip().splitlines() == [
        "quota status: eastus: a read call failed (boom second line)"
    ]


def test_a_failed_ticket_read_is_an_error_but_lists_the_rows(config, capsys):
    az = StatusAz()
    az.tickets = AzError("no support plan", "")  # type: ignore[assignment]

    def call(*args: str) -> Any:
        if args[0] == "support":
            raise az.tickets  # type: ignore[misc]
        return StatusAz.__call__(az, *args)

    assert status(config, call) == 1
    captured = capsys.readouterr()
    assert "westus2" in captured.out and "cannot read the support tickets" in captured.err


def test_the_az_calls_run_one_at_a_time(config):
    az = StatusAz()
    assert status(config, az) == 0
    assert not az.overlap
    assert not hasattr(quota, "ThreadPoolExecutor") and not hasattr(
        quota_status, "ThreadPoolExecutor"
    )


def test_a_call_that_fails_on_the_msal_token_cache_is_retried_once(config, capsys):
    az = StatusAz()
    failed = []
    slept = []

    def call(*args: str) -> Any:
        if args[:3] == ("quota", "request", "list") and not failed:
            failed.append(args)
            raise AzError("User x does not exist in MSAL token cache. Run az login.", "")
        return az(*args)

    assert status(config, call, sleep=slept.append) == 0
    assert slept == [2]
    assert "eastus" in capsys.readouterr().out


def test_another_error_is_not_retried():
    calls = []

    def call(*args: str) -> Any:
        calls.append(args)
        raise AzError("other", "")

    with pytest.raises(AzError):
        quota.retrying(call, lambda s: calls.append("sleep"))("x")
    assert calls == [("x",)]
    # a second MSAL failure is raised
    msal = [AzError("MSAL token cache", "")] * 2

    def twice(*args: str) -> Any:
        raise msal.pop()

    with pytest.raises(AzError):
        quota.retrying(twice, lambda s: None)("x")
    assert msal == []


def test_markdown_groups_by_region_with_the_ticket(config, capsys):
    az = StatusAz()
    az.tickets = [ticket("1000", "eastus", status="Closed")]
    assert status(config, az, "--format", "markdown") == 0
    out = capsys.readouterr().out
    assert out.startswith("# Azure GPU quota status, 2026-10-13\n")
    assert "## eastus — ticket 1000 (Closed)\n" in out
    assert "## westus2 — no ticket\n" in out
    assert "| H100 | 8 / 320 | 0 | failed |" in out
    assert "| cores | — / 500 | 500 | granted |" in out
    assert out.index("## eastus") < out.index("| T4 |") < out.index("## westus2")


def test_json_lists_the_rows(config, capsys):
    az = StatusAz()
    assert status(config, az, "--format", "json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["rows"][0]["state"] == "failed" and data["rows"][0]["ticket"] == "1000"


def test_missing_support_extension_stops_with_the_install_command(config, capsys):
    az = StatusAz()

    def call(*args: str) -> Any:
        if args[:2] == ("extension", "show") and args[3] == "support":
            raise AzError("The extension support is not installed.", "")
        return az(*args)

    assert status(config, call) == 2
    assert "az extension add --name support" in capsys.readouterr().err


def test_quota_main_dispatches_status(config, monkeypatch):
    seen = {}
    monkeypatch.setattr(quota_status, "main", lambda argv, az: seen.setdefault("argv", argv) and 0)
    quota.main(["status", "--config", str(config)], az=StatusAz())
    assert seen["argv"] == ["--config", str(config)]


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_make_quota_status_md_runs_the_markdown_format():
    out = subprocess.run(
        ["make", "-n", "quota/status/md"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    assert "ftg_preflight.quota status" in out and "--format markdown" in out
