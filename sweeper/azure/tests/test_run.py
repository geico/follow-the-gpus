import logging

import pytest

from sweeper.model import DISK, NIC, RESERVATION, VMSS
from sweeper.run import SweepError, sweep
from sweeper.settings import Settings
from tests.fakes import NOW, PROBE_RG, FakeDeleter, FakeRows, FakeScaler, rid, row, vm_row

SETTINGS = Settings(probe_rg=PROBE_RG, experiment="ftg", max_age_seconds=180)
LOGGER = logging.getLogger("sweeper")
VMSS_ID = rid(PROBE_RG, "Microsoft.Compute/virtualMachineScaleSets/ftg-b2s-eastus-multi")


def run(rows, deleter=None, scaler=None, settings=SETTINGS):
    deleter = deleter or FakeDeleter()
    scaler = scaler or FakeScaler()
    result = sweep(settings, FakeRows(rows), deleter, scaler, now=lambda: NOW, logger=LOGGER)
    return result, deleter, scaler


def decision_lines(caplog) -> list[str]:
    return [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("sweeper decision:")
    ]


def test_young_resource_stays(caplog) -> None:
    caplog.set_level(logging.INFO, logger="sweeper")
    result, deleter, _ = run([vm_row("young", 60)])
    assert deleter.calls == []
    assert result.scanned == 1 and result.deleted == 0 and result.errors == 0
    (line,) = decision_lines(caplog)
    assert "action=keep" in line and "age_s=60" in line and "virtualMachines/young" in line


def test_old_resource_is_deleted(caplog) -> None:
    caplog.set_level(logging.INFO, logger="sweeper")
    result, deleter, _ = run([vm_row("old", 300)])
    assert [r.name for r in deleter.calls] == ["old"]
    assert result.deleted == 1 and result.errors == 0
    (line,) = decision_lines(caplog)
    assert "action=deleted" in line and "age_s=300" in line


def test_404_counts_as_done_and_logs_already_gone(caplog) -> None:
    caplog.set_level(logging.INFO, logger="sweeper")
    gone = rid(PROBE_RG, "Microsoft.Compute/virtualMachines/gone")
    result, deleter, _ = run([vm_row("gone", 300)], FakeDeleter(gone={gone}))
    assert [r.name for r in deleter.calls] == ["gone"]
    assert result.deleted == 1 and result.errors == 0
    assert any("action=already gone" in line for line in decision_lines(caplog))


def test_other_tag_value_stays() -> None:
    result, deleter, _ = run([vm_row("foreign", 999, experiment="other")])
    assert deleter.calls == []
    assert result.deleted == 0 and result.errors == 0
    assert result.actions[rid(PROBE_RG, "Microsoft.Compute/virtualMachines/foreign")] == "skip"


def test_resource_outside_the_probe_group_is_never_touched() -> None:
    result, deleter, _ = run([vm_row("outside", 999, rg="ftg-control")])
    assert deleter.calls == []
    assert result.actions[rid("ftg-control", "Microsoft.Compute/virtualMachines/outside")] == "skip"


def test_delete_order_is_vms_then_nics_and_disks_then_reservations() -> None:
    rows = [
        row(
            "Microsoft.Compute/capacityReservationGroups/g/capacityReservations/r",
            rtype=RESERVATION,
            age_seconds=400,
        ),
        row("Microsoft.Compute/disks/d", rtype=DISK, age_seconds=400),
        row("Microsoft.Network/networkInterfaces/n", rtype=NIC, age_seconds=None),
        vm_row("v", 400),
    ]
    _, deleter, _ = run(rows)
    types = [r.type for r in deleter.calls]
    assert types[0] == "microsoft.compute/virtualmachines"
    assert set(types[1:3]) == {NIC, DISK}
    assert types[3] == RESERVATION


def test_delete_error_is_counted_and_the_run_continues() -> None:
    failing = rid(PROBE_RG, "Microsoft.Compute/virtualMachines/a")
    result, deleter, _ = run([vm_row("a", 300), vm_row("b", 300)], FakeDeleter(failing={failing}))
    assert [r.name for r in deleter.calls] == ["a", "b"]
    assert result.deleted == 1 and result.errors == 1
    assert result.actions[failing] == "error"


def test_slow_delete_is_accepted() -> None:
    slow = rid(PROBE_RG, "Microsoft.Compute/virtualMachines/slow")
    result, _, _ = run([vm_row("slow", 300)], FakeDeleter(slow={slow}))
    assert result.deleted == 1
    assert result.actions[slow] == "delete accepted"


def test_health_line_is_the_last_line(caplog) -> None:
    caplog.set_level(logging.INFO, logger="sweeper")
    gone = rid(PROBE_RG, "Microsoft.Compute/virtualMachines/gone")
    failing = rid(PROBE_RG, "Microsoft.Compute/virtualMachines/bad")
    rows = [vm_row("young", 10), vm_row("old", 300), vm_row("gone", 300), vm_row("bad", 300)]
    result, _, _ = run(rows, FakeDeleter(gone={gone}, failing={failing}))
    assert result.health_line == "sweeper run: scanned 4, deleted 2, errors 1"
    assert caplog.records[-1].getMessage() == result.health_line


def test_query_failure_logs_the_health_line_and_raises(caplog) -> None:
    caplog.set_level(logging.INFO, logger="sweeper")
    rows = FakeRows(error=RuntimeError("throttled"))
    with pytest.raises(SweepError):
        sweep(SETTINGS, rows, FakeDeleter(), FakeScaler(), now=lambda: NOW, logger=LOGGER)
    assert caplog.records[-1].getMessage() == "sweeper run: scanned 0, deleted 0, errors 1"


def test_bad_row_is_an_error_and_the_run_continues() -> None:
    result, deleter, _ = run([{"name": "no id"}, vm_row("old", 300)])
    assert result.scanned == 2 and result.deleted == 1 and result.errors == 1
    assert [r.name for r in deleter.calls] == ["old"]


def vmss_row(capacity: int = 1):
    return row(
        "Microsoft.Compute/virtualMachineScaleSets/ftg-b2s-eastus-multi",
        rtype=VMSS,
        age_seconds=86400,
        capacity=capacity,
    )


def test_fallback_scales_to_zero_when_a_scale_set_has_old_instances(caplog) -> None:
    caplog.set_level(logging.INFO, logger="sweeper")
    rows = [
        vmss_row(),
        vm_row("i0", 300, vmss_id=VMSS_ID),
        vm_row("i1", 300, vmss_id=VMSS_ID.upper()),
    ]
    result, deleter, scaler = run(rows)
    assert [r.name for r in deleter.calls] == ["i0", "i1"]
    assert [r.id for r in scaler.calls] == [VMSS_ID]
    assert result.scaled == [VMSS_ID]
    assert any(
        "sweeper fallback: action=scaled to 0 instances=2" in r.getMessage() for r in caplog.records
    )


def test_fallback_does_not_run_for_young_instances() -> None:
    _, _, scaler = run([vmss_row(), vm_row("i0", 30, vmss_id=VMSS_ID)])
    assert scaler.calls == []


def test_fallback_does_not_run_without_old_instances() -> None:
    _, _, scaler = run([vmss_row(0)])
    assert scaler.calls == []


def test_fallback_skips_a_scale_set_that_the_query_did_not_return(caplog) -> None:
    caplog.set_level(logging.INFO, logger="sweeper")
    _, _, scaler = run([vm_row("i0", 300, vmss_id=VMSS_ID)])
    assert scaler.calls == []
    assert any("sweeper fallback: action=skip" in r.getMessage() for r in caplog.records)


def test_fallback_runs_after_the_deletes() -> None:
    order: list[str] = []

    class OrderDeleter(FakeDeleter):
        def begin_delete(self, resource):
            order.append("delete")
            return super().begin_delete(resource)

    class OrderScaler(FakeScaler):
        def begin_scale_to_zero(self, vmss):
            order.append("scale")
            return super().begin_scale_to_zero(vmss)

    run([vmss_row(), vm_row("i0", 300, vmss_id=VMSS_ID)], OrderDeleter(), OrderScaler())
    assert order == ["delete", "scale"]


def untagged(r: dict, tags: dict | None = None) -> dict:
    """The row without the experiment tag, as the stack and the network owner write them."""
    return {**r, "tags": tags or {}}


def test_shared_group_deletes_only_tagged_probe_resources() -> None:
    # Spec 0005: the prober VM, the storage, the sweeper, and the probe resources share 1 group
    # with the resources of the network owner. Only probe resources carry the experiment tag.
    # The fake returns every row, so the age rule must skip the untagged ones by itself.
    rg = "platform-rg"
    settings = Settings(probe_rg=rg, experiment="ftg", max_age_seconds=180)
    prober_vm = rid(rg, "Microsoft.Compute/virtualMachines/ftg-prober")
    day = 86400
    keep = [
        untagged(vm_row("ftg-prober", day, rg=rg)),
        untagged(row("Microsoft.Compute/disks/ftg-prober-os", rtype=DISK, age_seconds=day, rg=rg)),
        untagged(
            row(
                "Microsoft.Network/networkInterfaces/ftg-prober-nic",
                rtype=NIC,
                age_seconds=None,
                rg=rg,
                attached_vm_id=prober_vm,
            )
        ),
        untagged(
            row("Microsoft.Network/networkInterfaces/spare", rtype=NIC, age_seconds=None, rg=rg)
        ),
        untagged(
            row(
                "Microsoft.Storage/storageAccounts/ftgdata0",
                rtype="microsoft.storage/storageaccounts",
                age_seconds=day,
                rg=rg,
            )
        ),
        untagged(
            row(
                "Microsoft.Web/sites/ftg-sweeper",
                rtype="microsoft.web/sites",
                age_seconds=day,
                rg=rg,
            )
        ),
        untagged(vm_row("owner-vm", day, rg=rg), {"owner": "platform"}),
        untagged(
            row("Microsoft.Compute/disks/owner-disk", rtype=DISK, age_seconds=day, rg=rg),
            {"owner": "platform"},
        ),
    ]
    probe_vm = vm_row("ftg-member", 300, rg=rg)
    rows = FakeRows([*keep, probe_vm])
    deleter = FakeDeleter()

    result = sweep(settings, rows, deleter, FakeScaler(), now=lambda: NOW, logger=LOGGER)

    assert deleter.deleted_ids == [probe_vm["id"]]
    assert result.deleted == 1 and result.errors == 0
    assert all(result.actions[r["id"]] == "skip" for r in keep)
    assert "| where tostring(tags['experiment']) =~ 'ftg'" in rows.queries[0]
