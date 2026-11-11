from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from ftg_preflight import cli
from ftg_preflight.report import Level, exit_code, fail, ok, render, warn
from ftg_preflight.terraform import Terraform, TerraformError, decode_console_json


class FakeRun:
    """Replays tofu console and state list output."""

    def __init__(self, values: dict[str, str], state: str | None = None):
        self.values = values
        self.state = state
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], *, input: str = "", **_: Any) -> Any:
        self.calls.append(args)
        if args[2:] == ["state", "list"]:
            if self.state is None:
                return subprocess.CompletedProcess(args, 1, "", "No state file was found!")
            return subprocess.CompletedProcess(args, 0, self.state, "")
        name = input.strip().removeprefix("jsonencode(var.").removesuffix(")")
        if name not in self.values:
            err = "Error: Reference to undeclared input variable"
            return subprocess.CompletedProcess(args, 1, "", err)
        return subprocess.CompletedProcess(args, 0, self.values[name] + "\n", "")


def test_decode_console_json():
    assert decode_console_json('"\\"eastus\\""\n') == "eastus"
    assert decode_console_json('"null"\n') is None
    assert decode_console_json("(known after apply)\n") is None


def test_terraform_vars_and_missing_optional():
    run = FakeRun({"location": '"\\"eastus\\""', "sweeper_plan_sku": '"\\"Y1\\""'})
    tf = Terraform("/stack", run=run)
    values = tf.vars(["location", "sweeper_plan_sku"], optional=["sweeper_location"])
    assert values == {"location": "eastus", "sweeper_plan_sku": "Y1", "sweeper_location": None}
    assert run.calls[0] == ["tofu", "-chdir=/stack", "console"]


def test_terraform_missing_required_var_raises():
    tf = Terraform("/stack", run=FakeRun({}))
    with pytest.raises(TerraformError):
        tf.var("location")


def test_has_resource():
    state = "azurerm_linux_virtual_machine.prober\nazurerm_resource_group.probe\n"
    assert Terraform("/s", run=FakeRun({}, state)).has_resource(
        "azurerm_linux_virtual_machine.prober"
    )
    assert not Terraform("/s", run=FakeRun({})).has_resource("azurerm_linux_virtual_machine.prober")


def test_render_and_exit_code():
    findings = [ok("a", "fine"), warn("b", "signal gap", "do x"), fail("c", "gap", "do y")]
    text = render(findings)
    assert "PASS  a: fine" in text
    assert "WARN  b: signal gap\n      fix: do x" in text
    assert text.endswith("1 passed, 1 warnings, 1 failures")
    assert exit_code(findings) == 1
    assert exit_code(findings[:2]) == 0


def test_main_exit_code_follows_the_findings(monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_azure", lambda tf, config: [fail("azure/x", "gap")])
    assert cli.main(["azure", "--tf-dir", "/stack"]) == 1
    monkeypatch.setattr(cli, "run_azure", lambda tf, config: [warn("azure/x", "signal")])
    assert cli.main(["azure", "--tf-dir", "/stack"]) == 0
    assert "WARN  azure/x: signal" in capsys.readouterr().out


def test_main_terraform_error_exits_2(monkeypatch, capsys):
    def boom(tf, config):
        raise TerraformError("tofu console failed")

    monkeypatch.setattr(cli, "run_aws", boom)
    assert cli.main(["aws", "--tf-dir", "/stack"]) == 2
    assert "tofu console failed" in capsys.readouterr().err


def test_run_aws_resolves_capacity_blocks_from_the_aws_cb_offering_job(monkeypatch):
    """Review finding: `run_aws` must read `capacity_blocks` from the `aws_cb_offering`
    job's settings (ADR 0010 section 3), not the old, now-empty flat key, so that
    `check_capacity_blocks` still calls `DescribeCapacityBlockOfferings` instead of silently
    passing with "the config has no Capacity Block targets"."""
    from ftg_preflight import aws
    from tests.fakes import FakeClient

    repo_config = Path(__file__).resolve().parents[2] / "config" / "targets.yaml"
    values = {
        "region": '"\\"us-east-1\\""',
        "instance_type": '"\\"t4g.small\\""',
        "vpc_id": "(known after apply)",
        "subnet_id": "(known after apply)",
    }
    seen = {}

    def fake_checks(client_factory, inputs, profiles):
        seen["inputs"] = inputs
        return [ok("aws/x", "fine")]

    monkeypatch.setattr(cli.aws, "run_checks", fake_checks)
    findings = cli.run_aws(Terraform(".", run=FakeRun(values)), repo_config)
    assert findings[0].level is Level.PASS
    cb = seen["inputs"].capacity_blocks
    assert cb["instance_types"] == [
        "p5.4xlarge",
        "p5.48xlarge",
        "p5en.48xlarge",
        "p6-b200.48xlarge",
        "p4d.24xlarge",
    ]
    ec2 = FakeClient({"describe_capacity_block_offerings": {"CapacityBlockOfferings": []}})
    finding = aws.check_capacity_blocks(ec2, seen["inputs"])
    assert finding.level is Level.PASS
    (call,) = ec2.calls
    assert call == (
        "describe_capacity_block_offerings",
        {
            "InstanceType": "p5.4xlarge",
            "InstanceCount": 1,
            "CapacityDurationHours": 24,
        },
    )


def test_aws_cb_offering_defaults_match_the_prober_package():
    """`cli.AWS_CB_OFFERING_DEFAULTS` is a copy of `ftg_prober.config.MODULE_DEFAULTS
    ["aws_cb_offering"]`, kept only because preflight has no runtime dependency on the prober
    package (review finding). This test reads the prober package from its path in this repo,
    for the comparison only; it adds no dependency to preflight itself."""
    import sys

    prober_src = Path(__file__).resolve().parents[2] / "prober"
    added = str(prober_src) not in sys.path
    if added:
        sys.path.insert(0, str(prober_src))
    try:
        from ftg_prober.config import MODULE_DEFAULTS
    finally:
        if added:
            sys.path.remove(str(prober_src))
    assert MODULE_DEFAULTS["aws_cb_offering"] == cli.AWS_CB_OFFERING_DEFAULTS


def test_run_aws_resolves_capacity_blocks_from_a_renamed_aws_cb_offering_module(
    tmp_path, monkeypatch
):
    """Review finding: a job may name `aws_cb_offering` through a renamed `modules:` entry
    (`type: aws_cb_offering`), not only the built-in name directly. `_capacity_blocks` must
    resolve by `type`, the same way `_module_override` does, not by the literal module name."""
    (tmp_path / "targets.yaml").write_text(
        "aws:\n"
        "  modules:\n"
        "    cb48: {type: aws_cb_offering, instance_types: [p5.4xlarge], duration_hours: [48]}\n"
        "  jobs:\n    - {module: cb48, regions: [us-east-1], interval: 2h}\n"
    )
    values = {
        "region": '"\\"us-east-1\\""',
        "instance_type": '"\\"t4g.small\\""',
        "vpc_id": "(known after apply)",
        "subnet_id": "(known after apply)",
    }
    seen = {}

    def fake_checks(client_factory, inputs, profiles):
        seen["inputs"] = inputs
        return [ok("aws/x", "fine")]

    monkeypatch.setattr(cli.aws, "run_checks", fake_checks)
    findings = cli.run_aws(Terraform(str(tmp_path), run=FakeRun(values)), tmp_path / "targets.yaml")
    assert findings[0].level is Level.PASS
    cb = seen["inputs"].capacity_blocks
    assert cb["instance_types"] == ["p5.4xlarge"]
    assert cb["duration_hours"] == [48]
    assert cb["instance_count"] == 1  # the code default; the module did not override it


def test_run_azure_reads_the_targets_file(tmp_path, monkeypatch):
    (tmp_path / "targets.yaml").write_text(
        "azure:\n"
        "  modules:\n    az_spot_score: {type: az_spot_score, spot_sizes: [Standard_NC4as_T4_v3]}\n"
        "  jobs:\n    - {module: az_spot_score, regions: [eastus], interval: 15m}\n"
        "  provisioning_probes:\n    az_capacity_reservation:\n"
        "      - {sku: Standard_D2as_v4, region: eastus}\n"
    )
    values = {
        "location": '"\\"eastus\\""',
        "prober_vm_size": '"\\"Standard_D2as_v4\\""',
        "sweeper_plan_sku": '"\\"Y1\\""',
        "alert_email": '"\\"\\""',
        "targets_file": '"\\"targets.yaml\\""',
        "resource_group_name": '"\\"ftg-rg\\""',
        "subnet_id": "(known after apply)",
    }
    seen = {}

    def fake_checks(az, inputs):
        seen["inputs"] = inputs
        return [ok("azure/x", "fine")]

    monkeypatch.setattr(cli.azure, "run_checks", fake_checks)
    monkeypatch.delenv("ARM_SUBSCRIPTION_ID", raising=False)
    findings = cli.run_azure(Terraform(str(tmp_path), run=FakeRun(values)), None)
    assert findings[0].level is Level.PASS
    got = seen["inputs"]
    assert (got.location, got.sweeper_region, got.prober_deployed) == ("eastus", "eastus", False)
    assert got.spot_sizes == ["Standard_NC4as_T4_v3"]
    assert got.provisioning_probes[0]["sku"] == "Standard_D2as_v4"
    assert (got.resource_group_name, got.subnet_id) == ("ftg-rg", None)


def test_run_azure_refuses_the_old_list_shape_with_the_adr_message(tmp_path):
    (tmp_path / "targets.yaml").write_text(
        "azure:\n  provisioning_probes:\n    - {sku: Standard_D2as_v4, region: eastus}\n"
    )
    values = {
        "location": '"\\"eastus\\""',
        "prober_vm_size": '"\\"Standard_D2as_v4\\""',
        "sweeper_plan_sku": '"\\"Y1\\""',
        "alert_email": '"\\"\\""',
        "targets_file": '"\\"targets.yaml\\""',
        "resource_group_name": '"\\"ftg-rg\\""',
        "subnet_id": "(known after apply)",
    }
    with pytest.raises(ValueError, match="mapping from probe type.*ADR 0022"):
        cli.run_azure(Terraform(str(tmp_path), run=FakeRun(values)), None)


def _run_azure_with_probes(tmp_path, probes: str):
    (tmp_path / "targets.yaml").write_text("azure:\n  provisioning_probes:\n" + probes)
    values = {
        "location": '"\\"eastus\\""',
        "prober_vm_size": '"\\"Standard_D2as_v4\\""',
        "sweeper_plan_sku": '"\\"Y1\\""',
        "alert_email": '"\\"\\""',
        "targets_file": '"\\"targets.yaml\\""',
        "resource_group_name": '"\\"ftg-rg\\""',
        "subnet_id": "(known after apply)",
    }
    return cli.run_azure(Terraform(str(tmp_path), run=FakeRun(values)), None)


def test_run_azure_null_probe_type_gives_the_config_message(tmp_path):
    with pytest.raises(ValueError, match=r"azure.provisioning_probes.az_vmss_flex must be a list"):
        _run_azure_with_probes(tmp_path, "    az_vmss_flex:\n")


def test_run_azure_rejects_two_scale_set_entries_with_the_same_size_and_region(tmp_path):
    probes = (
        "    az_vmss_flex:\n"
        "      - {sku: Standard_NC40ads_H100_v5, region: eastus, zone: '1'}\n"
        "      - {sku: standard_nc40ads_h100_v5, region: EastUS, zone: '2'}\n"
    )
    with pytest.raises(ValueError, match="two az_vmss_flex entries have the size"):
        _run_azure_with_probes(tmp_path, probes)


@pytest.mark.parametrize("value", ["'false'", "1", "null"])
def test_run_azure_rejects_a_non_boolean_simulate_and_names_the_entry(tmp_path, value):
    probes = (
        "    az_vmss_flex:\n"
        f"      - {{sku: Standard_B2s, region: eastus, zone: '1', simulate: {value}}}\n"
    )
    with pytest.raises(
        ValueError, match=r"az_vmss_flex Standard_B2s in eastus: simulate must be true or false"
    ):
        _run_azure_with_probes(tmp_path, probes)


@pytest.mark.parametrize("item", ["notadict", "null", "[1]"])
def test_run_azure_rejects_a_non_mapping_entry_and_names_it(tmp_path, item):
    probes = f"    az_vmss_flex:\n      - {item}\n"
    with pytest.raises(ValueError, match=r"az_vmss_flex\[0\] must be a mapping"):
        _run_azure_with_probes(tmp_path, probes)
