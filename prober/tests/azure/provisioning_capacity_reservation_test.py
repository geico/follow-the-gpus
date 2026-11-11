"""The capacity reservation probe: each outcome, the timeout, and the delete. Spec 0010, 8."""

import json
import logging

from ftg_prober.azure.capacity_reservation import CapacityReservationProbe
from ftg_prober.azure.provisioning_adapter import AzureCallError
from tests.azure.provisioning_fakes import (
    RUN_ID,
    FakeAdapter,
    FakeClock,
    entry,
    group_name,
    make_ctx,
    usages,
    wire,
)


def run(adapter, targets=None, **section):
    probe = wire(CapacityReservationProbe(), adapter)
    return probe.run(make_ctx(targets or [entry()], **section))


def one(adapter, **section):
    (row,) = run(adapter, **section)
    return row


def test_success_creates_deletes_and_verifies():
    adapter = FakeAdapter(FakeClock())
    row = one(adapter)
    assert (row.probe_type, row.outcome, row.n_requested, row.n_placed) == (
        "az_capacity_reservation",
        "success",
        2,
        2,
    )
    assert (row.placement_group_mode, row.error_code, row.zone) == ("n/a", None, None)
    create = next(c for c in adapter.calls if c[0] == "create_reservation")
    assert create[1:] == (
        "ftg-probe",
        group_name("eastus"),
        "ftg-cr-b2s-eastus-0b6f1f9e",
        "eastus",
        "Standard_B2s",
        None,
        2,
    )
    assert adapter.tags["ftg-cr-b2s-eastus-0b6f1f9e"] == {
        "experiment": "ftg",
        "target": "b2s-eastus",
        "run_id": RUN_ID,
    }
    assert row.details["teardown"]["verified"] is True
    assert adapter.reservation is None
    assert row.latency_ms == 5000


def test_probe_rg_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("FTG_PROBE_RG", "lab-probe")
    adapter = FakeAdapter(FakeClock())
    one(adapter)
    create = next(c for c in adapter.calls if c[0] == "create_reservation")
    assert create[1:3] == ("lab-probe", group_name("eastus"))


def test_zonal_entry_passes_the_zone():
    adapter = FakeAdapter(FakeClock())
    row = run(adapter, [entry(zone=1)])[0]
    create = next(c for c in adapter.calls if c[0] == "create_reservation")
    assert create[3] == "ftg-cr-b2s-eastus-z1-0b6f1f9e"
    assert create[6] == "1"
    assert row.zone == "1"


def test_allocation_failed_is_no_capacity():
    adapter = FakeAdapter(FakeClock(), create_op_error=AzureCallError("AllocationFailed", "none"))
    row = run(adapter)[0]
    assert (row.outcome, row.n_placed, row.error_code) == ("no_capacity", 0, "AllocationFailed")
    assert row.details["teardown"]["verified"] is True
    assert any(c[0] == "delete_reservation" for c in adapter.calls)


def test_quota_error_at_create():
    err = AzureCallError(
        "OperationNotAllowed", "Operation results in exceeding quota limits of Core."
    )
    row = one(FakeAdapter(FakeClock(), create_error=err))
    assert (row.outcome, row.n_placed, row.error_code) == ("quota", None, "OperationNotAllowed")


def test_sku_not_available():
    err = AzureCallError("SkuNotAvailable", "not here")
    row = one(FakeAdapter(FakeClock(), create_op_error=err))
    assert (row.outcome, row.n_placed) == ("restricted_or_no_capacity", 0)


def test_other_error_code_is_error():
    row = one(FakeAdapter(FakeClock(), create_error=AzureCallError("BadRequest", "bad")))
    assert (row.outcome, row.error_code) == ("error", "BadRequest")


def test_create_that_never_finishes_times_out_and_is_deleted():
    adapter = FakeAdapter(FakeClock(), create_after=None)
    row = one(adapter, probe_timeout_seconds=20)
    assert (row.outcome, row.n_placed, row.latency_ms) == ("timeout", None, 20_000)
    assert row.details["teardown"]["verified"] is True


def test_delete_is_tried_again_after_a_conflict():
    adapter = FakeAdapter(FakeClock(), delete_failures=2)
    row = one(adapter)
    assert row.details["teardown"]["verified"] is True
    assert row.details["teardown"]["errors"] == ["Conflict", "Conflict"]
    assert [c[0] for c in adapter.calls].count("delete_reservation") == 3


def test_delete_not_verified_inside_the_teardown_timeout():
    adapter = FakeAdapter(FakeClock(), delete_failures=1000)
    row = one(adapter, teardown_timeout_seconds=30)
    assert row.outcome == "success"
    assert row.details["teardown"]["verified"] is False


def test_identical_entries_probe_once():
    adapter = FakeAdapter(FakeClock())
    rows = run(adapter, [entry(), entry()])
    assert len(rows) == 1
    assert [c[0] for c in adapter.calls].count("create_reservation") == 1


def test_n_is_capped_by_the_quota_ceiling():
    adapter = FakeAdapter(FakeClock(), usage=usages(family_free=2))
    row = one(adapter)
    assert (row.n_requested, row.n_placed, row.details["n_ceiling"]) == (1, 1, 1)


def test_az_vmss_flex_entries_are_skipped():
    adapter = FakeAdapter(FakeClock())
    assert run(adapter, [entry(probe_type="az_vmss_flex")]) == []
    assert not any(c[0] in ("list_skus", "create_reservation") for c in adapter.calls)


def test_success_records_n_even_when_the_get_shows_less():
    """A reservation succeeds as a whole. `sku.capacity` is the request, not a fill count."""
    row = one(FakeAdapter(FakeClock(), reserved_capacity=1))
    assert (row.outcome, row.n_placed) == ("success", 2)


def test_each_entry_is_emitted_before_the_next_and_a_stop_starts_no_new_entry():
    """Spec 0003, section 6B: the rows of an entry go to `ctx.emit` before the next entry
    makes a call, and once the process stops, no new entry starts."""
    adapter = FakeAdapter(FakeClock())
    probe = wire(CapacityReservationProbe(), adapter)
    targets = [entry(), entry(region="eastus2"), entry(region="westus")]
    ctx = make_ctx(targets)
    emitted: list[tuple[list[str], int]] = []

    def creates() -> int:
        return sum(1 for call in adapter.calls if call[0] == "create_reservation")

    ctx.emit_fn = lambda rows: emitted.append(([row.region for row in rows], creates()))
    ctx.stop_fn = lambda: len(emitted) >= 2
    assert probe.run(ctx) == []
    assert emitted == [(["eastus"], 1), (["eastus2"], 2)]
    assert creates() == 2
    assert not any(call[1:2] == ("westus",) for call in adapter.calls)


def test_a_non_azure_exception_from_the_group_list_is_an_error_row_and_no_write(caplog):
    """A malformed 200 body in the list of the groups gives an `error` row and no
    reservation write. The message goes to the log as returned."""
    adapter = FakeAdapter(FakeClock())
    exc = json.JSONDecodeError("Expecting value: <html>bad gateway</html>", "", 0)

    def bad(rg):
        raise exc

    adapter.list_reservation_groups = bad
    with caplog.at_level(logging.WARNING):
        row = one(adapter)
    assert (row.outcome, row.error_code) == ("error", "JSONDecodeError")
    assert row.details["reason"] == "reservation groups not listable"
    assert not [c for c in adapter.calls if c[0] == "create_reservation"]
    assert str(exc) in caplog.text


def test_success_row_keeps_the_capacity_that_azure_returns():
    """The row keeps `capacity` as the read of the reservation returns it, not the request."""
    row = one(FakeAdapter(FakeClock(), reserved_capacity=1))
    assert (row.outcome, row.n_placed) == ("success", 2)
    assert row.details["capacity"] == 1
    assert row.details["provisioning_state"] == "Succeeded"


def test_failed_operation_row_keeps_every_code_in_order():
    err = AzureCallError("ResourceOperationFailure", "x", details=("AllocationFailed", "Other"))
    row = run(FakeAdapter(FakeClock(), create_op_error=err))[0]
    assert row.error_code == "AllocationFailed"  # the primary code: the first known one
    assert row.details["error_codes"] == ["ResourceOperationFailure", "AllocationFailed", "Other"]


def test_error_at_create_row_keeps_every_code_in_order():
    err = AzureCallError("InvalidParameter", "x", details=("Detail1", "Detail2"))
    row = one(FakeAdapter(FakeClock(), create_error=err))
    assert row.error_code == "InvalidParameter"
    assert row.details["error_codes"] == ["InvalidParameter", "Detail1", "Detail2"]


def test_success_row_has_no_error_codes():
    assert "error_codes" not in one(FakeAdapter(FakeClock())).details
