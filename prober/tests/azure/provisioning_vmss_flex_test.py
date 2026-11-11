"""The VMSS Flexible probe: each outcome, the timeout, tags, and teardown. Spec 0010 section 7."""

import json
import logging

import pytest

from ftg_prober.azure.prechecks import AllocationSettings
from ftg_prober.azure.provisioning_adapter import AzureCallError, Member
from ftg_prober.azure.vmss_flex import (
    MemberTagger,
    VmssFlexProbe,
    classify_allocation,
    classify_code,
    member_state,
    member_state_counts,
    teardown,
)
from tests.azure.provisioning_fakes import (
    RUN_ID,
    FakeAdapter,
    FakeClock,
    PlannedMember,
    b2s,
    entry,
    make_ctx,
    scale_set_name,
    usages,
    wire,
)

QUOTA = AzureCallError(
    "OperationNotAllowed", "Operation results in exceeding quota limits of Core.", status=409
)


def vmss_adapter(**kwargs) -> FakeAdapter:
    kwargs.setdefault("skus", [b2s(capacity_reservation_supported=False)])
    return FakeAdapter(FakeClock(), **kwargs)


def run(adapter, targets=None, **section):
    probe = wire(VmssFlexProbe(), adapter)
    rows = probe.run(make_ctx(targets or [entry()], **section))
    return rows


def one(adapter, **section):
    (row,) = run(adapter, **section)
    return row


@pytest.mark.parametrize(
    ("code", "message", "outcome"),
    [
        ("AllocationFailed", "", "no_capacity"),
        ("ZonalAllocationFailed", "", "no_capacity"),
        ("OverconstrainedAllocationRequest", "", "no_capacity"),
        ("OverconstrainedZonalAllocationRequest", "", "no_capacity"),
        ("SkuNotAvailable", "", "restricted_or_no_capacity"),
        ("ResourceQuotaExceeded", "", "quota"),
        ("OperationNotAllowed", "Operation results in exceeding quota limits of Core.", "quota"),
        ("OperationNotAllowed", "something else", "error"),
        ("InternalServerError", "", "error"),
        (None, "", "error"),
    ],
)
def test_classification_table(code, message, outcome):
    assert classify_code(code, message) == outcome


def test_classify_allocation_counts():
    assert classify_allocation(4, 4, []) == ("success", None)
    assert classify_allocation(4, 1, ["AllocationFailed"]) == ("no_capacity", "AllocationFailed")
    assert classify_allocation(4, 0, ["Conflict", "AllocationFailed"]) == (
        "no_capacity",
        "AllocationFailed",
    )
    assert classify_allocation(4, 2, [], timed_out=True) == ("timeout", None)


def test_success_places_all_and_tears_down():
    adapter = vmss_adapter(plan=[PlannedMember("placed"), PlannedMember("placed")])
    row = one(adapter)
    assert (row.probe_type, row.outcome, row.n_requested, row.n_placed) == (
        "az_vmss_flex",
        "success",
        2,
        2,
    )
    assert (row.placement_group_mode, row.error_code, row.simulated) == ("multi", None, False)
    assert row.details["scale_set"] == scale_set_name()
    assert row.details["match"] == {"experiment": "ftg", "sku": "Standard_B2s", "region": "eastus"}
    assert row.details["teardown"]["verified"] is True
    assert ("update_scale_set", scale_set_name(), 0, None) in adapter.calls
    assert row.latency_ms == 5000  # outcome seen at the first poll after 2 s; poll interval 5 s


def test_scale_out_sets_the_run_id_tag_on_the_scale_set():
    adapter = vmss_adapter(plan=[PlannedMember("placed"), PlannedMember("placed")])
    one(adapter)
    scale_out = next(c for c in adapter.calls if c[0] == "update_scale_set" and c[2] == 2)
    assert scale_out[3]["run_id"] == RUN_ID
    assert scale_out[3]["experiment"] == "ftg"


def test_partial_fill_is_a_failure_with_the_raw_count_in_details():
    plan = [PlannedMember("placed"), PlannedMember("failed", "AllocationFailed")]
    adapter = vmss_adapter(plan=plan, scale_op_error=AzureCallError("AllocationFailed"))
    row = run(adapter)[0]
    assert (row.outcome, row.n_placed, row.error_code) == ("no_capacity", 0, "AllocationFailed")
    assert row.details["n_placed"] == 1
    assert row.details["n_failed"] == 1
    assert ("delete_vm", f"{scale_set_name()}_1") in adapter.calls
    assert row.details["teardown"]["verified"] is True


def test_all_failed_is_no_capacity_with_fill_0():
    plan = [PlannedMember("failed", "AllocationFailed")] * 2
    row = run(vmss_adapter(plan=plan, scale_op_error=AzureCallError("AllocationFailed")))[0]
    assert (row.outcome, row.n_placed, row.error_code) == ("no_capacity", 0, "AllocationFailed")


def test_sku_not_available_is_restricted_or_no_capacity():
    plan = [PlannedMember("failed", "SkuNotAvailable")] * 2
    row = one(vmss_adapter(plan=plan))
    assert (row.outcome, row.error_code) == ("restricted_or_no_capacity", "SkuNotAvailable")


def test_quota_error_on_the_scale_out_request():
    row = one(vmss_adapter(scale_error=QUOTA))
    assert (row.outcome, row.n_placed, row.error_code) == ("quota", None, "OperationNotAllowed")
    assert row.details["teardown"]["verified"] is True


def test_other_error_code_is_error():
    row = one(vmss_adapter(scale_error=AzureCallError("InternalServerError", "boom")))
    assert (row.outcome, row.error_code) == ("error", "InternalServerError")


def test_inner_detail_code_is_the_error_code():
    err = AzureCallError("ResourceOperationFailure", "x", details=("ZonalAllocationFailed",))
    row = run(vmss_adapter(scale_error=err))[0]
    assert (row.outcome, row.error_code) == ("no_capacity", "ZonalAllocationFailed")


def test_hang_in_creating_times_out_then_tears_down():
    plan = [PlannedMember("hang"), PlannedMember("hang")]
    adapter = vmss_adapter(plan=plan, scale_op_after=None)
    row = one(adapter, probe_timeout_seconds=30)
    assert (row.outcome, row.n_placed) == ("timeout", None)
    assert row.latency_ms == 30_000
    assert row.details["n_pending"] == 2
    assert row.details["teardown"]["verified"] is True
    assert adapter.clock.t < 30 + 60 + 10


def test_timeout_comes_from_the_config():
    adapter = vmss_adapter(plan=[PlannedMember("hang")] * 2, scale_op_after=None)
    row = one(adapter, probe_timeout_seconds=60, poll_interval_seconds=10)
    assert row.latency_ms == 60_000
    assert row.details["polls"] == 7


def test_members_are_tagged_before_the_first_sleep():
    adapter = vmss_adapter(plan=[PlannedMember("placed"), PlannedMember("placed")])
    one(adapter)
    scale = next(i for i, c in enumerate(adapter.calls) if c[0] == "update_scale_set")
    first_list = next(
        i for i, c in enumerate(adapter.calls) if i > scale and c[0] == "list_members"
    )
    tag_calls = [i for i, c in enumerate(adapter.calls) if c[0] == "tag_resource"]
    assert len(tag_calls) == 6  # 2 VMs, 2 OS disks, 2 NICs
    assert all(i > first_list for i in tag_calls)
    assert adapter.clock.sleeps[0] == 5
    # Tagging happened at time 0, before the first sleep.
    assert adapter.tags[f"/fake/virtualMachines/{scale_set_name()}_0"] == {
        "experiment": "ftg",
        "target": "b2s-eastus-multi",
        "run_id": RUN_ID,
    }
    assert f"/fake/disks/{scale_set_name()}_1_disk" in adapter.tags
    assert f"/fake/networkInterfaces/{scale_set_name()}_1_nic" in adapter.tags


def test_failed_tag_call_is_tried_again():
    adapter = vmss_adapter(plan=[PlannedMember("placed")] * 2, tag_errors=2)
    row = one(adapter)
    assert row.details["tag_errors"] == 2
    assert row.details["tagged"] == 6


def test_teardown_deletes_members_that_survive_scale_in():
    plan = [PlannedMember("placed", survives_scale_in=True), PlannedMember("placed")]
    adapter = vmss_adapter(plan=plan)
    row = one(adapter)
    assert row.details["teardown"] == {
        "verified": True,
        "remaining": 0,
        "deleted_directly": 1,
        "errors": [],
        "seconds": 5.0,
    }


def test_teardown_reports_unverified_when_a_vm_stays():
    plan = [
        PlannedMember("placed", survives_scale_in=True, undeletable=True),
        PlannedMember("placed"),
    ]
    row = one(vmss_adapter(plan=plan))
    assert row.outcome == "success"
    assert row.details["teardown"]["verified"] is False
    assert row.details["teardown"]["remaining"] == 1


def test_leftover_members_are_removed_before_the_probe():
    adapter = vmss_adapter(plan=[PlannedMember("placed")] * 2, scale_set_capacity=1)
    row = one(adapter)
    assert row.details["pre_teardown"]["verified"] is True
    assert row.outcome == "success"


def test_restricted_entry_writes_a_row_with_no_scale_call():
    from tests.azure.provisioning_fakes import restriction

    sku = b2s(capacity_reservation_supported=False, restrictions=(restriction(),))
    adapter = FakeAdapter(FakeClock(), skus=[sku])
    row = one(adapter)
    assert (row.outcome, row.error_code, row.details["skipped"]) == (
        "restricted",
        "NotAvailableForSubscription",
        True,
    )
    assert not any(c[0] == "update_scale_set" for c in adapter.calls)


def test_quota_ceiling_0_writes_quota_with_no_scale_call():
    adapter = vmss_adapter(usage=usages(family_free=1))
    row = one(adapter)
    assert (row.outcome, row.n_placed) == ("quota", None)
    assert not any(c[0] == "update_scale_set" for c in adapter.calls)


def test_n_is_capped_by_the_quota_ceiling():
    adapter = vmss_adapter(usage=usages(family_free=2), plan=[PlannedMember("placed")] * 2)
    row = one(adapter)
    assert (row.n_requested, row.n_placed, row.details["n_config"]) == (1, 1, 2)


def test_az_capacity_reservation_entries_are_skipped():
    adapter = FakeAdapter(FakeClock())
    assert run(adapter, [entry(probe_type="az_capacity_reservation")]) == []
    assert not any(c[0] in ("list_skus", "list_scale_sets") for c in adapter.calls)


def test_list_error_gives_an_error_row_and_no_exception():
    adapter = vmss_adapter(list_error=AzureCallError("AuthorizationFailed", "no"))
    row = one(adapter)
    assert (row.outcome, row.error_code) == ("error", "AuthorizationFailed")


@pytest.mark.parametrize(
    ("method", "exc"),
    [
        ("list_members", json.JSONDecodeError("Expecting value: <html>bad gateway</html>", "", 0)),
        ("list_members", AttributeError("'list' object has no attribute 'get'")),
        ("list_scale_sets", json.JSONDecodeError("Expecting value: <html>", "", 0)),
        ("list_scale_sets", AttributeError("'list' object has no attribute 'get'")),
    ],
)
def test_a_malformed_200_body_before_the_scale_out_is_an_error_row(method, exc, caplog):
    """A read before the scale-out that raises a non-Azure exception gives an `error`
    row for the cell and no scale-out. The message goes to the log as returned."""
    adapter = vmss_adapter()

    def bad(*args, **kwargs):
        raise exc

    setattr(adapter, method, bad)
    with caplog.at_level(logging.WARNING):
        row = one(adapter)
    assert row.outcome == "error"
    assert row.error_code == type(exc).__name__
    assert not [c for c in adapter.calls if c[0] == "update_scale_set"]
    assert str(exc) in caplog.text
    assert "allocation.unexpected_error" in caplog.text


def test_teardown_reads_a_missing_capacity_as_unknown_not_as_0():
    """No `sku.capacity` in the response never counts toward a verified teardown."""
    adapter = vmss_adapter(plan=[PlannedMember("placed")] * 2, get_capacity_missing=True)
    row = one(adapter)
    assert row.details["teardown"]["verified"] is False
    assert row.details["teardown"]["remaining"] == -1


def _teardown_with_a_leftover(adapter: FakeAdapter, clock: FakeClock):
    adapter.leftovers = [
        Member(
            id="/fake/virtualMachines/vm0",
            name="vm0",
            provisioning_state="Succeeded",
            power_state="running",
            os_disk_id="/fake/disks/vm0_disk",
        )
    ]
    adapter.scale_in_ignored = True  # the scale-in never ends: the teardown deletes the VM
    adapter.scale_set_capacity = 1
    settings = AllocationSettings(
        probe_timeout_seconds=60, poll_interval_seconds=5, teardown_timeout_seconds=120
    )
    scale_set = adapter._scale_set("vmss")
    tagger = MemberTagger(adapter, {"a": "b"})
    started = clock.monotonic()
    return started, settings, teardown(adapter, clock, "rg", scale_set, settings, tagger)


def test_teardown_sends_every_call_through_the_bounded_copy():
    """The get, the scale-in, the member list, the VM delete, and the tag go through
    the object that `for_teardown` returns, and its callable gives the remaining budget."""
    adapter = vmss_adapter()
    clock = adapter.clock
    started, settings, _ = _teardown_with_a_leftover(adapter, clock)
    assert len(adapter.teardown_remaining) == 1
    remaining = adapter.teardown_remaining[0]
    assert remaining() == started + settings.teardown_timeout_seconds - clock.monotonic()
    clock.t += 7
    assert remaining() == started + settings.teardown_timeout_seconds - clock.monotonic()
    names = set(adapter.bounded_calls)
    assert {"update_scale_set", "list_members", "get_scale_set", "delete_vm", "tag_resource"} <= (
        names
    )
    # Every call the teardown made is one of the bounded calls: none went to the original.
    assert len([c for c in adapter.calls]) == len(adapter.bounded_calls)


def test_a_failed_tag_in_the_teardown_does_not_stop_the_deletes():
    adapter = vmss_adapter()
    clock = adapter.clock

    def hung(resource_id, tags):
        raise TimeoutError("tag call timed out")

    adapter.tag_resource = hung
    _, _, result = _teardown_with_a_leftover(adapter, clock)
    assert "delete_vm" in adapter.bounded_calls
    assert "TimeoutError" in result["errors"]


def test_the_teardown_sends_the_deletes_before_a_tag_call_that_hangs():
    """A tag call that uses its whole timeout does not delay the VM deletes."""
    adapter = vmss_adapter()
    clock = adapter.clock
    events = []
    real_delete = adapter.delete_vm

    def delete_vm(rg, name):
        events.append(("delete_vm", clock.monotonic()))
        return real_delete(rg, name)

    def hung(resource_id, tags):
        events.append(("tag_resource", clock.monotonic()))
        clock.t += 120  # the call runs for its whole timeout
        raise TimeoutError("tag call timed out")

    adapter.delete_vm = delete_vm
    adapter.tag_resource = hung
    adapter.scale_in_lost_until = 0.0  # the scale-in ends at once and leaves the VM
    started, _, _ = _teardown_with_a_leftover(adapter, clock)
    names = [name for name, _ in events]
    assert names.index("delete_vm") < names.index("tag_resource")
    assert dict(events)["delete_vm"] == started


def test_member_states_are_counted_by_raw_pair():
    plan = [PlannedMember("placed"), PlannedMember("failed", "AllocationFailed")]
    adapter = vmss_adapter(plan=plan, scale_op_error=AzureCallError("AllocationFailed"))
    row = run(adapter)[0]
    assert row.details["member_states"] == [
        {"provisioning_state": "Creating", "power_state": "starting", "count": 1},
        {"provisioning_state": "Failed", "power_state": None, "count": 1},
    ]
    assert (row.details["n_failed"], row.details["n_pending"]) == (1, 0)


def test_member_states_keep_the_case_azure_returns():
    members = [
        Member("/1", "a", "Succeeded", "Running"),
        Member("/2", "b", "Succeeded", "Running"),
        Member("/3", "c", None, None),
    ]
    assert member_state_counts(members) == [
        {"provisioning_state": "Succeeded", "power_state": "Running", "count": 2},
        {"provisioning_state": None, "power_state": None, "count": 1},
    ]
    assert member_state(members[0]) == "placed"  # `Running` counts as placed, case aside


def test_member_states_of_16_members_stay_small():
    members = [Member(f"/{i}", f"m{i}", "Succeeded", "running") for i in range(16)]
    assert len(member_state_counts(members)) == 1


def test_scale_out_operation_error_row_keeps_every_code_in_order():
    plan = [PlannedMember("failed", "AllocationFailed")] * 2
    err = AzureCallError("ResourceOperationFailure", "x", details=("AllocationFailed", "Other"))
    row = run(vmss_adapter(plan=plan, scale_op_error=err))[0]
    assert row.error_code == "AllocationFailed"
    assert row.details["error_codes"] == ["ResourceOperationFailure", "AllocationFailed", "Other"]


def test_scale_out_call_error_row_keeps_every_code_in_order():
    err = AzureCallError("InvalidParameter", "x", details=("Detail1", "Detail2"))
    row = one(vmss_adapter(scale_error=err))
    assert row.error_code == "InvalidParameter"
    assert row.details["error_codes"] == ["InvalidParameter", "Detail1", "Detail2"]


def test_success_row_has_no_error_codes():
    adapter = vmss_adapter(plan=[PlannedMember("placed"), PlannedMember("placed")])
    row = one(adapter)
    assert "error_codes" not in row.details
    assert row.details["member_states"]
