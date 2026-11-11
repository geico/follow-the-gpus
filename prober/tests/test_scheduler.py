import json
import logging

import pytest

from ftg_prober.config import Config
from ftg_prober.probes import Probe
from ftg_prober.scheduler import Scheduler
from tests.test_record import make_record


class CountingProbe(Probe):
    provider = "aws"

    def __init__(self, probe_type="aws_spot_score", default_cadence="5m", rows=1, fail=False):
        self.probe_type = probe_type
        self.default_cadence = default_cadence
        self.rows = rows
        self.fail = fail
        self.calls = []

    def run(self, ctx):
        self.calls.append(ctx)
        if self.fail:
            raise RuntimeError("boom")
        return [
            make_record(probe_type=self.probe_type, run_id=ctx.run_id, simulated=ctx.simulate)
            for _ in range(self.rows)
        ]


def make_config(**aws) -> Config:
    section = {"regions": ["us-east-1"], "spot_score_requests": [], "cadence": {}}
    section.update(aws)
    return Config(experiment="ftg", aws=section, azure=None)


def make_scheduler(probes, clock, store, **kwargs):
    config = kwargs.pop("config", make_config())
    return Scheduler(
        probes,
        config=config,
        provider="aws",
        store=store,
        clock=clock,
        sleep=clock.sleep,
        now=clock.now,
        **kwargs,
    )


def test_config_cadence_overrides_default_cadence(clock, memory_store):
    probe = CountingProbe(default_cadence="5m")
    config = make_config(cadence={"aws_spot_score": "1m"})
    scheduler = make_scheduler([probe], clock, memory_store, config=config)
    assert scheduler.cadence_seconds(probe) == 60
    scheduler = make_scheduler([probe], clock, memory_store)
    assert scheduler.cadence_seconds(probe) == 300


def test_run_once_runs_each_probe_one_time_and_writes_each_call_as_one_object(clock, memory_store):
    a = CountingProbe("aws_spot_score", rows=3)
    b = CountingProbe("aws_spot_price", rows=2)
    scheduler = make_scheduler([a, b], clock, memory_store)
    assert scheduler.run_once() == 2
    assert len(a.calls) == 1 and len(b.calls) == 1
    assert len(memory_store.puts) == 2
    for key, data in memory_store.objects.items():
        lines = data.decode().splitlines()
        rows = [json.loads(line) for line in lines]
        assert {row["run_id"] for row in rows} == {rows[0]["run_id"]}
        assert f"probe_type={rows[0]['probe_type']}/" in key
        assert key.startswith("experiment=ftg/provider=aws/")
    assert a.calls[0].run_id != b.calls[0].run_id


def test_run_once_with_probe_name_limits_to_one_probe(clock, memory_store):
    a = CountingProbe("aws_spot_score")
    b = CountingProbe("aws_spot_price")
    scheduler = make_scheduler([a, b], clock, memory_store)
    assert scheduler.run_once("aws_spot_price") == 1
    assert len(a.calls) == 0 and len(b.calls) == 1
    with pytest.raises(LookupError, match="no probe named 'nope'"):
        scheduler.run_once("nope")


def test_run_forever_runs_each_probe_at_its_cadence_with_a_fake_clock(clock, memory_store):
    a = CountingProbe("aws_spot_score", default_cadence="5m")
    b = CountingProbe("aws_spot_price", default_cadence="15m")
    scheduler = make_scheduler([a, b], clock, memory_store)
    calls = scheduler.run_forever(stop=lambda: clock() >= 1800)
    assert len(a.calls) == 6  # t = 0, 300, 600, 900, 1200, 1500
    assert len(b.calls) == 2  # t = 0, 900
    assert calls == 8
    assert clock.sleeps == [300.0] * 6
    assert len(memory_store.puts) == 8


def test_a_probe_exception_is_logged_and_the_others_still_run(clock, memory_store, caplog):
    bad = CountingProbe("aws_spot_score", fail=True)
    good = CountingProbe("aws_spot_price")
    scheduler = make_scheduler([bad, good], clock, memory_store)
    with caplog.at_level(logging.ERROR):
        scheduler.run_forever(stop=lambda: clock() >= 600)
    assert len(bad.calls) == 2
    assert len(good.calls) == 2
    assert len(memory_store.puts) == 2
    assert any("probe.failed" in r.message and "boom" in r.message for r in caplog.records)


def test_a_store_exception_is_logged_and_the_loop_goes_on(clock, caplog):
    class BrokenStore:
        url = "broken://"

        def put(self, key, data):
            raise OSError("disk full")

    probe = CountingProbe()
    scheduler = make_scheduler([probe], clock, BrokenStore())
    with caplog.at_level(logging.ERROR):
        scheduler.run_forever(stop=lambda: clock() >= 600)
    assert len(probe.calls) == 2
    assert sum("probe.failed" in r.message for r in caplog.records) == 2


def test_no_rows_means_no_object(clock, memory_store):
    probe = CountingProbe(rows=0)
    make_scheduler([probe], clock, memory_store).run_once()
    assert memory_store.puts == []


def test_the_key_uses_the_schedulers_experiment(clock, memory_store):
    probe = CountingProbe()
    make_scheduler([probe], clock, memory_store).run_once()
    assert memory_store.puts[0].startswith("experiment=ftg/")


def test_mixed_simulated_values_give_one_object_per_value(clock, memory_store, caplog):
    class MixedProbe(CountingProbe):
        def run(self, ctx):
            return [
                make_record(simulated=False, sku="a"),
                make_record(simulated=True, sku="b"),
                make_record(simulated=False, sku="c"),
            ]

    with caplog.at_level(logging.WARNING):
        make_scheduler([MixedProbe()], clock, memory_store).run_once()
    assert len(memory_store.puts) == 2
    real, simulated = (memory_store.objects[key] for key in memory_store.puts)
    assert [json.loads(line)["sku"] for line in real.decode().splitlines()] == ["a", "c"]
    assert [json.loads(line)["sku"] for line in simulated.decode().splitlines()] == ["b"]
    assert "_multi-us-east-1_" in memory_store.puts[0]
    assert "_b-us-east-1_" in memory_store.puts[1]
    assert all(key.startswith("experiment=ftg/provider=aws/") for key in memory_store.puts)


def azure_scheduler(probe, clock, store, **kwargs):
    probe.provider = "azure"
    config = Config(
        experiment="ftg",
        aws=None,
        azure={"regions": ["eastus"], "cadence": kwargs.pop("cadence", {})},
    )
    return Scheduler(
        [probe],
        config=config,
        provider="azure",
        store=store,
        clock=clock,
        sleep=clock.sleep,
        now=clock.now,
        **kwargs,
    )


def test_an_az_precheck_row_is_valid_and_uses_the_az_provisioning_cadence(
    clock, memory_store, caplog
):
    probe = CountingProbe("az_precheck", default_cadence="4h")
    scheduler = azure_scheduler(probe, clock, memory_store, cadence={"az_provisioning": "2h"})
    assert scheduler.cadence_seconds(probe) == 2 * 3600
    with caplog.at_level(logging.WARNING):
        scheduler.run_once()
    assert memory_store.puts[0].startswith("experiment=ftg/provider=azure/probe_type=az_precheck/")
    assert not any("row.invalid" in r.message for r in caplog.records)


def test_run_wide_simulate_still_calls_the_az_precheck_probe(clock, memory_store):
    """An provisioning probe type is never in `CORE_SIM_PROBE_TYPES`: the run-wide `--simulate`
    flag still calls the probe's own code, which reads `ctx.simulate` itself (ADR 0011
    section 7, item 6)."""
    probe = CountingProbe("az_precheck", default_cadence="2h")
    rows = azure_scheduler(probe, clock, memory_store, simulate=True).run_probe(probe)
    assert len(probe.calls) == 1
    assert [row.simulated for row in rows] == [True]


def test_run_wide_simulate_writes_synthetic_rows_for_a_signal_probe_without_calling_it(
    clock, memory_store, repo_config
):
    probe = CountingProbe("aws_spot_score")
    scheduler = make_scheduler([probe], clock, memory_store, config=repo_config, simulate=True)
    rows = scheduler.run_probe(probe)
    assert probe.calls == []
    assert len(rows) == 3 * 9
    assert {row.simulated for row in rows} == {True}
    assert len(memory_store.puts) == 1
    assert memory_store.puts[0].startswith(
        "experiment=ftg-20261014-example/provider=aws/probe_type=aws_spot_score/"
    )


def test_run_wide_simulate_writes_synthetic_rows_for_cb_offerings_without_calling_the_probe(
    clock, memory_store, repo_config
):
    probe = CountingProbe("aws_cb_offering", default_cadence="2h")
    scheduler = make_scheduler([probe], clock, memory_store, config=repo_config, simulate=True)
    rows = scheduler.run_probe(probe)
    assert probe.calls == []
    assert rows
    assert {row.simulated for row in rows} == {True}
    assert {row.probe_type for row in rows} == {"aws_cb_offering"}
    assert all(
        key.startswith("experiment=ftg-20261014-example/provider=aws/probe_type=aws_cb_offering/")
        for key in memory_store.puts
    )


def test_run_wide_simulate_still_calls_an_allocation_probe(clock, memory_store):
    probe = CountingProbe("az_vmss_flex", default_cadence="2h")
    probe.provider = "azure"
    config = Config(experiment="ftg", aws=None, azure={"regions": ["eastus"], "cadence": {}})
    scheduler = Scheduler(
        [probe],
        config=config,
        provider="azure",
        store=memory_store,
        simulate=True,
        clock=clock,
        sleep=clock.sleep,
        now=clock.now,
    )
    rows = scheduler.run_probe(probe)
    assert len(probe.calls) == 1
    assert [row.simulated for row in rows] == [True]
    assert memory_store.puts[0].startswith("experiment=ftg/provider=azure/probe_type=az_vmss_flex/")


def test_a_jobs_own_simulate_key_writes_synthetic_rows_for_a_signal_probe(
    clock, memory_store, repo_config
):
    """ADR 0011 section 2: a job's own `simulate: true` key, with no run-wide flag."""
    probe = CountingProbe("aws_spot_score")
    config = Config(
        experiment=repo_config.experiment,
        aws=repo_config.aws,
        azure=repo_config.azure,
        job_types=repo_config.job_types,
        job_simulate={"aws": {"aws_spot_score": True}},
    )
    scheduler = make_scheduler([probe], clock, memory_store, config=config)
    rows = scheduler.run_probe(probe)
    assert probe.calls == []
    assert {row.simulated for row in rows} == {True}


def test_probes_of_another_provider_are_ignored(clock, memory_store):
    azure_probe = CountingProbe("az_spot")
    azure_probe.provider = "azure"
    scheduler = make_scheduler([azure_probe, CountingProbe()], clock, memory_store)
    assert [p.probe_type for p in scheduler.probes] == ["aws_spot_score"]


def test_a_job_eligible_probe_type_with_no_job_is_not_loaded(clock, memory_store, tmp_path):
    """ADR 0010 section 1: a job-eligible probe type with no job never runs. A config with an
    aws_spot_score job but no aws_spot_price job must not load aws_spot_price, even though
    both probes were handed to the scheduler; if `Config.has_job` filtering is removed, this
    must fail."""
    from ftg_prober.config import load_config

    path = tmp_path / "t.yaml"
    path.write_text(
        "experiment: ftg-20261012-signals\n"
        "aws:\n"
        "  modules:\n"
        "    aws_spot_score: {type: aws_spot_score, spot_score_requests: "
        "[{name: p5, instance_types: [p5.4xlarge], label: x}]}\n"
        "  jobs:\n"
        "    - {module: aws_spot_score, regions: [us-east-1], interval: 5m}\n"
    )
    config = load_config(path)
    probes = [CountingProbe("aws_spot_score"), CountingProbe("aws_spot_price")]
    scheduler = Scheduler(
        probes,
        config=config,
        provider="aws",
        store=memory_store,
        clock=clock,
        sleep=clock.sleep,
        now=clock.now,
    )
    assert [p.probe_type for p in scheduler.probes] == ["aws_spot_score"]
