"""The real Azure provisioning probes in Region lanes, on a virtual clock per Region.

Spec 0010, section 5, step 7: each entry has its own search deadline, so a Region with many real
cells gives each cell its search in every cycle. Spec 0003, section 6B: the lanes.
"""

import json

from ftg_prober.azure.capacity_reservation import CapacityReservationProbe
from ftg_prober.azure.vmss_flex import VmssFlexProbe
from ftg_prober.config import Config
from ftg_prober.scheduler import Scheduler
from tests.azure.provisioning_fakes import FakeAdapter, FakeClock, PlannedMember, b2s, usages
from tests.azure.provisioning_fakes import wire as wire_probe
from tests.test_lanes import AZURE_REGIONS, AZURE_SIZES, CADENCE_S

# The defaults: probe 90 s + teardown 60 s, the search deadline of 1 entry.
ENTRY_BUDGET_S = 90 + 60
# Azure answers "no capacity" this long after the request. 40 s lets the 2 attempts of N = 2
# (2, then 1) fit in 1 entry deadline, but not the attempts of 2 entries.
NO_CAPACITY_AFTER_S = 40.0


def no_capacity_adapter(skus):
    """Every reservation and every scale-out fails for lack of capacity, after a long wait."""
    failed = PlannedMember("failed", "AllocationFailed", ready_after=NO_CAPACITY_AFTER_S)
    return FakeAdapter(
        FakeClock(),
        skus=skus,
        usage=usages(family_free=10_000, total_free=10_000),
        reservation_limit=0,
        create_after=NO_CAPACITY_AFTER_S,
        plan=[failed] * 2,
    )


PROBE_TYPE_OF_ROUTE = {"reservation": "az_capacity_reservation", "vmss": "az_vmss_flex"}


def sku_info(name, route, vcpus):
    return b2s(name=name, vcpus=vcpus, capacity_reservation_supported=route == "reservation")


def matrix_entries(regions):
    """The Azure cells of ADR 0018, all real, N = 2: 5 entries per Region."""
    entries = []
    for region in regions:
        for sku, (route, _) in AZURE_SIZES.items():
            for mode in ["multi"]:
                entries.append(
                    {"sku": sku, "region": region, "zone": None, "n": 2}
                    | {"probe_type": PROBE_TYPE_OF_ROUTE[route], "placement_group_mode": mode}
                )
    return entries


def scheduler_for(entries, clock, store):
    section = {"regions": [], "cadence": {}, "provisioning_probes": entries}
    return Scheduler(
        [CapacityReservationProbe(), VmssFlexProbe()],
        config=Config(experiment="ftg", aws=None, azure=section),
        provider="azure",
        store=store,
        clock=clock,
        sleep=clock.sleep,
        now=clock.now,
    )


def wire_lanes(scheduler, adapters):
    """Give the lane copy of each probe in each Region the fake adapter of that Region."""
    for probe in scheduler.probes:
        for region, adapter in adapters.items():
            lane_probe = type(probe)()
            scheduler.lane_probes[(probe.probe_type, region)] = wire_probe(lane_probe, adapter)


def cycle(scheduler):
    """1 cycle: the lanes of each provisioning probe, as `run_forever` starts them when due."""
    for probe in scheduler.probes:
        scheduler.start_lanes(probe)
    scheduler.join_lanes()


def rows_of(store, keys):
    return [json.loads(line) for key in keys for line in store.objects[key].splitlines()]


def cell(row):
    return (row["sku"], row["region"], row["placement_group_mode"])


def entry_cell(entry):
    """A reservation row has no placement group mode: `n/a` (spec 0010, section 7.5)."""
    mode = entry["placement_group_mode"] if entry["probe_type"] == "az_vmss_flex" else "n/a"
    return (entry["sku"], entry["region"], mode)


def assert_full_search_for_each_cell(rows, entries):
    """Each cell has its 2 attempts of N = 2 (2, then 1), both `no_capacity`, with a verified
    teardown, and no row of the cell has a deadline stop. No row is written twice."""
    by_cell = {}
    for row in rows:
        by_cell.setdefault(cell(row), []).append(row)
    assert set(by_cell) == {entry_cell(e) for e in entries}
    for key, cell_rows in by_cell.items():
        attempts = [(r["n_requested"], r["outcome"]) for r in cell_rows]
        assert attempts == [(2, "no_capacity"), (1, "no_capacity")], key
        assert all("search_stopped" not in r["details"] for r in cell_rows), key
        assert all(r["details"]["teardown"]["verified"] for r in cell_rows), key


def test_3_real_cells_in_1_region_each_get_a_full_search_in_every_cycle(clock, memory_store):
    region = "eastus"
    sizes = {
        "Standard_NC24ads_A100_v4": ("reservation", 24),
        "Standard_NV36ads_A10_v5": ("reservation", 36),
        "Standard_NC4as_T4_v3": ("reservation", 4),
        "Standard_NC40ads_H100_v5": ("vmss", 40),
    }
    entries = [
        {"sku": sku, "region": region, "zone": None, "n": 2}
        | {"probe_type": PROBE_TYPE_OF_ROUTE[route], "placement_group_mode": "multi"}
        for sku, (route, _) in sizes.items()
    ]
    adapter = no_capacity_adapter([sku_info(s, r, v) for s, (r, v) in sizes.items()])
    scheduler = scheduler_for(entries, clock, memory_store)
    wire_lanes(scheduler, {region: adapter})
    for _ in range(2):
        before = len(memory_store.puts)
        started = adapter.clock.t
        cycle(scheduler)
        assert_full_search_for_each_cell(rows_of(memory_store, memory_store.puts[before:]), entries)
        # The lane runs its entries 1 after the other, each inside its own deadline.
        assert adapter.clock.t - started <= len(entries) * ENTRY_BUDGET_S


def test_the_full_azure_matrix_with_every_cell_real_and_no_capacity_fits_the_cadence(
    clock, memory_store
):
    entries = matrix_entries(AZURE_REGIONS)
    assert len(entries) == 45
    skus = [sku_info(s, r, v) for s, (r, v) in AZURE_SIZES.items()]
    adapters = {region: no_capacity_adapter(skus) for region in AZURE_REGIONS}
    scheduler = scheduler_for(entries, clock, memory_store)
    wire_lanes(scheduler, adapters)
    cycle(scheduler)
    assert_full_search_for_each_cell(rows_of(memory_store, memory_store.puts), entries)
    # The Regions run at the same time, so the cycle is the slowest Region. Each of its 7
    # entries ends inside its own deadline of 150 s: at most 5 x 150 s = 12.5 min.
    per_region = {region: adapter.clock.t for region, adapter in adapters.items()}
    assert max(per_region.values()) <= 5 * ENTRY_BUDGET_S <= CADENCE_S


def test_a_lane_with_n_max_on_every_reservation_entry_fits_the_cadence(clock, memory_store):
    """ADR 0023: a reservation entry with `n_max: 16` has a budget of 4 x 150 s. The longest lane
    of the reference file is eastus: 4 reservation entries (a control and 3 GPU sizes) and 1 scale
    set entry, 4 x 600 s + 1 x 150 s = 42.5 min. Every reservation step succeeds in 85 s (just
    under the probe timeout), so each of the 4 makes its 4 steps; the scale set entry fails in 2."""
    region = "eastus"
    control = {"sku": "Standard_D2as_v4", "region": region, "zone": None, "n": 2}
    control |= {"probe_type": "az_capacity_reservation", "placement_group_mode": "multi"}
    entries = [
        e | ({"n_max": 16} if e["probe_type"] == "az_capacity_reservation" else {})
        for e in [control] + matrix_entries([region])
        if "RTXPRO" not in e["sku"]
    ]
    skus = [sku_info(s, r, v) for s, (r, v) in AZURE_SIZES.items()]
    skus.append(sku_info("Standard_D2as_v4", "reservation", 2))
    failed = PlannedMember("failed", "AllocationFailed", ready_after=NO_CAPACITY_AFTER_S)
    adapter = FakeAdapter(
        FakeClock(),
        skus=skus,
        usage=usages(family_free=10_000, total_free=10_000),
        create_after=85.0,
        plan=[failed] * 2,
    )
    scheduler = scheduler_for(entries, clock, memory_store)
    wire_lanes(scheduler, {region: adapter})
    cycle(scheduler)
    rows = rows_of(memory_store, memory_store.puts)
    for e in entries:
        mine = [r for r in rows if cell(r) == entry_cell(e)]
        assert all("search_stopped" not in r["details"] for r in mine)
        if e["probe_type"] == "az_capacity_reservation":
            assert [r["n_requested"] for r in mine] == [2, 4, 8, 16]
    lane_budget = 4 * 4 * ENTRY_BUDGET_S + 1 * ENTRY_BUDGET_S
    assert lane_budget == 42.5 * 60
    assert adapter.clock.t <= lane_budget <= CADENCE_S
