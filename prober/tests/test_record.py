import json
import re
from datetime import UTC, datetime

from ftg_prober.record import (
    FIELD_NAMES,
    PROBE_TYPES,
    Record,
    format_ts,
    simulated_from_run_mode,
    to_jsonl,
)
from tests.conftest import SPEC_0002_PATH


def make_record(**overrides) -> Record:
    fields = dict(
        ts_utc="2026-10-11T12:00:00.000Z",
        run_id="run-0001",
        simulated=False,
        provider="aws",
        region="us-east-1",
        zone=None,
        probe_type="aws_spot_score",
        sku="p5.48xlarge",
        placement_group_mode="n/a",
        n_requested=1,
        n_placed=None,
        outcome="success",
        error_code=None,
        latency_ms=120,
        details={"score": 3},
    )
    fields.update(overrides)
    return Record(**fields)


def spec_field_names() -> list[str]:
    """The field names of spec 0002 table 7.2, in table order."""
    text = SPEC_0002_PATH.read_text(encoding="utf-8")
    section = text.split("### 7.2 Record schema", 1)[1].split("### 7.3", 1)[0]
    names = []
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        first_cell = line.split("|")[1]
        names.extend(re.findall(r"`([a-z_]+)`", first_cell))
    return names


def test_field_names_match_spec_table():
    assert list(FIELD_NAMES) == spec_field_names()


def test_probe_types_match_spec_table():
    text = SPEC_0002_PATH.read_text(encoding="utf-8")
    section = text.split("### 7.2 Record schema", 1)[1].split("### 7.3", 1)[0]
    row = next(line for line in section.splitlines() if line.startswith("| `probe_type`"))
    spec_types = tuple(re.findall(r"`([a-z_]+)`", row.split("|")[2]))
    assert spec_types == PROBE_TYPES


def test_to_json_is_one_line_with_the_fields_in_order():
    line = make_record().to_json()
    assert "\n" not in line
    parsed = json.loads(line)
    assert list(parsed) == list(FIELD_NAMES)
    assert parsed["zone"] is None
    assert parsed["details"] == {"score": 3}


def test_to_json_escapes_new_lines_in_values():
    line = make_record(error_code="a\nb").to_json()
    assert "\n" not in line
    assert json.loads(line)["error_code"] == "a\nb"


def test_to_jsonl_has_one_line_per_row():
    data = to_jsonl([make_record(), make_record(region="us-west-2")])
    lines = data.decode().split("\n")
    assert lines[-1] == ""
    assert len(lines) == 3
    assert json.loads(lines[1])["region"] == "us-west-2"


def test_format_ts_is_iso_8601_utc_with_z():
    moment = datetime(2026, 10, 11, 1, 2, 3, 456_000, tzinfo=UTC)
    assert format_ts(moment) == "2026-10-11T01:02:03.456Z"
    assert format_ts(datetime(2026, 10, 11, 1, 2, 3)) == "2026-10-11T01:02:03.000Z"


def test_simulated_from_run_mode_follows_the_adr_0011_reader_rule():
    """ADR 0011 section 5: `local` means simulated; any other value means real."""
    assert simulated_from_run_mode("local") is True
    assert simulated_from_run_mode("cloud-gpu") is False
    assert simulated_from_run_mode("cloud-cpu") is False


def test_validate_accepts_a_good_row():
    assert make_record().validate() == []


def test_validate_names_each_bad_value():
    faults = make_record(
        simulated="test", outcome="ok", probe_type="x", placement_group_mode="one"
    ).validate()
    assert len(faults) == 4
    assert any("simulated" in f for f in faults)
    assert any("outcome" in f for f in faults)
