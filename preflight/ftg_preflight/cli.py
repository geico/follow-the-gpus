"""`python -m ftg_preflight aws|azure`. Exit 0: no gap. 1: a gap blocks the deploy. 2: usage."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from ftg_preflight import aws, azure
from ftg_preflight.report import EXIT_USAGE, Finding, exit_code, render
from ftg_preflight.terraform import Terraform, TerraformError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ftg-preflight", description="Read-only checks before make apply/aws or apply/azure."
    )
    parser.add_argument("cloud", choices=("aws", "azure"))
    parser.add_argument("--tf-dir", required=True, help="the OpenTofu folder of the stack")
    parser.add_argument("--config", help="targets.yaml; default: the targets_file variable")
    parser.add_argument("--terraform", default="tofu", help="the tofu command")
    return parser


def load_targets(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _job_regions(section: dict[str, Any]) -> list[str]:
    """The union of the `regions` of every job, in first-seen order (ADR 0010 section 1)."""
    regions: list[str] = []
    for job in section.get("jobs") or []:
        for region in (job or {}).get("regions") or []:
            if region not in regions:
                regions.append(region)
    return regions


def _has_job(section: dict[str, Any], probe_type: str) -> bool:
    """True when a job names `probe_type`, built in directly or via a `modules:` entry whose
    `type` is `probe_type` (a renamed module, for example `modules: {cb48: {type:
    aws_cb_offering}}`)."""
    modules = section.get("modules") or {}
    for job in section.get("jobs") or []:
        name = (job or {}).get("module")
        body = modules.get(name)
        if isinstance(body, dict):
            if body.get("type") == probe_type:
                return True
        elif name == probe_type:
            return True
    return False


def _module_override(section: dict[str, Any], probe_type: str) -> dict[str, Any]:
    """The override settings of the job that names `probe_type`, built in or via `modules:`."""
    modules = section.get("modules") or {}
    for job in section.get("jobs") or []:
        name = (job or {}).get("module")
        body = modules.get(name)
        if isinstance(body, dict):
            if body.get("type") == probe_type:
                return {k: v for k, v in body.items() if k != "type"}
        elif name == probe_type:
            return {}
    return {}


# The `aws_cb_offering` code default of `ftg_prober.config.MODULE_DEFAULTS` (the ADR 0010
# section 3 worked example), duplicated here because preflight has no dependency on the
# prober package. Used only when the targets file has no `aws_cb_offering` override for a key.
AWS_CB_OFFERING_DEFAULTS: dict[str, Any] = {
    "instance_types": ["p5.4xlarge", "p4d.24xlarge"],
    "instance_count": 1,
    "duration_hours": [24],
    "start_window_days": 14,
}


def _capacity_blocks(section: dict[str, Any]) -> dict[str, Any]:
    """The `aws_cb_offering` job's settings: code defaults merged with the override (ADR 0010
    section 3). Empty when no `aws_cb_offering` job is listed (built in, or via a renamed
    `modules:` entry whose `type` is `aws_cb_offering`)."""
    if not _has_job(section, "aws_cb_offering"):
        return {}
    return {**AWS_CB_OFFERING_DEFAULTS, **_module_override(section, "aws_cb_offering")}


def run_aws(tf: Terraform, config: Path | None) -> list[Finding]:
    import boto3

    values = tf.vars(["region", "instance_type", "vpc_id", "subnet_id"])
    if config is None:
        config = Path(tf.tf_dir) / str(tf.var("targets_file"))
    targets = load_targets(config).get("aws") or {}
    session = boto3.Session()
    profiles = session._session.full_config.get("profiles", {})
    inputs = aws.AwsInputs(
        region=str(values["region"]),
        instance_type=str(values["instance_type"]),
        vpc_id=values.get("vpc_id") or None,
        subnet_id=values.get("subnet_id") or None,
        capacity_blocks=_capacity_blocks(targets),
        env_region=os.environ.get("AWS_REGION"),
        profile=profiles.get(session.profile_name or "default", {}),
    )
    print(f"preflight aws: region {inputs.region}, instance type {inputs.instance_type}")
    return aws.run_checks(
        lambda service, region: session.client(service, region_name=region), inputs, profiles
    )


def _azure_probe_entries(config: Path, probes: dict[str, Any]) -> list[dict[str, Any]]:
    """The entries of all probe types. A probe type with no list, or two scale-set entries with
    the same size and Region (they share 1 scale-set key and name), is a config error."""
    entries: list[dict[str, Any]] = []
    for ptype, group in probes.items():
        if not isinstance(group, list):
            raise ValueError(
                f"{config}: azure.provisioning_probes.{ptype} must be a list of entries"
            )
        for i, entry in enumerate(group):
            if not isinstance(entry, dict):
                raise ValueError(
                    f"{config}: azure.provisioning_probes.{ptype}[{i}] must be a mapping, "
                    f"got {entry!r}"
                )
            if not isinstance(entry.get("simulate", False), bool):
                raise ValueError(
                    f"{config}: azure.provisioning_probes.{ptype} {entry.get('sku')} in "
                    f"{entry.get('region')}: simulate must be true or false, got "
                    f"{entry['simulate']!r}"
                )
        entries.extend(group)
    seen: set[tuple[str, str]] = set()
    for entry in probes.get("az_vmss_flex") or []:
        key = (str(entry["sku"]).lower(), str(entry["region"]).lower())
        if key in seen:
            raise ValueError(
                f"{config}: two az_vmss_flex entries have the size {entry['sku']} in the Region "
                f"{entry['region']}; they would share 1 scale set. List the size once per Region"
            )
        seen.add(key)
    return entries


def run_azure(tf: Terraform, config: Path | None) -> list[Finding]:
    values = tf.vars(
        [
            "location",
            "prober_vm_size",
            "sweeper_plan_sku",
            "alert_email",
            "targets_file",
            "resource_group_name",
            "subnet_id",
        ],
        optional=["sweeper_location"],
    )
    if config is None:
        config = Path(tf.tf_dir) / str(values["targets_file"])
    targets = load_targets(config).get("azure") or {}
    if not isinstance(targets.get("provisioning_probes") or {}, dict):
        raise ValueError(
            f"{config}: azure.provisioning_probes is a list. It is now a mapping from probe type "
            "(az_capacity_reservation, az_vmss_flex) to a list of entries (ADR 0022)"
        )
    inputs = azure.AzureInputs(
        location=str(values["location"]),
        prober_vm_size=str(values["prober_vm_size"]),
        sweeper_plan_sku=str(values["sweeper_plan_sku"]),
        resource_group_name=values.get("resource_group_name") or None,
        subnet_id=values.get("subnet_id") or None,
        sweeper_location=values.get("sweeper_location") or None,
        alert_email=str(values.get("alert_email") or ""),
        subscription=os.environ.get("ARM_SUBSCRIPTION_ID") or None,
        prober_deployed=tf.has_resource("azurerm_linux_virtual_machine.prober"),
        regions=_job_regions(targets),
        spot_sizes=list(
            _module_override(targets, "az_spot_score").get("spot_sizes")
            or _module_override(targets, "az_spot").get("spot_sizes")
            or []
        ),
        provisioning_probes=_azure_probe_entries(config, targets.get("provisioning_probes") or {}),
    )
    print(
        f"preflight azure: location {inputs.location}, prober VM {inputs.prober_vm_size}, "
        f"sweeper {inputs.sweeper_plan_sku} in {inputs.sweeper_region}"
    )
    return azure.run_checks(azure.az_cli, inputs)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tf = Terraform(str(Path(args.tf_dir).resolve()), binary=args.terraform)
    config = Path(args.config) if args.config else None
    try:
        findings = run_aws(tf, config) if args.cloud == "aws" else run_azure(tf, config)
    except (TerraformError, OSError, yaml.YAMLError, ValueError) as exc:
        print(f"ftg-preflight: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(render(findings))
    return exit_code(findings)
