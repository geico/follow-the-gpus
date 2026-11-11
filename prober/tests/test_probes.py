import sys
import textwrap
from datetime import UTC, datetime

import pytest

from ftg_prober.probes import (
    Clients,
    Probe,
    ProbeContext,
    is_probe_class,
    load_probes,
    packages_for,
)

PROBE_SOURCE = textwrap.dedent(
    """
    from ftg_prober.probes import Probe

    class {name}(Probe):
        probe_type = "{probe_type}"
        provider = "aws"
        default_cadence = "5m"

        def run(self, ctx):
            return []
    """
)


@pytest.fixture
def temp_package(tmp_path, monkeypatch):
    """A package `tmp_probes` with 1 probe module and 1 nested subpackage with a probe."""
    package = tmp_path / "tmp_probes"
    (package / "nested").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "nested" / "__init__.py").write_text("")
    (package / "score.py").write_text(
        PROBE_SOURCE.format(name="ScoreProbe", probe_type="aws_spot_score")
    )
    (package / "nested" / "price.py").write_text(
        PROBE_SOURCE.format(name="PriceProbe", probe_type="aws_spot_price")
    )
    (package / "helper.py").write_text(
        "from ftg_prober.probes import Probe\nclass Base(Probe):\n    pass\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    yield "tmp_probes"
    for name in list(sys.modules):
        if name == "tmp_probes" or name.startswith("tmp_probes."):
            del sys.modules[name]


def test_load_probes_finds_each_probe_subclass_in_a_temporary_package(temp_package):
    probes = load_probes((temp_package,))
    assert sorted(p.probe_type for p in probes) == ["aws_spot_price", "aws_spot_score"]
    assert all(isinstance(p, Probe) for p in probes)


def test_load_probes_skips_a_subclass_without_the_class_fields(temp_package):
    import tmp_probes.helper as helper

    assert not is_probe_class(helper.Base)
    assert not is_probe_class(Probe)


def test_load_probes_rejects_a_duplicate_probe_type(temp_package, tmp_path):
    (tmp_path / "tmp_probes" / "dup.py").write_text(
        PROBE_SOURCE.format(name="DupProbe", probe_type="aws_spot_score")
    )
    with pytest.raises(RuntimeError, match="defined twice"):
        load_probes((temp_package,))


def test_load_probes_fails_loudly_on_a_broken_subpackage(temp_package, tmp_path):
    broken = tmp_path / "tmp_probes" / "broken"
    broken.mkdir()
    (broken / "__init__.py").write_text("import no_such_module_for_ftg\n")
    with pytest.raises(ImportError):
        load_probes((temp_package,))


def test_load_probes_fails_loudly_on_a_broken_module(temp_package, tmp_path):
    (tmp_path / "tmp_probes" / "bad.py").write_text("import no_such_module_for_ftg\n")
    with pytest.raises(ModuleNotFoundError):
        load_probes((temp_package,))


def test_builtin_packages_load_and_hold_only_probes_of_their_provider():
    assert packages_for("aws") == ("ftg_prober.aws",)
    assert packages_for("azure") == ("ftg_prober.azure",)
    # Wave 2 adds the AWS probes (spec 0008) and the Azure probes (spec 0009, spec 0010).
    for provider in ("aws", "azure"):
        assert all(p.provider == provider for p in load_probes(packages_for(provider)))


def make_context(**overrides) -> ProbeContext:
    fields = dict(
        config={
            "regions": ["us-east-1"],
            "cadence": {"aws_spot_score": "1m", "az_provisioning": "3h"},
        },
        provider="aws",
        run_id="run-1",
        experiment="ftg",
        store=None,
    )
    fields.update(overrides)
    return ProbeContext(**fields)


def test_simulated_is_the_run_job_flag_or_falls_back_to_the_targets_own_key():
    ctx = make_context()
    assert ctx.simulated() is False
    assert ctx.simulated({"sku": "x"}) is False
    assert ctx.simulated({"sku": "x", "simulate": True}) is True
    ctx = make_context(simulate=True)
    assert ctx.simulated() is True
    assert ctx.simulated({"sku": "x", "simulate": False}) is True


def test_cadence_reads_the_config_with_the_az_provisioning_key():
    ctx = make_context()
    assert ctx.cadence("aws_spot_score") == "1m"
    assert ctx.cadence("az_vmss_flex") == "3h"
    assert ctx.cadence("aws_spot_price") is None


def test_now_and_timestamp_use_the_injected_clock():
    moment = datetime(2026, 10, 11, 1, 2, 3, 4000, tzinfo=UTC)
    ctx = make_context(now_fn=lambda: moment)
    assert ctx.now() is moment
    assert ctx.timestamp() == "2026-10-11T01:02:03.004Z"


def test_put_raw_writes_under_the_raw_key(memory_store):
    moment = datetime(2026, 10, 11, 1, 2, 3, tzinfo=UTC)
    ctx = make_context(store=memory_store, now_fn=lambda: moment)
    key = ctx.put_raw("aws_spot_advisor", b"{}")
    assert key.startswith(
        "experiment=ftg/provider=aws/probe_type=aws_spot_advisor/date=2026-10-11/20261011T010203Z_raw_"
    )
    assert key.endswith(".json")
    assert memory_store.objects[key] == b"{}"


def test_clients_use_the_factories_once_per_key():
    made = []

    def boto3_factory(service, region):
        made.append(("boto3", service, region))
        return object()

    def azure_factory(kind):
        made.append(("azure", kind))
        return object()

    clients = Clients(boto3_factory=boto3_factory, azure_factory=azure_factory)
    ec2 = clients.boto3("ec2", "us-east-1")
    assert clients.boto3("ec2", "us-east-1") is ec2
    assert clients.boto3("ec2", "us-west-2") is not ec2
    compute = clients.azure("compute")
    assert clients.azure("compute") is compute
    assert made == [
        ("boto3", "ec2", "us-east-1"),
        ("boto3", "ec2", "us-west-2"),
        ("azure", "compute"),
    ]


def test_probe_base_run_is_abstract():
    with pytest.raises(NotImplementedError):
        Probe().run(make_context())
