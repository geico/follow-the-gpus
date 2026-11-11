"""Tests for the daily compaction job and its S3 backend (ADR 0025)."""

import ast
import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import zstandard

from ftg_prober import cli, compact_job
from ftg_prober.compact_job import WriteRefused, _put, compact_all, daily_keys
from ftg_prober.compact_stores import FileCompactStore, S3CompactStore, open_compact_store
from tests.conftest import TARGETS_PATH, import_or_skip

EXP = "ftg-20261014-example"
RAW = f"experiment={EXP}/provider=aws/probe_type=aws_spot_score/date=2026-10-20/"
DATA, SIDECAR = daily_keys(EXP, "aws_spot_score", "2026-10-20")
NOW = datetime(2026, 10, 21, 0, 30, tzinfo=UTC)  # the day closed exactly 30 minutes ago


def _row(n: int, day: str = "2026-10-20") -> bytes:
    return json.dumps({"ts_utc": f"{day}T00:00:{n:02d}Z", "n": n}).encode() + b"\n"


class FakeStore:
    """A CompactStore in memory; `puts` records every put in order."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
        self.puts: list[str] = []
        self.corrupt_reads_of: str | None = None
        self.fail_gets_of: str | None = None

    def list(self, prefix):
        return [(k, len(v)) for k, v in sorted(self.objects.items()) if k.startswith(prefix)]

    def get(self, key):
        if self.fail_gets_of and self.fail_gets_of in key:
            raise OSError("read failed")
        data = self.objects[key]
        return data[:-3] if self.corrupt_reads_of and key.endswith(self.corrupt_reads_of) else data

    def put(self, key, data, content_type):
        self.puts.append(key)
        self.objects[key] = data


def _store(n: int = 3, day: str = "2026-10-20", probe: str = "aws_spot_score") -> FakeStore:
    raw = f"experiment={EXP}/provider=aws/probe_type={probe}/date={day}/"
    return FakeStore({f"{raw}{day}T00{i:02d}_x_{i}.jsonl": _row(i, day) for i in range(n)})


def _run(store, now=NOW):
    return compact_all(store, experiment=EXP, provider="aws", now=now)


def _decompress(data: bytes) -> bytes:
    return zstandard.ZstdDecompressor().decompressobj().decompress(data)


def test_the_key_names():
    assert f"experiment={EXP}/daily/v1/date=2026-10-20/aws_spot_score_2026-10-20.jsonl.zst" == DATA
    assert SIDECAR == DATA + ".manifest.json"


def test_a_closed_day_is_built_and_the_sidecar_is_written_last():
    store = _store(3)
    summary = _run(store)
    assert (summary.built, summary.unchanged, summary.failed) == (1, 0, 0)
    assert store.puts == [DATA, SIDECAR]
    manifest = json.loads(store.objects[SIDECAR])
    assert manifest["row_count"] == 3 and manifest["raw_object_count"] == 3
    assert manifest["file_bytes"] == len(store.objects[DATA])
    assert manifest["file_sha256"] == hashlib.sha256(store.objects[DATA]).hexdigest()
    assert _decompress(store.objects[DATA]) == b"".join(_row(i) for i in range(3))


def test_the_open_day_and_a_day_closed_under_30_minutes_ago_are_not_built():
    store = _store(2)
    assert _run(store, now=datetime(2026, 10, 21, 0, 29, 59, tzinfo=UTC)).built == 0
    assert store.puts == []
    open_day = _store(2, day="2026-10-21")
    assert _run(open_day, now=datetime(2026, 10, 21, 23, 59, tzinfo=UTC)).built == 0


@pytest.mark.parametrize("bad", ["2026-13-45", "2026-02-30", "9999-12-31"])
def test_a_raw_key_with_an_invalid_date_is_skipped_with_a_warning_and_the_run_goes_on(bad, caplog):
    store = _store(2)
    bad_key = f"experiment={EXP}/provider=aws/probe_type=aws_spot_score/date={bad}/x_1.jsonl"
    store.objects[bad_key] = _row(1, "2026-10-20")
    with caplog.at_level("WARNING", logger="ftg_prober.compact_job"):
        summary = _run(store)
    assert (summary.built, summary.failed) == (1, 0)
    assert any("compact.bad_date_skipped" in r.getMessage() for r in caplog.records)
    assert bad_key in store.objects  # never deleted
    assert all(bad not in k for k in store.puts)


def test_a_raw_key_with_non_ascii_digits_in_the_date_is_not_a_row():
    store = _store(2)
    store.objects[RAW.replace("2026-10-20", "\u0662\u0660\u0662\u0666-10-20") + "x_1.jsonl"] = (
        b"{}\n"
    )
    summary = _run(store)
    assert (summary.built, summary.failed) == (1, 0)


def test_raw_feed_objects_are_not_rows():
    store = _store(2)
    store.objects[RAW + "x_raw_1.json"] = b'{"feed": 1}\n'
    _run(store)
    assert json.loads(store.objects[SIDECAR])["raw_object_count"] == 2


def test_an_unchanged_day_writes_nothing():
    store = _store(3)
    _run(store)
    store.puts.clear()
    summary = _run(store)
    assert (summary.built, summary.unchanged) == (0, 1) and store.puts == []


def test_a_late_object_rebuilds_the_day():
    store = _store(3)
    _run(store)
    store.objects[RAW + "2026-10-20T0099_x_late.jsonl"] = _row(9)
    store.puts.clear()
    assert _run(store).built == 1
    assert store.puts == [DATA, SIDECAR]
    assert json.loads(store.objects[SIDECAR])["row_count"] == 4


def test_a_day_with_a_data_file_and_no_sidecar_is_built():
    store = _store(3)
    store.objects[DATA] = b"left by a killed run"
    assert _run(store).built == 1
    assert SIDECAR in store.objects


def test_a_failed_read_back_writes_no_sidecar_and_the_other_days_still_run():
    store = _store(3)
    store.objects.update(_store(2, day="2026-10-19", probe="aws_spot_price").objects)
    store.corrupt_reads_of = ".jsonl.zst"
    summary = _run(store)
    assert summary.failed == 2 and summary.built == 0
    assert not [k for k in store.puts if k.endswith(".manifest.json")]


def test_one_failed_day_does_not_stop_the_next_day():
    store = _store(3)
    store.objects.update(_store(2, day="2026-10-19", probe="aws_spot_price").objects)
    store.fail_gets_of = "date=2026-10-19"
    summary = _run(store)
    assert (summary.built, summary.failed) == (1, 1)
    assert SIDECAR in store.objects and not any("2026-10-19" in k for k in store.puts)


def test_an_object_listed_but_changed_before_the_read_fails_the_day():
    store = _store(3)
    original = store.get
    store.get = lambda key: original(key) + (b" " if key.endswith("_x_1.jsonl") else b"")
    assert _run(store).failed == 1 and store.puts == []


# --- writes stay under daily/v1 --------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        RAW + "2026-10-20T0001_x_1.jsonl",  # a raw row
        f"experiment={EXP}/logs/provider=aws/date=2026-10-20/a.jsonl",  # a log
        DATA.replace(EXP, "ftg-other"),  # another experiment
        DATA.replace("/daily/v1/", "/daily/v1/../../provider=aws/"),  # traversal
        "manifest.json",
    ],
)
def test_a_write_outside_daily_v1_is_refused(key):
    store = FakeStore()
    with pytest.raises(WriteRefused):
        _put(store, EXP, key, b"x", "application/json")
    assert store.puts == []


def test_every_write_of_the_job_is_under_daily_v1_and_leaves_other_objects_alone():
    store = _store(3)
    store.objects[f"experiment={EXP}/logs/provider=aws/date=2026-10-20/a.jsonl"] = b"log\n"
    store.objects["code/prober-aaaa.zip"] = b"zip"
    before = dict(store.objects)
    _run(store)
    assert store.puts and all(k.startswith(f"experiment={EXP}/daily/v1/") for k in store.puts)
    assert {k: v for k, v in store.objects.items() if k in before} == before


FORBIDDEN_STORE_CALLS = (
    "delete_object", "delete_objects", "delete_bucket", "put_bucket_lifecycle",
    "put_bucket_lifecycle_configuration", "delete_blob", "delete_blobs", "delete_container",
    "set_blob_tier", "set_standard_blob_tier", "set_delete_retention", "set_service_properties",
    "put_object_retention", "put_bucket_versioning",
)  # fmt: skip


def test_no_code_in_the_prober_package_removes_or_re_tiers_probe_data():
    """Our code never removes probe data. No allowlist is needed."""
    package = Path(compact_job.__file__).parent
    hits = []
    for path in package.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            name = getattr(node, "attr", None) or getattr(node, "id", None)
            if isinstance(name, str) and (
                name in FORBIDDEN_STORE_CALLS or name.startswith("put_bucket_lifecycle")
            ):
                hits.append(f"{path.relative_to(package)}:{node.lineno} {name}")
    assert hits == []


def test_the_job_writes_only_through_put():
    """Every store write in the job goes through `_put`, which checks the key."""
    tree = ast.parse(Path(compact_job.__file__).read_text())
    receivers = [
        (n.func.value.id if isinstance(n.func.value, ast.Name) else ast.unparse(n.func.value))
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "put"
    ]
    assert receivers == ["store"]  # the single store.put, inside _put


# --- the S3 backend --------------------------------------------------------------------------


class FakePaginator:
    def __init__(self, client):
        self.client = client

    def paginate(self, **kwargs):
        assert set(kwargs) == {"Bucket", "Prefix"}
        keys = sorted(k for k in self.client.objects if k.startswith(kwargs["Prefix"]))
        size = self.client.page
        for i in range(0, max(len(keys), 1), size):
            chunk = keys[i : i + size]
            page = {"IsTruncated": i + size < len(keys), "KeyCount": len(chunk)}
            if chunk:  # `Contents` is absent when nothing matches
                page["Contents"] = [
                    {"Key": k, "Size": len(self.client.objects[k]), "ETag": '"e"'} for k in chunk
                ]
            yield page


class FakeS3Client:
    """A boto3 S3 client with the real response shapes; pages hold at most `page` keys."""

    def __init__(self, objects: dict[str, bytes], page: int = 2) -> None:
        self.objects, self.page, self.calls = dict(objects), page, []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return FakePaginator(self)

    def get_object(self, **kwargs):
        self.calls.append(("get_object", kwargs))
        return {"Body": io.BytesIO(self.objects[kwargs["Key"]])}

    def put_object(self, **kwargs):
        self.calls.append(("put_object", kwargs))
        assert set(kwargs) == {"Bucket", "Key", "Body", "ContentType"}
        self.objects[kwargs["Key"]] = kwargs["Body"]
        return {"ETag": '"x"'}


def test_s3_store_lists_all_pages_and_an_empty_listing_has_no_contents():
    client = FakeS3Client({f"p/k{i}": b"x" * i for i in range(5)}, page=2)
    store = S3CompactStore("b", client=client)
    assert list(store.list("p/")) == [(f"p/k{i}", i) for i in range(5)]
    assert list(store.list("none/")) == []


def test_s3_store_prefix_is_added_to_calls_and_stripped_from_listings():
    client = FakeS3Client({"pre/a/1": b"abc"})
    store = S3CompactStore("b", "/pre/", client=client)
    assert list(store.list("a/")) == [("a/1", 3)]
    assert store.get("a/1") == b"abc"
    store.put("a/2", b"zz", "application/json")
    assert client.objects["pre/a/2"] == b"zz"


def test_s3_store_calls_match_the_botocore_service_model():
    """Validates our requests and the response shapes against the real botocore S3 model."""
    boto3 = import_or_skip("boto3")
    from botocore.stub import Stubber

    client = boto3.client(
        "s3", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x"
    )
    with Stubber(client) as stub:
        stub.add_response(
            "list_objects_v2",
            {
                "IsTruncated": True,
                "NextContinuationToken": "t",
                "Contents": [{"Key": "a", "Size": 1, "ETag": '"e1"'}],
            },
            {"Bucket": "b", "Prefix": "p/"},
        )
        stub.add_response(
            "list_objects_v2",
            {"IsTruncated": False, "Contents": [{"Key": "b", "Size": 2, "ETag": '"e2"'}]},
            {"Bucket": "b", "Prefix": "p/", "ContinuationToken": "t"},
        )
        stub.add_response(
            "list_objects_v2", {"IsTruncated": False}, {"Bucket": "b", "Prefix": "q/"}
        )
        stub.add_response("get_object", {"Body": io.BytesIO(b"hi")}, {"Bucket": "b", "Key": "k"})
        stub.add_response(
            "put_object",
            {},
            {"Bucket": "b", "Key": "k", "Body": b"d", "ContentType": "application/json"},
        )
        store = S3CompactStore("b", client=client)
        assert list(store.list("p/")) == [("a", 1), ("b", 2)]
        assert list(store.list("q/")) == []
        assert store.get("k") == b"hi"
        store.put("k", b"d", "application/json")
        stub.assert_no_pending_responses()


def test_s3_store_runs_the_whole_job():
    client = FakeS3Client(_store(4).objects, page=3)
    assert _run(S3CompactStore("b", client=client)).built == 1
    assert json.loads(client.objects[SIDECAR])["row_count"] == 4
    assert [c[1]["Key"] for c in client.calls if c[0] == "put_object"] == [DATA, SIDECAR]


def test_open_compact_store():
    assert isinstance(open_compact_store("s3://b/p", client=object()), S3CompactStore)
    assert open_compact_store("s3://b/p/", client=object()).url == "s3://b/p"
    for bad in ("az://a", "gs://x", "s3://", "file://host/x"):
        with pytest.raises(ValueError):
            open_compact_store(bad)


# --- the command -----------------------------------------------------------------------------


def _argv(root: Path, provider: str = "aws") -> list[str]:
    return ["compact", "--provider", provider, "--config", str(TARGETS_PATH),
            "--store", f"file://{root}"]  # fmt: skip


def _fill(root: Path) -> None:
    for key, body in _store(3).objects.items():
        (root / key).parent.mkdir(parents=True, exist_ok=True)
        (root / key).write_bytes(body)


def test_cli_compacts_a_file_store_and_exits_0_then_writes_nothing_more(tmp_path):
    root = tmp_path / "store"
    _fill(root)
    assert cli.main(_argv(root)) == 0
    assert (root / DATA).is_file() and (root / SIDECAR).is_file()
    stamp = (root / SIDECAR).stat().st_mtime_ns
    assert cli.main(_argv(root)) == 0
    assert (root / SIDECAR).stat().st_mtime_ns == stamp
    assert len(list((root / RAW).glob("*.jsonl"))) == 3  # the raw objects are untouched


def test_cli_exits_1_when_a_day_fails(tmp_path, monkeypatch):
    root = tmp_path / "store"
    _fill(root)

    def boom(self, key):
        raise OSError("x")

    monkeypatch.setattr(FileCompactStore, "get", boom)
    assert cli.main(_argv(root)) == 1
    assert not (root / SIDECAR).exists()


def test_cli_exits_2_on_a_bad_store_url():
    base = ["compact", "--config", str(TARGETS_PATH), "--provider", "aws"]
    assert cli.main([*base, "--store", "gs://x"]) == 2
    assert cli.main([*base, "--store", "az://a"]) == 2


def test_zstandard_is_a_core_dependency_of_the_release():
    """sync.sh runs `pip install prober[aws]`; the pin must be in `dependencies`, not an extra.
    zstandard 0.25.0 has a cp312 manylinux2014 aarch64 wheel (checked with pip download)."""
    import tomllib

    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert "zstandard==0.25.0" in pyproject["project"]["dependencies"]
