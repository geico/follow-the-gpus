"""The halving search of the Azure provisioning probes. ADR 0009 section 2, spec 0010 section 5.

The fakes answer like Azure: a reservation over the limit fails as a whole with
`AllocationFailed`; a scale-out over the limit leaves members `Failed` with that code.
"""

import math

import pytest

from ftg_prober.azure.capacity_reservation import CapacityReservationProbe
from ftg_prober.azure.provisioning_adapter import AzureCallError
from ftg_prober.azure.vmss_flex import VmssFlexProbe
from tests.azure.provisioning_fakes import (
    ALLOCATION_FAILED_MESSAGE,
    FakeAdapter,
    FakeClock,
    PlannedMember,
    b2s,
    entry,
    make_ctx,
    usages,
    wire,
)


def reserve(adapter, n, **section):
    probe = wire(CapacityReservationProbe(), adapter)
    return probe.run(make_ctx([entry(n=n)], **section))


def scale(adapter, n, **section):
    probe = wire(VmssFlexProbe(), adapter)
    return probe.run(make_ctx([entry(n=n)], **section))


def vmss_adapter(limit, total=8, **kwargs):
    """`limit` members place; the rest fail with `AllocationFailed`, as Azure reports them."""
    plan = [PlannedMember("placed")] * limit + [PlannedMember("failed", "AllocationFailed")] * (
        total - limit
    )
    kwargs.setdefault("skus", [b2s(capacity_reservation_supported=False)])
    return FakeAdapter(FakeClock(), plan=plan, **kwargs)


def attempts(rows):
    return [(r.n_requested, r.outcome) for r in rows]


def test_reservation_finds_the_largest_n_that_fits():
    adapter = FakeAdapter(FakeClock(), reservation_limit=5)
    rows = reserve(adapter, 8)
    assert attempts(rows) == [
        (8, "no_capacity"),
        (4, "success"),
        (6, "no_capacity"),
        (5, "success"),
    ]
    assert [r.details["search_step"] for r in rows] == [1, 2, 3, 4]
    assert [r.n_placed for r in rows] == [0, 4, 0, 5]
    assert {r.run_id for r in rows} == {rows[0].run_id}
    assert all(r.error_code in (None, "AllocationFailed") for r in rows)


def test_each_attempt_is_deleted_and_verified_before_the_next():
    adapter = FakeAdapter(FakeClock(), reservation_limit=1)
    rows = reserve(adapter, 4)
    assert attempts(rows) == [(4, "no_capacity"), (2, "no_capacity"), (1, "success")]
    names = [c[0] for c in adapter.calls if c[0] in ("create_reservation", "delete_reservation")]
    assert names == ["create_reservation", "delete_reservation"] * 3
    assert all(r.details["teardown"]["verified"] for r in rows)
    assert adapter.reservation is None


@pytest.mark.parametrize("n", [1, 2, 3, 8, 32])
def test_reservation_with_no_capacity_stays_within_the_attempt_bound(n):
    rows = reserve(FakeAdapter(FakeClock(), reservation_limit=0), n)
    assert len(rows) <= 1 + math.ceil(math.log2(n))
    assert rows[-1].n_requested == 1
    assert {r.outcome for r in rows} == {"no_capacity"}


def test_reservation_that_fits_at_the_first_attempt_stops():
    rows = reserve(FakeAdapter(FakeClock()), 8)
    assert attempts(rows) == [(8, "success")]


def test_reservation_quota_error_stops_the_search():
    err = AzureCallError(
        "OperationNotAllowed", "Operation results in exceeding quota limits of Core."
    )
    adapter = FakeAdapter(FakeClock(), create_error=err)
    rows = reserve(adapter, 8)
    assert attempts(rows) == [(8, "quota")]
    assert [c[0] for c in adapter.calls].count("create_reservation") == 1


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (AzureCallError("SkuNotAvailable", "not here"), "restricted_or_no_capacity"),
        (AzureCallError("AuthorizationFailed", "no", status=403), "error"),
    ],
)
def test_reservation_other_errors_stop_the_search(error, outcome):
    adapter = FakeAdapter(FakeClock(), create_op_error=error)
    assert attempts(reserve(adapter, 8)) == [(8, outcome)]


def test_reservation_timeout_stops_the_search_and_uses_the_probe_timeout():
    adapter = FakeAdapter(FakeClock(), create_after=None)
    rows = reserve(adapter, 8, probe_timeout_seconds=20)
    assert attempts(rows) == [(8, "timeout")]
    assert rows[0].latency_ms == 20_000


def test_reservation_unverified_delete_stops_the_search_within_the_teardown_timeout():
    adapter = FakeAdapter(FakeClock(), reservation_limit=0, delete_failures=1000)
    rows = reserve(adapter, 8, teardown_timeout_seconds=30)
    assert attempts(rows) == [(8, "no_capacity")]
    assert rows[0].details["teardown"]["verified"] is False
    assert adapter.clock.t < 100


def test_reservation_search_starts_at_the_quota_ceiling():
    adapter = FakeAdapter(FakeClock(), reservation_limit=1, usage=usages(family_free=4))
    rows = reserve(adapter, 8)  # 2 vCPUs for each B2s: ceiling 2
    assert attempts(rows) == [(2, "no_capacity"), (1, "success")]
    assert rows[0].details["n_config"] == 8


def test_error_message_is_logged_not_stored(caplog):
    rows = reserve(FakeAdapter(FakeClock(), reservation_limit=0), 2)
    assert ALLOCATION_FAILED_MESSAGE not in repr(rows)


def test_vmss_finds_the_largest_n_that_fits():
    adapter = vmss_adapter(limit=5)
    rows = scale(adapter, 8)
    assert attempts(rows) == [
        (8, "no_capacity"),
        (4, "success"),
        (6, "no_capacity"),
        (5, "success"),
    ]
    assert [r.details["search_step"] for r in rows] == [1, 2, 3, 4]
    assert [r.n_placed for r in rows] == [0, 4, 0, 5]
    assert rows[0].details["n_placed"] == 5  # the raw count of the short fill
    assert all(r.details["teardown"]["verified"] for r in rows)
    assert adapter.members == {}


def test_vmss_each_attempt_scales_in_before_the_next():
    adapter = vmss_adapter(limit=0)
    rows = scale(adapter, 4)
    assert attempts(rows) == [(4, "no_capacity"), (2, "no_capacity"), (1, "no_capacity")]
    sets = [c[2] for c in adapter.calls if c[0] == "update_scale_set"]
    zeros = [i for i, capacity in enumerate(sets) if capacity == 0]
    outs = [i for i, capacity in enumerate(sets) if capacity != 0]
    assert [sets[i] for i in outs] == [4, 2, 1]
    assert all(any(z > o for z in zeros) for o in outs)
    assert all(r.details["teardown"]["verified"] for r in rows)


def test_vmss_stops_at_the_first_failed_member():
    plan = [PlannedMember("failed", "AllocationFailed"), PlannedMember("hang")]
    adapter = FakeAdapter(FakeClock(), plan=plan, skus=[b2s(capacity_reservation_supported=False)])
    row = scale(adapter, 2)[0]
    assert (row.outcome, row.n_placed) == ("no_capacity", 0)
    assert row.latency_ms < 90_000  # it did not wait for the pending member to time out


def test_vmss_quota_error_stops_the_search():
    err = AzureCallError(
        "OperationNotAllowed", "Operation results in exceeding quota limits of Core."
    )
    adapter = vmss_adapter(limit=0, scale_error=err)
    assert attempts(scale(adapter, 8)) == [(8, "quota")]


def test_vmss_timeout_stops_the_search():
    adapter = vmss_adapter(limit=8, scale_op_after=None)
    adapter.plan = [PlannedMember("hang")] * 8
    rows = scale(adapter, 8, probe_timeout_seconds=20)
    assert attempts(rows) == [(8, "timeout")]


def test_vmss_unverified_teardown_stops_the_search():
    plan = [PlannedMember("failed", "AllocationFailed", undeletable=True)] * 8
    adapter = FakeAdapter(FakeClock(), plan=plan, skus=[b2s(capacity_reservation_supported=False)])
    rows = scale(adapter, 8, teardown_timeout_seconds=30)
    assert attempts(rows) == [(8, "no_capacity")]
    assert rows[0].details["teardown"]["verified"] is False


def test_vmss_waits_for_the_code_of_a_failed_member():
    # The member is `Failed` before its instance view shows the code; the row is not `error`.
    plan = [PlannedMember("failed", "AllocationFailed", code_after=6.0), PlannedMember("hang")]
    adapter = FakeAdapter(
        FakeClock(),
        plan=plan,
        skus=[b2s(capacity_reservation_supported=False)],
        scale_op_after=None,
    )
    row = scale(adapter, 2)[0]
    assert (row.outcome, row.error_code) == ("no_capacity", "AllocationFailed")


def test_vmss_failed_member_with_no_code_is_an_error_when_the_operation_is_done():
    plan = [PlannedMember("failed", None), PlannedMember("hang")]
    adapter = FakeAdapter(FakeClock(), plan=plan, skus=[b2s(capacity_reservation_supported=False)])
    row = scale(adapter, 2)[0]
    assert (row.outcome, row.error_code) == ("error", None)


def test_search_stops_before_the_deadline_and_returns_the_rows_so_far():
    # Budget 40 s: each attempt takes about 12 s with its delete, so the 8th-halving
    # search (8, 4, 2, 1) cannot finish; it stops with the rows written so far.
    adapter = FakeAdapter(FakeClock(), reservation_limit=0, create_after=10.0)
    rows = reserve(adapter, 8, probe_timeout_seconds=20, teardown_timeout_seconds=20)
    assert 1 <= len(rows) < 4
    assert rows[-1].details["search_stopped"] == "deadline"
    assert all("search_stopped" not in r.details for r in rows[:-1])
    assert adapter.clock.t <= 40


def test_search_starts_no_new_attempt_when_the_process_stops():
    adapter = FakeAdapter(FakeClock(), reservation_limit=0, create_after=10.0)
    ctx = make_ctx([entry(n=8)])
    ctx.stop_fn = lambda: adapter.clock.t > 0  # true after the first attempt
    rows = wire(CapacityReservationProbe(), adapter).run(ctx)
    assert len(rows) == 1
    assert rows[0].details["search_stopped"] == "stop"


@pytest.mark.parametrize(
    ("probe", "create", "crs"),
    [
        (CapacityReservationProbe, "create_reservation", True),
        (VmssFlexProbe, "update_scale_set", False),
    ],
)
def test_a_stop_during_the_precheck_starts_no_first_attempt(probe, create, crs):
    adapter = FakeAdapter(FakeClock(), skus=[b2s(capacity_reservation_supported=crs)])
    ctx = make_ctx([entry(n=2)])
    ctx.stop_fn = lambda: any(c[0] == "list_skus" for c in adapter.calls)
    rows = wire(probe(), adapter).run(ctx)
    assert [(r.outcome, r.details["search_stopped"]) for r in rows] == [("error", "stop")]
    assert not any(c[0] == create for c in adapter.calls)


def test_a_search_with_time_left_has_no_stop_reason():
    adapter = FakeAdapter(FakeClock(), reservation_limit=5)
    rows = reserve(adapter, 8)
    assert all("search_stopped" not in r.details for r in rows)


def test_each_entry_of_a_call_has_its_own_deadline():
    # Budget 40 s per entry; each entry takes about 12 s. With 1 deadline for the call, only 1.5
    # entries would fit and the later entries would get a `deadline` row in every cycle.
    adapter = FakeAdapter(FakeClock(), reservation_limit=0, create_after=10.0)
    probe = wire(CapacityReservationProbe(), adapter)
    targets = [entry(n=1, region=r) for r in ("eastus", "westus", "northeurope")]
    ctx = make_ctx(targets, probe_timeout_seconds=20, teardown_timeout_seconds=20)
    rows = probe.run(ctx)
    assert [(r.region, r.outcome) for r in rows] == [
        ("eastus", "no_capacity"),
        ("westus", "no_capacity"),
        ("northeurope", "no_capacity"),
    ]
    assert all("search_stopped" not in r.details for r in rows)
    assert adapter.clock.t <= 3 * 40


class StopOnSkuAdapter(FakeAdapter):
    """Sets the stop flag when the reservation of `stop_sku` is requested, as a SIGTERM that
    arrives during that attempt."""

    stop_sku: str = ""
    stopped_at: float | None = None

    def create_reservation(self, rg, group, name, region, sku, zone, capacity, tags):
        if sku == self.stop_sku and self.stopped_at is None:
            self.stopped_at = self.clock.t
        return super().create_reservation(rg, group, name, region, sku, zone, capacity, tags)


def test_a_stop_during_the_2nd_entry_starts_no_new_attempt_and_drains_in_the_stop_timeout():
    skus = ["Standard_B2s", "Standard_B4ms", "Standard_B8ms"]
    adapter = StopOnSkuAdapter(
        FakeClock(),
        skus=[b2s(name=sku) for sku in skus],
        reservation_limit=0,
        create_after=40.0,
    )
    adapter.stop_sku = skus[1]
    ctx = make_ctx([entry(sku=sku, n=2) for sku in skus])
    ctx.stop_fn = lambda: adapter.stopped_at is not None
    rows = wire(CapacityReservationProbe(), adapter).run(ctx)
    # Entry 1 made its full search; entry 2 ended its attempt in progress and started no new
    # one; entry 3 made no call and has no row.
    assert [(r.sku, r.n_requested, r.outcome) for r in rows] == [
        (skus[0], 2, "no_capacity"),
        (skus[0], 1, "no_capacity"),
        (skus[1], 2, "no_capacity"),
    ]
    assert rows[-1].details["search_stopped"] == "stop"
    assert not any(c[0] == "create_reservation" and c[5] == skus[2] for c in adapter.calls)
    # Every attempt ended with its verified teardown, inside the stop timeout of `sync.sh`:
    # max(180, probe + 2 x teardown + 30) = 240 s at the defaults.
    assert all(r.details["teardown"]["verified"] for r in rows)
    assert adapter.reservation is None
    assert adapter.clock.t - adapter.stopped_at <= max(180, 90 + 2 * 60 + 30)


class SlowCleanAdapter(FakeAdapter):
    """A scale set that holds 1 VM and takes `delay` seconds to scale in, once per name with
    `dirty_for` in it."""

    delay: float = 0.0
    dirty_for: str = ""
    pending_dirty: bool = False
    dirtied: bool = False
    region: str = ""

    def list_skus(self, region):
        self.region = region  # the Region of the entry that runs now
        return super().list_skus(region)

    def list_scale_sets(self, rg):
        if (
            self.dirty_for in self.region
            and not self.dirtied
            and not self.members
            and not self.scale_set_capacity
        ):
            self.dirtied = True
            self.scale_set_capacity = 1
            self.pending_dirty = True
        return super().list_scale_sets(rg)

    def update_scale_set(self, rg, name, sku_name, capacity, tags):
        if capacity == 0 and self.pending_dirty:
            self.pending_dirty = False
            self.clock.t += self.delay
        return super().update_scale_set(rg, name, sku_name, capacity, tags)


def nonempty_adapter(pre_teardown_seconds, dirty_for=""):
    adapter = SlowCleanAdapter(
        FakeClock(),
        plan=[PlannedMember("failed", "AllocationFailed")] * 4,
        skus=[b2s(capacity_reservation_supported=False)],
    )
    adapter.delay = pre_teardown_seconds
    adapter.dirty_for = dirty_for
    return adapter


@pytest.mark.parametrize("pre", [55, 60])
def test_a_nonempty_scale_set_on_the_first_attempt_keeps_the_call_in_the_deadline(pre):
    # Probe 90 + teardown 60 = 150 s. Pre-teardown + probe + teardown must fit in it.
    adapter = nonempty_adapter(pre)
    rows = scale(adapter, 4)
    assert rows[0].details["pre_teardown"]["verified"]
    assert rows[0].outcome == "no_capacity"
    assert adapter.clock.t <= 150


@pytest.mark.parametrize("pre", [55, 60])
def test_a_nonempty_first_attempt_that_cannot_fit_gets_a_deadline_row(pre):
    adapter = nonempty_adapter(pre)
    ctx = make_ctx([entry(n=4)], poll_interval_seconds=40)
    rows = wire(VmssFlexProbe(), adapter).run(ctx)
    assert len(rows) == 1
    row = rows[0]
    assert (row.outcome, row.n_placed) == ("error", None)
    assert row.details["search_stopped"] == "deadline"
    assert row.details["skipped"] is True and row.details["reason"] == "call deadline"
    assert row.details["pre_teardown"]["verified"]
    assert adapter.clock.t <= 150
    assert not any(c[0] == "update_scale_set" and c[2] > 0 for c in adapter.calls)


@pytest.mark.parametrize("pre", [55, 60])
def test_a_nonempty_scale_set_on_a_later_entry_behaves_the_same(pre):
    adapter = nonempty_adapter(pre, dirty_for="westus")
    targets = [entry(n=1, region="eastus"), entry(n=4, region="westus")]
    rows = wire(VmssFlexProbe(), adapter).run(make_ctx(targets))
    assert adapter.clock.t <= 150
    assert {r.region for r in rows} == {"eastus", "westus"}
    later = [r for r in rows if r.region == "westus"]
    assert any(
        "pre_teardown" in r.details or r.details.get("search_stopped") == "deadline" for r in later
    )


def test_an_entry_after_a_deadline_row_gets_its_own_deadline():
    # Entry A: a 90 s pre-teardown leaves no window, so A gets the deadline row and makes no
    # scale-out. Entry B starts its own deadline before its precheck and makes its search.
    adapter = nonempty_adapter(90, dirty_for="eastus")
    targets = [entry(n=4, region="eastus"), entry(n=8, region="westus")]
    rows = wire(VmssFlexProbe(), adapter).run(make_ctx(targets))
    first, *later = rows
    assert (first.region, first.outcome, first.n_placed) == ("eastus", "error", None)
    assert first.details["skipped"] is True and first.details["reason"] == "call deadline"
    assert first.details["search_stopped"] == "deadline"
    scale_outs = [c for c in adapter.calls if c[0] == "update_scale_set" and c[2] > 0]
    assert scale_outs and all("westus" in c[1] for c in scale_outs)
    assert later and all(r.region == "westus" and "skipped" not in r.details for r in later)
    assert all("search_stopped" not in r.details for r in later)
    assert adapter.clock.t <= 2 * 150


class SlowPrecheckAdapter(FakeAdapter):
    seconds: float = 0.0

    def list_skus(self, region):
        self.clock.t += self.seconds
        return super().list_skus(region)


def test_the_first_attempt_of_an_entry_checks_the_window_too():
    # A precheck that takes 100 s leaves a window of 150 - 100 - 60 < 0 for the first attempt.
    adapter = SlowPrecheckAdapter(FakeClock(), skus=[b2s(capacity_reservation_supported=False)])
    adapter.seconds = 100
    rows = wire(VmssFlexProbe(), adapter).run(make_ctx([entry(n=4)]))
    assert len(rows) == 1
    row = rows[0]
    assert (row.outcome, row.n_placed, row.latency_ms) == ("error", None, 0)
    assert row.details["skipped"] is True and row.details["search_stopped"] == "deadline"
    assert not any(c[0] == "update_scale_set" for c in adapter.calls)


def test_the_teardown_sends_the_scale_in_again_while_the_capacity_is_above_0():
    # The first scale-in has no effect until 20 s. The teardown reads the capacity, sees it above
    # 0, and sends the scale-in again.
    adapter = vmss_adapter(0, total=4, scale_op_after=30.0)
    adapter.scale_in_lost_until = 20.0
    rows = scale(adapter, 4)
    assert rows[0].outcome == "no_capacity"
    assert rows[0].details["teardown"]["verified"] is True
    assert adapter.scale_set_capacity == 0
    assert sum(1 for c in adapter.calls if c[0] == "update_scale_set" and c[2] == 0) > 1


def test_a_success_with_a_long_scale_out_has_a_verified_teardown_and_no_wait_for_boot():
    # The scale-out operation ends at 90 s, long after the members are allocated (2 s).
    adapter = vmss_adapter(4, total=4, scale_op_after=90.0)
    rows = scale(adapter, 4)
    assert rows[0].outcome == "success"
    teardown = rows[0].details["teardown"]
    assert teardown["verified"] is True and teardown["seconds"] < 30
    assert adapter.scale_set_capacity == 0


def test_the_search_continues_when_the_scale_out_runs_long_after_a_coded_failure():
    adapter = vmss_adapter(4, total=8, scale_op_after=90.0)
    rows = scale(adapter, 8)
    assert len(rows) > 1
    assert all(r.details["teardown"]["verified"] is True for r in rows)
    assert (4, "success") in attempts(rows)


def test_the_teardown_is_not_verified_when_the_scale_set_capacity_stays_above_0():
    adapter = vmss_adapter(0, total=4)
    adapter.scale_in_ignored = True
    rows = scale(adapter, 4)
    assert rows[0].details["teardown"]["verified"] is False
    assert adapter.scale_set_capacity == 4


@pytest.mark.parametrize("probe", [CapacityReservationProbe, VmssFlexProbe])
def test_a_simulated_entry_never_writes_search_stopped_when_the_process_stops(probe):
    """Spec 0010 section 5: a stop before the first attempt gives a stop row with
    no `search_stopped` for a simulated entry."""
    ctx = make_ctx([entry(n=8, sim_script="success")], simulate=True)
    calls = []
    ctx.stop_fn = lambda: bool(calls.append(1)) or len(calls) > 1  # false once, then true
    rows = wire(probe(), FakeAdapter(FakeClock())).run(ctx)
    assert [(r.simulated, r.outcome, r.details["reason"]) for r in rows] == [
        (True, "error", "stop")
    ]
    assert "search_stopped" not in rows[0].details
