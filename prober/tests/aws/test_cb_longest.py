"""The first free day and the longest Capacity Block. Spec 0008 section 3B."""

from datetime import UTC, datetime, timedelta

from ftg_prober.aws.cb_earliest import LEAD, next_boundary, search_window
from ftg_prober.aws.cb_longest import CapacityBlockLongestProbe, Offer, Step
from ftg_prober.callqueue import Call, CallQueue
from ftg_prober.config import Config
from ftg_prober.probes import Clients
from ftg_prober.record import format_ts
from ftg_prober.scheduler import Scheduler
from tests.aws.fakes import NOW, FakeClientError, FakeEc2, make_ctx, provider_response, section
from tests.conftest import FakeClock, MemoryStore


def one_region(**overrides):
    return section(
        regions=["us-east-1"],
        capacity_blocks={
            "instance_types": ["p5.48xlarge"],
            "instance_count": 1,
            "duration_hours": [24],
            "start_window_days": 14,
        },
        **overrides,
    )


def offer(start: datetime, end: datetime, zone="us-east-1a", offering_id="cb-1", fee="100.00"):
    return {
        "CapacityBlockOfferingId": offering_id,
        "InstanceType": "p5.48xlarge",
        "AvailabilityZone": zone,
        "InstanceCount": 1,
        "StartDate": start,
        "EndDate": end,
        "CapacityBlockDurationHours": int((end - start).total_seconds() // 3600),
        "UpfrontFee": fee,
        "CurrencyCode": "USD",
    }


def none_() -> dict:
    return {"CapacityBlockOfferings": []}


def offer_answer(*args, **kwargs) -> dict:
    return {"CapacityBlockOfferings": [offer(*args, **kwargs)]}


S = datetime(2026, 10, 30, 11, 30, tzinfo=UTC)
S_ISO = "2026-10-30T11:30:00.000Z"
# `next_boundary(NOW + LEAD)`: the B0 of a search planned at `NOW` (spec 0008, section 3B.2).
B0_ISO = "2026-10-12T11:30:00.000Z"


def test_an_offer_at_the_first_step_skips_the_bisection_and_gallops():
    ec2 = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[
            offer_answer(S, S + timedelta(days=1)),  # earliest.0: found, D=1 day
            none_(),  # gallop D=2: stop
        ],
    )
    rows = CapacityBlockLongestProbe().run(make_ctx(one_region(), {"us-east-1": ec2}))

    assert len(rows) == 1
    row = rows[0]
    assert row.probe_type == "aws_cb_longest"
    assert row.outcome == "success"
    assert row.zone == "us-east-1a"
    assert row.details["earliest_start"] == S_ISO
    assert row.details["longest_duration_hours"] == 24
    assert row.details["calls"] == 2
    assert [s["purpose"] for s in row.details["steps"]] == ["earliest.0", "gallop"]
    assert "_next" not in row.details


def test_the_bisection_finds_the_smallest_j_with_an_offer():
    # earliest.0: none. Bisect: j=28 none, j=42 offer(hi=42), j=35 none(lo=35), j=38 offer(hi=38),
    # j=36 offer(hi=36): hi - lo = 1, done at j=36. Then gallop D=2 fails: the search ends at 24h.
    answers = [
        none_(),  # earliest.0
        none_(),  # j=28
        offer_answer(S, S + timedelta(days=1)),  # j=42
        none_(),  # j=35
        offer_answer(S, S + timedelta(days=1)),  # j=38
        offer_answer(S, S + timedelta(days=1)),  # j=36
        none_(),  # gallop D=2, stop
    ]
    ec2 = FakeEc2("us-east-1", describe_capacity_block_offerings=list(answers))
    rows = CapacityBlockLongestProbe().run(make_ctx(one_region(), {"us-east-1": ec2}))

    assert len(rows) == 1
    row = rows[0]
    assert row.outcome == "success"
    assert [s["purpose"] for s in row.details["steps"]] == [
        "earliest.0",
        "earliest_bisect",
        "earliest_bisect",
        "earliest_bisect",
        "earliest_bisect",
        "earliest_bisect",
        "gallop",
    ]
    assert row.details["earliest_start"] == S_ISO
    assert row.details["longest_duration_hours"] == 24
    assert row.details["calls"] == 7


def test_no_offer_in_56_days_gives_the_none_outcome():
    ec2 = FakeEc2("us-east-1", describe_capacity_block_offerings=[none_() for _ in range(7)])
    rows = CapacityBlockLongestProbe().run(make_ctx(one_region(), {"us-east-1": ec2}))

    assert len(rows) == 1
    assert rows[0].outcome == "none"
    assert rows[0].zone is None
    assert rows[0].details["earliest_start"] is None
    assert rows[0].details["longest_duration_hours"] is None
    assert len(rows[0].details["steps"]) == 7  # earliest.0 + 6 bisect steps


def test_the_gallop_stops_and_the_bisect_narrows_between_the_bounds():
    # earliest.0 offer D=1. gallop D=2, D=4 ok; D=8 stop (a different start). The allowed-days gap
    # between 4 and 8 is [5, 6, 7]: bisect mid=6 ok, mid=7 ok -> hi - lo = 1, settles at 7 days.
    answers = [
        offer_answer(S, S + timedelta(days=1)),  # earliest.0
        offer_answer(S, S + timedelta(days=2)),  # gallop D=2
        offer_answer(S, S + timedelta(days=4)),  # gallop D=4
        offer_answer(S + timedelta(hours=1), S + timedelta(days=8)),  # gallop D=8: other start
        offer_answer(S, S + timedelta(days=6)),  # bisect D=6
        offer_answer(S, S + timedelta(days=7)),  # bisect D=7
    ]
    ec2 = FakeEc2("us-east-1", describe_capacity_block_offerings=list(answers))
    rows = CapacityBlockLongestProbe().run(make_ctx(one_region(), {"us-east-1": ec2}))

    assert len(rows) == 1
    row = rows[0]
    assert row.outcome == "success"
    assert [s["purpose"] for s in row.details["steps"]] == [
        "earliest.0",
        "gallop",
        "gallop",
        "gallop",
        "bisect",
        "bisect",
    ]
    assert row.details["longest_duration_hours"] == 7 * 24
    assert row.details["calls"] == 6


def test_the_gallop_reaches_14_days_and_stops_at_28():
    # gallop succeeds at D=2, 4, 8, 14, then D=28 has no offer. The allowed-days gap between 14
    # and 28 is [21]: 1 bisect step, also no offer, settles back at 14 days.
    answers = [
        offer_answer(S, S + timedelta(days=1)),  # earliest.0
        offer_answer(S, S + timedelta(days=2)),  # gallop D=2
        offer_answer(S, S + timedelta(days=4)),  # gallop D=4
        offer_answer(S, S + timedelta(days=8)),  # gallop D=8
        offer_answer(S, S + timedelta(days=14)),  # gallop D=14
        none_(),  # gallop D=28: stop
        none_(),  # bisect D=21
    ]
    ec2 = FakeEc2("us-east-1", describe_capacity_block_offerings=list(answers))
    rows = CapacityBlockLongestProbe().run(make_ctx(one_region(), {"us-east-1": ec2}))

    assert len(rows) == 1
    row = rows[0]
    assert row.outcome == "success"
    purposes = [s["purpose"] for s in row.details["steps"]]
    assert purposes == ["earliest.0", "gallop", "gallop", "gallop", "gallop", "gallop", "bisect"]
    assert [s["duration_hours"] for s in row.details["steps"] if s["purpose"] == "gallop"] == [
        2 * 24,
        4 * 24,
        8 * 24,
        14 * 24,
        28 * 24,
    ]
    assert row.details["longest_duration_hours"] == 14 * 24
    assert row.details["calls"] == 7


def test_an_error_mid_search_ends_the_search_with_the_raw_code_and_the_steps_so_far():
    ec2 = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[
            offer_answer(S, S + timedelta(days=1)),  # earliest.0
            FakeClientError("InvalidParameterValue"),  # gallop D=2
        ],
    )
    rows = CapacityBlockLongestProbe().run(make_ctx(one_region(), {"us-east-1": ec2}))

    assert len(rows) == 1
    row = rows[0]
    assert row.outcome == "error"
    assert row.error_code == "InvalidParameterValue"
    assert len(row.details["steps"]) == 2
    assert row.details["steps"][1]["result"] == "error"
    assert row.details["steps"][1]["error_code"] == "InvalidParameterValue"
    # The earliest offer, found before the error, is kept.
    assert row.details["earliest_start"] == S_ISO


def _dummy_step(purpose="gallop", result="none") -> Step:
    return Step(
        purpose=purpose,
        duration_hours=24,
        start="2026-10-13T00:00:00.000Z",
        end="2026-10-14T00:00:00.000Z",
        result=result,
        start_date=None,
        end_date=None,
        zone=None,
        offering_id=None,
        upfront_fee=None,
        currency=None,
        error_code=None,
        ts="2026-10-13T00:00:00.000Z",
        b0="2026-10-13T11:30:00.000Z",
    )


def test_a_search_never_makes_more_than_19_calls():
    # The true worst case is 19 calls (1 + 6 + 8 + 4), so the cap is defensive and does not cut
    # a call a well-formed search still needs. This builds an artificial state (18 steps already
    # made, still mid-bisect) that keeps needing calls past 19, to show the cap still holds.
    probe = CapacityBlockLongestProbe()
    steps = tuple(_dummy_step() for _ in range(18))
    earliest = Offer(
        start_date=S_ISO,
        end_date=None,
        zone="us-east-1a",
        offering_id="cb-earliest",
        upfront_fee=50.0,
        currency="USD",
        duration_hours=24,
    )
    longest_so_far = Offer(
        start_date=S_ISO,
        end_date=None,
        zone="us-east-1a",
        offering_id="cb-147",
        upfront_fee=50.0,
        currency="USD",
        duration_hours=147 * 24,
    )
    # (lo_idx, hi_idx) = (32, 37) (147, 182 days) gives the midpoint index 34 (161 days). An
    # offer there would normally keep the bisect going (hi_idx - lo_idx = 3), but this is the
    # 19th call: the cap must end the search here and keep this step's offer.
    call = Call(
        "aws_cb_longest",
        "us-east-1",
        (
            "p5.48xlarge",
            1,
            "bisect",
            (32, 37),
            earliest,
            longest_so_far,
            steps,
            "2026-10-13T00:00:00.000Z",
            B0_ISO,
        ),
    )
    ec2 = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[offer_answer(S, S + timedelta(days=161))],
    )
    rows = probe.run_call(make_ctx(one_region(), {"us-east-1": ec2}), call)

    assert len(rows) == 1
    row = rows[0]
    assert "_next" not in row.details
    assert len(row.details["steps"]) == 19
    assert row.outcome == "success"
    assert row.details["longest_duration_hours"] == 161 * 24  # the 19th call's offer is kept,
    # not the 154-day offer confirmed before it, and the bisect did not get a 20th, narrower call.


def test_a_new_cycle_does_not_duplicate_a_search_already_in_the_queue():
    # A planned `earliest0` call has other `args` than a mid-search follow-up (spec 0003, 6A, rule
    # 1): without a shared queue identity, cycle 2's call would sit alongside the running search's
    # call instead of being skipped, breaking "the queue does not grow" (spec 0008, 3.3 rule 8).
    probe = CapacityBlockLongestProbe()
    config = one_region()
    queue = CallQueue()
    for call in probe.plan(config):
        queue.add(call)
    assert len(queue) == 1

    # The search advances 1 step: the queue takes its call, and the step's own follow-up call
    # (a different phase, the same key) takes its place.
    [(call, _attempts)] = queue.take("us-east-1", 1)
    ec2 = FakeEc2("us-east-1", describe_capacity_block_offerings=[none_()])
    rows = probe.run_call(make_ctx(config, {"us-east-1": ec2}), call)
    for follow in probe.follow_up(call, rows):
        queue.add(follow)
    assert len(queue) == 1  # still mid-flight, 1 call for this (Region, type)

    # Cycle 2 starts while search 1 has not finished: `plan()` runs again.
    for call in probe.plan(config):
        queue.add(call)

    assert len(queue) == 1  # not duplicated
    [(remaining, _)] = queue.take("us-east-1", 5)
    assert remaining.args[2] != "earliest0"  # the running search's own step, not a fresh restart


def test_a_throttled_step_is_put_back_and_does_not_count_as_a_step():
    clock = FakeClock()
    first = CapacityBlockLongestProbe().plan(one_region())[0]
    ec2 = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[
            FakeClientError("RequestLimitExceeded"),
            offer_answer(S, S + timedelta(days=1)),
            none_(),  # gallop D=2: stop, finishes the search
        ],
    )
    store = MemoryStore()
    aws = section(regions=["us-east-1"], capacity_blocks=one_region()["capacity_blocks"])
    scheduler = Scheduler(
        [CapacityBlockLongestProbe()],
        config=Config(experiment="ftg", aws=aws, azure=None),
        provider="aws",
        store=store,
        clients=_clients({"us-east-1": ec2}),
        clock=clock,
        sleep=clock.sleep,
        now=lambda: NOW,
    )
    scheduler.queue.add(first)

    scheduler.region_due["us-east-1"] = 0.0
    scheduler.work()  # throttled: put back, no row
    assert len(store.puts) == 0
    assert len(scheduler.queue) == 1

    clock.t = 100  # past the backoff
    scheduler.region_due["us-east-1"] = 0.0
    scheduler.work()  # earliest.0 succeeds: a follow-up call, still no row
    assert len(store.puts) == 0
    assert len(scheduler.queue) == 1

    scheduler.region_due["us-east-1"] = clock.t
    scheduler.work()  # gallop D=2 fails: the search ends, 1 row
    assert len(store.puts) == 1
    rows = _rows_of(store)
    assert len(rows) == 1
    # The throttled attempt is not a step: only earliest.0 and gallop count.
    assert rows[0]["details"]["calls"] == 2
    assert [s["purpose"] for s in rows[0]["details"]["steps"]] == ["earliest.0", "gallop"]


def test_the_5th_throttle_writes_a_full_error_row_with_the_steps_so_far():
    # The queue drops the call and writes the row of the last allowed attempt (spec 0003, section
    # 6A, rule 4; default `queue_max_throttle_attempts` is 5). That row must keep the search's
    # state: it is not a bare, detail-less error row.
    clock = FakeClock()
    steps = (_dummy_step(purpose="earliest.0", result="offer"),)
    earliest = Offer(
        start_date=S_ISO,
        end_date=None,
        zone="us-east-1a",
        offering_id="cb-1",
        upfront_fee=10.0,
        currency="USD",
        duration_hours=24,
    )
    call = Call(
        "aws_cb_longest",
        "us-east-1",
        (
            "p5.48xlarge",
            1,
            "gallop",
            (0,),
            earliest,
            earliest,
            steps,
            "2026-10-13T00:00:00.000Z",
            B0_ISO,
        ),
    )
    ec2 = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[FakeClientError("RequestLimitExceeded")] * 5,
    )
    store = MemoryStore()
    aws = section(regions=["us-east-1"], capacity_blocks=one_region()["capacity_blocks"])
    scheduler = Scheduler(
        [CapacityBlockLongestProbe()],
        config=Config(experiment="ftg", aws=aws, azure=None),
        provider="aws",
        store=store,
        clients=_clients({"us-east-1": ec2}),
        clock=clock,
        sleep=clock.sleep,
        now=lambda: NOW,
    )
    scheduler.queue.add(call)

    for _ in range(5):
        scheduler.region_due["us-east-1"] = 0.0
        scheduler.work()
        clock.t += 700  # past the backoff cap (600 s) each time

    assert len(scheduler.queue) == 0  # dropped after the 5th attempt
    rows = _rows_of(store)
    assert len(rows) == 1
    row = rows[0]
    assert row["outcome"] == "error"
    assert row["error_code"] == "RequestLimitExceeded"
    assert row["zone"] == "us-east-1a"
    # The throttled attempts are not steps: only the 1 step made before them counts.
    assert row["details"]["calls"] == 1
    assert [s["purpose"] for s in row["details"]["steps"]] == ["earliest.0"]
    assert row["details"]["earliest_start"] == S_ISO


def _rows_of(store):
    import json

    return [
        json.loads(line) for data in store.objects.values() for line in data.decode().splitlines()
    ]


def _clients(fakes):
    def factory(service, region):
        assert service == "ec2"
        return fakes[region]

    return Clients(boto3_factory=factory)


def test_the_request_arguments_of_earliest_0():
    ec2 = FakeEc2("us-east-1", describe_capacity_block_offerings=[none_()])
    call = CapacityBlockLongestProbe().plan(one_region())[0]
    CapacityBlockLongestProbe().run_call(make_ctx(one_region(), {"us-east-1": ec2}), call)

    name, kwargs = ec2.calls[0]
    assert name == "describe_capacity_block_offerings"
    assert kwargs["InstanceType"] == "p5.48xlarge"
    assert kwargs["InstanceCount"] == 1
    assert kwargs["CapacityDurationHours"] == 24
    assert kwargs["StartDateRange"] == NOW
    assert kwargs["EndDateRange"] == search_window(NOW, 24)[1]


def test_the_request_arguments_of_an_earliest_bisect_step():
    # (lo, hi) = (0, 57) gives the midpoint j = 28.
    call = Call(
        "aws_cb_longest",
        "us-east-1",
        ("p5.48xlarge", 1, "earliest_bisect", (0, 57, None), None, None, (), None, B0_ISO),
    )
    ec2 = FakeEc2("us-east-1", describe_capacity_block_offerings=[none_()])
    CapacityBlockLongestProbe().run_call(make_ctx(one_region(), {"us-east-1": ec2}), call)

    _, kwargs = ec2.calls[0]
    assert kwargs["CapacityDurationHours"] == 24
    assert kwargs["StartDateRange"] == NOW
    b0 = next_boundary(NOW + LEAD)
    assert kwargs["EndDateRange"] == b0 + timedelta(days=28 + 1, hours=1)


def test_an_earliest_bisect_step_uses_the_search_fixed_b0_not_the_call_time():
    # B0 is fixed once, at the first call of the search, and carried forward (spec 0008, 3B.2).
    # Even though `now` of this call would itself give a later B0, the request must still use the
    # B0 that the search started with.
    fixed_b0 = next_boundary(NOW + LEAD)  # 2026-10-12 11:30 UTC
    later_now = fixed_b0 + timedelta(hours=1)  # past 11:00 UTC: a fresh B0 here would move a day
    args = (
        "p5.48xlarge",
        1,
        "earliest_bisect",
        (0, 57, None),
        None,
        None,
        (),
        None,
        format_ts(fixed_b0),
    )
    call = Call("aws_cb_longest", "us-east-1", args)
    ec2 = FakeEc2("us-east-1", describe_capacity_block_offerings=[none_()])
    ctx = make_ctx(one_region(), {"us-east-1": ec2}, now=later_now)
    rows = CapacityBlockLongestProbe().run_call(ctx, call)

    _, kwargs = ec2.calls[0]
    assert kwargs["StartDateRange"] == later_now  # StartDateRange is still `now` of the call
    assert kwargs["EndDateRange"] == fixed_b0 + timedelta(days=28 + 1, hours=1)
    next_call = rows[0].details["_next"]
    assert next_call.args[8] == format_ts(fixed_b0)  # the next step still carries the same B0


def test_b0_does_not_move_when_now_crosses_11_00_utc_between_2_bisect_steps():
    # earliest.0 at 10:50 UTC sets B0 to 11:30 UTC the same day. The 2nd step (a bisect) runs at
    # 11:10 UTC, past 11:00: a freshly computed B0 there would be 11:30 UTC the NEXT day, moving
    # the whole earliest-bisect window and breaking the search (spec 0008, section 3B.2).
    first_now = datetime(2026, 10, 13, 10, 50, tzinfo=UTC)
    second_now = datetime(2026, 10, 13, 11, 10, tzinfo=UTC)
    fixed_b0 = next_boundary(first_now + LEAD)
    assert fixed_b0 == datetime(2026, 10, 13, 11, 30, tzinfo=UTC)

    probe = CapacityBlockLongestProbe()
    config = one_region()
    ec2 = FakeEc2("us-east-1", describe_capacity_block_offerings=[none_(), none_()])
    first_call = probe.plan(config)[0]
    first_rows = probe.run_call(make_ctx(config, {"us-east-1": ec2}, now=first_now), first_call)
    second_call = first_rows[0].details["_next"]
    assert second_call.args[8] == format_ts(fixed_b0)  # B0 was set at the 1st call

    probe.run_call(make_ctx(config, {"us-east-1": ec2}, now=second_now), second_call)

    _, second_kwargs = ec2.calls[1]
    # j = 28 at (lo, hi) = (0, 57): the window still ends relative to the 1st call's B0.
    assert second_kwargs["EndDateRange"] == fixed_b0 + timedelta(days=28 + 1, hours=1)


def test_the_gallop_continues_when_a_first_start_drifts_later_between_calls():
    # S is a "first" start (now + 30 min), not an 11:30 UTC boundary: a later call's own "first"
    # offering drifts later than S, because that call's `now` is later, but it is still the same
    # possible block start (spec 0008, section 3B.2). Exact `StartDate` equality would see this
    # drift as a different start and stop the gallop at D=2, which is wrong.
    first_now = NOW  # 12:07:30 UTC
    s = first_now + LEAD  # 12:37:30 UTC
    later_now = first_now + timedelta(minutes=5)  # 12:12:30 UTC
    drifted_start = later_now + LEAD  # 12:42:30 UTC: later than S, but the same block day
    assert next_boundary(s).date() == next_boundary(drifted_start).date()

    earliest = Offer(
        start_date=format_ts(s),
        end_date=None,
        zone="us-east-1a",
        offering_id="cb-1",
        upfront_fee=10.0,
        currency="USD",
        duration_hours=24,
    )
    call = Call(
        "aws_cb_longest",
        "us-east-1",
        ("p5.48xlarge", 1, "gallop", (0,), earliest, earliest, (), None, B0_ISO),
    )
    ec2 = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[
            offer_answer(drifted_start, drifted_start + timedelta(days=2))
        ],
    )
    rows = CapacityBlockLongestProbe().run_call(
        make_ctx(one_region(), {"us-east-1": ec2}, now=later_now), call
    )

    assert "_next" in rows[0].details  # the gallop continued: it did not stop at D=2
    next_call = rows[0].details["_next"]
    assert next_call.args[2] == "gallop"
    assert next_call.args[3] == (1,)  # the gallop index advanced, past D=2


def test_the_earliest_phases_pick_the_smallest_start_date_not_the_first_item():
    # The API returns up to 6 offerings, by price, not by date (spec 0008, section 3.3). The
    # earliest phases must pick the smallest `StartDate` among them, not simply `items[0]`.
    later = offer(S + timedelta(days=1), S + timedelta(days=2), offering_id="cb-later")
    earlier = offer(S, S + timedelta(days=1), offering_id="cb-earlier")
    ec2 = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[
            {"CapacityBlockOfferings": [later, earlier]},
            none_(),  # gallop D=2: stop
        ],
    )
    rows = CapacityBlockLongestProbe().run(make_ctx(one_region(), {"us-east-1": ec2}))

    assert rows[0].details["earliest_start"] == S_ISO
    assert rows[0].details["earliest_offering_id"] == "cb-earlier"


def test_an_item_with_no_start_date_does_not_raise_and_the_search_still_ends():
    # An offering with no `StartDate` answers no question `_pick_offer` asks: it must be dropped,
    # not read with `item["StartDate"]`, which would raise KeyError, lose the row to the
    # scheduler's blanket exception handler, and lose the search entirely.
    no_start_date = {k: v for k, v in offer(S, S + timedelta(days=1)).items() if k != "StartDate"}
    ec2 = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[{"CapacityBlockOfferings": [no_start_date]}],
    )

    rows = CapacityBlockLongestProbe().run(make_ctx(one_region(), {"us-east-1": ec2}))

    assert len(rows) == 1
    row = rows[0]
    assert row.outcome == "none"  # the lone item with no StartDate does not count as an offer
    assert row.details["steps"][0]["result"] == "none"
    assert row.details["earliest_start"] is None


def test_the_gallop_stops_with_a_none_result_not_an_offer_result():
    # When no returned item is at S, the step is a stop: its own `result` must read `none`, not
    # `offer`, even though the call did return an offering (spec 0008, section 3B.2).
    other_start = offer(
        S + timedelta(hours=2), S + timedelta(days=2, hours=2), offering_id="cb-other"
    )
    earliest = Offer(
        start_date=S_ISO,
        end_date=None,
        zone="us-east-1a",
        offering_id="cb-1",
        upfront_fee=10.0,
        currency="USD",
        duration_hours=24,
    )
    call = Call(
        "aws_cb_longest",
        "us-east-1",
        ("p5.48xlarge", 1, "gallop", (0,), earliest, earliest, (), None, B0_ISO),
    )
    ec2 = FakeEc2(
        "us-east-1", describe_capacity_block_offerings=[{"CapacityBlockOfferings": [other_start]}]
    )

    rows = CapacityBlockLongestProbe().run_call(make_ctx(one_region(), {"us-east-1": ec2}), call)

    assert "_next" not in rows[0].details  # the stop ended the search
    assert rows[0].details["steps"][-1]["result"] == "none"
    assert rows[0].details["steps"][-1]["start_date"] is None


def test_the_gallop_picks_the_item_at_s_even_when_it_is_not_the_first_item():
    other_start = offer(
        S + timedelta(hours=2), S + timedelta(days=2, hours=2), offering_id="cb-other"
    )
    at_s = offer(S, S + timedelta(days=2), offering_id="cb-at-s")
    earliest = Offer(
        start_date=S_ISO,
        end_date=None,
        zone="us-east-1a",
        offering_id="cb-1",
        upfront_fee=10.0,
        currency="USD",
        duration_hours=24,
    )
    call = Call(
        "aws_cb_longest",
        "us-east-1",
        ("p5.48xlarge", 1, "gallop", (0,), earliest, earliest, (), None, B0_ISO),
    )
    ec2 = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[{"CapacityBlockOfferings": [other_start, at_s]}],
    )
    rows = CapacityBlockLongestProbe().run_call(make_ctx(one_region(), {"us-east-1": ec2}), call)

    # Had `items[0]` (the other start) been used, the gallop would have read it as a stop.
    assert "_next" in rows[0].details
    assert rows[0].details["_next"].args[3] == (1,)


def test_the_request_arguments_of_a_gallop_step():
    earliest = Offer(
        start_date=S_ISO,
        end_date=None,
        zone="us-east-1a",
        offering_id="cb-1",
        upfront_fee=10.0,
        currency="USD",
        duration_hours=24,
    )
    call = Call(
        "aws_cb_longest",
        "us-east-1",
        ("p5.48xlarge", 1, "gallop", (0,), earliest, earliest, (), None, B0_ISO),
    )
    ec2 = FakeEc2("us-east-1", describe_capacity_block_offerings=[none_()])
    CapacityBlockLongestProbe().run_call(make_ctx(one_region(), {"us-east-1": ec2}), call)

    _, kwargs = ec2.calls[0]
    assert kwargs["CapacityDurationHours"] == 2 * 24
    assert kwargs["StartDateRange"] == NOW
    assert kwargs["EndDateRange"] == S + timedelta(days=2, hours=1)


def test_the_request_arguments_of_a_bisect_step():
    earliest = Offer(
        start_date=S_ISO,
        end_date=None,
        zone="us-east-1a",
        offering_id="cb-1",
        upfront_fee=10.0,
        currency="USD",
        duration_hours=24,
    )
    # (lo_idx, hi_idx) = (3, 7) (4 days, 8 days) gives the midpoint index 5 (6 days).
    call = Call(
        "aws_cb_longest",
        "us-east-1",
        ("p5.48xlarge", 1, "bisect", (3, 7), earliest, earliest, (), None, B0_ISO),
    )
    ec2 = FakeEc2("us-east-1", describe_capacity_block_offerings=[none_()])
    CapacityBlockLongestProbe().run_call(make_ctx(one_region(), {"us-east-1": ec2}), call)

    _, kwargs = ec2.calls[0]
    assert kwargs["CapacityDurationHours"] == 6 * 24
    assert kwargs["StartDateRange"] == NOW
    assert kwargs["EndDateRange"] == S + timedelta(days=6, hours=1)


def test_the_probe_plan_is_1_call_per_region_and_type():
    config = one_region()
    calls = CapacityBlockLongestProbe().plan(config)

    assert len(calls) == 1
    call = calls[0]
    assert call.probe_type == "aws_cb_longest"
    assert call.region == "us-east-1"
    instance_type, count, phase, state, earliest, longest, steps, planned_at, b0 = call.args
    assert (instance_type, count, phase, state, earliest, longest, steps, planned_at, b0) == (
        "p5.48xlarge",
        1,
        "earliest0",
        (),
        None,
        None,
        (),
        None,
        None,
    )


def test_the_provider_response_gives_the_real_offer_fields_of_the_earliest_step():
    """Runs the real `run_call` (the `earliest.0` step, spec 0008 section 3B.2) against a
    reconstructed `DescribeCapacityBlockOfferings` response, in the shape of the AWS EC2 API
    reference (`CapacityBlockOffering` object). The search continues (`_next`), so this step's
    parsed fields travel inside the carried `Offer`, not in the row's own `details`. This
    project stores no `p5.48xlarge` search to ground an offer in, so `UpfrontFee` is
    illustrative; the offer's `StartDate`/`EndDate` are a plausible answer to the `earliest.0`
    request this step sends (`search_window(NOW, 24)`): 1 day (24 hours) starting at the next
    11:30 UTC boundary, inside `[StartDateRange, EndDateRange]`.
    https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html
    https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CapacityBlockOffering.html
    """
    body = provider_response("describe_capacity_block_offerings_longest_step_response.json")
    config = one_region()
    probe = CapacityBlockLongestProbe()
    call = probe.plan(config)[0]
    ec2 = FakeEc2("us-east-1", describe_capacity_block_offerings=[body])
    rows = probe.run_call(make_ctx(config, {"us-east-1": ec2}), call)

    assert len(rows) == 1
    kwargs = ec2.calls[0][1]
    assert kwargs["CapacityDurationHours"] == 24
    offer_start = datetime(2026, 10, 12, 11, 30, tzinfo=UTC)
    offer_end = datetime(2026, 10, 13, 11, 30, tzinfo=UTC)
    assert kwargs["StartDateRange"] <= offer_start
    assert offer_end <= kwargs["EndDateRange"]
    row = rows[0]
    assert (row.probe_type, row.outcome, row.error_code) == ("aws_cb_longest", "none", None)
    next_call = row.details["_next"]
    assert isinstance(next_call, Call)
    _instance_type, _count, next_phase, _state, earliest, longest, *_ = next_call.args
    assert next_phase == "gallop"
    assert earliest == Offer(
        start_date="2026-10-12T11:30:00.000Z",
        end_date="2026-10-13T11:30:00.000Z",
        zone="us-east-1a",
        offering_id="cb-0123456789abcdef3",
        upfront_fee=768.0,
        currency="USD",
        duration_hours=24,
        duration_minutes=0,
    )
    assert longest == earliest


def counts_config():
    return section(
        regions=["us-east-1"],
        capacity_blocks={
            "instance_types": ["p5.4xlarge", "p5.48xlarge"],
            "instance_count": 1,
            "instance_counts": {"p5.4xlarge": [1, 4, 8]},
            "duration_hours": [24],
            "start_window_days": 14,
        },
    )


def test_the_plan_has_1_search_per_type_and_count_with_distinct_keys():
    calls = CapacityBlockLongestProbe().plan(counts_config())

    assert [(c.args[0], c.args[1]) for c in calls] == [
        ("p5.4xlarge", 1),
        ("p5.4xlarge", 4),
        ("p5.4xlarge", 8),
        ("p5.48xlarge", 1),
    ]
    assert [c.key for c in calls] == [
        ("p5.4xlarge", 1),
        ("p5.4xlarge", 4),
        ("p5.4xlarge", 8),
        ("p5.48xlarge", 1),
    ]


def test_two_searches_of_one_type_with_different_counts_do_not_dedupe_each_other():
    queue = CallQueue()
    for call in CapacityBlockLongestProbe().plan(counts_config()):
        queue.add(call)
        queue.add(call)  # a 2nd cycle adds the same plan again

    assert len(queue) == 4


def test_a_search_asks_for_its_own_count_at_every_step_and_writes_it_in_n_requested():
    probe = CapacityBlockLongestProbe()
    config = counts_config()
    call = probe.plan(config)[1]  # p5.4xlarge, count 4
    ec2 = FakeEc2("us-east-1", describe_capacity_block_offerings=[none_()] * 20)
    rows = probe._run_to_end(make_ctx(config, {"us-east-1": ec2}), call)

    assert len(ec2.calls) > 1
    assert {kwargs["InstanceCount"] for _, kwargs in ec2.calls} == {4}
    assert {kwargs["InstanceType"] for _, kwargs in ec2.calls} == {"p5.4xlarge"}
    [row] = rows
    assert row.n_requested == 4
    assert row.sku == "p5.4xlarge"
