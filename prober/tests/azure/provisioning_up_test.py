"""The upward search of the Azure provisioning probes. ADR 0023, spec 0010 section 5.

An entry with `n_max` goes up after a full success at C: min(2N, U), one request at a time, with
a verified teardown between the steps. The first step that is not a success ends the search.
"""

import pytest

from ftg_prober.azure.capacity_reservation import CapacityReservationProbe
from ftg_prober.azure.prechecks import up_steps
from ftg_prober.azure.vmss_flex import VmssFlexProbe
from tests.azure.provisioning_fakes import (
    FakeAdapter,
    FakeClock,
    PlannedMember,
    b2s,
    entry,
    make_ctx,
    usages,
    wire,
)


def reserve_up(adapter, n=2, n_max=16, **section):
    probe = wire(CapacityReservationProbe(), adapter)
    return probe.run(make_ctx([entry(n=n, n_max=n_max)], **section))


def attempts(rows):
    return [(r.n_requested, r.outcome) for r in rows]


def test_a_success_goes_up_by_doubling_to_n_max():
    adapter = FakeAdapter(FakeClock(), usage=usages(family_free=1000, total_free=1000))
    rows = reserve_up(adapter)
    assert attempts(rows) == [(2, "success"), (4, "success"), (8, "success"), (16, "success")]
    assert [r.details["search_step"] for r in rows] == [1, 2, 3, 4]
    assert [r.n_placed for r in rows] == [2, 4, 8, 16]
    # Only the rows above C say so, and they carry n_max.
    assert ["search_direction" in r.details for r in rows] == [False, True, True, True]
    assert [r.details.get("n_max") for r in rows] == [None, 16, 16, 16]
    assert {r.details["search_direction"] for r in rows[1:]} == {"up"}
    assert all(r.details["teardown"]["verified"] for r in rows)
    names = [c[0] for c in adapter.calls if c[0] in ("create_reservation", "delete_reservation")]
    assert names == ["create_reservation", "delete_reservation"] * 4


def test_the_last_step_is_capped_at_n_max():
    adapter = FakeAdapter(FakeClock(), usage=usages(family_free=1000, total_free=1000))
    rows = reserve_up(adapter, n=3, n_max=10)
    assert attempts(rows) == [(3, "success"), (6, "success"), (10, "success")]


def test_the_first_failure_ends_the_search_with_no_bisection():
    adapter = FakeAdapter(
        FakeClock(), reservation_limit=5, usage=usages(family_free=1000, total_free=1000)
    )
    rows = reserve_up(adapter)
    assert attempts(rows) == [(2, "success"), (4, "success"), (8, "no_capacity")]
    assert rows[-1].details["search_direction"] == "up"
    assert [c[7] for c in adapter.calls if c[0] == "create_reservation"] == [2, 4, 8]


def test_a_failure_at_c_halves_and_never_goes_up():
    adapter = FakeAdapter(FakeClock(), reservation_limit=1)
    rows = reserve_up(adapter, n=4)
    assert attempts(rows) == [(4, "no_capacity"), (2, "no_capacity"), (1, "success")]
    assert not any("search_direction" in r.details for r in rows)


def test_the_quota_ceiling_below_n_max_caps_the_steps():
    # B2s has 2 vCPUs: 20 free vCPUs give a ceiling of 10.
    adapter = FakeAdapter(FakeClock(), usage=usages(family_free=20, total_free=20))
    rows = reserve_up(adapter)
    assert attempts(rows) == [(2, "success"), (4, "success"), (8, "success"), (10, "success")]


def test_a_ceiling_at_c_makes_no_step_up():
    adapter = FakeAdapter(FakeClock(), usage=usages(family_free=4, total_free=4))
    assert attempts(reserve_up(adapter)) == [(2, "success")]


def test_an_entry_without_n_max_gives_todays_rows():
    adapter = FakeAdapter(FakeClock(), usage=usages(family_free=1000, total_free=1000))
    rows = wire(CapacityReservationProbe(), adapter).run(make_ctx([entry(n=2)]))
    assert attempts(rows) == [(2, "success")]
    assert "search_direction" not in rows[0].details
    assert "n_max" not in rows[0].details


def test_an_unverified_teardown_stops_the_search_going_up():
    adapter = FakeAdapter(
        FakeClock(), delete_failures=1000, usage=usages(family_free=1000, total_free=1000)
    )
    rows = reserve_up(adapter, teardown_timeout_seconds=30)
    assert attempts(rows) == [(2, "success")]
    assert rows[0].details["teardown"]["verified"] is False


def test_a_stop_starts_no_step_up():
    adapter = FakeAdapter(FakeClock(), usage=usages(family_free=1000, total_free=1000))
    ctx = make_ctx([entry(n=2, n_max=16)])
    ctx.stop_fn = lambda: any(c[0] == "delete_reservation" for c in adapter.calls)
    rows = wire(CapacityReservationProbe(), adapter).run(ctx)
    assert attempts(rows) == [(2, "success")]
    assert rows[0].details["search_stopped"] == "stop"


def test_the_entry_budget_is_600_s_for_2_to_16_and_150_s_without_n_max():
    assert [up_steps({"n": 2, "n_max": m}) for m in (3, 4, 5, 8, 16)] == [1, 1, 2, 2, 3]
    assert up_steps({"n": 3, "n_max": 16}) == 3
    assert up_steps({"n": 2}) == 0


def test_all_4_steps_fit_the_600_s_budget_at_the_slowest_allowed_step():
    # Each success takes 85 s (the probe timeout is 90 s): 4 steps end at 340 s. A budget of
    # 150 s (no steps up) would end the search with a deadline row after the first step.
    adapter = FakeAdapter(
        FakeClock(), create_after=85.0, usage=usages(family_free=1000, total_free=1000)
    )
    rows = reserve_up(adapter)
    assert len(rows) == 4
    assert all("search_stopped" not in r.details for r in rows)
    assert adapter.clock.t <= 600


def test_a_scale_set_goes_up_too():
    plan = [PlannedMember("placed")] * 16
    adapter = FakeAdapter(
        FakeClock(),
        plan=plan,
        skus=[b2s(capacity_reservation_supported=False)],
        usage=usages(family_free=1000, total_free=1000),
    )
    probe = wire(VmssFlexProbe(), adapter)
    rows = probe.run(make_ctx([entry(n=2, n_max=8)]))
    assert attempts(rows) == [(2, "success"), (4, "success"), (8, "success")]


def test_entries_that_differ_only_in_n_max_are_not_the_same_probe():
    adapter = FakeAdapter(FakeClock(), usage=usages(family_free=1000, total_free=1000))
    probe = wire(CapacityReservationProbe(), adapter)
    rows = probe.run(make_ctx([entry(n=2), entry(n=2, n_max=4)]))
    assert attempts(rows) == [(2, "success"), (2, "success"), (4, "success")]


@pytest.mark.parametrize("n_max", [4, 16])
def test_the_simulated_adapter_supports_the_steps_up(n_max):
    probe = wire(CapacityReservationProbe(), FakeAdapter(FakeClock()))
    ctx = make_ctx([entry(n=2, n_max=n_max, simulate=True, sim_script="success")])
    rows = probe.run(ctx)
    assert [r.n_requested for r in rows] == [2**i for i in range(1, n_max.bit_length())]
    assert {r.outcome for r in rows} == {"success"}
    assert all(r.simulated for r in rows)
