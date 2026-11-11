import json
import logging
from pathlib import Path

import pytest

from ftg_prober.aws.spot_score import SpotPlacementScoreProbe, request_name
from tests.aws.fakes import FakeClientError, FakeEc2, make_ctx, section

REGIONS = ["us-east-1", "us-west-2"]
# Response body in the shape of the AWS EC2 API reference (`GetSpotPlacementScores` response
# elements, and the `SpotPlacementScore` object: `region`, `score`); no example body is shown on
# either page. The scores are the values this project's own stored rows gave for the `p5-1x`
# Spot score request on 2026-10-14 (p5.4xlarge: us-east-1=1, us-east-2=1, us-west-2=3).
# https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html
# https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_SpotPlacementScore.html
PROVIDER_RESPONSE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "provider"
    / "aws"
    / "get_spot_placement_scores_response.json"
)


def scores(east, west):
    return {
        "SpotPlacementScores": [
            {"Region": "us-east-1", "Score": east},
            {"Region": "us-west-2", "Score": west},
        ]
    }


def scorer(table):
    """A fake `get_spot_placement_scores` that answers from `{types tuple: answer}`."""

    def answer(**kwargs):
        value = table[tuple(kwargs["InstanceTypes"])]
        return value() if callable(value) else value

    return answer


def default_table():
    return {
        ("p5.48xlarge",): scores(9, 7),
        ("g5.xlarge",): scores(5, 6),
        ("g6.xlarge",): scores(4, 4),
        ("g5.xlarge", "g4dn.xlarge", "g6.xlarge"): scores(6, 6),
        ("m5.4xlarge",): scores(2, 3),
    }


def run(table, probe=None):
    ec2 = FakeEc2("us-east-1", get_spot_placement_scores=scorer(table))
    probe = probe or SpotPlacementScoreProbe()
    rows = probe.run(make_ctx(section(), {"us-east-1": ec2}))
    return rows, ec2, probe


def by_request(rows, name):
    return [r for r in rows if r.details["request"] == name]


def test_one_row_per_region_per_entry_with_the_score_in_details():
    rows, ec2, _ = run(default_table())

    assert len(rows) == 5 * 2
    p5 = by_request(rows, "p5")
    assert [(r.region, r.outcome, r.details["score"]) for r in p5] == [
        ("us-east-1", "success", 9),
        ("us-west-2", "success", 7),
    ]
    first = p5[0]
    assert first.probe_type == "aws_spot_score"
    assert first.sku == "p5.48xlarge"
    assert first.zone is None
    assert first.n_requested == 1
    assert first.details["instance_types"] == ["p5.48xlarge"]
    assert first.details["label"] == "8x H100"
    assert "control" not in first.details
    assert all(r.validate() == [] for r in rows)


def test_request_parameters_of_spec_4_2():
    _, ec2, _ = run(default_table())

    assert len(ec2.calls) == 5
    name, kwargs = ec2.calls[0]
    assert name == "get_spot_placement_scores"
    assert kwargs == {
        "InstanceTypes": ["p5.48xlarge"],
        "TargetCapacity": 1,
        "TargetCapacityUnitType": "units",
        "SingleAvailabilityZone": False,
        "RegionNames": REGIONS,
    }


def test_a_mix_entry_joins_the_types_with_a_comma_in_sku():
    rows, _, _ = run(default_table())
    trio = by_request(rows, "control-trio")
    assert {r.sku for r in trio} == {"g5.xlarge,g4dn.xlarge,g6.xlarge"}
    assert trio[0].details["instance_types"] == ["g5.xlarge", "g4dn.xlarge", "g6.xlarge"]


def test_request_name_reads_a_new_row_and_an_old_row():
    rows, _, _ = run(default_table())
    new = by_request(rows, "g5")[0]
    assert request_name(new) == "g5" and "panel" not in new.details
    new.details["panel"] = new.details.pop("request")
    assert request_name(new) == "g5"


def test_request_name_prefers_request_when_a_row_has_both_keys():
    row = by_request(run(default_table())[0], "g5")[0]
    row.details["panel"] = "old-name"
    assert request_name(row) == "g5"


def test_request_name_is_none_when_a_row_has_neither_key():
    row = by_request(run(default_table())[0], "g5")[0]
    del row.details["request"]
    assert request_name(row) is None


def test_controls_are_written_on_the_control_rows():
    table = default_table()
    table[("m5.4xlarge",)] = scores(2, 5)  # us-west-2 breaks the CPU rule
    table[("g5.xlarge",)] = scores(5, 7)  # us-west-2: g5 alone above the trio
    rows, _, _ = run(table)

    cpu = by_request(rows, "control-cpu")
    assert [r.details["control"] for r in cpu] == [
        {"rule": "cpu_score_max", "max": 3, "ok": True},
        {"rule": "cpu_score_max", "max": 3, "ok": False},
    ]
    trio = by_request(rows, "control-trio")
    assert trio[0].details["control"] == {
        "rule": "single_not_above_mix",
        "singles": {"g5.xlarge": 5, "g6.xlarge": 4},
        "ok": True,
    }
    assert trio[1].details["control"]["ok"] is False
    assert all("control" not in r.details for r in by_request(rows, "g5"))


def test_a_control_without_the_scores_it_needs_is_not_judged():
    table = default_table()
    table[("g5.xlarge", "g4dn.xlarge", "g6.xlarge")] = FakeClientError("InternalError")
    table[("m5.4xlarge",)] = {"SpotPlacementScores": [{"Region": "us-east-1", "Score": 1}]}
    rows, _, _ = run(table)

    assert [r.details["control"]["ok"] for r in by_request(rows, "control-trio")] == [None, None]
    cpu = by_request(rows, "control-cpu")
    assert [(r.outcome, r.details["control"]["ok"]) for r in cpu] == [
        ("success", True),
        ("none", None),
    ]


def test_cpu_mix_control_passes_when_the_mix_scores_above_3():
    requests = section(
        spot_score_requests=[
            {
                "name": "control-cpu",
                "instance_types": ["m5.4xlarge"],
                "label": "Control: CPU alone",
            },
            {
                "name": "control-cpu-trio",
                "instance_types": ["m5.2xlarge", "m5.4xlarge", "m5.8xlarge"],
                "label": "Control: CPU 3 sizes",
            },
        ]
    )
    table = {
        ("m5.4xlarge",): scores(2, 3),
        ("m5.2xlarge", "m5.4xlarge", "m5.8xlarge"): scores(9, 4),
    }
    ec2 = FakeEc2("us-east-1", get_spot_placement_scores=scorer(table))
    rows = SpotPlacementScoreProbe().run(make_ctx(requests, {"us-east-1": ec2}))

    trio = by_request(rows, "control-cpu-trio")
    assert [r.details["control"] for r in trio] == [
        {"rule": "cpu_mix_min", "min": 4, "singles": {"m5.4xlarge": 2}, "ok": True},
        {"rule": "cpu_mix_min", "min": 4, "singles": {"m5.4xlarge": 3}, "ok": True},
    ]


def test_cpu_mix_control_fails_when_the_mix_scores_3_or_lower():
    requests = section(
        spot_score_requests=[
            {
                "name": "control-cpu",
                "instance_types": ["m5.4xlarge"],
                "label": "Control: CPU alone",
            },
            {
                "name": "control-cpu-trio",
                "instance_types": ["m5.2xlarge", "m5.4xlarge", "m5.8xlarge"],
                "label": "Control: CPU 3 sizes",
            },
        ]
    )
    table = {
        ("m5.4xlarge",): scores(2, 3),
        ("m5.2xlarge", "m5.4xlarge", "m5.8xlarge"): scores(9, 3),
    }
    ec2 = FakeEc2("us-east-1", get_spot_placement_scores=scorer(table))
    rows = SpotPlacementScoreProbe().run(make_ctx(requests, {"us-east-1": ec2}))

    trio = by_request(rows, "control-cpu-trio")
    assert trio[0].details["control"]["ok"] is True
    assert trio[1].details["control"]["ok"] is False


TRIO = ("m5.2xlarge", "m5.4xlarge", "m5.8xlarge")


def trio_ok(single, trio):
    """Run the CPU control pair in 1 Region and return the trio `ok` value."""
    requests = section(
        spot_score_requests=[
            {
                "name": "control-cpu",
                "instance_types": ["m5.4xlarge"],
                "label": "Control: CPU alone",
            },
            {
                "name": "control-cpu-trio",
                "instance_types": list(TRIO),
                "label": "Control: CPU 3 sizes",
            },
        ]
    )
    table = {("m5.4xlarge",): scores(single, 3), TRIO: scores(trio, 9)}
    ec2 = FakeEc2("us-east-1", get_spot_placement_scores=scorer(table))
    rows = SpotPlacementScoreProbe().run(make_ctx(requests, {"us-east-1": ec2}))
    controls = [r.details["control"] for r in by_request(rows, "control-cpu-trio")]
    assert [c["singles"] for c in controls] == [{"m5.4xlarge": single}, {"m5.4xlarge": 3}]
    return [c["ok"] for c in controls]


def test_cpu_mix_control_is_null_when_the_single_scores_1_in_the_same_region():
    # us-east-2 was short of m5 capacity; single 1 and trio 1 was reported as false.
    # East: single 1, trio 1 -> null. West: single 3, trio 9 -> true (other Region unaffected).
    assert trio_ok(1, 1) == [None, True]


def test_cpu_mix_control_stays_false_when_the_single_is_not_short():
    # East: single 3, trio 1 -> false.
    assert trio_ok(3, 1) == [False, True]


def test_cpu_mix_control_stays_true_when_the_trio_passes_and_the_single_scores_1():
    assert trio_ok(1, 9) == [True, True]


def test_cpu_mix_control_is_true_when_the_trio_scores_9():
    assert trio_ok(3, 9) == [True, True]


def test_cpu_mix_control_without_a_score_is_not_judged():
    requests = section(
        spot_score_requests=[
            {
                "name": "control-cpu",
                "instance_types": ["m5.4xlarge"],
                "label": "Control: CPU alone",
            },
            {
                "name": "control-cpu-trio",
                "instance_types": ["m5.2xlarge", "m5.4xlarge", "m5.8xlarge"],
                "label": "Control: CPU 3 sizes",
            },
        ]
    )
    table = {
        ("m5.4xlarge",): scores(2, 3),
        ("m5.2xlarge", "m5.4xlarge", "m5.8xlarge"): FakeClientError("InternalError"),
    }
    ec2 = FakeEc2("us-east-1", get_spot_placement_scores=scorer(table))
    rows = SpotPlacementScoreProbe().run(make_ctx(requests, {"us-east-1": ec2}))

    assert [r.details["control"] for r in by_request(rows, "control-cpu-trio")] == [
        {"rule": "cpu_mix_min", "min": 4, "singles": {"m5.4xlarge": 2}, "ok": None},
        {"rule": "cpu_mix_min", "min": 4, "singles": {"m5.4xlarge": 3}, "ok": None},
    ]


def test_a_2_type_cpu_entry_uses_the_cpu_control_not_the_mix_control():
    """AWS scores a request of fewer than 3 types low, so a 2-type CPU entry is not a mix."""
    requests = section(
        spot_score_requests=[
            {
                "name": "control-cpu-pair",
                "instance_types": ["m5.4xlarge", "m5.8xlarge"],
                "label": "Control: CPU 2 sizes",
            },
        ]
    )
    table = {("m5.4xlarge", "m5.8xlarge"): scores(2, 9)}
    ec2 = FakeEc2("us-east-1", get_spot_placement_scores=scorer(table))
    rows = SpotPlacementScoreProbe().run(make_ctx(requests, {"us-east-1": ec2}))

    pair = by_request(rows, "control-cpu-pair")
    assert pair[0].details["control"] == {"rule": "cpu_score_max", "max": 3, "ok": True}
    assert pair[1].details["control"] == {"rule": "cpu_score_max", "max": 3, "ok": False}


def test_single_not_above_mix_control_on_the_g5_family():
    requests = section(
        spot_score_requests=[
            {"name": "g5", "instance_types": ["g5.xlarge"], "label": "1x A10G, this size"},
            {"name": "g5-2x", "instance_types": ["g5.2xlarge"], "label": "1x A10G, this size"},
            {"name": "g5-4x", "instance_types": ["g5.4xlarge"], "label": "1x A10G, this size"},
            {
                "name": "g5-family",
                "instance_types": ["g5.xlarge", "g5.2xlarge", "g5.4xlarge"],
                "label": "1x A10G, any of 3 sizes",
            },
        ]
    )
    table = {
        ("g5.xlarge",): scores(5, 7),  # us-west-2: g5 alone above the family mix
        ("g5.2xlarge",): scores(4, 6),
        ("g5.4xlarge",): scores(3, 6),
        ("g5.xlarge", "g5.2xlarge", "g5.4xlarge"): scores(6, 6),
    }
    ec2 = FakeEc2("us-east-1", get_spot_placement_scores=scorer(table))
    rows = SpotPlacementScoreProbe().run(make_ctx(requests, {"us-east-1": ec2}))

    family = by_request(rows, "g5-family")
    assert family[0].details["control"] == {
        "rule": "single_not_above_mix",
        "singles": {"g5.xlarge": 5, "g5.2xlarge": 4, "g5.4xlarge": 3},
        "ok": True,
    }
    assert family[1].details["control"]["singles"] == {
        "g5.xlarge": 7,
        "g5.2xlarge": 6,
        "g5.4xlarge": 6,
    }
    assert family[1].details["control"]["ok"] is False
    assert all("control" not in r.details for r in by_request(rows, "g5"))


def test_single_not_above_mix_control_without_a_score_is_not_judged():
    requests = section(
        spot_score_requests=[
            {"name": "g5", "instance_types": ["g5.xlarge"], "label": "1x A10G, this size"},
            {"name": "g5-2x", "instance_types": ["g5.2xlarge"], "label": "1x A10G, this size"},
            {"name": "g5-4x", "instance_types": ["g5.4xlarge"], "label": "1x A10G, this size"},
            {
                "name": "g5-family",
                "instance_types": ["g5.xlarge", "g5.2xlarge", "g5.4xlarge"],
                "label": "1x A10G, any of 3 sizes",
            },
        ]
    )
    table = {
        ("g5.xlarge",): FakeClientError("InternalError"),
        ("g5.2xlarge",): scores(4, 6),
        ("g5.4xlarge",): scores(3, 6),
        ("g5.xlarge", "g5.2xlarge", "g5.4xlarge"): FakeClientError("InternalError"),
    }
    ec2 = FakeEc2("us-east-1", get_spot_placement_scores=scorer(table))
    rows = SpotPlacementScoreProbe().run(make_ctx(requests, {"us-east-1": ec2}))

    family = by_request(rows, "g5-family")
    assert [r.details["control"]["ok"] for r in family] == [None, None]
    assert family[0].details["control"]["singles"] == {
        "g5.xlarge": None,
        "g5.2xlarge": 4,
        "g5.4xlarge": 3,
    }


def test_freeze_on_a_new_entry_does_not_skip_an_older_entry(repo_config):
    """The repo's full request list. `g5-family`, the newest entry, hits the config limit in the
    first run, but every entry before it still gets a score (spec 0008, 4.4)."""
    requests = repo_config.aws["spot_score_requests"]
    names = [entry["name"] for entry in requests]
    assert names[-2:] == ["control-cpu-trio", "g5-family"]

    table = {tuple(entry["instance_types"]): scores(5, 5) for entry in requests}
    table[("g5.xlarge", "g5.2xlarge", "g5.4xlarge")] = FakeClientError("MaxConfigLimitExceeded")
    ec2 = FakeEc2("us-east-1", get_spot_placement_scores=scorer(table))
    rows = SpotPlacementScoreProbe().run(
        make_ctx(section(spot_score_requests=requests), {"us-east-1": ec2})
    )

    older_names = [name for name in names if name != "g5-family"]
    for name in older_names:
        assert by_request(rows, name)[0].outcome == "success", name
    assert by_request(rows, "g5-family")[0].outcome == "error"


def test_empty_result_gives_none_rows():
    table = default_table()
    table[("p5.48xlarge",)] = {"SpotPlacementScores": []}
    rows, _, _ = run(table)

    p5 = by_request(rows, "p5")
    assert [(r.outcome, "score" in r.details) for r in p5] == [("none", False), ("none", False)]


def test_target_capacity_limit_gives_cap_rows():
    for code in ("TargetCapacityLimitExceeded", "TargetCapacityLimitExceededException"):
        table = default_table()
        table[("p5.48xlarge",)] = FakeClientError(code)
        rows, _, _ = run(table)

        p5 = by_request(rows, "p5")
        assert [(r.region, r.outcome, r.error_code) for r in p5] == [
            ("us-east-1", "cap", code),
            ("us-west-2", "cap", code),
        ]
        assert all(r.validate() == [] for r in p5)


def test_other_errors_give_error_rows_with_the_raw_code():
    table = default_table()
    table[("p5.48xlarge",)] = FakeClientError("UnauthorizedOperation")
    rows, _, probe = run(table)

    assert {(r.outcome, r.error_code) for r in by_request(rows, "p5")} == {
        ("error", "UnauthorizedOperation")
    }
    assert probe.frozen is False


@pytest.mark.parametrize("code", ["MaxConfigLimitExceeded", "MaxConfigLimitExceededException"])
def test_config_limit_freezes_the_request_list_for_the_process_lifetime(caplog, code):
    calls = {"p5": 0}

    def p5_once_then_limit():
        calls["p5"] += 1
        return scores(9, 7)

    table = default_table()
    table[("p5.48xlarge",)] = p5_once_then_limit
    table[("m5.4xlarge",)] = FakeClientError(code)
    probe = SpotPlacementScoreProbe()

    with caplog.at_level(logging.WARNING):
        rows, ec2, _ = run(table, probe)
    assert probe.frozen is True
    cpu = by_request(rows, "control-cpu")
    assert [(r.outcome, r.error_code) for r in cpu] == [("error", code), ("error", code)]
    assert cpu[0].details["frozen"] is True
    assert cpu[0].details["live_configs"] == 4
    assert "spot_score.frozen" in caplog.text
    assert "live_configs=4" in caplog.text

    # The next call skips the entry that never got a score. The others still run.
    rows, ec2, _ = run(table, probe)
    assert {r.details["request"] for r in rows} == {"p5", "g5", "g6", "control-trio"}
    assert [c[1]["InstanceTypes"] for c in ec2.calls] == [
        ["p5.48xlarge"],
        ["g5.xlarge"],
        ["g6.xlarge"],
        ["g5.xlarge", "g4dn.xlarge", "g6.xlarge"],
    ]
    assert calls["p5"] == 2


def test_next_token_is_followed():
    pages = [
        {"SpotPlacementScores": [{"Region": "us-east-1", "Score": 8}], "NextToken": "t2"},
        {"SpotPlacementScores": [{"Region": "us-west-2", "Score": 2}]},
    ]
    config = section(
        spot_score_requests=[{"name": "p5", "instance_types": ["p5.48xlarge"], "label": "8x H100"}]
    )
    ec2 = FakeEc2("us-east-1", get_spot_placement_scores=pages)
    rows = SpotPlacementScoreProbe().run(make_ctx(config, {"us-east-1": ec2}))

    assert [r.details["score"] for r in rows] == [8, 2]
    assert ec2.calls[1][1]["NextToken"] == "t2"


def test_the_provider_response_gives_the_real_scores_and_units():
    """Runs the real probe against a reconstructed `GetSpotPlacementScores` response
    (PROVIDER_RESPONSE). The scores match this project's own stored rows (comment above)."""
    body = json.loads(PROVIDER_RESPONSE.read_text())
    requests = section(
        regions=["us-east-1", "us-east-2", "us-west-2"],
        spot_score_requests=[
            {"name": "p5-1x", "instance_types": ["p5.4xlarge"], "label": "Spot score, 1x H100"}
        ],
    )
    ec2 = FakeEc2("us-east-1", get_spot_placement_scores=[body])
    rows = SpotPlacementScoreProbe().run(make_ctx(requests, {"us-east-1": ec2}))

    assert len(rows) == 3
    by_region = {r.region: r for r in rows}
    assert by_region["us-east-1"].details["score"] == 1
    assert by_region["us-east-2"].details["score"] == 1
    assert by_region["us-west-2"].details["score"] == 3
    assert {r.outcome for r in rows} == {"success"}
    assert {r.error_code for r in rows} == {None}
    assert {type(r.details["score"]) for r in rows} == {int}
    assert all(r.sku == "p5.4xlarge" for r in rows)
    assert all(r.validate() == [] for r in rows)
