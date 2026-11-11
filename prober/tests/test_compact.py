"""Tests for the daily compaction core."""

import hashlib
import json
import random
from datetime import UTC, datetime

import pytest
import zstandard

from ftg_prober import __version__, compact

NEW = "experiment=ftg-x/provider=aws/probe_type=t/date=2026-10-20/"
OLD = "run_mode=live/provider=aws/probe_type=t/date=2026-10-20/"


def _row(ts: str, n: int = 0) -> bytes:
    return json.dumps({"ts_utc": ts, "n": n}, separators=(",", ":")).encode() + b"\n"


def _out(res: compact.Compacted) -> bytes:
    return zstandard.ZstdDecompressor().decompressobj().decompress(res.data)


def test_dedup_across_layouts_prefers_experiment_key():
    objs = [
        (OLD + "a.jsonl", b'{"ts_utc":"2026-10-20T00:00:01Z","from":"old"}\n'),
        (NEW + "a.jsonl", b'{"ts_utc":"2026-10-20T00:00:01Z","from":"new"}\n'),
        (OLD + "b.jsonl", _row("2026-10-20T00:00:02Z")),
    ]
    for order in (objs, objs[::-1]):
        res = compact.compact_day(order)
        assert res.manifest["raw_object_count"] == 3
        assert res.manifest["kept_object_count"] == 2
        assert b'"from":"new"' in _out(res) and b'"from":"old"' not in _out(res)


def test_lines_are_byte_identical_in_base_name_order():
    a = b'{"ts_utc":"2026-10-20T00:00:01Z",  "k": "\xc3\xa9"}\r\n\n  \n'
    b = _row("2026-10-20T00:00:02Z", 7)
    res = compact.compact_day([(NEW + "b.jsonl", b), (NEW + "a.jsonl", a)])
    assert _out(res) == a + b  # blank lines kept, like `awk 1`
    assert res.manifest["row_count"] == 4
    assert res.manifest["unparsed_row_count"] == 2  # the 2 blank lines


def test_missing_trailing_newline_is_added():
    res = compact.compact_day(
        [
            (NEW + "a.jsonl", b'{"ts_utc":"2026-10-20T00:00:01Z"}'),
            (NEW + "b.jsonl", _row("2026-10-20T00:00:02Z")),
        ]
    )
    assert _out(res).count(b"\n") == 2 and res.manifest["row_count"] == 2


def test_empty_object_adds_nothing_but_is_counted():
    res = compact.compact_day(
        [(NEW + "a.jsonl", b""), (NEW + "b.jsonl", _row("2026-10-20T00:00:02Z"))]
    )
    assert res.manifest["raw_object_count"] == 2 == res.manifest["kept_object_count"]
    assert res.manifest["row_count"] == 1


def test_no_objects_gives_an_empty_valid_file():
    res = compact.compact_day([])
    assert _out(res) == b"" and res.manifest["row_count"] == 0
    assert res.manifest["first_ts_utc"] is None


def test_malformed_line_is_kept_and_counted():
    bad = b"{not json\n"
    res = compact.compact_day([(NEW + "a.jsonl", bad + _row("2026-10-20T00:00:05Z"))])
    assert _out(res).startswith(bad)
    m = res.manifest
    assert (m["row_count"], m["unparsed_row_count"]) == (2, 1)
    assert m["first_ts_utc"] == m["last_ts_utc"] == "2026-10-20T00:00:05Z"


def test_manifest_fields_and_ts_range():
    now = datetime(2026, 10, 21, 1, 2, 3, tzinfo=UTC)
    res = compact.compact_day(
        [
            (NEW + "a.jsonl", _row("2026-10-20T10:00:00.500Z") + _row("2026-10-20T09:00:00Z")),
            (NEW + "b.jsonl", _row("2026-10-20T23:59:59.999Z")),
        ],
        now=now,
    )
    m = res.manifest
    assert m["first_ts_utc"] == "2026-10-20T09:00:00Z"
    assert m["last_ts_utc"] == "2026-10-20T23:59:59.999Z"
    assert m["ftg_prober_version"] == __version__
    assert m["built_utc"] == "2026-10-21T01:02:03Z"
    assert len(m["objects_sha256"]) == 64


def test_digest_stable_under_input_order_and_sensitive_to_content():
    objs = [(NEW + f"f{i}.jsonl", _row("2026-10-20T00:00:00Z", i)) for i in range(20)]
    base = compact.compact_day(objs)
    shuffled = objs[:]
    random.Random(1).shuffle(shuffled)
    again = compact.compact_day(shuffled)
    assert again.manifest["objects_sha256"] == base.manifest["objects_sha256"]
    assert again.data == base.data
    changed = objs[:-1] + [(objs[-1][0], objs[-1][1] + b"x\n")]
    assert (
        compact.compact_day(changed).manifest["objects_sha256"] != base.manifest["objects_sha256"]
    )


def test_output_is_one_zstd_frame():
    res = compact.compact_day([(NEW + "a.jsonl", _row("2026-10-20T00:00:00Z"))])
    d = zstandard.ZstdDecompressor().decompressobj()
    d.decompress(res.data)
    assert d.eof and d.unused_data == b""


def test_verify_catches_a_truncated_output():
    res = compact.compact_day(
        [(NEW + f"f{i}.jsonl", _row("2026-10-20T00:00:00Z", i)) for i in range(50)]
    )
    compact.verify(res.data, res.manifest)
    with pytest.raises(compact.CompactVerifyError):
        compact.verify(res.data[:-5], res.manifest)
    with pytest.raises(compact.CompactVerifyError):
        compact.verify(res.data + b"junk", res.manifest)
    with pytest.raises(compact.CompactVerifyError):
        compact.verify(res.data, {**res.manifest, "row_count": 49})


def _big() -> compact.Compacted:
    return compact.compact_day(
        [(NEW + f"f{i}.jsonl", _row("2026-10-20T00:00:00Z", i)) for i in range(30)]
    )


def test_manifest_has_content_digest_and_frame_has_checksum():
    res = _big()
    raw = _out(res)
    assert res.manifest["rows_bytes"] == len(raw)
    assert res.manifest["rows_sha256"] == hashlib.sha256(raw).hexdigest()
    assert zstandard.get_frame_parameters(res.data).has_checksum


def test_verify_catches_content_change_with_equal_row_count():
    res = _big()
    other = compact.compact_day(
        [(NEW + f"f{i}.jsonl", _row("2026-10-20T00:00:00Z", i + 1)) for i in range(30)]
    )
    assert other.manifest["row_count"] == res.manifest["row_count"]
    with pytest.raises(compact.CompactVerifyError):
        compact.verify(other.data, res.manifest)


def test_verify_raises_verify_error_for_a_missing_key():
    res = _big()
    for key in ("row_count", "rows_bytes", "rows_sha256", "file_bytes", "file_sha256"):
        bad = {k: v for k, v in res.manifest.items() if k != key}
        with pytest.raises(compact.CompactVerifyError):
            compact.verify(res.data, bad)


def test_random_bit_flips_never_pass_verify():
    res = _big()
    rng = random.Random(458)
    for _ in range(200):
        buf = bytearray(res.data)
        buf[rng.randrange(len(buf))] ^= 1 << rng.randrange(8)
        with pytest.raises(compact.CompactVerifyError):
            compact.verify(bytes(buf), res.manifest)


def test_objects_digest_agrees_with_the_listing_digest():
    objs = [(NEW + f"f{i}.jsonl", _row("2026-10-20T00:00:00Z", i)) for i in range(5)]
    objs.append((OLD + "f0.jsonl", b"dup\n"))
    listing = [(k, len(b)) for k, b in objs][::-1]
    res = compact.compact_day(objs)
    assert compact.objects_sha256_from_listing(listing) == res.manifest["objects_sha256"]


def test_ts_never_raises_and_naive_is_unparsed():
    rows = (
        b'{"ts_utc":"2026-10-20T00:00:00Z"}\n'
        b'{"ts_utc":"2026-10-20T00:00:00"}\n'
        b'{"ts_utc":"2026-10-20"}\n'
        b'{"ts_utc":5}\n'
        b"[1]\n" + b"[" * 100000 + b"\n" + b'{"ts_utc":"2026-10-20T01:00:00+00:00"}\n'
    )
    m = compact.compact_day([(NEW + "a.jsonl", rows)]).manifest
    assert m["row_count"] == 7 and m["unparsed_row_count"] == 5
    assert m["first_ts_utc"] == "2026-10-20T00:00:00Z"
    assert m["last_ts_utc"] == "2026-10-20T01:00:00+00:00"


def test_only_jsonl_keys_are_rows():
    objs = [
        (NEW + "a.jsonl", _row("2026-10-20T00:00:00Z")),
        (NEW + "a.jsonl.tmp", b"x\n"),
        (NEW + "manifest.json", b"{}\n"),
        (NEW, b""),
    ]
    m = compact.compact_day(objs).manifest
    assert (m["raw_object_count"], m["kept_object_count"], m["row_count"]) == (1, 1, 1)


def test_preferred_tests_the_path_not_the_base_name():
    objs = [
        (OLD + "experiment=x.jsonl", b'{"from":"old"}\n'),
        (NEW + "experiment=x.jsonl", b'{"from":"new"}\n'),
    ]
    for order in (objs, objs[::-1]):
        assert b'"new"' in _out(compact.compact_day(order))
    only_name = compact.compact_day([(OLD + "experiment=y.jsonl", b'{"a":1}\n')])
    assert only_name.manifest["row_count"] == 1


def test_more_than_one_group_raises():
    other = NEW.replace("probe_type=t", "probe_type=u")
    with pytest.raises(ValueError):
        compact.compact_day([(NEW + "a.jsonl", b"{}\n"), (other + "b.jsonl", b"{}\n")])
    with pytest.raises(ValueError):
        compact.objects_sha256_from_listing([(NEW + "a.jsonl", 1), (other + "b.jsonl", 1)])


def _manifest_and_data():
    res = _big()
    return res.data, dict(res.manifest)


def test_verify_checks_unparsed_and_ts_range():
    data, m = _manifest_and_data()
    for key, bad in (
        ("unparsed_row_count", m["unparsed_row_count"] + 1),
        ("first_ts_utc", "2026-10-19T00:00:00Z"),
        ("last_ts_utc", "2026-10-21T00:00:00Z"),
        ("first_ts_utc", None),
    ):
        with pytest.raises(compact.CompactVerifyError, match=key):
            compact.verify(data, {**m, key: bad})
        gone = {k: v for k, v in m.items() if k != key}
        with pytest.raises(compact.CompactVerifyError, match=key):
            compact.verify(data, gone)


def test_verify_rejects_a_float_or_bool_for_an_int_field():
    data, m = _manifest_and_data()
    compact.verify(data, m)
    for key in ("row_count", "rows_bytes", "file_bytes", "unparsed_row_count"):
        with pytest.raises(compact.CompactVerifyError, match=key):
            compact.verify(data, {**m, key: float(m[key])})
    one = compact.compact_day([(NEW + "a.jsonl", b"{}\n")])  # row_count 1, unparsed 1
    for key in ("row_count", "unparsed_row_count"):
        with pytest.raises(compact.CompactVerifyError, match=key):
            compact.verify(one.data, {**one.manifest, key: True})


def test_verify_raises_verify_error_for_a_non_dict_manifest():
    data, _ = _manifest_and_data()
    for bad in (None, [], "x", 5):
        with pytest.raises(compact.CompactVerifyError):
            compact.verify(data, bad)  # type: ignore[arg-type]


def test_digest_framing_cannot_collide_and_normal_names_are_unchanged():
    d = compact._digest
    assert d([("a\t1\nb", 2)]) != d([("a", 1), ("b", 2)])
    assert d([("a\\", 1)]) != d([("a", 1)]) and d([("a\\t", 1)]) != d([("a\t", 1)])
    # A name without a tab, newline, or backslash hashes as in the first release (old manifests).
    assert d([("f0.jsonl", 5)]) == hashlib.sha256(b"f0.jsonl\t5\n").hexdigest()


def test_preferred_needs_a_folder_segment_not_a_substring():
    assert compact._preferred("experiment=x/provider=aws/a.jsonl")
    assert not compact._preferred("run_mode=live/xexperiment=x/a.jsonl")
    assert not compact._preferred("run_mode=live/a-experiment=x.jsonl")
