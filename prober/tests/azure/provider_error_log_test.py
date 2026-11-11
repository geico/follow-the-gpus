"""A provider error gives a row with no message and 1 log record with the message as returned.

Logs are logs, metrics are metrics. Spec 0003 section 8, spec 0009 A7, spec 0010.
"""

import logging

from ftg_prober.azure.capacity_reservation import CapacityReservationProbe
from ftg_prober.azure.prechecks import PrecheckProbe
from ftg_prober.azure.provisioning_adapter import AzureCallError
from ftg_prober.azure.vmss_flex import VmssFlexProbe
from tests.azure.provisioning_fakes import FakeAdapter, FakeClock, entry, make_ctx, wire

# A message with a space, a quote, and an ID-shaped token, as Azure returns it.
MESSAGE = "Allocation failed. /subscriptions/not-a-real-id isn't available"


def provider_lines(caplog):
    return [r.getMessage() for r in caplog.records if "code=" in r.getMessage()]


def assert_logged_once(caplog, rows, probe_type, region, code):
    assert rows
    for row in rows:
        assert "error_message" not in row.details
        assert "message" not in str(row.details).lower()
        assert MESSAGE not in repr(row)
    (line,) = provider_lines(caplog)
    assert f"probe_type={probe_type}" in line
    assert f"region={region}" in line
    assert f"code={code}" in line
    assert repr(MESSAGE) in line or MESSAGE in line


def test_capacity_reservation_error_logs_the_message_and_the_row_has_none(caplog):
    err = AzureCallError("BadRequest", MESSAGE, status=400)
    adapter = FakeAdapter(FakeClock(), create_error=err)
    with caplog.at_level(logging.WARNING):
        rows = wire(CapacityReservationProbe(), adapter).run(make_ctx([entry()]))
    assert_logged_once(caplog, rows, "az_capacity_reservation", "eastus", "BadRequest")
    assert "status=400" in provider_lines(caplog)[0]


def test_capacity_reservation_operation_error_logs_the_message(caplog):
    err = AzureCallError("AllocationFailed", MESSAGE)
    adapter = FakeAdapter(FakeClock(), create_op_error=err)
    with caplog.at_level(logging.WARNING):
        rows = wire(CapacityReservationProbe(), adapter).run(make_ctx([entry(n=1)]))  # 1 attempt
    assert [r.outcome for r in rows] == ["no_capacity"]
    assert_logged_once(caplog, rows, "az_capacity_reservation", "eastus", "AllocationFailed")


def test_unexpected_exception_puts_no_repr_in_the_row():
    class Boom(Exception):
        pass

    adapter = FakeAdapter(FakeClock())
    adapter.create_reservation = lambda *a, **k: (_ for _ in ()).throw(Boom(MESSAGE))
    (row,) = wire(CapacityReservationProbe(), adapter).run(make_ctx([entry()]))
    assert row.outcome == "error"
    assert "exception" not in row.details
    assert MESSAGE not in repr(row)


def test_vmss_flex_scale_error_logs_the_message_and_the_row_has_none(caplog):
    from tests.azure.provisioning_vmss_flex_test import vmss_adapter

    adapter = vmss_adapter(scale_error=AzureCallError("InternalServerError", MESSAGE))
    with caplog.at_level(logging.WARNING):
        rows = wire(VmssFlexProbe(), adapter).run(make_ctx([entry(sku="Standard_B2s")]))
    assert_logged_once(caplog, rows, "az_vmss_flex", "eastus", "InternalServerError")


def test_vmss_flex_operation_error_logs_the_message_and_the_row_has_none(caplog):
    from tests.azure.provisioning_fakes import PlannedMember
    from tests.azure.provisioning_vmss_flex_test import vmss_adapter

    adapter = vmss_adapter(
        plan=[PlannedMember("failed", "AllocationFailed")],
        scale_op_error=AzureCallError("AllocationFailed", MESSAGE),
    )
    with caplog.at_level(logging.WARNING):
        rows = wire(VmssFlexProbe(), adapter).run(make_ctx([entry(sku="Standard_B2s", n=1)]))
    assert_logged_once(caplog, rows, "az_vmss_flex", "eastus", "AllocationFailed")


def test_precheck_error_logs_the_message_and_the_row_has_none(caplog):
    adapter = FakeAdapter(FakeClock(), skus_error=AzureCallError("AuthorizationFailed", MESSAGE))
    with caplog.at_level(logging.WARNING):
        rows = wire(PrecheckProbe(), adapter).run(make_ctx([entry()]))
    assert [r.outcome for r in rows] == ["error"]
    assert_logged_once(caplog, rows, "az_precheck", "eastus", "AuthorizationFailed")


def test_vmss_flex_teardown_logs_a_failed_delete_vm(caplog):
    from tests.azure.provisioning_fakes import PlannedMember
    from tests.azure.provisioning_vmss_flex_test import vmss_adapter

    adapter = vmss_adapter(plan=[PlannedMember("placed", survives_scale_in=True)] * 2)

    def delete_vm(rg, name):
        raise AzureCallError("DeleteDenied", MESSAGE)

    adapter.delete_vm = delete_vm
    with caplog.at_level(logging.WARNING):
        wire(VmssFlexProbe(), adapter).run(make_ctx([entry(sku="Standard_B2s")]))
    lines = [m for m in provider_lines(caplog) if "call=delete_vm" in m]
    assert lines
    assert "code=DeleteDenied" in lines[0]
    assert repr(MESSAGE) in lines[0] or MESSAGE in lines[0]


def test_precheck_list_usage_error_logs_the_message(caplog):
    adapter = FakeAdapter(FakeClock())

    def list_usage(region):
        raise AzureCallError("UsageDenied", MESSAGE)

    adapter.list_usage = list_usage
    with caplog.at_level(logging.WARNING):
        rows = wire(PrecheckProbe(), adapter).run(make_ctx([entry()]))
    assert [r.outcome for r in rows] == ["error"]
    lines = [m for m in provider_lines(caplog) if "call=list_usage" in m]
    assert len(lines) == 1
    assert "code=UsageDenied" in lines[0]
    assert repr(MESSAGE) in lines[0] or MESSAGE in lines[0]


def test_vmss_flex_logs_the_error_of_a_failed_scale_in_operation(caplog):
    from tests.azure.provisioning_vmss_flex_test import vmss_adapter

    adapter = vmss_adapter(scale_in_op_error=AzureCallError("ScaleInDenied", MESSAGE))
    with caplog.at_level(logging.WARNING):
        rows = wire(VmssFlexProbe(), adapter).run(make_ctx([entry(sku="Standard_B2s", n=1)]))
    assert "ScaleInDenied" in rows[0].details["teardown"]["errors"]
    lines = [m for m in provider_lines(caplog) if "code=ScaleInDenied" in m]
    assert len(lines) == 1
    assert "call=update_scale_set" in lines[0]
    assert repr(MESSAGE) in lines[0] or MESSAGE in lines[0]


def test_vmss_flex_logs_the_message_of_a_failed_member(caplog):
    from tests.azure.provisioning_fakes import PlannedMember
    from tests.azure.provisioning_vmss_flex_test import vmss_adapter

    adapter = vmss_adapter(plan=[PlannedMember("failed", "AllocationFailed", message=MESSAGE)])
    with caplog.at_level(logging.WARNING):
        rows = wire(VmssFlexProbe(), adapter).run(make_ctx([entry(sku="Standard_B2s", n=1)]))
    assert MESSAGE not in repr(rows)
    lines = [m for m in provider_lines(caplog) if "call=member_instance_view" in m]
    assert len(lines) == 1
    assert "code=AllocationFailed" in lines[0]
    assert repr(MESSAGE) in lines[0] or MESSAGE in lines[0]
