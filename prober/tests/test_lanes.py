"""Region lanes: the provisioning probes run off the signal thread. Spec 0003 section 6B."""

import json
import logging
import threading
from datetime import timedelta

from ftg_prober.config import Config
from ftg_prober.probes import Probe
from ftg_prober.scheduler import Scheduler
from tests.test_record import make_record
from tests.test_scheduler import CountingProbe

# The worst case of 1 entry in 1 cycle: 2 attempts of probe timeout + teardown timeout, the
# defaults 90 s and 60 s (ADR 0018, section 5). A real wait never happens in these tests.
WORST_ENTRY_S = 2 * (90 + 60)
CADENCE_S = 2 * 3600
WAIT_S = 10  # a real-time bound, so that a broken test fails instead of hanging


class LaneProbe(Probe):
    """A fake provisioning probe. Each entry of its route moves the virtual clock of the Region
    by `WORST_ENTRY_S`, and its rows go to `ctx.emit`, as in the real probes."""

    region_lanes = True
    default_cadence = "2h"

    def __init__(self, probe_type, provider, route=None, world=None, gate=None):
        self.probe_type = probe_type
        self.provider = provider
        self.route = route
        self.world = world
        self.gate = gate  # an Event that each entry waits for, when set
        self.started: list[str] = []
        self.entered = threading.Event()  # set when the first entry has started

    def run(self, ctx):
        rows = []
        for target in ctx.config["provisioning_probes"]:
            if ctx.stopping():
                break
            if self.route is not None and target["route"] != self.route:
                continue
            self.started.append(target["sku"])
            self.entered.set()
            if self.world is not None:
                self.world.enter(target, self.probe_type)
            if self.gate is not None:
                assert self.gate.wait(WAIT_S)
            row = make_record(
                probe_type=self.probe_type,
                run_id=ctx.run_id,
                sku=target["sku"],
                region=target["region"],
            )
            rows.extend(ctx.emit([row]))
            if self.world is not None:
                self.world.leave(target)
        return rows


class World:
    """The virtual clock and the vCPUs held, per Region. The lanes are real threads."""

    def __init__(self, regions: int):
        self.lock = threading.Lock()
        self.clock: dict[str, float] = {}
        self.held: dict[str, int] = {}
        self.peak: dict[str, int] = {}
        self.held_all = 0
        self.peak_all = 0
        # Every Region's lane must reach this at the same time: Regions run in parallel.
        self.barrier = threading.Barrier(regions)
        self.waited: set[str] = set()

    def enter(self, target, probe_type):
        region = target["region"]
        if region not in self.waited:
            self.waited.add(region)
            self.barrier.wait(WAIT_S)
        with self.lock:
            vcpus = target["n"] * target["vcpus"]
            self.held[region] = self.held.get(region, 0) + vcpus
            self.peak[region] = max(self.peak.get(region, 0), self.held[region])
            self.held_all += vcpus
            self.peak_all = max(self.peak_all, self.held_all)
            self.clock[region] = self.clock.get(region, 0.0) + WORST_ENTRY_S

    def leave(self, target):
        with self.lock:
            vcpus = target["n"] * target["vcpus"]
            self.held[target["region"]] -= vcpus
            self.held_all -= vcpus


# The full matrix of ADR 0018: the vCPUs of each size come from the size name (Azure) or the
# instance type page (AWS); N = 2 for every cell.
AZURE_REGIONS = [
    "eastus",
    "eastus2",
    "southcentralus",
    "westus2",
    "westus3",
    "centralus",
    "northcentralus",
    "westus",
    "westcentralus",
]
AZURE_SIZES = {  # size: (route, vCPUs)
    "Standard_NC40ads_H100_v5": ("vmss", 40),
    "Standard_NC24ads_A100_v4": ("reservation", 24),
    "Standard_NV36ads_A10_v5": ("reservation", 36),
    "Standard_NC4as_T4_v3": ("reservation", 4),
    "Standard_NC144ds_xl_RTXPRO6000BSE_v6": ("vmss", 144),
}
AWS_REGIONS = ["us-east-1", "us-east-2", "us-west-2"]
AWS_TYPES = {
    "p5.4xlarge": 16,
    "g7e.2xlarge": 8,
    "g6e.xlarge": 4,
    "g5.xlarge": 4,
    "g4dn.xlarge": 4,
    "g6.xlarge": 4,
}


def azure_matrix():
    entries = []
    for region in AZURE_REGIONS:
        for sku, (route, vcpus) in AZURE_SIZES.items():
            modes = ["multi"]
            for mode in modes:
                entries.append(
                    {"sku": sku, "region": region, "route": route, "vcpus": vcpus, "n": 2}
                    | {"placement_group_mode": mode}
                )
    return entries


def aws_matrix():
    return [
        {"sku": t, "region": r, "route": "odcr", "vcpus": v, "n": 2}
        for r in AWS_REGIONS
        for t, v in AWS_TYPES.items()
    ]


def scheduler_for(provider, probes, entries, clock, store):
    section = {"regions": [], "cadence": {}, "provisioning_probes": entries}
    sections = {"aws": None, "azure": None, provider: section}
    config = Config(experiment="ftg", **sections)
    return Scheduler(
        probes,
        config=config,
        provider=provider,
        store=store,
        clock=clock,
        sleep=clock.sleep,
        now=clock.now,
    )


def test_the_full_azure_matrix_fits_its_cadence_with_1_cell_per_region_at_a_time(
    clock, memory_store
):
    entries = azure_matrix()
    assert len(entries) == 45
    world = World(len(AZURE_REGIONS))
    reservation = LaneProbe("az_capacity_reservation", "azure", "reservation", world)
    vmss = LaneProbe("az_vmss_flex", "azure", "vmss", world)
    scheduler = scheduler_for("azure", [reservation, vmss], entries, clock, memory_store)
    assert scheduler.run_once() == 2
    # The cycle is the slowest Region: 5 entries x 300 s = 25 min, under the 2 h cadence.
    assert max(world.clock.values()) == 5 * WORST_ENTRY_S == 1500 <= CADENCE_S
    assert set(world.clock) == set(AZURE_REGIONS)
    # 1 cell per Region at a time: the peak of a Region is its largest cell, not a sum.
    assert world.peak == {region: 2 * 144 for region in AZURE_REGIONS}
    assert world.peak_all <= 9 * 2 * 144
    # Each entry wrote its own object at once.
    assert len(memory_store.puts) == 45
    # Each lane has its own copy of the probe, never the shared one.
    copies = list(scheduler.lane_probes.values())
    assert len(copies) == 18 and len({id(p) for p in copies}) == 18
    assert reservation not in copies and vmss not in copies


def test_the_full_aws_matrix_fits_its_cadence_with_1_cell_per_region_at_a_time(clock, memory_store):
    entries = aws_matrix()
    assert len(entries) == 18
    world = World(len(AWS_REGIONS))
    odcr = LaneProbe("aws_odcr", "aws", world=world)
    scheduler = scheduler_for("aws", [odcr], entries, clock, memory_store)
    assert scheduler.run_once() == 1
    assert max(world.clock.values()) == 6 * WORST_ENTRY_S == 1800 <= CADENCE_S
    assert world.peak == {region: 2 * 16 for region in AWS_REGIONS}
    assert world.peak_all <= 3 * 2 * 16
    assert len(memory_store.puts) == 18


def test_a_running_lane_does_not_delay_a_signal_probe_and_a_stop_drains_the_lanes(
    clock, memory_store
):
    gate = threading.Event()
    odcr = LaneProbe("aws_odcr", "aws", gate=gate)
    other = LaneProbe("aws_other_lane", "aws")  # waits for the lock of the same Region
    signal = CountingProbe("aws_spot_score", default_cadence="5m")
    entries = [
        {"sku": "p5.4xlarge", "region": "us-east-1"},
        {"sku": "g5.xlarge", "region": "us-east-1"},
    ]
    # The lane grid slot (2 h) is 300 s after the start, on the same tick as the signal probe.
    clock.t = 3700.0
    scheduler = scheduler_for("aws", [odcr, other, signal], entries, clock, memory_store)
    seen = {}

    main = threading.current_thread()

    def stop():
        # The clock is virtual, so a lane thread that starts late would see the stop time before
        # its first entry. Once the lanes are started, the main thread waits for the
        # entry before it goes on.
        if threading.current_thread() is main and scheduler.lanes:
            assert odcr.entered.wait(WAIT_S)
        if clock() >= 3700 + 1800 and not seen:
            # The provisioning lane is still in its first entry at the stop.
            seen.update(signal_calls=len(signal.calls), odcr=list(odcr.started))
            gate.set()
        return clock() >= 3700 + 1800

    calls = scheduler.run_forever(stop=stop)
    # The 5-minute probe ran on time, 6 times in 30 min, while the lane was blocked.
    assert seen == {"signal_calls": 6, "odcr": ["p5.4xlarge"]}
    assert clock.sleeps == [300.0] * 6
    assert calls == 6 + 2
    # The stop: the entry in progress finished and wrote its row; no new entry started, and
    # the lane that waited for the Region lock made no call. run_forever waited for the lanes.
    assert odcr.started == ["p5.4xlarge"]
    assert other.started == []
    assert not any(lane.is_alive() for lane in scheduler.lanes.values())
    odcr_keys = [k for k in memory_store.puts if "probe_type=aws_odcr/" in k]
    assert len(odcr_keys) == 1
    (row,) = [json.loads(line) for line in memory_store.objects[odcr_keys[0]].splitlines()]
    assert row["sku"] == "p5.4xlarge"


def test_a_signal_probe_runs_while_a_lane_is_blocked_and_its_call_frees_the_lane(
    clock, memory_store
):
    gate = threading.Event()
    odcr = LaneProbe("aws_odcr", "aws", gate=gate)
    seen = []

    class ReleasingSignal(CountingProbe):
        def run(self, ctx):
            # Only a call after the lane has started its entry opens the gate. A lane that
            # blocks the signal probe never gets past the gate, and the gate wait fails.
            if odcr.entered.is_set() and not gate.is_set():
                seen.append(True)
                gate.set()
            return super().run(ctx)

    signal = ReleasingSignal("aws_spot_score", default_cadence="5m")
    entries = [{"sku": "p5.4xlarge", "region": "us-east-1"}]
    clock.t = 3700.0  # the lane grid slot is 300 s after the start, on the first signal tick
    scheduler = scheduler_for("aws", [odcr, signal], entries, clock, memory_store)

    def stop():
        # The clock is virtual: once the lanes are started, the main thread waits for the entry
        # so the next signal call finds the lane blocked on the gate.
        if threading.current_thread() is threading.main_thread() and scheduler.lanes:
            assert odcr.entered.wait(WAIT_S)
        return clock() >= 3700 + 1800

    scheduler.run_forever(stop=stop)
    assert seen == [True]  # the signal probe ran while the lane was blocked, and freed it
    assert len(signal.calls) == 6
    scheduler.join_lanes()
    assert odcr.started == ["p5.4xlarge"]
    assert len([k for k in memory_store.puts if "probe_type=aws_odcr/" in k]) == 1


def test_a_lane_still_running_is_not_started_again(clock, memory_store, caplog):
    gate = threading.Event()
    odcr = LaneProbe("aws_odcr", "aws", gate=gate)
    entries = [{"sku": "p5.4xlarge", "region": "us-east-1"}]
    scheduler = scheduler_for("aws", [odcr], entries, clock, memory_store)
    with caplog.at_level(logging.WARNING):
        scheduler.start_lanes(odcr)
        scheduler.start_lanes(odcr)
    gate.set()
    scheduler.join_lanes()
    (copy,) = scheduler.lane_probes.values()
    assert copy is not odcr
    assert copy.started == ["p5.4xlarge"]
    assert sum("lane.overrun" in r.message for r in caplog.records) == 1
    assert len(memory_store.puts) == 1


def test_a_store_failure_of_1_entry_is_logged_and_the_next_entry_still_runs(clock, caplog):
    class FailOnceStore:
        url = "memory://"

        def __init__(self):
            self.puts = []

        def put(self, key, data):
            if not self.puts:
                self.puts.append(None)
                raise OSError("store down")
            self.puts.append(key)

    store = FailOnceStore()
    odcr = LaneProbe("aws_odcr", "aws")
    entries = [
        {"sku": "p5.4xlarge", "region": "us-east-1"},
        {"sku": "g5.xlarge", "region": "us-east-1"},
    ]
    scheduler = scheduler_for("aws", [odcr], entries, clock, store)
    with caplog.at_level(logging.ERROR):
        scheduler.run_once()
    (copy,) = scheduler.lane_probes.values()
    assert copy.started == ["p5.4xlarge", "g5.xlarge"]
    assert len(store.puts) == 2 and store.puts[1] is not None
    assert sum("probe.failed" in r.message for r in caplog.records) == 1


def test_a_lane_that_gets_the_region_lock_after_a_stop_does_not_call_the_probe(clock, memory_store):
    """The stop check after the lock itself, not only the check inside the probe."""
    calls = []

    class Recording(LaneProbe):
        def run(self, ctx):
            calls.append(ctx.run_id)
            return []

    probe = Recording("aws_odcr", "aws")
    entries = [{"sku": "p5.4xlarge", "region": "us-east-1"}]
    scheduler = scheduler_for("aws", [probe], entries, clock, memory_store)
    scheduler.stop = lambda: True
    scheduler.run_lane(probe, "us-east-1", scheduler.lane_sections["us-east-1"])
    assert calls == []
    scheduler.stop = lambda: False
    scheduler.run_lane(probe, "us-east-1", scheduler.lane_sections["us-east-1"])
    assert len(calls) == 1


def test_the_halt_flag_alone_stops_the_scheduler(clock, memory_store):
    scheduler = scheduler_for("aws", [], [], clock, memory_store)
    assert scheduler.stop() is False
    assert scheduler.stopping() is False
    scheduler.halt.set()
    assert scheduler.stopping() is True


def test_ctrl_c_on_run_once_halts_the_lanes_so_they_start_no_new_entry(clock, memory_store):
    gate = threading.Event()
    odcr = LaneProbe("aws_odcr", "aws", gate=gate)

    class Interrupt(Probe):
        probe_type = "aws_interrupt"
        provider = "aws"
        default_cadence = "5m"

        def run(self, ctx):
            # Ctrl-C arrives while the lane is in its first entry.
            assert odcr_started.wait(WAIT_S)
            threading.Timer(0.05, gate.set).start()
            raise KeyboardInterrupt

    odcr_started = threading.Event()
    real_run = odcr.run

    def run(ctx):
        odcr_started.set()
        return real_run(ctx)

    entries = [
        {"sku": "p5.4xlarge", "region": "us-east-1"},
        {"sku": "g5.xlarge", "region": "us-east-1"},
    ]
    scheduler = scheduler_for("aws", [odcr, Interrupt()], entries, clock, memory_store)
    odcr.run = run
    try:
        scheduler.run_once()
        raise AssertionError("run_once must raise KeyboardInterrupt")
    except KeyboardInterrupt:
        pass
    assert scheduler.halt.is_set()
    assert not any(lane.is_alive() for lane in scheduler.lanes.values())
    (copy,) = scheduler.lane_probes.values()
    assert copy.started == ["p5.4xlarge"]


def test_a_lane_probe_is_not_due_at_start_and_runs_on_the_utc_grid_of_its_cadence(
    clock, memory_store
):
    """A start at T runs no lane cycle at T; the first cycle runs at the next slot of
    the grid (a multiple of the cadence since the Unix epoch), and the next 1 cadence later."""
    odcr = LaneProbe("aws_odcr", "aws")
    entries = [{"sku": "g5.xlarge", "region": "us-east-1"}]
    scheduler = scheduler_for("aws", [odcr], entries, clock, memory_store)
    start_wall = clock.now().timestamp()
    first_slot = (start_wall // CADENCE_S + 1) * CADENCE_S
    until_first = first_slot - start_wall
    assert 0 < until_first <= CADENCE_S

    def stop():
        if threading.current_thread() is threading.main_thread():
            scheduler.join_lanes()  # a lane thread must run before the next wake
        return clock() >= until_first + CADENCE_S + 60

    scheduler.run_forever(stop=stop)
    # No cycle at the start: the first sleep reaches the first slot; the 2nd is 1 cadence later.
    assert clock.sleeps[:2] == [until_first, CADENCE_S]
    assert len(odcr.started) == 2


def test_grid_wait_after_a_late_wake_gives_the_slot_after_the_slot_that_was_due(
    clock, memory_store
):
    """A wake just more than half a cadence after a slot must not
    skip the next slot. `round(wall / cadence)` picked the slot after the due one."""
    scheduler = scheduler_for("aws", [LaneProbe("aws_odcr", "aws")], [], clock, memory_store)
    _, due = scheduler.grid_wait(CADENCE_S)
    clock.t += (due * CADENCE_S - clock.now().timestamp()) + CADENCE_S * 0.5 + 1
    wait, nxt = scheduler.grid_wait(CADENCE_S, due)
    assert (wait, nxt) == (CADENCE_S / 2 - 1, due + 1)


def run_grid(clock, memory_store, fire_durations, step_back_at=None):
    """Drive `run_forever` for 5 slots with a fake `start_lanes` whose duration varies, and
    return the clock time of each fire. `step_back_at` is the number of the sleep during which
    the wall clock steps back 50 ms (the monotonic clock does not)."""
    odcr = LaneProbe("aws_odcr", "aws")
    scheduler = scheduler_for("aws", [odcr], [], clock, memory_store)
    offset = [0.0]
    scheduler.now = lambda: clock.now() + timedelta(seconds=offset[0])
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock.sleep(seconds)
        if step_back_at == len(sleeps):
            offset[0] -= 0.05

    scheduler.sleep = sleep
    fires = []

    def start_lanes(probe):
        fires.append(clock.t)
        clock.t += fire_durations[len(fires) % len(fire_durations)]

    scheduler.start_lanes = start_lanes
    _, first = scheduler.grid_wait(CADENCE_S)
    end = first * CADENCE_S - scheduler.now().timestamp() + 4 * CADENCE_S + 60
    scheduler.run_forever(stop=lambda: clock() >= end)
    return fires


def test_a_lane_probe_fires_once_per_slot_whatever_the_duration_of_start_lanes(clock, memory_store):
    """The loop reads `now` before `start_lanes`, so the next due time is early by the
    duration of the call. A wake that early must not give the slot that already ran."""
    fires = run_grid(clock, memory_store, [0.0001, 0.006, 0.0003, 0.004, 0.002])
    assert len(fires) == 5
    assert all(b - a > CADENCE_S / 2 for a, b in zip(fires, fires[1:], strict=False))


def test_a_lane_probe_fires_once_per_slot_when_the_wall_clock_steps_back_in_a_sleep(
    clock, memory_store
):
    fires = run_grid(clock, memory_store, [0.001], step_back_at=2)
    assert len(fires) == 5
    assert all(b - a > CADENCE_S / 2 for a, b in zip(fires, fires[1:], strict=False))


def test_a_lane_probe_never_fires_before_its_slot_and_fires_at_most_the_bound_a_utc_day(
    clock, memory_store
):
    """`next_due` must come from a monotonic read taken with the wall read of the
    slot. With the read at the top of the pass, each fire landed early by the duration of the
    pass, and 2 slots around UTC midnight could both fire in 1 day (13 cycles a day at 2 h)."""
    odcr = LaneProbe("aws_odcr", "aws")
    scheduler = scheduler_for("aws", [odcr], [], clock, memory_store)
    midnight = -(-int(clock.now().timestamp()) // 86400) * 86400
    clock.t = midnight - clock.now().timestamp() + clock.t - 5  # 5 s before a UTC midnight
    oversleeps = [0.0, 0.004, 0.0, 0.009, 0.001]
    sleeps = []

    def sleep(seconds):
        clock.sleep(seconds + oversleeps[len(sleeps) % len(oversleeps)])
        sleeps.append(seconds)

    scheduler.sleep = sleep
    walls = []
    durations = [0.2, 7.0, 0.01, 30.0, 1.5, 0.0]

    def start_lanes(probe):
        walls.append(clock.now().timestamp())
        clock.t += durations[len(walls) % len(durations)]

    scheduler.start_lanes = start_lanes
    first = midnight  # the first slot after the start is the midnight, 5 s ahead
    end = clock() + 5 + 4 * 86400 + 60
    scheduler.run_forever(stop=lambda: clock() >= end)
    assert len(walls) == 4 * 12 + 1
    assert all(w >= first + i * CADENCE_S for i, w in enumerate(walls))
    per_day = {}
    for w in walls:
        per_day[int(w // 86400)] = per_day.get(int(w // 86400), 0) + 1
    assert max(per_day.values()) <= 12


def test_a_lane_probe_does_not_fire_before_its_slot_when_the_wall_clock_steps_back(
    clock, memory_store
):
    """The wall clock steps back 60 s in the sleep that ends at a UTC midnight slot.
    The monotonic wake is then 60 s before the slot. The fire must wait for the slot, so no
    UTC day gets more than 12 fires at a 2 h cadence."""
    odcr = LaneProbe("aws_odcr", "aws")
    scheduler = scheduler_for("aws", [odcr], [], clock, memory_store)
    midnight = -(-int(clock.now().timestamp()) // 86400) * 86400
    clock.t += midnight - clock.now().timestamp() - 3600  # 1 h before a UTC midnight
    offset = [0.0]
    scheduler.now = lambda: clock.now() + timedelta(seconds=offset[0])
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock.sleep(seconds)
        if len(sleeps) == 1:
            offset[0] -= 60  # a time-sync step back during the sleep to the midnight slot

    scheduler.sleep = sleep
    walls = []

    def start_lanes(probe):
        walls.append(scheduler.now().timestamp())

    scheduler.start_lanes = start_lanes
    end = clock() + 3600 + 2 * 86400
    scheduler.run_forever(stop=lambda: clock() >= end)
    assert walls[0] >= midnight
    assert all(w >= midnight + i * CADENCE_S for i, w in enumerate(walls))
    per_day = {}
    for w in walls:
        per_day[int(w // 86400)] = per_day.get(int(w // 86400), 0) + 1
    assert max(per_day.values()) <= 12
