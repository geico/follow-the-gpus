import pytest

from ftg_prober import cli
from ftg_prober.probes import Probe
from tests.conftest import TARGETS_PATH
from tests.test_record import make_record


def test_check_config_ok_exits_0(capsys):
    assert cli.main(["check-config", str(TARGETS_PATH)]) == 0
    out = capsys.readouterr().out
    assert "ok" in out and "providers=aws,azure" in out


def test_check_config_fault_exits_2_with_a_message(tmp_path, capsys):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "azure:\n"
        "  jobs:\n    - {module: az_spot_score, regions: [eastus]}\n"
        "  provisioning_probes: {az_vmss_flex: [{sku: x}]}\n"
    )
    assert cli.main(["check-config", str(path)]) == 2
    err = capsys.readouterr().err
    assert "config error" in err
    assert "azure.jobs[az_spot_score].spot_sizes" in err
    assert "az_vmss_flex[0].region" in err


def test_check_config_missing_file_exits_2(tmp_path, capsys):
    assert cli.main(["check-config", str(tmp_path / "none.yaml")]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_check_config_provider_ok_exits_0(capsys):
    # ADR 0012 section 4, rule 4: the gate (config.section, load_probes, Scheduler, select)
    # passes for a config that already has an aws: section.
    assert cli.main(["check-config", str(TARGETS_PATH), "--provider", "aws"]) == 0
    out = capsys.readouterr().out
    assert "ok" in out and "providers=aws,azure" in out


def test_check_config_provider_missing_section_exits_2(tmp_path, capsys):
    # check-config alone passes a config with no aws: section; the --provider gate must not,
    # because `run --provider aws` would fail at config.section(args.provider) (ADR 0012).
    path = tmp_path / "azure_only.yaml"
    path.write_text(
        "experiment: ftg-20261014-azure-only\n"
        "azure:\n"
        "  modules:\n"
        "    az_spot_score: {type: az_spot_score, spot_sizes: [Standard_NC40ads_H100_v5]}\n"
        "  jobs:\n    - {module: az_spot_score, regions: [eastus]}\n"
    )
    assert cli.main(["check-config", str(path)]) == 0
    assert cli.main(["check-config", str(path), "--provider", "aws"]) == 2
    err = capsys.readouterr().err
    assert "config error" in err


def test_sim_writes_history_to_a_file_store(tmp_path):
    out = tmp_path / "data" / "sim"
    assert (
        cli.main(["sim", "--config", str(TARGETS_PATH), "--store", f"file://{out}", "--hours", "1"])
        == 0
    )
    files = [p for p in out.rglob("*") if p.is_file()]
    assert len(files) > 50
    prefix = "experiment=ftg-20261014-example/"
    assert all(str(p.relative_to(out)).startswith(prefix) for p in files)


def test_sim_rejects_a_bad_store_url_and_hours(tmp_path, capsys):
    assert (
        cli.main(["sim", "--config", str(TARGETS_PATH), "--store", "gs://x", "--hours", "1"]) == 2
    )
    assert "unknown store URL scheme" in capsys.readouterr().err
    assert (
        cli.main(
            ["sim", "--config", str(TARGETS_PATH), "--store", f"file://{tmp_path}", "--hours", "0"]
        )
        == 2
    )


def test_sim_refuses_a_store_that_is_not_file(tmp_path, capsys):
    """ADR 0011 section 2: `sim` gets the same `file://`-only store rule as `run --simulate`."""
    assert (
        cli.main(["sim", "--config", str(TARGETS_PATH), "--store", "s3://a-bucket", "--hours", "1"])
        == 2
    )
    assert "sim refuses a --store that is not file://" in capsys.readouterr().err


class OneProbe(Probe):
    probe_type = "aws_spot_score"
    provider = "aws"
    default_cadence = "5m"
    calls = 0

    def run(self, ctx):
        OneProbe.calls += 1
        return [make_record(run_id=ctx.run_id, simulated=ctx.simulate)]


def test_run_once_runs_the_loaded_probes_and_writes_one_object(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_probes", lambda packages: [OneProbe()])
    OneProbe.calls = 0
    out = tmp_path / "store"
    args = [
        "run",
        "--provider",
        "aws",
        "--config",
        str(TARGETS_PATH),
        "--store",
        f"file://{out}",
        "--once",
    ]
    assert cli.main(args) == 0
    assert OneProbe.calls == 1
    files = [p for p in out.rglob("*.jsonl") if "/logs/" not in p.as_posix()]
    assert len(files) == 1
    assert "experiment=ftg-20261014-example/provider=aws/probe_type=aws_spot_score/" in str(
        files[0]
    )
    # The second log handler (spec 0003 section 8.2) flushes `run.start` and `probe.done` to
    # the logs prefix at shutdown, as 1 more object under the same store.
    logs_files = [p for p in out.rglob("*.jsonl") if "/logs/" in p.as_posix()]
    assert len(logs_files) == 1
    assert "experiment=ftg-20261014-example/logs/provider=aws/" in str(logs_files[0])


def test_run_once_with_simulate_writes_simulated_rows_to_a_file_store(tmp_path, monkeypatch):
    """A signal probe's job runs the `sim.py` generator under `--simulate` (ADR 0011 section
    7, item 6), so `OneProbe.run` is never called; every written row is `simulated: true`."""
    monkeypatch.setattr(cli, "load_probes", lambda packages: [OneProbe()])
    OneProbe.calls = 0
    out = tmp_path / "store"
    args = [
        "run",
        "--provider",
        "aws",
        "--config",
        str(TARGETS_PATH),
        "--store",
        f"file://{out}",
        "--simulate",
        "--once",
    ]
    assert cli.main(args) == 0
    assert OneProbe.calls == 0
    files = [p for p in out.rglob("*.jsonl") if "/logs/" not in p.as_posix()]
    assert files and str(files[0]).startswith(str(out / "experiment=ftg-20261014-example/"))
    assert all('"simulated":true' in line for line in files[0].read_text().splitlines() if line)
    # A `--simulate` run writes its logs to the same local store as its rows (spec 0003
    # section 8.2; the research note's recommendation 4).
    logs_files = [p for p in out.rglob("*.jsonl") if "/logs/" in p.as_posix()]
    assert logs_files


def test_run_simulate_refuses_a_store_that_is_not_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_probes", lambda packages: [OneProbe()])
    args = [
        "run",
        "--provider",
        "aws",
        "--config",
        str(TARGETS_PATH),
        "--store",
        "s3://a-bucket",
        "--simulate",
        "--once",
    ]
    assert cli.main(args) == 2
    assert "run --simulate refuses a --store that is not file://" in capsys.readouterr().err


def test_run_rejects_an_unknown_probe(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_probes", lambda packages: [OneProbe()])
    base = [
        "run",
        "--provider",
        "aws",
        "--config",
        str(TARGETS_PATH),
        "--store",
        f"file://{tmp_path}",
        "--once",
    ]
    assert cli.main([*base, "--probe", "nope"]) == 2
    assert "no probe named 'nope'" in capsys.readouterr().err


def _run_args(config, tmp_path):
    store = f"file://{tmp_path}/s"
    return ["run", "--provider", "aws", "--config", str(config), "--store", store, "--once"]


def _with_allocation_aws_entry(allow_billable_provisioning=False, simulate=False):
    entry = {"instance_type": "m5.large", "region": "us-east-1", "n": 2}
    if allow_billable_provisioning:
        entry["allow_billable_provisioning"] = True
    if simulate:
        entry["simulate"] = True
    import yaml

    data = yaml.safe_load(TARGETS_PATH.read_text())
    data["aws"]["provisioning_probes"] = [entry]
    return yaml.safe_dump(data)


def test_allocation_guard_without_allow_billable_provisioning_exits_2(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "load_probes", lambda packages: [OneProbe()])
    config = tmp_path / "allocation.yaml"
    config.write_text(_with_allocation_aws_entry())
    assert cli.main(_run_args(config, tmp_path)) == 2
    err = capsys.readouterr().err
    assert "aws.provisioning_probes[0]" in err and "allow_billable_provisioning: true" in err
    assert not list(tmp_path.rglob("*.jsonl"))
    assert cli.main(["check-config", str(config)]) == 2
    assert "allow_billable_provisioning: true" in capsys.readouterr().err


def test_allocation_guard_passes_with_allow_billable_provisioning_true(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_probes", lambda packages: [OneProbe()])
    config = tmp_path / "allocation.yaml"
    config.write_text(_with_allocation_aws_entry(allow_billable_provisioning=True))
    assert cli.main(_run_args(config, tmp_path)) == 0
    assert cli.main(["check-config", str(config)]) == 0


def test_allocation_guard_skipped_by_the_run_wide_simulate_flag(tmp_path, monkeypatch):
    """ADR 0011 section 3, rule 1: `run --simulate` simulates every job, so the guard never
    fires, even with no `allow_billable_provisioning` on the provisioning entry."""
    monkeypatch.setattr(cli, "load_probes", lambda packages: [OneProbe()])
    config = tmp_path / "allocation.yaml"
    config.write_text(_with_allocation_aws_entry())
    store = f"file://{tmp_path}/s"
    args = [
        "run",
        "--provider",
        "aws",
        "--config",
        str(config),
        "--store",
        store,
        "--simulate",
        "--once",
    ]
    assert cli.main(args) == 0


def test_simulated_entry_needs_no_allow_billable_provisioning(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_probes", lambda packages: [OneProbe()])
    config = tmp_path / "allocation.yaml"
    config.write_text(_with_allocation_aws_entry(simulate=True))
    assert cli.main(_run_args(config, tmp_path)) == 0
    assert cli.main(["check-config", str(config)]) == 0


def test_run_with_no_probes_for_the_provider_exits_0(tmp_path):
    args = [
        "run",
        "--provider",
        "azure",
        "--config",
        str(TARGETS_PATH),
        "--store",
        f"file://{tmp_path}",
        "--once",
    ]
    assert cli.main(args) == 0


def test_run_forever_stops_on_the_stop_event(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_probes", lambda packages: [OneProbe()])
    OneProbe.calls = 0
    import signal

    monkeypatch.setattr(signal, "signal", lambda *_: None)

    class StopSoon:
        def __init__(self):
            self.ticks = 0

        def __call__(self):
            self.ticks += 1
            return self.ticks > 1

    original = cli.Scheduler.run_forever

    def run_forever(self, probe_name=None, stop=lambda: False):
        self.sleep = lambda seconds: None
        return original(self, probe_name, stop=StopSoon())

    monkeypatch.setattr(cli.Scheduler, "run_forever", run_forever)
    args = [
        "run",
        "--provider",
        "aws",
        "--config",
        str(TARGETS_PATH),
        "--store",
        f"file://{tmp_path}",
    ]
    assert cli.main(args) == 0
    assert OneProbe.calls == 1


def test_usage_error_exits_2():
    with pytest.raises(SystemExit) as info:
        cli.main(["run"])
    assert info.value.code == 2


def test_bad_log_level_exits_2_not_a_traceback(capsys):
    assert cli.main(["--log-level", "bogus", "check-config", str(TARGETS_PATH)]) == 2
    assert "config error" in capsys.readouterr().err


def test_release_id_of_reads_the_release_folder_through_the_current_symlink(tmp_path):
    # The VM layout of ADR 0012 section 4: current -> releases/<release ID>, config under it.
    rid = "ab" * 32
    cfg = tmp_path / "releases" / rid / "config"
    cfg.mkdir(parents=True)
    (cfg / "targets.yaml").write_text("x")
    (tmp_path / "current").symlink_to(tmp_path / "releases" / rid)
    assert cli.release_id_of(str(tmp_path / "current" / "config" / "targets.yaml")) == rid
    assert cli.release_id_of(str(TARGETS_PATH)) == "none"


def test_release_id_of_ignores_a_file_directly_in_a_releases_folder(tmp_path):
    cfg = tmp_path / "releases" / "targets.yaml"
    cfg.parent.mkdir()
    cfg.write_text("x")
    assert cli.release_id_of(str(cfg)) == "none"


def test_run_start_logs_the_release_id(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_probes", lambda packages: [OneProbe()])
    rid = "cd" * 32
    cfg_dir = tmp_path / "releases" / rid / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "targets.yaml").write_text(TARGETS_PATH.read_text())
    (tmp_path / "current").symlink_to(tmp_path / "releases" / rid)
    args = [
        "run",
        "--provider",
        "aws",
        "--config",
        str(tmp_path / "current/config/targets.yaml"),
        "--store",
        f"file://{tmp_path / 'store'}",
        "--once",
    ]
    assert cli.main(args) == 0
    line = next(ln for ln in capsys.readouterr().out.splitlines() if " run.start " in ln)
    assert f"release={rid}" in line
