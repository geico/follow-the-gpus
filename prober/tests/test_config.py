import copy
import json
import re
from pathlib import Path

import pytest
import yaml

from ftg_prober.aws.cb_longest import MAX_STEPS as CB_LONGEST_MAX_STEPS
from ftg_prober.config import (
    KNOWN_CLOUD_KEYS,
    MAX_CB_CALLS_PER_REGION,
    MAX_CB_LONGEST_CALLS,
    RENAMED_KEYS,
    ConfigError,
    _validate_jobs,
    cadence_key,
    counts_for,
    experiment_fault,
    load_config,
    parse_cadence,
    validate,
)
from tests.conftest import TARGETS_PATH


@pytest.fixture
def data() -> dict:
    return yaml.safe_load(TARGETS_PATH.read_text(encoding="utf-8"))


# The 14 cells of 5 sizes x 9 Regions that the Region does not offer (ADR 0018).
RTX = "Standard_NC144ds_xl_RTXPRO6000BSE_v6"
NOT_OFFERED = (
    {(RTX, r) for r in ["eastus", "eastus2", "southcentralus", "westus3", "centralus"]}
    | {(RTX, r) for r in ["northcentralus", "westus", "westcentralus"]}
    | {
        (s, r)
        for s in ["Standard_NC40ads_H100_v5", "Standard_NC24ads_A100_v4"]
        for r in ["northcentralus", "westcentralus"]
    }
    | {(s, "westcentralus") for s in ["Standard_NV36ads_A10_v5", "Standard_NC4as_T4_v3"]}
)


def test_repo_targets_file_is_valid(repo_config):
    assert repo_config.experiment == "ftg-20261014-example"
    assert repo_config.aws["regions"] == ["us-east-1", "us-east-2", "us-west-2"]
    assert len(repo_config.aws["spot_score_requests"]) == 9
    assert len(repo_config.azure["regions"]) == 9
    assert len(repo_config.azure["spot_sizes"]) >= 5
    # The provisioning matrix of ADR 0018: 2 control entries plus the 31 cells of 5 sizes in 9
    # regions where the Region offers the size (45 cells minus 14), with 1 `multi`
    # entry for each size (ADR 0021).
    assert len(repo_config.azure["provisioning_probes"]) == 2 + 9 * 5 - len(NOT_OFFERED)
    modes = {e["placement_group_mode"] for e in repo_config.azure["provisioning_probes"]}
    assert modes == {"multi"}
    # 1 control entry plus the 6 Spot score GPU types in 3 Regions.
    assert len(repo_config.aws["provisioning_probes"]) == 1 + 3 * 6
    assert repo_config.aws["capacity_blocks"]["instance_types"] == [
        "p5.4xlarge",
        "p5.48xlarge",
        "p5en.48xlarge",
        "p6-b200.48xlarge",
        "p4d.24xlarge",
    ]


def test_repo_matrix_is_simulated_with_no_consent(repo_config):
    """The repo file is the reference matrix: it creates no capacity (ADR 0018, item 5)."""
    entries = repo_config.aws["provisioning_probes"] + repo_config.azure["provisioning_probes"]
    assert all(e.get("simulate") is True for e in entries)
    assert not any("allow_billable_provisioning" in e for e in entries)


def test_repo_matrix_covers_every_gpu_size_in_every_region(repo_config):
    """Every signal GPU size in every signal Region where the Region offers it (ADR 0018,
    items 1 and 2, and its amendment)."""
    az = {(e["sku"], e["region"]) for e in repo_config.azure["provisioning_probes"]}
    assert len(NOT_OFFERED) == 14 and not az & NOT_OFFERED
    for sku in repo_config.azure["spot_sizes"]:
        for region in repo_config.azure["regions"]:
            if (sku, region) not in NOT_OFFERED:
                assert (sku, region) in az
    gpu_types = [
        t
        for r in repo_config.aws["spot_score_requests"]
        if len(r["instance_types"]) == 1
        for t in r["instance_types"]
        if t[0] in "pg"
    ]
    assert len(gpu_types) == 6
    aws = {(e["instance_type"], e["region"]) for e in repo_config.aws["provisioning_probes"]}
    for instance_type in gpu_types:
        for region in repo_config.aws["regions"]:
            assert (instance_type, region) in aws


def test_experiment_must_match_the_name_pattern(data):
    assert _mutate(data, lambda d: d.update(experiment="Ftg-20261012-signals")) == [
        "experiment must match ^[a-z0-9]+(-[a-z0-9]+)*$ in at most 63 characters (lowercase "
        "words of letters and digits, joined by single dashes)"
    ]
    assert _mutate(data, lambda d: d.update(experiment="-ftg-20261012-signals")) != []
    assert _mutate(data, lambda d: d.update(experiment="ftg--20261012-signals")) != []
    assert _mutate(data, lambda d: d.update(experiment="ftg-20261012-" + "x" * 51)) != []
    assert _mutate(data, lambda d: d.update(experiment="ftg-20261012-" + "x" * 50)) == []


@pytest.mark.parametrize(
    "name", ["ftg-20261012-signals", "ftg-20261014-example", "lab-20240229-h100-spot"]
)
def test_experiment_dated_and_descriptive_passes(data, name):
    """Spec 0003 section 2: a word, a real `-YYYYMMDD-` date, then at least 1 word."""
    assert _mutate(data, lambda d: d.update(experiment=name)) == []


@pytest.mark.parametrize(
    "name",
    ["ftg", "ftg-signals", "ftg-20261399-x", "ftg-20261012", "20261012-signals", "ftg-20250229-x"],
)
def test_experiment_without_a_real_date_and_a_word_after_it_fails(data, name):
    assert _mutate(data, lambda d: d.update(experiment=name)) == [
        f"experiment `{name}` must be dated and descriptive: a word, a `-YYYYMMDD-` date, and "
        "at least 1 word after it, for example `ftg-20261012-signals`"
    ]


def test_experiment_is_required_unless_the_run_is_simulated(data, tmp_path):
    del data["experiment"]
    assert validate(data) == [
        "the config needs an `experiment` name, dated and descriptive, for example "
        "`ftg-20261012-signals`"
    ]
    path = tmp_path / "t.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="needs an `experiment` name"):
        load_config(path)
    config = load_config(path, force_simulate=True)
    assert config.experiment.startswith("ftg-") and config.experiment.endswith("-simulated")
    assert experiment_fault(config.experiment) is None


def test_section_of_a_missing_provider_raises(data, tmp_path):
    del data["azure"]
    path = tmp_path / "t.yaml"
    path.write_text(yaml.safe_dump(data))
    config = load_config(path)
    assert config.azure is None
    with pytest.raises(ConfigError, match="no `azure` section"):
        config.section("azure")


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("30s", 30), ("5m", 300), ("15m", 900), ("2h", 7200), ("24h", 86400), ("1d", 86400)],
)
def test_parse_cadence(text, seconds):
    assert parse_cadence(text) == seconds


@pytest.mark.parametrize("text", ["", "5", "5 m", "m5", "0m", "-5m", None, "1w"])
def test_parse_cadence_rejects_bad_values(text):
    with pytest.raises(ValueError):
        parse_cadence(text)


def test_cadence_key_maps_the_precheck_and_both_provisioning_probes_to_az_provisioning():
    assert cadence_key("az_precheck") == "az_provisioning"
    assert cadence_key("az_capacity_reservation") == "az_provisioning"
    assert cadence_key("az_vmss_flex") == "az_provisioning"
    assert cadence_key("aws_spot_score") == "aws_spot_score"


def test_validate_requires_a_mapping_with_a_provider():
    assert validate([]) == ["the top level must be a mapping"]
    faults = validate({"experiment": "ftg-20261012-signals"})
    assert faults == ["the config needs an `aws` section, an `azure` section, or both"]


def _mutate(data, fn):
    copy_ = copy.deepcopy(data)
    fn(copy_)
    return validate(copy_)


R = "azure.provisioning_probes.az_capacity_reservation"


def _az_entries(d):
    """The Azure provisioning entries as 1 flat list, in file order. The entries are the dicts of
    the mapping, so a change to one changes the config."""
    return [e for entries in d["azure"]["provisioning_probes"].values() for e in entries]


def _aws_requests(d):
    return d["aws"]["modules"]["aws_spot_score"]["spot_score_requests"]


def _aws_blocks(d):
    return d["aws"]["modules"]["aws_cb_offering"]


def test_repo_matrix_has_n_max_16_on_every_reservation_entry_and_on_no_scale_set_entry(
    repo_config,
):
    """ADR 0023: the reference file turns the upward search on for every `aws_odcr` entry and
    every `az_capacity_reservation` entry, and for no `az_vmss_flex` entry."""
    assert all(e["n_max"] == 16 for e in repo_config.aws["provisioning_probes"])
    for entry in repo_config.azure["provisioning_probes"]:
        assert entry.get("n_max") == (
            16 if entry["probe_type"] == "az_capacity_reservation" else None
        )


@pytest.mark.parametrize("bad", [2, 1, 17, 32, 3.0, "16", True, None, 0, -1])
def test_validate_refuses_a_bad_n_max_on_every_provisioning_entry_type(data, bad):
    """`n_max` is an integer, greater than `n` (2 here), at most `N_MAX_LIMIT` = 16 (ADR 0023)."""

    def set_bad(d):
        d["aws"]["provisioning_probes"][0]["n_max"] = bad
        d["azure"]["provisioning_probes"]["az_capacity_reservation"][0]["n_max"] = bad
        d["azure"]["provisioning_probes"]["az_vmss_flex"][0]["n_max"] = bad

    faults = _mutate(data, set_bad)
    assert len(faults) == 3
    assert any("aws.provisioning_probes[0].n_max must be" in f for f in faults)
    assert any(f"{R}[0].n_max must be" in f for f in faults)
    assert any("az_vmss_flex[0].n_max must be" in f for f in faults)


def test_validate_accepts_n_max_from_n_plus_1_to_16_also_on_simulated_entries(data):
    def set_ok(d):
        d["aws"]["provisioning_probes"][0].update(n_max=3, simulate=True)
        d["azure"]["provisioning_probes"]["az_capacity_reservation"][0].update(n_max=16)
        d["azure"]["provisioning_probes"]["az_vmss_flex"][0].update(n_max=16, simulate=True)

    assert _mutate(data, set_ok) == []


def test_validate_names_the_n_max_rule_in_its_message(data):
    faults = _mutate(data, lambda d: d["aws"]["provisioning_probes"][0].update(n_max=2))
    assert faults == ["aws.provisioning_probes[0].n_max must be greater than n"]
    faults = _mutate(data, lambda d: d["aws"]["provisioning_probes"][0].update(n_max=17))
    assert faults == ["aws.provisioning_probes[0].n_max must be an integer of at most 16"]


def test_validate_reports_each_aws_fault(data):
    def break_it(d):
        d["aws"]["jobs"][0]["regions"] = []
        del d["aws"]["quota"]
        _aws_requests(d).extend(
            {"name": f"x{i}", "instance_types": [f"m5.x{i}"], "label": "x"} for i in range(3)
        )
        _aws_blocks(d)["instance_count"] = 0
        d["aws"]["jobs"][0]["interval"] = "five"
        d["aws"]["run_mode"] = "test"  # ADR 0011: no longer a known key

    faults = _mutate(data, break_it)
    assert any("aws.jobs[0].regions" in f for f in faults)
    assert any("limit is 11" in f for f in faults)
    assert any("instance_count" in f for f in faults)
    assert any("aws.jobs[0].interval" in f for f in faults)
    assert any("aws.run_mode is not a known key" in f for f in faults)
    # Emptying job[0]'s regions alone also makes the jobs disagree on regions (ADR 0010
    # section 3, decided here: every job of 1 provider shares 1 regions list in this build).
    assert any("must use the same `regions` list" in f for f in faults)
    assert len(faults) == 6


def test_validate_rejects_12_spot_score_requests(data):
    def break_it(d):
        requests = _aws_requests(d)
        while len(requests) < 12:
            requests.append(
                {
                    "name": f"x{len(requests)}",
                    "instance_types": [f"m5.x{len(requests)}"],
                    "label": "x",
                }
            )

    faults = _mutate(data, break_it)
    assert faults == [
        "aws.jobs[aws_spot_score].spot_score_requests has 12 entries; the limit is 11"
    ]


def test_validate_rejects_two_single_spot_score_requests_of_one_type(data):
    requests = _aws_requests(data)
    single = next(r for r in requests if len(r["instance_types"]) == 1)
    t = single["instance_types"][0]

    def break_it(d):
        _aws_requests(d).append({"name": "dup", "instance_types": [t], "label": "x"})

    faults = _mutate(data, break_it)
    assert faults == [
        f"aws.jobs[aws_spot_score].spot_score_requests[{len(requests)}].instance_types: "
        f"single type {t!r} is used twice"
    ]


def test_validate_rejects_a_job_that_names_an_unknown_module_or_probe_type(data):
    index = len(data["aws"]["jobs"])
    faults = _mutate(
        data, lambda d: d["aws"]["jobs"].append({"module": "nope", "regions": ["us-east-1"]})
    )
    assert any(f"aws.jobs[{index}].module 'nope' is not a known module" in f for f in faults)


def test_validate_rejects_2_jobs_for_the_same_probe_type(data):
    def break_it(d):
        d["aws"]["jobs"].append({"module": "aws_spot_score", "regions": ["us-east-1"]})

    faults = _mutate(data, break_it)
    assert faults == ["aws.jobs: probe type 'aws_spot_score' has more than 1 job"]


def test_validate_rejects_an_unknown_type_in_modules(data):
    faults = _mutate(
        data, lambda d: d["aws"]["modules"]["aws_spot_score"].update(type="not_a_probe_type")
    )
    assert any(
        "aws.modules.aws_spot_score.type must be a known aws probe type" in f for f in faults
    )


def test_validate_rejects_a_module_setting_that_is_not_a_known_setting_of_its_type(data):
    """Critical review finding 1: a module may set only the settings of its probe type, so a
    key like `provisioning_probes` cannot ride along on a signal-only module and reach the flat
    section, past the allocation guard."""
    faults = _mutate(
        data,
        lambda d: d["aws"]["modules"]["aws_spot_score"].update(
            provisioning_probes=[
                {
                    "instance_type": "p5.4xlarge",
                    "region": "us-west-2",
                    "n": 1,
                    "run_mode": "cloud-gpu",
                }
            ]
        ),
    )
    assert faults == [
        "aws.modules.aws_spot_score.provisioning_probes is not a setting of aws_spot_score; "
        "the settings are spot_score_requests"
    ]


def test_an_aws_spot_price_job_needs_an_aws_spot_score_job_too(data):
    """Critical review finding 2: aws_spot_price and aws_spot_advisor read the aws_spot_score
    job's Spot score requests; a file with only one of them must be refused, not silently run
    with no Spot score requests."""
    mutated = copy.deepcopy(data)
    mutated["aws"]["jobs"] = [
        job for job in mutated["aws"]["jobs"] if job["module"] != "aws_spot_score"
    ]
    del mutated["aws"]["modules"]["aws_spot_score"]
    faults = validate(mutated)
    assert "aws.jobs: aws_spot_price needs a aws_spot_score job too, for its settings" in faults
    assert "aws.jobs: aws_spot_advisor needs a aws_spot_score job too, for its settings" in faults


def test_an_aws_cb_earliest_job_needs_an_aws_cb_offering_job_too(data):
    mutated = copy.deepcopy(data)
    mutated["aws"]["jobs"] = [
        job for job in mutated["aws"]["jobs"] if job["module"] != "aws_cb_offering"
    ]
    del mutated["aws"]["modules"]["aws_cb_offering"]
    faults = validate(mutated)
    assert "aws.jobs: aws_cb_earliest needs a aws_cb_offering job too, for its settings" in faults
    assert "aws.jobs: aws_cb_longest needs a aws_cb_offering job too, for its settings" in faults


def test_jobs_of_1_provider_must_share_the_same_regions(data):
    """Critical review finding 3: the flat, legacy-shaped section has 1 shared `regions` list
    per cloud; jobs that disagree are refused instead of silently merged into a union that no
    single job asked for."""
    faults = _mutate(data, lambda d: d["aws"]["jobs"][0].update(regions=["us-west-2"]))
    assert "aws.jobs: every job of aws must use the same `regions` list in this build" in "\n".join(
        faults
    )


def test_az_spot_score_and_az_spot_must_share_spot_sizes(data):
    faults = _mutate(
        data,
        lambda d: d["azure"]["modules"]["az_spot_score"].update(
            spot_sizes=["Standard_NC40ads_H100_v5"]
        ),
    )
    assert any("az_spot_score and az_spot must have the same spot_sizes" in f for f in faults)


def test_adr_0010_section_3_merge_example():
    """The ADR 0010 section 3 worked example, word for word, on 2 of the 4 real
    `aws_cb_offering` settings: the code default `instance_types` is `[p5.4xlarge,
    p4d.24xlarge]` and `duration_hours` is `[24]`; a file override of `{duration_hours: [24,
    48]}` gives `instance_types: [p5.4xlarge, p4d.24xlarge]` (kept) and `duration_hours: [24,
    48]` (replaced as a whole list, not appended to the default `[24]`). Asserts the whole
    merged settings mapping, including the 2 keys the ADR example does not mention
    (`instance_count`, `start_window_days`), so a mismatch between the code default and this
    test cannot hide behind a partial assertion."""
    data = {
        "experiment": "ftg-20261012-signals",
        "aws": {
            "modules": {"aws_cb_offering": {"type": "aws_cb_offering", "duration_hours": [24, 48]}},
            "jobs": [{"module": "aws_cb_offering", "regions": ["us-east-1"]}],
        },
    }
    assert validate(data) == []
    resolved, _faults = _validate_jobs(data["aws"], "aws")
    assert resolved["aws_cb_offering"]["settings"] == {
        "instance_types": ["p5.4xlarge", "p4d.24xlarge"],
        "instance_count": 1,
        "duration_hours": [24, 48],
        "start_window_days": 14,
    }


def test_validate_rejects_an_unknown_key_in_a_cloud_section(data):
    """A cloud section key outside `KNOWN_CLOUD_KEYS` must be refused, not silently ignored
    (review finding): a key of an earlier schema revision that moved under the jobs model
    must not go dark with no error."""
    faults = _mutate(data, lambda d: d["azure"].update(mystery_key="x"))
    assert faults == [
        "azure.mystery_key is not a known key. See spec 0003 section 3 for the file shape."
    ]


def test_validate_rejects_an_unknown_key_in_a_job_entry(data):
    """Adversarial review finding: an unknown key in a `*.jobs[i]` entry (for example a
    typo'd `intervall: 1m`) was accepted and silently dropped. Must be a fault."""
    faults = _mutate(data, lambda d: d["aws"]["jobs"][0].update(intervall="1m"))
    assert any("aws.jobs[0].intervall is not a known key" in f for f in faults)


def test_validate_accepts_simulate_and_allow_billable_provisioning_on_a_job_entry(data):
    """ADR 0011 sections 2 and 3: a `*.jobs[i]` entry may set its own `simulate` and
    `allow_billable_provisioning` keys (JOB_ENTRY_KEYS)."""
    faults = _mutate(
        data, lambda d: d["aws"]["jobs"][0].update(simulate=True, allow_billable_provisioning=True)
    )
    assert faults == []


@pytest.mark.parametrize("key", ["simulate", "allow_billable_provisioning"])
@pytest.mark.parametrize("bad", ["true", 1, None])
def test_validate_rejects_a_non_boolean_simulate_or_allow_billable_provisioning_on_a_job_entry(
    data, key, bad
):
    faults = _mutate(data, lambda d: d["aws"]["jobs"][0].update({key: bad}))
    assert faults == [f"aws.jobs[0].{key} must be true or false"]


def test_validate_rejects_an_unknown_key_in_a_quota_size_entry(data):
    faults = _mutate(data, lambda d: d["aws"]["quota"]["sizes"][0].update(mystery=1))
    assert any("aws.quota.sizes[0].mystery is not a known key" in f for f in faults)


@pytest.mark.parametrize(
    "path",
    [("aws", "jobs", 0), ("aws", "provisioning_probes", 0), ("azure", "provisioning_probes", 0)],
)
def test_validate_refuses_the_old_allow_allocation_key_and_names_the_new_one(data, path):
    """ADR 0016: the old key is an unknown key; the fault names `allow_billable_provisioning`."""
    cloud, list_key, i = path
    if cloud == "azure":  # the entries of the mapping, in file order (ADR 0022)
        faults = _mutate(data, lambda d: _az_entries(d)[i].update(allow_allocation=True))
        list_key = "provisioning_probes.az_capacity_reservation"
    else:
        faults = _mutate(data, lambda d: d[cloud][list_key][i].update(allow_allocation=True))
    where = f"{cloud}.{list_key}[{i}].allow_allocation is not a known key"
    assert any(where in f and "`allow_billable_provisioning`" in f for f in faults)


@pytest.mark.parametrize("cloud", ["aws", "azure"])
def test_validate_refuses_the_old_allocation_probes_section_key_and_names_the_new_one(data, cloud):
    """The old section key is an unknown key; the fault names `provisioning_probes`."""
    faults = _mutate(data, lambda d: d[cloud].update(allocation_probes=[]))
    assert any(
        f"{cloud}.allocation_probes is not a known key" in f and "`provisioning_probes`" in f
        for f in faults
    )


@pytest.mark.parametrize("cloud", ["aws", "azure"])
def test_validate_refuses_the_old_az_allocation_cadence_key_and_names_the_new_one(data, cloud):
    """A silently ignored old cadence key would change the pre-check cadence."""
    faults = _mutate(data, lambda d: d[cloud].setdefault("cadence", {}).update(az_allocation="2h"))
    assert any(
        f"{cloud}.cadence.az_allocation is not a known key" in f and "`az_provisioning`" in f
        for f in faults
    )


def test_validate_rejects_an_unknown_key_in_an_aws_allocation_probe_entry(data):
    faults = _mutate(data, lambda d: d["aws"]["provisioning_probes"][0].update(mystery=1))
    assert any("aws.provisioning_probes[0].mystery is not a known key" in f for f in faults)


def test_validate_rejects_an_unknown_key_in_an_azure_allocation_probe_entry(data):
    faults = _mutate(data, lambda d: _az_entries(d)[0].update(mystery=1))
    assert any(
        "azure.provisioning_probes.az_capacity_reservation[0].mystery is not a known key" in f
        for f in faults
    )


def test_validate_accepts_the_optional_sim_script_allocation_probe_key(data):
    """`sim_script` is a documented optional key of an `azure.provisioning_probes` entry (spec
    0010 section 10); the code reads it (azure/sim.py)."""
    assert _mutate(data, lambda d: _az_entries(d)[0].update(sim_script="fail")) == []


def test_validate_refuses_the_removed_route_key_and_names_the_probe_type(data):
    faults = _mutate(data, lambda d: _az_entries(d)[0].update(route="vmss"))
    assert faults == [
        "azure.provisioning_probes.az_capacity_reservation[0].route is removed (ADR 0022); the"
        " probe type that lists the entry decides what the probe does"
    ]


def test_validate_refuses_the_old_list_shape_of_azure_provisioning_probes(data):
    def old_shape(d):
        d["azure"]["provisioning_probes"] = _az_entries(d)

    (fault,) = _mutate(data, old_shape)
    assert fault.startswith("azure.provisioning_probes is a list. It is now a mapping from probe")
    assert "az_capacity_reservation, az_vmss_flex" in fault


def test_validate_refuses_an_unknown_probe_type_key(data):
    faults = _mutate(
        data, lambda d: d["azure"]["provisioning_probes"].update(az_vm=[_entry(simulate=True)])
    )
    assert faults == [
        "azure.provisioning_probes.az_vm is not a known probe type."
        " Use az_capacity_reservation or az_vmss_flex (ADR 0022)"
    ]


def test_validate_refuses_a_probe_type_that_is_not_a_list(data):
    faults = _mutate(
        data, lambda d: d["azure"]["provisioning_probes"].update(az_vmss_flex=_entry())
    )
    assert faults == ["azure.provisioning_probes.az_vmss_flex must be a list of entries"]


def test_validate_refuses_2_vmss_entries_for_1_size_and_region_in_any_case(data):
    def duplicate(d):
        first = {"az_vmss_flex": [_entry(simulate=True, sku="Standard_NC40ads_H100_v5")]}
        again = _entry(simulate=True, sku="standard_nc40ads_h100_v5", region="EastUS")
        again["zone"] = 2  # a zone does not make a second entry
        first["az_vmss_flex"].append({**again, "region": "EASTUS"})
        first["az_vmss_flex"][0]["region"] = "eastus"
        d["azure"]["provisioning_probes"] = first

    (fault,) = _mutate(data, duplicate)
    assert fault.startswith(
        "azure.provisioning_probes.az_vmss_flex[1] repeats the size and Region of"
        " azure.provisioning_probes.az_vmss_flex[0]."
    )


def test_the_same_size_and_region_under_both_probe_types_is_allowed(data):
    def both(d):
        d["azure"]["provisioning_probes"] = {
            "az_capacity_reservation": [_entry(simulate=True)],
            "az_vmss_flex": [_entry(simulate=True)],
        }

    assert _mutate(data, both) == []


def test_the_loader_flattens_the_mapping_and_adds_the_probe_type(tmp_path):
    path = tmp_path / "targets.yaml"
    path.write_text(
        "experiment: ftg-20261014-example\n"
        "azure:\n"
        "  provisioning_probes:\n"
        "    az_vmss_flex:\n"
        "      - {sku: Standard_NC40ads_H100_v5, region: eastus2, zone: null, n: 2,"
        " placement_group_mode: multi, simulate: true}\n"
        "    az_capacity_reservation:\n"
        "      - {sku: Standard_NC24ads_A100_v4, region: eastus2, zone: null, n: 2,"
        " placement_group_mode: multi, simulate: true}\n"
    )
    entries = load_config(path).azure["provisioning_probes"]
    assert [(e["probe_type"], e["sku"]) for e in entries] == [
        ("az_vmss_flex", "Standard_NC40ads_H100_v5"),
        ("az_capacity_reservation", "Standard_NC24ads_A100_v4"),
    ]


def test_validate_rejects_an_unknown_key_in_a_spot_score_request(data):
    faults = _mutate(data, lambda d: _aws_requests(d)[0].update(mystery=1))
    assert any(
        "aws.jobs[aws_spot_score].spot_score_requests[0].mystery is not a known key" in f
        for f in faults
    )


def test_a_null_jobs_key_is_a_fault_not_a_crash(data):
    faults = _mutate(data, lambda d: d["aws"].update(jobs=None))
    assert "aws.jobs must be a list" in faults


def test_a_job_naming_a_module_with_an_invalid_type_gives_1_fault_not_2():
    """Nit: a job whose `module` is a `*.modules` entry with an invalid `type` must not fall
    through to a built-in probe type of the same name (it is not shadowing one on purpose;
    its own `type` is simply wrong). Only the `*.modules.<name>.type` fault should appear,
    not also a 2nd, misleading fault about the job itself."""
    data = {
        "experiment": "ftg-20261012-signals",
        "azure": {
            "modules": {"az_spot_score": {"type": "not_a_probe_type"}},
            "jobs": [{"module": "az_spot_score", "regions": ["eastus"]}],
        },
    }
    assert validate(data) == ["azure.modules.az_spot_score.type must be a known azure probe type"]


def test_validate_rejects_a_flat_regions_key(data):
    faults = _mutate(data, lambda d: d["aws"].update(regions=["us-east-1"]))
    assert any("aws.regions is not a known key" in f for f in faults)


def test_validate_rejects_a_flat_cadence_key_for_a_job_eligible_type_with_no_job(data):
    """A flat `cadence` entry for a job-eligible type must be refused even when that type has
    no job at all (not only when it conflicts with an existing job's `interval`)."""
    mutated = copy.deepcopy(data)
    del mutated["aws"]["jobs"][0]
    del mutated["aws"]["modules"]["aws_spot_score"]
    mutated["aws"]["cadence"]["aws_spot_score"] = "5m"
    faults = validate(mutated)
    assert any(
        "aws.cadence.aws_spot_score is set, but aws_spot_score is job-eligible" in f for f in faults
    )


def test_validate_rejects_a_flat_spot_price_max_age_days_key(data):
    faults = _mutate(data, lambda d: d["azure"].update(spot_price_max_age_days=30))
    assert any("azure.spot_price_max_age_days is not a known key" in f for f in faults)


def test_a_named_module_wins_over_a_same_named_built_in_probe_type(tmp_path):
    """ADR 0010 section 12, decided in spec 0003 section 3: `modules:` wins over a built-in
    probe type of the same name. The job below names the built-in type `az_spot_score`
    directly (no `modules:` entry of that name exists yet); adding one must change what the
    job resolves to, without touching the job itself."""
    from ftg_prober.config import load_config

    shadowed = {
        "experiment": "ftg-20261012-signals",
        "azure": {
            "jobs": [{"module": "az_spot_score", "regions": ["eastus"]}],
            "modules": {
                "az_spot_score": {
                    "type": "az_spot_score",
                    "spot_sizes": ["Standard_NC40ads_H100_v5"],
                }
            },
        },
    }
    assert validate(shadowed) == []
    path = tmp_path / "t.yaml"
    path.write_text(yaml.safe_dump(shadowed))
    config = load_config(path)
    # If the built-in type (empty `spot_sizes` default) had won instead of the module, this
    # would be `[]`, and validate() above would already have failed.
    assert config.azure["spot_sizes"] == ["Standard_NC40ads_H100_v5"]


def test_a_null_or_empty_mapping_override_is_a_literal_value_not_a_clear(data):
    """ADR 0010 section 12, decided in spec 0003 section 3: `null`/`{}` is an ordinary override
    value, not special "clear this key" syntax."""
    faults = _mutate(
        data, lambda d: d["aws"]["modules"]["aws_spot_score"].update(spot_score_requests=None)
    )
    assert faults == ["aws.jobs[aws_spot_score].spot_score_requests must be a non-empty list"]


def test_a_job_eligible_cadence_key_conflicts_with_its_jobs_interval(data):
    faults = _mutate(data, lambda d: d["aws"]["cadence"].update(aws_spot_score="5m"))
    assert faults == [
        "aws.cadence.aws_spot_score is set, but aws.jobs[aws_spot_score] already has its own "
        "`interval`; remove 1 of the 2"
    ]


def test_validate_rejects_the_removed_single_placement_group_mode(data):
    def break_it(d):
        _az_entries(d)[0]["placement_group_mode"] = "single"

    faults = _mutate(data, break_it)
    assert faults == [f"{R}[0].placement_group_mode `single` is removed (ADR 0021); use `multi`"]


def test_validate_reports_each_azure_fault(data):
    def break_it(d):
        d["azure"]["modules"]["az_spot_score"]["spot_sizes"] = "Standard_B2s"
        _az_entries(d)[0]["placement_group_mode"] = "both"
        _az_entries(d)[1]["simulate"] = "real"
        _az_entries(d)[1]["allow_billable_provisioning"] = True  # avoid an unrelated fault
        _az_entries(d)[2]["n"] = "2"
        d["azure"]["cadence"]["az_provisioning"] = "2 hours"

    faults = _mutate(data, break_it)
    assert any("azure.jobs[az_spot_score].spot_sizes" in f for f in faults)
    assert any("az_capacity_reservation[0].placement_group_mode" in f for f in faults)
    assert any("az_capacity_reservation[1].simulate" in f for f in faults)
    assert any("az_capacity_reservation[2].n" in f for f in faults)
    assert any("azure.cadence.az_provisioning" in f for f in faults)
    # az_spot_score's spot_sizes no longer matches az_spot's (1 shared list, decided here).
    assert any("must have the same spot_sizes" in f for f in faults)
    assert len(faults) == 6


AZURE_OPTIONAL_INT_KEYS = [
    "probe_timeout_seconds",
    "poll_interval_seconds",
    "teardown_timeout_seconds",
]


def test_validate_accepts_a_config_without_the_optional_azure_int_keys(data):
    def drop(d):
        for key in AZURE_OPTIONAL_INT_KEYS:
            d["azure"].pop(key, None)

    assert _mutate(data, drop) == []


@pytest.mark.parametrize("key", AZURE_OPTIONAL_INT_KEYS)
def test_validate_accepts_a_good_optional_azure_int_key(data, key):
    assert _mutate(data, lambda d: d["azure"].update({key: 30})) == []


@pytest.mark.parametrize("bad", [0, -1, "30", 1.5, True, None])
@pytest.mark.parametrize("key", AZURE_OPTIONAL_INT_KEYS)
def test_validate_reports_a_bad_optional_azure_int_key(data, key, bad):
    faults = _mutate(data, lambda d: d["azure"].update({key: bad}))
    assert faults == [f"azure.{key} must be an integer of 1 or more"]


@pytest.mark.parametrize("bad", [0, -1, "30", 1.5, True, None])
def test_validate_reports_a_bad_az_spot_price_max_age_days(data, bad):
    faults = _mutate(
        data, lambda d: d["azure"]["modules"]["az_spot"].update(spot_price_max_age_days=bad)
    )
    assert faults == ["azure.jobs[az_spot].spot_price_max_age_days must be an integer of 1 or more"]


def test_validate_rejects_other_keys_in_a_cloud_section(data):
    """A cloud section no longer allows an arbitrary key (review finding: silently ignoring
    an unknown key let an old-shape key go dark with no error)."""

    def add_keys(d):
        d["aws"]["note"] = "x"
        d["azure"]["note"] = "x"

    faults = _mutate(data, add_keys)
    assert sorted(faults) == [
        "aws.note is not a known key. See spec 0003 section 3 for the file shape.",
        "azure.note is not a known key. See spec 0003 section 3 for the file shape.",
    ]


def test_validate_rejects_unknown_top_level_keys_and_duplicate_request_names(data):
    def break_it(d):
        d["gcp"] = {}
        _aws_requests(d)[1]["name"] = _aws_requests(d)[0]["name"]

    faults = _mutate(data, break_it)
    assert any("unknown top-level key `gcp`" in f for f in faults)
    assert any("used twice" in f for f in faults)


def test_load_config_error_lists_the_path_and_each_fault(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("aws:\n  jobs: [{module: aws_spot_score, regions: [us-east-1]}]\n")
    with pytest.raises(ConfigError) as info:
        load_config(path)
    message = str(info.value)
    assert message.count(str(path)) >= 1
    assert "aws.jobs[aws_spot_score].spot_score_requests" in message


def test_load_config_rejects_a_missing_file_and_bad_yaml(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "none.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("aws: [unclosed\n")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(bad)


def test_the_capacity_block_limit_counts_the_probes_own_cap():
    # 1 constant for the cap: the config check and `aws_cb_longest.MAX_STEPS` must agree, or the
    # limit protects against a number the probe no longer uses.
    assert MAX_CB_LONGEST_CALLS == CB_LONGEST_MAX_STEPS


def test_validate_limits_the_capacity_block_calls_per_region(data):
    # The worst case of `aws_cb_longest` (19 calls) counts for each (type, count) pair (spec 0008,
    # 3.3 rule 1).
    def plain(d, types):
        _aws_blocks(d).pop("instance_counts", None)
        _aws_blocks(d)["instance_types"] = [f"p{i}.48xlarge" for i in range(types)]
        _aws_blocks(d)["duration_hours"] = [24, 48, 72]
        _aws_blocks(d)["earliest_duration_hours"] = [24, 48, 72]

    assert _mutate(data, lambda d: plain(d, 8)) == []  # 8 x (3 + 3 + 19) = 200
    assert _mutate(data, lambda d: plain(d, 9)) == [
        "aws.jobs[aws_cb_offering] makes 225 worst-case calls per Region "
        "((instance_types, instance count) pairs x (duration_hours + earliest_duration_hours "
        "+ 19)); the limit is 200"
    ]


def test_a_capacity_block_plan_of_exactly_201_calls_is_rejected(data):
    # 3 pairs x (24 + 24 + 19) = 201: 1 over the limit of 200, so the check is `>` 200.
    def plan(d):
        _aws_blocks(d).pop("instance_counts", None)
        _aws_blocks(d)["instance_types"] = ["p1.48xlarge", "p2.48xlarge", "p3.48xlarge"]
        _aws_blocks(d)["duration_hours"] = list(range(1, 25))
        _aws_blocks(d)["earliest_duration_hours"] = list(range(1, 25))

    [fault] = _mutate(data, plan)
    assert "makes 201 worst-case calls per Region" in fault


def test_the_earliest_calls_count_toward_the_capacity_block_limit(data):
    def with_earliest(d):
        _aws_blocks(d).pop("instance_counts", None)
        _aws_blocks(d)["instance_types"] = [f"p{i}.48xlarge" for i in range(10)]
        _aws_blocks(d)["duration_hours"] = [24]
        del _aws_blocks(d)["earliest_duration_hours"]  # the default is duration_hours

    def earliest_off(d):
        with_earliest(d)
        _aws_blocks(d)["earliest_duration_hours"] = []

    faults = _mutate(data, with_earliest)
    assert len(faults) == 1
    assert "makes 210 worst-case calls per Region" in faults[0]
    assert _mutate(data, earliest_off) == []


def test_the_repo_config_is_the_h100_grid_inside_the_limit(data):
    blocks = _aws_blocks(data)
    assert blocks["duration_hours"] == [24, 72, 120, 168]
    assert blocks["instance_counts"] == {"p5.4xlarge": [1, 4, 8]}
    pairs = sum(len(counts_for(blocks, t)) for t in blocks["instance_types"])
    calls = pairs * (len(blocks["duration_hours"]) + len(blocks["earliest_duration_hours"]) + 19)
    assert calls == 175 <= MAX_CB_CALLS_PER_REGION


def test_the_limit_counts_type_count_pairs(data):
    def one_type_many_counts(d):
        _aws_blocks(d)["instance_types"] = ["p5.4xlarge"]
        _aws_blocks(d)["instance_counts"] = {"p5.4xlarge": list(range(1, 9))}
        _aws_blocks(d)["duration_hours"] = [24, 72, 120]
        _aws_blocks(d)["earliest_duration_hours"] = [24]

    # 8 pairs x (3 + 1 + 19) = 184: fits. With 9 counts: 207, over.
    assert _mutate(data, one_type_many_counts) == []

    def nine(d):
        one_type_many_counts(d)
        _aws_blocks(d)["instance_counts"] = {"p5.4xlarge": list(range(1, 10))}

    [fault] = _mutate(data, nine)
    assert "makes 207 worst-case calls per Region" in fault


def test_validate_checks_instance_counts(data):
    def set_counts(counts):
        def mutate(d):
            _aws_blocks(d)["instance_counts"] = counts

        return mutate

    where = "aws.jobs[aws_cb_offering].instance_counts"
    assert _mutate(data, set_counts({"p5.4xlarge": [1, 4]})) == []
    assert _mutate(data, set_counts({"p9.48xlarge": [1]})) == [
        f"{where}[p9.48xlarge]: 'p9.48xlarge' is not in instance_types"
    ]
    for bad in ([], [0], [-1, 2], [True], ["4"], "4"):
        [fault] = _mutate(data, set_counts({"p5.4xlarge": bad}))
        assert fault == f"{where}[p5.4xlarge] must be a non-empty list of integers of 1 or more"
    assert _mutate(data, set_counts({"p5.4xlarge": [1, 4, 4]})) == [
        f"{where}[p5.4xlarge] lists a count twice"
    ]
    assert _mutate(data, set_counts([1, 4])) == [
        f"{where} must be a mapping of instance type to a list"
    ]


def test_counts_for_falls_back_to_instance_count():
    blocks = {"instance_count": 2, "instance_counts": {"a": [1, 4]}}
    assert counts_for(blocks, "a") == [1, 4]
    assert counts_for(blocks, "b") == [2]
    assert counts_for({"instance_count": 1}, "a") == [1]


def test_validate_checks_the_earliest_durations(data):
    def bad(d):
        _aws_blocks(d)["instance_types"] = ["p5.48xlarge"]
        del _aws_blocks(d)["instance_counts"]
        _aws_blocks(d)["earliest_duration_hours"] = [0, "x"]

    assert _mutate(data, bad) == [
        "aws.jobs[aws_cb_offering].earliest_duration_hours must be a list of integers > 0"
    ]


# One validator checks both quota blocks (spec 0003 section 3), so each test runs for both clouds.
CLOUDS = ["aws", "azure"]
# A region of the cloud that is a valid name, and a name that is not in <cloud>'s job regions.
VALID_REGION = {"aws": "us-east-2", "azure": "eastus2"}
# A region name that is valid but not in a job's regions.
UNKNOWN_REGION = {"aws": "eu-west-1", "azure": "mars"}
BAD_NAMES = {
    "aws": ["US-East-1", "us_east_1", "useast1", "us-east-1\n", "East US"],
    "azure": ["East US", "east us", "EastUS", "east-us", "eastus\n"],
}
BAD_NAME_HINT = {
    "aws": "a Region code such as us-east-1",
    "azure": "lowercase letters and digits only",
}


def _job_regions(d, cloud):
    """Every job's `regions` list of a cloud (they are identical in the repo file)."""
    return d[cloud]["jobs"][0]["regions"]


def test_repo_targets_file_has_a_quota_section_for_each_cloud(repo_config):
    for cloud in CLOUDS:
        section = getattr(repo_config, cloud)
        assert set(section["quota"]["regions"]) <= set(section["regions"])
    azure = repo_config.azure
    assert [s["size"] for s in azure["quota"]["sizes"]][:4] == azure["spot_sizes"][0:4]
    aws = repo_config.aws
    request_types = [p["instance_types"][0] for p in aws["spot_score_requests"][:6]]
    assert [s["size"] for s in aws["quota"]["sizes"]] == request_types


@pytest.mark.parametrize("cloud", CLOUDS)
def test_validate_accepts_a_config_without_a_quota_section(data, cloud):
    assert _mutate(data, lambda d: d[cloud].pop("quota")) == []


@pytest.mark.parametrize("cloud", CLOUDS)
def test_validate_reports_each_quota_fault(data, cloud):
    def break_it(d):
        q = d[cloud]["quota"]
        sizes = q["sizes"]
        q["regions"].append(UNKNOWN_REGION[cloud])
        sizes[0]["count"] = 0
        sizes[1]["size"] = ""
        sizes[3]["size"] = sizes[2]["size"]
        sizes[4]["regions"] = [VALID_REGION[cloud]]
        q["regions"].remove(VALID_REGION[cloud])

    faults = _mutate(data, break_it)
    size3 = data[cloud]["quota"]["sizes"][2]["size"]
    assert faults == [
        f"{cloud}.quota.regions: {UNKNOWN_REGION[cloud]!r} is not in the regions of any "
        f"{cloud} job",
        f"{cloud}.quota.sizes[0].count must be an integer of 1 or more",
        f"{cloud}.quota.sizes[1].size must be a non-empty string",
        f"{cloud}.quota.sizes[3].size {size3!r} is used twice",
        f"{cloud}.quota.sizes[4].regions: {VALID_REGION[cloud]!r} is not in {cloud}.quota.regions",
    ]


@pytest.mark.parametrize("cloud", CLOUDS)
@pytest.mark.parametrize(
    ("quota", "fault"),
    [
        ([], "quota must be a mapping"),
        ({"regions": [], "sizes": [{"size": "g5.xlarge", "count": 1}]}, "quota.regions must be"),
        ({"regions": ["R"], "sizes": []}, "quota.sizes must be a non-empty list"),
        ({"regions": ["R"]}, "quota.sizes must be a non-empty list"),
        ({"regions": ["R"], "sizes": ["x"]}, "quota.sizes[0] must be a mapping"),
        ({"regions": ["R"], "sizes": [{"size": ["x"], "count": 1}]}, "quota.sizes[0].size"),
        (
            {"regions": ["R"], "sizes": [{"size": "g5.xlarge", "count": True}]},
            "quota.sizes[0].count",
        ),
        (
            {"regions": ["R"], "sizes": [{"size": "g5.xlarge", "count": 1, "regions": []}]},
            "quota.sizes[0].regions must be",
        ),
    ],
)
def test_validate_reports_a_bad_quota_shape(data, cloud, quota, fault):
    quota = json.loads(json.dumps(quota).replace('"R"', json.dumps(VALID_REGION[cloud])))
    faults = _mutate(data, lambda d: d[cloud].update({"quota": quota}))
    assert len(faults) == 1 and faults[0].startswith(f"{cloud}.{fault}")


@pytest.mark.parametrize("cloud", CLOUDS)
def test_validate_reports_a_duplicate_in_the_quota_section(data, cloud):
    region = VALID_REGION[cloud]
    upper = region.upper()

    def mutate(d):
        q = d[cloud]["quota"]
        _job_regions(d, cloud).append(upper)
        q["regions"].append(upper)
        q["sizes"][1]["size"] = q["sizes"][0]["size"].upper()
        q["sizes"][2]["regions"] = [region, upper]

    faults = _mutate(data, mutate)
    assert f"{cloud}.quota.regions: {upper!r} is used twice" in faults
    assert f"{cloud}.quota.sizes[2].regions: {upper!r} is used twice" in faults
    first = data[cloud]["quota"]["sizes"][0]["size"].upper()
    assert f"{cloud}.quota.sizes[1].size {first!r} is used twice" in faults


@pytest.mark.parametrize("cloud", CLOUDS)
def test_validate_reports_a_quota_region_that_is_not_canonical(data, cloud):
    for name in BAD_NAMES[cloud]:

        def mutate(d, name=name):
            _job_regions(d, cloud).append(name)
            d[cloud]["quota"]["regions"].append(name)
            d[cloud]["quota"]["sizes"][4]["regions"] = [VALID_REGION[cloud], name]

        faults = _mutate(data, mutate)
        hint = BAD_NAME_HINT[cloud]
        assert f"{cloud}.quota.regions: {name!r} must be {hint}" in faults
        assert f"{cloud}.quota.sizes[4].regions: {name!r} must be {hint}" in faults


@pytest.mark.parametrize("name", ["g5", "G5.xlarge", "g5.", ".xlarge", "g5 .xlarge", "g5.xlarge\n"])
def test_validate_reports_an_aws_size_that_is_not_an_instance_type(data, name):
    faults = _mutate(data, lambda d: d["aws"]["quota"]["sizes"][0].update({"size": name}))
    assert f"aws.quota.sizes[0].size {name!r} must be an EC2 instance type" in faults


ALLOCATION_GUARD_FIXTURE = Path(__file__).parent / "fixtures" / "provisioning_guard.yaml"


def test_shared_allocation_guard_fixture_fails_without_allow_billable_provisioning():
    """ADR 0011 section 3: this fixture, unmodified, must give exactly 1 fault."""
    raw = yaml.safe_load(ALLOCATION_GUARD_FIXTURE.read_text(encoding="utf-8"))
    faults = validate(raw)
    assert len(faults) == 1
    assert "azure.provisioning_probes.az_capacity_reservation[0]" in faults[0]
    assert "allow_billable_provisioning: true" in faults[0]


def test_shared_allocation_guard_fixture_passes_with_allow_billable_provisioning_true():
    """The same fixture, with only `allow_billable_provisioning: true` added, must give 0 faults."""
    raw = yaml.safe_load(ALLOCATION_GUARD_FIXTURE.read_text(encoding="utf-8"))
    _az_entries(raw)[0]["allow_billable_provisioning"] = True
    assert validate(raw) == []


_FIXTURES = Path(__file__).parent / "fixtures"
ALLOCATION_GUARD_ALLOWED_FIXTURE = _FIXTURES / "provisioning_guard_allowed.yaml"
ALLOCATION_GUARD_AWS_FIXTURE = _FIXTURES / "provisioning_guard_aws.yaml"
ALLOCATION_GUARD_AWS_ALLOWED_FIXTURE = _FIXTURES / "provisioning_guard_aws_allowed.yaml"
NO_ALLOCATION_JOB_FIXTURE = _FIXTURES / "no_provisioning_job.yaml"
SIMULATED_ENTRY_FIXTURE = _FIXTURES / "simulated_entry.yaml"
SIMULATED_ENTRY_AWS_FIXTURE = _FIXTURES / "simulated_entry_aws.yaml"


def test_shared_allocation_guard_fixture_passes_with_simulate_true():
    """`simulated_entry.yaml`, a real file (a companion of ALLOCATION_GUARD_FIXTURE, not an
    in-memory patch of it), must give 0 faults (ADR 0011 section 3, rule 3: a simulated entry
    needs no `allow_billable_provisioning`)."""
    raw = yaml.safe_load(SIMULATED_ENTRY_FIXTURE.read_text(encoding="utf-8"))
    assert validate(raw) == []


def test_shared_allocation_guard_aws_fixture_passes_with_simulate_true():
    """`simulated_entry_aws.yaml`, AWS's own copy of the fixture above, must give 0 faults."""
    raw = yaml.safe_load(SIMULATED_ENTRY_AWS_FIXTURE.read_text(encoding="utf-8"))
    assert validate(raw) == []


def test_shared_no_provisioning_job_fixture_passes():
    """`no_provisioning_job.yaml` (no `azure.provisioning_probes` key at all) must give 0 faults
    (ADR 0010 section 7: no allocation job, no consent to check)."""
    raw = yaml.safe_load(NO_ALLOCATION_JOB_FIXTURE.read_text(encoding="utf-8"))
    assert validate(raw) == []


def test_shared_allocation_guard_allowed_fixture_passes():
    """The on-disk companion of ALLOCATION_GUARD_FIXTURE, with
    `allow_billable_provisioning: true` baked in: 0 faults."""
    raw = yaml.safe_load(ALLOCATION_GUARD_ALLOWED_FIXTURE.read_text(encoding="utf-8"))
    assert validate(raw) == []


def test_shared_allocation_guard_fixtures_differ_only_by_allow_billable_provisioning():
    """Keeps provisioning_guard.yaml and its on-disk `allow_billable_provisioning: true`
    companion from drifting apart: each is its own fixture file, not a mutation of 1, so nothing
    enforces this but the test itself."""
    base = yaml.safe_load(ALLOCATION_GUARD_FIXTURE.read_text(encoding="utf-8"))
    allowed = yaml.safe_load(ALLOCATION_GUARD_ALLOWED_FIXTURE.read_text(encoding="utf-8"))
    _az_entries(allowed)[0].pop("allow_billable_provisioning")
    assert base == allowed


def test_shared_allocation_guard_aws_fixture_fails_without_allow_billable_provisioning():
    """ADR 0011 section 3: this fixture, unmodified, must give exactly 1 fault."""
    raw = yaml.safe_load(ALLOCATION_GUARD_AWS_FIXTURE.read_text(encoding="utf-8"))
    faults = validate(raw)
    assert len(faults) == 1
    assert "aws.provisioning_probes[0]" in faults[0]
    assert "allow_billable_provisioning: true" in faults[0]


def test_shared_allocation_guard_aws_fixture_passes_with_allow_billable_provisioning_true():
    """The same fixture, with only `allow_billable_provisioning: true` added, must give 0 faults."""
    raw = yaml.safe_load(ALLOCATION_GUARD_AWS_FIXTURE.read_text(encoding="utf-8"))
    raw["aws"]["provisioning_probes"][0]["allow_billable_provisioning"] = True
    assert validate(raw) == []


def test_shared_allocation_guard_aws_allowed_fixture_passes():
    """The on-disk companion of ALLOCATION_GUARD_AWS_FIXTURE, with
    `allow_billable_provisioning: true` baked in: 0 faults."""
    raw = yaml.safe_load(ALLOCATION_GUARD_AWS_ALLOWED_FIXTURE.read_text(encoding="utf-8"))
    assert validate(raw) == []


def test_shared_allocation_guard_aws_fixtures_differ_only_by_allow_billable_provisioning():
    """Keeps provisioning_guard_aws.yaml and its on-disk `allow_billable_provisioning: true`
    companion from drifting apart: each is its own fixture file, not a mutation of 1, so nothing
    enforces this but the test itself."""
    base = yaml.safe_load(ALLOCATION_GUARD_AWS_FIXTURE.read_text(encoding="utf-8"))
    allowed = yaml.safe_load(ALLOCATION_GUARD_AWS_ALLOWED_FIXTURE.read_text(encoding="utf-8"))
    allowed["aws"]["provisioning_probes"][0].pop("allow_billable_provisioning")
    assert base == allowed


def _entry(
    simulate=False, sku="Standard_D2as_v4", region="eastus", allow_billable_provisioning=False
):
    entry = {"sku": sku, "region": region, "zone": None, "n": 2, "placement_group_mode": "multi"}
    if simulate:
        entry["simulate"] = True
    if allow_billable_provisioning:
        entry["allow_billable_provisioning"] = True
    return entry


def _with_entries(data, entries, **top):
    data = copy.deepcopy(data)
    data["azure"]["provisioning_probes"] = {"az_capacity_reservation": entries}
    data.update(top)
    return data


def _without_entries(data):
    data = copy.deepcopy(data)
    del data["azure"]["provisioning_probes"]
    return data


def test_allocation_guard_passes_without_entries_and_with_only_simulated_entries(data):
    assert validate(_with_entries(data, [])) == []
    assert validate(_with_entries(data, [])) == validate(_without_entries(data))
    entries = [_entry(simulate=True), _entry(simulate=True, region="eastus2")]
    assert validate(_with_entries(data, entries)) == []


def test_allocation_guard_names_each_allocation_entry(data):
    entries = [
        _entry(simulate=True),
        _entry(sku="Standard_NC4as_T4_v3", region="westus2"),
    ]
    faults = validate(_with_entries(data, entries))
    assert len(faults) == 1
    assert "azure.provisioning_probes.az_capacity_reservation[1]" in faults[0]
    assert "Standard_NC4as_T4_v3" in faults[0] and "westus2" in faults[0]
    assert "allow_billable_provisioning: true" in faults[0]


def test_allocation_guard_passes_with_allow_billable_provisioning_true(data):
    entries = [
        _entry(allow_billable_provisioning=True),
        _entry(allow_billable_provisioning=True, region="eastus2"),
    ]
    assert validate(_with_entries(data, entries)) == []


def test_allocation_guard_skips_force_simulate(data):
    """`force_simulate` (the run-wide `--simulate` flag, or the `sim` command) skips the guard
    entirely, even with no `simulate` or `allow_billable_provisioning` key on the entry."""
    entries = [_entry()]
    assert validate(_with_entries(data, entries), force_simulate=True) == []


@pytest.mark.parametrize("bad", ["true", 1, None])
def test_validate_rejects_a_non_boolean_simulate(data, bad):
    data = _with_entries(data, [_entry()])
    _az_entries(data)[0]["simulate"] = bad
    assert validate(data) == [
        f"{R}[0].simulate must be true or false",
        f"{R}[0] (Standard_D2as_v4, eastus) is a provisioning probe:"
        " it creates VMs."
        " Set `allow_billable_provisioning: true` on purpose, or add `simulate: true`",
    ]


@pytest.mark.parametrize("bad", ["true", 1, None])
def test_validate_rejects_a_non_boolean_allow_billable_provisioning(data, bad):
    data = _with_entries(data, [_entry()])
    _az_entries(data)[0]["allow_billable_provisioning"] = bad
    assert validate(data) == [
        f"{R}[0].allow_billable_provisioning must be true or false",
        f"{R}[0] (Standard_D2as_v4, eastus) is a provisioning probe:"
        " it creates VMs."
        " Set `allow_billable_provisioning: true` on purpose, or add `simulate: true`",
    ]


@pytest.mark.parametrize("run_mode", ["local", "cloud-gpu", "cloud-cpu", "anything"])
def test_validate_rejects_run_mode_on_an_azure_allocation_entry(data, run_mode):
    """ADR 0011: no `run_mode` anywhere. An Azure entry that still carries it is a fault that
    says to use `simulate: true` instead, not a silently-ignored or merely-unknown key."""
    entry = _entry(allow_billable_provisioning=True)
    entry["run_mode"] = run_mode
    faults = validate(_with_entries(data, [entry]))
    assert faults == [f"{R}[0].run_mode is removed (ADR 0011); use `simulate: true` instead"]


@pytest.mark.parametrize("jitter", [1, 1.5, -0.1, "x", True])
def test_validate_requires_the_queue_jitter_to_be_at_least_0_and_less_than_1(data, jitter):
    def bad(d):
        d["aws"]["queue_jitter"] = jitter

    assert _mutate(data, bad) == ["aws.queue_jitter must be a number with 0 <= jitter < 1"]


def test_validate_accepts_a_queue_jitter_of_0_and_0_99(data):
    for jitter in (0, 0.99):
        assert _mutate(data, lambda d, j=jitter: d["aws"].update(queue_jitter=j)) == []


def _aws_entry(
    simulate=False,
    instance_type="m5.large",
    region="us-east-1",
    allow_billable_provisioning=False,
):
    entry = {"instance_type": instance_type, "region": region, "n": 2}
    if simulate:
        entry["simulate"] = True
    if allow_billable_provisioning:
        entry["allow_billable_provisioning"] = True
    return entry


def _with_aws_entries(data, entries, **top):
    data = copy.deepcopy(data)
    data["aws"]["provisioning_probes"] = entries
    data.update(top)
    return data


def test_aws_allocation_guard_passes_without_entries_and_with_only_simulated_entries(data):
    assert validate(_with_aws_entries(data, [])) == []
    entries = [_aws_entry(simulate=True), _aws_entry(simulate=True, instance_type="g5.xlarge")]
    assert validate(_with_aws_entries(data, entries)) == []


def test_aws_allocation_guard_names_each_allocation_entry(data):
    allocation = _aws_entry(instance_type="p5.4xlarge", region="us-west-2")
    entries = [_aws_entry(simulate=True), allocation]
    faults = validate(_with_aws_entries(data, entries))
    assert len(faults) == 1
    assert "aws.provisioning_probes[1]" in faults[0]
    assert "p5.4xlarge" in faults[0] and "us-west-2" in faults[0]
    assert "allow_billable_provisioning: true" in faults[0]


def test_aws_allocation_guard_passes_with_allow_billable_provisioning_true(data):
    entries = [
        _aws_entry(allow_billable_provisioning=True),
        _aws_entry(allow_billable_provisioning=True, instance_type="g5.xlarge"),
    ]
    assert validate(_with_aws_entries(data, entries)) == []


def test_validate_reports_each_aws_allocation_probe_fault(data):
    def break_it(d):
        entry = dict(d["aws"]["provisioning_probes"][0])
        entry["instance_type"] = "not valid"
        entry["zone"] = "us-east-1a"
        entry["n"] = "2"
        entry["simulate"] = "test"
        d["aws"]["provisioning_probes"] = [entry]

    faults = _mutate(data, break_it)
    assert any("provisioning_probes[0].instance_type" in f for f in faults)
    assert any("provisioning_probes[0].zone is not a known key" in f for f in faults)
    assert any("provisioning_probes[0].n" in f for f in faults)
    assert any("provisioning_probes[0].simulate" in f for f in faults)


def test_the_old_spot_score_panel_key_is_refused_and_the_fault_names_the_new_key(data):
    """The old key `spot_score_panel` is refused. The fault names `spot_score_requests`."""
    module = data["aws"]["modules"]["aws_spot_score"]
    module["spot_score_panel"] = module.pop("spot_score_requests")
    assert validate(data) == [
        "aws.modules.aws_spot_score.spot_score_panel is not a setting of aws_spot_score; the "
        "settings are spot_score_requests",
        "aws.jobs[aws_spot_score].spot_score_requests must be a non-empty list",
    ]


# The Terraform stacks keep their own copy of the known-key and renamed-key lists, so the plan
# can fail on the same keys as `check-config`. This test keeps the copies the same.
REPO_ROOT = Path(__file__).resolve().parents[2]


def _hcl_known_keys(path: str, provider: str) -> set[str]:
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    opener = re.search(rf"known_{provider}_keys\s*=\s*\[", text)
    assert opener, f"{path}: no known_{provider}_keys list"
    # Scan to the first `]` that is code. A string may hold `#`, `//`, `]`, or an escaped quote;
    # a `#` or `//` comment runs to the end of the line; a `/* */` comment may hold any of these.
    token = re.compile(
        r'"(?P<s>(?:[^"\\\n]|\\.)*)"|#[^\n]*|//[^\n]*|/\*.*?\*/|(?P<end>\])', re.DOTALL
    )
    keys: set[str] = set()
    pos = opener.end()
    while True:
        m = token.search(text, pos)
        assert m, f"{path}: known_{provider}_keys list is not closed"
        if m.group("end"):
            return keys
        if m.group("s") is not None:
            keys.add(json.loads(m.group(0)))
        pos = m.end()


def _hcl_renamed_keys(path: str) -> dict[str, str]:
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    match = re.search(r"renamed_keys\s*=\s*\{(.*?)\}", text, re.DOTALL)
    assert match, f"{path}: no renamed_keys map"
    return dict(re.findall(r'(\w+)\s*=\s*"(.*)"', match.group(1)))


def test_hcl_known_keys_reader_ignores_a_quoted_word_in_a_comment(tmp_path, monkeypatch):
    import sys

    module = sys.modules[__name__]
    (tmp_path / "t.tf").write_text(
        'known_aws_keys = ["jobs", # drop "cadence" later\n  "quota"]\n', encoding="utf-8"
    )
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    assert _hcl_known_keys("t.tf", "aws") == {"jobs", "quota"}


def test_hcl_known_keys_reader_keeps_a_hash_in_a_string_and_drops_a_slash_comment(
    tmp_path, monkeypatch
):
    import sys

    module = sys.modules[__name__]
    (tmp_path / "t.tf").write_text(
        'known_aws_keys = ["a#b", // drop "cadence" later\n  "c//d", "quota"]\n', encoding="utf-8"
    )
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    assert _hcl_known_keys("t.tf", "aws") == {"a#b", "c//d", "quota"}


@pytest.mark.parametrize(
    ("hcl", "expected"),
    [
        # an escaped quote stays inside its string
        ('known_aws_keys = ["a\\"b", "quota"]\n', {'a"b', "quota"}),
        # a quoted word in a /* */ comment is not a key, even over several lines
        ('known_aws_keys = ["jobs", /* drop "cadence"\n later */ "quota"]\n', {"jobs", "quota"}),
        # a `]` in a comment does not end the list
        ('known_aws_keys = ["jobs", # old ] value\n  "quota"]\n', {"jobs", "quota"}),
        ('known_aws_keys = ["jobs", /* ] */ "quota"]\n', {"jobs", "quota"}),
        # a `]` in a string does not end the list
        ('known_aws_keys = ["a]b", "quota"]\n', {"a]b", "quota"}),
    ],
)
def test_hcl_known_keys_reader_handles_escapes_and_comments(tmp_path, monkeypatch, hcl, expected):
    import sys

    module = sys.modules[__name__]
    (tmp_path / "t.tf").write_text(hcl, encoding="utf-8")
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    assert _hcl_known_keys("t.tf", "aws") == expected


@pytest.mark.parametrize(
    ("provider", "path"),
    [("aws", "terraform/aws/main.tf"), ("azure", "terraform/azure/probe.tf")],
)
def test_hcl_known_keys_match_the_python_list(provider, path):
    assert _hcl_known_keys(path, provider) == set(KNOWN_CLOUD_KEYS[provider])


@pytest.mark.parametrize("path", ["terraform/aws/main.tf", "terraform/azure/probe.tf"])
def test_hcl_renamed_keys_match_the_python_map(path):
    assert _hcl_renamed_keys(path) == RENAMED_KEYS


# The worst-case Azure lane of a Region must fit the cadence (spec 0003, section 6B,
# rule 8). The budget of an entry is (1 + k) x (probe + teardown) = (1 + k) x 150 s by default.
def _lane_entry(region: str, n_max: int | None = 16, sku: str = "Standard_NC4as_T4_v3") -> dict:
    entry = {
        "sku": sku,
        "region": region,
        "zone": None,
        "n": 2,
        "placement_group_mode": "multi",
        "simulate": True,
    }
    if n_max is not None:
        entry["n_max"] = n_max
    return entry


def test_azure_lane_that_exceeds_the_cadence_is_refused(data):
    """13 entries with k = 3 are 7800 s; 12 are 7200 s, equal to the 2 h cadence, and are refused
    too (the pre-check lane is not in the sum); 11 are 6600 s and fit."""
    probes = data["azure"]["provisioning_probes"]
    probes["az_capacity_reservation"] = [_lane_entry("eastus") for _ in range(13)]
    probes["az_vmss_flex"] = []
    faults = validate(data)
    assert len(faults) == 1
    assert "Region eastus is 7800 s" in faults[0] and "7200 s" in faults[0]
    probes["az_capacity_reservation"].pop()
    assert any("Region eastus is 7200 s" in f for f in validate(data))
    probes["az_capacity_reservation"].pop()
    assert validate(data) == []


def test_azure_lane_counts_both_probe_types_and_uses_the_azure_timeouts_and_cadence(data):
    probes = data["azure"]["provisioning_probes"]
    probes["az_capacity_reservation"] = [_lane_entry("eastus") for _ in range(6)]
    probes["az_vmss_flex"] = [
        _lane_entry("EastUS", None, f"Standard_X{i}") for i in range(5)
    ]  # 3600 + 750 = 4350 s
    data["azure"]["cadence"]["az_provisioning"] = "73m"  # 4380 s
    assert validate(data) == []
    data["azure"]["cadence"]["az_provisioning"] = "72m"  # 4320 s
    assert any("Region eastus is 4350 s" in f for f in validate(data))
    data["azure"]["cadence"]["az_provisioning"] = "1h"
    assert any("Region eastus is 4350 s" in f for f in validate(data))
    data["azure"]["cadence"]["az_provisioning"] = "2h"
    data["azure"]["probe_timeout_seconds"] = 300  # 360 s per attempt: 6 x 4 x 360 + 5 x 360
    assert any("Region eastus is 10440 s" in f for f in validate(data))


def test_azure_lane_check_defaults_equal_the_prober_defaults():
    from ftg_prober import config
    from ftg_prober.azure.prechecks import DEFAULTS, AzureAllocationBase

    assert DEFAULTS["probe_timeout_seconds"] == config.AZURE_DEFAULT_PROBE_TIMEOUT
    assert DEFAULTS["teardown_timeout_seconds"] == config.AZURE_DEFAULT_TEARDOWN_TIMEOUT
    assert AzureAllocationBase.default_cadence == config.AZURE_DEFAULT_PROVISIONING_CADENCE


@pytest.mark.parametrize("bad", ["2h", [1]])
def test_azure_lane_check_leaves_a_non_mapping_cadence_to_its_own_fault(data, bad):
    data["azure"]["cadence"] = bad
    faults = validate(data)
    assert any("azure.cadence must be a mapping" in f for f in faults)


# The same refusal for the worst-case `aws_odcr` lane of a Region.
def _aws_lane_entry(region: str, n_max: int | None = 16, index: int = 0) -> dict:
    entry = {
        "instance_type": f"m5.{index}xlarge",
        "region": region,
        "n": 2,
        "simulate": True,
    }
    if n_max is not None:
        entry["n_max"] = n_max
    return entry


def test_aws_lane_that_exceeds_the_cadence_is_refused(data):
    """13 entries with k = 3 are 7800 s; 12 are 7200 s, equal to the 2 h cadence, and are refused
    too (strict); 11 are 6600 s and fit."""
    data["aws"]["provisioning_probes"] = [_aws_lane_entry("us-east-1", index=i) for i in range(13)]
    faults = validate(data)
    assert len(faults) == 1
    assert "Region us-east-1 is 7800 s" in faults[0] and "aws.cadence.aws_odcr" in faults[0]
    data["aws"]["provisioning_probes"].pop()
    assert any("Region us-east-1 is 7200 s" in f for f in validate(data))
    data["aws"]["provisioning_probes"].pop()
    assert validate(data) == []


def test_aws_lane_uses_the_aws_timeouts_and_cadence_per_region(data):
    probes = data["aws"]["provisioning_probes"] = [
        _aws_lane_entry("us-east-1", index=i) for i in range(6)
    ]
    probes += [_aws_lane_entry("us-east-2", index=i) for i in range(2)]
    data["aws"]["cadence"]["aws_odcr"] = "41m"  # 2460 s; 6 x 600 = 3600 s
    assert [f.split(" is ")[0][-9:] for f in validate(data)] == ["us-east-1"]
    data["aws"]["cadence"]["aws_odcr"] = "2h"
    data["aws"]["teardown_timeout_seconds"] = 360  # 450 s per attempt: 6 x 4 x 450 = 10800 s
    assert any("Region us-east-1 is 10800 s" in f for f in validate(data))


def test_aws_lane_check_defaults_equal_the_prober_defaults():
    from ftg_prober import config
    from ftg_prober.aws.odcr import DEFAULTS, OdcrProbe

    assert DEFAULTS["probe_timeout_seconds"] == config.AWS_DEFAULT_PROBE_TIMEOUT
    assert DEFAULTS["teardown_timeout_seconds"] == config.AWS_DEFAULT_TEARDOWN_TIMEOUT
    assert OdcrProbe.default_cadence == config.AWS_DEFAULT_PROVISIONING_CADENCE


@pytest.mark.parametrize("bad", ["2h", [1]])
def test_aws_lane_check_leaves_a_non_mapping_cadence_to_its_own_fault(data, bad):
    data["aws"]["cadence"] = bad
    faults = validate(data)
    assert any("aws.cadence must be a mapping" in f for f in faults)
    assert not any("worst-case lane" in f for f in faults)


@pytest.mark.parametrize("cloud", ["aws", "azure"])
def test_probe_timeout_not_longer_than_the_poll_interval_is_refused(data, cloud):
    """With 3 and 5 no create call could start (the deadline floor is the poll
    interval), so every entry would write only a skipped `call deadline` row."""
    data[cloud]["probe_timeout_seconds"] = 3
    data[cloud]["poll_interval_seconds"] = 5
    faults = validate(data)
    assert len(faults) == 1
    assert f"{cloud}.probe_timeout_seconds (3)" in faults[0] and "(5)" in faults[0]
    data[cloud]["probe_timeout_seconds"] = 5  # equal is refused: the precheck uses the window
    assert len(validate(data)) == 1
    data[cloud]["probe_timeout_seconds"] = 6
    assert validate(data) == []
    del data[cloud]["probe_timeout_seconds"]  # the default 90 against a long poll interval
    data[cloud]["poll_interval_seconds"] = 91
    assert len(validate(data)) == 1
    data[cloud]["poll_interval_seconds"] = 90
    assert len(validate(data)) == 1
    data[cloud]["poll_interval_seconds"] = 89
    assert validate(data) == []
    data[cloud]["probe_timeout_seconds"] = 3  # the default poll interval of 5
    del data[cloud]["poll_interval_seconds"]
    assert len(validate(data)) == 1


def test_poll_interval_check_defaults_equal_the_prober_defaults():
    from ftg_prober import config
    from ftg_prober.aws.odcr import DEFAULTS as AWS_DEFAULTS
    from ftg_prober.azure.prechecks import DEFAULTS

    assert DEFAULTS["poll_interval_seconds"] == config.DEFAULT_POLL_INTERVAL
    assert AWS_DEFAULTS["poll_interval_seconds"] == config.DEFAULT_POLL_INTERVAL
    assert AWS_DEFAULTS["probe_timeout_seconds"] == config.AZURE_DEFAULT_PROBE_TIMEOUT


def test_aws_entry_repeating_the_type_and_region_is_refused(data):
    """The probe covers every zone of a Region: a second entry would probe twice."""
    entries = [_aws_entry(simulate=True), _aws_entry(simulate=True)]
    (fault,) = validate(_with_aws_entries(data, entries))
    assert fault.startswith("aws.provisioning_probes[1] repeats the instance type and Region of")
