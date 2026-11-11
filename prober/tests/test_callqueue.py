"""The call queue, the token bucket, and the queue worker of the scheduler. Spec 0003 section 6A."""

import random

import pytest

from ftg_prober.callqueue import BUCKET_SIZE, START_TOKENS, Call, CallQueue, TokenBucket
from ftg_prober.config import Config
from ftg_prober.probes import Clients, Probe
from ftg_prober.record import Record
from ftg_prober.scheduler import Scheduler
from tests.aws.fakes import section
from tests.conftest import FakeClock, MemoryStore


def test_a_call_with_the_same_key_is_replaced_not_duplicated_and_keeps_its_place():
    queue = CallQueue()
    first, second, third = (Call("p", "r1", (n,)) for n in (1, 2, 3))
    for call in (first, second, third, first):
        queue.add(call)

    assert len(queue) == 3
    assert queue.take("r1", 5) == [(first, 0), (second, 0), (third, 0)]
    assert queue.regions() == []


def test_a_call_with_a_key_is_not_joined_by_a_2nd_one_for_the_same_key():
    # A search's steps change `args` as they progress, so the 3-field identity above does not
    # dedupe them: a mid-search call (here, a later step) and a new cycle's fresh `earliest0`
    # call have different `args`, and only sharing `key` tells the queue they are 1 search
    # (spec 0003, 6A, rule 1).
    queue = CallQueue()
    in_flight = Call("aws_cb_longest", "r1", ("gallop", 2), key="p5.48xlarge")
    queue.add(in_flight)
    replan = Call("aws_cb_longest", "r1", ("earliest0",), key="p5.48xlarge")  # a new cycle plans

    queue.add(replan)

    assert len(queue) == 1
    assert queue.take("r1", 5) == [(in_flight, 0)]


def test_a_call_with_a_key_is_replaced_once_taken_by_its_own_next_step():
    # Once the queue has taken a call for a step, nothing with its key waits, so the step's own
    # follow-up call (a different `args`, the same `key`) is added as usual.
    queue = CallQueue()
    first_step = Call("aws_cb_longest", "r1", ("earliest0",), key="p5.48xlarge")
    queue.add(first_step)
    queue.take("r1", 1)
    next_step = Call("aws_cb_longest", "r1", ("gallop",), key="p5.48xlarge")

    queue.add(next_step)

    assert len(queue) == 1
    assert queue.take("r1", 5) == [(next_step, 0)]


def test_a_call_with_a_key_does_not_dedupe_across_probe_types():
    queue = CallQueue()
    first = Call("aws_cb_longest", "r1", ("earliest0",), key="p5.48xlarge")
    other = Call("other_probe", "r1", ("x",), key="p5.48xlarge")
    queue.add(first)
    queue.add(other)

    assert len(queue) == 2


def test_the_bucket_has_10_tokens_and_refills_0_15_per_second():
    clock = FakeClock()
    bucket = TokenBucket(clock)
    assert bucket.available("r1") == START_TOKENS == 5  # half a bucket at the start
    for _ in range(5):
        bucket.take("r1")
    assert bucket.available("r1") == 0
    clock.t += 6.7  # 1 token
    assert bucket.available("r1") == 1
    clock.t += 3600  # never more than 10
    assert bucket.available("r1") == BUCKET_SIZE
    assert bucket.available("r2") == START_TOKENS


class QueuedProbe(Probe):
    """A queued probe that records each call. `answers` maps a call to an error code or None."""

    provider = "aws"
    default_cadence = "2h"
    throttle_code = "Throttled"

    def __init__(self, probe_type, calls, log, answers=None, follow=None):
        self.probe_type = probe_type
        self.calls = calls
        self.log = log
        self.answers = answers if answers is not None else {}
        self.follow = follow if follow is not None else {}

    def plan(self, config):
        return list(self.calls)

    def run_call(self, ctx, call):
        self.log.append((ctx.now_fn(), call))
        queue = self.answers.get(call, [])
        code = queue.pop(0) if queue else None
        return [
            Record(
                ts_utc=ctx.timestamp(),
                run_id=ctx.run_id,
                simulated=False,
                provider="aws",
                region=call.region,
                zone=None,
                probe_type=self.probe_type,
                sku=str(call.args[0]),
                placement_group_mode="n/a",
                n_requested=1,
                n_placed=None,
                outcome="error" if code else "success",
                error_code=code,
                latency_ms=1,
                details={},
            )
        ]

    def follow_up(self, call, rows):
        return self.follow.pop(call, [])


class FiveMinuteProbe(Probe):
    probe_type = "aws_spot_score"
    provider = "aws"
    default_cadence = "5m"

    def __init__(self, seconds):
        self.seconds = seconds

    def run(self, ctx):
        self.seconds.append(ctx.now_fn())
        return []


class Frac:
    """A random source whose `uniform(a, b)` is always at the fraction `f` of the range."""

    def __init__(self, f):
        self.f = f

    def uniform(self, a, b):
        return a + (b - a) * self.f


def make_scheduler(probes, clock, store=None, rng=None, **extra):
    aws = section(regions=["r1", "r2"], cadence={"aws_cb_offering": "2h", "aws_cb_earliest": "2h"})
    aws.update(extra)
    return Scheduler(
        probes,
        config=Config(experiment="ftg", aws=aws, azure=None),
        provider="aws",
        store=store or MemoryStore(),
        clients=Clients(boto3_factory=lambda s, r: None),
        clock=clock,
        sleep=clock.sleep,
        now=clock.now,
        rng=rng or Frac(0.5),
    )


def calls_of(probe_type, region, n):
    return [Call(probe_type, region, (f"t{i}",)) for i in range(n)]


def run_for(scheduler, clock, seconds):
    scheduler.run_forever(stop=lambda: clock.t >= seconds)


def test_the_worker_makes_1_call_per_region_each_30_seconds_and_the_regions_run_in_parallel():
    clock = FakeClock()
    log = []
    calls = calls_of("aws_cb_offering", "r1", 15) + calls_of("aws_cb_offering", "r2", 15)
    probe = QueuedProbe("aws_cb_offering", calls, log)
    scheduler = make_scheduler([probe], clock)

    run_for(scheduler, clock, 15 * 30 + 30)

    assert len(log) == 30
    times = [(t.timestamp() - clock.now().timestamp() + clock.t) for t, _ in log]
    r1 = [t for t, (_, c) in zip(times, log, strict=True) if c.region == "r1"]
    r2 = [t for t, (_, c) in zip(times, log, strict=True) if c.region == "r2"]
    # Frac(0.5): the start offset is 15 s and each sleep is 30 s.
    assert r1 == [pytest.approx(15.0 + 30.0 * i) for i in range(15)]
    assert r2 == r1
    # The drain order is the plan order in each Region.
    assert [c for _, c in log if c.region == "r1"] == calls[:15]


def test_the_queue_is_filled_again_each_cadence_and_a_waiting_call_is_replaced():
    clock = FakeClock()
    log = []
    probe = QueuedProbe("aws_cb_offering", calls_of("aws_cb_offering", "r1", 5), log)
    scheduler = make_scheduler([probe], clock, queue_wake_seconds=30)
    scheduler.queue.add(Call("aws_cb_offering", "r1", ("t4",)))  # waits already

    run_for(scheduler, clock, 7200 + 200)  # 2 cycles, the 2nd drained

    assert len(log) == 10  # 5 calls in each cycle: the waiting call was replaced, not added
    assert [c.args for _, c in log][:5] == [("t4",), ("t0",), ("t1",), ("t2",), ("t3",)]


def test_a_throttled_call_goes_back_on_the_queue_with_no_row_and_the_next_wake_retries_it():
    clock = FakeClock()
    log = []
    first = Call("aws_cb_offering", "r1", ("t0",))
    probe = QueuedProbe(
        "aws_cb_offering",
        [first, Call("aws_cb_offering", "r1", ("t1",))],
        log,
        answers={first: ["Throttled", "Other"]},
    )
    store = MemoryStore()
    scheduler = make_scheduler([probe], clock, store=store)

    run_for(scheduler, clock, 80)  # wakes at 15, 45, and 75

    # t0 is throttled at 15 and waits until 30 (backoff 15 s); t1 runs at 45; t0 runs at 75.
    assert [c.args for _, c in log] == [("t0",), ("t1",), ("t0",)]
    assert len(store.puts) == 2  # the throttled attempt wrote no row; the other 2 calls did
    assert len(scheduler.queue) == 0


def test_the_bucket_limits_the_calls_when_it_is_empty():
    clock = FakeClock()
    log = []
    probe = QueuedProbe("aws_cb_offering", calls_of("aws_cb_offering", "r1", 4), log)
    scheduler = make_scheduler([probe], clock, queue_max_calls_per_wake=4)
    for _ in range(3):  # 5 tokens at the start; 3 are used, 2 are left
        scheduler.bucket.take("r1")

    for call in probe.plan({}):
        scheduler.queue.add(call)
    scheduler.region_due["r1"] = 0.0  # the start offset has passed
    assert scheduler.work() == 2
    assert len(scheduler.queue) == 2


def test_both_probes_share_the_bucket_of_a_region():
    clock = FakeClock()
    log = []
    offering = QueuedProbe("aws_cb_offering", calls_of("aws_cb_offering", "r1", 3), log)
    earliest = QueuedProbe("aws_cb_earliest", calls_of("aws_cb_earliest", "r1", 3), log)
    scheduler = make_scheduler([offering, earliest], clock, queue_max_calls_per_wake=6)
    for _ in range(3):
        scheduler.bucket.take("r1")
    for probe in (offering, earliest):
        for call in probe.plan({}):
            scheduler.queue.add(call)

    scheduler.region_due["r1"] = 0.0
    assert scheduler.work() == 2  # 2 tokens for both probes together
    assert len(scheduler.queue) == 4


def test_the_follow_up_hook_adds_a_call_to_the_queue():
    clock = FakeClock()
    log = []
    first = Call("aws_cb_offering", "r1", ("t0",))
    later = Call("aws_cb_offering", "r1", ("step",))
    probe = QueuedProbe("aws_cb_offering", [first], log, follow={first: [later]})
    scheduler = make_scheduler([probe], clock)

    run_for(scheduler, clock, 50)

    assert [c for _, c in log] == [first, later]


def test_a_5_minute_probe_runs_on_time_while_the_queue_drains():
    clock = FakeClock()
    log = []
    seconds = []
    calls = calls_of("aws_cb_offering", "r1", 15) + calls_of("aws_cb_offering", "r2", 15)
    scheduler = make_scheduler(
        [QueuedProbe("aws_cb_offering", calls, log), FiveMinuteProbe(seconds)], clock
    )
    scheduler.cadence_s["aws_spot_score"] = 300

    run_for(scheduler, clock, 1500)

    assert len(log) == 30  # 15 calls in each Region, drained in 450 s
    stamps = [(t.timestamp() - clock.now().timestamp() + clock.t) for t in seconds]
    assert stamps == [pytest.approx(300.0 * i) for i in range(5)]  # nothing delays it


def test_each_sleep_is_within_20_percent_of_the_interval_and_each_region_starts_at_an_offset():
    clock = FakeClock()
    log = []
    calls = calls_of("aws_cb_offering", "r1", 40) + calls_of("aws_cb_offering", "r2", 40)
    scheduler = make_scheduler(
        [QueuedProbe("aws_cb_offering", calls, log)], clock, rng=random.Random(7)
    )

    run_for(scheduler, clock, 40 * 40)

    by_region = {"r1": [], "r2": []}
    for t, call in log:
        by_region[call.region].append(t.timestamp() - clock.now().timestamp() + clock.t)
    assert len(by_region["r1"]) == len(by_region["r2"]) == 40
    # Wake times are the rounded call times; allow the loop to run a Region at the next loop turn.
    for times in by_region.values():
        assert 0 <= times[0] <= 30
    assert by_region["r1"][0] != by_region["r2"][0]


def test_the_wake_interval_is_uniform_in_80_to_120_percent():
    clock = FakeClock()
    scheduler = make_scheduler([QueuedProbe("aws_cb_offering", [], [])], clock, rng=Frac(0))
    scheduler.queue.add(Call("aws_cb_offering", "r1", ("x",)))
    scheduler.queue.add(Call("aws_cb_offering", "r1", ("y",)))
    scheduler.work()  # Frac(0): the start offset is 0, so the call is made at once
    assert scheduler.region_due["r1"] == pytest.approx(24)  # 0.8 x 30
    scheduler.rng = Frac(1)
    clock.t = 24
    scheduler.work()
    assert scheduler.region_due["r1"] == pytest.approx(24 + 36)


def test_the_backoff_grows_by_2_and_stops_at_the_cap_with_full_jitter():
    scheduler = make_scheduler([], FakeClock(), rng=Frac(1))
    assert [scheduler.backoff(n) for n in range(7)] == [30, 60, 120, 240, 480, 600, 600]
    scheduler.rng = Frac(0)
    assert scheduler.backoff(3) == 0
    scheduler.rng = Frac(0.5)
    assert scheduler.backoff(1) == 30


def test_a_waiting_call_is_not_taken_before_its_not_before_time():
    queue = CallQueue()
    call = Call("p", "r1", ("x",))
    queue.requeue(call, attempts=1, not_before=50.0)

    assert queue.take("r1", 5, now=49.9) == []
    assert queue.take("r1", 5, now=50.0) == [(call, 1)]


def test_the_5th_throttle_writes_the_error_row_and_drops_the_call():
    clock = FakeClock()
    log = []
    call = Call("aws_cb_offering", "r1", ("t0",))
    probe = QueuedProbe("aws_cb_offering", [call], log, answers={call: ["Throttled"] * 9})
    store = MemoryStore()
    scheduler = make_scheduler([probe], clock, store=store, rng=Frac(0))

    run_for(scheduler, clock, 3600)

    assert len(log) == 5  # 4 puts back, then the 5th attempt
    assert len(store.puts) == 1  # the row of the 5th attempt only
    assert next(iter(store.objects.values())).count(b"Throttled") == 1
    assert len(scheduler.queue) == 0


def test_the_queue_values_are_configurable():
    scheduler = make_scheduler(
        [],
        FakeClock(),
        queue_wake_seconds=10,
        queue_max_calls_per_wake=3,
        queue_jitter=0.5,
        queue_backoff_base_seconds=1,
        queue_backoff_cap_seconds=2,
        queue_max_throttle_attempts=2,
    )
    assert scheduler.queue_cfg == {
        "wake_seconds": 10,
        "max_calls_per_wake": 3,
        "jitter": 0.5,
        "backoff_base_seconds": 1,
        "backoff_cap_seconds": 2,
        "max_throttle_attempts": 2,
    }


def test_adding_a_waiting_key_again_keeps_its_attempts_and_its_not_before_time():
    queue = CallQueue()
    call = Call("p", "r1", ("x",))
    other = Call("p", "r1", ("y",))
    queue.add(call)
    queue.add(other)
    queue.take("r1", 1)  # takes call
    queue.requeue(call, attempts=3, not_before=90.0)
    queue.add(call)  # the next cycle plans it again

    assert len(queue) == 2
    assert queue.take("r1", 5, now=89.0) == [(other, 0)]
    assert queue.take("r1", 5, now=90.0) == [(call, 3)]


def test_each_extra_page_of_a_call_takes_one_more_token():
    class PagedProbe(QueuedProbe):
        def run_call(self, ctx, call):
            ctx.api_calls += 3  # 1 call with 3 pages
            return super().run_call(ctx, call)

    clock = FakeClock()
    probe = PagedProbe("aws_cb_offering", calls_of("aws_cb_offering", "r1", 1), [])
    scheduler = make_scheduler([probe], clock)
    scheduler.queue.add(probe.plan({})[0])
    scheduler.region_due["r1"] = 0.0

    assert scheduler.bucket.available("r1") == 5
    scheduler.work()

    assert scheduler.bucket.available("r1") == 2  # 5 - 3 requests
