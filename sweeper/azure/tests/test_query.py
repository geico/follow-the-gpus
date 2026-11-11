from datetime import UTC, datetime

import pytest

from sweeper.model import DISK, NIC, RESERVATION, VM, VMSS
from sweeper.query import QueryError, build_query, parse_row, parse_time, resource_group_of
from tests.fakes import PROBE_RG, row, vm_row


def test_query_filters_on_resource_group_tag_and_types() -> None:
    kql = build_query("ftg-probe", "ftg")
    assert kql.startswith("Resources\n")
    assert "| where resourceGroup =~ 'ftg-probe'" in kql
    assert "| where tostring(tags['experiment']) =~ 'ftg'" in kql
    for rtype in (VM, DISK, NIC, RESERVATION, VMSS):
        assert f"'{rtype}'" in kql
    assert "timeCreated = tostring(properties.timeCreated)" in kql
    assert "vmssId = tostring(properties.virtualMachineScaleSet.id)" in kql
    assert "attachedVmId = tostring(properties.virtualMachine.id)" in kql


@pytest.mark.parametrize("bad", ["", "a'b", "rg | where 1", "x" * 91])
def test_query_rejects_unsafe_names(bad: str) -> None:
    with pytest.raises(QueryError):
        build_query(bad, "ftg")
    with pytest.raises(QueryError):
        build_query("ftg-probe", bad)


def test_parse_time_accepts_seven_fraction_digits_and_z_or_offset() -> None:
    parsed = parse_time("2026-10-11T10:00:00.1234567Z")
    assert parsed == datetime(2026, 10, 11, 10, 0, 0, 123456, tzinfo=UTC)
    assert parse_time("2026-10-11T10:00:00.1234567+00:00") == parsed
    assert parse_time("") is None
    assert parse_time(None) is None


def test_parse_row_reads_each_column() -> None:
    raw = vm_row("vm1", 200, vmss_id="/subscriptions/s/resourceGroups/rg/x/vmss1")
    resource = parse_row(raw)
    assert resource.type == VM
    assert resource.name == "vm1"
    assert resource.resource_group == PROBE_RG
    assert resource.tags == {"experiment": "ftg"}
    assert resource.time_created is not None
    assert resource.vmss_id == "/subscriptions/s/resourceGroups/rg/x/vmss1"
    assert resource.attached_vm_id is None
    assert resource.capacity is None


def test_parse_row_takes_the_resource_group_from_the_id() -> None:
    raw = row("Microsoft.Compute/disks/d1", rtype=DISK, age_seconds=10, rg="Other-RG")
    assert parse_row(raw).resource_group == "Other-RG"
    assert resource_group_of("/subscriptions/s/resourcegroups/RG1/providers/p/t/n") == "RG1"
    assert resource_group_of("/subscriptions/s") is None


def test_parse_row_rejects_a_row_without_an_id() -> None:
    with pytest.raises(QueryError):
        parse_row({"name": "x"})
