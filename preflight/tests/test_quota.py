"""Tests of `python -m ftg_preflight.quota` with a fake az. No network, no credentials."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from ftg_preflight import quota
from ftg_preflight.azure import AzError

SUB = "test-subscription"
SPOTN = "lowPriorityCores"
CONFIG = """
azure:
  jobs:
    - {module: az_spot_score, regions: [eastus, westus2, centralus]}
  quota:
    regions: [eastus, westus2]
    sizes:
      - {size: Standard_NC40ads_H100_v5, count: 8}
      - {size: Standard_NC4as_T4_v3, count: 32}
      - {size: Standard_NC144ds_xl_RTXPRO6000BSE_v6, count: 8, regions: [westus2]}
"""
FAMILIES = {
    "Standard_NC40ads_H100_v5": ("StandardNCadsH100v5Family", 40),
    "Standard_NC4as_T4_v3": ("Standard NCASv3_T4 Family", 4),
    "Standard_NC144ds_xl_RTXPRO6000BSE_v6": ("StandardNCdsRTXPRO6000BSEv6XLFamily", 144),
}


GPUS = {"Standard_NC40ads_H100_v5": 1, "Standard_NC4as_T4_v3": 1}


def sku(size: str, region: str, restrictions: list[Any] | None = None) -> dict[str, Any]:
    family, vcpus = FAMILIES[size]
    return {
        "resourceType": "virtualMachines",
        "name": size,
        "family": family,
        "locations": [region],
        "capabilities": [
            {"name": "vCPUs", "value": str(vcpus)},
            {"name": "GPUs", "value": str(GPUS.get(size, 4))},
        ],
        "restrictions": restrictions or [],
    }


def location_restriction(region: str) -> dict[str, Any]:
    return {
        "type": "Location",
        "reasonCode": "NotAvailableForSubscription",
        "values": [region],
        "restrictionInfo": {"locations": [region], "zones": None},
    }


def zone_restriction(region: str) -> dict[str, Any]:
    return {
        "type": "Zone",
        "reasonCode": "NotAvailableForSubscription",
        "values": [region],
        "restrictionInfo": {"locations": [region], "zones": ["1"]},
    }


class FakeAz:
    """Answers the az calls of the quota command and records them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.extension = True
        self.vnets = ["eastus", "westus2"]
        self.skus = {
            "eastus": [sku(s, "eastus") for s in FAMILIES],
            "westus2": [sku(s, "westus2") for s in FAMILIES],
        }
        # (region, resource name) -> limit; usage of cores per region.
        self.limits: dict[tuple[str, str], int] = {}
        self.usage = {"eastus": 4, "westus2": 0}
        self.fail_update: set[str] = set()

    def __call__(self, *args: str) -> Any:
        self.calls.append(args)
        if args[:2] == ("extension", "show"):
            if not self.extension:
                raise AzError("The extension quota is not installed.", "")
            return {"name": "quota"}
        if args[:2] == ("extension", "add"):
            self.extension = True
            return None
        if args[:2] == ("account", "show"):
            return {"id": SUB}
        if args[:3] == ("network", "vnet", "list"):
            return list(self.vnets)
        if args[0] == "rest":
            url = args[args.index("--url") + 1]
            region = next(r for r in ("eastus", "westus2") if f"%27{r}%27" in url)
            return {"value": self.skus[region], "nextLink": None}
        if args[0] == "quota":
            scope = args[args.index("--scope") + 1]
            region = scope.rsplit("/", 1)[1]
            name = args[args.index("--resource-name") + 1]
            if args[1] == "show":
                return {"properties": {"limit": {"value": self.limits.get((region, name), 0)}}}
            if args[1:3] == ("usage", "show"):
                return {"properties": {"usages": {"value": self.usage[region]}}}
            if args[1] == "update":
                if name in self.fail_update:
                    raise AzError("QuotaExceeded", "QuotaExceeded")
                return {"properties": {"provisioningState": "Succeeded"}}
        raise AssertionError(f"unexpected az call: {args}")

    def updates(self) -> list[tuple[str, str, str]]:
        """(region, resource name, limit object) of each `az quota update` call."""
        out = []
        for c in self.calls:
            if c[:2] == ("quota", "update"):
                region = c[c.index("--scope") + 1].rsplit("/", 1)[1]
                out.append((region, c[c.index("--resource-name") + 1], c[-1]))
        return out


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "targets.yaml"
    path.write_text(CONFIG)
    return path


@pytest.fixture(autouse=True)
def no_env_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
    monkeypatch.delenv("ARM_SUBSCRIPTION_ID", raising=False)


def run(config: Path, az: FakeAz, *extra: str) -> int:
    return quota.main(["--config", str(config), *extra], az=az)


def test_cores_target_rounds_up_to_100():
    assert quota.cores_target(4, 1600) == 1700
    assert quota.cores_target(0, 1600) == 1600
    assert quota.cores_target(0, 1) == 100


def test_dry_run_prints_the_requests_and_sends_nothing(config, capsys):
    az = FakeAz()
    assert run(config, az) == 0
    out = capsys.readouterr().out
    assert az.updates() == []
    assert "dry run" in out
    scope = f"/subscriptions/{SUB}/providers/Microsoft.Compute/locations/eastus"
    # eastus: 8 x 40 + 32 x 4 = 448 family vCPUs, usage 4 -> 500.
    assert (
        f"az quota update --scope {scope} --resource-name cores --resource-type dedicated "
        "--limit-object value=500" in out
    )
    # The family name with spaces is quoted, so the line can be pasted into a shell.
    assert "--resource-name 'Standard NCASv3_T4 Family'" in out
    assert "value=1152" in out  # RTX PRO 6000 in westus2: 8 x 144
    # The regional Spot quota: the vCPUs of all sizes of the region (spec 0011 rule 1.1).
    assert "--resource-name lowPriorityCores --limit-object value=448" in out
    assert "## 9 requests to send, 0 errors." in out


def test_cores_requests_come_before_the_family_requests(config):
    az = FakeAz()
    assert run(config, az, "--apply") == 0
    names = [name for _, name, _ in az.updates()]
    assert names[:2] == ["cores", "cores"]
    assert "cores" not in names[2:]
    assert names[-2:] == ["lowPriorityCores", "lowPriorityCores"]
    assert len(names) == 9


def test_apply_sends_the_right_commands(config, capsys):
    az = FakeAz()
    assert run(config, az, "--apply") == 0
    assert az.updates() == [
        ("eastus", "cores", "value=500"),
        ("westus2", "cores", "value=1600"),
        ("eastus", "StandardNCadsH100v5Family", "value=320"),
        ("eastus", "Standard NCASv3_T4 Family", "value=128"),
        ("westus2", "StandardNCadsH100v5Family", "value=320"),
        ("westus2", "Standard NCASv3_T4 Family", "value=128"),
        ("westus2", "StandardNCdsRTXPRO6000BSEv6XLFamily", "value=1152"),
        ("eastus", "lowPriorityCores", "value=448"),
        ("westus2", "lowPriorityCores", "value=1600"),
    ]
    spot = next(c for c in az.calls if c[:2] == ("quota", "update") and "lowPriorityCores" in c)
    assert "--resource-type" not in spot
    first = next(c for c in az.calls if c[:2] == ("quota", "update"))
    assert first[first.index("--resource-type") + 1] == "dedicated"
    assert "## 9 requests sent, 0 errors." in capsys.readouterr().out


def test_a_location_restricted_size_is_skipped(config, capsys):
    az = FakeAz()
    az.skus["eastus"][0] = sku(
        "Standard_NC40ads_H100_v5", "eastus", [location_restriction("eastus")]
    )
    assert run(config, az, "--apply") == 0
    assert ("eastus", "StandardNCadsH100v5Family", "value=320") not in az.updates()
    # The eastus cores target counts only the T4 ask: 4 + 128 -> 200.
    assert ("eastus", "cores", "value=200") in az.updates()
    out = capsys.readouterr().out
    assert "# eastus Standard_NC40ads_H100_v5: NotAvailableForSubscription; skip" in out


def test_the_spot_request_sums_the_vcpus_of_the_sizes_that_stay(config):
    az = FakeAz()
    az.skus["eastus"][0] = sku(
        "Standard_NC40ads_H100_v5", "eastus", [location_restriction("eastus")]
    )
    az.limits[("westus2", SPOTN)] = 1600
    assert run(config, az, "--apply") == 0
    # eastus: only the T4 ask stays, 32 x 4 = 128. westus2 already has enough.
    assert ("eastus", SPOTN, "value=128") in az.updates()
    assert ("westus2", SPOTN, "value=1600") not in az.updates()


def test_a_zone_restriction_is_not_a_skip(config):
    az = FakeAz()
    az.skus["eastus"][0] = sku("Standard_NC40ads_H100_v5", "eastus", [zone_restriction("eastus")])
    assert run(config, az, "--apply") == 0
    assert ("eastus", "StandardNCadsH100v5Family", "value=320") in az.updates()


def test_a_size_missing_from_the_sku_list_is_skipped(config, capsys):
    az = FakeAz()
    az.skus["westus2"] = az.skus["westus2"][:2]
    assert run(config, az) == 0
    assert "# westus2 Standard_NC144ds_xl_RTXPRO6000BSE_v6: not offered; skip" in (
        capsys.readouterr().out
    )


def test_a_region_with_no_vnet_is_warned_and_still_requested(config, capsys):
    az = FakeAz()
    az.vnets = ["eastus"]
    assert run(config, az, "--apply") == 0
    assert ("westus2", "cores", "value=1600") in az.updates()
    assert len(az.updates()) == 9
    out = capsys.readouterr().out
    assert "# warning: no VNet in westus2; provision one before probes run" in out
    assert "Regions with no VNet: westus2" in out


def test_require_vnet_skips_a_region_with_no_vnet(config, capsys):
    az = FakeAz()
    az.vnets = ["eastus"]
    assert run(config, az, "--apply", "--require-vnet") == 0
    assert {region for region, _, _ in az.updates()} == {"eastus"}
    assert "# westus2: no virtual network in the subscription; skip (--require-vnet)" in (
        capsys.readouterr().out
    )


def test_a_quota_that_is_already_high_enough_is_skipped(config, capsys):
    az = FakeAz()
    az.limits[("eastus", "StandardNCadsH100v5Family")] = 320
    az.limits[("eastus", "cores")] = 1000
    assert run(config, az, "--apply") == 0
    updates = az.updates()
    assert ("eastus", "StandardNCadsH100v5Family", "value=320") not in updates
    assert ("eastus", "cores", "value=500") not in updates
    out = capsys.readouterr().out
    assert "# eastus 'cores': limit 1000 >= target 500; skip" in out
    assert "# eastus 'StandardNCadsH100v5Family': limit 320 >= target 320; skip" in out


def test_missing_extension_stops_with_the_install_command(config, capsys):
    az = FakeAz()
    az.extension = False
    assert run(config, az, "--apply") == 2
    err = capsys.readouterr().err
    assert "az extension add --name quota" in err
    assert az.calls == [("extension", "show", "--name", "quota")]


def test_install_extension_flag_installs_it(config):
    az = FakeAz()
    az.extension = False
    assert run(config, az, "--install-extension") == 0
    assert ("extension", "add", "--name", "quota") in az.calls


def test_a_failed_cores_request_skips_the_families_of_its_region(config, capsys):
    az = FakeAz()
    az.fail_update = {"cores"}
    assert run(config, az, "--apply") == 1
    # The Spot request does not depend on cores.
    assert [n for _, n, _ in az.updates()] == ["cores", "cores", SPOTN, SPOTN]
    assert "the cores request failed; skip" in capsys.readouterr().out


def test_subscription_comes_from_the_flag_or_the_environment(config, monkeypatch):
    az = FakeAz()
    assert run(config, az, "--subscription", "flag-sub") == 0
    assert ("account", "show") not in az.calls
    monkeypatch.setenv("ARM_SUBSCRIPTION_ID", "env-sub")
    az = FakeAz()
    assert run(config, az) == 0
    assert ("account", "show") not in az.calls
    assert any("/subscriptions/env-sub/" in " ".join(c) for c in az.calls)


def test_missing_quota_section_exits_2(tmp_path, capsys):
    path = tmp_path / "targets.yaml"
    path.write_text("azure:\n  regions: [eastus]\n")
    assert quota.main(["--config", str(path)], az=FakeAz()) == 2
    assert "no azure.quota section" in capsys.readouterr().err


def test_real_az_cli_without_the_extension(config, tmp_path, monkeypatch, capsys):
    """The real az_cli path, with a fake `az` program on PATH that has no quota extension."""
    fake = tmp_path / "bin" / "az"
    fake.parent.mkdir()
    fake.write_text('#!/bin/sh\necho "ERROR: The extension quota is not installed." >&2\nexit 1\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    assert quota.main(["--config", str(config)]) == 2
    assert "Install it with: az extension add --name quota" in capsys.readouterr().err


@pytest.mark.parametrize(
    "bad",
    [
        "regions: eastus\n    sizes: [{size: A, count: 1}]",
        "regions: [eastus]\n    sizes: [{size: A, count: 0}]",
        "regions: [eastus]\n    sizes: [{size: A, count: 1}, {size: A, count: 2}]",
        "regions: [eastus]\n    sizes: [{size: A, count: 1, regions: [westus]}]",
        "regions: [eastus]\n    sizes: [{size: [A], count: 1}]",
        "regions: [westus9]\n    sizes: [{size: A, count: 1}]",
        "regions: [eastus]\n    sizes: []",
        'regions: [""]\n    sizes: [{size: A, count: 1}]',
        'regions: [eastus]\n    sizes: [{size: "", count: 1}]',
        'regions: [eastus]\n    sizes: [{size: A, count: 1, regions: [""]}]',
        'regions: [eastus, "East US"]\n    sizes: [{size: A, count: 1}]',
        'regions: [eastus, "east us"]\n    sizes: [{size: A, count: 1}]',
    ],
)
def test_a_bad_quota_section_exits_2(tmp_path, capsys, bad):
    path = tmp_path / "targets.yaml"
    path.write_text(f"azure:\n  regions: [eastus, westus, East US, east us]\n  quota:\n    {bad}\n")
    az = FakeAz()
    assert quota.main(["--config", str(path)], az=az) == 2
    assert "is not valid" in capsys.readouterr().err
    assert az.calls == []


def test_a_region_with_a_trailing_newline_exits_2(tmp_path, capsys):
    path = tmp_path / "targets.yaml"
    path.write_text(
        'azure:\n  jobs: [{module: az_spot_score, regions: [eastus, "eastus\\n"]}]\n  quota:\n'
        '    regions: [eastus, "eastus\\n"]\n    sizes: [{size: A, count: 1}]\n'
    )
    az = FakeAz()
    assert quota.main(["--config", str(path)], az=az) == 2
    assert "lowercase letters and digits only" in capsys.readouterr().err
    assert az.calls == []


def test_a_failed_vnet_read_warns_and_counts_an_error(config, capsys, monkeypatch):
    az = FakeAz()

    def no_vnets(*_: Any) -> Any:
        raise AzError("AuthorizationFailed", "AuthorizationFailed")

    monkeypatch.setattr(quota, "vnet_regions", no_vnets)
    assert run(config, az) == 1
    out = capsys.readouterr().out
    assert "# warning: cannot read the virtual networks" in out
    assert "## 9 requests to send, 1 errors." in out
    assert run(config, az, "--require-vnet") == 2


@pytest.mark.parametrize("broken", ["usage", "cores limit"])
def test_unknown_cores_state_skips_the_families_of_the_region(config, capsys, broken):
    az = FakeAz()

    def flaky(*args: str) -> Any:
        if args[0] == "quota" and "eastus" in args[args.index("--scope") + 1].rsplit("/", 1)[1]:
            name = args[args.index("--resource-name") + 1]
            if broken == "usage" and args[1] == "usage":
                raise AzError("throttled", "")
            if broken == "cores limit" and args[1] == "show" and name == "cores":
                raise AzError("throttled", "")
        return az(*args)

    assert quota.main(["--config", str(config), "--apply"], az=flaky) == 1
    assert {r for r, n, _ in az.updates() if n != SPOTN} == {"westus2"}
    out = capsys.readouterr().out
    assert "# eastus 'StandardNCadsH100v5Family': the cores state is unknown; skip" in out


def test_a_sku_without_a_family_is_skipped_with_a_warning(config, capsys):
    az = FakeAz()
    del az.skus["eastus"][0]["family"]
    assert run(config, az, "--apply") == 0
    assert ("eastus", "StandardNCadsH100v5Family", "value=320") not in az.updates()
    assert "# eastus Standard_NC40ads_H100_v5: no family in the SKU list; skip" in (
        capsys.readouterr().out
    )


def test_an_unexpected_az_shape_is_a_clean_error(config, capsys):
    az = FakeAz()
    az.skus["eastus"] = "not a list"
    assert run(config, az) == 1
    assert "# eastus: cannot read the SKU list" in capsys.readouterr().out


def test_az_cli_turns_non_json_output_into_an_az_error(tmp_path, monkeypatch):
    from ftg_preflight.azure import az_cli

    fake = tmp_path / "bin" / "az"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\necho 'WARNING: not json'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(AzError, match="not JSON"):
        az_cli("account", "show")


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        ("regions: [eastus, eastus]\n    sizes: [{size: A, count: 1}]", "listed twice"),
        (
            "regions: [eastus]\n    sizes: [{size: A, count: 1, regions: [eastus, eastus]}]",
            "listed twice",
        ),
        (
            "regions: [eastus]\n    sizes: [{size: Std_A, count: 1}, {size: std_a, count: 2}]",
            "unique",
        ),
    ],
)
def test_a_duplicate_region_or_size_exits_2(tmp_path, capsys, bad, reason):
    path = tmp_path / "targets.yaml"
    path.write_text(
        f"azure:\n  jobs: [{{module: az_spot_score, regions: [eastus]}}]\n  quota:\n    {bad}\n"
    )
    assert quota.main(["--config", str(path)], az=FakeAz()) == 2
    err = capsys.readouterr().err
    assert "is not valid" in err
    assert reason in err


def test_send_never_sends_the_same_resource_twice(config, monkeypatch, capsys):
    regions, sizes = quota.load_quota(config)
    monkeypatch.setattr(quota, "load_quota", lambda path: (regions + ["eastus"], sizes))
    az = FakeAz()
    assert run(config, az, "--apply") == 0
    updates = az.updates()
    assert len(updates) == len(set(updates)) == 9
    assert "already requested in this run; skip" in capsys.readouterr().out


def test_a_failed_spot_read_skips_that_request_and_counts_an_error(config, capsys):
    """Spec 0011 rule 12: a failed Spot read skips that request and counts as an error."""
    az = FakeAz()

    def flaky(*args: str) -> Any:
        show = args[:2] == ("quota", "show")
        if show and SPOTN in args and args[args.index("--scope") + 1].endswith("/eastus"):
            raise AzError("throttled", "")
        return az(*args)

    assert quota.main(["--config", str(config), "--apply"], az=flaky) == 1
    assert ("eastus", SPOTN) not in {(r, n) for r, n, _ in az.updates()}
    assert ("westus2", SPOTN) in {(r, n) for r, n, _ in az.updates()}
    assert "1 errors." in capsys.readouterr().out
