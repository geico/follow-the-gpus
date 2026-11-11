"""A simulated job: scripted outcomes, no Azure call, no real sleep. Spec 0010 section 9."""

import json
import time

import pytest

from ftg_prober.azure.capacity_reservation import CapacityReservationProbe
from ftg_prober.azure.prechecks import PrecheckProbe
from ftg_prober.azure.sim import DEFAULT_SIM_SCRIPT, script_of
from ftg_prober.azure.vmss_flex import VmssFlexProbe
from ftg_prober.config import Config, load_config
from ftg_prober.probes import Clients, load_probes
from ftg_prober.scheduler import Scheduler
from tests.azure.provisioning_fakes import entry, make_ctx, type_entries
from tests.conftest import TARGETS_PATH, MemoryStore


def no_real_adapter(ctx):
    raise AssertionError("local mode must not build a real adapter")


def sim_probe(cls):
    probe = cls()
    probe.adapter_factory = no_real_adapter
    probe.clock_factory = lambda: pytest.fail("local mode must not use the real clock")
    return type_entries(probe)


EXPECTED = {
    "success": ("success", 2, None),
    "partial": ("no_capacity", 0, "AllocationFailed"),
    "allocation_failed": ("no_capacity", 0, "AllocationFailed"),
    "hang": ("timeout", None, None),
    "quota": ("quota", None, "OperationNotAllowed"),
}


def test_default_script_has_the_5_faults():
    assert set(DEFAULT_SIM_SCRIPT) == set(EXPECTED)
    assert script_of(entry()) == list(DEFAULT_SIM_SCRIPT)
    assert script_of(entry(sim_script=["hang"])) == ["hang"]
    assert script_of(entry(sim_script="quota")) == ["quota"]


def test_vmss_sim_cycles_the_default_script_per_run():
    probe = sim_probe(VmssFlexProbe)
    target = entry(simulate=True)
    seen = []
    started = time.monotonic()
    for _ in range(len(DEFAULT_SIM_SCRIPT) + 1):
        row = probe.run(make_ctx([target]))[0]  # a failed first attempt is followed by a halved one
        assert row.simulated is True
        seen.append((row.details["sim_step"], row.outcome, row.n_placed, row.error_code))
    assert time.monotonic() - started < 2  # the hang step uses the virtual clock
    steps = [s[0] for s in seen]
    assert steps == [*DEFAULT_SIM_SCRIPT, DEFAULT_SIM_SCRIPT[0]]
    for step, outcome, n_placed, code in seen:
        assert (outcome, n_placed, code) == EXPECTED[step], step


@pytest.mark.parametrize("step", sorted(EXPECTED))
def test_vmss_sim_step_from_the_entry_script(step):
    row = sim_probe(VmssFlexProbe).run(make_ctx([entry(simulate=True, sim_script=[step])]))[0]
    assert (row.outcome, row.n_placed, row.error_code) == EXPECTED[step]
    assert row.details["teardown"]["verified"] is True
    assert row.details["tagged"] == (6 if step not in ("quota",) else 0)


RESERVATION_EXPECTED = {
    "success": ("success", 2, None),
    "partial": ("no_capacity", 0, "AllocationFailed"),
    "allocation_failed": ("no_capacity", 0, "AllocationFailed"),
    "hang": ("timeout", None, None),
    "quota": ("quota", None, "OperationNotAllowed"),
}


@pytest.mark.parametrize("step", sorted(RESERVATION_EXPECTED))
def test_reservation_sim_step(step):
    target = entry(simulate=True, sim_script=[step])
    row = sim_probe(CapacityReservationProbe).run(make_ctx([target]))[0]
    assert (row.outcome, row.n_placed, row.error_code) == RESERVATION_EXPECTED[step]
    assert row.details["teardown"]["verified"] is True


def test_unknown_step_gives_an_error_row():
    (row,) = sim_probe(VmssFlexProbe).run(make_ctx([entry(simulate=True, sim_script=["boom"])]))
    assert (row.outcome, row.error_code) == ("error", "SimUnknownStep")


def test_run_wide_simulate_runs_every_entry_with_no_azure_call():
    ctx = make_ctx([entry(), entry(region="eastus2")], simulate=True)
    rows = sim_probe(VmssFlexProbe).run(ctx)
    assert [r.simulated for r in rows] == [True, True]
    assert (
        sim_probe(CapacityReservationProbe).run(ctx) == []
    )  # the entries are az_vmss_flex entries
    assert len(sim_probe(PrecheckProbe).run(ctx)) == 2


def test_scheduler_runs_the_repo_config_in_local_mode_with_no_azure_client():
    """The scheduler calls the provisioning probes in local mode. No client, no real wait."""

    def no_client(*args):
        raise AssertionError(f"no cloud client in local mode: {args}")

    config = load_config(TARGETS_PATH)
    azure_only = Config(experiment=config.experiment, aws=None, azure=config.azure)
    probes = [
        p
        for p in load_probes(["ftg_prober.azure"])
        if p.probe_type in ("az_precheck", "az_capacity_reservation", "az_vmss_flex")
    ]
    store = MemoryStore()
    scheduler = Scheduler(
        probes,
        config=azure_only,
        provider="azure",
        store=store,
        clients=Clients(boto3_factory=no_client, azure_factory=no_client),
        simulate=True,
    )
    started = time.monotonic()
    scheduler.run_once()
    assert time.monotonic() - started < 2
    keys = sorted(store.objects)
    assert all(k.startswith("experiment=ftg-20261014-example/provider=azure/") for k in keys)
    types = {k.split("probe_type=")[1].split("/")[0] for k in keys}
    assert types == {"az_precheck", "az_capacity_reservation", "az_vmss_flex"}
    # A provisioning probe writes the rows of each entry at once: 1 object per entry, with 1 row
    # per step. An entry with `n_max` has 1 row per step up as well (ADR 0023).
    for probe_type in ("az_capacity_reservation", "az_vmss_flex"):
        probe_keys = [k for k in keys if f"probe_type={probe_type}" in k]
        rows = [json.loads(x) for k in probe_keys for x in store.objects[k].decode().splitlines()]
        entries = [e for e in config.azure["provisioning_probes"] if e["probe_type"] == probe_type]
        assert len(probe_keys) == len(entries) > 0
        # The default `success` script goes 2, 4, 8, 16 on a reservation entry (n_max 16).
        assert len(rows) == len(entries) * (4 if probe_type == "az_capacity_reservation" else 1)
