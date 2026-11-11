"""Pre-checks, names, and the `az_precheck` probe. Spec 0010 sections 4 and 5."""

import json
import logging

import pytest

from ftg_prober.azure.prechecks import (
    PrecheckProbe,
    allocation_settings,
    name_prefix,
    reservation_target_tag,
    run_precheck,
    sku_slug,
    vmss_target_tag,
)
from ftg_prober.azure.provisioning_adapter import AzureCallError, SkuRestriction, UsageInfo
from ftg_prober.config import Config
from ftg_prober.probes import load_probes
from ftg_prober.scheduler import Scheduler
from tests.azure.provisioning_fakes import (
    FakeAdapter,
    FakeClock,
    b2s,
    entry,
    make_ctx,
    restriction,
    usages,
    wire,
)
from tests.conftest import MemoryStore


def precheck(adapter, target=None):
    return run_precheck(adapter, target or entry(), adapter.clock)


def test_the_capability_flag_is_a_fact_and_decides_nothing():
    """ADR 0022: the probe type of the entry decides what runs, not the capability flag."""
    adapter = FakeAdapter(FakeClock(), skus=[b2s(capacity_reservation_supported=False)])
    result = precheck(adapter)
    assert result.outcome == "success"
    assert result.details["capacity_reservation_supported"] is False
    assert not {"route", "route_source"} & set(result.details)
    assert not hasattr(result, "route")


def test_n_ceiling_is_the_smaller_free_quota_divided_by_vcpus():
    adapter = FakeAdapter(FakeClock(), usage=usages(family_free=7, total_free=20))
    result = precheck(adapter)
    assert result.n_ceiling == 3  # min(7, 20) // 2
    assert result.details["family_available_vcpus"] == 7
    assert result.details["total_available_vcpus"] == 20


def test_n_ceiling_is_0_when_quota_is_used_up():
    assert precheck(FakeAdapter(FakeClock(), usage=usages(family_free=1))).n_ceiling == 0


def test_n_ceiling_is_none_when_no_usage_row_matches():
    assert precheck(FakeAdapter(FakeClock(), usage=[])).n_ceiling is None


def test_location_restriction_gives_restricted_with_the_reason_code():
    adapter = FakeAdapter(FakeClock(), skus=[b2s(restrictions=(restriction("Location"),))])
    result = precheck(adapter)
    assert (result.outcome, result.error_code) == ("restricted", "NotAvailableForSubscription")
    assert ("list_usage", "eastus") not in adapter.calls


def test_zone_restriction_applies_only_to_that_zone():
    sku = b2s(restrictions=(restriction("Zone", zones=("2",)),))
    assert precheck(FakeAdapter(FakeClock(), skus=[sku]), entry(zone=2)).outcome == "restricted"
    assert precheck(FakeAdapter(FakeClock(), skus=[sku]), entry(zone="1")).outcome == "success"
    assert precheck(FakeAdapter(FakeClock(), skus=[sku]), entry(zone=None)).outcome == "success"


def test_size_missing_from_the_region_is_restricted():
    result = precheck(FakeAdapter(FakeClock(), skus=[b2s(name="Standard_D2s_v5")]))
    assert result.outcome == "restricted"


def test_zone_not_offered_is_restricted():
    adapter = FakeAdapter(FakeClock(), skus=[b2s(zones=("1",))])
    assert precheck(adapter, entry(zone="3")).outcome == "restricted"


def test_raw_usage_numbers_stay_next_to_the_derived_values():
    usage = [
        UsageInfo("cores", "Total Regional vCPUs", 12, 40),
        UsageInfo("standardBSFamily", "Standard BS Family vCPUs", 3, 10),
    ]
    details = precheck(FakeAdapter(FakeClock(), usage=usage)).details
    assert details["family_current_vcpus"] == 3
    assert details["family_limit_vcpus"] == 10
    assert details["total_current_vcpus"] == 12
    assert details["total_limit_vcpus"] == 40
    assert details["family_available_vcpus"] == 7
    assert details["total_available_vcpus"] == 28
    assert details["n_ceiling"] == 3  # min(7, 28) // 2


def test_missing_usage_row_gives_null_raw_and_derived_values():
    usage = [UsageInfo("cores", "Total Regional vCPUs", 4, 20)]
    details = precheck(FakeAdapter(FakeClock(), usage=usage)).details
    assert details["family_current_vcpus"] is None
    assert details["family_limit_vcpus"] is None
    assert details["family_available_vcpus"] is None
    assert (details["total_current_vcpus"], details["total_limit_vcpus"]) == (4, 20)
    assert details["n_ceiling"] == 8  # 16 // 2
    none = precheck(FakeAdapter(FakeClock(), usage=[])).details
    raw_keys = (
        "family_current_vcpus",
        "family_limit_vcpus",
        "total_current_vcpus",
        "total_limit_vcpus",
    )
    assert all(none[key] is None for key in raw_keys)


def test_size_with_no_restriction_stores_an_empty_list_and_the_zones():
    details = precheck(FakeAdapter(FakeClock())).details
    assert details["restrictions"] == []
    assert details["sku_zones"] == ["1", "2", "3"]


def test_restrictions_are_stored_as_returned_also_when_they_do_not_apply():
    other = SkuRestriction("Zone", "QuotaId", ("eastus",), ("2", "3"), ("eastus",))
    adapter = FakeAdapter(FakeClock(), skus=[b2s(zones=("1", "2", "3"), restrictions=(other,))])
    result = precheck(adapter, entry(zone="1"))
    assert (result.outcome, result.error_code) == ("success", None)
    assert result.details["restrictions"] == [
        {
            "type": "Zone",
            "reason_code": "QuotaId",
            "values": ["eastus"],
            "locations": ["eastus"],
            "zones": ["2", "3"],
        }
    ]
    assert result.details["sku_zones"] == ["1", "2", "3"]


def test_restriction_that_applies_is_stored_with_the_derived_reason():
    adapter = FakeAdapter(FakeClock(), skus=[b2s(restrictions=(restriction("Location"),))])
    result = precheck(adapter)
    assert result.outcome == "restricted"
    assert result.details["reason"] == "Location restriction"
    assert result.details["restrictions"] == [
        {
            "type": "Location",
            "reason_code": "NotAvailableForSubscription",
            "values": ["eastus"],
            "locations": ["eastus"],
            "zones": [],
        }
    ]
    assert "family_current_vcpus" not in result.details  # no Usage call


def test_location_restriction_with_only_values_applies():
    only_values = SkuRestriction("Location", "NotAvailableForSubscription", (), (), ("eastus",))
    adapter = FakeAdapter(FakeClock(), skus=[b2s(restrictions=(only_values,))])
    result = precheck(adapter)
    assert result.outcome == "restricted"
    assert result.details["restrictions"][0]["locations"] == []
    assert result.details["restrictions"][0]["values"] == ["eastus"]


def test_sku_call_error_gives_error():
    adapter = FakeAdapter(FakeClock(), skus_error=AzureCallError("AuthorizationFailed", "no"))
    result = precheck(adapter)
    assert (result.outcome, result.error_code) == ("error", "AuthorizationFailed")


def test_names_follow_spec_0005():
    target = entry(sku="Standard_ND96isr_H100_v5", placement_group_mode="multi")
    assert sku_slug("Standard_B2s") == "b2s"
    assert vmss_target_tag(target) == "nd96isr-h100-v5-eastus-multi"
    assert reservation_target_tag(entry(zone=1)) == "b2s-eastus-z1"
    assert name_prefix("ftg-probe") == "ftg"
    assert name_prefix("lab-probe") == "lab"
    assert name_prefix("other") == "ftg"


def test_settings_defaults_and_validation():
    settings = allocation_settings({})
    assert (
        settings.probe_timeout_seconds,
        settings.poll_interval_seconds,
        settings.teardown_timeout_seconds,
    ) == (90, 5, 60)
    with pytest.raises(ValueError, match="probe_timeout_seconds"):
        allocation_settings({"probe_timeout_seconds": 0})


def test_precheck_probe_writes_1_row_per_entry():
    adapter = FakeAdapter(FakeClock(), usage=usages(family_free=10))
    probe = wire(PrecheckProbe(), adapter)
    targets = [entry(), entry(zone=1)]
    rows = probe.run(make_ctx(targets))
    assert [r.probe_type for r in rows] == ["az_precheck", "az_precheck"]
    assert [r.placement_group_mode for r in rows] == ["multi", "multi"]
    row = rows[0]
    assert (row.simulated, row.outcome, row.n_requested, row.n_placed) == (
        False,
        "success",
        2,
        None,
    )
    assert row.details["entry_probe_type"] == "az_vmss_flex"
    assert row.details["n_ceiling"] == 5
    assert set(row.details) >= {"entry_probe_type", "n_ceiling", "vcpus", "family"}
    assert not {"route", "route_source"} & set(row.details)
    assert set(row.details) >= {"sku_zones", "restrictions", "family_limit_vcpus"}
    json.loads(row.to_json())


def test_precheck_probe_emits_each_entry_and_a_stop_starts_no_new_entry():
    """Spec 0003, section 5.1 rule 7: the rows of an entry go to `ctx.emit` before the next
    entry makes a call, and once the process stops, no new entry starts."""
    adapter = FakeAdapter(FakeClock(), usage=usages(family_free=10))
    probe = wire(PrecheckProbe(), adapter)
    targets = [entry(), entry(zone=1), entry(region="eastus2")]
    ctx = make_ctx(targets)
    emitted: list[tuple[int, int]] = []
    ctx.emit_fn = lambda rows: emitted.append((len(rows), len(adapter.calls)))
    ctx.stop_fn = lambda: len(emitted) >= 2
    assert probe.run(ctx) == []
    assert [n for n, _ in emitted] == [1, 1]
    calls_after_2 = emitted[-1][1]
    assert len(adapter.calls) == calls_after_2  # the 3rd entry made no provider call


def test_precheck_probe_returns_all_rows_and_the_scheduler_splits_them_by_simulated():
    """2 real entries and 1 simulated entry. The probe writes no row. The core splits by value."""
    store = MemoryStore()
    adapter = FakeAdapter(FakeClock())
    probe = wire(PrecheckProbe(), adapter)
    targets = [
        entry(),
        entry(region="eastus2", simulate=True),
        entry(zone=1),
    ]
    rows = probe.run(make_ctx(targets, store=store))
    assert [r.simulated for r in rows] == [False, True, False]
    assert store.objects == {}
    # The simulated entry made no call on the real adapter.
    assert all(call[1:2] != ("eastus2",) for call in adapter.calls)

    config = Config(experiment="ftg", aws=None, azure=make_ctx(targets).config)
    scheduler = Scheduler([probe], config=config, provider="azure", store=store)
    scheduler.run_probe(probe)
    # The probe emits each entry at once: 1 object per entry.
    assert len(store.puts) == 3
    assert all(
        k.startswith("experiment=ftg/provider=azure/probe_type=az_precheck/") for k in store.puts
    )
    all_rows = [
        json.loads(line) for key in store.puts for line in store.objects[key].decode().splitlines()
    ]
    real_rows = [r for r in all_rows if not r["simulated"]]
    simulated_rows = [r for r in all_rows if r["simulated"]]
    assert [r["placement_group_mode"] for r in real_rows] == ["multi", "multi"]
    assert [r["region"] for r in simulated_rows] == ["eastus2"]
    assert simulated_rows[0]["details"]["entry_probe_type"] == "az_vmss_flex"


def test_loader_finds_the_3_azure_provisioning_probes():
    found = {p.probe_type for p in load_probes(["ftg_prober.azure"])}
    assert {"az_precheck", "az_capacity_reservation", "az_vmss_flex"} <= found


@pytest.mark.parametrize("method", ["list_skus", "list_usage"])
@pytest.mark.parametrize(
    "exc",
    [
        json.JSONDecodeError("Expecting value: <html>bad gateway</html>", "", 0),
        AttributeError("'list' object has no attribute 'get'"),
    ],
)
def test_a_non_azure_exception_from_a_precheck_list_is_an_error_row(method, exc, caplog):
    """A malformed 200 body gives an `error` row whose code is the exception type.
    The message goes to the log as returned."""
    adapter = FakeAdapter(FakeClock())

    def bad(region):
        raise exc

    setattr(adapter, method, bad)
    with caplog.at_level(logging.WARNING):
        rows = wire(PrecheckProbe(), adapter).run(make_ctx([entry()]))
    assert [(r.outcome, r.error_code) for r in rows] == [("error", type(exc).__name__)]
    assert str(exc) in caplog.text
    assert "allocation.unexpected_error" in caplog.text
    assert f"call={method}" in caplog.text


def test_an_exception_in_1_precheck_entry_gives_an_error_row_and_the_next_entry_runs(caplog):
    """A SKU with no name raises after the list calls. The entry gets an `error`
    row with the exception type as code, the message goes to the log, and the next entry runs."""
    adapter = FakeAdapter(FakeClock(), usage=usages(family_free=10))
    original = adapter.list_skus

    def skus(region):
        return [b2s(name=None)] if region == "westus" else original(region)

    adapter.list_skus = skus
    targets = [entry(region="westus"), entry(region="eastus")]
    with caplog.at_level(logging.WARNING):
        rows = wire(PrecheckProbe(), adapter).run(make_ctx(targets))
    assert [(r.region, r.outcome, r.error_code) for r in rows] == [
        ("westus", "error", "AttributeError"),
        ("eastus", "success", None),
    ]
    assert rows[0].details["entry_probe_type"] == "az_vmss_flex"
    assert "allocation.failed" in caplog.text
    assert "'NoneType' object has no attribute 'lower'" in caplog.text
    json.loads(rows[0].to_json())
