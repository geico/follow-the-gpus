import hashlib
import json
import random
import re
from datetime import UTC, datetime

import pytest

from ftg_prober import cli
from ftg_prober.aws.cb_earliest import earliest_plan
from ftg_prober.aws.cb_offerings import request_plan
from ftg_prober.record import PROBE_TYPES, Record
from ftg_prober.sim import (
    AWS_CPU_MAX_SCORE,
    AZURE_EVICTION_BUCKETS,
    AZURE_SCORES,
    StoreNotEmptyError,
    sim_calls,
    write_history,
)
from ftg_prober.store import FileStore
from tests.conftest import REPO_ROOT, TARGETS_PATH, MemoryStore
from tests.shapes import step_types, value_types, variant

END = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
KEY_RE = re.compile(
    r"^experiment=[a-z0-9-]+/provider=(aws|azure)/probe_type=(?P<t>[a-z_]+)/date=\d{4}-\d{2}-\d{2}/"
    r"\d{8}T\d{6}Z_[a-z0-9-]+_[0-9a-f-]{36}\.(jsonl|json)$"
)


def rows_of(store: MemoryStore) -> list[Record]:
    rows = []
    for key, data in store.objects.items():
        if key.endswith(".jsonl"):
            rows.extend(Record(**json.loads(line)) for line in data.decode().splitlines())
    return rows


def test_write_history_covers_every_probe_type_region_and_sku(repo_config, memory_store):
    counts = write_history(repo_config, memory_store, hours=1, seed=1, end=END)
    assert set(counts) == set(PROBE_TYPES)
    assert all(c["calls"] >= 1 and c["rows"] >= 1 for c in counts.values())
    rows = rows_of(memory_store)
    assert {row.simulated for row in rows} == {True}
    assert {row.probe_type for row in rows} == set(PROBE_TYPES)
    assert all(row.validate() == [] for row in rows)
    for key in memory_store.objects:
        assert KEY_RE.match(key), key

    aws = [r for r in rows if r.provider == "aws"]
    assert {r.region for r in aws} == set(repo_config.aws["regions"])
    azure = [r for r in rows if r.probe_type in ("az_spot_score", "az_spot")]
    assert {r.region for r in azure} == set(repo_config.azure["regions"])
    assert {r.sku for r in azure} == set(repo_config.azure["spot_sizes"])
    requests = {",".join(e["instance_types"]) for e in repo_config.aws["spot_score_requests"]}
    assert {r.sku for r in rows if r.probe_type == "aws_spot_score"} == requests


def test_cadence_gives_the_call_counts(repo_config, memory_store):
    counts = write_history(repo_config, memory_store, hours=2, seed=1, end=END)
    assert counts["aws_spot_score"]["calls"] == 24  # every 5 min
    # every 15 min, per group of 5 sizes: 1 regional call per 8 regions, 1 zonal per region
    azure = repo_config.azure
    size_groups = -(-len(azure["spot_sizes"]) // 5)
    region_groups = -(-len(azure["regions"]) // 8)
    assert counts["az_spot_score"]["calls"] == 8 * size_groups * (
        region_groups + len(azure["regions"])
    )
    # every 2 h, 1 per (Region, type, count, duration) of the probe
    assert counts["aws_cb_offering"]["calls"] == len(request_plan(repo_config.aws))
    # every 2 h, 1 per (Region, type, count, earliest duration)
    assert counts["aws_cb_earliest"]["calls"] == len(earliest_plan(repo_config.aws))
    assert counts["aws_spot_advisor"]["calls"] == 1  # daily
    entries = repo_config.azure["provisioning_probes"]
    for probe_type in ("az_capacity_reservation", "az_vmss_flex"):
        own = [e for e in entries if e["probe_type"] == probe_type]
        assert counts[probe_type]["calls"] == len(own) > 0  # every 2 h, 1 per entry of its type
    # every 2 h, 1 row per entry
    assert counts["az_precheck"] == {"calls": 1, "rows": len(entries)}
    assert sum(k.endswith(".json") for k in memory_store.objects) == 1  # 1 raw advisor feed


def test_values_use_each_clouds_native_scale(repo_config, memory_store):
    write_history(repo_config, memory_store, hours=1, seed=7, end=END)
    rows = rows_of(memory_store)
    scores = [r for r in rows if r.probe_type == "aws_spot_score"]
    assert all(
        isinstance(r.details["score"], int) and 1 <= r.details["score"] <= 10 for r in scores
    )
    assert all(r.details["score"] <= AWS_CPU_MAX_SCORE for r in scores if r.sku == "m5.4xlarge")
    cpu_mix = [r for r in scores if r.sku == "m5.2xlarge,m5.4xlarge,m5.8xlarge"]
    assert cpu_mix and all(r.details["score"] > AWS_CPU_MAX_SCORE for r in cpu_mix)
    az_scores = [r for r in rows if r.probe_type == "az_spot_score"]
    for r in az_scores:
        if r.outcome == "success":
            assert r.details["score"] in AZURE_SCORES
        else:
            assert (r.outcome, r.details["score"]) in (
                ("restricted", "RestrictedSkuNotAvailable"),
                ("none", "DataNotFoundOrStale"),
            )
    az_spot = [r for r in rows if r.probe_type == "az_spot"]
    assert all(r.details["eviction_rate"] in (*AZURE_EVICTION_BUCKETS, None) for r in az_spot)
    assert all(r.details["price"] > 0 for r in az_spot if r.outcome == "success")
    prices = [r for r in rows if r.probe_type == "aws_spot_price"]
    assert {r.outcome for r in prices} == {"success", "none"}
    assert all(r.zone and r.zone.startswith(r.region) for r in prices)
    allocation = [r for r in rows if r.probe_type in ("az_capacity_reservation", "az_vmss_flex")]
    entries = repo_config.azure["provisioning_probes"]
    assert {r.sku for r in allocation} == {e["sku"] for e in entries}
    assert all(r.n_requested == 2 for r in allocation)
    assert {
        r.placement_group_mode for r in allocation if r.probe_type == "az_capacity_reservation"
    } == {"n/a"}


# The `details` keys of every `az_precheck` row (spec 0010 section 4.1).
PRECHECK_DETAILS = {"entry_probe_type", "n_ceiling", "sku_zones", "restrictions"}
PRECHECK_USAGE = {
    "family_current_vcpus",
    "family_limit_vcpus",
    "total_current_vcpus",
    "total_limit_vcpus",
    "family_available_vcpus",
    "total_available_vcpus",
}


def test_az_precheck_rows_follow_spec_0010(repo_config, memory_store):
    write_history(repo_config, memory_store, hours=24, seed=9, end=END)
    rows = [r for r in rows_of(memory_store) if r.probe_type == "az_precheck"]
    entries = repo_config.azure["provisioning_probes"]
    assert len(rows) == 12 * len(entries)
    first_cycle = rows[: len(entries)]
    assert [(r.sku, r.region, r.placement_group_mode, r.n_requested) for r in first_cycle] == [
        (e["sku"], e["region"], e["placement_group_mode"], e["n"]) for e in entries
    ]
    assert {r.outcome for r in rows} <= {"success", "restricted"}
    assert all(r.n_placed is None and r.validate() == [] for r in rows)
    for row in rows:
        assert set(row.details) >= PRECHECK_DETAILS
        assert not {"route", "route_source"} & set(row.details)
        if row.outcome == "success":
            assert row.error_code is None and row.details["n_ceiling"] >= 1
            assert set(row.details) >= PRECHECK_USAGE and row.details["restrictions"] == []
            d = row.details
            assert (
                d["family_available_vcpus"] == d["family_limit_vcpus"] - d["family_current_vcpus"]
            )
        else:
            assert row.error_code == "NotAvailableForSubscription"
            assert row.details["n_ceiling"] is None
            (restriction,) = row.details["restrictions"]
            assert restriction["type"] == "Location"
            assert restriction["reason_code"] == row.error_code


def test_az_precheck_names_the_probe_type_of_each_entry():
    section = {
        "provisioning_probes": [
            {"sku": "S", "region": "r", "zone": 1, "n": 2, "placement_group_mode": "multi"}
            | {"probe_type": "az_capacity_reservation"},
            {"sku": "S", "region": "r", "zone": None, "n": 2, "placement_group_mode": "multi"}
            | {"probe_type": "az_vmss_flex"},
        ]
    }
    (call,) = sim_calls("az_precheck", section, END, random.Random(0))
    first, second = call.rows
    assert first.zone == "1" and second.zone is None
    assert first.details["entry_probe_type"] == "az_capacity_reservation"
    assert first.details["capacity_reservation_supported"] is True
    assert second.details["entry_probe_type"] == "az_vmss_flex"
    assert second.details["capacity_reservation_supported"] is False
    assert sim_calls("az_precheck", {"provisioning_probes": []}, END, random.Random(0)) == []


def test_same_seed_gives_the_same_objects(repo_config, tmp_path):
    first = FileStore(tmp_path / "a")
    second = FileStore(tmp_path / "b")
    third = FileStore(tmp_path / "c")
    write_history(repo_config, first, hours=1, seed=3, end=END)
    write_history(repo_config, second, hours=1, seed=3, end=END)
    write_history(repo_config, third, hours=1, seed=4, end=END)

    def digest(store: FileStore) -> str:
        files = sorted(p.relative_to(store.root) for p in store.root.rglob("*") if p.is_file())
        h = hashlib.sha256()
        for rel in files:
            h.update(str(rel).encode())
            h.update((store.root / rel).read_bytes())
        return h.hexdigest()

    assert digest(first) == digest(second)
    assert digest(first) != digest(third)


def test_sim_calls_make_one_call_per_group(repo_config):
    rng = random.Random(0)
    calls = sim_calls("aws_spot_price", repo_config.aws, END, rng)
    assert len(calls) == 3
    assert {r.region for r in calls[0].rows} == {"us-east-1"}
    calls = sim_calls("az_spot_score", repo_config.azure, END, rng)
    # 1 regional call per 8 regions, then 1 zonal call per region (5 sizes each)
    assert len(calls) == 2 + 9
    assert len(calls[0].rows) == 8 * 5 and len(calls[1].rows) == 1 * 5
    assert {r.details["scope"] for call in calls[:2] for r in call.rows} == {"regional"}
    for call in calls[2:]:
        assert {r.details["scope"] for r in call.rows} == {"zonal"}
        assert len(call.rows) in (5, 15)  # no zone, or the zones 1, 2, 3
    calls = sim_calls("aws_spot_advisor", repo_config.aws, END, rng)
    assert calls[0].raw is not None
    assert "spot_advisor" in json.loads(calls[0].raw)


SPEC_DETAILS_PATH = REPO_ROOT / "spec" / "0003-prober-core-spec.md"
NUMBER = (int, float)
# A copy of the `details` keys and types in the table of spec 0003 section 4.4.
# The fill-ratio panels read only record fields.
DETAILS_TABLE: dict[str, dict[str, type | tuple[type, ...]]] = {
    "aws_spot_score": {"score": int, "instance_types": list},
    "aws_spot_price": {"price": NUMBER, "price_ts": str},
    "aws_spot_advisor": {"interruption_range": str, "savings_pct": NUMBER},
    "aws_cb_offering": {"start": str, "end": str, "duration_hours": NUMBER, "upfront_fee": NUMBER},
    "aws_odcr": {},
    "az_spot_score": {"score": str, "desired_count": int},
    "az_spot": {"eviction_rate": (str, type(None)), "price": NUMBER, "os_type": str},
    "az_capacity_reservation": {},
    "az_vmss_flex": {},
}
OLD_KEYS = {"spot_price", "record_ts"}


def spec_table_keys() -> dict[str, set[str]]:
    """The `details.<key>` names in each row of the spec 0003 section 4.4 table."""
    text = SPEC_DETAILS_PATH.read_text()
    section = text.split("### 4.4 ", 1)[1].split("\n### ", 1)[0]
    keys = {}
    for line in section.splitlines():
        match = re.match(r"^\| `([a-z_]+)` \|", line)
        if match and match.group(1) != "probe_type":
            keys[match.group(1)] = set(re.findall(r"details\.([a-z_]+)", line))
    return keys


def test_the_details_table_is_a_copy_of_spec_details():
    copied = {t: set(keys) for t, keys in DETAILS_TABLE.items()}
    assert spec_table_keys() == copied
    # Spec 0003 section 4.4 has no `az_precheck`, `aws_cb_earliest`, or `aws_cb_longest` row: no
    # panel reads them.
    assert set(DETAILS_TABLE) == set(PROBE_TYPES) - {
        "az_precheck",
        "aws_cb_earliest",
        "aws_cb_longest",
    }


def test_details_keys_match_the_dashboard_contract(repo_config, memory_store):
    write_history(repo_config, memory_store, hours=3, seed=5, end=END)
    rows = rows_of(memory_store)
    for probe_type, keys in DETAILS_TABLE.items():
        typed = [r for r in rows if r.probe_type == probe_type]
        success = [r for r in typed if r.outcome == "success"]
        assert success, probe_type
        # The page reads the panel value from a `success` row. Each `az_spot` row has the keys.
        checked = typed if probe_type == "az_spot" else success
        for row in checked:
            for key, kind in keys.items():
                assert key in row.details, (probe_type, key)
                value = row.details[key]
                if value is None and row.outcome != "success":
                    continue  # a `none` or `error` row has no value
                assert isinstance(value, kind) and not isinstance(value, bool), (probe_type, key)
    for row in rows:
        assert not OLD_KEYS & set(row.details), row.probe_type


def test_write_history_refuses_a_file_store_that_is_not_empty(repo_config, tmp_path):
    store = FileStore(tmp_path / "sim")
    write_history(repo_config, store, hours=1, seed=1, end=END)
    before = sorted(store.root.rglob("*"))
    with pytest.raises(StoreNotEmptyError, match="not empty"):
        write_history(repo_config, store, hours=1, seed=1, end=END)
    assert sorted(store.root.rglob("*")) == before


def test_write_history_accepts_an_empty_folder(repo_config, tmp_path):
    (tmp_path / "sim").mkdir()
    counts = write_history(repo_config, FileStore(tmp_path / "sim"), hours=1, seed=1, end=END)
    assert all(c["calls"] >= 1 for c in counts.values())


def test_a_second_sim_run_into_the_same_folder_exits_2(tmp_path, capsys):
    store = f"file://{tmp_path / 'sim'}"
    argv = ["sim", "--config", str(TARGETS_PATH), "--store", store, "--hours", "1"]
    assert cli.main(argv) == 0
    capsys.readouterr()
    assert cli.main(argv) == 2
    err = capsys.readouterr().err
    assert "is not empty" in err and str(tmp_path / "sim") in err


def test_the_earliest_rows_have_the_raw_fields_or_the_none_details(repo_config, memory_store):
    write_history(repo_config, memory_store, hours=6, seed=3, end=END)
    rows = [r for r in rows_of(memory_store) if r.probe_type == "aws_cb_earliest"]

    assert {r.outcome for r in rows} == {"success", "none"}
    for row in rows:
        assert row.details["duration_hours"] in (24, 168)
        if row.outcome == "success":
            assert row.details["start"] < row.details["end"]
            assert row.details["duration_minutes"] == 0
            assert re.fullmatch(r"cb-sim-[0-9a-f]{16}", row.details["offering_id"])


def test_aws_cb_longest_sim_rows_have_the_same_details_keys_as_a_real_row():
    """The sim row and a real row (fake client) must have the same `details` keys, for each
    outcome, or a reader of one would see keys the other never has (spec 0008, section 3B.4)."""
    from datetime import timedelta

    from ftg_prober.aws.cb_longest import CapacityBlockLongestProbe
    from tests.aws.fakes import FakeClientError, FakeEc2, make_ctx
    from tests.aws.fakes import section as aws_section

    config = aws_section(
        regions=["us-east-1"],
        capacity_blocks={
            "instance_types": ["p5.48xlarge"],
            "instance_count": 1,
            "duration_hours": [24],
            "start_window_days": 14,
        },
    )
    s = datetime(2026, 10, 30, 11, 30, tzinfo=UTC)

    def offer():
        return {
            "CapacityBlockOfferingId": "cb-1",
            "InstanceType": "p5.48xlarge",
            "AvailabilityZone": "us-east-1a",
            "InstanceCount": 1,
            "StartDate": s,
            "EndDate": s + timedelta(days=1),
            "CapacityBlockDurationHours": 24,
            "CapacityBlockDurationMinutes": 0,
            "UpfrontFee": "10.00",
            "CurrencyCode": "USD",
        }

    probe = CapacityBlockLongestProbe()
    success_ec2 = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[
            {"CapacityBlockOfferings": [offer()]},
            {"CapacityBlockOfferings": []},  # gallop D=2: stop
        ],
    )
    success_rows = probe.run(make_ctx(config, {"us-east-1": success_ec2}))

    none_ec2 = FakeEc2(
        "us-east-1", describe_capacity_block_offerings=[{"CapacityBlockOfferings": []}] * 7
    )
    none_rows = probe.run(make_ctx(config, {"us-east-1": none_ec2}))

    error_ec2 = FakeEc2(
        "us-east-1",
        describe_capacity_block_offerings=[
            {"CapacityBlockOfferings": [offer()]},
            FakeClientError("InvalidParameterValue"),
        ],
    )
    error_rows = probe.run(make_ctx(config, {"us-east-1": error_ec2}))

    real = {
        "success": success_rows[0],
        "none": none_rows[0],
        "error": error_rows[0],
    }
    assert {row.outcome for row in real.values()} == {"success", "none", "error"}

    # Every sim row has the keys and the value types (null, number, string, bool) of a real
    # row of its outcome; a step is compared with a real step of the same result.
    seen = set()
    for seed in range(200):
        for call in sim_calls("aws_cb_longest", config, END, random.Random(seed)):
            for row in call.rows:
                seen.add(row.outcome)
                assert value_types(row) == value_types(real[row.outcome]), row.outcome
                real_steps = step_types(real[row.outcome])
                for result, types in step_types(row).items():
                    if result in real_steps:
                        assert types == real_steps[result], (row.outcome, result)
    assert seen == set(real)


def test_write_history_writes_no_rows_for_a_job_eligible_type_with_no_job(tmp_path):
    """ADR 0010 section 1: a job-eligible probe type with no job never runs, in `sim` too.
    An `az_spot_score` job with no `az_spot` job must write no `az_spot` rows."""
    from ftg_prober.config import load_config

    path = tmp_path / "t.yaml"
    path.write_text(
        "experiment: ftg-20261012-signals\n"
        "azure:\n"
        "  modules:\n"
        "    az_spot_score: {type: az_spot_score, spot_sizes: [Standard_NC40ads_H100_v5]}\n"
        "  jobs:\n"
        "    - {module: az_spot_score, regions: [eastus], interval: 15m}\n"
    )
    config = load_config(path)
    store = MemoryStore()
    counts = write_history(config, store, hours=1, seed=1, end=END)
    assert "az_spot_score" in counts and counts["az_spot_score"]["rows"] > 0
    assert "az_spot" not in counts
    assert not [r for r in rows_of(store) if r.probe_type == "az_spot"]


def test_write_history_does_not_crash_on_an_allocation_only_file(tmp_path):
    """An azure file with only provisioning_probes (no jobs: at all) must not crash: the
    job-eligible signal types (az_spot_score, az_spot) have no job and write nothing, while
    the provisioning probes and the pre-check, which are not job-eligible, still run."""
    from ftg_prober.config import load_config

    path = tmp_path / "t.yaml"
    path.write_text(
        "experiment: ftg-20261012-signals\n"
        "azure:\n"
        "  provisioning_probes:\n"
        "    az_capacity_reservation:\n"
        "      - {sku: Standard_D2as_v4, region: eastus, zone: null, n: 2, "
        "placement_group_mode: multi, simulate: true}\n"
    )
    config = load_config(path)
    store = MemoryStore()
    counts = write_history(config, store, hours=1, seed=1, end=END)
    assert "az_spot_score" not in counts
    assert "az_spot" not in counts
    assert counts["az_precheck"]["rows"] > 0
    assert counts["az_capacity_reservation"]["rows"] > 0


def test_cb_sim_expands_instance_counts_like_the_probes_and_ids_are_unique():
    """1 call per (type, count, duration); an offering id is unique per
    offering; a success row has `n_placed` null like a real offering row."""
    from tests.aws.fakes import section as aws_section

    config = aws_section(
        regions=["us-east-1", "us-west-2"],
        capacity_blocks={
            "instance_types": ["p5.4xlarge", "p5.48xlarge"],
            "instance_count": 1,
            "instance_counts": {"p5.4xlarge": [1, 4, 8]},
            "duration_hours": [24, 48],
            "earliest_duration_hours": [24, 48],
            "start_window_days": 14,
        },
    )
    ids = set()
    n_offerings = 0
    for seed in range(100):
        rng = random.Random(seed)
        calls = sim_calls("aws_cb_offering", config, END, rng)
        assert len(calls) == len(request_plan(config)) == 2 * (3 + 1) * 2
        for call in calls:
            call_ids = [r.details["offering_id"] for r in call.rows if r.outcome == "success"]
            assert len(call_ids) == len(set(call_ids))
            n_offerings += len(call_ids)
            ids.update(call_ids)
            for row in call.rows:
                assert row.n_placed is None
                assert row.n_requested == row.details.get("instance_count", row.n_requested)
        assert len(sim_calls("aws_cb_earliest", config, END, rng)) == len(earliest_plan(config))
        assert len(sim_calls("aws_cb_longest", config, END, rng)) == 2 * (3 + 1)
        assert {c.rows[0].n_requested for c in sim_calls("aws_cb_longest", config, END, rng)} == {
            1,
            4,
            8,
        }
        for probe_type in ("aws_cb_earliest", "aws_cb_longest"):
            for call in sim_calls(probe_type, config, END, rng):
                assert all(r.n_placed is None for r in call.rows)
    assert n_offerings > 0


def test_aws_cb_offering_and_earliest_sim_rows_have_the_same_details_keys_as_a_real_row():
    """A sim row and a real row (fake client) have the same `details` keys, for each outcome.
    `aws_cb_earliest` uses the same `offering_rows` code as `aws_cb_offering`."""
    from ftg_prober.aws.cb_earliest import CapacityBlockEarliestProbe
    from ftg_prober.aws.cb_offerings import CapacityBlockOfferingsProbe
    from tests.aws.fakes import FakeClientError, FakeEc2, make_ctx
    from tests.aws.fakes import section as aws_section
    from tests.aws.test_cb_offerings import OFFERING

    config = aws_section(
        regions=["us-east-1"],
        capacity_blocks={
            "instance_types": ["p5.48xlarge"],
            "instance_count": 1,
            "duration_hours": [24],
            "start_window_days": 14,
        },
    )
    for probe_type, probe in (
        ("aws_cb_offering", CapacityBlockOfferingsProbe()),
        ("aws_cb_earliest", CapacityBlockEarliestProbe()),
    ):
        responses = {
            "success": [
                {"CapacityBlockOfferings": [{**OFFERING, "CapacityBlockDurationMinutes": 0}]}
            ],
            "none": [{"CapacityBlockOfferings": []}],
            "error": [FakeClientError("InvalidParameterValue")],
        }
        real = {}
        for outcome, response in responses.items():
            ec2 = FakeEc2("us-east-1", describe_capacity_block_offerings=response * 20)
            rows = probe.run(make_ctx(config, {"us-east-1": ec2}))
            assert rows[0].outcome == outcome
            real[outcome] = rows[0]

        # Every sim row has the keys and the value types of a real row of its outcome
        # The sim writes no `error` row for these types.
        seen = set()
        for seed in range(50):
            for call in sim_calls(probe_type, config, END, random.Random(seed)):
                for row in call.rows:
                    seen.add(row.outcome)
                    assert value_types(row) == value_types(real[row.outcome]), (
                        probe_type,
                        row.outcome,
                    )
        assert seen == {"success", "none"}


def assert_sim_has_the_types_of_real_rows(probe_type, section, real_rows):
    """Every sim row has the keys and value types of a real row of its variant (outcome, hourly
    effective row, null bucket, open range), and the sim writes every variant of `real_rows`."""
    real = {variant(row): row for row in real_rows}
    seen = set()
    for seed in range(60):
        for call in sim_calls(probe_type, section, END, random.Random(seed)):
            for row in call.rows:
                seen.add(variant(row))
                assert variant(row) in real, (probe_type, variant(row))
                assert value_types(row) == value_types(real[variant(row)]), (
                    probe_type,
                    variant(row),
                )
    assert seen == set(real), (probe_type, seen ^ set(real))


def test_aws_spot_sim_rows_have_the_value_types_of_a_real_row(repo_config, monkeypatch):
    from datetime import timedelta

    from ftg_prober import sim
    from ftg_prober.aws.spot_advisor import SpotAdvisorProbe
    from ftg_prober.aws.spot_price import SpotPriceProbe
    from ftg_prober.aws.spot_score import SpotPlacementScoreProbe
    from tests.aws.fakes import FakeClientError, FakeEc2, make_ctx
    from tests.aws.fakes import section as aws_section
    from tests.aws.test_spot_advisor import RECORDED_FEED, FakeHttp, FakeResponse
    from tests.aws.test_spot_price import record
    from tests.aws.test_spot_score import scorer, scores
    from tests.conftest import MemoryStore

    aws = repo_config.aws

    # The real Spot score rows of the repo targets: a plain score, the 3 control rules, and an
    # error row of each (a control entry gets `control` with `ok` null on an error row).
    requests = aws["spot_score_requests"]
    table = {
        tuple(e["instance_types"]): scores(*((2, 2) if e["name"] == "control-cpu" else (9, 9)))
        for e in requests
    }
    section = aws_section(spot_score_requests=requests)
    failing = {key: FakeClientError("ProxyConnectionError") for key in table}
    real_scores = {}
    for answers in (table, failing):
        ec2 = FakeEc2("us-east-1", get_spot_placement_scores=scorer(answers))
        for row in SpotPlacementScoreProbe().run(make_ctx(section, {"us-east-1": ec2})):
            real_scores.setdefault(variant(row), row)
    assert {v[0] for v in real_scores} == {"success", "error"}
    assert {v[8] for v in real_scores} == {
        None,
        "cpu_score_max",
        "cpu_mix_min",
        "single_not_above_mix",
    }
    assert len(real_scores) == 8  # 4 control kinds (none, 3 rules) x (success, error)
    monkeypatch.setattr(sim, "AWS_SPOT_SCORE_ERROR_SHARE", 0.5)
    assert_sim_has_the_types_of_real_rows("aws_spot_score", aws, list(real_scores.values()))

    # The real Spot price rows: a new record (success), no new record (none), and the hourly
    # effective row (success with `effective`).
    now = datetime(2026, 10, 11, 12, 2, 30, tzinfo=UTC)
    start = now - timedelta(minutes=5)
    history = [
        record("p5.48xlarge", "us-east-1a", "12.75", start + timedelta(minutes=1)),
        record("p5.48xlarge", "us-east-1b", "13.0", start - timedelta(hours=1)),
    ]
    ec2 = FakeEc2("us-east-1", describe_spot_price_history=[{"SpotPriceHistory": history}])
    rows = SpotPriceProbe().run(
        make_ctx(aws_section(regions=["us-east-1"]), {"us-east-1": ec2}, now=now)
    )
    assert any(r.details.get("effective") for r in rows)
    failing = FakeEc2("us-east-1", describe_spot_price_history=[FakeClientError("RequestLimit")])
    errors = SpotPriceProbe().run(
        make_ctx(aws_section(regions=["us-east-1"]), {"us-east-1": failing}, now=now)
    )
    assert {r.outcome for r in errors} == {"error"}
    monkeypatch.setattr(sim, "AWS_SPOT_PRICE_ERROR_SHARE", 0.5)
    assert_sim_has_the_types_of_real_rows(
        "aws_spot_price",
        aws,
        [
            next(r for r in rows if r.outcome == "success" and not r.details.get("effective")),
            next(r for r in rows if r.outcome == "none" and r.details),
            next(r for r in rows if r.details.get("effective")),
            errors[0],
        ],
    )

    # The real Spot advisor row, from the recorded public feed (the `feed()` fixture shares a
    # mutable `RANGES` list that other tests change).
    rows = []
    for open_range in (False, True):
        data = json.loads(RECORDED_FEED.read_text())
        if open_range:  # every entry in the open ">20%" range
            index = next(r["index"] for r in data["ranges"] if r["label"] == ">20%")
            for regional in data["spot_advisor"].values():
                for entry in regional["Linux"].values():
                    entry["r"] = index
        http = FakeHttp(FakeResponse(200, json.dumps(data).encode()))
        rows += SpotAdvisorProbe(http=http).run(make_ctx(aws_section(), store=MemoryStore()))
    samples = {variant(r): r for r in rows if r.outcome == "success"}
    assert len(samples) == 2
    assert_sim_has_the_types_of_real_rows("aws_spot_advisor", aws, list(samples.values()))


def test_the_hourly_effective_price_rows_follow_the_probe_crosses_hour_rule(repo_config):
    """With a cadence of 45 min, most hourly polls are not at minute 0. The sim writes
    the effective rows on the same polls as the real probe (`crosses_hour`)."""
    from datetime import timedelta

    from ftg_prober.aws.spot_price import crosses_hour

    aws = {**repo_config.aws, "cadence": {"aws_spot_price": "45m"}}
    rng = random.Random(3)
    hourly = []
    for step in range(32):
        at = END + timedelta(minutes=45 * step + 15)
        has_effective = any(
            row.details.get("effective")
            for call in sim_calls("aws_spot_price", aws, at, rng)
            for row in call.rows
        )
        hourly.append(has_effective)
        assert has_effective == crosses_hour(at - timedelta(minutes=45), at), at
    assert any(hourly) and not all(hourly)
