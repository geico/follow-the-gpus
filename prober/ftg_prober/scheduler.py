"""Run each probe at its cadence and write the rows of each call at once. Spec 0003 section 6."""

import copy
import logging
import random
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ftg_prober import sim
from ftg_prober.callqueue import Call, CallQueue, TokenBucket
from ftg_prober.config import Config, cadence_key, parse_cadence
from ftg_prober.log import kv
from ftg_prober.probes import Clients, Probe, ProbeContext
from ftg_prober.record import CORE_SIM_PROBE_TYPES, Record, to_jsonl
from ftg_prober.store import Store, build_key, target_for

log = logging.getLogger(__name__)

# The defaults of the queue worker. Each has a config key `aws.queue_<name>`. Spec 0003 section 6A.
QUEUE_DEFAULTS = {
    "wake_seconds": 30,
    "max_calls_per_wake": 1,
    "jitter": 0.2,  # each sleep is uniform in [0.8, 1.2] x the interval
    "backoff_base_seconds": 30,
    "backoff_cap_seconds": 600,
    "max_throttle_attempts": 5,
}
WORKER = "(queue worker)"
# The config key of the entries that a lane probe reads (spec 0003, section 6B).
ENTRIES_KEY = "provisioning_probes"


class Scheduler:
    """Owns the probes of 1 provider. `clock`, `sleep`, and `now` are replaceable for tests."""

    def __init__(
        self,
        probes: list[Probe],
        *,
        config: Config,
        provider: str,
        store: Store,
        clients: Clients | None = None,
        simulate: bool = False,
        experiment: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        rng: random.Random | None = None,
    ) -> None:
        # A job-eligible probe type with no job never runs (ADR 0010 section 1); a provisioning
        # probe type, which is not job-eligible, is unaffected (config.has_job).
        self.probes = [
            p for p in probes if p.provider == provider and config.has_job(provider, p.probe_type)
        ]
        self.config = config
        self.provider = provider
        self.section = config.section(provider)
        self.store = store
        self.clients = clients or Clients()
        # The run-wide `--simulate` flag (ADR 0011 section 2). A job's own `simulate: true` key
        # is layered on top per probe type (`job_simulated`) or per target (`ctx.simulated`).
        self.run_simulate = simulate
        self.experiment = experiment or config.experiment
        self.clock = clock
        self.sleep = sleep
        self.now = now
        self.cadence_s = {p.probe_type: self.cadence_seconds(p) for p in self.probes}
        self.queue = CallQueue()
        self.bucket = TokenBucket(clock)
        self.rng = rng or random.Random()
        self.queue_cfg = {
            name: self.section.get(f"queue_{name}", default)
            for name, default in QUEUE_DEFAULTS.items()
        }
        self.region_due: dict[str, float] = {}
        # Region lanes (spec 0003, section 6B). `halt` is set when `run_forever` ends; `stop` is
        # its stop flag. A lane checks both before it starts a call and before each entry.
        self.lane_sections = self._lane_sections()
        self.region_locks = {region: threading.Lock() for region in self.lane_sections}
        self.lanes: dict[tuple[str, str], threading.Thread] = {}
        self.lane_probes: dict[tuple[str, str], Probe] = {}
        self.halt = threading.Event()
        self.stop: Callable[[], bool] = lambda: False

    def cadence_seconds(self, probe: Probe) -> int:
        """The config `cadence` entry wins over `default_cadence`."""
        configured = (self.section.get("cadence") or {}).get(cadence_key(probe.probe_type))
        return parse_cadence(configured or probe.default_cadence)

    def select(self, probe_name: str | None) -> list[Probe]:
        if probe_name is None:
            return list(self.probes)
        chosen = [p for p in self.probes if p.probe_type == probe_name]
        if not chosen:
            names = ", ".join(sorted(p.probe_type for p in self.probes)) or "(none)"
            raise LookupError(
                f"no probe named {probe_name!r} for provider {self.provider}; loaded: {names}"
            )
        return chosen

    def job_simulated(self, probe_type: str) -> bool:
        """The run-wide `--simulate` flag, or this probe type's own job `simulate: true` key."""
        return self.run_simulate or self.config.simulate_job(self.provider, probe_type)

    def context(
        self, run_id: str, simulate: bool = False, config: dict[str, Any] | None = None
    ) -> ProbeContext:
        return ProbeContext(
            config=self.section if config is None else config,
            provider=self.provider,
            run_id=run_id,
            experiment=self.experiment,
            store=self.store,
            clients=self.clients,
            now_fn=self.now,
            simulate=simulate,
            stop_fn=self.stopping,
        )

    def stopping(self) -> bool:
        """True once the process stops: a lane starts no new call and no new entry."""
        return self.halt.is_set() or self.stop()

    def _lane_sections(self) -> dict[str, dict[str, Any]]:
        """The section of each Region lane: the provider section with the entries of 1 Region."""
        by_region: dict[str, list[Any]] = {}
        for entry in self.section.get(ENTRIES_KEY) or []:
            by_region.setdefault(str(entry["region"]).lower(), []).append(entry)
        return {region: {**self.section, ENTRIES_KEY: rows} for region, rows in by_region.items()}

    def start_lanes(self, probe: Probe) -> None:
        """Start 1 thread per Region for a call of a lane probe. A lane of the last call that
        still runs is not started again: it logs `lane.overrun`."""
        for region, section in self.lane_sections.items():
            key = (probe.probe_type, region)
            running = self.lanes.get(key)
            if running is not None and running.is_alive():
                log.warning("lane.overrun %s", kv(probe_type=probe.probe_type, region=region))
                continue
            # Each lane has its own shallow copy of the probe, so the state that a probe keeps
            # for 1 call (for example the adapter of an Azure probe) is never shared by 2 threads.
            lane_probe = self.lane_probes.setdefault(key, copy.copy(probe))
            lane = threading.Thread(
                target=self.run_lane,
                args=(lane_probe, region, section),
                name=f"lane-{probe.probe_type}-{region}",
            )
            self.lanes[key] = lane
            lane.start()

    def run_lane(self, probe: Probe, region: str, section: dict[str, Any]) -> None:
        """1 call of a lane probe for the entries of 1 Region, under the lock of the Region."""
        with self.region_locks[region]:
            if self.stopping():
                return
            self.run_probe(probe, config=section, region=region)

    def join_lanes(self) -> None:
        """Wait until each lane thread ends."""
        for lane in list(self.lanes.values()):
            lane.join()

    def planned(self, probe: Probe) -> list[Call] | None:
        """The calls of a queued probe, else None. A simulated job is not queued (spec 0003, 6A)."""
        plan = probe.plan(self.section)
        if not plan or self.job_simulated(probe.probe_type):
            return None
        return plan

    def work(self) -> int:
        """1 wake of the worker. Take the calls the budget allows from the queue of each Region.

        The budget of a Region is `max_calls_per_wake` and the tokens of its bucket. Return the
        number of calls made.
        """
        by_type = {p.probe_type: p for p in self.probes}
        wake = float(self.queue_cfg["wake_seconds"])
        spread = float(self.queue_cfg["jitter"])
        made = 0
        regions = self.queue.regions()
        for region in list(self.region_due):
            if region not in regions:
                del self.region_due[region]  # an empty Region gets a new start offset
        for region in regions:
            now = self.clock()
            if region not in self.region_due:
                self.region_due[region] = now + self.rng.uniform(0, wake)  # start offset
            if now < self.region_due[region]:
                continue
            self.region_due[region] = now + wake * self.rng.uniform(1 - spread, 1 + spread)
            budget = min(int(self.queue_cfg["max_calls_per_wake"]), self.bucket.available(region))
            for call, attempts in self.queue.take(region, budget, now):
                self.bucket.take(region)
                self.run_probe(by_type[call.probe_type], call, attempts)
                made += 1
        return made

    def next_wake(self) -> float:
        """The time of the next worker wake."""
        default = self.clock() + float(self.queue_cfg["wake_seconds"])
        return min(self.region_due.values(), default=default)

    def backoff(self, attempts: int) -> float:
        """Full jitter: uniform in [0, min(cap, base x 2^attempts)]. AWS Builders' Library."""
        base = float(self.queue_cfg["backoff_base_seconds"])
        cap = float(self.queue_cfg["backoff_cap_seconds"])
        return self.rng.uniform(0, min(cap, base * 2**attempts))

    def run_probe(
        self,
        probe: Probe,
        call: Call | None = None,
        attempts: int = 0,
        config: dict[str, Any] | None = None,
        region: str | None = None,
    ) -> list[Record]:
        """Call 1 probe, write its rows (1 object per `simulated` value), and return them.
        Never raise.

        With `call`, run that one call of the probe plan. A throttled call goes back on the queue
        and writes no row, until `max_throttle_attempts` attempts. The follow-up calls of the probe
        join the queue. With `config`, the probe gets that section (a Region lane). The rows that
        the probe emits for each entry (`ctx.emit`) are written at once."""
        run_id = str(uuid.uuid4())
        simulated = self.job_simulated(probe.probe_type)
        ctx = self.context(run_id, simulate=simulated, config=config)
        emitted: list[Record] = []

        def emit(entry_rows: list[Record]) -> None:
            try:
                self.write(probe, entry_rows)
            except Exception as exc:  # noqa: BLE001 - a store failure never stops the entries
                log.exception(
                    "probe.failed %s",
                    kv(probe_type=probe.probe_type, run_id=run_id, error=repr(exc)),
                )
                return
            emitted.extend(entry_rows)

        ctx.emit_fn = emit
        started = self.clock()
        try:
            if simulated and probe.probe_type in CORE_SIM_PROBE_TYPES:
                rows = self._sim_rows(probe, ctx, run_id)
            else:
                rows = probe.run(ctx) if call is None else probe.run_call(ctx, call)
                if call is not None:  # the worker took 1 token; each extra page is 1 more request
                    for _ in range(ctx.api_calls - 1):
                        self.bucket.take(call.region)
                if (
                    call is not None
                    and self._throttled(probe, rows)
                    and attempts + 1 < int(self.queue_cfg["max_throttle_attempts"])
                ):
                    delay = self.backoff(attempts)
                    self.queue.requeue(call, attempts + 1, self.clock() + delay)
                    log.info(
                        "call.throttled %s",
                        kv(probe_type=probe.probe_type, region=call.region, retry_in_s=int(delay)),
                    )
                    return []
                follow = probe.follow_up(call, rows) if call is not None else []
                # A call with a follow-up is a step of a search, not a finished result: the
                # scheduler writes no row for it (spec 0003, 6A rule 5). The default `follow_up`
                # returns none, so an ordinary queued call still writes its row as before.
                if not follow and (rows or not emitted):
                    self.write(probe, rows)
                for next_call in follow:
                    self.queue.add(next_call)
        except Exception as exc:  # noqa: BLE001 - 1 probe failure never stops the loop
            log.exception(
                "probe.failed %s", kv(probe_type=probe.probe_type, run_id=run_id, error=repr(exc))
            )
            return emitted
        elapsed_ms = int((self.clock() - started) * 1000)
        rows = emitted + rows
        where = {} if region is None else {"region": region}
        log.info(
            "probe.done %s",
            kv(probe_type=probe.probe_type, run_id=run_id, rows=len(rows), ms=elapsed_ms, **where),
        )
        return rows

    @staticmethod
    def _throttled(probe: Probe, rows: list[Record]) -> bool:
        return (
            probe.throttle_code is not None
            and len(rows) == 1
            and rows[0].error_code == probe.throttle_code
        )

    def _sim_rows(self, probe: Probe, ctx: ProbeContext, run_id: str) -> list[Record]:
        rows: list[Record] = []
        rng = random.Random(run_id)
        for call in sim.sim_calls(probe.probe_type, self.section, ctx.now(), rng, simulated=True):
            sim.write_call(self.store, self.experiment, self.provider, probe.probe_type, call)
            rows.extend(call.rows)
        return rows

    def write(self, probe: Probe, rows: list[Record]) -> list[str]:
        """Write the rows of 1 call as 1 JSONL object per `simulated` value. Return the keys.

        The groups keep the order of the first row of each value. No rows give no object. No
        object mixes a simulated row with a real one (ADR 0011 section 6, item 6).
        """
        if not rows:
            log.info("probe.empty %s", kv(probe_type=probe.probe_type))
            return []
        for row in rows:
            for fault in row.validate():
                log.warning("row.invalid %s", kv(probe_type=probe.probe_type, fault=fault))
        groups: dict[bool, list[Record]] = {}
        for row in rows:
            groups.setdefault(row.simulated, []).append(row)
        keys = []
        for group in groups.values():
            target = target_for(group)
            key = build_key(self.experiment, self.provider, probe.probe_type, self.now(), target)
            self.store.put(key, to_jsonl(group))
            log.info(
                "store.put %s",
                kv(key=key, rows=len(group), bytes=sum(len(r.to_json()) + 1 for r in group)),
            )
            keys.append(key)
        return keys

    def run_once(self, probe_name: str | None = None) -> int:
        """Run each selected probe 1 time; a lane probe in its Region lanes, which this waits
        for. Return the number of probe calls."""
        chosen = self.select(probe_name)
        try:
            for probe in chosen:
                if probe.region_lanes:
                    self.start_lanes(probe)
                else:
                    self.run_probe(probe)
            self.join_lanes()
        finally:
            self.halt.set()  # after a Ctrl-C, the lanes start no new entry
            self.join_lanes()
        return len(chosen)

    def grid_wait(self, cadence: int, after: int | None = None) -> tuple[float, int]:
        """Seconds until the next slot of the UTC grid of `cadence`, and that slot's index
        (spec 0003, section 6, rule 2).

        A slot is a multiple of the cadence since the Unix epoch; its index is the multiple.
        At start (`after` is None) the next slot is the first one strictly after now. After a
        call, `after` is the index of the slot that was due. The next slot is the one after the
        later of `after` and the slot that holds now, so a late wake (less than 1 cadence late)
        gives the slot after the one that was due, and an early wake or a small step back of the
        wall clock never gives the slot that already ran.
        """
        wall = self.now().timestamp()
        here = int(wall // cadence)
        slot = (here if after is None else max(here, after)) + 1
        return slot * cadence - wall, slot

    def run_forever(
        self, probe_name: str | None = None, stop: Callable[[], bool] = lambda: False
    ) -> int:
        """Loop: run each probe when it is due, then sleep until the next one is due.

        `stop()` is checked before each sleep. A lane probe starts its Region lanes and the loop
        goes on (spec 0003, section 6B). At the end, the loop waits for the lanes, which finish
        the entry in progress and start no new one. Returns the number of probe calls.
        """
        chosen = self.select(probe_name)
        if not chosen:
            return 0
        self.stop = stop
        start = self.clock()
        # A lane probe is due on the UTC grid of its cadence, never at start (spec 0003, section 6,
        # rule 2): a restart adds no cycle. Every other probe is due at start.
        next_due = {p.probe_type: start for p in chosen}
        due_slot: dict[str, int] = {}  # the index of the grid slot that each lane probe waits for
        for p in chosen:
            if p.region_lanes:
                wait, due_slot[p.probe_type] = self.grid_wait(self.cadence_s[p.probe_type])
                next_due[p.probe_type] = self.clock() + wait  # read with the wall clock
        queued = {p.probe_type: plan for p in chosen if (plan := self.planned(p))}
        if queued:
            next_due[WORKER] = start
        calls = 0
        try:
            while not stop():
                now = self.clock()
                for probe in chosen:
                    if now >= next_due[probe.probe_type]:
                        if probe.region_lanes:
                            short = (
                                due_slot[probe.probe_type] * self.cadence_s[probe.probe_type]
                                - self.now().timestamp()
                            )
                            if short > 0:
                                # The wall clock stepped back during the sleep: the monotonic
                                # wake is before the slot. Wait for the slot.
                                next_due[probe.probe_type] = self.clock() + short
                                continue
                            self.start_lanes(probe)
                        elif probe.probe_type in queued:
                            for call in queued[probe.probe_type]:
                                self.queue.add(call)
                            next_due[WORKER] = now
                        else:
                            self.run_probe(probe)
                        calls += 1
                        cadence = self.cadence_s[probe.probe_type]
                        if probe.region_lanes:
                            wait, due_slot[probe.probe_type] = self.grid_wait(
                                cadence, due_slot[probe.probe_type]
                            )
                            # The monotonic read goes with the wall read of grid_wait, not with
                            # `now` of the top of the pass, so the wake never lands before the slot.
                            next_due[probe.probe_type] = self.clock() + wait
                        else:
                            next_due[probe.probe_type] = now + cadence
                if WORKER in next_due and now >= next_due[WORKER]:
                    calls += self.work()
                    next_due[WORKER] = self.next_wake()
                if stop():
                    break
                wait = min(next_due.values()) - self.clock()
                if wait > 0:
                    self.sleep(wait)
        finally:
            self.halt.set()
            self.join_lanes()
        log.info("scheduler.stopped %s", kv(calls=calls))
        return calls
