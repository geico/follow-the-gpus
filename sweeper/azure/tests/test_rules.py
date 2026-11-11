from datetime import timedelta

from sweeper.model import DISK, NIC, VMSS, Resource
from sweeper.query import parse_row
from sweeper.rules import DELETE, KEEP, SKIP, decide, is_young_vm
from tests.fakes import NOW, PROBE_RG, rid, row, vm_row

RULE = {
    "max_age_seconds": 180,
    "probe_rg": PROBE_RG,
    "experiment": "ftg",
    "probe_in_flight": False,
}


def test_young_resource_is_kept() -> None:
    decision = decide(parse_row(vm_row("vm", 100)), NOW, **RULE)
    assert decision.action == KEEP
    assert decision.age_seconds == 100


def test_resource_at_the_threshold_is_kept() -> None:
    assert decide(parse_row(vm_row("vm", 180)), NOW, **RULE).action == KEEP


def test_old_resource_is_deleted() -> None:
    decision = decide(parse_row(vm_row("vm", 181)), NOW, **RULE)
    assert decision.action == DELETE
    assert decision.age_seconds == 181


def test_other_tag_value_is_skipped() -> None:
    decision = decide(parse_row(vm_row("vm", 999, experiment="other")), NOW, **RULE)
    assert decision.action == SKIP
    assert "tag" in decision.reason


def test_other_resource_group_is_skipped() -> None:
    decision = decide(parse_row(vm_row("vm", 999, rg="ftg-control")), NOW, **RULE)
    assert decision.action == SKIP
    assert "resource group" in decision.reason


def test_resource_group_match_ignores_case() -> None:
    decision = decide(parse_row(vm_row("vm", 999, rg="FTG-Probe")), NOW, **RULE)
    assert decision.action == DELETE


def test_scale_set_is_never_deleted() -> None:
    raw = row("Microsoft.Compute/virtualMachineScaleSets/s", rtype=VMSS, age_seconds=99999)
    assert decide(parse_row(raw), NOW, **RULE).action == SKIP


def test_deletable_type_without_time_created_is_kept() -> None:
    raw = row("Microsoft.Compute/disks/d", rtype=DISK, age_seconds=None)
    decision = decide(parse_row(raw), NOW, **RULE)
    assert decision.action == KEEP
    assert decision.age_seconds is None


def test_nic_rules() -> None:
    vm_id = rid(PROBE_RG, "Microsoft.Compute/virtualMachines/vm")
    attached = parse_row(
        row(
            "Microsoft.Network/networkInterfaces/n",
            rtype=NIC,
            age_seconds=None,
            attached_vm_id=vm_id,
        )
    )
    loose = parse_row(row("Microsoft.Network/networkInterfaces/n", rtype=NIC, age_seconds=None))
    assert decide(attached, NOW, **RULE).action == KEEP
    assert decide(loose, NOW, **RULE).action == DELETE
    assert decide(loose, NOW, **{**RULE, "probe_in_flight": True}).action == KEEP


def test_is_young_vm_looks_only_at_vms() -> None:
    assert is_young_vm(parse_row(vm_row("vm", 10)), NOW, 180)
    assert not is_young_vm(parse_row(vm_row("vm", 181)), NOW, 180)
    disk = Resource(
        id=rid(PROBE_RG, "Microsoft.Compute/disks/d"),
        name="d",
        type=DISK,
        resource_group=PROBE_RG,
        time_created=NOW - timedelta(seconds=10),
    )
    assert not is_young_vm(disk, NOW, 180)
