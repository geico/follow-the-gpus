"""The 6 AWS probes load through the core loader and run at their cadence."""

import json

from ftg_prober.aws.spot_advisor import SpotAdvisorProbe
from ftg_prober.config import Config
from ftg_prober.probes import Clients, load_probes, packages_for
from ftg_prober.scheduler import Scheduler
from tests.aws.fakes import section
from tests.aws.test_spot_advisor import FakeHttp, FakeResponse, feed
from tests.conftest import FakeClock, MemoryStore

EXPECTED = {
    "aws_cb_offering": "2h",
    "aws_cb_earliest": "2h",
    "aws_cb_longest": "2h",
    "aws_spot_score": "5m",
    "aws_spot_price": "5m",
    "aws_spot_advisor": "24h",
    "aws_odcr": "2h",
}


def test_the_loader_finds_the_6_aws_probes_with_the_d2_cadence():
    probes = load_probes(packages_for("aws"))
    assert {p.probe_type: p.default_cadence for p in probes} == EXPECTED
    assert {p.provider for p in probes} == {"aws"}


def test_the_feed_is_fetched_once_a_day():
    clock = FakeClock()
    http = FakeHttp(FakeResponse(200, json.dumps(feed()).encode()))
    probe = SpotAdvisorProbe(http=http)
    aws = section()
    aws["cadence"] = {}
    store = MemoryStore()
    scheduler = Scheduler(
        [probe],
        config=Config(experiment="ftg", aws=aws, azure=None),
        provider="aws",
        store=store,
        clients=Clients(boto3_factory=lambda s, r: None),
        clock=clock,
        sleep=clock.sleep,
        now=clock.now,
    )
    assert scheduler.cadence_s["aws_spot_advisor"] == 24 * 3600

    stop_after = 47 * 3600
    scheduler.run_forever(stop=lambda: clock.t >= stop_after)

    assert len(http.calls) == 2
    raw = [k for k in store.puts if "_raw_" in k]
    rows = [k for k in store.puts if k.endswith(".jsonl")]
    assert len(raw) == 2
    assert len(rows) == 2
